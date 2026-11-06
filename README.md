# Chronos

**A freshness firewall for the Bifrost AI gateway.**

Semantic caching is the cheapest win in an LLM stack and the easiest way to
serve a confidently wrong answer. A cache keyed on *what the user asked* has no
idea that the answer it stored contains a stock price, a model's per-token
price, or the name of a chief executive — facts with a shelf life measured in
minutes, weeks, and "until someone resigns".

Chronos sits inside the gateway. For every answer, fresh or replayed from
cache, it pulls out the claims that depend on today's date, checks them against
the live web with Tavily, scores each one 0–100, reports the result in response
headers, sets the cache TTL from how fast the answer's facts actually move, and
**deletes the cache entry behind any answer it finds has gone stale**.

```
request ─▶ Bifrost ─▶ semantic cache ─▶ provider
              │            │
              │            └── hit? the answer may be months old
              ▼
           Chronos  ─▶ claims ─▶ Tavily ─▶ score ─▶ x-chronos-* headers
                                                 └─▶ DELETE /api/cache/clear/{id}
```

Full diagram and the reasoning behind each decision: [docs/architecture.md](docs/architecture.md).

---

## What actually runs, and what it produced

This is a working system, not a mock-up. Everything below was measured on this
machine; `scripts/verify-all.sh` reproduces it.

A pricing/version question asked three times through the gateway:

| # | Bifrost cache | Chronos verdict | What happened |
|---|---|---|---|
| 1 | miss | `pending`, class `version`, `x-chronos-ttl: 21600` | verified asynchronously — no added latency on the expensive path |
| 2 | **hit** | `outdated`, score **36**, 1 stale claim | caught inline, and the entry **evicted** |
| 3 | **miss again** | `pending` | the stale answer is gone; the next caller gets a fresh one |

Real headers from request 2:

```
x-chronos-verdict: outdated
x-chronos-score: 36
x-chronos-claims: 1
x-chronos-stale: 1
x-chronos-class: version
x-chronos-ttl: 21600
x-chronos-report: http://verifier:8000/v1/reports/657e6eeb813c4388
x-chronos-latency-ms: 28
```

And the gateway's own log, from the plugin:

```
stale_hit_prevented cache_id=b0a16f57-9e39-5040-b087-56a8be8744f1
  cache_key=verify-1793982948 score=36 stale_claims=1 report=657e6eeb813c4388
```

---

## Quick start

```bash
git clone <this repo> && cd Chronos
cp .env.example .env         # works with no keys at all; see "Offline demo"
cd deploy && docker compose up --build
```

Then:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'x-bf-cache-key: demo' \
  -d '{"model":"mockllm/chronos-demo",
       "messages":[{"role":"user","content":"What is the latest version of Python?"}]}' -i
