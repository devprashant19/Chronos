#!/usr/bin/env python3
"""StaleBench runner.

Measures what Chronos actually does, and writes bench/results.json. Nothing in
that file is a target or an estimate: every number here is produced by a run,
and the file records the run's configuration so a reader can tell what it
means.

    python bench/runner.py --verifier http://localhost:8000
    python bench/runner.py --offline            # fixtures only, no credits
    python bench/runner.py --limit 20           # a quick subset
    python bench/runner.py --audit              # label disagreements, in detail
    python bench/runner.py --modes observe,annotate,guard

What it reports
---------------
  precision / recall / F1 for the "outdated" verdict, overall and per class
  timeless skip rate      — how often a timeless claim costs no Tavily credit
  p50 / p95 latency       — per mode
  Tavily credits per 1,000 answers, with and without the claim cache
  stale cache hits prevented, on replayed traffic

A note on the headline metric: precision and recall are computed for the
`outdated` verdict against the dataset's labels. The `current` labels decay
(see bench/build_dataset.py), so --audit prints every disagreement with its
label confidence, which is how a label bug gets told apart from a system bug.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    print("this runner needs httpx:  pip install httpx", file=sys.stderr)
    raise SystemExit(2) from None

HERE = Path(__file__).parent
DATASET = HERE / "stalebench.json"
RESULTS = HERE / "results.json"

#: A claim is turned into a one-sentence answer so the verifier sees the same
#: shape of input it sees in production: an answer, not a pre-parsed claim.
STALE_VERDICTS = {"outdated"}


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    """Precision, recall, F1. Undefined cases report 0.0 rather than crash."""
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


class Bench:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.verifier = args.verifier.rstrip("/")
        self.dataset = json.loads(DATASET.read_text(encoding="utf-8"))
        self.claims: list[dict] = self.dataset["claims"]
        if args.limit:
            # Keep the group balance when subsetting, or the precision numbers
            # describe a different dataset than the one named.
            per_group = max(1, args.limit // 3)
            grouped: dict[str, list[dict]] = defaultdict(list)
            for c in self.claims:
                grouped[c["group"]].append(c)
            self.claims = [c for g in grouped.values() for c in g[:per_group]]

    async def run(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)

        # Generous: a paced verifier waits on purpose, and a client timeout
        # would be recorded as an error rather than as slowness.
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            health = await self._health(client)
            stats_before = await self._stats(client)

            modes = [m.strip() for m in self.args.modes.split(",") if m.strip()]
            per_mode: dict[str, Any] = {}

            # First pass on a cold claim cache, which is the honest number for
            # credits-per-answer. Later passes measure the cache's effect.
            first_mode = modes[0]
            cold = await self._pass(client, first_mode, label="cold")
            per_mode[first_mode] = cold

            for mode in modes[1:]:
                per_mode[mode] = await self._pass(client, mode, label="warm")

            warm = None
            if self.args.measure_cache:
                warm = await self._pass(client, first_mode, label="warm-repeat")

            replay = None
            if self.args.replay:
                replay = await self._replay(client)

            stats_after = await self._stats(client)

        finished = datetime.now(timezone.utc)

        return self._assemble(
            health, stats_before, stats_after, per_mode, cold, warm, replay, started, finished
        )

    # -- single pass ----------------------------------------------------

    async def _pass(self, client: httpx.AsyncClient, mode: str, label: str) -> dict[str, Any]:
        print(f"\n=== pass: mode={mode} ({label}) over {len(self.claims)} claims ===")

        sem = asyncio.Semaphore(self.args.concurrency)
        results: list[dict] = []

        async def one(claim: dict) -> None:
            async with sem:
                results.append(await self._verify(client, claim, mode))

        t0 = time.perf_counter()
        await asyncio.gather(*[one(c) for c in self.claims])
        wall = time.perf_counter() - t0

        results.sort(key=lambda r: r["id"])
        return self._score_pass(results, mode, label, wall)

    async def _verify(self, client: httpx.AsyncClient, claim: dict, mode: str) -> dict[str, Any]:
        payload = {
            "answer": claim["claim"],
            "prompt": f"{claim['subject']} {claim['predicate']}",
            "mode": mode,
            "request_id": f"bench-{claim['id']}",
        }

        t0 = time.perf_counter()
        try:
            resp = await client.post(f"{self.verifier}/v1/verify", json=payload)
            resp.raise_for_status()
            body = resp.json()
            error = None
        except Exception as exc:  # noqa: BLE001
            body = {}
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - t0) * 1000

        claims_out = body.get("claims") or []
        return {
            "id": claim["id"],
            "group": claim["group"],
            "class": claim["class"],
            "expected": claim["expected_verdict"],
            "label_confidence": claim.get("label_confidence", "high"),
            "label_basis": claim.get("label_basis", ""),
            "claim": claim["claim"],
            "actual": body.get("verdict", "error"),
            "score": body.get("score", 0.0),
            "claim_count": body.get("claim_count", 0),
            "skipped": body.get("skipped", 0),
            "credits": body.get("tavily_credits", 0),
            "ttl_seconds": body.get("ttl_seconds", 0),
            "from_claim_cache": bool(claims_out and claims_out[0].get("from_claim_cache")),
            "extracted_class": (claims_out[0].get("class") if claims_out else None),
            "current_value": (claims_out[0].get("current_value") if claims_out else None),
            "source_url": (claims_out[0].get("source_url") if claims_out else None),
            "latency_ms": round(latency_ms, 1),
            "verifier_latency_ms": body.get("latency_ms", 0),
            "degraded": bool(body.get("degraded")),
            "error": error,
        }

    # -- scoring --------------------------------------------------------

    def _score_pass(
        self, results: list[dict], mode: str, label: str, wall: float
    ) -> dict[str, Any]:
        errors = [r for r in results if r["error"]]
        usable = [r for r in results if not r["error"]]

        # Binary classification on "is this answer outdated?"
        tp = sum(1 for r in usable if r["expected"] == "outdated" and r["actual"] in STALE_VERDICTS)
        fp = sum(1 for r in usable if r["expected"] != "outdated" and r["actual"] in STALE_VERDICTS)
        fn = sum(1 for r in usable if r["expected"] == "outdated" and r["actual"] not in STALE_VERDICTS)
        tn = sum(1 for r in usable if r["expected"] != "outdated" and r["actual"] not in STALE_VERDICTS)

        overall = prf(tp, fp, fn)
        overall["tn"] = tn
        overall["accuracy"] = round((tp + tn) / len(usable), 4) if usable else 0.0

        # Per class, on the stale group only: that is where a class label means
        # something for detection.
        per_class: dict[str, Any] = {}
        classes = sorted({r["class"] for r in usable if r["class"] != "timeless"})
        for cls in classes:
            subset = [r for r in usable if r["class"] == cls]
            c_tp = sum(1 for r in subset if r["expected"] == "outdated" and r["actual"] in STALE_VERDICTS)
            c_fp = sum(1 for r in subset if r["expected"] != "outdated" and r["actual"] in STALE_VERDICTS)
            c_fn = sum(1 for r in subset if r["expected"] == "outdated" and r["actual"] not in STALE_VERDICTS)
            per_class[cls] = {**prf(c_tp, c_fp, c_fn), "n": len(subset)}

        # Timeless skip rate: a timeless claim should cost nothing.
        timeless = [r for r in usable if r["group"] == "timeless"]
        skipped_free = [r for r in timeless if r["credits"] == 0]
        skip_rate = len(skipped_free) / len(timeless) if timeless else 0.0

        latencies = [r["latency_ms"] for r in usable]
        credits = sum(r["credits"] for r in usable)
        cache_hits = sum(1 for r in usable if r["from_claim_cache"])

        unverifiable = sum(1 for r in usable if r["actual"] == "unverifiable")

        summary = {
            "mode": mode,
            "label": label,
            "n": len(results),
            "usable": len(usable),
            "errors": len(errors),
            "wall_seconds": round(wall, 1),
            "outdated_detection": overall,
            "per_class": per_class,
            "timeless": {
                "n": len(timeless),
                "skipped_without_credits": len(skipped_free),
                "skip_rate": round(skip_rate, 4),
            },
            "latency_ms": {
                "p50": percentile(latencies, 0.50),
                "p95": percentile(latencies, 0.95),
                "mean": round(statistics.fmean(latencies), 1) if latencies else 0.0,
                "max": round(max(latencies), 1) if latencies else 0.0,
            },
            "tavily_credits": {
                "total": credits,
                "per_1000_answers": round(credits / len(usable) * 1000, 1) if usable else 0.0,
            },
            "claim_cache_hits": cache_hits,
            "unverifiable": unverifiable,
            "degraded_responses": sum(1 for r in usable if r["degraded"]),
        }

        print(
            f"  outdated: P={overall['precision']:.2f} R={overall['recall']:.2f} "
            f"F1={overall['f1']:.2f} | timeless skip={skip_rate:.0%} | "
            f"p50={summary['latency_ms']['p50']}ms p95={summary['latency_ms']['p95']}ms | "
            f"credits={credits} | errors={len(errors)}"
        )

        summary["_results"] = results
        return summary

    # -- replayed traffic ------------------------------------------------

    async def _replay(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """Replay stale claims as cache hits, to count prevented stale hits.

        Each stale claim is submitted as though Bifrost had just served it from
        the cache, which is the situation Chronos exists for. A correct run
        evicts every one it judges outdated.
        """
        print("\n=== replay: stale claims presented as cache hits ===")
        stale = [c for c in self.claims if c["group"] == "stale"]

        prevented = 0
        served = 0
        sem = asyncio.Semaphore(self.args.concurrency)

        async def one(claim: dict, idx: int) -> None:
            nonlocal prevented, served
            async with sem:
                payload = {
                    "answer": claim["claim"],
                    "prompt": f"{claim['subject']} {claim['predicate']}",
                    "mode": "annotate",
                    "request_id": f"replay-{claim['id']}",
                    "cache_hit": True,
                    "cache_id": f"bench-replay-{idx}",
                    "cache_key": "stalebench-replay",
                }
                try:
                    resp = await client.post(f"{self.verifier}/v1/verify", json=payload)
                    resp.raise_for_status()
                    body = resp.json()
                except Exception:  # noqa: BLE001
                    return
                served += 1
                if body.get("verdict") in STALE_VERDICTS:
                    prevented += 1

        await asyncio.gather(*[one(c, i) for i, c in enumerate(stale)])

        rate = prevented / served if served else 0.0
        print(f"  replayed {served} cached answers, {prevented} caught as stale ({rate:.0%})")
        return {
            "replayed_cache_hits": served,
            "stale_hits_prevented": prevented,
            "prevention_rate": round(rate, 4),
        }

    # -- service info ----------------------------------------------------

    async def _health(self, client: httpx.AsyncClient) -> dict[str, Any]:
        try:
            r = await client.get(f"{self.verifier}/health", timeout=10)
            return r.json()
        except Exception as exc:  # noqa: BLE001
            print(f"verifier not reachable at {self.verifier}: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    async def _stats(self, client: httpx.AsyncClient) -> dict[str, Any]:
        try:
            r = await client.get(f"{self.verifier}/v1/stats", timeout=20)
            return r.json()
        except Exception:  # noqa: BLE001
            return {}

    # -- output ----------------------------------------------------------

    def _assemble(
        self,
        health: dict,
        before: dict,
        after: dict,
        per_mode: dict,
        cold: dict,
        warm: dict | None,
        replay: dict | None,
        started: datetime,
        finished: datetime,
    ) -> dict[str, Any]:
        cold_credits = cold["tavily_credits"]["per_1000_answers"]
        warm_credits = warm["tavily_credits"]["per_1000_answers"] if warm else None
        saving = None
        if warm_credits is not None and cold_credits:
            saving = round(1.0 - (warm_credits / cold_credits), 4)

        disagreements = [
            {
                "id": r["id"],
                "group": r["group"],
                "class": r["class"],
                "claim": r["claim"],
                "expected": r["expected"],
                "actual": r["actual"],
                "score": r["score"],
                "label_confidence": r["label_confidence"],
                "label_basis": r["label_basis"],
                "current_value": r["current_value"],
                "source_url": r["source_url"],
            }
            for r in cold["_results"]
            if not r["error"] and _disagrees(r)
        ]

        out = {
            "benchmark": "StaleBench",
            "dataset_version": self.dataset["version"],
            "dataset_date": self.dataset["dataset_date"],
            "run": {
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_s": round((finished - started).total_seconds(), 1),
                "claims": len(self.claims),
                "concurrency": self.args.concurrency,
                "verifier": self.verifier,
            },
            "configuration": {
                "offline_demo": health.get("offline_demo"),
                "tavily_configured": health.get("tavily_configured"),
                "claim_cache_backend": health.get("claim_cache"),
                "evidence_source": "fixtures" if health.get("offline_demo") else "tavily",
                "note": (
                    "Numbers measured with this configuration only. An offline run "
                    "uses recorded evidence, so its precision reflects the fixtures, "
                    "not the live web."
                ),
            },
            "modes": {m: _strip(v) for m, v in per_mode.items()},
            "claim_cache_effect": {
                "cold_credits_per_1000_answers": cold_credits,
                "warm_credits_per_1000_answers": warm_credits,
                "credit_saving_fraction": saving,
                "measured": warm is not None,
                "note": (
                    "Cold is a first pass against an empty claim cache; warm repeats "
                    "the same claims. The saving is the share of Tavily credits the "
                    "claim cache avoided."
                    if warm is not None
                    else "Re-run with --measure-cache to populate this."
                ),
            },
            "replay": replay
            or {"measured": False, "note": "Re-run with --replay to populate this."},
            "service_counters": {"before": before, "after": after},
            "label_disagreements": {
                "count": len(disagreements),
                "note": (
                    "A disagreement on a `current`-group claim may be a stale LABEL "
                    "rather than a system error; check label_confidence and the source."
                ),
                "items": disagreements,
            },
        }
        return out


def _disagrees(r: dict) -> bool:
    expected_stale = r["expected"] == "outdated"
    actual_stale = r["actual"] in STALE_VERDICTS
    return expected_stale != actual_stale


def _strip(summary: dict) -> dict:
    """Drop per-claim detail from the published summary; keep it for --audit."""
    return {k: v for k, v in summary.items() if not k.startswith("_")}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run StaleBench against a Chronos verifier.")
    ap.add_argument("--verifier", default=os.getenv("CHRONOS_VERIFIER", "http://localhost:8000"))
    ap.add_argument("--modes", default="annotate", help="comma-separated modes to measure")
    ap.add_argument("--limit", type=int, default=0, help="run a balanced subset")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--measure-cache", action="store_true", help="second pass to measure the claim cache")
    ap.add_argument("--replay", action="store_true", help="replay stale claims as cache hits")
    ap.add_argument("--audit", action="store_true", help="print every label disagreement")
    ap.add_argument("--offline", action="store_true", help="note the run as offline in results")
    ap.add_argument("--out", default=str(RESULTS))
    args = ap.parse_args()

    bench = Bench(args)
    results = asyncio.run(bench.run())

    out = Path(args.out)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")

    if args.audit:
        items = results["label_disagreements"]["items"]
        print(f"\n=== {len(items)} label disagreements ===")
        for d in items:
            print(f"\n[{d['id']}] {d['group']}/{d['class']}  expected={d['expected']} actual={d['actual']} score={d['score']}")
            print(f"  claim        : {d['claim']}")
            print(f"  label basis  : {d['label_basis']}  (confidence: {d['label_confidence']})")
            print(f"  current value: {d['current_value']}")
            print(f"  source       : {d['source_url']}")

    first = next(iter(results["modes"].values()))
    det = first["outdated_detection"]
    print(
        f"\nheadline  precision={det['precision']:.2f}  recall={det['recall']:.2f}  "
        f"F1={det['f1']:.2f}  timeless_skip={first['timeless']['skip_rate']:.0%}"
    )


if __name__ == "__main__":
    main()
