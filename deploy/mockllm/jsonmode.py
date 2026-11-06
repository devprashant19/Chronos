"""Strict-JSON replies for the extraction and judge prompts.

Why this exists: Chronos's *designed* path uses a model for claim extraction
and judging, and that path has a lot of machinery around it — a JSON contract,
Pydantic validation, span repair, a retry-with-the-error, per-source calls
feeding the score. None of that runs when there is no model key, so none of it
gets tested.

So mockllm recognises Chronos's two system prompts and answers them with valid
JSON. It is not pretending to be intelligent: the extractor here is a handful
of regexes and the judge is a substring check. What it does is let the whole
model path execute end to end with no credentials, so the plumbing is verified
and only the judgement quality depends on a real model.

Spans are computed as real offsets into the answer, because span repair is one
of the things worth testing.
"""

from __future__ import annotations

import json
import re
from typing import Any

# How Chronos's two prompts identify themselves.
EXTRACT_MARKER = "You extract time-sensitive factual claims"
JUDGE_MARKER = "You judge whether a claim is still true"

#: Split on sentence-ending punctuation ONLY when followed by whitespace and a
#: capital or digit. A naive `[^.!?]+` split cuts "3.11" into "3." and "11",
#: and "$5.00" into two fragments — which produced a claim whose stated value
#: was "3" and a second, meaningless claim about "00 per million".
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])|\n+")

_MONEY = re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?")
_VERSION = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")
_BARE_VERSION = re.compile(r"\b(?:version|release|lts)\b[^.]{0,20}?\b(\d+)\b", re.I)
_PERSON = re.compile(r"\b[A-Z][a-z]{1,15}(?:\s+[A-Z][a-z.]{1,15}){1,2}\b")
_PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s?%")

#: Cue → class, most volatile first so the first match wins.
_CUES: list[tuple[str, re.Pattern[str]]] = [
    ("market", re.compile(r"\b(trading at|spot price|market cap|all-time high|bitcoin|btc)\b", re.I)),
    ("pricing", re.compile(r"\b(price[sd]?|pricing|costs?|per million|per 1k|free tier)\b", re.I)),
    ("version", re.compile(r"\b(version|release|lts|latest|newest|logo|consensus)\b", re.I)),
    ("leadership", re.compile(r"\b(ceo|cto|cfo|chief executive|president|prime minister|chancellor|monarch|king|queen)\b", re.I)),
    ("law", re.compile(r"\b(act\b|law|regulation|directive|gdpr|member state|in force|minimum wage)\b", re.I)),
    ("events", re.compile(r"\b(won|launched|announced|acquired|raised|scheduled)\b", re.I)),
]

_TIMELESS = re.compile(
    r"\b(capital of|boils? at|freezes? at|speed of light|chemical formula|atomic number|"
    r"stands for|wrote|painted|square root|prime number|lifo|fifo)\b",
    re.I,
)

_OPINION = re.compile(r"\b(i think|in my opinion|you should|i recommend|it depends)\b", re.I)


def is_extract_prompt(system: str) -> bool:
    return EXTRACT_MARKER in (system or "")


def is_judge_prompt(system: str) -> bool:
    return JUDGE_MARKER in (system or "")


def _classify(sentence: str) -> str | None:
    if _OPINION.search(sentence):
        return None
    if _TIMELESS.search(sentence):
        return "timeless"
    for name, pattern in _CUES:
        if pattern.search(sentence):
            return name
    return None


def _stated_value(sentence: str, claim_class: str) -> str:
    if claim_class in {"pricing", "market"}:
        m = _MONEY.search(sentence) or _PERCENT.search(sentence)
        if m:
            return m.group(0)
    if claim_class == "version":
        m = _VERSION.search(sentence)
        if m:
            return m.group(0)
        m = _BARE_VERSION.search(sentence)
        if m:
            return m.group(1)
    if claim_class == "leadership":
        m = _PERSON.search(sentence)
        if m:
            return m.group(0)
    m = _MONEY.search(sentence) or _VERSION.search(sentence)
    return m.group(0) if m else ""


def _subject(sentence: str) -> str:
    stop = {
        "the", "a", "an", "it", "this", "that", "there", "its", "their", "his",
        "her", "they", "he", "she", "at", "in", "on", "of", "for", "as", "and",
    }
    for m in re.finditer(r"\b([A-Z][\w.+-]*(?:\s+[A-Z][\w.+-]*){0,2})\b", sentence):
        phrase = m.group(1)
        head = phrase.split()[0].lower().strip(".,:;")
        if head not in stop:
            return phrase
        rest = " ".join(phrase.split()[1:])
        if rest:
            return rest
    words = [w for w in sentence.split() if w.lower().strip(".,:;") not in stop]
    return " ".join(words[:3]) or "unknown"


_PREDICATE = {
    "market": "current market value",
    "pricing": "current price",
    "version": "latest version",
    "leadership": "holder of role",
    "law": "legal status",
    "events": "recent event",
    "timeless": "timeless statement",
}


def extraction_reply(answer: str, max_claims: int = 12) -> str:
    """A valid claims payload, with real character offsets into `answer`."""
    claims: list[dict[str, Any]] = []
    skipped = 0

    cursor = 0
    for piece in _SENTENCE_SPLIT.split(answer):
        if not piece:
            continue
        found = answer.find(piece, cursor)
        if found < 0:
            found = cursor
        cursor = found + len(piece)

        sentence = piece.strip()
        if len(sentence) < 12:
            continue

        claim_class = _classify(sentence)
        if claim_class is None:
            skipped += 1
            continue

        start = answer.find(sentence, found)
        if start < 0:
            start = found

        claims.append(
            {
                "text": sentence,
                "span_start": start,
                "span_end": start + len(sentence),
                "subject": _subject(sentence),
                "predicate": _PREDICATE[claim_class],
                "stated_value": _stated_value(sentence, claim_class),
                "class": claim_class,
                "as_of_hint": None,
            }
        )
        if len(claims) >= max_claims:
            break

    return json.dumps({"claims": claims, "skipped": skipped})


_SOURCE_BLOCK = re.compile(r"^\[(\d+)\]\s+url:\s*(\S+)", re.M)
_STATED = re.compile(r"^Value the answer stated:\s*(.+)$", re.M)


def judge_reply(user_message: str) -> str:
    """A per-source judgement, derived by looking for the stated value.

    Deliberately simple: this is a test double. It parses the rendered source
    list out of the prompt, looks for the stated value in each source's text,
    and reports supports/contradicts/insufficient accordingly — which is enough
    to drive the real scoring code.
    """
    stated_match = _STATED.search(user_message)
    stated = (stated_match.group(1).strip() if stated_match else "").strip()
    if stated in {"(none given)", ""}:
        stated = ""

    blocks = list(_SOURCE_BLOCK.finditer(user_message))
    sources: list[dict[str, str]] = []
    current_value = stated
    saw_signal = False

    for i, block in enumerate(blocks):
        url = block.group(2)
        body = user_message[block.end() : (blocks[i + 1].start() if i + 1 < len(blocks) else len(user_message))]

        call = "insufficient"
        quote = ""

        if stated and stated.lower() in body.lower():
            call = "supports"
            saw_signal = True
            quote = _sentence_with(body, stated)
        else:
            # Look for a same-shaped value that differs from the stated one.
            rival = _MONEY.search(body) or _VERSION.search(body)
            if rival and stated:
                call = "contradicts"
                saw_signal = True
                current_value = rival.group(0)
                quote = _sentence_with(body, rival.group(0))

        sources.append({"url": url, "call": call, "quote": quote[:300]})

    published = re.search(r"^published:\s*(\d{4}-\d{2}-\d{2})", user_message, re.M)

    return json.dumps(
        {
            "sources": sources,
            "current_value": current_value,
            "confidence": 0.8 if saw_signal else 0.2,
            "best_source_url": blocks[0].group(2) if blocks else "",
            "publish_date": published.group(1) if published else None,
        }
    )


def _sentence_with(text: str, needle: str) -> str:
    for sentence in re.split(r"(?<=[.!?])\s+", text[:3000]):
        if needle and needle.lower() in sentence.lower():
            return sentence.strip()
    return ""
