"""Per-virtual-key policy.

Different callers want different trade-offs from the same gateway. A customer
support bot wants `guard` and will pay the latency; an internal analytics job
wants `observe` and no interference; a free tier wants a small credit
allowance. Because Bifrost already resolves a virtual key for every request and
puts it on the context, Chronos can key its behaviour on the same identity the
gateway bills and rate-limits — which is the right place for this to live.

Policies are cached in memory and persisted to SQLite, so a restart keeps them
and a read on the hot path costs a dict lookup.
"""

from __future__ import annotations

import logging

from .models import Mode, Policy
from .settings import Settings, get_settings
from .store import ReportStore

logger = logging.getLogger("chronos.policies")


class PolicyStore:
    def __init__(self, store: ReportStore, settings: Settings | None = None) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self._cache: dict[str, Policy] = {}
        self._loaded = False

    async def load(self) -> None:
        try:
            for raw in await self.store.all_policies():
                try:
                    policy = Policy.model_validate(raw)
                except Exception:
                    continue
                self._cache[policy.virtual_key] = policy
        except Exception as exc:
            logger.warning("could not load policies: %s", exc)
        self._loaded = True
        logger.info("loaded %d virtual-key policies", len(self._cache))

    def default(self, virtual_key: str = "") -> Policy:
        """The policy an unconfigured key gets: whatever the service defaults to."""
        return Policy(
            virtual_key=virtual_key,
            mode="annotate",
            guard_threshold=50,
            skip_classes=[],
            daily_tavily_credits=self.settings.tavily_daily_credits,
            evict_stale=True,
        )

    def get(self, virtual_key: str) -> Policy:
        if not virtual_key:
            return self.default()
        return self._cache.get(virtual_key) or self.default(virtual_key)

    async def put(self, policy: Policy) -> Policy:
        self._cache[policy.virtual_key] = policy
        await self.store.put_policy(policy)
        logger.info(
            "policy for %s set to mode=%s threshold=%d skip=%s",
            policy.virtual_key,
            policy.mode,
            policy.guard_threshold,
            ",".join(policy.skip_classes) or "-",
        )
        return policy

    def all(self) -> list[Policy]:
        return list(self._cache.values())

    def effective_mode(self, virtual_key: str, requested: Mode, budget_exhausted: bool) -> Mode:
        """Resolve the mode actually used for a request.

        Precedence, strongest first:
          1. a spent credit budget forces `observe` — Chronos will not pretend
             to verify what it cannot afford to check;
          2. the virtual key's configured mode;
          3. what the caller asked for.
        """
        if budget_exhausted:
            return "observe"
        policy = self.get(virtual_key)
        if virtual_key and virtual_key in self._cache:
            return policy.mode
        return requested
