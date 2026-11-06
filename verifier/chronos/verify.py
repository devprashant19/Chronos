"""Verification: claims in, scored verdicts out.

The flow for one answer:

    extract claims
      └─ for each claim, in parallel under a semaphore:
            claim cache hit?  ──yes──> reuse the verdict, re-scored for age
                              ──no───> Tavily search → Tavily extract → judge
                                       → score → store in the claim cache
      └─ reduce to an answer score, verdict and cache TTL

Three decisions in here matter more than the plumbing:

**Timeless claims never reach Tavily.** They are scored 100 and skipped. This
is the single largest saving in the system and it is measured in the benchmark.

**A cache hit is re-scored, not replayed.** A verdict pulled from the claim
cache keeps its evidence but its decay term D is recomputed against *now*, so a
claim verified 50 minutes ago under a 1-hour re-check interval scores lower
than the same claim verified a minute ago. Replaying the stored score would
reintroduce exactly the staleness Chronos exists to catch, one level up.

**Evidence absent is not evidence against.** A claim with no usable sources is
`unverifiable`, scored but never counted as stale, and never used to evict a
cache entry. Guessing here would make Chronos delete good cache entries on a
bad search day.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime

from .cache import ClaimCache
from .events import BUS
from .extract import extract_claims
from .judge_rules import judge as judge_by_rules
from .judge_rules import values_agree
from .llm import LLMClient, ModelUnavailable
from .metrics import METRICS
from .models import (
    Claim,
    ClaimVerdict,
    Evidence,
    VerifyRequest,
    VerifyResponse,
    utcnow,
)
from .score import (
    answer_score,
    answer_ttl_seconds,
    classify_source,
    date_from_url,
    is_contradicted,
    score_claim,
    verdict_for,
)
from .settings import SKIPPABLE_CLASSES, Settings, get_settings
from .tavily_client import (
    BudgetExhausted,
    TavilyClient,
    build_query,
    official_domains_for,
    topic_for_class,
)

logger = logging.getLogger("chronos.verify")

JUDGE_SYSTEM = """You judge whether a claim is still true, using only the sources given.

Today's date is {today}.

Return STRICT JSON, no prose:
{{"sources": [{{"url": "...", "call": "supports|contradicts|insufficient", "quote": "..."}}],
  "current_value": "...", "confidence": 0.0, "best_source_url": "...", "publish_date": "YYYY-MM-DD"}}

Rules:
- Judge EVERY source given, one entry each, in the order given.
- "supports" means the source confirms the claim's stated value is still
  correct. "contradicts" means the source gives a different current value.
  "insufficient" means the source does not address the claim.
- "quote" must be a sentence copied verbatim from that source. Empty if
  insufficient.
- "current_value" is what the sources say the value is NOW. If the claim is
  still correct, repeat the stated value.
- "confidence" is your confidence in your own judgement, 0.0 to 1.0.
- "publish_date" is the publication date of the best source, if given.
- Do not use any knowledge beyond the sources. If they do not settle it, say
  insufficient.
"""

JUDGE_USER = """Claim: {text}
Subject: {subject}
Property: {predicate}
Value the answer stated: {stated_value}
Class: {claim_class}

