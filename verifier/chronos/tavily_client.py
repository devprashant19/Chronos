"""Live evidence via Tavily, with a credit budget and an offline fallback.

Two things here are worth more than the API wrapper itself.

**The budget.** Tavily credits are finite and a verifier sitting in front of a
gateway can burn them fast. `CreditBudget` meters every call, and when the day's
allowance is gone the verifier degrades to observe mode — it keeps extracting
and reporting claims, but stops buying evidence — rather than failing requests
or silently spending past the limit. Degradation is reported in the response
(`degraded`, `reason`) and on the `/v1/stats` endpoint, so it is never silent.

**The fixtures.** With `CHRONOS_OFFLINE_DEMO=1`, or with no API key, evidence
comes from `verifier/fixtures/evidence.json` instead of the network. That is
what lets `docker compose up` and CI exercise the real scoring path with no
credentials. Fixture evidence is marked `recorded: true` on every result so it
can never be mistaken for a live lookup.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .settings import Settings, get_settings

logger = logging.getLogger("chronos.tavily")

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"

#: Tavily bills per request; `advanced` search depth costs more than `basic`.
#: These are the units the benchmark reports, not currency.
CREDITS_PER_SEARCH_ADVANCED = 2
CREDITS_PER_SEARCH_BASIC = 1
CREDITS_PER_EXTRACT_URL = 1


class BudgetExhausted(RuntimeError):
    """Raised when a call would exceed the day's credit allowance."""


class CreditBudget:
    """A per-day credit meter, shared across the process.

    Deliberately not persisted: a restart resets the day's count. Persisting it
    would be straightforward (the SQLite store is right there), but a budget
    that silently blocks a fresh deployment is a worse failure than one that
    over-spends slightly after a restart, and this is a demo-scale service.
    The limitation is listed in the README.
    """

    def __init__(self, daily_limit: int) -> None:
        self.daily_limit = max(0, daily_limit)
        self._day = date.today()
        self._used = 0
        self._lock = asyncio.Lock()

    def _roll(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self._used = 0

    @property
    def used(self) -> int:
        self._roll()
        return self._used

    @property
    def remaining(self) -> int:
        self._roll()
        if self.daily_limit == 0:
            return 0
        return max(0, self.daily_limit - self._used)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    async def spend(self, credits: int) -> None:
        async with self._lock:
            self._roll()
            if self.daily_limit and self._used + credits > self.daily_limit:
                raise BudgetExhausted(
                    f"daily Tavily budget of {self.daily_limit} credits is spent "
                    f"({self._used} used); degrading to observe mode"
                )
            self._used += credits

    def snapshot(self) -> dict[str, int]:
        self._roll()
        return {"limit": self.daily_limit, "used": self._used, "remaining": self.remaining}


class TavilyClient:
    """Search and extract, with the budget applied and offline fallback."""

    def __init__(self, settings: Settings | None = None, budget: CreditBudget | None = None) -> None:
        self.settings = settings or get_settings()
        self.budget = budget or CreditBudget(self.settings.tavily_daily_credits)
        self._semaphore = asyncio.Semaphore(max(1, self.settings.tavily_concurrency))
        self._client: Any | None = None
        self._fixtures: dict[str, Any] | None = None

    # -- lifecycle ------------------------------------------------------

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from tavily import AsyncTavilyClient
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(f"tavily-python not installed: {exc}") from exc
        self._client = AsyncTavilyClient(api_key=self.settings.tavily_api_key)
        return self._client

    def _load_fixtures(self) -> dict[str, Any]:
        if self._fixtures is not None:
            return self._fixtures
        path = FIXTURES_DIR / "evidence.json"
        try:
            with path.open(encoding="utf-8") as fh:
                self._fixtures = json.load(fh)
        except Exception as exc:
            logger.warning("no evidence fixtures at %s (%s)", path, exc)
            self._fixtures = {"queries": []}
        return self._fixtures

    @property
    def live(self) -> bool:
        return self.settings.has_tavily

    # -- search ---------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        topic: str = "general",
        include_domains: list[str] | None = None,
        search_depth: str = "advanced",
        max_results: int | None = None,
    ) -> dict[str, Any]:
        """Run one search. Returns {"results": [...], "credits": n, "recorded": bool}."""
        max_results = max_results or self.settings.tavily_max_results

        if not self.live:
            return self._fixture_search(query)

        cost = (
            CREDITS_PER_SEARCH_ADVANCED
            if search_depth == "advanced"
            else CREDITS_PER_SEARCH_BASIC
        )
        await self.budget.spend(cost)

        kwargs: dict[str, Any] = {
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
            "topic": topic if topic in {"general", "news", "finance"} else "general",
        }
        if include_domains:
            kwargs["include_domains"] = include_domains
        if kwargs["topic"] == "news":
            # Only the news topic accepts a day window.
            kwargs["days"] = 30

        async with self._semaphore:
            try:
                client = self._ensure_client()
                raw = await client.search(**kwargs)
            except Exception as exc:
                logger.warning("tavily search failed for %r: %s", query[:80], exc)
                return {"results": [], "credits": cost, "recorded": False, "error": str(exc)}

        results = [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "content": r.get("content", "") or "",
                "published_date": r.get("published_date") or r.get("published_time"),
                "score": r.get("score"),
            }
            for r in (raw.get("results") or [])
            if r.get("url")
        ]
        return {"results": results, "credits": cost, "recorded": False, "answer": raw.get("answer")}

    async def extract(self, urls: list[str]) -> dict[str, Any]:
        """Pull page text for up to `tavily_extract_top_n` URLs."""
        urls = [u for u in urls if u][: self.settings.tavily_extract_top_n]
        if not urls:
            return {"pages": [], "credits": 0, "recorded": not self.live}

        if not self.live:
            return self._fixture_extract(urls)

        cost = CREDITS_PER_EXTRACT_URL * len(urls)
        await self.budget.spend(cost)

        async with self._semaphore:
            try:
                client = self._ensure_client()
                raw = await client.extract(urls=urls)
            except Exception as exc:
                logger.warning("tavily extract failed: %s", exc)
                return {"pages": [], "credits": cost, "recorded": False, "error": str(exc)}

        pages = [
            {
                "url": item.get("url", ""),
                "raw_content": (item.get("raw_content") or "")[:20000],
            }
            for item in (raw.get("results") or [])
            if item.get("url")
        ]
        return {"pages": pages, "credits": cost, "recorded": False}

    # -- offline --------------------------------------------------------

    def _fixture_search(self, query: str) -> dict[str, Any]:
        """Match a recorded query by keyword overlap.

        Matching is intentionally loose: fixtures exist so the pipeline runs,
        not so it is tested against exact query strings the extractor might
        reasonably reword.
        """
        fixtures = self._load_fixtures()
        tokens = set(_tokens(query))

        best: dict[str, Any] | None = None
        best_overlap = 0
        for entry in fixtures.get("queries", []):
            for pattern in entry.get("match", []):
                if re.search(pattern, query, re.I):
                    return _fixture_payload(entry)
            entry_tokens = set(_tokens(" ".join(entry.get("keywords", []))))
            overlap = len(tokens & entry_tokens)
            if overlap > best_overlap:
                best, best_overlap = entry, overlap

        if best is not None and best_overlap >= 2:
            return _fixture_payload(best)

        return {"results": [], "credits": 0, "recorded": True}

    def _fixture_extract(self, urls: list[str]) -> dict[str, Any]:
        fixtures = self._load_fixtures()
        by_url: dict[str, str] = {}
        for entry in fixtures.get("queries", []):
            for src in entry.get("results", []):
                if src.get("url"):
                    by_url[src["url"]] = src.get("raw_content") or src.get("content", "")

        pages = [{"url": u, "raw_content": by_url.get(u, "")} for u in urls]
        return {"pages": pages, "credits": 0, "recorded": True}


