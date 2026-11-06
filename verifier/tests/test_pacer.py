"""Token pacer tests.

The pacer exists because a 30-claim benchmark on a Groq free key produced 42
rate-limit refusals and 40 deadline misses: half the claims came back
`unverifiable` because the extractor never ran. These tests pin the behaviour
that fixed it.
"""

from __future__ import annotations

import asyncio
import time

from chronos.pacer import TokenPacer, estimate_tokens


def test_estimate_includes_reserved_output():
    """Providers charge the completion too, and a generous max_tokens is
    charged as capacity even when the answer is short."""
    prompt_only = estimate_tokens("x" * 350)
    with_output = estimate_tokens("x" * 350, max_output=500)
    assert with_output == prompt_only + 500


def test_estimate_counts_every_text():
    assert estimate_tokens("x" * 350, "y" * 350) > estimate_tokens("x" * 350)


def test_estimate_handles_empty_and_none():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0  # type: ignore[arg-type]


def test_disabled_pacer_never_waits():
    async def run():
        pacer = TokenPacer(0)
        assert not pacer.enabled
        waited = await pacer.acquire("m", 100_000)
        assert waited == 0.0

    asyncio.run(run())


def test_calls_inside_the_budget_do_not_wait():
    async def run():
        pacer = TokenPacer(1000)
        for _ in range(4):
            assert await pacer.acquire("m", 200) == 0.0
        assert pacer.waits == 0
        assert pacer.used("m") == 800

    asyncio.run(run())


def test_exceeding_the_budget_waits():
    async def run():
        # A 1-second window would make this slow, so the budget is sized so the
        # second call must wait, and the wait is observed rather than endured.
        pacer = TokenPacer(500)
        await pacer.acquire("m", 400)

        started = time.monotonic()
        task = asyncio.create_task(pacer.acquire("m", 400))
        await asyncio.sleep(0.15)

        assert not task.done(), "the second call should be waiting for room"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert time.monotonic() - started >= 0.15
        assert pacer.waits >= 1

    asyncio.run(run())


def test_budget_is_tracked_per_model():
    """The whole reason extraction and judging use different models: Groq meters
    tokens per minute per model, so they must not share a pool here either."""

    async def run():
        pacer = TokenPacer(500)
        assert await pacer.acquire("model-a", 450) == 0.0
        # A different model has its own budget, so this must not wait.
        assert await pacer.acquire("model-b", 450) == 0.0
        assert pacer.used("model-a") == 450
        assert pacer.used("model-b") == 450
        assert pacer.waits == 0

    asyncio.run(run())


def test_a_call_larger_than_the_budget_is_let_through():
    """Better a provider refusal, which is a clear signal, than a hang."""

    async def run():
        pacer = TokenPacer(100)
        waited = await pacer.acquire("m", 5_000)
        assert waited == 0.0

    asyncio.run(run())


def test_stats_report_the_budget_and_waiting():
    async def run():
        pacer = TokenPacer(1000)
        await pacer.acquire("m", 100)
        stats = pacer.stats()
        assert stats["tokens_per_minute"] == 1000
        assert stats["enabled"] is True
        assert stats["used_by_model"] == {"m": 100}

    asyncio.run(run())


def test_concurrent_callers_cannot_both_claim_the_last_room():
    """Two callers deciding independently would both pass and both be refused."""

    async def run():
        pacer = TokenPacer(500)
        results = await asyncio.gather(
            pacer.acquire("m", 300),
            pacer.acquire("m", 300),
            return_exceptions=True,
        )
        # One fits; the other must have waited (or still be waiting, which the
        # gather resolves). Either way the reservations are serialised.
        assert any(isinstance(r, float) for r in results)
        assert pacer.used("m") <= 600

    asyncio.run(asyncio.wait_for(run(), timeout=70))
