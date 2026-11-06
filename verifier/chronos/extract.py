"""Pulling time-sensitive claims out of an answer.

Two extractors, same output contract:

  * `extract_with_model` asks a model for strict JSON and validates it against
    the `Claim` schema. On a validation failure it retries once, handing the
    model its own error, then gives up — a wrong claim list is worse than no
    claim list, because it produces confident scores about sentences the answer
    never made.

  * `extract_with_rules` finds claims with patterns only. It is the offline and
    model-unavailable path, and it is deterministic, which is what makes the
    demo and the benchmark reproducible.

What both must do is *skip* the sentences that do not need checking. Opinions,
arithmetic, code and definitions have no "current value" to look up, and
spending a Tavily credit on "the capital of France is Paris" is the easiest
waste in the whole system. The benchmark measures this as the timeless skip
rate.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from pydantic import ValidationError

from .llm import LLMClient, ModelUnavailable
from .models import Claim, ExtractionResult
from .settings import Settings, get_settings

logger = logging.getLogger("chronos.extract")

EXTRACT_SYSTEM = """You extract time-sensitive factual claims from an AI assistant's answer.

Today's date is {today}.

Return STRICT JSON, no prose, in exactly this shape:
{{"claims": [{{"text": "...", "span_start": 0, "span_end": 0, "subject": "...",
"predicate": "...", "stated_value": "...", "class": "...", "as_of_hint": null}}],
"skipped": 0}}

Rules:
- A claim is an assertion whose truth can change over time AND that could be
  checked against a public source today.
- "class" must be exactly one of: market, pricing, version, leadership, law,
  events, timeless.
    market     prices, rates, market caps, anything quoted live
    pricing    product or service prices, plans, per-token costs
    version    software versions, releases, model names, deprecations
    leadership people holding a role: CEO, minister, manager
    law        statutes, regulations, effective dates, legal status
    events     results, schedules, recent happenings
    timeless   facts that do not change
- "span_start" and "span_end" are character offsets into the answer EXACTLY as
  given, such that answer[span_start:span_end] is the claim's sentence.
- "subject" is the entity the claim is about, normalised (e.g. "OpenAI GPT-4o").
- "predicate" is the property asserted (e.g. "input token price").
- "stated_value" is what the answer says that property is, verbatim.
- SKIP and do not return: opinions, recommendations, hedged statements,
  arithmetic, code, definitions, and anything with no checkable public source.
  Count what you skipped in "skipped".
- Return at most {max_claims} claims, the most time-sensitive first.
- If the answer contains no time-sensitive claims, return {{"claims": [], "skipped": N}}.
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
            logger.warning("extraction model unavailable on attempt %d: %s", attempt, exc)
            last_error = str(exc)
            if attempt == 2:
                return ExtractionResult(error=f"extraction model unavailable: {exc}")
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
            skipped=int(skipped) if isinstance(skipped, (int, float)) else 0,
            model_used=settings.extract_model,
        )

    return ExtractionResult(error=last_error or "extraction failed")


def _validate_claims(payload: dict, answer: str, max_claims: int) -> list[Claim]:
    """Validate the model's claim list, repairing spans where we safely can."""
    raw_claims = payload.get("claims")
    if raw_claims is None:
        raw_claims = []
    if not isinstance(raw_claims, list):
        raise ValidationError.from_exception_data(
            "ExtractionResult",
            [
                {
                    "type": "list_type",
                    "loc": ("claims",),
                    "input": raw_claims,
                }
            ],
        )

    out: list[Claim] = []
    for item in raw_claims[:max_claims]:
        if not isinstance(item, dict):
            continue
        claim = Claim.model_validate(item)

        # Models are unreliable about character offsets. If the span does not
        # actually contain the claim text, relocate it by searching the answer;
        # only drop the claim if the text is not present at all.
        if answer[claim.span_start : claim.span_end].strip() != claim.text.strip():
            found = answer.find(claim.text.strip())
            if found == -1:
                snippet = claim.text.strip()[:60]
                found = answer.find(snippet) if snippet else -1
            if found == -1:
                logger.debug("dropping claim whose text is absent from the answer: %r", claim.text[:60])
                continue
            claim = claim.model_copy(
                update={"span_start": found, "span_end": found + len(claim.text.strip())}
            )

        out.append(claim)
    return out


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

