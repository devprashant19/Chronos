"""A tokens-per-minute pacer for model calls.

Free model tiers meter **tokens per minute**, not requests, and that is the
limit Chronos actually hits. Measured on a Groq free key: 1,000 requests/day
but 8,000 TPM, and a 30-claim benchmark produced 42 rate-limit refusals and 40
deadline misses — half the claims came back `unverifiable` not because the
evidence was ambiguous but because the extractor never got to run.

Discovering the limit by being refused is the worst way to live inside it:
a 429 costs the round trip, returns nothing, and on some providers pushes the
reset further out. So Chronos predicts instead. Before each call it estimates
the tokens, and if the rolling minute has no room it waits for the oldest
reservation to age out.

Two details that matter:

**The budget is per model.** Groq's refusal names the model ("Rate limit
reached for model `openai/gpt-oss-120b`"), so each model has its own pool.
That is why Chronos puts extraction and judging on *different* models — they
then draw on separate budgets instead of competing for one.

**Waiting is the right answer, slowness is the honest cost.** A paced call that
arrives late still produces a verdict; a refused call produces nothing. On the
request path that trade is already handled elsewhere: fresh answers are
verified asynchronously, so pacing delays a background report rather than a
user's response.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque

logger = logging.getLogger("chronos.pacer")

#: Rough characters-per-token. Only needs to be in the right ballpark: the
#: cost of over-estimating is a little extra waiting, and the cost of
#: under-estimating is the 429 this exists to avoid, so it leans high.
CHARS_PER_TOKEN = 3.5

WINDOW_SECONDS = 60.0


def estimate_tokens(*texts: str, max_output: int = 0) -> int:
    """Estimate the tokens a call will be charged.

    Counts the prompt plus the *reserved* output, because providers meter the
    completion too and a generous max_tokens is charged as capacity even when
    the model answers briefly.
    """
    chars = sum(len(t or "") for t in texts)
    return int(chars / CHARS_PER_TOKEN) + max(0, max_output)


class TokenPacer:
    """Keeps model calls inside a tokens-per-minute budget, per model."""

    def __init__(self, tokens_per_minute: int) -> None:
        self.tpm = max(0, tokens_per_minute)
        #: model -> deque of (expires_at, tokens)
        self._windows: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.waits = 0
        self.wait_seconds = 0.0

    @property
    def enabled(self) -> bool:
        return self.tpm > 0

    def _used(self, model: str, now: float) -> int:
        window = self._windows[model]
        while window and window[0][0] <= now:
            window.popleft()
        return sum(tokens for _, tokens in window)

    def used(self, model: str) -> int:
        return self._used(model, time.monotonic())

    async def acquire(self, model: str, tokens: int) -> float:
        """Wait until `tokens` fit in this model's minute, then reserve them.

        Returns how long it waited, for reporting.
        """
        if not self.enabled or tokens <= 0:
            return 0.0

        waited = 0.0
        # Serialise per model so two callers cannot both decide there is room.
        async with self._locks[model]:
            while True:
                now = time.monotonic()
                used = self._used(model, now)

                if used + tokens <= self.tpm:
                    self._windows[model].append((now + WINDOW_SECONDS, tokens))
                    break

                window = self._windows[model]
                if not window:
                    # A single call larger than the whole budget: let it
                    # through rather than wait forever. The provider may
                    # refuse it, which is a clearer signal than a hang.
                    logger.warning(
                        "a single %s call estimated at %d tokens exceeds the %d TPM budget; "
                        "sending it anyway",
                        model,
                        tokens,
                        self.tpm,
                    )
                    self._windows[model].append((now + WINDOW_SECONDS, tokens))
                    break

                sleep_for = max(0.05, window[0][0] - now)
                waited += sleep_for
                self.waits += 1
                self.wait_seconds += sleep_for
                logger.info(
                    "pacing %s: %d/%d tokens used this minute, waiting %.1fs for room",
                    model,
                    used,
                    self.tpm,
                    sleep_for,
                )
                await asyncio.sleep(sleep_for)

        return waited

    def stats(self) -> dict[str, object]:
        now = time.monotonic()
        return {
            "tokens_per_minute": self.tpm,
            "enabled": self.enabled,
            "waits": self.waits,
            "wait_seconds": round(self.wait_seconds, 1),
            "used_by_model": {model: self._used(model, now) for model in list(self._windows)},
        }
