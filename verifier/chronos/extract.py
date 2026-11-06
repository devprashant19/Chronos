"""Pulling time-sensitive claims out of an answer.

Two extractors, one output contract:

  * `extract_with_model` asks a model for strict JSON and validates it against
    the `Claim` schema. On a validation failure it retries once, handing the
    model its own error, then gives up — a wrong claim list is worse than no
    claim list, because it produces confident scores about sentences the answer
    never made.

  * `extract_with_rules` finds claims with patterns only (see rules.py). It is
    the offline and model-unavailable path, and it is deterministic, which is
    what makes the demo and the benchmark reproducible.

Both must *skip* the sentences that do not need checking. Opinions, arithmetic,
code and definitions have no current value to look up, and spending a Tavily
credit on "the capital of France is Paris" is the easiest waste in the system.
The benchmark measures this as the timeless skip rate.

The failure mode to design against is silence. A claim that is never extracted
produces a 100 score and a "current" verdict — a staleness detector saying
nothing and looking like approval. That is why the rule extractor has a generic
second layer rather than only hand-written patterns.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from pydantic import ValidationError

from .llm import LLMClient, ModelUnavailable
from .models import Claim, ExtractionResult
from .rules import (
    CLAIM_RULES,
    GENERIC_ASSERTION,
    SKIP_PATTERNS,
    TIMELESS_PATTERNS,
    infer_class,
)
from .settings import Settings, get_settings

logger = logging.getLogger("chronos.extract")

EXTRACT_SYSTEM = """Extract time-sensitive factual claims from an assistant's answer. Today is {today}.

Return ONLY this JSON:
{{"claims":[{{"text":"","span_start":0,"span_end":0,"subject":"","predicate":"","stated_value":"","class":"","as_of_hint":null}}],"skipped":0}}

class = one of: market (live prices/rates), pricing (product prices), version
(releases, model names, branding), leadership (who holds a role), law (statutes,
effective dates), events (results, schedules), timeless (never changes).

- span_start/span_end are character offsets into the answer, so that
  answer[span_start:span_end] is the claim's sentence.
- subject = the entity (e.g. "OpenAI GPT-4o"). predicate = the property
  (e.g. "input token price"). stated_value = what the answer says it is.
- Skip opinions, hedges, arithmetic and code; count them in "skipped".
- At most {max_claims} claims, most time-sensitive first.
"""

EXTRACT_RETRY_SUFFIX = """

Your previous reply was rejected: {error}
Return only valid JSON matching the schema exactly."""


# ---------------------------------------------------------------------------
# model-based extraction
# ---------------------------------------------------------------------------


async def extract_with_model(
    answer: str,
    llm: LLMClient,
    settings: Settings | None = None,
) -> ExtractionResult:
    """Extract claims with a model, validating strictly and retrying once."""
    settings = settings or get_settings()
    system = EXTRACT_SYSTEM.format(
        today=date.today().isoformat(), max_claims=settings.max_claims_per_answer
    )

    last_error: str | None = None
    for attempt in (1, 2):
        prompt = system if attempt == 1 else system + EXTRACT_RETRY_SUFFIX.format(error=last_error)
        try:
            payload = await llm.complete_json(
                system=prompt,
                user=answer,
                model=settings.extract_model,
                max_tokens=1800,
            )
        except ModelUnavailable as exc:
            last_error = str(exc)
            # A rate-limited call must not be retried. The limit on a free tier
            # is tokens per minute, so retrying immediately spends budget that
            # is already gone, delays the reset, and ends in the same failure —
            # measured as three 429s per answer before this check existed.
            if getattr(exc, "rate_limited", False):
                logger.warning("extraction rate-limited; not retrying: %s", _brief(exc))
                return ExtractionResult(error=f"extraction rate-limited: {_brief(exc)}")
            logger.warning("extraction model unavailable on attempt %d: %s", attempt, _brief(exc))
            if attempt == 2:
                return ExtractionResult(error=f"extraction model unavailable: {_brief(exc)}")
            continue

        try:
            claims = _validate_claims(payload, answer, settings.max_claims_per_answer)
        except ValidationError as exc:
            last_error = _short_validation_error(exc)
            logger.info("extraction validation failed on attempt %d: %s", attempt, last_error)
            if attempt == 2:
                return ExtractionResult(error=f"extraction failed validation: {last_error}")
            continue

        skipped = payload.get("skipped")
        return ExtractionResult(
            claims=claims,
            skipped=int(skipped) if isinstance(skipped, int | float) else 0,
            model_used=settings.extract_model,
        )

    return ExtractionResult(error=last_error or "extraction failed")


def _validate_claims(payload: dict, answer: str, max_claims: int) -> list[Claim]:
    """Validate the model's claim list, repairing spans where we safely can."""
    raw_claims = payload.get("claims") or []
    if not isinstance(raw_claims, list):
        raise ValueError("claims must be a list")

    out: list[Claim] = []
    for item in raw_claims[:max_claims]:
        if not isinstance(item, dict):
            continue
        claim = Claim.model_validate(item)

        # Models are unreliable about character offsets. If the span does not
        # actually contain the claim text, relocate it by searching the answer;
        # drop the claim only if its text is absent entirely.
        if answer[claim.span_start : claim.span_end].strip() != claim.text.strip():
            needle = claim.text.strip()
            found = answer.find(needle)
            if found == -1 and len(needle) > 60:
                found = answer.find(needle[:60])
            if found == -1:
                logger.debug("dropping claim absent from the answer: %r", needle[:60])
                continue
            claim = claim.model_copy(
                update={"span_start": found, "span_end": found + len(needle)}
            )

        out.append(claim)
    return out