```

| Service | URL |
|---|---|
| Bifrost gateway (OpenAI-compatible) | <http://localhost:8080/v1/chat/completions> |
| Chronos verifier | <http://localhost:8000/health> |
| Console | <http://localhost:5173> |
| Prometheus metrics | <http://localhost:8000/metrics> |

The first build takes several minutes: it downloads Bifrost's source and
compiles the gateway *and* the plugin together (see
[How the plugin is built](#how-the-plugin-is-built)).

---

## How Bifrost is used

Chronos is a **Bifrost plugin**, not a proxy in front of one. It uses the
gateway's own extension points, and it would not work as anything else.

| Bifrost feature | What Chronos does with it |
|---|---|
| `HTTPTransportPreHook` | Classifies the prompt's volatility with rules only — a few regex scans, no model call — and sets `x-bf-cache-ttl` from the result. |
| `PreLLMHook` | Sets the semantic cache's `CacheTTLKey` context value (a `time.Duration`), which is what the cache actually reads. |
| `PostLLMHook` | Receives the answer *after* the cache has served it, so cached answers are checked too. Reads the cache outcome from `schemas.CacheLookupFromContext`. |
| `HTTPTransportResponseHeadersHook` | Writes the `x-chronos-*` headers just before headers commit, so **streaming** clients see the verdict too. |
| Plugin placement | `placement: "pre_builtin"`, `order: 0`. Post-hooks run in reverse, so Chronos's `PostLLMHook` runs *after* the built-in cache's — which is the only way to see a cache hit. |
| Semantic cache | Chronos drives it rather than reimplementing it: per-request TTL in, `cache_debug` out. |
| `DELETE /api/cache/clear/{cache_id}` | The eviction mechanism. Chronos never touches the vector store directly. |
| Virtual keys | The verifier authenticates to Bifrost with its own virtual key, so Chronos's own model spend has its own budget and rate limit. |
| Fallbacks | The verifier's model calls carry a `fallbacks` chain, so a 429 becomes a fallback call rather than an unverifiable answer. |
| Custom providers | `deploy/mockllm` is registered as a custom provider (`base_provider_type: openai`) for the keyless demo. |

**Chronos dogfoods the gateway**: every model call it makes for extraction and
judging goes back out through Bifrost, rather than direct to a provider.

## How Tavily is used

| Tavily feature | What Chronos does with it |
|---|---|
| `search` | One advanced search per claim-cache miss, with `topic: news` for fast-moving classes and `include_domains` set to official domains when the subject is recognised. |
| `extract` | Full page text for the top 3 results, so the judge reads pages rather than search snippets. |
| Credit budget | Every call is metered. When the day's allowance is spent, the verifier degrades to observe mode and says so in `degraded`/`reason` — it never silently overspends or fails a request. |
| Claim cache | A Tavily call only happens on a claim-cache miss. Timeless claims never reach Tavily at all. |

---

## Measured results

From `bench/results.json`, produced by `bench/runner.py` against **live
Tavily** with **no model key**. Nothing here is a target.

| Metric | Measured |
|---|---|
| Timeless skip rate | **100%** — no timeless claim ever cost a Tavily credit |
| `version` class precision | **1.00** |
| `leadership` class | precision 0.40, recall 0.20 |
| Overall `outdated` detection | precision 0.40, recall 0.20 |
| Claim cache effect | p50 **841 ms → 5.4 ms**; 90 → 55 credits on a repeat pass |
| Inline verification on a cache hit (warm) | **28 ms** |
| Plugin added latency, fresh answers | one goroutine spawn — verification is asynchronous |

### Read these numbers honestly

**The overall precision and recall are modest, and the reason is architectural
rather than a tuning problem.** Chronos is designed to extract and judge claims
with a model. This run had a Tavily key but no model key, so the model
extractor and judge never executed and the **rule-based fallbacks** were
measured instead. Deciding who currently holds a role is reading
comprehension; a regex cannot do it, and `leadership` is where the score is
lost. `version` claims, where the comparison is mechanical, score 1.00
precision.

Other caveats that belong on the same page as the numbers:

- **Live search varies between calls.** The same claim can come back `outdated`
  on one run and `check` on the next, because Tavily returns a different result
  set. Single-run figures move by several points.
- `bench/results-offline.json` is a **fixture-coverage check, not a quality
  measure**: offline mode only has recorded evidence for ~5 subjects, so the
  other 35 stale claims correctly come back `unverifiable`.
- The canonical run used a balanced 30-claim subset to stay inside the dev
  key's credit budget. `--limit 0` runs all 120.
- StaleBench's `current` labels decay by construction. `runner.py --audit`
  prints every disagreement with its label confidence and source, because a
  label going stale and the system being wrong look identical in a score. One
  such case is already visible in the audit output: a `current`-labelled CEO
  claim where the evidence pointed at a successor.

Reproduce:

```bash
python bench/runner.py --verifier http://localhost:8000 --measure-cache --replay --audit
```

---

## Scoring

Each claim gets a score and a full breakdown, so no number is unexplained:

```
S = 100 * (0.45·A + 0.25·R + 0.15·Q + 0.15·C) * (1 − 0.5·V·D)
```

| Term | Meaning |
|---|---|
| **A** | agreement: net supporting share **of the sources that addressed the claim** |
| **R** | recency: 1.0 under 7 days, falling linearly to 0 at 365 |
| **Q** | source quality: official 1.0, major news 0.7, other 0.4 |
| **C** | the judge's confidence |
| **V** | volatility weight: market 1.0 … timeless 0.0 |
| **D** | time since last check ÷ the class's re-check interval, capped at 1 |

Verdicts: **80+** `current`, **50–79** `check`, **below 50** `outdated`, and no
usable evidence is `unverifiable` — never `outdated`.

Three decisions in there are load-bearing, and each came from a measured bug:

1. **A divides by the sources that addressed the claim**, not by everything the
   search returned. Dividing by all five results gave one confirming source
   plus four irrelevant ones an A of 0.2 and a score of 44.5 — `outdated` for a
   claim that had just been confirmed and never disputed.
2. **The verdict accounts for direction, not just magnitude.** With A floored
   at 0, R/Q/C alone cap a flatly refuted claim at 55 — inside the `check`
   band. A claim refuted by two official same-week sources is strong evidence
   of *wrongness*, so a contradicted claim is `outdated` whatever S says.
3. **Absent evidence is `unverifiable`.** A claim Chronos could not check is a
   different thing from one it checked and found wrong, and only the latter may
   evict a cache entry. Conflating them deletes good cache entries on a bad
   search day.

## Volatility policy

| Class | Re-check interval | Max answer TTL |
|---|---|---|
| market | 1 h | 15 min |
| pricing | 3 d | 6 h |
| version | 3 d | 6 h |
| leadership | 14 d | 24 h |
| law | 14 d | 24 h |
| events | 7 d | 12 h |
| timeless | never | plugin default |

An answer's TTL is the **minimum** across its claims, and its score is the
minimum too: one outdated sentence makes an answer unsafe to act on, and
averaging would hide it behind nine timeless ones.

An unrecognised prompt is classified `events`, **not** `timeless`. Guessing
`timeless` would hand it a week-long TTL and never look at it again — the one
mistake that silently serves stale answers.

---

## Modes

| Mode | Behaviour | Added latency |
|---|---|---|
| `observe` | Verifies out of band, never touches the response | ~0 |
| `annotate` *(default)* | Adds `x-chronos-*` headers; evicts stale cache entries | ~0 on fresh answers |
| `guard` | Verifies inline within a deadline; below the threshold, regenerates once with the evidence injected, or blocks with HTTP 409 | bounded by `verify_timeout_ms` |

**Cache hits are verified inline even in `annotate`** (`sync_on_cache_hit`,
default on). This is deliberate: a cache hit costs no provider latency and is
the *only* answer that can be stale, so it is the one worth waiting for. Fresh
answers stay fully asynchronous, which is what keeps added latency on the
expensive path at a goroutine spawn. With a warm claim cache that inline check
measured **28 ms**.

### Chronos never breaks the gateway

Every failure path — verifier down, slow, malformed, budget spent — passes the
response through with verdict `unverifiable`. No hook returns an error that
fails a request. `scripts/verify-all.sh` asserts this by stopping the verifier
mid-run and requiring the gateway to keep answering.

---

## How the plugin is built

Go's plugin loader rejects any `.so` whose toolchain, build flags or shared
dependency versions differ from the host binary's. Rather than hoping two
module graphs resolve identically, `deploy/Dockerfile.bifrost` compiles the
plugin **as a package of Bifrost's own `transports` module** — same `go.mod`,
same `go.sum`, same toolchain, same flags, same libc. An ABI mismatch is
structurally impossible.

The gateway is built **without** `-extldflags '-static'` and without the
`sqlite_static` tag, which is what makes it able to `dlopen` a plugin at all;
the build fails loudly if it ever produces a static binary, because that would
start fine and then refuse every plugin at runtime.

Built and verified against:

```
bifrost_ref   transports/v2.4.0
go            1.27.3
core          v1.14.1
framework     v1.11.1
semanticcache v1.7.4
```

`plugin/` is also a standalone Go module, so `go test ./...` and `go vet` work
normally for development and CI.

### Three things the docs do not tell you

Found the hard way; all three are in the code comments too.

1. **Bifrost context values propagate downward only.** A verdict set in
   `PostLLMHook` is *not* visible to `HTTPTransportResponseHeadersHook`, so no
   headers appeared at first. Chronos mints its own correlation id in
   `HTTPTransportPreHook` — a transport hook, so the value reaches both
   `PostLLMHook` and the sibling hook that commits headers — and parks the
   verdict in a bounded, TTL'd store keyed on it.
2. **`ctx.Log` from a `.so` plugin did not reach the gateway's log stream.**
   `stale_hit_prevented` is therefore also written to stderr, where it is
   actually observable.
3. **The management API needs a setup token.** While dashboard auth is
   inactive, every `/api` call must carry `X-Bifrost-Setup-Token` or eviction
   returns 403.

---

## Offline demo

The whole stack runs with **no API keys**, which is how judging and CI work
without credentials:

- `deploy/mockllm` is an OpenAI-compatible test double serving fixed answers,
  several deliberately out of date. Each one records *why* it is stale in
  `deploy/mockllm/fixtures/answers.json`. Nothing there is presented as true.
- With `CHRONOS_OFFLINE_DEMO=1`, evidence comes from
  `verifier/fixtures/evidence.json` instead of Tavily. The **scores are
  computed by the real scoring code**; the **evidence is synthetic**, and each
  fixture is written to assert only that a claim is *out of date*, pointing at
  where the current value lives rather than inventing a replacement figure.
- The console has a **Demo mode** that replays a recorded log.

Credentials unlock the real paths rather than changing them:

| Variable | Effect |
|---|---|
| `TAVILY_API_KEY` | Evidence comes from the live web |
| `OPENAI_API_KEY` (+ `CHRONOS_LIVE_MODELS=1`) | Model-based extraction and judging — the designed path |
| neither | Rule-based extraction and judging over recorded evidence |

`CHRONOS_LIVE_MODELS` is separate from `CHRONOS_OFFLINE_DEMO` on purpose: a
Tavily key and a model key rarely arrive together, and live-evidence +
rule-based reasoning is a genuinely useful middle configuration.

---

## Repository layout

```
plugin/      the Bifrost plugin (Go). Thin and fast; this is the upstream-PR
             candidate — see plugin/README.md, written as a PR description