def _fixture_payload(entry: dict[str, Any]) -> dict[str, Any]:
    results = []
    for src in entry.get("results", []):
        published = src.get("published_date")
        if isinstance(published, str) and published.startswith("T-"):
            # Relative dates keep fixtures from aging: "T-3" means 3 days ago.
            try:
                days = int(published[2:])
                published = (
                    datetime.now(UTC) - timedelta(days=days)
                ).date().isoformat()
            except ValueError:
                published = None
        results.append(
            {
                "url": src.get("url", ""),
                "title": src.get("title", ""),
                "content": src.get("content", ""),
                "published_date": published,
                "score": src.get("score"),
                # Recorded judgement, read back by the offline judge.
                "call": src.get("call"),
                "quote": src.get("quote"),
                "current_value": src.get("current_value"),
            }
        )
    return {"results": results, "credits": 0, "recorded": True}


_TOKEN_RE = re.compile(r"[a-z0-9.+#-]{3,}")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


#: A named position. Tried before the generic wording below, because regex
#: alternation is leftmost-first: in "holder of role (CEO)" the generic phrase
#: starts at index 0 and would win, giving the useless query "who is the
#: current holder of role of Google".
_SPECIFIC_ROLE = re.compile(
    r"\b(chief executive|ceo|cto|cfo|coo|president|prime minister|chancellor|"
    r"monarch|king|queen|chair(?:man|person|woman)?|minister|governor|mayor|"
    r"head coach|coach|manager)\b",
    re.I,
)

#: The generic wording Chronos's own rule extractor produces.
_GENERIC_ROLE = re.compile(r"\b(holder of role|role holder|head of)\b", re.I)


def _role_word(predicate: str) -> str | None:
    """The role a predicate names, preferring the specific term."""
    match = _SPECIFIC_ROLE.search(predicate) or _GENERIC_ROLE.search(predicate)
    return match.group(0) if match else None


def build_query(subject: str, predicate: str) -> str:
    """Turn a claim into a search query.

    A pure function so it can be tested, and the one place query wording —
    which drives both result quality and credit spend — is decided.

    A role claim is asked as a question about the CURRENT holder. Measured on
    StaleBench: "Larry Page CEO of Google" retrieves pages *about Larry Page*,
    so the judge never sees a page saying who holds the job now and correctly
    reports "insufficient" — four of five missed stale claims failed exactly
    this way. "who is the current CEO of Google" retrieves the answer. The
    extractor is also told to put the organisation in `subject`; this is the
    safety net for when it does not.
    """
    subject = (subject or "").strip()
    predicate = (predicate or "").strip()
    if not (subject or predicate):
        return ""

    year = date.today().year
    role = _role_word(predicate)
    if role and subject:
        return f"who is the current {role} of {subject} {year}"

    base = f"{subject} {predicate}".strip()
    return f"{base} current {year}"


def topic_for_class(claim_class: str) -> str:
    """Tavily's news topic carries a date window; use it where recency rules."""
    if claim_class in {"market", "events"}:
        return "news"
    if claim_class in {"leadership", "law"}:
        return "news"
    return "general"


def official_domains_for(subject: str) -> list[str]:
    """Official domains to prefer for a subject, when we recognise it."""
    from .settings import OFFICIAL_DOMAIN_HINTS

    low = (subject or "").lower()
    out: list[str] = []
    for key, domains in OFFICIAL_DOMAIN_HINTS.items():
        if key in low:
            out.extend(domains)
    return out
