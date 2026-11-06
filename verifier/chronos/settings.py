"""Configuration for the Chronos verifier.

Everything is environment-driven so the same image runs in CI, in the offline
demo and against live APIs, with no code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from functools import lru_cache


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Volatility policy
# ---------------------------------------------------------------------------

#: How long a verified claim stays trustworthy before Chronos re-checks it.
#: This is the TTL of the claim cache, and the denominator of the decay term in
#: the score.
RECHECK_INTERVAL: dict[str, timedelta | None] = {
    "market": timedelta(hours=1),
    "pricing": timedelta(days=3),
    "version": timedelta(days=3),
    "leadership": timedelta(days=14),
    "law": timedelta(days=14),
    "events": timedelta(days=7),
    "timeless": None,  # never re-checked
}

#: The longest Bifrost may cache an *answer* containing a claim of this class.
#: An answer's TTL is the minimum across its claims.
MAX_ANSWER_TTL: dict[str, timedelta | None] = {
    "market": timedelta(minutes=15),
    "pricing": timedelta(hours=6),
    "version": timedelta(hours=6),
    "leadership": timedelta(hours=24),
    "law": timedelta(hours=24),
    "events": timedelta(hours=12),
    "timeless": None,  # leave the plugin's default in place
}

#: Volatility weight V in the score: how much age alone should cost a claim.
VOLATILITY_WEIGHT: dict[str, float] = {
    "market": 1.0,
    "pricing": 0.8,
    "version": 0.7,
    "events": 0.6,
    "leadership": 0.4,
    "law": 0.3,
    "timeless": 0.0,
}

CLAIM_CLASSES = tuple(RECHECK_INTERVAL.keys())

#: Classes that never need live evidence. Skipping them is the single biggest
#: saver of Tavily credits, so it is measured explicitly in the benchmark.
SKIPPABLE_CLASSES = frozenset({"timeless"})

#: Domains treated as authoritative for a subject, when we can tell. Used to
#: steer Tavily's include_domains and to grade source quality.
OFFICIAL_DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "openai": ("openai.com", "platform.openai.com"),
    "anthropic": ("anthropic.com", "docs.anthropic.com"),
    "google": ("google.com", "blog.google", "cloud.google.com"),
    "python": ("python.org", "docs.python.org"),
    "node": ("nodejs.org",),
    "golang": ("go.dev", "golang.org"),
    "rust": ("rust-lang.org",),
    "postgres": ("postgresql.org",),
    "redis": ("redis.io",),
    "docker": ("docker.com", "docs.docker.com"),
    "kubernetes": ("kubernetes.io",),
    "aws": ("aws.amazon.com",),
    "azure": ("azure.microsoft.com", "microsoft.com"),
    "meta": ("about.fb.com", "meta.com"),
    "x": ("about.x.com", "x.com"),
    "twitter": ("about.x.com", "x.com"),
    "eu": ("europa.eu", "eur-lex.europa.eu"),
    "tavily": ("tavily.com", "docs.tavily.com"),
    "bifrost": ("getbifrost.ai", "docs.getbifrost.ai"),
}

#: Source-quality tiers for Q in the score.
OFFICIAL_TLDS = (".gov", ".gov.uk", ".europa.eu", ".int", ".edu")

MAJOR_NEWS_DOMAINS = frozenset(
    {
        "reuters.com",
        "apnews.com",
        "bbc.com",
        "bbc.co.uk",
        "ft.com",
        "wsj.com",
        "nytimes.com",
        "bloomberg.com",
        "theguardian.com",
        "economist.com",
        "cnbc.com",
        "techcrunch.com",
        "theverge.com",
        "arstechnica.com",
        "wired.com",
        "npr.org",
        "aljazeera.com",
        "politico.eu",
        "axios.com",
    }
)


@dataclass(frozen=True)
class Settings:
    """Resolved runtime configuration."""

    # --- service ---
    log_level: str = field(default_factory=lambda: os.getenv("CHRONOS_LOG_LEVEL", "INFO"))
    db_path: str = field(default_factory=lambda: os.getenv("CHRONOS_DB_PATH", "./chronos.sqlite"))
    redis_url: str = field(
        default_factory=lambda: os.getenv("CHRONOS_REDIS_URL", "redis://localhost:6379/1")
    )

    # --- Bifrost: every model call the verifier makes goes back through it ---
    bifrost_url: str = field(
        default_factory=lambda: os.getenv("CHRONOS_BIFROST_URL", "http://localhost:8080/v1")
    )
    bifrost_admin_url: str = field(
        default_factory=lambda: os.getenv("CHRONOS_BIFROST_ADMIN_URL", "http://localhost:8080")
    )
    virtual_key: str = field(default_factory=lambda: os.getenv("CHRONOS_VK", "chronos-verifier-vk"))

    extract_model: str = field(
        default_factory=lambda: os.getenv("CHRONOS_EXTRACT_MODEL", "openai/gpt-4o-mini")
    )
    judge_model: str = field(
        default_factory=lambda: os.getenv("CHRONOS_JUDGE_MODEL", "openai/gpt-4o-mini")
    )
    #: Comma-separated provider/model pairs Bifrost should fall back to.
    fallback_models: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            m.strip()
            for m in os.getenv(
                "CHRONOS_FALLBACK_MODELS",
                "groq/llama-3.1-8b-instant,ollama/llama3.1",
            ).split(",")
            if m.strip()
        )
    )

    # --- Tavily ---
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))
    tavily_max_results: int = field(default_factory=lambda: _env_int("CHRONOS_TAVILY_MAX_RESULTS", 5))
    tavily_extract_top_n: int = field(default_factory=lambda: _env_int("CHRONOS_TAVILY_EXTRACT_TOP_N", 3))
    tavily_daily_credits: int = field(
        default_factory=lambda: _env_int("CHRONOS_TAVILY_DAILY_CREDITS", 400)
    )

    # --- concurrency / limits ---
    claim_concurrency: int = field(default_factory=lambda: _env_int("CHRONOS_CLAIM_CONCURRENCY", 4))
    tavily_concurrency: int = field(default_factory=lambda: _env_int("CHRONOS_TAVILY_CONCURRENCY", 3))
    max_claims_per_answer: int = field(default_factory=lambda: _env_int("CHRONOS_MAX_CLAIMS", 12))
    verify_deadline_s: float = field(default_factory=lambda: _env_float("CHRONOS_VERIFY_DEADLINE_S", 20.0))

    # --- scoring thresholds ---
    current_threshold: float = field(default_factory=lambda: _env_float("CHRONOS_CURRENT_THRESHOLD", 80.0))
    check_threshold: float = field(default_factory=lambda: _env_float("CHRONOS_CHECK_THRESHOLD", 50.0))

    # --- sweeper ---
    sweep_interval_s: int = field(default_factory=lambda: _env_int("CHRONOS_SWEEP_INTERVAL_S", 600))
    sweep_batch: int = field(default_factory=lambda: _env_int("CHRONOS_SWEEP_BATCH", 50))
    sweeper_enabled: bool = field(default_factory=lambda: _env_bool("CHRONOS_SWEEPER_ENABLED", True))

    # --- offline / demo ---
    #: When true, no network calls are made: claim extraction uses a rule-based
    #: extractor and evidence comes from verifier/fixtures. This is what makes
    #: `docker compose up` and CI work with no keys.
    offline_demo: bool = field(default_factory=lambda: _env_bool("CHRONOS_OFFLINE_DEMO", False))

    @property
    def has_tavily(self) -> bool:
        return bool(self.tavily_api_key) and not self.offline_demo

    @property
    def live_models(self) -> bool:
        return not self.offline_demo


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def recheck_interval(claim_class: str) -> timedelta | None:
    return RECHECK_INTERVAL.get(claim_class, timedelta(days=7))


def max_answer_ttl(claim_class: str) -> timedelta | None:
    return MAX_ANSWER_TTL.get(claim_class, timedelta(hours=12))


def volatility_weight(claim_class: str) -> float:
    return VOLATILITY_WEIGHT.get(claim_class, 0.6)