verifier/    extraction, Tavily verification, scoring, reports, sweeper (Python)
console/     the operator console (Vite + React + TypeScript)
bench/       StaleBench: 120 hand-labelled claims, the runner, and results
deploy/      docker-compose, the multi-stage image, gateway config, mockllm
scripts/     verify-all.sh — every check, end to end
design/      the console design reference and the original blueprint
docs/        architecture.md - the diagram and the design decisions
```

## Verifying

```bash
./scripts/verify-all.sh            # everything: 39 checks
./scripts/verify-all.sh --unit     # unit tests only, no Docker stack
```

All 39 pass on this machine, and the same script is what CI runs.

It asserts specifics rather than absence of errors: that the gateway loaded
`chronos.so`, that a second identical request is a cache hit, that Chronos set
a 6-hour TTL for a `version` claim, that a stale cached answer is caught and
evicted and the next request misses, that metrics and SSE are live, and that
the gateway keeps answering with the verifier stopped.

---

## Limitations

Stated plainly, because a freshness tool that overstates itself is self-refuting.

- **Role claims need a model judge.** The rule-based judge cannot reliably
  decide who currently holds a position; `leadership` precision is 0.40.
- **Live search is non-deterministic.** Verdicts move between runs.
- **The Tavily credit budget is per process and not persisted.** A restart
  resets the day's count. Deliberate — a budget that blocks a fresh deployment
  is worse than one that slightly overspends after a restart at this scale.
- **Streaming answers are not content-verified.** A stream is assembled by the
  client, so there is no complete text to check inside the gateway. Streams
  still get headers, with verdict `pending`.
- **Only the last user turn drives classification.** Long multi-turn
  conversations may be classified from too little context.
- **Bifrost's own web UI is stubbed** in this image to keep the build small;
  the HTTP API is complete.
- **StaleBench's `current` labels decay.** See `--audit`.
- The claim cache keys on subject + predicate, so an ambiguous subject
  ("the CEO") can collide across entities.

## AI tools used

Required disclosure. This project was built with **Claude Code** (Anthropic)
doing the implementation, debugging and measurement, directed by the author.
The Bifrost hook signatures, config schema and semantic-cache contract were
read from the official docs and the `maximhq/bifrost` source rather than
recalled; the benchmark numbers were measured by `bench/runner.py`, not
estimated.

## License

Apache-2.0. See [LICENSE](LICENSE) and [CONTRIBUTING.md](CONTRIBUTING.md).
