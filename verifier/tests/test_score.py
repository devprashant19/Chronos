"""Scoring tests.

These pin the behaviours that were wrong at some point during the build, so
they cannot regress quietly:

  * A is computed over the sources that addressed the claim, not over every
    retrieved result.
  * No evidence is `unverifiable`, never `outdated`.
  * A contradicted claim is `outdated` even though the score formula alone
    cannot express direction.
  * Decay reduces a score as a claim ages against its re-check interval.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chronos.models import Evidence
from chronos.score import (
    agreement,
    answer_score,
    answer_ttl_seconds,
    classify_source,
    date_from_url,
    decay,
    is_contradicted,
    quality,
    recency,
    score_claim,
    verdict_for,
)


def ev(call: str, url: str = "https://example.com/a", tier: str = "other", published=None) -> Evidence:
    return Evidence(url=url, call=call, tier=tier, published_date=published)


# ---------------------------------------------------------------------------
# A: agreement
# ---------------------------------------------------------------------------


def test_agreement_all_supporting():
    a, sup, con, insuf = agreement([ev("supports"), ev("supports")])
    assert a == 1.0
    assert (sup, con, insuf) == (2, 0, 0)


def test_agreement_all_contradicting_floors_at_zero():
    a, sup, con, _ = agreement([ev("contradicts"), ev("contradicts")])
    assert a == 0.0
    assert (sup, con) == (0, 2)


def test_agreement_ignores_insufficient_sources_in_the_denominator():
    """The bug this pins: one confirming source plus four irrelevant ones used
    to give A = 0.2 and a score of 44.5 — "outdated" for a claim that had just
    been confirmed and never disputed."""
    evidence = [ev("supports")] + [ev("insufficient") for _ in range(4)]
    a, sup, con, insuf = agreement(evidence)
    assert a == 1.0, "insufficient sources must not dilute agreement"
    assert (sup, con, insuf) == (1, 0, 4)


def test_agreement_no_evidence_at_all():
    a, sup, con, insuf = agreement([])
    assert a == 0.0
    assert (sup, con, insuf) == (0, 0, 0)


def test_agreement_mixed():
    a, *_ = agreement([ev("supports"), ev("supports"), ev("contradicts")])
    assert a == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# R: recency
# ---------------------------------------------------------------------------


def test_recency_recent_source_is_full_marks():
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=2)).date().isoformat()
    assert recency([ev("supports", published=recent)], now=now) == 1.0


def test_recency_decays_linearly_then_floors():
    now = datetime.now(timezone.utc)
    mid = (now - timedelta(days=186)).date().isoformat()
    old = (now - timedelta(days=400)).date().isoformat()
    assert 0.3 < recency([ev("supports", published=mid)], now=now) < 0.7
    assert recency([ev("supports", published=old)], now=now) == 0.0


def test_recency_undated_sources_are_not_treated_as_ancient():
    """An authoritative page with no date should not be scored as a year old."""
    assert recency([ev("supports")]) == 0.4


def test_recency_uses_the_freshest_source():
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(days=1)).date().isoformat()
    stale = (now - timedelta(days=300)).date().isoformat()
    assert recency([ev("supports", published=stale), ev("supports", published=fresh)], now=now) == 1.0


# ---------------------------------------------------------------------------
# Q: quality
# ---------------------------------------------------------------------------


def test_quality_tiers():
    assert quality([ev("supports", tier="official")]) == 1.0
    assert quality([ev("supports", tier="news")]) == 0.7
    assert quality([ev("supports", tier="other")]) == 0.4
    assert quality([]) == 0.0


def test_quality_takes_the_best_source():
    assert quality([ev("supports", tier="other"), ev("supports", tier="official")]) == 1.0


def test_classify_source():
    assert classify_source("https://www.python.org/downloads/", "Python") == "official"
    assert classify_source("https://www.dol.gov/whd", "minimum wage") == "official"
    assert classify_source("https://www.reuters.com/tech", "Twitter") == "news"
    assert classify_source("https://some-blog.example/post", "Twitter") == "other"


def test_classify_source_official_requires_subject_match():
    """openai.com is official for an OpenAI claim, not for a Python one."""
    assert classify_source("https://openai.com/api/pricing/", "OpenAI GPT-4o") == "official"
    assert classify_source("https://openai.com/api/pricing/", "Python") == "other"


# ---------------------------------------------------------------------------
# D: decay
# ---------------------------------------------------------------------------


def test_decay_zero_when_just_checked():
    now = datetime.now(timezone.utc)
    assert decay("market", now, now=now) == 0.0


def test_decay_caps_at_one():
    now = datetime.now(timezone.utc)
    long_ago = now - timedelta(days=400)
    assert decay("market", long_ago, now=now) == 1.0


def test_decay_is_relative_to_the_class_interval():
    """The same age costs a market claim far more than a legal one."""
    now = datetime.now(timezone.utc)
    checked = now - timedelta(hours=1)
    assert decay("market", checked, now=now) == pytest.approx(1.0, abs=0.01)
    assert decay("law", checked, now=now) < 0.01


def test_decay_timeless_never_decays():
    now = datetime.now(timezone.utc)
    assert decay("timeless", now - timedelta(days=1000), now=now) == 0.0


# ---------------------------------------------------------------------------
# the composite score
# ---------------------------------------------------------------------------


def test_score_well_supported_claim_is_current():
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(days=1)).date().isoformat()
    evidence = [
        ev("supports", tier="official", published=fresh),
        ev("supports", tier="news", published=fresh),
    ]
    bd = score_claim("version", evidence, confidence=0.9, last_checked=now, now=now)
    assert bd.score >= 80
    assert verdict_for(bd.score, has_evidence=True, contradicted=is_contradicted(bd)) == "current"


def test_score_contradicted_claim_is_outdated():
    """Direction, not just magnitude. With A floored at 0, R/Q/C alone can keep
    a flatly refuted claim inside the `check` band — so the verdict takes the
    direction of the evidence into account explicitly."""
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(days=1)).date().isoformat()
    evidence = [
        ev("contradicts", tier="official", published=fresh),
        ev("contradicts", tier="official", published=fresh),
    ]
    bd = score_claim("version", evidence, confidence=0.9, last_checked=now, now=now)
    assert is_contradicted(bd)
    assert verdict_for(bd.score, has_evidence=True, contradicted=True) == "outdated"


def test_no_evidence_is_unverifiable_not_outdated():
    bd = score_claim("pricing", [], confidence=0.0, last_checked=None)
    assert verdict_for(bd.score, has_evidence=False) == "unverifiable"


def test_decay_reduces_the_score_of_an_ageing_claim():
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(days=1)).date().isoformat()
    evidence = [ev("supports", tier="official", published=fresh)]

    just_now = score_claim("market", evidence, confidence=0.9, last_checked=now, now=now)
    an_hour_ago = score_claim(
        "market", evidence, confidence=0.9, last_checked=now - timedelta(hours=1), now=now
    )
    assert an_hour_ago.score < just_now.score


def test_breakdown_is_complete():
    """Every number must be explainable; the console renders this directly."""
    bd = score_claim("pricing", [ev("supports", tier="official")], confidence=0.8)
    for field in ("agreement", "recency", "quality", "confidence", "volatility", "decay",
                  "base", "decay_multiplier", "score"):
        assert getattr(bd, field) is not None
    assert bd.formula.startswith("S = 100*")


# ---------------------------------------------------------------------------
# answer-level reduction
# ---------------------------------------------------------------------------


def test_answer_score_is_the_minimum():
    """One outdated sentence makes an answer unsafe; averaging would hide it
    behind nine timeless ones."""
    assert answer_score([95.0, 90.0, 20.0]) == 20.0
    assert answer_score([]) == 0.0


def test_answer_ttl_is_the_fastest_moving_claim():
    assert answer_ttl_seconds(["market", "leadership"]) == 15 * 60
    assert answer_ttl_seconds(["leadership", "law"]) == 24 * 3600
    assert answer_ttl_seconds(["pricing"]) == 6 * 3600


def test_answer_ttl_timeless_keeps_the_caller_default():
    assert answer_ttl_seconds(["timeless"], default_seconds=4242) == 4242
    assert answer_ttl_seconds([], default_seconds=4242) == 4242


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


def test_date_from_url():
    assert date_from_url("https://fortune.com/2026/10/15/story").date().isoformat() == "2026-10-15"
    assert date_from_url("https://x.example/2026-10-15/story").date().isoformat() == "2026-10-15"
    assert date_from_url("https://openai.com/api/pricing/") is None
    assert date_from_url("") is None


def test_date_from_url_rejects_the_future():
    """A path segment that looks like a date must not invent fresher evidence
    than exists."""
    assert date_from_url("https://x.example/2099/01/01/story") is None


def test_date_from_url_rejects_impossible_dates():
    assert date_from_url("https://x.example/2026/13/45/story") is None
