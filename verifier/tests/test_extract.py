"""Extraction tests.

The failure these guard against is the quiet one: a claim that is never
extracted produces a score of 100 and a verdict of "current". A staleness
detector that says nothing looks exactly like one that approves.

So the recall cases below are written as the specific sentences that were
being missed, and `test_no_claim_is_silently_approved` states the invariant
directly.
"""

from __future__ import annotations

import pytest

from chronos.extract import extract_with_rules
from chronos.rules import infer_class


def only_claim(answer: str):
    result = extract_with_rules(answer)
    assert result.claims, f"no claim extracted from {answer!r}"
    return result.claims[0]


# ---------------------------------------------------------------------------
# recall: the sentences the first version of the extractor missed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer,expected_class",
    [
        ("Elizabeth II is the reigning monarch of the United Kingdom.", "leadership"),
        ("Angela Merkel is the Chancellor of Germany.", "leadership"),
        ("Jack Dorsey is the CEO of Twitter.", "leadership"),
        ("The CEO of Microsoft is Satya Nadella.", "leadership"),
        ("Bob Iger has retired and is no longer involved with Disney.", "leadership"),
        ("Node.js 18 is the current LTS release.", "version"),
        ("The current version of Java is Java 17.", "version"),
        ("The latest stable release of Python is 3.11.", "version"),
        ("Ethereum uses proof-of-work consensus.", "version"),
        ("Bitcoin is trading at about $43,200.", "market"),
        ("The EU AI Act is still a proposal under negotiation.", "law"),
        ("The United Kingdom is a member state of the European Union.", "law"),
        ("Heroku offers a free tier for hobby applications.", "pricing"),
    ],
)
def test_extracts_time_sensitive_claims(answer, expected_class):
    claim = only_claim(answer)
    assert claim.claim_class == expected_class, (
        f"{answer!r} classified {claim.claim_class}, expected {expected_class}"
    )
    assert claim.subject, "a claim needs a subject to build a search query from"
    assert claim.predicate


def test_no_claim_is_silently_approved():
    """Every time-sensitive sentence must produce either a claim or an explicit
    skip. Producing neither is how an answer scores 100 by accident."""
    sentences = [
        "Jack Dorsey is the CEO of Twitter.",
        "Node.js 18 is the current LTS release.",
        "Bitcoin is trading at about $43,200.",
        "The latest stable release of Python is 3.11.",
    ]
    for s in sentences:
        result = extract_with_rules(s)
        assert result.claims, f"{s!r} produced no claim and no explicit skip"


# ---------------------------------------------------------------------------
# precision: what must NOT be checked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "The capital of France is Paris.",
        "Water boils at 100 degrees Celsius at standard atmospheric pressure.",
        "The speed of light in a vacuum is about 299,792,458 metres per second.",
        "William Shakespeare wrote Hamlet.",
        "SQL stands for Structured Query Language.",
    ],
)
def test_timeless_claims_are_marked_timeless(answer):
    """Timeless claims are recorded, not dropped: that is what makes the saved
    Tavily credit visible in the report rather than invisible."""
    claim = only_claim(answer)
    assert claim.claim_class == "timeless"


@pytest.mark.parametrize(
    "answer",
    [
        "I think you should consider using Postgres for this.",
        "    def reverse(s): return s[::-1]",
        "import json",
        "In my opinion the second approach reads better.",
    ],
)
def test_opinions_and_code_are_skipped(answer):
    result = extract_with_rules(answer)
    assert not result.claims, f"{answer!r} should not produce a claim"


# ---------------------------------------------------------------------------
# field quality
# ---------------------------------------------------------------------------


def test_subject_drops_leading_articles():
    """"the United Kingdom" and "United Kingdom" must share a claim-cache key."""
    claim = only_claim("Elizabeth II is the reigning monarch of the United Kingdom.")
    assert not claim.subject.lower().startswith("the ")


def test_stated_value_is_the_asserted_value_not_the_subject():
    claim = only_claim("Bob Iger has retired and is no longer involved with Disney.")
    assert "retired" in claim.stated_value.lower()
    assert claim.stated_value.lower() != claim.subject.lower()


def test_leadership_value_is_the_person():
    claim = only_claim("Jack Dorsey is the CEO of Twitter.")
    assert claim.stated_value == "Jack Dorsey"
    assert "twitter" in claim.subject.lower()


def test_version_value_is_the_version():
    claim = only_claim("The latest stable release of Python is 3.11.")
    assert claim.stated_value == "3.11"
    assert "python" in claim.subject.lower()


def test_spans_point_at_the_claim_text():
    answer = "Python is great. The latest stable release of Python is 3.11. Use it."
    result = extract_with_rules(answer)
    for claim in result.claims:
        assert answer[claim.span_start : claim.span_end].strip() == claim.text.strip()


def test_multiple_claims_from_one_answer():
    answer = (
        "The latest stable release of Python is 3.11. "
        "Bitcoin is trading at about $43,200. "
        "The capital of France is Paris."
    )
    result = extract_with_rules(answer)
    classes = {c.claim_class for c in result.claims}
    assert {"version", "market", "timeless"} <= classes


def test_duplicate_claims_are_deduplicated():
    answer = "Jack Dorsey is the CEO of Twitter. Jack Dorsey is the CEO of Twitter."
    result = extract_with_rules(answer)
    keys = {(c.subject.lower(), c.predicate.lower()) for c in result.claims}
    assert len(keys) == len(result.claims)


def test_extraction_never_raises_on_odd_input():
    for answer in ["", " ", "\n\n", "?!?", "a", "é" * 50, "x" * 5000]:
        extract_with_rules(answer)  # must not raise


# ---------------------------------------------------------------------------
# class inference
# ---------------------------------------------------------------------------


def test_infer_class_picks_the_most_volatile_cue():
    """A sentence with both a price and a version cue must take the faster one,
    because the TTL has to protect the fastest-moving fact."""
    assert infer_class("The latest version costs $5.00 per million tokens") == "pricing"
    assert infer_class("The share price of the latest release") == "market"


def test_infer_class_returns_none_without_a_cue():
    assert infer_class("This sentence contains nothing time sensitive at all") is None
