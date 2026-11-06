"""The claim cache — the component that makes Chronos affordable.

Verifying a claim costs a Tavily search plus an extract plus a judge call. The
same claims recur constantly: every user who asks what GPT-4o costs produces
the same claim about the same subject and predicate. Caching the *verdict*,
keyed on the normalised claim rather than on the answer text, means the second
and later answers containing that claim cost nothing.

The TTL is the claim's own re-check interval, so a market claim is re-checked
within the hour while a legal status is trusted for a fortnight. That is the
same policy that drives the cache TTL and the decay term in the score, which is
why it lives in settings.py rather than being hard-coded here.

The cache is Redis when Redis is reachable and an in-process dict when it is
not. Falling back rather than failing is the right call for a component whose
only job is to save money: a cold cache costs credits, a broken verifier costs
the gateway.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

from .models import ClaimVerdict
from .settings import Settings, get_settings, recheck_interval

logger = logging.getLogger("chronos.cache")

KEY_PREFIX = "chronos:claim:"


def normalise(text: str) -> str:
    """Normalise a subject or predicate for keying.

    Lowercase, strip punctuation and collapse whitespace, so "OpenAI GPT-4o"
    and "openai gpt-4o" share a cache entry while "GPT-4o mini" does not.
    """
    lowered = (text or "").lower().strip()
    lowered = re.sub(r"[^\w\s.+#-]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def claim_key(subject: str, predicate: str) -> str:
    """Stable cache key for a claim's identity.

    Note what is *not* in the key: the stated value. Two answers asserting
    different prices for the same product are the same question — "what is this
    product's price?" — and should share one verified answer. Including the
    stated value would mean a wrong answer never hits the cache, which is
    exactly backwards.
    """
    basis = f"{normalise(subject)}|{normalise(predicate)}"
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]
    return f"{KEY_PREFIX}{digest}"


class ClaimCache:
    """Verdict cache with a Redis backend and an in-memory fallback."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._redis: Any | None = None
        self._memory: dict[str, tuple[float, str]] = {}
        self._connected = False
        self.hits = 0
        self.misses = 0

    async def connect(self) -> None:
        """Try Redis once at startup. A failure is logged, not raised."""
        try:
            import redis.asyncio as aioredis
        except ImportError:  # pragma: no cover
            logger.warning("redis client not installed; using in-memory claim cache")
            return

        try:
            client = aioredis.from_url(
                self.settings.redis_url, encoding="utf-8", decode_responses=True
            )
            await client.ping()
        except Exception as exc:  # noqa: BLE001
            logger.warning("claim cache falling back to memory (redis: %s)", exc)
            return

        self._redis = client
        self._connected = True
        logger.info("claim cache connected to %s", self.settings.redis_url)

    async def aclose(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._redis = None
            self._connected = False

    @property
    def backend(self) -> str:
        return "redis" if self._connected else "memory"

    # -- operations -----------------------------------------------------

    async def get(self, subject: str, predicate: str) -> ClaimVerdict | None:
        key = claim_key(subject, predicate)
        raw = await self._get_raw(key)
        if raw is None:
            self.misses += 1
            return None

        try:
            payload = json.loads(raw)
            verdict = ClaimVerdict.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            # A poisoned entry must not break a request, but it must not be
            # quiet either: an unreadable entry turns every hit into a miss and
            # silently re-buys evidence Chronos already paid for.
            logger.warning("discarding unreadable claim cache entry %s: %s", key, exc)
            self.misses += 1
            return None

        self.hits += 1
        verdict.from_claim_cache = True
        return verdict

    async def put(self, subject: str, predicate: str, verdict: ClaimVerdict) -> None:
        interval = recheck_interval(verdict.claim_class)
        if interval is None:
            # Timeless claims are cached for a week: long enough to save the
            # credits, short enough that a reclassification takes effect.
            ttl_seconds = 7 * 24 * 3600
        else:
            ttl_seconds = int(interval.total_seconds())

        key = claim_key(subject, predicate)
        # Stored under field names, not wire aliases: this is internal
        # storage, and a round trip that needs an alias map is one more
        # thing that can silently break a read.
        payload = verdict.model_dump_json()
        await self._set_raw(key, payload, ttl_seconds)

    async def invalidate(self, subject: str, predicate: str) -> None:
        key = claim_key(subject, predicate)
        if self._redis is not None:
            try:
                await self._redis.delete(key)
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug("redis delete failed, falling through: %s", exc)
        self._memory.pop(key, None)

    async def ttl(self, subject: str, predicate: str) -> int:
        """Seconds until this verdict needs re-checking; -1 when absent."""
        key = claim_key(subject, predicate)
        if self._redis is not None:
            try:
                value = await self._redis.ttl(key)
                return int(value)
            except Exception:  # noqa: BLE001
                pass
        entry = self._memory.get(key)
        if entry is None:
            return -1
        return max(0, int(entry[0] - time.time()))

    async def near_expiry(self, within_seconds: int, limit: int = 50) -> list[ClaimVerdict]:
        """Verdicts whose re-check is due within `within_seconds`.

        Used by the sweeper to re-verify popular claims before they go stale,
        so the next user gets a cache hit rather than a slow path.
        """
        out: list[ClaimVerdict] = []

        if self._redis is not None:
            try:
                async for key in self._redis.scan_iter(match=f"{KEY_PREFIX}*", count=200):
                    ttl_left = int(await self._redis.ttl(key))
                    if ttl_left < 0 or ttl_left > within_seconds:
                        continue
                    raw = await self._redis.get(key)
                    if not raw:
                        continue
                    try:
                        out.append(ClaimVerdict.model_validate(json.loads(raw)))
                    except Exception:  # noqa: BLE001
                        continue
                    if len(out) >= limit:
                        break
                return out
            except Exception as exc:  # noqa: BLE001
                logger.debug("redis scan failed: %s", exc)

        now = time.time()
        for expires, raw in list(self._memory.values()):
            if expires - now > within_seconds:
                continue
            try:
                out.append(ClaimVerdict.model_validate(json.loads(raw)))
            except Exception:  # noqa: BLE001
                continue
            if len(out) >= limit:
                break
        return out

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "backend": self.backend,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "entries_in_memory": len(self._memory),
        }

    # -- backend plumbing ------------------------------------------------

    async def _get_raw(self, key: str) -> str | None:
        if self._redis is not None:
            try:
                return await self._redis.get(key)
            except Exception as exc:  # noqa: BLE001
                logger.debug("redis get failed, using memory: %s", exc)

        entry = self._memory.get(key)
        if entry is None:
            return None
        expires, payload = entry
        if expires < time.time():
            self._memory.pop(key, None)
            return None
        return payload

    async def _set_raw(self, key: str, payload: str, ttl_seconds: int) -> None:
        if self._redis is not None:
            try:
                await self._redis.setex(key, ttl_seconds, payload)
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug("redis setex failed, using memory: %s", exc)

        self._memory[key] = (time.time() + ttl_seconds, payload)
        # Keep the fallback bounded; it is a safety net, not a store.
        if len(self._memory) > 5000:
            now = time.time()
            for k, (exp, _) in list(self._memory.items()):
                if exp < now:
                    self._memory.pop(k, None)
            while len(self._memory) > 5000:
                self._memory.pop(next(iter(self._memory)))
