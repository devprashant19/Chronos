# API

Two HTTP surfaces. The **gateway** on `:8080` is Bifrost's own OpenAI-compatible API, which
Chronos only adds headers to — no new endpoint, no SDK change. The **verifier** on `:8000`
is a FastAPI service with eighteen routes, called by the plugin, by the console and by
`bench/runner.py`. Nothing on either surface requires authentication inside the compose
network; see [security.md](security.md) for what that means and what to do before exposing
either one.

---

## 1. The gateway: response headers

Send a normal chat completion. Chronos reads the answer and writes its verdict into the
response. These nine headers are published on `Access-Control-Expose-Headers`, so a browser
can read them too.

| Header | Example | Meaning |
| :--- | :--- | :--- |
| `x-chronos-verdict` | `outdated` | `current`, `check`, `outdated`, `unverifiable`, or `pending` while an out-of-band check runs |
| `x-chronos-score` | `39` | 0–100, the minimum across the answer's claims |
| `x-chronos-claims` | `1` | Time-sensitive claims found |
| `x-chronos-stale` | `1` | How many of them came back `outdated` |
| `x-chronos-class` | `version` | The prompt's volatility class, decided before the model was called |
| `x-chronos-ttl` | `21600` | Seconds. The cache TTL this class allows |
| `x-chronos-report` | `http://verifier:8000/v1/reports/c42b3a05790b489c` | The full report, with every claim's evidence |
| `x-chronos-latency-ms` | `1524` | How long the check took, when it ran inline |
| `x-chronos-mode` | `annotate` | `observe`, `annotate` or `guard` |