Sources:
{sources}
"""


class Verifier:
    """Orchestrates extraction, evidence gathering, judging and scoring."""

    def __init__(
        self,
        settings: Settings | None = None,
        claim_cache: ClaimCache | None = None,
        tavily: TavilyClient | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.claim_cache = claim_cache or ClaimCache(self.settings)
        self.tavily = tavily or TavilyClient(self.settings)
        self.llm = llm or LLMClient(self.settings)
        self._semaphore = asyncio.Semaphore(max(1, self.settings.claim_concurrency))

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def verify(self, req: VerifyRequest) -> VerifyResponse:
        """Verify one answer and return a complete report."""
        started = time.perf_counter()
        report_id = uuid.uuid4().hex[:16]
        now = datetime.now(UTC)

        degraded = False
        reason: str | None = None

        with METRICS.time_verify(req.mode, req.cache_hit):
            extraction = await extract_claims(
                req.answer, self.llm if self.settings.live_models else None, self.settings
            )

            if extraction.error:
                return self._unverifiable(
                    report_id, req, started, reason=extraction.error, skipped=extraction.skipped
                )

            claims = extraction.claims[: self.settings.max_claims_per_answer]

            if not claims:
                # Nothing time-sensitive was said. That is a pass, not a
                # failure: the answer cannot go stale.
                elapsed = int((time.perf_counter() - started) * 1000)
                response = VerifyResponse(
                    report_id=report_id,
                    score=100.0,
                    verdict="current",
                    claims=[],
                    claim_count=0,
                    stale_count=0,
                    skipped=extraction.skipped,
                    ttl_seconds=answer_ttl_seconds([]),
                    latency_ms=elapsed,
                    request_id=req.request_id,
                    cache_hit=req.cache_hit,
                    cache_id=req.cache_id,
                    reason="no time-sensitive claims found",
                )
                METRICS.answers.labels(
                    verdict="current", cache_hit=str(bool(req.cache_hit)).lower()
                ).inc()
                return response

            try:
                verdicts = await asyncio.wait_for(
                    self._verify_claims(claims, now),
                    timeout=self.settings.verify_deadline_s,
                )
            except TimeoutError:
                logger.warning("verification deadline hit for report %s", report_id)
                return self._unverifiable(
                    report_id,
                    req,
                    started,
                    reason=f"verification exceeded {self.settings.verify_deadline_s}s",
                    skipped=extraction.skipped,
                )
            except BudgetExhausted as exc:
                degraded = True
                reason = str(exc)
                logger.warning("tavily budget exhausted: %s", exc)
                verdicts = [self._budget_skipped(c, now) for c in claims]

        credits = sum(v.tavily_credits for v in verdicts)
        checkable = [v for v in verdicts if v.verdict != "unverifiable"]

        score = answer_score([v.score for v in checkable]) if checkable else 0.0
        stale_count = sum(1 for v in verdicts if v.verdict == "outdated")

        if not checkable:
            verdict = "unverifiable"
        elif stale_count:
            verdict = "outdated"
        else:
            verdict = verdict_for(score, has_evidence=True)

        ttl = answer_ttl_seconds([v.claim_class for v in verdicts])
        elapsed = int((time.perf_counter() - started) * 1000)

        response = VerifyResponse(
            report_id=report_id,
            score=round(score, 1),
            verdict=verdict,
            claims=verdicts,
            claim_count=len(verdicts),
            stale_count=stale_count,
            skipped=extraction.skipped,
            ttl_seconds=ttl,
            tavily_credits=credits,
            degraded=degraded,
            reason=reason,
            latency_ms=elapsed,
            request_id=req.request_id,
            cache_hit=req.cache_hit,
            cache_id=req.cache_id,
        )

        METRICS.answers.labels(
            verdict=verdict, cache_hit=str(bool(req.cache_hit)).lower()
        ).inc()
        METRICS.budget_remaining.set(self.tavily.budget.remaining)

        # Guard mode may want a corrected answer rather than just a verdict.
        if req.regenerate and score < req.threshold and stale_count:
            corrected = await self._regenerate(req, verdicts)
            if corrected:
                response.corrected_answer = corrected
            else:
                response.blocked = True

        BUS.publish(
            "verdict",
            {
                "report_id": report_id,
                "request_id": req.request_id,
                "verdict": verdict,
                "score": response.score,
                "claims": len(verdicts),
                "stale": stale_count,
                "skipped": extraction.skipped,
                "cache_hit": req.cache_hit,
                "cache_id": req.cache_id,
                "ttl_seconds": ttl,
                "latency_ms": elapsed,
                "credits": credits,
                "model": req.model,
                "prompt": req.prompt[:160],
                "degraded": degraded,
                "at": utcnow().isoformat(),
            },
        )
        return response

    # ------------------------------------------------------------------
    # per-claim
    # ------------------------------------------------------------------

    async def _verify_claims(self, claims: list[Claim], now: datetime) -> list[ClaimVerdict]:
        tasks = [self._verify_one(c, now) for c in claims]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: list[ClaimVerdict] = []
        for claim, result in zip(claims, results, strict=False):
            if isinstance(result, BudgetExhausted):
                raise result
            if isinstance(result, BaseException):
                logger.warning("claim verification failed (%s): %s", claim.subject, result)
                out.append(self._unverifiable_claim(claim, now, str(result)))
                continue
            out.append(result)
        return out

    async def _verify_one(self, claim: Claim, now: datetime) -> ClaimVerdict:
        # 1. Timeless claims are skipped outright.
        if claim.claim_class in SKIPPABLE_CLASSES:
            METRICS.record_claim(claim.claim_class, "current")
            return ClaimVerdict(
                text=claim.text,
                span_start=claim.span_start,
                span_end=claim.span_end,
                subject=claim.subject,
                predicate=claim.predicate,
                stated_value=claim.stated_value,
                claim_class=claim.claim_class,
                verdict="current",
                score=100.0,
                current_value=claim.stated_value,
                quote="Timeless claim: skipped without spending a lookup.",
                checked_at=now,
                tavily_credits=0,
            )

        # 2. Claim cache.
        cached = await self.claim_cache.get(claim.subject, claim.predicate)
        if cached is not None:
            METRICS.claim_cache.labels(outcome="hit").inc()
            return self._rescore_cached(cached, claim, now)

        METRICS.claim_cache.labels(outcome="miss").inc()

        # 3. Evidence + judge.
        async with self._semaphore:
            verdict = await self._fresh_verdict(claim, now)

        if verdict.verdict != "unverifiable":
            await self.claim_cache.put(claim.subject, claim.predicate, verdict)

        METRICS.record_claim(claim.claim_class, verdict.verdict)
        return verdict

    def _rescore_cached(self, cached: ClaimVerdict, claim: Claim, now: datetime) -> ClaimVerdict:
        """Re-apply the decay term to a cached verdict.

        The evidence is reused; the age penalty is not. See the module docstring.
        """
        breakdown = score_claim(
            claim.claim_class,
            cached.evidence,
            confidence=cached.breakdown.confidence if cached.breakdown else 0.6,
            last_checked=cached.checked_at,
            now=now,
        )
        verdict_name = verdict_for(
            breakdown.score,
            has_evidence=bool(cached.evidence),
            contradicted=is_contradicted(breakdown),
        )

        # If the cached evidence contradicted the *cached* stated value, the
        # live answer may still differ. Compare against this answer's value.
        if (
            cached.current_value
            and claim.stated_value
            and not values_agree(claim.stated_value, cached.current_value)
        ):
            verdict_name = "outdated"
            breakdown.score = min(breakdown.score, 40.0)

        METRICS.record_claim(claim.claim_class, verdict_name)

        return ClaimVerdict(
            text=claim.text,
            span_start=claim.span_start,
            span_end=claim.span_end,
            subject=claim.subject,
            predicate=claim.predicate,
            stated_value=claim.stated_value,
            claim_class=claim.claim_class,
            verdict=verdict_name,
            score=round(breakdown.score, 1),
            current_value=cached.current_value,
            source_url=cached.source_url,
            publish_date=cached.publish_date,
            quote=cached.quote,
            breakdown=breakdown,
            evidence=cached.evidence,
            from_claim_cache=True,
            checked_at=cached.checked_at,
            tavily_credits=0,
        )

    async def _fresh_verdict(self, claim: Claim, now: datetime) -> ClaimVerdict:
        """Buy evidence and judge it."""
        query = build_query(claim.subject, claim.predicate)
        if not query:
            return self._unverifiable_claim(claim, now, "no searchable subject")

        credits = 0
        search = await self.tavily.search(
            query,
            topic=topic_for_class(claim.claim_class),
            include_domains=official_domains_for(claim.subject) or None,
            search_depth="advanced",
        )
        credits += int(search.get("credits", 0))
        METRICS.record_credits("search", int(search.get("credits", 0)))

        results = search.get("results") or []
        if not results:
            return self._unverifiable_claim(
                claim, now, search.get("error") or "no sources found", credits=credits
            )

        # Pull full text for the top sources so the judge reads pages, not
        # search snippets.
        top_urls = [r["url"] for r in results[: self.settings.tavily_extract_top_n]]
        extracted = await self.tavily.extract(top_urls)
        credits += int(extracted.get("credits", 0))
        METRICS.record_credits("extract", int(extracted.get("credits", 0)))

        pages = {p["url"]: p.get("raw_content", "") for p in extracted.get("pages", [])}

        evidence: list[Evidence] = []
        recorded: dict[str, dict] = {}
        for r in results[: self.settings.tavily_max_results]:
            url = r["url"]
            body = pages.get(url) or r.get("content") or ""

            # Recorded fixtures may carry their own call/quote/current value.
            # A live search never does, so this is empty on the live path.
            if r.get("call") or r.get("current_value"):
                recorded[url] = {
                    "call": r.get("call"),
                    "quote": r.get("quote") or "",
                    "current_value": r.get("current_value") or "",
                }

            # Fall back to the date in the URL path when the search API
            # returned none; an undated source scores R = 0.4 and drags an
            # otherwise well-supported claim below the `current` threshold.
            published = r.get("published_date")
            if not published:
                from_url = date_from_url(url)
                if from_url is not None:
                    published = from_url.date().isoformat()

            evidence.append(
                Evidence(
                    url=url,
                    title=r.get("title", ""),
                    snippet=body[:1500],
                    published_date=published,
                    tier=classify_source(url, claim.subject),
                    call="insufficient",
                )
            )

        judgement = await self._judge(claim, evidence, recorded)

        # Apply the judge's per-source calls.
        calls = judgement.get("sources") or []
        by_url = {str(c.get("url", "")): c for c in calls if isinstance(c, dict)}
        for e in evidence:
            call = by_url.get(e.url) or {}
            raw_call = str(call.get("call", "insufficient")).lower()
            e.call = raw_call if raw_call in {"supports", "contradicts", "insufficient"} else "insufficient"
            e.quote = str(call.get("quote", ""))[:600]

        confidence = _clamp_float(judgement.get("confidence"), 0.6)
        current_value = str(judgement.get("current_value") or "").strip()
        best_url = str(judgement.get("best_source_url") or "") or (evidence[0].url if evidence else "")
        publish_date = str(judgement.get("publish_date") or "") or next(
            (e.published_date for e in evidence if e.published_date), None
        )
        quote = next((e.quote for e in evidence if e.quote), "")

        has_evidence = any(e.call in {"supports", "contradicts"} for e in evidence)
        breakdown = score_claim(
            claim.claim_class,
            evidence,
            confidence=confidence,
            last_checked=now,  # just checked: D = 0
            now=now,
        )
        verdict_name = verdict_for(
            breakdown.score,
            has_evidence=has_evidence,
            contradicted=is_contradicted(breakdown),
        )

        # Consistency guard: if the evidence's current value agrees with what
        # the answer said, the claim cannot be `outdated`, whatever the score
        # arithmetic produced. Reporting "outdated, current value: Satya
        # Nadella" for the claim "Satya Nadella is the CEO of Microsoft" is
        # self-contradictory output, and it happened whenever thin or undated
        # evidence pushed a well-supported claim under the threshold. Such a
        # claim is `check` — worth a human look, not a correction.
        if (
            verdict_name == "outdated"
            and current_value
            and values_agree(claim.stated_value, current_value)
        ):
            verdict_name = "check"
            breakdown.score = max(breakdown.score, self.settings.check_threshold)

        return ClaimVerdict(
            text=claim.text,
            span_start=claim.span_start,
            span_end=claim.span_end,
            subject=claim.subject,
            predicate=claim.predicate,
            stated_value=claim.stated_value,
            claim_class=claim.claim_class,
            verdict=verdict_name,
            score=round(breakdown.score, 1),
            current_value=current_value or None,
            source_url=best_url or None,
            publish_date=publish_date,
            quote=quote or None,
            breakdown=breakdown,
            evidence=evidence,
            from_claim_cache=False,
            checked_at=now,
            tavily_credits=credits,
        )

    # ------------------------------------------------------------------
    # judging
    # ------------------------------------------------------------------

    async def _judge(
        self, claim: Claim, evidence: list[Evidence], recorded: dict[str, dict] | None = None
    ) -> dict:
        """Ask a model to judge the evidence, or judge it by rule when offline."""
        if not self.settings.live_models:
            return judge_by_rules(claim, evidence, recorded)

        rendered = "\n\n".join(
            f"[{i + 1}] url: {e.url}\npublished: {e.published_date or 'unknown'}\n"
            f"title: {e.title}\ntext: {e.snippet[:1200]}"
            for i, e in enumerate(evidence)
        )
        user = JUDGE_USER.format(
            text=claim.text,
            subject=claim.subject,
            predicate=claim.predicate,
            stated_value=claim.stated_value or "(none given)",
            claim_class=claim.claim_class,
            sources=rendered,
        )

        try:
            return await self.llm.complete_json(
                system=JUDGE_SYSTEM.format(today=datetime.now(UTC).date().isoformat()),
                user=user,
                model=self.settings.judge_model,
                max_tokens=1200,
                cache_key=f"chronos-judge:{claim.subject[:40]}",
            )
        except ModelUnavailable as exc:
            logger.warning("judge unavailable, falling back to rules: %s", exc)
            return judge_by_rules(claim, evidence, recorded)

    # ------------------------------------------------------------------
    # regeneration (guard mode)
    # ------------------------------------------------------------------

    async def _regenerate(self, req: VerifyRequest, verdicts: list[ClaimVerdict]) -> str | None:
        """Rewrite an answer once, with the evidence injected.

        Only the outdated claims are corrected; the rest of the answer is left
        alone. Returning None tells the caller to block instead.
        """
        stale = [v for v in verdicts if v.verdict == "outdated" and v.current_value]
        if not stale or not self.settings.live_models:
            return None

        corrections = "\n".join(
            f"- {v.subject} {v.predicate}: the answer said {v.stated_value!r}, "
            f"but {v.source_url} ({v.publish_date or 'undated'}) says {v.current_value!r}."
            for v in stale
        )
        system = (
            "You correct out-of-date facts in an assistant's answer. Rewrite the "
            "answer so each listed fact is current, citing the source URL inline "
            "for each correction. Change nothing else: keep the structure, tone "
            "and every statement not listed. Return only the corrected answer."
        )
        user = f"Answer:\n{req.answer}\n\nCorrections required:\n{corrections}"

        try:
            return await self.llm.complete_text(
                system=system, user=user, model=self.settings.judge_model, max_tokens=1600
            )
        except ModelUnavailable as exc:
            logger.warning("regeneration unavailable: %s", exc)
            return None

    # ------------------------------------------------------------------
    # degraded results
    # ------------------------------------------------------------------

    def _unverifiable(
        self,
        report_id: str,
        req: VerifyRequest,
        started: float,
        reason: str,
        skipped: int = 0,
    ) -> VerifyResponse:
        elapsed = int((time.perf_counter() - started) * 1000)
        METRICS.answers.labels(
            verdict="unverifiable", cache_hit=str(bool(req.cache_hit)).lower()
        ).inc()
        return VerifyResponse(
            report_id=report_id,
            score=0.0,
            verdict="unverifiable",
            claims=[],
            claim_count=0,
            stale_count=0,
            skipped=skipped,
            ttl_seconds=0,
            latency_ms=elapsed,
            request_id=req.request_id,
            cache_hit=req.cache_hit,
            cache_id=req.cache_id,
            degraded=True,
            reason=reason,
        )

    def _unverifiable_claim(
        self, claim: Claim, now: datetime, reason: str, credits: int = 0
    ) -> ClaimVerdict:
        METRICS.record_claim(claim.claim_class, "unverifiable")
        return ClaimVerdict(
            text=claim.text,
            span_start=claim.span_start,
            span_end=claim.span_end,
            subject=claim.subject,
            predicate=claim.predicate,
            stated_value=claim.stated_value,
            claim_class=claim.claim_class,
            verdict="unverifiable",
            score=0.0,
            quote=None,
            breakdown=None,
            evidence=[],
            checked_at=now,
            tavily_credits=credits,
            current_value=None,
            source_url=None,
            publish_date=None,
        )

    def _budget_skipped(self, claim: Claim, now: datetime) -> ClaimVerdict:
        return self._unverifiable_claim(
            claim, now, "Tavily budget spent; degraded to observe mode"
        )


# ---------------------------------------------------------------------------
# value comparison
# ---------------------------------------------------------------------------

def _clamp_float(value: object, default: float) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))
