"""Model access — always through Bifrost, never direct to a provider.

This is a deliberate architectural choice rather than a convenience. Chronos's
own extraction and judging calls are LLM traffic like any other, so routing
them through the gateway means they inherit, for free:

  * a virtual key with its own budget and rate limit, so a runaway verifier
    cannot spend the production key's quota;
  * provider fallbacks, so a 429 from OpenAI becomes a Groq call rather than an
    unverifiable answer;
  * the gateway's logs, so Chronos's own spend is visible next to the traffic
    it is checking.

It also means Chronos dogfoods the gateway it extends.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from .settings import Settings, get_settings

logger = logging.getLogger("chronos.llm")

#: Raised when the model layer cannot produce a usable answer. Callers treat
#: this as "unverifiable", never as a server error.
class ModelUnavailable(RuntimeError):
    pass


def _split_model(ref: str) -> tuple[str, str]:
    """Split "provider/model" into its parts."""
    if "/" in ref:
        provider, _, model = ref.partition("/")
        return provider, model
    return "", ref


class LLMClient:
    """Thin async wrapper over the OpenAI SDK, pointed at Bifrost."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise ModelUnavailable(f"openai SDK not installed: {exc}") from exc

        self._client = AsyncOpenAI(
            base_url=self.settings.bifrost_url,
            api_key=self.settings.virtual_key or "chronos",
            max_retries=0,  # Bifrost owns retries and fallbacks
            timeout=30.0,
        )
        return self._client

    def _fallbacks(self) -> list[dict[str, str]]:
        """Bifrost's per-request fallback chain.

        Sent as extra_body so the OpenAI SDK passes it through untouched.
        """
        out: list[dict[str, str]] = []
        for ref in self.settings.fallback_models:
            provider, model = _split_model(ref)
            if provider and model:
                out.append({"provider": provider, "model": model})
        return out

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 1600,
        temperature: float = 0.0,
        cache_key: str | None = None,
    ) -> dict[str, Any]:
        """Ask for a JSON object and return it parsed.

        Raises ModelUnavailable on transport failure or unparseable output, so
        the caller can retry once with the error attached and then give up
        cleanly rather than guessing.
        """
        raw = await self.complete_text(
            system=system,
            user=user,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=True,
            cache_key=cache_key,
        )
        return _parse_json_object(raw)

    async def complete_text(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int = 1600,
        temperature: float = 0.0,
        json_mode: bool = False,
        cache_key: str | None = None,
    ) -> str:
        if not self.settings.live_models:
            raise ModelUnavailable("offline demo mode: no model calls are made")

        client = self._ensure_client()
        target = model or self.settings.judge_model

        extra_body: dict[str, Any] = {}
        fallbacks = self._fallbacks()
        if fallbacks:
            extra_body["fallbacks"] = fallbacks

        extra_headers: dict[str, str] = {}
        if cache_key:
            # Chronos's own calls are cacheable too — an identical judging
            # prompt does not need a second provider round trip.
            extra_headers["x-bf-cache-key"] = cache_key
            extra_headers["x-bf-cache-ttl"] = "1h"

        kwargs: dict[str, Any] = {
            "model": target,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if extra_body:
            kwargs["extra_body"] = extra_body
        if extra_headers:
            kwargs["extra_headers"] = extra_headers

        try:
            resp = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise ModelUnavailable(f"model call failed ({target}): {exc}") from exc

        try:
            content = resp.choices[0].message.content or ""
        except (AttributeError, IndexError) as exc:
            raise ModelUnavailable(f"model returned no content ({target}): {exc}") from exc

        if not content.strip():
            raise ModelUnavailable(f"model returned empty content ({target})")
        return content

    async def aclose(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.close()
            self._client = None


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object out of a model reply.

    Models wrap JSON in prose or fences often enough that giving up on the
    first failure would waste a paid call, so this strips the usual wrappers
    before admitting defeat.
    """
    text = raw.strip()

    if text.startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ModelUnavailable(f"model did not return JSON: {raw[:200]!r}") from None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelUnavailable(f"model returned malformed JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ModelUnavailable(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed
