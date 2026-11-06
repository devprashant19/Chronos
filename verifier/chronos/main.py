"""The Chronos verifier service.

Endpoints
---------
    GET  /health                    liveness
    POST /v1/verify                 verify an answer, return a full report
    POST /v1/verify/async           accept an answer, report later (202)
    GET  /v1/reports/{id}           one stored report, with score breakdowns
    GET  /v1/reports                recent reports (bootstraps the console feed)
    GET  /v1/stream                 Server-Sent Events: the live feed
    GET  /v1/stats                  counters, latencies, budget, cache stats
    GET  /v1/cache/events           the eviction timeline
    POST /v1/sweep                  run one sweep now (demos and tests)
    POST /v1/brief                  briefing mode
    GET  /v1/policies/{vk}          read a virtual key's policy
    PUT  /v1/policies/{vk}          set a virtual key's policy
    GET  /metrics                   Prometheus

The service's contract with the plugin is narrow on purpose: the plugin posts
an answer plus its cache context, and gets back a score, a verdict and a TTL.
Everything expensive or stateful — models, Tavily, scoring, storage, the
sweeper — lives here, so the gateway's hot path stays thin.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .briefing import BriefingService
from .cache import ClaimCache
from .events import BUS
from .evictor import Evictor
from .llm import LLMClient
from .metrics import METRICS
from .models import (
    BriefRequest,
    BriefResponse,
    Policy,
    VerifyRequest,
    VerifyResponse,
)
from .policies import PolicyStore
from .settings import get_settings
from .store import ReportStore
from .sweeper import Sweeper
from .tavily_client import TavilyClient
from .verify import Verifier

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
)
logger = logging.getLogger("chronos")

STARTED_AT = time.time()


class App:
    """Container for the service's long-lived objects."""

    def __init__(self) -> None:
        self.store = ReportStore(settings.db_path)
        self.claim_cache = ClaimCache(settings)
        self.tavily = TavilyClient(settings)
        self.llm = LLMClient(settings)
        self.evictor = Evictor(settings)
        self.verifier = Verifier(settings, self.claim_cache, self.tavily, self.llm)
        self.policies = PolicyStore(self.store, settings)
        self.briefing = BriefingService(self.verifier, self.tavily, self.llm, settings)
        self.sweeper = Sweeper(
            self.verifier, self.store, self.claim_cache, self.evictor, settings
        )

    async def startup(self) -> None:
        await self.store.init()
        await self.claim_cache.connect()
        await self.policies.load()
        self.sweeper.start()

        logger.info(
            "chronos verifier up | offline_demo=%s tavily=%s models=%s claim_cache=%s",
            settings.offline_demo,
            "configured" if settings.tavily_api_key else "absent",
            settings.extract_model if settings.live_models else "disabled (offline)",
            self.claim_cache.backend,
        )
        if settings.offline_demo:
            logger.warning(
                "OFFLINE DEMO MODE: evidence comes from verifier/fixtures and no "
                "external call is made. Scores are real; the evidence is recorded."
            )

    async def shutdown(self) -> None:
        await self.sweeper.stop()
        await self.claim_cache.aclose()
        await self.evictor.aclose()
        await self.llm.aclose()


APP = App()


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    await APP.startup()
    try:
        yield
    finally:
        await APP.shutdown()


api = FastAPI(
    title="Chronos verifier",
    version="0.1.0",
    description="Extracts time-sensitive claims, verifies them with Tavily, scores them.",
    lifespan=lifespan,
)

# The console is served from a different origin in development.
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@api.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "chronos-verifier",
        "version": "0.1.0",
        "offline_demo": settings.offline_demo,
        "tavily_configured": bool(settings.tavily_api_key),
        "claim_cache": APP.claim_cache.backend,
        "uptime_s": round(time.time() - STARTED_AT, 1),
    }


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------


@api.post("/v1/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest) -> VerifyResponse:
    """Verify an answer inline and return the full report."""
    effective_mode = APP.policies.effective_mode(
        req.virtual_key, req.mode, APP.tavily.budget.exhausted
    )
    if effective_mode != req.mode:
        logger.info(
            "mode for %s resolved to %s (requested %s)",
            req.virtual_key or "(no vk)",
            effective_mode,
            req.mode,
        )
        req = req.model_copy(update={"mode": effective_mode})

    try:
        report = await APP.verifier.verify(req)
    except Exception as exc:
        # The plugin treats any error as "unverifiable" and passes the answer
        # through, but returning a 500 would also show up in the gateway's logs
        # as a Chronos fault, which is exactly what we want to be loud about.
        logger.exception("verification raised")
        raise HTTPException(status_code=500, detail=f"verification failed: {exc}") from exc

    await _finalise(report, req)
    return report


@api.post("/v1/verify/async", status_code=202)
async def verify_async(req: VerifyRequest, background: BackgroundTasks) -> dict[str, Any]:
    """Accept an answer and verify it after the response has gone out.

    This is the path fresh (non-cached) answers take, so the gateway's latency
    is unaffected. Because the caller is gone by the time the verdict exists,
    eviction for this path happens here rather than in the plugin.
    """
    report_id = await _reserve_report_id()
    background.add_task(_verify_in_background, req, report_id)
    return {
        "report_id": report_id,
        "verdict": "pending",
        "accepted": True,
        "poll": f"/v1/reports/{report_id}",
    }


async def _reserve_report_id() -> str:
    import uuid

    return uuid.uuid4().hex[:16]


async def _verify_in_background(req: VerifyRequest, report_id: str) -> None:
    try:
        report = await APP.verifier.verify(req)
        report.report_id = report_id
        await _finalise(report, req)
    except Exception:
        logger.exception("background verification failed for %s", report_id)


async def _finalise(report: VerifyResponse, req: VerifyRequest) -> None:
    """Persist a report and act on it: evict, count, announce."""
    policy = APP.policies.get(req.virtual_key)

    should_evict = (
        report.verdict == "outdated" and req.cache_hit and req.cache_id and policy.evict_stale
    )
    # Short-circuits: the clear is only attempted when the verdict warrants it.
    if should_evict and await APP.evictor.clear_cache_id(req.cache_id):
        report.evicted = True
        METRICS.evictions.labels(reason="stale_hit").inc()
        await APP.store.record_cache_event(
            "evicted",
            cache_id=req.cache_id,
            cache_key=req.cache_key,
            report_id=report.report_id,
            score=report.score,
            detail="served an outdated claim",
        )
        BUS.publish(
            "eviction",
            {
                "cache_id": req.cache_id,
                "cache_key": req.cache_key,
                "report_id": report.report_id,
                "score": report.score,
                "reason": "stale_hit",
                "stale_claims": report.stale_count,
            },
        )
        logger.info(
            "stale_hit_prevented cache_id=%s score=%.0f report=%s",
            req.cache_id,
            report.score,
            report.report_id,
        )

    await APP.store.save(report, prompt=req.prompt, answer=req.answer)


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------


@api.get("/v1/reports/{report_id}")
async def get_report(report_id: str) -> dict[str, Any]:
    report = await APP.store.get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="no such report")
    return report


@api.get("/v1/reports")
async def list_reports(limit: int = 50, verdict: str | None = None) -> dict[str, Any]:
    limit = max(1, min(500, limit))
    rows = await APP.store.recent(limit=limit, verdict=verdict)
    return {"reports": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# live stream
# ---------------------------------------------------------------------------


@api.get("/v1/stream")
async def stream(request: Request, replay: int = 20) -> StreamingResponse:
    """Server-Sent Events for the console."""

    async def generator():
        try:
            async for frame in BUS.stream(replay=max(0, min(100, replay))):
                if await request.is_disconnected():
                    break
                yield frame
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# stats and metrics
# ---------------------------------------------------------------------------


@api.get("/v1/stats")
async def stats() -> dict[str, Any]:
    counters = await APP.store.counters()
    cache_stats = APP.claim_cache.stats()
    budget = APP.tavily.budget.snapshot()

    return {
        **counters,
        "claim_cache": cache_stats,
        "tavily": {
            **budget,
            "configured": bool(settings.tavily_api_key),
            "live": APP.tavily.live,
        },
        "evictor": APP.evictor.stats(),
        "model_pacing": APP.llm.pacer.stats(),
        "sweeper": APP.sweeper.stats(),
        "stream_subscribers": BUS.subscriber_count,
        "mode": {
            "offline_demo": settings.offline_demo,
            "extract_model": settings.extract_model if settings.live_models else None,
            "judge_model": settings.judge_model if settings.live_models else None,
        },
        "thresholds": {
            "current": settings.current_threshold,
            "check": settings.check_threshold,
        },
        "uptime_s": round(time.time() - STARTED_AT, 1),
    }


@api.get("/v1/cache/events")
async def cache_events(limit: int = 200, since_seconds: int = 86400) -> dict[str, Any]:
    events = await APP.store.cache_events(
        limit=max(1, min(1000, limit)), since_seconds=max(60, since_seconds)
    )
    return {"events": events, "count": len(events)}


@api.post("/v1/sweep")
async def sweep_now() -> dict[str, Any]:
    """Run one sweep immediately. Exposed for demos and the end-to-end test."""
    result = await APP.sweeper.sweep_once()
    return {"ok": True, **result}


@api.get("/metrics")
async def metrics() -> Response:
    METRICS.budget_remaining.set(APP.tavily.budget.remaining)
    body, content_type = METRICS.render()
    return Response(content=body, media_type=content_type)


# ---------------------------------------------------------------------------
# policies
# ---------------------------------------------------------------------------


@api.get("/v1/policies/{virtual_key}", response_model=Policy)
async def get_policy(virtual_key: str) -> Policy:
    return APP.policies.get(virtual_key)


@api.put("/v1/policies/{virtual_key}", response_model=Policy)
async def put_policy(virtual_key: str, body: Policy) -> Policy:
    if body.virtual_key and body.virtual_key != virtual_key:
        raise HTTPException(
            status_code=400,
            detail="virtual_key in the body must match the path, or be omitted",
        )
    policy = body.model_copy(update={"virtual_key": virtual_key})
    return await APP.policies.put(policy)


@api.get("/v1/policies")
async def list_policies() -> dict[str, Any]:
    return {"policies": [p.model_dump() for p in APP.policies.all()]}


# ---------------------------------------------------------------------------
# briefing
# ---------------------------------------------------------------------------


@api.post("/v1/brief", response_model=BriefResponse)
async def brief(req: BriefRequest) -> BriefResponse:
    try:
        return await APP.briefing.brief(req)
    except Exception as exc:
        logger.exception("briefing failed")
        raise HTTPException(status_code=500, detail=f"briefing failed: {exc}") from exc


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


@api.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": str(exc)})


app = api
