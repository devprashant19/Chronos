"""Judging tests.

Every case here is a false positive or false negative that the benchmark
actually produced. They are written as the thing that went wrong, so the
reasoning cannot quietly regress to the naive version.
"""

from __future__ import annotations

import pytest

from chronos.models import Claim, Evidence
from chronos.judge_rules import (
    ROLE_CORROBORATION,
    judge,
    role_assertions,
    value_kind,
    values_agree,
)


def claim_of(stated: str, predicate: str, claim_class: str = "leadership") -> Claim:
    return Claim(
        text="t",
        span_start=0,
        span_end=1,
        subject="Subject",
        predicate=predicate,
        stated_value=stated,
        **{"class": claim_class},
    )


def pages(*texts: str, tier: str = "news") -> list[Evidence]:
    return [
        Evidence(url=f"https://s{i}.example/p", snippet=t, tier=tier)
        for i, t in enumerate(texts)
    ]


def calls(result: dict) -> list[str]:
    return [s["call"] for s in result["sources"]]


# ---------------------------------------------------------------------------
# typed value comparison
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,predicate,kind",
    [
        ("$5.00 per million", "price", "money"),
        ("3.11", "latest version", "version"),
        ("18", "current release", "version"),
        ("7.25%", "interest rate", "percent"),
        ("Jack Dorsey", "holder of role", "name"),
        ("a blue bird", "branding", "phrase"),
        ("", "anything", "empty"),
    ],
)
def test_value_kind(value, predicate, kind):
    assert value_kind(value, predicate) == kind


def test_values_agree_compares_numbers_not_strings():
    assert values_agree("$5.00", "5.00 dollars")
    assert values_agree("5", "5.0")
    assert not values_agree("$5.00", "$2.50")


def test_a_page_of_numbers_cannot_contradict_a_name_claim():
    """The original false positive: judging "Satya Nadella is the CEO" against
    a page of unrelated figures reported a contradiction."""
    c = claim_of("Satya Nadella", "holder of role (CEO)")
    result = judge(c, pages("Azure B1s instances cost $0.0104 per hour. Storage is $0.018."))
    assert "contradicts" not in calls(result)


# ---------------------------------------------------------------------------
# role judging: grammatical, symmetric, corroborated
# ---------------------------------------------------------------------------


def test_role_assertion_requires_grammar_not_proximity():
    """A window around the word "CEO" picked up headings, product names and
    bylines as rival role-holders."""
    for text in (
        "World Politics Business Technology. Microsoft CEO on AI safety.",
        "Amazon CEO says shopping agents are coming. By Sebastian Herrera.",
        "Meta Quest 3 experiences and apps for CEO demos",
    ):
        _asserts, others = role_assertions(text, "holder of role (CEO)", "Satya Nadella")
        assert not others, f"spurious rival from {text!r}: {others}"


def test_role_assertion_finds_a_genuine_rival():
    _asserts, others = role_assertions(
        "Twitter CEO Linda Yaccarino announced new policies today.",
        "holder of role (CEO)",
        "Jack Dorsey",
    )
    assert any("Yaccarino" in o for o in others)


def test_role_assertion_is_symmetric():
    """Mere mention is not support. "Dorsey stepped down" names Dorsey while
    asserting the opposite of "Dorsey is the CEO"."""
    asserts, _ = role_assertions(
        "Jack Dorsey stepped down as chief executive in November 2021.",
        "holder of role (CEO)",
        "Jack Dorsey",
    )
    assert not asserts

    asserts, _ = role_assertions(
        "Microsoft CEO Satya Nadella spoke today.", "holder of role (CEO)", "Satya Nadella"
    )
    assert asserts


def test_one_confirming_page_blocks_a_contradiction():
    """Direct confirmation beats absence elsewhere."""
    c = claim_of("Satya Nadella", "holder of role (CEO)")
    result = judge(
        c,
        pages(
            "Microsoft CEO Satya Nadella spoke today.",
            "World Politics Business section.",
            "Azure pricing per hour.",
        ),
    )
    assert "supports" in calls(result)
    assert "contradicts" not in calls(result)
    assert result["current_value"] == "Satya Nadella"


def test_a_single_uncorroborated_rival_is_insufficient():
    """One page naming someone else is as likely a parsing artefact as a real
    change, so it must not condemn the claim on its own."""
    c = claim_of("Tim Cook", "holder of role (CEO)")
    result = judge(c, pages("New Apple CEO John Ternus charts a course.", "Apple store hours."))
    assert "contradicts" not in calls(result)


def test_two_corroborating_rivals_do_contradict():
    c = claim_of("Jack Dorsey", "holder of role (CEO)")
    result = judge(
        c,
        pages(
            "Twitter CEO Linda Yaccarino announced policies.",
            "Linda Yaccarino, the CEO, said today.",
            "Dorsey stepped down in 2021.",
        ),
    )
    assert calls(result).count("contradicts") >= ROLE_CORROBORATION
    assert "Yaccarino" in result["current_value"]


def test_places_are_not_mistaken_for_people():
    """"United Kingdom" was reported as a rival monarch."""
    c = claim_of("Charles III", "holder of role (monarch)")
    _asserts, others = role_assertions(
        "The monarch of the United Kingdom attended.", "holder of role (monarch)", "Charles III"
    )
    assert not any("United Kingdom" in o for o in others)


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------


def test_version_prefix_counts_as_agreement():
    """"Python 3" and "3.15" describe the same line; comparing them numerically
    called it a 4% mismatch and reported a contradiction."""
    c = claim_of("3", "latest version", claim_class="version")
    result = judge(c, pages("The current stable release is 3.15.1 as of today."))
    assert "contradicts" not in calls(result)


def test_different_versions_do_contradict():
    c = claim_of("3.11", "latest version", claim_class="version")
    result = judge(c, pages("The current stable release is 3.15.1 as of today."))
    assert "contradicts" in calls(result)
    assert "3.15" in result["current_value"]


def test_money_mismatch_contradicts():
    c = claim_of("$5.00", "input token price", claim_class="pricing")
    result = judge(c, pages("GPT-4o is priced at $2.50 per million input tokens."))
    assert "contradicts" in calls(result)


def test_money_match_supports():
    c = claim_of("$2.50", "input token price", claim_class="pricing")
    result = judge(c, pages("GPT-4o is priced at $2.50 per million input tokens."))
    assert "supports" in calls(result)


# ---------------------------------------------------------------------------
# recorded fixtures
# ---------------------------------------------------------------------------


def test_recorded_judgements_are_read_back():
    """Offline mode replays a fixture's own call rather than re-deriving it."""
    c = claim_of("3.11", "latest version", claim_class="version")
    evidence = pages("irrelevant body text")
    recorded = {
        evidence[0].url: {
            "call": "contradicts",
            "quote": "a later release exists",
            "current_value": "a later 3.x release",
        }
    }
    result = judge(c, evidence, recorded)
    assert calls(result) == ["contradicts"]
    assert result["current_value"] == "a later 3.x release"


def test_no_usable_evidence_is_insufficient_everywhere():
    c = claim_of("Jack Dorsey", "holder of role (CEO)")
    result = judge(c, pages("", ""))
    assert set(calls(result)) == {"insufficient"}
    assert result["confidence"] < 0.5
