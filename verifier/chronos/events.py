"""The live event bus behind GET /v1/stream (Server-Sent Events).

The console's traffic feed, eviction timeline and counters are all driven from
this one stream, so the UI never polls.

Two design points worth stating:

* **Slow consumers are dropped, not buffered.** Each subscriber gets a bounded
  queue; when it fills, the oldest event is discarded. A browser tab that has
  been backgrounded for an hour must not be able to grow the verifier's memory,
  and for a live feed the newest events are the ones that matter.

* **Events are fire-and-forget.** `publish` never awaits a consumer and never
  raises, because it is called from the verification path. Losing a UI update
  is acceptable; delaying a verification to deliver one is not.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any, AsyncIterator

logger = logging.getLogger("chronos.events")

MAX_QUEUE = 256
REPLAY_BUFFER = 100


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._recent: deque[str] = deque(maxlen=REPLAY_BUFFER)
        self._seq = 0

    def publish(self, kind: str, payload: dict[str, Any]) -> None:
        """Broadcast an event. Never blocks, never raises."""
        self._seq += 1
        try:
            frame = json.dumps(
                {"seq": self._seq, "type": kind, **payload},
                default=str,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            logger.debug("undeliverable event %s: %s", kind, exc)
            return

        self._recent.append(frame)

        for queue in list(self._subscribers):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                # Drop the oldest so the newest still lands.
                try:
                    queue.get_nowait()
                    queue.put_nowait(frame)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def recent(self, limit: int = REPLAY_BUFFER) -> list[str]:
        return list(self._recent)[-limit:]

    async def stream(self, replay: int = 20, keepalive_s: float = 15.0) -> AsyncIterator[str]:
        """Yield SSE frames for one client.

        Replays the last few events first so a console that connects mid-demo
        is not staring at an empty feed, then follows live. A comment frame
        every `keepalive_s` keeps proxies from closing an idle stream.
        """
        queue = self.subscribe()
        try:
            yield ": chronos stream open\n\n"
            for frame in self.recent(replay):
                yield f"data: {frame}\n\n"

            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=keepalive_s)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {frame}\n\n"
        except asyncio.CancelledError:  # client went away
            raise
        finally:
            self.unsubscribe(queue)


BUS = EventBus()