#: Sentences that look like these never need checking.
_SKIP_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"^\s*(def |class |import |from |#|//|<|\{|\}|return |print\()",
        r"^\s{4,}\S",  # indented code
        r"\b(i think|in my opinion|you should|i recommend|consider|it depends|generally|typically)\b",
        r"\b(for example|e\.g\.|such as)\b.*\b(code|function|snippet)\b",
        r"^\s*\d+\s*[-+*/x]\s*\d+",
    )
]

_CLAIM_RULES: list[tuple[str, re.Pattern[str], str, str]] = [
    # (class, pattern, predicate, value-group-name)
    (
        "market",
        re.compile(
            r"(?P<subject>bitcoin|btc|ethereum|eth|[A-Z]{1,5}\s+(?:stock|shares))\b[^.]{0,60}?"
            r"(?:trading at|is at|price of|worth|costs?)\s*(?P<value>[$€£]\s?[\d,]+(?:\.\d+)?(?:\s?(?:k|m|bn|billion|million|trillion))?)",
            re.I,
        ),
        "spot price",
        "value",
    ),
    (
        "market",
        re.compile(
            r"market cap(?:italisation|italization)?\s*(?:of|sits near|is|at)?\s*(?P<value>[$€£]\s?[\d,.]+\s?(?:k|m|bn|billion|million|trillion)?)",
            re.I,
        ),
        "market capitalisation",
        "value",
    ),
    (
        "pricing",
        re.compile(
            r"(?P<value>[$€£]\s?[\d,]+(?:\.\d+)?)\s*(?:per|/)\s*(?P<unit>million|1m|1k|thousand|month|year|seat|user|request)\b[^.]{0,30}?(?P<kind>input|output|token|tokens)?",
            re.I,
        ),
        "price",
        "value",
    ),
    (
        "pricing",
        re.compile(
            r"\b(?:costs?|priced at|is)\s*(?P<value>[$€£]\s?[\d,]+(?:\.\d+)?)\b",
            re.I,
        ),
        "price",
        "value",
    ),
    (
        "version",
        re.compile(
            r"(?:latest|current|newest|most recent)\s+(?:stable\s+)?(?:release|version)\s+of\s+(?P<subject>[A-Za-z][\w .+#-]{1,40}?)\s+is\s+(?P<value>v?\d[\w.]*)",
            re.I,
        ),
        "latest version",
        "value",
    ),
    (
        "version",
        re.compile(
            r"(?P<subject>[A-Z][\w .+#-]{1,30}?)\s+(?P<value>v?\d+\.\d+(?:\.\d+)?)\s+is\s+the\s+(?:latest|current|newest|version most)",
            re.I,
        ),
        "latest version",
        "value",
    ),
    (
        "leadership",
        re.compile(
            r"(?P<value>[A-Z][a-z]+(?:\s+[A-Z][a-z.]+){0,2})\s+is\s+(?:the\s+)?(?P<role>chief executive|ceo|cto|cfo|coo|president|prime minister|chair(?:man|person)?)\s+of\s+(?P<subject>[A-Z][\w .&-]{1,40})",
            re.I,
        ),
        "holder of role",
        "value",
    ),
    (
        "leadership",
        re.compile(
            r"(?:the\s+)?(?P<role>ceo|cto|cfo|president|prime minister)\s+of\s+(?P<subject>[A-Z][\w .&-]{1,40}?)\s+is\s+(?P<value>[A-Z][a-z]+(?:\s+[A-Z][a-z.]+){0,2})",
            re.I,
        ),
        "holder of role",
        "value",
    ),
    (
        "law",
        re.compile(
            r"(?P<subject>[A-Z][\w .-]{2,50}(?:Act|Regulation|Directive|Law|Bill))\b[^.]{0,80}?"
            r"(?P<value>is still a (?:legislative )?proposal|has not yet|comes into force[^.]{0,30}|applies from[^.]{0,30}|entered into force[^.]{0,30})",
            re.I,
        ),
        "legal status",
        "value",
    ),
    (
        "events",
        re.compile(
            r"(?P<subject>[A-Z][\w .&-]{2,40})\s+(?:won|lost|announced|launched|acquired|raised)\s+(?P<value>[^.]{3,60})",
        ),
        "recent event",
        "value",
    ),
]

