"""A tiny OpenAI-compatible provider for offline demos and CI.

Chronos's whole premise is that a cached answer can go stale, so demonstrating
it needs answers that *are* stale. Depending on a live provider for that is
both expensive and non-deterministic: you cannot reliably make gpt-4o-mini
assert that Twitter's CEO is Jack Dorsey.

So this service serves fixed answers that deliberately contain out-of-date
facts, which lets the full pipeline — gateway, cache, plugin, verifier, console
— run end to end with no API keys at all, and lets CI assert on exact outputs.

It is NOT part of Chronos. It is a test double, registered in Bifrost as a
custom provider named `mockllm` with `base_provider_type: openai`.

Every answer here is labelled with what is wrong about it in
`fixtures/answers.json`, so nothing in this file is passed off as true.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

FIXTURES = Path(__file__).parent / "fixtures" / "answers.json"

app = FastAPI(title="mockllm", docs_url=None, redoc_url=None)


def _load_fixtures() -> dict[str, Any]:
    try:
        with FIXTURES.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # pragma: no cover - fixtures ship with the image
        return {"answers": [], "default": "I do not have information on that."}


_FIXTURES = _load_fixtures()


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
    return ""


def _pick_answer(prompt: str) -> tuple[str, str]:
    """Return (answer_text, fixture_id) for a prompt.

    Matching is keyword-based and deterministic: the same prompt always gets
    the same answer, which is what makes the cache demo and CI assertions work.
    """
    low = prompt.lower()
    for entry in _FIXTURES.get("answers", []):
        for pattern in entry.get("match", []):
            if re.search(pattern, low):
                return entry["answer"], entry.get("id", "unknown")
    return _FIXTURES.get("default", "I do not know."), "default"


def _token_estimate(text: str) -> int:
    return max(1, len(text) // 4)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "mockllm"}


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    ids = [e.get("id", "chronos-demo") for e in _FIXTURES.get("answers", [])]
    return {
        "object": "list",
        "data": [
            {"id": "chronos-demo", "object": "model", "owned_by": "mockllm"},
            *[{"id": i, "object": "model", "owned_by": "mockllm"} for i in ids],
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body = await request.json()
    messages = body.get("messages") or []
    prompt = _last_user_message(messages)
    answer, fixture_id = _pick_answer(prompt)

    model = body.get("model") or "chronos-demo"
    # Deterministic id so cache behaviour is reproducible across runs.
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]
    created = int(time.time())

    # Optional latency simulation, off by default so CI stays fast.
    latency_ms = int(os.getenv("MOCKLLM_LATENCY_MS", "0"))
    if latency_ms > 0:
        import asyncio

        await asyncio.sleep(latency_ms / 1000.0)

    if body.get("stream"):
        return StreamingResponse(
            _stream(digest, created, model, answer),
            media_type="text/event-stream",
        )

    return JSONResponse(
        {
            "id": f"chatcmpl-{digest}",
            "object": "chat.completion",
            "created": created,
            "model": model,
            "system_fingerprint": f"mockllm-{fixture_id}",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": _token_estimate(prompt),
                "completion_tokens": _token_estimate(answer),
                "total_tokens": _token_estimate(prompt) + _token_estimate(answer),
            },
        }
    )


async def _stream(digest: str, created: int, model: str, answer: str):
    head = {
        "id": f"chatcmpl-{digest}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(head)}\n\n"
    for word in answer.split(" "):
        chunk = {
            "id": f"chatcmpl-{digest}",
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {"index": 0, "delta": {"content": word + " "}, "finish_reason": None}
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
    tail = {
        "id": f"chatcmpl-{digest}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(tail)}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> Any:
    """Deterministic pseudo-embeddings.

    Only needed if someone switches the semantic cache out of direct-only mode
    while offline. The vectors are hash-derived, not meaningful.
    """
    body = await request.json()
    raw = body.get("input")
    items = raw if isinstance(raw, list) else [raw or ""]
    dim = int(body.get("dimensions") or 1536)

    data = []
    for idx, item in enumerate(items):
        seed = hashlib.sha256(str(item).encode("utf-8")).digest()
        vec = [((seed[i % len(seed)] / 255.0) - 0.5) for i in range(dim)]
        data.append({"object": "embedding", "index": idx, "embedding": vec})

    return JSONResponse(
        {
            "object": "list",
            "data": data,
            "model": body.get("model") or "mock-embedding",
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        }
    )