def _brief(exc: Exception) -> str:
    """One line of an error, for logs.

    Provider rate-limit bodies are several hundred characters of JSON, and
    repeating them twice per failure made the logs unreadable at exactly the
    moment they needed to be read.
    """
    text = " ".join(str(exc).split())
    return text[:200] + ("…" if len(text) > 200 else "")


def _short_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts) or "invalid claim payload"


# ---------------------------------------------------------------------------
# rule-based extraction
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])|\n+")

#: Sentence-initial words capitalised by grammar, not by being a name. Without
#: this, the subject of "The capital of France is Paris" comes out as "The",
#: which is a useless cache key and a useless search query.
_LEADING_STOPWORDS = frozenset(
    {
        "the", "a", "an", "it", "this", "that", "these", "those", "there",
        "here", "its", "their", "his", "her", "they", "he", "she", "we",
        "you", "i", "at", "in", "on", "of", "for", "as", "and", "but", "so",
        "if", "when", "while", "with", "without", "however", "although",
        "every", "each", "some", "any", "most", "many", "both", "all",
    }
)

_SUBJECT_HINT = re.compile(r"\b([A-Z][\w.+-]*(?:\s+[A-Z][\w.+-]*){0,3})\b")


def extract_with_rules(answer: str, settings: Settings | None = None) -> ExtractionResult:
    """Deterministic, pattern-based extraction. No network, no model."""
    settings = settings or get_settings()

    claims: list[Claim] = []
    skipped = 0
    seen: set[tuple[str, str]] = set()

    for sentence, start in _sentences(answer):
        stripped = sentence.strip()
        if len(stripped) < 12:
            continue

        if any(p.search(stripped) for p in SKIP_PATTERNS):
            skipped += 1
            continue

        # Timeless sentences are recorded as timeless claims, not dropped, so
        # the saved Tavily credit shows up in the report instead of vanishing.
        if any(p.search(stripped) for p in TIMELESS_PATTERNS):
            claims.append(
                _make_claim(stripped, start, _guess_subject(stripped),
                            "timeless statement", "", "timeless")
            )
            continue

        claim = _match_specific(stripped, start, seen) or _match_generic(stripped, start, seen)
        if claim is None:
            skipped += 1
        else:
            claims.append(claim)

        if len(claims) >= settings.max_claims_per_answer:
            break

    return ExtractionResult(claims=claims, skipped=skipped, model_used="rules")


def _match_specific(
    sentence: str, start: int, seen: set[tuple[str, str]]
) -> Claim | None:
    """Layer 1: a hand-written pattern with named groups."""
    for claim_class, pattern, predicate, value_group in CLAIM_RULES:
        m = pattern.search(sentence)
        if not m:
            continue

        groups = m.groupdict()
        raw_subject = (groups.get("subject") or _guess_subject(sentence)).strip(" .,:;")
        subject = _strip_leading_stopwords(raw_subject) or raw_subject
        value = (groups.get(value_group) or m.group(0)).strip(" .,:;")
        role = groups.get("role")
        full_predicate = f"{predicate} ({role})" if role else predicate

        key = (subject.lower(), full_predicate.lower())
        if key in seen:
            return None
        seen.add(key)

        return _make_claim(sentence, start, subject, full_predicate, value, claim_class)
    return None


def _match_generic(
    sentence: str, start: int, seen: set[tuple[str, str]]
) -> Claim | None:
    """Layer 2: any stative assertion carrying a time-sensitivity cue.

    This is what stops an unanticipated phrasing from being silently treated as
    having nothing to check.
    """
    claim_class = infer_class(sentence)
    if claim_class is None:
        return None

    m = GENERIC_ASSERTION.match(sentence)
    if not m:
        return None

    raw_subject = m.group("subject").strip(" .,:;")
    subject = _strip_leading_stopwords(raw_subject) or _guess_subject(sentence)
    value = m.group("value").strip(" .,:;")

    # The predicate is the cue that classified the sentence, which keeps the
    # claim-cache key stable across rewordings of the same question.
    predicate = _predicate_for(claim_class)

    key = (subject.lower(), predicate.lower())
    if key in seen:
        return None
    seen.add(key)

    return _make_claim(sentence, start, subject, predicate, value, claim_class)


def _predicate_for(claim_class: str) -> str:
    return {
        "market": "current market value",
        "pricing": "current price",
        "version": "current version or state",
        "leadership": "current role holder",
        "law": "current legal status",
        "events": "recent event",
    }.get(claim_class, "current state")


def _make_claim(
    sentence: str, start: int, subject: str, predicate: str, value: str, claim_class: str
) -> Claim:
    return Claim(
        text=sentence,
        span_start=start,
        span_end=start + len(sentence),
        subject=subject[:300] or "unknown",
        predicate=predicate[:300],
        stated_value=value[:500],
        **{"class": claim_class},
    )


def _sentences(text: str) -> list[tuple[str, int]]:
    """Split into sentences, keeping each one's offset into the original text."""
    out: list[tuple[str, int]] = []
    cursor = 0
    for piece in _SENTENCE_SPLIT.split(text):
        if not piece:
            continue
        idx = text.find(piece, cursor)
        if idx == -1:
            idx = cursor
        out.append((piece, idx))
        cursor = idx + len(piece)
    return out


def _strip_leading_stopwords(phrase: str) -> str:
    words = phrase.split()
    while words and words[0].lower().strip(".,:;") in _LEADING_STOPWORDS:
        words.pop(0)
    return " ".join(words)


def _guess_subject(sentence: str) -> str:
    """Best-effort subject: the first capitalised phrase that is not a
    sentence-initial function word, else the first few content words."""
    for match in _SUBJECT_HINT.finditer(sentence):
        phrase = match.group(1)
        head = phrase.split()[0].lower().strip(".,:;")
        if head not in _LEADING_STOPWORDS:
            return phrase
        rest = " ".join(phrase.split()[1:])
        if rest:
            return rest

    words = [w for w in sentence.split() if w.lower().strip(".,:;") not in _LEADING_STOPWORDS]
    return " ".join(words[:4]) if words else " ".join(sentence.split()[:4])


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------


async def extract_claims(
    answer: str,
    llm: LLMClient | None = None,
    settings: Settings | None = None,
) -> ExtractionResult:
    """Extract claims with whichever extractor is available.

    Falls back from model to rules rather than failing: a rule-extracted claim
    list is less complete than a model-extracted one, but it is still a real
    check, and degrading beats reporting nothing.
    """
    settings = settings or get_settings()

    if not settings.live_models or llm is None:
        return extract_with_rules(answer, settings)

    result = await extract_with_model(answer, llm, settings)
    if result.error is None:
        return result

    logger.warning("falling back to rule-based extraction: %s", result.error)
    fallback = extract_with_rules(answer, settings)
    fallback.error = None
    fallback.model_used = "rules (model unavailable)"
    return fallback
