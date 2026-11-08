#!/usr/bin/env python3
"""Run every Chronos feature against the live stack and record what happened.

    docker compose -f deploy/docker-compose.yml up -d
    python scripts/record-proof.py            # writes console/public/proof.json

The console's "Every feature, working" section replays the file this writes,
so the hosted demo shows real outputs from a real run rather than a mock-up.
Each feature records the requests it made, the values that came back, and
whether they were what the feature promises. A failure is written down as a
failure, never dropped: the page shows it in red.

Standard library only, so it runs on the host with no virtualenv. Takes a few
minutes and spends roughly 30 Tavily credits, mostly on the briefing. It stops
the verifier container once, for the degrade check, and starts it again.

Never reads deploy/.env. Nothing it records contains a key: the verifier and
gateway never put one in a response.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "console" / "public" / "proof.json"
GATEWAY = "http://localhost:8080"
VERIFIER = "http://localhost:8000"
MODEL = "mockllm/chronos-demo"
STALE_PROMPT = "What is the latest version of Python?"
STALE_ANSWER = "The latest stable release of Python is 3.11."

FEATURES: list[dict] = []


def log(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)


def http(method: str, url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 120):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, raw
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


def jget(path: str):
    return json.loads(http("GET", f"{VERIFIER}{path}", timeout=30)[2])


def chat(prompt: str, headers: dict | None = None):
    return http("POST", f"{GATEWAY}/v1/chat/completions",
                {"model": MODEL, "messages": [{"role": "user", "content": prompt}]}, headers)


def chronos_headers(h: dict) -> dict:
    return {k: v for k, v in sorted(h.items()) if k.startswith(("x-chronos-", "x-bifrost-cache"))}


def feature(fid: str, title: str, section: str, promise: str, run) -> None:
    """Run one feature, record its evidence, and never let a failure stop the rest."""
    log(f"{fid}: {title}")
    started = time.time()
    entry = {"id": fid, "title": title, "section": section, "promise": promise}
    try:
        status, evidence, note = run()
        entry.update(status=status, evidence=evidence, note=note)
    except Exception as exc:  # recorded, not raised: the page shows the failure
        entry.update(status="fail", evidence=[], note=f"{type(exc).__name__}: {exc}"[:400])
    entry["seconds"] = round(time.time() - started, 1)
    FEATURES.append(entry)
    log(f"   -> {entry['status']}")


def ev(label: str, value) -> dict:
    return {"label": label, "value": value if isinstance(value, str) else json.dumps(value)}


# ---------------------------------------------------------------------------
# the cycle, through the gateway
# ---------------------------------------------------------------------------

CYCLE: dict = {}


def cycle():
    key = f"proof-{int(time.time())}"
    hs = []
    for n in range(3):
        _, h, _ = chat(STALE_PROMPT, {"x-bf-cache-key": key})
        hs.append(chronos_headers(h))
        if n < 2:
            time.sleep(9)
    CYCLE.update(h1=hs[0], h2=hs[1], h3=hs[2])
    return hs


def f_miss():
    h1 = CYCLE["h1"]
    ok = h1.get("x-bifrost-cache-hit") in (None, "false") and h1.get("x-chronos-verdict") == "pending"
    return ("pass" if ok else "fail",
            [ev("request 1 · " + k, v) for k, v in h1.items()],
            "A fresh answer is verified in the background, so it is never delayed: its verdict says pending.")


def f_hit():
    h2 = CYCLE["h2"]
    ok = h2.get("x-bifrost-cache-hit") == "true" and bool(h2.get("x-bifrost-cache-id"))
    return ("pass" if ok else "fail",
            [ev("request 2 · x-bifrost-cache-hit", h2.get("x-bifrost-cache-hit", "")),
             ev("request 2 · x-bifrost-cache-id", h2.get("x-bifrost-cache-id", ""))],
            "The same question again is answered from Bifrost's semantic cache.")


def f_caught():
    h2 = CYCLE["h2"]
    ok = h2.get("x-chronos-verdict") == "outdated"
    return ("pass" if ok else "fail",
            [ev("request 2 · x-chronos-verdict", h2.get("x-chronos-verdict", "")),
             ev("request 2 · x-chronos-score", h2.get("x-chronos-score", "")),
             ev("request 2 · x-chronos-stale", h2.get("x-chronos-stale", "")),
             ev("request 2 · x-chronos-latency-ms", h2.get("x-chronos-latency-ms", ""))],
            "The cache hit is checked inline against live Tavily evidence, and the verdict rides back on the response."
            + ("" if ok else " Live search varies: a search that returns nothing usable makes this unverifiable."))


def f_evicted():
    h3 = CYCLE["h3"]
    cache_id = CYCLE["h2"].get("x-bifrost-cache-id", "")
    events = jget("/v1/cache/events").get("events", [])
    matching = [e for e in events if e.get("cache_id") == cache_id]
    ok = h3.get("x-bifrost-cache-hit") in (None, "false") and bool(matching)
    return ("pass" if ok else "fail",
            [ev("request 3 · x-bifrost-cache-hit", h3.get("x-bifrost-cache-hit", "false")),
             ev("cache event", {k: matching[0].get(k) for k in ("kind", "cache_id", "score")} if matching else "none")],
            "The stale entry is deleted through Bifrost's own DELETE /api/cache/clear/{id}, so the next caller gets a fresh answer.")


def f_ttl():
    h1 = CYCLE["h1"]
    ok = h1.get("x-chronos-class") == "version" and h1.get("x-chronos-ttl") == "21600"
    return ("pass" if ok else "fail",
            [ev("x-chronos-class", h1.get("x-chronos-class", "")), ev("x-chronos-ttl", h1.get("x-chronos-ttl", ""))],
            "A version question is cached for six hours, not a flat default. A stock price would get fifteen minutes.")


def f_breakdown():
    # The header is a link (http://verifier:8000/v1/reports/<id>), not a bare id.
    report_id = CYCLE["h2"].get("x-chronos-report", "").rstrip("/").rsplit("/", 1)[-1]
    report = jget(f"/v1/reports/{report_id}")
    claim = (report.get("claims") or [{}])[0]
    b = claim.get("breakdown") or {}
    ok = all(k in b for k in ("agreement", "recency", "quality", "confidence", "volatility", "decay", "score"))
    sources = [{"host": e["url"].split("/")[2], "call": e["call"], "tier": e["tier"]} for e in claim.get("evidence", [])[:4]]
    return ("pass" if ok else "fail",
            [ev("report", report_id), ev("claim", claim.get("text", "")),
             ev("stated → current", f"{claim.get('stated_value')} → {claim.get('current_value')}"),
             ev("breakdown", {k: b.get(k) for k in ("agreement", "recency", "quality", "confidence", "volatility", "decay", "score")}),
             ev("sources", sources)],
            "Every score shows its working: each term of the formula, and the sources behind it.")


# ---------------------------------------------------------------------------
# the verifier, directly
# ---------------------------------------------------------------------------

def f_timeless():
    _, _, raw = http("POST", f"{VERIFIER}/v1/verify", {"answer": "The capital of France is Paris.", "mode": "annotate"})
    d = json.loads(raw)
    ok = d["tavily_credits"] == 0 and d["verdict"] == "current"
    return ("pass" if ok else "fail",
            [ev("verdict", d["verdict"]), ev("tavily_credits", d["tavily_credits"]), ev("claims skipped", d["skipped"])],
            "A fact that never changes is skipped before any search: it costs nothing.")


def f_claim_cache():
    first = json.loads(http("POST", f"{VERIFIER}/v1/verify", {"answer": STALE_ANSWER})[2])
    second = json.loads(http("POST", f"{VERIFIER}/v1/verify", {"answer": STALE_ANSWER})[2])
    c2 = (second.get("claims") or [{}])[0]
    ok = c2.get("from_claim_cache") is True and second["tavily_credits"] == 0
    return ("pass" if ok else "fail",
            [ev("first check · credits", first["tavily_credits"]), ev("second check · from_claim_cache", c2.get("from_claim_cache")),
             ev("second check · credits", second["tavily_credits"]), ev("extractor", second.get("extractor", ""))],
            "A claim already verified is re-scored from the claim cache, not searched again.")


def f_cors():
    _, h, _ = chat("hello", {"Origin": "http://localhost:5173", "x-bf-cache-key": f"proof-cors-{int(time.time())}"})
    exposed = h.get("access-control-expose-headers", "")
    ok = "x-chronos-verdict" in exposed.lower()
    return ("pass" if ok else "fail", [ev("access-control-expose-headers", exposed)],
            "The verdict is readable from a browser, which is how this console reads it.")


def f_internal():
    _, h, _ = chat("What is the latest version of Python?", {"x-chronos-internal": "proof", "x-bf-cache-key": f"proof-int-{int(time.time())}"})
    present = [k for k in h if k.startswith("x-chronos-")]
    ok = not present
    return ("pass" if ok else "fail", [ev("x-chronos-* headers on a marked request", present or "none")],
            "Chronos's own model calls go through the gateway too, and are skipped, so it never verifies its own verification.")


def f_policy():
    policy = {"virtual_key": "vk-proof", "mode": "guard", "guard_threshold": 65, "skip_classes": [],
              "daily_tavily_credits": 100, "evict_stale": True}
    http("PUT", f"{VERIFIER}/v1/policies/vk-proof", policy)
    got = jget("/v1/policies/vk-proof")
    ok = got.get("mode") == "guard" and got.get("guard_threshold") == 65
    return ("pass" if ok else "fail", [ev("PUT then GET /v1/policies/vk-proof", {k: got.get(k) for k in ("mode", "guard_threshold", "evict_stale")})],
            "Each virtual key can have its own mode and threshold, changed at runtime.")


def f_sweeper():
    d = json.loads(http("POST", f"{VERIFIER}/v1/sweep", timeout=300)[2])
    ok = d.get("ok") is True
    return ("pass" if ok else "fail", [ev("POST /v1/sweep", d)],
            "The sweeper re-checks verdicts that are about to expire and applies retention, every ten minutes.")


def f_guard_mode():
    _, _, raw = http("POST", f"{VERIFIER}/v1/verify",
                     {"answer": STALE_ANSWER, "mode": "guard", "regenerate": True, "threshold": 50}, timeout=180)
    d = json.loads(raw)
    acted = bool(d.get("corrected_answer")) or d.get("blocked") is True
    ok = d["verdict"] == "outdated" and acted
    return ("pass" if ok else "fail",
            [ev("verdict", d["verdict"]), ev("score", d["score"]),
             ev("corrected_answer", d.get("corrected_answer") or "—"), ev("blocked", d.get("blocked"))],
            "In guard mode an outdated answer is rewritten with the evidence, or blocked with a 409. Measured here at "
            "the verifier; the gateway half (substitute or 409) is held by plugin/guard_test.go, because the demo "
            "gateway runs in annotate mode.")


def f_observability():
    stats = jget("/v1/stats")
    metrics = http("GET", f"{VERIFIER}/metrics", timeout=30)[2].decode()
    names = ["chronos_claims_total", "chronos_cache_evictions_total", "chronos_tavily_credits_total",
             "chronos_injection_flagged_sources_total", "chronos_claim_cache_bypassed_total"]
    missing = [n for n in names if n not in metrics]
    ok = not missing and "model_pacing" in stats and "guards" in stats
    return ("pass" if ok else "fail",
            [ev("/metrics", [n for n in names if n in metrics]),
             ev("model_pacing", {k: stats["model_pacing"].get(k) for k in ("tokens_per_minute", "waits")}),
             ev("guards", stats.get("guards")),
             ev("claim_cache", {k: stats["claim_cache"].get(k) for k in ("backend", "hits", "misses")})],
            "Every counter is in /v1/stats and in Prometheus format, including the pacing that keeps a free tier inside its limit.")


def f_retention_live():
    s = jget("/v1/stats")
    ok = s["answers_checked"] == s["reports_stored"] + s["reports_pruned"]
    return ("pass" if ok else "fail",
            [ev("retention_days", s["sweeper"].get("retention_days")), ev("answers_checked", s["answers_checked"]),
             ev("reports_stored + reports_pruned", f"{s['reports_stored']} + {s['reports_pruned']}")],
            "Old reports are deleted after the window, and the lifetime counters never go backwards.")


def f_briefing():
    _, _, raw = http("POST", f"{VERIFIER}/v1/brief", {"subject": "Bifrost (Maxim AI)", "meeting_time": "Thu 12 Nov 2026 10:30 IST", "days": 30}, timeout=420)
    d = json.loads(raw)
    ok = bool(d.get("verified") or d.get("unconfirmed")) and "open_questions" in d
    return ("pass" if ok else "fail",
            [ev("verified", len(d.get("verified", []))), ev("unconfirmed", len(d.get("unconfirmed", []))),
             ev("open questions", len(d.get("open_questions", []))),
             ev("first verified line", (d.get("verified") or [{}])[0].get("text", "—")),
             ev("tavily_credits", d.get("tavily_credits"))],
            "A subject and a meeting time become a one-page brief, every fact Chronos-scored, with what it could not confirm said out loud.")


# ---------------------------------------------------------------------------
# inside the verifier container: the guards against the real judge model
# ---------------------------------------------------------------------------

INSIDE: dict = {}


def inside():
    script = (ROOT / "scripts" / "proof" / "in_verifier.py").read_text(encoding="utf-8")
    out = subprocess.run(["docker", "exec", "-i", "chronos-verifier", "python", "-"], input=script,
                         capture_output=True, text=True, timeout=900, check=True)
    INSIDE.update(json.loads(out.stdout.strip().splitlines()[-1]))


def f_injection():
    on, off = INSIDE["injection_with_guard"], INSIDE["injection_without_guard"]
    ok = on["source_call"] == "insufficient" and on["injection_flags"] and on["verdict"] != "current"
    resisted = off["source_call"] != "supports"
    return ("pass" if ok else "fail",
            [ev("judge", on["judge_model"]),
             ev("with the guard · flags", on["injection_flags"]), ev("with the guard · source call", on["source_call"]),
             ev("with the guard · verdict", on["verdict"]),
             ev("without the guard · source call", off["source_call"]), ev("without the guard · verdict", off["verdict"])],
            "A page that tries to instruct the judge has the instruction removed and is set aside as evidence. "
            + ("Run against the real judge model with a fixed attack page. Without the guard this model resisted the "
               "attack on its own on this run; the guard means the result no longer depends on that."
               if resisted else
               "Run against the real judge model with a fixed attack page. Without the guard this model obeyed the "
               "attack and called the page supports; with it, the page was set aside."))


def f_ambiguous():
    a = INSIDE["ambiguous"]
    vague, named = a["the company"], a["Alphabet"]
    ok = vague["searches_for_two_checks"] == 2 and not vague["second_from_claim_cache"] and named["second_from_claim_cache"]
    return ("pass" if ok else "fail",
            [ev("'the company' · searches for two checks", vague["searches_for_two_checks"]),
             ev("'the company' · reason", vague["bypass_reason"]),
             ev("'Alphabet' · searches for two checks", named["searches_for_two_checks"]),
             ev("'Alphabet' · second from claim cache", named["second_from_claim_cache"])],
            "A subject that names nothing in particular is never cached, so one company's verdict is never served for another's. Real Tavily, real judge.")


def f_retention_mechanism():
    r = INSIDE["retention"]
    ok = r["deleted"] and r["kept"] and r["answers_checked_before"] == r["answers_checked_after"]
    return ("pass" if ok else "fail",
            [ev("pruned", r["pruned"]), ev("answers_checked before → after", f"{r['answers_checked_before']} → {r['answers_checked_after']}"),
             ev("stale_hits_prevented before → after", f"{r['stale_hits_prevented_before']} → {r['stale_hits_prevented_after']}"),
             ev("reports left", r["reports_stored_after"])],
            "Against a throwaway database with the real store: reports past the window are deleted, their counts are kept.")


# ---------------------------------------------------------------------------
# the degrade path: last, because it stops the verifier
# ---------------------------------------------------------------------------

def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", "-f", str(ROOT / "deploy" / "docker-compose.yml"), *args],
                   capture_output=True, timeout=180, check=True)


def f_degrade():
    compose("stop", "verifier")
    try:
        time.sleep(2)
        status, h, raw = chat("What is the capital of France?", {"x-bf-cache-key": f"proof-degrade-{int(time.time())}"})
        answered = status == 200 and json.loads(raw)["choices"][0]["message"]["content"]
    finally:
        compose("start", "verifier")
        for _ in range(60):
            try:
                if http("GET", f"{VERIFIER}/health", timeout=3)[0] == 200:
                    break
            except Exception:
                time.sleep(2)
    return ("pass" if answered else "fail",
            [ev("gateway status with the verifier stopped", status), ev("x-chronos-verdict", h.get("x-chronos-verdict", "none"))],
            "Stop the verifier entirely and the gateway keeps answering. No failure of Chronos fails a request.")


def f_bench():
    m = json.loads((ROOT / "bench" / "results-model.json").read_text(encoding="utf-8"))
    a = m["modes"]["annotate"]
    d = a["outdated_detection"]
    return ("measured",
            [ev("source", "bench/results-model.json"),
             ev("claims", f"{a['n']} (all leadership, the hardest class)"),
             ev("outdated · precision / recall / F1", f"{d['precision']} / {d['recall']} / {d['f1']}"),
             ev("timeless skipped without credits", f"{a['timeless']['skipped_without_credits']} of {a['timeless']['n']}"),
             ev("p50 verification", f"{round(a['latency_ms']['p50'] / 1000, 1)} s, paced on a free tier")],
            "Quality is measured against hand-labelled claims, not asserted. A measurement, not a pass or fail; the full table is in the Benchmark section.")


def main() -> None:
    if http("GET", f"{VERIFIER}/health", timeout=10)[0] != 200:
        sys.exit("the verifier is not answering on :8000; start the stack first")
    stats = jget("/v1/stats")

    log("the cycle: three identical requests through the gateway")
    cycle()
    feature("miss", "A fresh answer is never delayed", "#run", "pending on a cache miss", f_miss)
    feature("hit", "The same question again is a cache hit", "#run", "x-bifrost-cache-hit: true", f_hit)
    feature("caught", "The cached answer is checked, and caught", "#run", "x-chronos-verdict: outdated on the hit", f_caught)
    feature("evicted", "The stale entry is deleted", "#cache", "the next request is a miss again", f_evicted)
    feature("ttl", "Volatility sets the cache lifetime", "#cache", "version → 6 h, not a flat TTL", f_ttl)
    feature("breakdown", "Every score shows its working", "#inspect", "every term of the formula, every source", f_breakdown)
    feature("timeless", "A timeless fact costs nothing", "#inspect", "0 Tavily credits", f_timeless)
    feature("claim-cache", "A repeated claim is not searched again", "#cache", "from_claim_cache, 0 credits", f_claim_cache)
    feature("cors", "The verdict reaches a browser", "#playground", "x-chronos-* on Access-Control-Expose-Headers", f_cors)
    feature("internal", "Chronos never checks its own calls", "#path", "no x-chronos-* on a marked request", f_internal)
    feature("policy", "Per-key policy, changed at runtime", "#traffic", "PUT then GET round trips", f_policy)
    feature("guard-mode", "Guard mode rewrites or blocks", "#run", "an outdated answer is corrected or refused", f_guard_mode)
    feature("sweeper", "The sweeper re-checks before expiry", "#cache", "POST /v1/sweep runs a pass", f_sweeper)

    log("the guards, inside the verifier container (about a minute)")
    try:
        inside()
    except Exception as exc:
        INSIDE["error"] = str(exc)
    feature("injection", "A page cannot instruct the judge", "#inspect", "set aside, never counted", f_injection)
    feature("ambiguous", "A vague subject never shares a verdict", "#inspect", "'the company' is never cached", f_ambiguous)
    feature("retention", "Old reports are deleted, counts are kept", "#traffic", "deleted past the window, totals unchanged", f_retention_mechanism)
    feature("retention-live", "Retention on the running stack", "#traffic", "answers = stored + pruned", f_retention_live)
    feature("observability", "Everything is counted", "#traffic", "/v1/stats and /metrics", f_observability)
    feature("briefing", "A meeting brief, every fact checked", "#brief", "verified, unconfirmed, open questions", f_briefing)
    feature("bench", "Measured against 120 labelled claims", "#bench", "precision and recall, not promises", f_bench)
    feature("degrade", "The gateway survives Chronos failing", "#path", "verifier stopped, gateway still answers", f_degrade)

    record = {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "recorded_by": "scripts/record-proof.py",
        "stack": {
            "gateway": GATEWAY, "verifier": VERIFIER, "provider": MODEL,
            "extract_model": stats["mode"].get("extract_model"), "judge_model": stats["mode"].get("judge_model"),
            "evidence": "live Tavily" if stats["tavily"].get("live") else "recorded fixtures",
        },
        "summary": {s: sum(1 for f in FEATURES if f["status"] == s) for s in ("pass", "fail", "measured")},
        "features": FEATURES,
    }
    OUT.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"wrote {OUT.relative_to(ROOT)}: {record['summary']}")
    failed = [f["id"] for f in FEATURES if f["status"] == "fail"]
    if failed:
        log(f"FAILED: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
