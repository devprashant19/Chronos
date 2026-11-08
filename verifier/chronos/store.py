"""Report storage in SQLite.

Reports are what make a score defensible: a header says 42, and the report says
which sentence, which source, which published date and which term of the
formula produced the 42. The console's score drawer and
`GET /v1/reports/{id}` both read from here.

SQLite is accessed from a thread pool rather than asynchronously. The write
volume is one row per verified answer, so a connection-per-call against WAL is
well within budget, and it avoids adding a database service to a stack that
already has four.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import Policy, VerifyResponse

logger = logging.getLogger("chronos.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    report_id   TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    request_id  TEXT,
    virtual_key TEXT,
    model       TEXT,
    prompt      TEXT,
    answer      TEXT,
    verdict     TEXT NOT NULL,
    score       REAL NOT NULL,
    claim_count INTEGER NOT NULL DEFAULT 0,
    stale_count INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,
    ttl_seconds INTEGER NOT NULL DEFAULT 0,
    cache_hit   INTEGER NOT NULL DEFAULT 0,
    cache_id    TEXT,
    cache_key   TEXT,
    evicted     INTEGER NOT NULL DEFAULT 0,
    tavily_credits INTEGER NOT NULL DEFAULT 0,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    degraded    INTEGER NOT NULL DEFAULT 0,
    payload     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_created  ON reports(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_verdict  ON reports(verdict);
CREATE INDEX IF NOT EXISTS idx_reports_cachekey ON reports(cache_key);
CREATE INDEX IF NOT EXISTS idx_reports_request  ON reports(request_id);

CREATE TABLE IF NOT EXISTS policies (
    virtual_key TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cache_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    kind        TEXT NOT NULL,
    cache_id    TEXT,
    cache_key   TEXT,
    report_id   TEXT,
    score       REAL,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_cache_events_created ON cache_events(created_at DESC);

-- What retention deleted, so lifetime counters survive pruning. One row.
CREATE TABLE IF NOT EXISTS pruned_totals (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    answers        INTEGER NOT NULL DEFAULT 0,
    claims         INTEGER NOT NULL DEFAULT 0,
    skipped        INTEGER NOT NULL DEFAULT 0,
    stale_claims   INTEGER NOT NULL DEFAULT 0,
    credits        INTEGER NOT NULL DEFAULT 0,
    outdated       INTEGER NOT NULL DEFAULT 0,
    evictions      INTEGER NOT NULL DEFAULT 0,
    stale_hits     INTEGER NOT NULL DEFAULT 0,
    events         INTEGER NOT NULL DEFAULT 0,
    last_pruned_at REAL
);
"""

#: The aggregate both the counters and pruning compute, over any WHERE clause.
_AGGREGATE = """SELECT
     COUNT(*)                                      AS answers,
     COALESCE(SUM(claim_count), 0)                 AS claims,
     COALESCE(SUM(skipped), 0)                     AS skipped,
     COALESCE(SUM(stale_count), 0)                 AS stale_claims,
     COALESCE(SUM(tavily_credits), 0)              AS credits,
     COALESCE(SUM(CASE WHEN verdict='outdated' THEN 1 ELSE 0 END), 0) AS outdated,
     COALESCE(SUM(CASE WHEN evicted=1 THEN 1 ELSE 0 END), 0)          AS evictions,
     COALESCE(SUM(CASE WHEN evicted=1 AND cache_hit=1 THEN 1 ELSE 0 END), 0) AS stale_hits
   FROM reports"""

_TOTAL_COLUMNS = ("answers", "claims", "skipped", "stale_claims", "credits", "outdated", "evictions", "stale_hits")


class ReportStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._initialised = False

    # -- lifecycle ------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        path = Path(self.db_path)
        if path.parent and str(path.parent) not in {"", "."}:
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        self._initialised = True

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)
        logger.info("report store ready at %s", self.db_path)

    # -- reports --------------------------------------------------------

    def _save_sync(self, report: VerifyResponse, prompt: str, answer: str) -> None:
        payload = report.model_dump_json(by_alias=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO reports (
                    report_id, created_at, request_id, virtual_key, model,
                    prompt, answer, verdict, score, claim_count, stale_count,
                    skipped, ttl_seconds, cache_hit, cache_id, cache_key,
                    evicted, tavily_credits, latency_ms, degraded, payload
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    report.report_id,
                    report.created_at.timestamp(),
                    report.request_id,
                    getattr(report, "virtual_key", "") or "",
                    "",
                    prompt[:4000],
                    answer[:16000],
                    report.verdict,
                    report.score,
                    report.claim_count,
                    report.stale_count,
                    report.skipped,
                    report.ttl_seconds,
                    1 if report.cache_hit else 0,
                    report.cache_id,
                    "",
                    1 if report.evicted else 0,
                    report.tavily_credits,
                    report.latency_ms,
                    1 if report.degraded else 0,
                    payload,
                ),
            )

    async def save(self, report: VerifyResponse, prompt: str = "", answer: str = "") -> None:
        try:
            await asyncio.to_thread(self._save_sync, report, prompt, answer)
        except Exception as exc:
            # Losing a report must not fail the verification that produced it.
            logger.warning("could not persist report %s: %s", report.report_id, exc)

    def _get_sync(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM reports WHERE report_id = ?", (report_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

    async def get(self, report_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_sync, report_id)

    def _recent_sync(self, limit: int, verdict: str | None) -> list[dict[str, Any]]:
        query = (
            "SELECT report_id, created_at, request_id, verdict, score, claim_count, "
            "stale_count, skipped, ttl_seconds, cache_hit, cache_id, evicted, "
            "tavily_credits, latency_ms, degraded, prompt FROM reports"
        )
        params: tuple[Any, ...] = ()
        if verdict:
            query += " WHERE verdict = ?"
            params = (verdict,)
        query += " ORDER BY created_at DESC LIMIT ?"
        params = (*params, limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    async def recent(self, limit: int = 50, verdict: str | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._recent_sync, limit, verdict)

    # -- cache events (the eviction timeline) ---------------------------

    def _event_sync(
        self, kind: str, cache_id: str, cache_key: str, report_id: str, score: float, detail: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO cache_events (created_at, kind, cache_id, cache_key, report_id, score, detail)
                   VALUES (?,?,?,?,?,?,?)""",
                (time.time(), kind, cache_id, cache_key, report_id, score, detail),
            )

    async def record_cache_event(
        self,
        kind: str,
        *,
        cache_id: str = "",
        cache_key: str = "",
        report_id: str = "",
        score: float = 0.0,
        detail: str = "",
    ) -> None:
        try:
            await asyncio.to_thread(
                self._event_sync, kind, cache_id, cache_key, report_id, score, detail
            )
        except Exception as exc:
            logger.debug("could not record cache event: %s", exc)

    def _cache_events_sync(self, limit: int, since: float) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT created_at, kind, cache_id, cache_key, report_id, score, detail
                   FROM cache_events WHERE created_at >= ?
                   ORDER BY created_at DESC LIMIT ?""",
                (since, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    async def cache_events(self, limit: int = 200, since_seconds: int = 86400) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._cache_events_sync, limit, time.time() - since_seconds
        )

    # -- aggregate counters for /v1/stats --------------------------------

    def _counters_sync(self) -> dict[str, Any]:
        with self._connect() as conn:
            live = conn.execute(_AGGREGATE).fetchone()
            pruned = conn.execute("SELECT * FROM pruned_totals WHERE id = 1").fetchone()
            # Lifetime counters are what is stored plus what retention has
            # already deleted, so pruning never makes a counter go backwards.
            row = {
                col: int(live[col]) + (int(pruned[col]) if pruned is not None else 0)
                for col in _TOTAL_COLUMNS
            }

            latencies = [
                r["latency_ms"]
                for r in conn.execute(
                    "SELECT latency_ms FROM reports WHERE latency_ms > 0 ORDER BY latency_ms"
                ).fetchall()
            ]

        def pct(values: list[int], q: float) -> float:
            if not values:
                return 0.0
            idx = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
            return float(values[idx])

        return {
            "answers_checked": row["answers"],
            "claims_checked": row["claims"],
            "claims_skipped": row["skipped"],
            "stale_claims": row["stale_claims"],
            "outdated_answers": row["outdated"],
            "cache_evictions": row["evictions"],
            "stale_hits_prevented": row["stale_hits"],
            "tavily_credits_used": row["credits"],
            "verify_p50_ms": pct(latencies, 0.50),
            "verify_p95_ms": pct(latencies, 0.95),
            "reports_stored": int(live["answers"]),
            "reports_pruned": int(pruned["answers"]) if pruned is not None else 0,
        }

    # -- retention ------------------------------------------------------

    def _prune_sync(self, older_than: float) -> dict[str, int]:
        with self._connect() as conn:
            gone = conn.execute(_AGGREGATE + " WHERE created_at < ?", (older_than,)).fetchone()
            events = conn.execute(
                "SELECT COUNT(*) AS n FROM cache_events WHERE created_at < ?", (older_than,)
            ).fetchone()["n"]
            if not gone["answers"] and not events:
                return {"reports": 0, "events": 0}

            # Fold the counts in before deleting, in the same transaction, so
            # a crash between the two cannot lose or double-count anything.
            conn.execute("INSERT OR IGNORE INTO pruned_totals (id) VALUES (1)")
            assignments = ", ".join(f"{col} = {col} + ?" for col in _TOTAL_COLUMNS)
            conn.execute(
                f"UPDATE pruned_totals SET {assignments}, events = events + ?, last_pruned_at = ? WHERE id = 1",
                (*(int(gone[col]) for col in _TOTAL_COLUMNS), int(events), time.time()),
            )
            conn.execute("DELETE FROM reports WHERE created_at < ?", (older_than,))
            conn.execute("DELETE FROM cache_events WHERE created_at < ?", (older_than,))
        return {"reports": int(gone["answers"]), "events": int(events)}

    async def prune(self, retention_days: int) -> dict[str, int]:
        """Delete reports and cache events older than `retention_days`.

        Zero means keep everything. The lifetime counters in /v1/stats are
        unaffected: what is deleted is folded into `pruned_totals` first.
        Reports hold third-party page text — titles, URLs, quotes — so keeping
        them forever is a decision, not a default. See docs/security.md.
        """
        if retention_days <= 0:
            return {"reports": 0, "events": 0}
        cutoff = time.time() - retention_days * 86400
        try:
            return await asyncio.to_thread(self._prune_sync, cutoff)
        except Exception as exc:
            logger.warning("could not prune reports: %s", exc)
            return {"reports": 0, "events": 0}

    async def counters(self) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self._counters_sync)
        except Exception as exc:
            logger.warning("could not read counters: %s", exc)
            return {}

    # -- policies --------------------------------------------------------

    def _policy_get_sync(self, virtual_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM policies WHERE virtual_key = ?", (virtual_key,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

    async def get_policy(self, virtual_key: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._policy_get_sync, virtual_key)

    def _policy_put_sync(self, policy: Policy) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO policies (virtual_key, payload, updated_at)
                   VALUES (?,?,?)""",
                (policy.virtual_key, policy.model_dump_json(), time.time()),
            )

    async def put_policy(self, policy: Policy) -> None:
        await asyncio.to_thread(self._policy_put_sync, policy)

    def _policies_sync(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM policies").fetchall()
        out = []
        for r in rows:
            try:
                out.append(json.loads(r["payload"]))
            except json.JSONDecodeError:
                continue
        return out

    async def all_policies(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._policies_sync)

    # -- sweeper support -------------------------------------------------

    def _hot_cache_keys_sync(self, limit: int) -> list[dict[str, Any]]:
        """Cache entries seen most often, newest verdict first.

        "Most-hit" is approximated by how many reports mention a cache id,
        which is the only hit signal the verifier sees.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT cache_id, COUNT(*) AS hits, MAX(created_at) AS last_seen,
                          MIN(score) AS worst_score
                   FROM reports
                   WHERE cache_id IS NOT NULL AND cache_id != '' AND evicted = 0
                   GROUP BY cache_id
                   ORDER BY hits DESC, last_seen DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    async def hot_cache_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(self._hot_cache_keys_sync, limit)
        except Exception as exc:
            logger.debug("could not read hot cache entries: %s", exc)
            return []

    def _mark_evicted_sync(self, report_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE reports SET evicted = 1 WHERE report_id = ?", (report_id,))

    async def mark_evicted(self, report_id: str) -> None:
        try:
            await asyncio.to_thread(self._mark_evicted_sync, report_id)
        except Exception as exc:
            logger.debug("could not mark report evicted: %s", exc)