_TIMELESS_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"\bcapital of [A-Z]",
        r"\bboil(?:s|ing)? at\b",
        r"\bspeed of light\b",
        r"\b(?:sits on|is located|is situated) (?:the|in|on)\b",
        r"\bquadratic|\blinear(?:ly)? with\b",
        r"\bhas been the (?:seat|home)\b",
        r"\bdegrees (?:celsius|fahrenheit)\b",
    )
]


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

        if any(p.search(stripped) for p in _SKIP_PATTERNS):
            skipped += 1
            continue

        # A sentence that is clearly timeless is recorded as a timeless claim
        # so the report can show it as deliberately skipped, which is what
        # makes the credit saving visible rather than invisible.
        if any(p.search(stripped) for p in _TIMELESS_PATTERNS):
            claims.append(
                Claim(
                    text=stripped,
                    span_start=start,
                    span_end=start + len(stripped),
                    subject=_guess_subject(stripped),
                    predicate="timeless statement",
                    stated_value="",
                    **{"class": "timeless"},
                )
            )
            continue

        matched = False
        for claim_class, pattern, predicate, value_group in _CLAIM_RULES:
            m = pattern.search(stripped)
            if not m:
                continue

            groups = m.groupdict()
            subject = (groups.get("subject") or _guess_subject(stripped)).strip(" .,:;")
            value = (groups.get(value_group) or m.group(0)).strip(" .,:;")
            role = groups.get("role")
            full_predicate = f"{predicate} ({role})" if role else predicate

            key = (subject.lower(), full_predicate.lower())
            if key in seen:
                continue
            seen.add(key)

            claims.append(
                Claim(
                    text=stripped,
                    span_start=start,
                    span_end=start + len(stripped),
                    subject=subject,
                    predicate=full_predicate,
                    stated_value=value,
                    **{"class": claim_class},
                )
            )
            matched = True
            break

        if not matched:
            skipped += 1

        if len(claims) >= settings.max_claims_per_answer:
            break

    return ExtractionResult(claims=claims, skipped=skipped, model_used="rules")


def _sentences(text: str) -> list[tuple[str, int]]:
    """Split into sentences, keeping each one's offset into the original text."""
    out: list[tuple[str, int]] = []
    cursor = 0
    for piece in _SENTENCE_SPLIT.split(text):
        if piece is None:
            continue
        idx = text.find(piece, cursor)
        if idx == -1:
            idx = cursor
        out.append((piece, idx))
        cursor = idx + len(piece)
    return out


_SUBJECT_HINT = re.compile(r"\b([A-Z][\w.+-]*(?:\s+[A-Z][\w.+-]*){0,3})\b")

#: Sentence-initial words that are capitalised by grammar, not by being a name.
#: Without this the subject of "The capital of France is Paris" comes out as
#: "The", which makes a useless cache key and a useless search query.
_LEADING_STOPWORDS = frozenset(
    {
        "the", "a", "an", "it", "this", "that", "these", "those", "there",
        "here", "its", "their", "his", "her", "they", "he", "she", "we",
        "you", "i", "at", "in", "on", "of", "for", "as", "and", "but", "so",
        "if", "when", "while", "with", "without", "however", "although",
        "every", "each", "some", "any", "most", "many", "both", "all",
    }
)


def _guess_subject(sentence: str) -> str:
    """Best-effort subject: the first capitalised phrase that is not a
    sentence-initial function word, else the first few content words."""
    for match in _SUBJECT_HINT.finditer(sentence):
        phrase = match.group(1)
        head = phrase.split()[0].lower().strip(".,:;")
        if head not in _LEADING_STOPWORDS:
            return phrase
        # "The capital of France" -> keep looking, but remember the tail.
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
    """Extract claims using whichever extractor is available.

    Falls back from model to rules rather than failing: a rule-extracted claim
    list is less complete than a model-extracted one, but it is still a real
    check, and degrading is always better than reporting nothing.
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
