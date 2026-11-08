"""Prometheus metrics.

The metric names are the ones the blueprint specifies, so a dashboard built
against them keeps working:

    chronos_claims_total
    chronos_stale_total
    chronos_cache_evictions_total
    chronos_tavily_credits_total
    chronos_verify_latency_seconds

prometheus_client is optional. If it is missing the module degrades to no-op
recorders so the verifier still starts — an observability dependency should
never be the reason a service will not boot.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("chronos.metrics")

try:  # pragma: no cover - import-time branch
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    from prometheus_client.exposition import CONTENT_TYPE_LATEST

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; charset=utf-8"


class _NoopMetric:
    def labels(self, *_args: Any, **_kwargs: Any) -> _NoopMetric:
        return self

    def inc(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def set(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def observe(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class Metrics:
    """Metric handles, real or no-op."""

    def __init__(self) -> None:
        self.available = _AVAILABLE
        if not _AVAILABLE:
            logger.info("prometheus_client not installed; metrics are no-ops")
            self.registry = None
            self.claims = _NoopMetric()
            self.stale = _NoopMetric()
            self.evictions = _NoopMetric()
            self.credits = _NoopMetric()
            self.latency = _NoopMetric()
            self.answers = _NoopMetric()
            self.claim_cache = _NoopMetric()
            self.budget_remaining = _NoopMetric()
            self.degraded = _NoopMetric()
            self.injection = _NoopMetric()
            self.cache_bypass = _NoopMetric()
            return

        self.registry = CollectorRegistry()

        self.claims = Counter(
            "chronos_claims_total",
            "Claims extracted and processed, by class and verdict.",
            ["claim_class", "verdict"],
            registry=self.registry,
        )
        self.stale = Counter(
            "chronos_stale_total",
            "Claims found to be outdated, by class.",
            ["claim_class"],
            registry=self.registry,
        )
        self.evictions = Counter(
            "chronos_cache_evictions_total",
            "Semantic cache entries evicted because they served an outdated claim.",
            ["reason"],
            registry=self.registry,
        )
        self.credits = Counter(
            "chronos_tavily_credits_total",
            "Tavily credits spent, by operation.",
            ["operation"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "chronos_verify_latency_seconds",
            "End-to-end verification latency.",
            ["mode", "cache_hit"],
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 6.0, 10.0, 20.0),
            registry=self.registry,
        )
        self.answers = Counter(
            "chronos_answers_total",
            "Answers verified, by verdict and whether they came from the cache.",
            ["verdict", "cache_hit"],
            registry=self.registry,
        )
        self.claim_cache = Counter(
            "chronos_claim_cache_total",
            "Claim cache lookups, by outcome.",
            ["outcome"],
            registry=self.registry,
        )
        self.budget_remaining = Gauge(
            "chronos_tavily_credits_remaining",
            "Tavily credits left in today's budget.",
            registry=self.registry,
        )
        self.degraded = Gauge(
            "chronos_degraded",
            "1 when the verifier is running in a degraded mode.",
            ["reason"],
            registry=self.registry,
        )
        self.injection = Counter(
            "chronos_injection_flagged_sources_total",
            "Evidence sources set aside because their text tried to instruct the judge, by pattern.",
            ["pattern"],
            registry=self.registry,
        )
        self.cache_bypass = Counter(
            "chronos_claim_cache_bypassed_total",
            "Claims verified without the claim cache because their subject could name more than one thing.",
            registry=self.registry,
        )

    # -- helpers --------------------------------------------------------

    @contextmanager
    def time_verify(self, mode: str, cache_hit: bool) -> Iterator[None]:
        import time

        start = time.perf_counter()
        try:
            yield
        finally:
            self.latency.labels(mode=mode, cache_hit=str(bool(cache_hit)).lower()).observe(
                time.perf_counter() - start
            )

    def record_claim(self, claim_class: str, verdict: str) -> None:
        self.claims.labels(claim_class=claim_class, verdict=verdict).inc()
        if verdict == "outdated":
            self.stale.labels(claim_class=claim_class).inc()

    def record_credits(self, operation: str, amount: int) -> None:
        if amount > 0:
            self.credits.labels(operation=operation).inc(amount)

    def render(self) -> tuple[bytes, str]:
        if not self.available or self.registry is None:
            return (
                b"# prometheus_client is not installed in this build\n",
                CONTENT_TYPE_LATEST,
            )
        return generate_latest(self.registry), CONTENT_TYPE_LATEST


METRICS = Metrics()
