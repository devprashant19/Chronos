"""Cache eviction from the verifier side.

The plugin evicts on the request path, because it is the component that knows a
cache hit just happened. The verifier needs the same ability for work that has
no request attached to it: the sweeper's speculative re-checks, and
asynchronous verifications whose answer has already been sent.

Both go through the same gateway API — `DELETE /api/cache/clear/{cache_id}` —
so there is exactly one way a cache entry can be invalidated, and it is
Bifrost's own.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from .settings import Settings, get_settings

logger = logging.getLogger("chronos.evictor")


class Evictor:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None
        self.cleared = 0
        self.failed = 0

    def _ensure(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.settings.bifrost_admin_url.rstrip("/"),
                timeout=httpx.Timeout(5.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def clear_cache_id(self, cache_id: str) -> bool:
        """Evict one entry. Returns True when the gateway accepted it.

        Clearing an id that has already expired succeeds on every vector store,
        so this is safe to call optimistically and a False here means a real
        problem reaching the gateway.
        """
        if not cache_id:
            return False
        path = f"/api/cache/clear/{quote(cache_id, safe='')}"
        try:
            resp = await self._ensure().delete(path)
        except httpx.HTTPError as exc:
            self.failed += 1
            logger.warning("cache clear for %s failed: %s", cache_id, exc)
            return False

        if resp.status_code >= 300:
            self.failed += 1
            logger.warning(
                "cache clear for %s returned %d: %s",
                cache_id,
                resp.status_code,
                resp.text[:200],
            )
            return False

        self.cleared += 1
        logger.info("evicted stale cache entry %s", cache_id)
        return True

    async def clear_cache_key(self, cache_key: str) -> bool:
        """Evict every identity's entries under one cache key."""
        if not cache_key:
            return False
        path = f"/api/cache/clear-by-key/{quote(cache_key, safe='')}"
        try:
            resp = await self._ensure().delete(path)
        except httpx.HTTPError as exc:
            self.failed += 1
            logger.warning("cache clear-by-key for %s failed: %s", cache_key, exc)
            return False

        if resp.status_code >= 300:
            self.failed += 1
            return False

        self.cleared += 1
        return True

    def stats(self) -> dict[str, int]:
        return {"cleared": self.cleared, "failed": self.failed}
