"""The sweeper: re-verify popular claims before anyone asks.

Chronos is reactive by default — it checks an answer when an answer happens.
That leaves a gap: the most-requested cached answers are exactly the ones whose
claims are about to expire, and the unlucky user who arrives first after expiry
pays the full verification latency.

Every `CHRONOS_SWEEP_INTERVAL_S` (default 10 minutes) the sweeper takes the
most-hit cache entries whose claims are near their re-check time, verifies them
in the background, and evicts the ones that have gone stale. Users then either
get a fresh answer or a claim-cache hit, and nobody waits.

It is budget-aware: sweeping is the first thing to stop when credits run low,
because a speculative check should never outbid a real request.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC

from .cache import ClaimCache
from .events import BUS
from .evictor import Evictor
from .metrics import METRICS
from .models import Claim
from .settings import Settings, get_settings
from .store import ReportStore
from .verify import Verifier

logger = logging.getLogger("chronos.sweeper")

#: Never spend more than this share of the day's credits on speculative work.
SWEEP_BUDGET_SHARE = 0.25


class Sweeper:
    def __init__(
        self,
        verifier: Verifier,
        store: ReportStore,
        claim_cache: ClaimCache,
        evictor: Evictor,
        settings: Settings | None = None,
    ) -> None:
        self.verifier = verifier
        self.store = store
        self.claim_cache = claim_cache
        self.evictor = evictor
        self.settings = settings or get_settings()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_run: float = 0.0
        self.runs = 0
        self.reverified = 0
        self.evicted = 0

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        if not self.settings.sweeper_enabled:
            logger.info("sweeper disabled")
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="chronos-sweeper")
        logger.info("sweeper started, every %ds", self.settings.sweep_interval_s)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        # Let the service settle before the first sweep.
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=30)
            return
        except TimeoutError:
            pass

        while not self._stop.is_set():
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("sweep failed: %s", exc)

            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.sweep_interval_s
                )
            except TimeoutError:
                continue

    # -- the sweep ------------------------------------------------------

    async def sweep_once(self) -> dict[str, int]:
        """One pass. Returns a small summary, which /v1/stats surfaces."""
        self.runs += 1
        self.last_run = time.time()

        budget = self.verifier.tavily.budget
        reserve = int(budget.daily_limit * SWEEP_BUDGET_SHARE)
        if budget.daily_limit and budget.remaining <= reserve:
            logger.info(
                "skipping sweep: %d credits left, keeping %d in reserve for live traffic",
                budget.remaining,
                reserve,
            )
            return {"reverified": 0, "evicted": 0, "skipped": 1}

        # Claims whose re-check falls due within one sweep interval.
        due = await self.claim_cache.near_expiry(
            within_seconds=self.settings.sweep_interval_s,
            limit=self.settings.sweep_batch,
        )
        if not due:
            return {"reverified": 0, "evicted": 0, "skipped": 0}

        logger.info("sweeping %d claims near expiry", len(due))

        reverified = 0
        evicted = 0

        for cached in due:
            if self._stop.is_set():
                break

            claim = Claim(
                text=cached.text,
                span_start=cached.span_start,
                span_end=cached.span_end,
                subject=cached.subject,
                predicate=cached.predicate,
                stated_value=cached.stated_value,
                **{"class": cached.claim_class},
            )

            # Force a fresh lookup: drop the entry, then verify.
            await self.claim_cache.invalidate(claim.subject, claim.predicate)
            try:
                fresh = await self.verifier._verify_one(claim, _now())
            except Exception as exc:
                logger.debug("sweep could not re-verify %s: %s", claim.subject, exc)
                continue

            reverified += 1
            if fresh.verdict == "outdated":
                evicted += await self._evict_answers_for(claim, fresh.score)

        self.reverified += reverified
        self.evicted += evicted

        BUS.publish(
            "sweep",
            {
                "reverified": reverified,
                "evicted": evicted,
                "due": len(due),
                "credits_remaining": budget.remaining,
            },
        )
        logger.info("sweep done: re-verified %d, evicted %d", reverified, evicted)
        return {"reverified": reverified, "evicted": evicted, "skipped": 0}

    async def _evict_answers_for(self, claim: Claim, score: float) -> int:
        """Evict every cached answer known to assert a now-stale claim.

        The link from claim back to cache entry comes from the report store:
        reports record which cache id served which answer.
        """
        evicted = 0
        try:
            hot = await self.store.hot_cache_entries(limit=self.settings.sweep_batch)
        except Exception as exc:
            logger.debug("could not list cache entries: %s", exc)
            return 0

        for entry in hot:
            cache_id = entry.get("cache_id")
            if not cache_id:
                continue
            ok = await self.evictor.clear_cache_id(cache_id)
            if not ok:
                continue
            evicted += 1
            METRICS.evictions.labels(reason="sweeper").inc()
            await self.store.record_cache_event(
                "evicted",
                cache_id=cache_id,
                score=score,
                detail=f"sweeper: {claim.subject} {claim.predicate} went stale",
            )
            BUS.publish(
                "eviction",
                {
                    "cache_id": cache_id,
                    "reason": "sweeper",
                    "subject": claim.subject,
                    "predicate": claim.predicate,
                    "score": score,
                },
            )
        return evicted

    def stats(self) -> dict[str, float | int]:
        return {
            "runs": self.runs,
            "last_run": self.last_run,
            "reverified": self.reverified,
            "evicted": self.evicted,
            "interval_s": self.settings.sweep_interval_s,
            "enabled": self.settings.sweeper_enabled,
        }


def _now():
    from datetime import datetime

    return datetime.now(UTC)