A fresh answer carries `x-chronos-verdict: pending` and no score: it is being verified in a
goroutine and the response did not wait. A **cache hit** carries the full verdict, because a
hit is checked inline — it is the only answer that can be stale, and it cost no provider
latency. See the [README's signature section](../README.md#a-cache-hit-is-the-only-answer-that-can-be-stale).

```bash
curl -i http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'x-bf-cache-key: demo' \
  -d '{"model":"mockllm/chronos-demo",
       "messages":[{"role":"user","content":"What is the latest version of Python?"}]}'
```

Two request headers change behaviour:

| Header | Effect |
| :--- | :--- |
| `x-bf-cache-key` | Bifrost's own: groups requests into one cache namespace. Chronos sets `x-bf-cache-ttl` alongside it from the volatility class |
| `x-chronos-internal` | Marks a request as Chronos's own traffic. The plugin skips it entirely. The verifier sets it on every model call it makes, which is what stops Chronos verifying its own verification calls |

> `x-chronos-internal` is load-bearing, not hygiene. Without it one 18-claim benchmark run
> produced 4,605 verifications and 37 minutes of rate-limit waiting, because each
> verification call was itself verified. `plugin/internal_test.go` pins the string.

---

## 2. The verifier

Base URL `http://localhost:8000`. JSON in, JSON out. Interactive docs at `/docs`, served by
FastAPI.

### Verifying

| Method | Path | What it does |
| :--- | :--- | :--- |
| `POST` | `/v1/verify` | Verify an answer and wait for the report |
| `POST` | `/v1/verify/async` | Accept the answer, return a report id, verify in the background |

Request body — only `answer` is required:

```json
{
  "answer": "The latest stable release of Python is 3.11, which came out in October 2022.",
  "prompt": "What is the latest version of Python?",
  "model": "mockllm/chronos-demo",
  "virtual_key": "sk-bf-team-a",
  "cache_hit": true,
  "cache_id": "610663b3-0fc6-59e3-9486-4cfe2375aa3e",
  "mode": "annotate",
  "threshold": 50
}
```

A real response, trimmed to one claim and one piece of evidence:

```json
{
  "report_id": "ec4b55bda8524ea3",
  "score": 39.2,
  "verdict": "outdated",
  "claims": [
    {
      "text": "The latest stable release of Python is 3.11, which came out in October 2022.",
      "span_start": 0,
      "span_end": 76,
      "subject": "Python",
      "predicate": "latest stable release version",
      "stated_value": "3.11",
      "class": "version",
      "verdict": "outdated",
      "score": 39.2,
      "current_value": "3.14.x",
      "source_url": "https://krishnamohanproductions.com/blog/tech-blog/which-python-version-should-i-use-2026",
      "publish_date": "2026-02-01",
      "breakdown": {
        "agreement": 0.0, "recency": 0.4, "quality": 1.0, "confidence": 0.95,
        "volatility": 0.7, "decay": 0.0043,
        "base": 0.3925, "decay_multiplier": 0.9985, "score": 39.2,
        "supporting": 0, "contradicting": 2, "insufficient": 3,
        "formula": "S = 100*(0.45A + 0.25R + 0.15Q + 0.15C)*(1 - 0.5*V*D)"
      },
      "evidence": [
        {
          "url": "https://www.python.org/downloads/windows",
          "title": "Python Releases for Windows | Python.org",
          "tier": "official",
          "call": "insufficient",
          "quote": ""
        }
      ],
      "from_claim_cache": false,
      "tavily_credits": 5
    }
  ],
  "claim_count": 1,
  "stale_count": 1,
  "ttl_seconds": 21600,
  "tavily_credits": 5,
  "degraded": false,
  "reason": null,
  "latency_ms": 11840,
  "extractor": "groq/openai/gpt-oss-120b",
  "evicted": false
}
```

Three fields are worth knowing about:

- **`breakdown`** is the whole argument for the score. Every term in the formula, plus how
  many sources supported, contradicted, or said nothing either way. A claim with `breakdown:
  null` was never searched — a timeless claim, which costs no credit and scores 100.
- **`extractor`** names what produced the claims: a model id, `rules`, or
  `rules (model unavailable)`. A silent fall back from the model to the rule engine looks
  exactly like a bad model, so it is reported rather than inferred.
- **`degraded`** with a `reason` means the daily Tavily budget is spent. The request still
  succeeds; the verdict is `unverifiable`.

### Reports

| Method | Path | What it does |
| :--- | :--- | :--- |
| `GET` | `/v1/reports/{report_id}` | One report, as above. `404` if unknown |
| `GET` | `/v1/reports` | The most recent reports. `?limit=` (default 50), `?verdict=` to filter |

Reports live in SQLite at `CHRONOS_DB_PATH`, so a verdict outlives the process that made it.
The `x-chronos-report` header is a link into this route.

### Watching

| Method | Path | What it does |
| :--- | :--- | :--- |
| `GET` | `/v1/stream` | Server-sent events: one per verification, as it finishes |
| `GET` | `/v1/cache/events` | The recent evictions, newest first |
| `GET` | `/v1/stats` | Counters, budgets, pacing, thresholds and which models are in use |
| `GET` | `/metrics` | The same counters in Prometheus text format |
| `GET` | `/health` | Liveness, plus whether a Tavily key and a claim-cache backend are present |

`/v1/stats` is the one to read when something looks wrong. A real response, trimmed:

```json
{
  "answers_checked": 4654,
  "claims_checked": 892,
  "claims_skipped": 3122,
  "stale_claims": 306,
  "cache_evictions": 15,
  "stale_hits_prevented": 15,
  "tavily_credits_used": 2303,
  "verify_p50_ms": 3671.0,
  "tavily": { "limit": 400, "used": 0, "remaining": 400, "configured": true, "live": true },
  "evictor": { "cleared": 1, "failed": 0 },
  "model_pacing": {
    "tokens_per_minute": 8000, "enabled": true, "waits": 0, "wait_seconds": 0.0,
    "used_by_model": { "groq/openai/gpt-oss-120b": 2324 }
  },
  "mode": {
    "offline_demo": false,
    "extract_model": "groq/openai/gpt-oss-120b",
    "judge_model": "groq/openai/gpt-oss-20b"
  },
  "thresholds": { "current": 80.0, "check": 50.0 }
}
```

`model_pacing.waits` climbing means the verifier is deliberately holding calls back to stay
inside a token budget, not that it is stuck. `evictor.failed` climbing usually means the
gateway's setup token is wrong; see [troubleshooting.md](troubleshooting.md).

### Policies and the briefing

| Method | Path | What it does |
| :--- | :--- | :--- |
| `GET` | `/v1/policies` | Every per-virtual-key policy |
| `GET` | `/v1/policies/{virtual_key}` | One policy, or the defaults if none is set |
| `PUT` | `/v1/policies/{virtual_key}` | Set mode, guard threshold, skipped classes, daily credits, whether to evict |
| `POST` | `/v1/sweep` | Run the background re-verification sweep now instead of waiting |
| `POST` | `/v1/brief` | A short written summary of what went stale, for the console's briefing panel |

A policy lets one key run in `observe` while another runs in `guard`:

```bash
curl -X PUT http://localhost:8000/v1/policies/sk-bf-team-a \
  -H 'Content-Type: application/json' \
  -d '{"virtual_key":"sk-bf-team-a","mode":"guard","guard_threshold":60,
       "skip_classes":["timeless","events"],"daily_tavily_credits":200,"evict_stale":true}'
```

---

## 3. The mock provider

`deploy/mockllm` is an OpenAI-compatible test double on `:8900`, registered with Bifrost as
a custom provider. It exists so the whole stack runs with no keys at all.

| Method | Path |
| :--- | :--- |
| `GET` | `/health` |
| `GET` | `/v1/models` |
| `POST` | `/v1/chat/completions` |
| `POST` | `/v1/embeddings` |

It serves fixed answers from `deploy/mockllm/fixtures/answers.json`, several deliberately out
of date, each recording *why* it is stale. It also answers Chronos's own extraction and judge
prompts with strict JSON (`deploy/mockllm/jsonmode.py`), which is what lets the model path be
exercised with no key: the JSON contract, the Pydantic validation, the span repair and the
per-source judging all run. Only the quality of the judgement needs a real model.

---

## Errors

| Status | When | What the caller should do |
| :--- | :--- | :--- |
| `422` | The body failed Pydantic validation — usually an empty `answer` | Fix the body |
| `404` | No such report or policy | Nothing; the id is wrong or the report has been pruned |
| `200` with `degraded: true` | The daily Tavily budget is spent | Nothing. The verdict is `unverifiable`, which never evicts |
| `200` with `verdict: unverifiable` | No usable evidence was found | Treat as "not checked", never as "wrong" |

The verifier does not return `5xx` for a verification it could not complete. That is
deliberate, and it is the same rule the plugin follows: **no failure in Chronos may fail a
request through the gateway.** `scripts/verify-all.sh` asserts it by stopping the verifier
mid-run and requiring the gateway to keep answering.
