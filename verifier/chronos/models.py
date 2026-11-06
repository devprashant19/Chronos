"""Pydantic models: the verifier's wire contract and internal types.

The extractor's output is validated against `Claim` strictly. That matters:
a model asked for JSON will occasionally invent a field or drop a span, and
Chronos would rather retry once and then declare an answer unverifiable than
score a claim it does not actually understand.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

ClaimClass = Literal["market", "pricing", "version", "leadership", "law", "events", "timeless"]
Verdict = Literal["current", "check", "outdated", "unverifiable", "pending"]
JudgeCall = Literal["supports", "contradicts", "insufficient"]
Mode = Literal["observe", "annotate", "guard"]


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class Claim(BaseModel):
    """One time-sensitive assertion pulled out of an answer."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1, max_length=2000)
    span_start: int = Field(ge=0)
    span_end: int = Field(ge=0)
    subject: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=300)
    stated_value: str = Field(default="", max_length=500)
    claim_class: ClaimClass = Field(alias="class")
    as_of_hint: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _spans_make_sense(self) -> Claim:
        if self.span_end <= self.span_start:
            raise ValueError("span_end must be greater than span_start")
        return self

    @field_validator("subject", "predicate", "stated_value", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class ExtractionResult(BaseModel):
    """What the extractor produced for one answer."""

    claims: list[Claim] = Field(default_factory=list)
    skipped: int = 0
    #: Set when extraction failed outright, which makes the answer unverifiable.
    error: str | None = None
    model_used: str | None = None


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """One source, as the judge saw it."""

    url: str
    title: str = ""
    snippet: str = ""
    published_date: str | None = None
    #: official | news | other — drives Q in the score.
    tier: Literal["official", "news", "other"] = "other"
    call: JudgeCall = "insufficient"
    quote: str = ""


class ScoreBreakdown(BaseModel):
    """Every term in the score, so a number is never unexplained.

    The console's score drawer renders this directly, and so does
    `GET /v1/reports/{id}`.
    """

    model_config = ConfigDict(populate_by_name=True)

    agreement: float = Field(description="A: net supporting share of sources, floored at 0")
    recency: float = Field(description="R: 1.0 under 7 days, linear to 0 at 365 days")
    quality: float = Field(description="Q: best source tier seen")
    confidence: float = Field(description="C: the judge's own confidence")
    volatility: float = Field(description="V: how fast this class of fact moves")
    decay: float = Field(description="D: age since last check over the re-check interval")
    base: float = Field(description="0.45A + 0.25R + 0.15Q + 0.15C")
    decay_multiplier: float = Field(description="(1 - 0.5*V*D)")
    score: float = Field(description="100 * base * decay_multiplier")

    supporting: int = 0
    contradicting: int = 0
    insufficient: int = 0

    formula: str = "S = 100*(0.45A + 0.25R + 0.15Q + 0.15C)*(1 - 0.5*V*D)"


class ClaimVerdict(BaseModel):
    """A scored claim, with its evidence and the arithmetic behind the score."""

    text: str
    span_start: int
    span_end: int
    subject: str
    predicate: str
    stated_value: str
    claim_class: ClaimClass = Field(
        validation_alias=AliasChoices("claim_class", "class"),
        serialization_alias="class",
    )

    verdict: Verdict
    score: float

    current_value: str | None = None
    source_url: str | None = None
    publish_date: str | None = None
    quote: str | None = None

    breakdown: ScoreBreakdown | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    #: True when this verdict came from the claim cache rather than a fresh
    #: Tavily round trip. The benchmark reports the hit rate.
    from_claim_cache: bool = False
    checked_at: datetime = Field(default_factory=utcnow)
    tavily_credits: int = 0

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# API: /v1/verify
# ---------------------------------------------------------------------------


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: str = Field(min_length=1)
    request_id: str = ""
    prompt: str = ""
    model: str = ""
    provider: str = ""
    virtual_key: str = ""

    cache_hit: bool = False
    cache_id: str = ""
    cache_key: str = ""
    hit_type: str = ""
    similarity: float = 0.0

    prompt_class: str = ""
    mode: Mode = "annotate"

    regenerate: bool = False
    threshold: int = 50
    asynchronous: bool = Field(default=False, alias="async")


class VerifyResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    report_id: str
    score: float = 0.0
    verdict: Verdict = "unverifiable"

    claims: list[ClaimVerdict] = Field(default_factory=list)
    claim_count: int = 0
    stale_count: int = 0
    skipped: int = 0

    ttl_seconds: int = 0

    corrected_answer: str | None = None
    blocked: bool = False

    tavily_credits: int = 0
    degraded: bool = False
    reason: str | None = None
    latency_ms: int = 0

    #: Echoed back so the console can correlate a report with gateway traffic.
    request_id: str = ""
    cache_hit: bool = False
    cache_id: str = ""
    evicted: bool = False
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# API: policies, stats, briefing
# ---------------------------------------------------------------------------


class Policy(BaseModel):
    """Per-virtual-key behaviour, settable at runtime."""

    model_config = ConfigDict(extra="ignore")

    virtual_key: str
    mode: Mode = "annotate"
    guard_threshold: int = Field(default=50, ge=0, le=100)
    #: Classes this key does not care about, skipped to save credits.
    skip_classes: list[ClaimClass] = Field(default_factory=list)
    daily_tavily_credits: int = Field(default=400, ge=0)
    evict_stale: bool = True
    updated_at: datetime = Field(default_factory=utcnow)


class Stats(BaseModel):
    answers_checked: int = 0
    claims_checked: int = 0
    claims_skipped: int = 0
    outdated_answers: int = 0
    stale_hits_prevented: int = 0
    cache_evictions: int = 0
    tavily_credits_used: int = 0
    claim_cache_hits: int = 0
    claim_cache_misses: int = 0
    verify_p50_ms: float = 0.0
    verify_p95_ms: float = 0.0
    mode: str = "annotate"
    offline_demo: bool = False
    tavily_configured: bool = False
    budget_remaining: int = 0
    uptime_s: float = 0.0

    @property
    def claim_cache_hit_rate(self) -> float:
        total = self.claim_cache_hits + self.claim_cache_misses
        return (self.claim_cache_hits / total) if total else 0.0


class BriefRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subject: str = Field(min_length=1, max_length=200)
    meeting_time: str = ""
    days: int = Field(default=30, ge=1, le=180)


class BriefLine(BaseModel):
    text: str
    verdict: Verdict
    score: float
    source_url: str | None = None
    publish_date: str | None = None
    reason: str | None = None


class BriefResponse(BaseModel):
    subject: str
    meeting_time: str = ""
    generated_at: datetime = Field(default_factory=utcnow)

    verified: list[BriefLine] = Field(default_factory=list)
    unconfirmed: list[BriefLine] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    tavily_credits: int = 0
    degraded: bool = False
    reason: str | None = None
