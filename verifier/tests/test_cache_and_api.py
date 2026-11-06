"""Claim cache and HTTP API tests.

The cache test exists because of a real bug: verdicts were written with wire
aliases and read back by field name, so every read failed, every failure was
counted as a miss, and Chronos silently re-bought evidence it already had. The
round-trip assertions below are the regression guard.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from chronos.cache import ClaimCache, claim_key, normalise
from chronos.models import ClaimVerdict, Evidence, Policy


def verdict(subject: str = "Python", predicate: str = "latest version") -> ClaimVerdict:
    return ClaimVerdict(
        text="The latest stable release of Python is 3.11.",
        span_start=0,
        span_end=43,
        subject=subject,
        predicate=predicate,
        stated_value="3.11",
        claim_class="version",
        verdict="outdated",
        score=36.2,
        current_value="3.15",
        source_url="https://www.python.org/downloads/",
        evidence=[Evidence(url="https://www.python.org/downloads/", call="contradicts", tier="official")],
    )


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------


def test_normalise_collapses_case_and_punctuation():
    assert normalise("  OpenAI, GPT-4o!  ") == "openai gpt-4o"
    assert normalise("OpenAI GPT-4o") == normalise("openai   gpt-4o")


def test_claim_key_is_stable_and_case_insensitive():
    assert claim_key("Python", "latest version") == claim_key("python", "Latest Version")


def test_claim_key_excludes_the_stated_value():
    """Two answers asserting different prices for the same product are the same
    question. Keying on the stated value would mean a wrong answer never hits
    the cache, which is exactly backwards."""
    a = claim_key("OpenAI GPT-4o", "input token price")
    b = claim_key("OpenAI GPT-4o", "input token price")
    assert a == b


def test_claim_key_distinguishes_different_subjects():
    assert claim_key("GPT-4o", "price") != claim_key("GPT-4o mini", "price")


# ---------------------------------------------------------------------------
# round trip
# ---------------------------------------------------------------------------


def test_verdict_round_trips_through_both_serialisations():
    """The bug: stored with by_alias=True (writing "class"), validated only
    against the field name, so every read failed as a miss."""
    v = verdict()
    for payload in (v.model_dump_json(), v.model_dump_json(by_alias=True)):
        back = ClaimVerdict.model_validate(json.loads(payload))
        assert back.claim_class == "version"
        assert back.current_value == "3.15"


def test_wire_output_uses_the_class_alias():
    """The API contract says "class"; the console and the plugin read that."""
    assert "class" in json.loads(verdict().model_dump_json(by_alias=True))


def test_memory_cache_round_trip():
    async def run():
        cache = ClaimCache()  # no connect(): stays in memory
        await cache.put("Python", "latest version", verdict())
        got = await cache.get("Python", "latest version")
        assert got is not None, "a verdict just written must be readable"
        assert got.from_claim_cache is True
        assert got.current_value == "3.15"
        assert cache.hits == 1 and cache.misses == 0

    asyncio.run(run())


def test_memory_cache_miss_is_counted():
    async def run():
        cache = ClaimCache()
        assert await cache.get("Nothing", "stored") is None
        assert cache.misses == 1

    asyncio.run(run())


def test_cache_invalidate():
    async def run():
        cache = ClaimCache()
        await cache.put("Python", "latest version", verdict())
        await cache.invalidate("Python", "latest version")
        assert await cache.get("Python", "latest version") is None

    asyncio.run(run())


def test_cache_ttl_follows_the_recheck_interval():
    async def run():
        cache = ClaimCache()
        await cache.put("Python", "latest version", verdict())
        ttl = await cache.ttl("Python", "latest version")
        assert ttl == pytest.approx(3 * 24 * 3600, rel=0.05)

    asyncio.run(run())


def test_unreadable_entry_is_a_miss_not_a_crash():
    async def run():
        cache = ClaimCache()
        await cache._set_raw(claim_key("X", "y"), "{not json", 60)
        assert await cache.get("X", "y") is None
        assert cache.misses == 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import os

    os.environ["CHRONOS_OFFLINE_DEMO"] = "1"
    os.environ["CHRONOS_SWEEPER_ENABLED"] = "0"
    os.environ["CHRONOS_DB_PATH"] = str(tmp_path_factory.mktemp("db") / "test.sqlite")

    from chronos.settings import get_settings

    get_settings.cache_clear()

    from chronos.main import api

    with TestClient(api) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["offline_demo"] is True


def test_verify_returns_a_full_report(client):
    r = client.post(
        "/v1/verify",
        json={"answer": "The latest stable release of Python is 3.11.", "mode": "annotate"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["report_id"]
    assert body["verdict"] in {"current", "check", "outdated", "unverifiable"}
    assert body["ttl_seconds"] > 0, "an answer with a version claim must cap the cache TTL"
    assert isinstance(body["claims"], list)


def test_verify_timeless_answer_spends_nothing(client):
    r = client.post("/v1/verify", json={"answer": "The capital of France is Paris.", "mode": "annotate"})
    body = r.json()
    assert body["tavily_credits"] == 0
    assert body["verdict"] == "current"


def test_report_is_retrievable(client):
    created = client.post(
        "/v1/verify", json={"answer": "The capital of France is Paris.", "mode": "annotate"}
    ).json()
    r = client.get(f"/v1/reports/{created['report_id']}")
    assert r.status_code == 200
    assert r.json()["report_id"] == created["report_id"]


def test_unknown_report_is_404(client):
    assert client.get("/v1/reports/does-not-exist").status_code == 404


def test_verify_async_accepts_and_returns_pending(client):
    r = client.post("/v1/verify/async", json={"answer": "Bitcoin is trading at about $43,200."})
    assert r.status_code == 202
    body = r.json()
    assert body["verdict"] == "pending"
    assert body["report_id"]


def test_stats_shape(client):
    body = client.get("/v1/stats").json()
    for key in ("answers_checked", "claims_checked", "claim_cache", "tavily", "sweeper"):
        assert key in body


def test_metrics_endpoint(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    # Metric names are a published contract; a dashboard depends on them.
    for name in (
        "chronos_claims_total",
        "chronos_stale_total",
        "chronos_cache_evictions_total",
        "chronos_tavily_credits_total",
        "chronos_verify_latency_seconds",
    ):
        assert name in r.text


def test_policy_round_trip(client):
    policy = Policy(
        virtual_key="vk-test", mode="guard", guard_threshold=70, skip_classes=["timeless"]
    ).model_dump(mode="json")

    put = client.put("/v1/policies/vk-test", json=policy)
    assert put.status_code == 200
    assert put.json()["mode"] == "guard"

    got = client.get("/v1/policies/vk-test").json()
    assert got["guard_threshold"] == 70
    assert got["skip_classes"] == ["timeless"]


def test_policy_default_for_unknown_key(client):
    got = client.get("/v1/policies/never-configured").json()
    assert got["mode"] == "annotate"


def test_policy_body_path_mismatch_is_rejected(client):
    body = Policy(virtual_key="other-key").model_dump(mode="json")
    assert client.put("/v1/policies/vk-test", json=body).status_code == 400


def test_cache_events_endpoint(client):
    r = client.get("/v1/cache/events")
    assert r.status_code == 200
    assert "events" in r.json()


def test_verify_rejects_an_empty_answer(client):
    assert client.post("/v1/verify", json={"answer": ""}).status_code == 422
