# Design decisions

Every decision that shaped Chronos, from the first commit (6 Nov 2026) to now, in one place.
Each entry says what was decided, why, and where it lives so you can check it. The full
argument for most of them is elsewhere — [ARCHITECTURE.md](ARCHITECTURE.md),
[docs/deployment.md](docs/deployment.md), [docs/ui.md](docs/ui.md),
[docs/security.md](docs/security.md) — and this page links there rather than repeating it.

The numbers on this page are what `bench/runner.py` and `scripts/verify-all.sh` measured. None
are targets.

**(from a failure)** marks a decision that was not the first design: something broke, it was
measured, and the decision is what replaced it. Those are the most worth reading, because the
obvious alternative is exactly what was tried first.

---

## Contents

1. [The premise](#1-the-premise)
2. [Where Chronos runs](#2-where-chronos-runs)
3. [The plugin](#3-the-plugin)
4. [Building and loading the plugin](#4-building-and-loading-the-plugin)
5. [The verifier](#5-the-verifier)
6. [Evidence](#6-evidence)
7. [Scoring and verdicts](#7-scoring-and-verdicts)
8. [Volatility and TTLs](#8-volatility-and-ttls)
9. [Models, and living on a free tier](#9-models-and-living-on-a-free-tier)
10. [Failure and degradation](#10-failure-and-degradation)
11. [Security posture](#11-security-posture)
12. [The console](#12-the-console)
13. [Hosting the demo](#13-hosting-the-demo)
14. [Measuring it](#14-measuring-it)
15. [Testing and CI](#15-testing-and-ci)
16. [Documentation and presentation](#16-documentation-and-presentation)
17. [Repository conventions](#17-repository-conventions)
18. [Decided against](#18-decided-against)
19. [Open decisions](#19-open-decisions)

---

## 1. The premise

| # | Decision | Why |
| :-- | :--- | :--- |
| 1.1 | **The cache hit is the risky request, so it is the one that waits.** Fresh answers are verified in the background; cache hits are verified inline, within a deadline | A miss has just been written by the model and is as fresh as it can be. A hit cost nothing and was written at some point in the past. The budget for waiting is exactly the provider latency the hit just saved. [ARCHITECTURE.md §1.1](ARCHITECTURE.md#11-the-cache-hit-is-the-risky-request-so-it-is-the-one-that-waits) |
| 1.2 | **Annotate and evict by default; do not block.** The caller who hit the stale answer still receives it, with the verdict in its headers. The entry is then deleted so the *next* caller gets a fresh one. Blocking is `guard` mode, which is off by default | A freshness check that can fail a request becomes a new source of outages. `guard` exists for callers who want to pay for blocking |
| 1.3 | **Not knowing is a third answer.** `unverifiable` is its own verdict, separate from `outdated`, and only `outdated` may delete anything | Treat "couldn't check" as "wrong" and a bad search day starts deleting good cache entries, which is worse than the problem being solved |
| 1.4 | **The unit is a time-dependent claim, not a whole answer.** A claim records its subject, predicate, stated value, volatility class and exact character offsets | Most of an answer is timeless text around a few facts that decay, and only those are worth a search. The offsets let the console highlight exactly the span being judged, so an off-by-one shows on screen instead of hiding |

## 2. Where Chronos runs

| # | Decision | Why |
| :-- | :--- | :--- |
| 2.1 | **A Bifrost plugin, not a proxy in front of the gateway** | Only a plugin sees the semantic cache's outcome, and only a plugin can set the cache's TTL per request. A proxy in front would see every answer as fresh |
| 2.2 | **Two processes: a thin Go plugin and a Python verifier** | The plugin must be Go because it runs inside Bifrost. The verifier must not be: extraction, search and judging are slow, I/O-bound work that is much faster to iterate on in Python. The cost is a network hop, so every failure path has to be designed (§10) |
| 2.3 | **The plugin stays thin.** It makes no model calls, sends no third-party HTTP, stores nothing and computes no score. It depends on nothing but `bifrost/core` and the standard library | A plugin shares the gateway's dependency graph, so every import becomes a version constraint on Bifrost. [plugin/README.md](plugin/README.md#design-constraints) |
| 2.4 | **Chronos's own model calls go back out through Bifrost** | They get their own virtual key, budget and rate limit, the gateway's fallbacks, and its logs, so Chronos's spend is visible next to the traffic it checks. The cost was a self-amplifying loop (3.5) |
| 2.5 | **Eviction goes through the gateway's management API** (`DELETE /api/cache/clear/{id}`), never the vector store | Eviction stays correct if Bifrost changes how it stores the cache |
| 2.6 | **Five containers in one Compose file:** gateway, verifier, console, Redis and a mock provider | One command brings it all up, the same way everywhere Docker runs. Only Windows with Docker Desktop has been exercised |

## 3. The plugin

| # | Decision | Why |
| :-- | :--- | :--- |
| 3.1 | **Four hooks, each with one job.** `HTTPTransportPreHook` classifies the prompt and sets the TTL. `PreLLMHook` sets the cache's TTL context value. `PostLLMHook` sees the answer after the cache has served it. `HTTPTransportResponseHeadersHook` writes the verdict headers | Each hook is the earliest or latest point at which its job can be done. Headers are written just before they commit, so streaming clients see the verdict too |
| 3.2 | **Placement `pre_builtin`, order `0`** in `deploy/bifrost.config.json` | Post-hooks run in reverse order, so this is the only arrangement in which Chronos's `PostLLMHook` runs after the built-in cache's. Move it and every test still passes while the product stops working |
| 3.3 | **(from a failure)** **The transport pre-hook mints a correlation id, and the verdict is parked in `plugin/state.go`**, which holds at most 4096 entries with a 60 s TTL | Bifrost context values propagate downward only. A verdict set in `PostLLMHook` was invisible to the headers hook, so at first no headers appeared at all. Both limits matter, because the store lives in a long-running process |
| 3.4 | **(from a failure)** **`stale_hit_prevented` is written to stderr as well as `ctx.Log`, and eviction sends `X-Bifrost-Setup-Token`** | `ctx.Log` from a `.so` never reached the gateway's log stream, and without the token, eviction returned 403. Neither is in the docs; both are commented at the site |
| 3.5 | **(from a failure)** **`x-chronos-internal` marks Chronos's own traffic, and the plugin skips any request carrying it.** The name is hard-coded in Go and in Python, and `plugin/internal_test.go` pins it | Without the marker, Chronos verified its own verification calls, and each of those made more calls. One 18-claim run produced **4,605 verifications and 37 minutes of rate-limit waiting** before anyone noticed |
| 3.6 | **The nine `x-chronos-*` headers are appended to `Access-Control-Expose-Headers`**, keeping the gateway's own entries | Without that, a browser — and so the console — cannot read the verdict |
| 3.7 | **Classification uses rules only** (`plugin/classify.go`) | It sits in front of every request, so a few regex scans, no model call and no network |
| 3.8 | **The background verification queue is capped.** When it is full, a report is dropped instead of spawning another goroutine | Memory has to stay bounded inside someone else's process |
| 3.9 | **`Init` does not fail on a recoverable misconfiguration** | A gateway that refuses to boot over a misspelled setting is strictly worse than one that boots degraded and says so |
| 3.10 | **(from a failure)** **Guard mode acts on any `outdated` answer, not only one scoring under the threshold.** `guardShouldAct` in the plugin, `fails_guard` in the verifier | A refuted claim is `outdated` whatever S says (7.4), so an answer can be `outdated` at 50.4 with a threshold of 50. Keyed on the score alone, guard mode served exactly that answer unchanged. Found by the first tests guard mode ever had, written on 8 Nov after a README line was found to claim tests that did not exist |

## 4. Building and loading the plugin

| # | Decision | Why |
| :-- | :--- | :--- |
| 4.1 | **The plugin is compiled inside Bifrost's own `transports` module.** The Dockerfile copies `plugin/` in as `./chronosplugin` and deletes its `go.mod`, `go.sum` and tests | Go rejects a `.so` built with any different toolchain, flags or dependency version, and says so only at runtime, after a clean start. Sharing one module makes a mismatch impossible rather than unlikely. [docs/deployment.md](docs/deployment.md#why-the-image-is-built-this-way) |
| 4.2 | **`plugin/` is also a standalone module** | So `go test` and `go vet` work outside Docker. Both module setups are needed, and neither is redundant |
| 4.3 | **The gateway is dynamically linked, and the build fails if `ldd` reports a static binary** | A static gateway starts perfectly and refuses every plugin afterwards. That failure gives no error, so the build checks for it |
| 4.4 | **Bifrost is pinned to `transports/v2.4.0`**, and the vendored tarball is checked with `gzip -t` | A truncated tarball otherwise fails much later with a confusing error. The image records its exact versions in `BUILDINFO` |
| 4.5 | **Bifrost's web UI is stubbed out of the image** | It keeps the build small. Chronos uses only the HTTP API, which is complete |
| 4.6 | **The binary lives at `/app/main`** | Bifrost's entrypoint execs that path and ignores `CMD`. Any other name gives a container that starts and immediately exits |

## 5. The verifier

| # | Decision | Why |
| :-- | :--- | :--- |
| 5.1 | **FastAPI with eighteen routes and no logic in them.** `llm.py` is the only file that calls a model and `tavily_client.py` the only one that calls Tavily, and each owns its own budget | Each budget is enforced in exactly one place |
| 5.2 | **Verdicts are cached on subject plus predicate, never on the stated value** (`verifier/chronos/cache.py`) | Two answers giving different prices for one product are the same question. Include the stated value and a *wrong* answer never hits the cache, which is exactly backwards. Measured: p50 **841 ms → 5.4 ms**, and credits on a repeat pass **90 → 55** |
| 5.3 | **A cached verdict is re-scored, not replayed.** The evidence is reused and decay is recomputed against now | Replaying a stored score would reintroduce staleness one level up: a stale staleness check |
| 5.4 | **Redis db1 holds the claim cache, and SQLite holds reports, policies and cache events.** Redis db0 remains Bifrost's | The two caches cannot collide, and reports survive restarts in a volume |
| 5.5 | **A sweeper re-verifies stored reports every ten minutes** | An answer cached for six hours should not be trusted for six hours on the strength of one check |
| 5.6 | **Mode can be set per virtual key at runtime** (`PUT /v1/policies/{key}`) | Bifrost has already resolved the key, so the policy reuses that identity instead of inventing one |
| 5.7 | **Every report names its extractor**, model or rules | Two wire-format bugs (9.5) dropped the verifier onto its rule engine without any error. Reporting the extractor made that visible |
| 5.8 | **The briefing states what it could not confirm instead of leaving it out** | Leaving it out would present an incomplete brief as a complete one |
| 5.9 | **A subject that names nothing in particular bypasses the claim cache** (`cache.ambiguous_subject`): a bare pronoun, or nothing but determiners and generic nouns ("the CEO", "the latest version"). The claim is still verified, every time, and the report says why in `claim_cache_bypassed` | Keyed on subject plus predicate, "the CEO" would serve one company's verified leader for another company's claim. The rule is deliberately narrow: "the CEO of Apple" and "the Bank of England" each carry a word that names something and still use the cache. None of the 95 subjects in StaleBench and the fixtures trips it |
| 5.10 | **Reports and cache events are pruned after `CHRONOS_REPORT_RETENTION_DAYS`, 30 by default**, at startup and on every sweep. What is deleted is first folded into a one-row totals table, in the same transaction | Reports hold third-party page text, so keeping them forever should be chosen, not inherited. Folding the counts in first means the lifetime counters in `/v1/stats` never go backwards, which keeps stats additive. Only the latency percentiles narrow to the window |
| 5.11 | **Retention has a floor of two days** | The sweeper finds the cached answers to evict through their reports, and an answer can stay cached for 24 hours. A shorter window could leave a stale answer the sweeper can no longer find |

## 6. Evidence

| # | Decision | Why |
| :-- | :--- | :--- |
| 6.1 | **One advanced Tavily search per claim-cache miss**, with `topic: news` for fast-moving classes, **then a full-page `extract` of the top three results** | The judge reads whole pages, not search snippets |
| 6.2 | **(from a failure)** **The judge's window is centred on the claim's predicate, not its stated value** (`verifier/chronos/pagetext.py`) | Centring on the value misses exactly the claims that are wrong, because the wrong value is not on the page. Judged against Google's newsroom, "Larry Page is the CEO of Google" found no "Larry Page", so the window fell back to the top of the page and the judge called a page that answered the question "insufficient". Until this was found, it cost the model path all of its recall |
| 6.3 | **(from a failure)** **`include_domains` is not used** | It is a hard filter, not a preference, and it turned a correct `outdated` into `unverifiable` |
| 6.4 | **Every Tavily credit is metered against a daily budget**, 400 by default. When it is spent, the verifier drops to observe mode and reports `degraded` with a reason | It never overspends silently and never fails a request |
| 6.5 | **Timeless claims never reach Tavily**: they are skipped before the cache lookup | Measured skip rate: **100%**. A check in `verify-all.sh` asserts that credits are unchanged |
| 6.6 | **Source quality comes from the URL**: official 1.0, major news 0.7, anything else 0.4 | A page cannot claim to be official |
| 6.7 | **Recorded evidence fixtures stand in when there is no Tavily key** | They only assert that a claim is *out of date* and say where the current value lives. They never invent a replacement figure |
| 6.8 | **Every page is scanned for injection before any judge sees it** (`verifier/chronos/injection.py`). Instruction-like sentences are replaced with a fixed marker, and a page that tried is **disqualified**: after judging, its call is forced to `insufficient` and its quote cleared | A page that tries to instruct the judge is not evidence, whatever else it says. Forcing the call *after* judging means even a judge that obeyed it changes nothing, and an attacker can only remove their own page. Patterns, not a classifier: each is held to ordinary pages it must not flag, such as a fact check ("rated the claim as true"), a contact page ("please call our office"), a law ("will override the previous rules") and API docs ("the system message") |
| 6.9 | **Each source's text is fenced between `<<<SOURCE n TEXT>>>` markers, and anything shaped like them is stripped from pages.** The `[n] url:` header lines are unchanged | A page cannot close its own block and open a fake source. The headers stay as they were because `deploy/mockllm` parses them to answer the judge without a key |

## 7. Scoring and verdicts

```
S = 100 * (0.45·A + 0.25·R + 0.15·Q + 0.15·C) * (1 − 0.5·V·D)
```

| # | Decision | Why |
| :-- | :--- | :--- |
| 7.1 | **The score is pure functions, and every term is reported in `ScoreBreakdown`** (`score.py`, `models.py`) | A score with a term nobody can see is a number nobody can argue with, and arguing with the number is what the product is for |
| 7.2 | **Bands: 80 and above is `current`, 50–79 is `check`, under 50 is `outdated`.** No usable evidence is `unverifiable`, never `outdated` | See 1.3 |
| 7.3 | **(from a failure)** **Agreement divides by the sources that addressed the claim**, not by every source returned | Dividing by all five gave one confirming source and four irrelevant ones A = 0.2 and a score of 44.5. That is `outdated`, for a claim just confirmed and never disputed |
| 7.4 | **(from a failure)** **A contradicted claim is `outdated` whatever S says** | With A floored at 0, R, Q and C alone still give a flatly refuted claim up to 55, inside `check`. The verdict has to account for which way the evidence points, not only how strong it is |
| 7.5 | **An answer's score and TTL are the minimum across its claims, never the mean** | One outdated sentence makes the whole answer unsafe, and an average hides it behind nine timeless ones. The cost: one stock price pulls a stable answer down to fifteen minutes, which is the conservative direction and the correct one |
| 7.6 | **The judge returns a call and a confidence, not a score** | Agreement, recency, tier and decay are computed from facts about the sources, so a page's text cannot set its own score (11.4) |

## 8. Volatility and TTLs

| # | Decision | Why |
| :-- | :--- | :--- |
| 8.1 | **Seven classes, each with a re-check interval and a maximum answer TTL** (see the table below) | One flat TTL fits no fact: a stock price and a capital city should not share a cache lifetime |
| 8.2 | **An unrecognised prompt is classified `events`, not `timeless`** | `timeless` means a week-long TTL and never looking again, which is the one mistake that serves stale answers forever without anyone noticing |
| 8.3 | **The TTL is set two ways: the `x-bf-cache-ttl` header and a `time.Duration` context value** | The context value is what the semantic cache actually reads |
| 8.4 | **Only the last user turn drives classification** | It is cheap enough for the request path. The known cost is that a long conversation may be classified from too little context |

The classes:

| Class | Re-check interval | Max answer TTL |
| :--- | :--- | :--- |
| market | 1 h | 15 min |
| pricing, version | 3 d | 6 h |
| leadership, law | 14 d | 24 h |
| events | 7 d | 12 h |
| timeless | never | 7 d (`168h` in `plugin/config.go`) |

## 9. Models, and living on a free tier

| # | Decision | Why |
| :-- | :--- | :--- |
| 9.1 | **Open-weights models served free by Groq.** `gpt-oss-120b` extracts, `gpt-oss-20b` judges, `qwen3.8-27b` is the fallback and `ollama/llama3.1` the last resort | Groq is the only provider still offering a real allowance with no credit card. The ids were queried live: the Llama models most guides recommend now return 404. `scripts/check-model.sh --all` prints what works today |
| 9.2 | **(from a failure)** **Calls send `reasoning_effort: low`**, and retry once without it if a model rejects it | `gpt-oss` counts its chain of thought against the token budget even though Chronos never reads it. One extraction went from **~2,400 tokens and 12–24 s to 192 tokens and 1.6 s**, with no loss of quality on a fixed-schema task |
| 9.3 | **(from a failure)** **Chronos paces itself to tokens per minute, per model** (`verifier/chronos/pacer.py`) | The free limit is 8,000 tokens a minute, alongside 1,000 requests a day. Before pacing, a 30-claim run produced 42 refusals and 40 deadline misses, and a 429 costs the round trip and returns nothing. Pacing is shown in `/v1/stats` |
| 9.4 | **Extraction and judging use different models** | Groq meters the token budget per model, so splitting them doubles the headroom |
| 9.5 | **(from a failure)** **`fallbacks` is sent as a list of strings** (`["groq/openai/gpt-oss-20b"]`) | The Go struct suggests objects, and the OpenAI-compatible route answers objects with a 400. Every model call failed, and the only symptom was worse quality. `verify-all.sh` now checks the format |
| 9.6 | **Optional parameters such as `response_format` are dropped for models that reject them** | They are not portable between models, and the failure is just as quiet as 9.5 |
| 9.7 | **A rule engine handles extraction and judging when no model is available** | The stack works with no key at all. Mechanical claims such as versions and prices score well on rules; role claims do not, and that gap is the argument for the model path |
| 9.8 | **A mock provider, `deploy/mockllm`**, registered as a custom OpenAI-type provider. It serves fixed answers, several deliberately stale, and strict JSON for Chronos's own prompts | The whole cycle, model path included, can be tested without a key. Each stale fixture records *why* it is stale, and none is presented as true |

## 10. Failure and degradation

| # | Decision | Why |
| :-- | :--- | :--- |
| 10.1 | **No hook may return an error that fails a request.** Every failure passes the response through with verdict `unverifiable` | Chronos must never be the reason the gateway is down. `verify-all.sh` stops the verifier mid-run and requires the gateway to keep answering |
| 10.2 | **Every failure degrades towards `unverifiable`, never towards `outdated`** | The same asymmetry as 1.3 |
| 10.3 | **Each failure has one defined outcome.** Without Redis, the claim cache becomes a bounded in-process dict. Nonsense from extraction is retried once with the error attached, then becomes `unverifiable`. An eviction error is logged and the answer is unaffected | [ARCHITECTURE.md §6](ARCHITECTURE.md#6-degradation-security-and-observability) |
| 10.4 | **The Tavily budget is held per process and not persisted** | A budget that blocks a fresh deployment is worse than one that slightly overspends after a restart, at this scale |

## 11. Security posture

| # | Decision | Why |
| :-- | :--- | :--- |
| 11.1 | **Keys never reach a log, a report, the console or the repository.** `.env` is gitignored, and `verify-all.sh` fails if `.env` is tracked or a tracked string looks like a `sk-`, `tvly-` or `gsk_` key | It is a public repository |
| 11.2 | **What is not protected is written down, not hidden.** The verifier is unauthenticated, with CORS `*`. Inference is unauthenticated, the SSRF guard is off for the providers, and the dev tokens sit in `.env.example` | It is a single-machine demo. [docs/security.md](docs/security.md) lists exactly what to change before it goes on a network |
| 11.3 | **Eviction needs a token** | It is the one destructive action that crosses the process boundary |
| 11.4 | **The judge is the injection surface, and the design limits it:** the scan and disqualification of 6.8, the fences of 6.9, a fixed Pydantic schema, an 800-character window, at most four sources, a URL-derived tier, and a score the judge does not set | The judged text comes from pages a search engine chose for a claim an attacker may have written. `tests/test_guards.py` drives the real orchestrator with a judge that obeys every instruction it reads. Nothing has been run against a real model with real attack pages yet |
| 11.5 | **No security policy file** | It is a single-author hackathon project with no security contact. The docs say so rather than imply a process that does not exist |

## 12. The console

| # | Decision | Why |
| :-- | :--- | :--- |
| 12.1 | **Vite, React and TypeScript, served by nginx.** One page of nine sections, each a *reader*: `useChronos.ts` owns mode, feed, counters and connection, and no section fetches anything except on a click | State has one owner, and the sections are easy to test |
| 12.2 | **Demo mode is the default, and the page labels it as a recorded replay.** The console also falls back to it whenever the verifier cannot be reached | Judging and screenshots have to work without the stack or keys. A freshness tool that faked its own freshness would refute itself, so nothing in demo mode is presented as live |
| 12.3 | **(from a failure)** **The mode lock is held in a ref** | A late health probe overruled a choice the user had already made |
| 12.4 | **`instrument.css` is lifted from `design/chronos-console.html`, and `design.test.ts` holds every `:root` token to it** | The slash in the brand mark once went missing and nothing caught it. Change the reference and re-extract; never edit one side alone |
| 12.5 | **Colour means ownership.** Blue is Bifrost, magenta is Tavily, ink is Chronos, and none is used for anything else. A test keeps the brand violet distinct from Bifrost's blue | Every box shows who does that step. A brand colour must never read as "Bifrost owns this" |
| 12.6 | **The palette moved to violet on 8 Nov, and the reference changed in the same commit** | So the token test still means something |
| 12.7 | **Type.** Big Shoulders Display for headings, Instrument Sans for body, **Instrument Serif italic** for the headline's second line, IBM Plex Mono for anything that is a value | The serif line turns a slogan into a sentence someone wrote, and mono marks what is data |
| 12.8 | **Two drawings of the mark.** The header has the struck-through dial; the favicon has a dial with its last quarter filled in | Below about 32 px the slash fills the mark in. The second drawing still reads at 16 px |
| 12.9 | **The opening is a full-bleed stage that is always dark**, made by pointing the palette tokens at the reference's own dark values | No new colours, and everything nested in it renders dark for free |
| 12.10 | **The globe is SVG and CSS transforms, not WebGL.** Each meridian is an ellipse animated on `scaleX` from 1 to −1, each with its own offset | The page already ships 222 kB of JavaScript. A test pins the distinct offsets, because collapsing them makes the sphere pulse instead of turn |
| 12.11 | **Every movement has a job.** Counters count up from real stats, the decay scope sweeps a week because that curve *is* the argument, and packets cross the signal path while a request is in flight. All of it is off under `prefers-reduced-motion` | Motion that only decorates is noise. With motion off, the page renders complete rather than hidden and waiting |
| 12.12 | **(from a failure)** **The count-up reads `performance.now()`, not the timestamp `requestAnimationFrame` passes in** | The two need not share an origin. When they did not, the most prominent number on the page stopped short of its target. A flaky test caught it |
| 12.13 | **"Run it" is a five-step cycle driven by one button, and it never dead-ends.** If a live request fails, a labelled recording stands in and the button still advances | It shows the product's whole point in order. A failure of ours must not stop the person in front of it, the same rule as 10.1 |
| 12.14 | **The walkthrough has ten steps, and each scrolls to a real section and rings it.** It is a draggable card, clamped to the screen | A tour that shows something the product cannot do is a lie with a Next button. It began as a bar across the foot of the page, which covered what step one pointed at |
| 12.15 | **The scroll reveal uses `IntersectionObserver`, not a scroll handler** | A long page costs no per-frame layout reads |
| 12.16 | **(from a failure)** **The console does not probe the verifier when the page is https and the verifier URL is http** (`api.blockedByBrowser`) | Mixed content and Chromium's private-network rule refuse the request anyway, and the hosted demo logged a CORS error on every load |
| 12.17 | **(from a failure)** **Labels are read from the response, not assumed.** The playground's hit or miss comes from `x-bifrost-cache-hit`. The inspector labels a recorded report as recorded, even in Live mode | Screenshots showed a live miss titled "cache hit", and a recording sitting unlabelled under a LIVE header |
| 12.18 | **The score drawer shows both guards.** A disqualified source is marked "set aside", with the patterns that fired; a claim kept out of the claim cache is marked "claim cache bypassed · ambiguous subject" | A guard that changes a verdict without showing it is a term nobody can see (7.1). Both fields are optional in the console's types, so older reports and the recorded demo still render |
| 12.19 | **An "Every feature" section replays a recorded run of every feature against the live stack.** `scripts/record-proof.py` writes `console/public/proof.json` with the real headers, report fields and counters, and whether each matched what the feature promises. In Live mode the checks a browser can make are re-run in place | A demo of features should show the features working, not describe them. Recording a real run keeps the hosted demo honest without a backend, and a failure is shown in red rather than left out. The checks that need to stop a container or run inside the verifier stay recorded and say so |

## 13. Hosting the demo

| # | Decision | Why |
| :-- | :--- | :--- |
| 13.1 | **The console builds into a self-contained static bundle.** `scripts/build-demo-site.sh` uses relative paths and copies in `bench/results.json` | It works on any host, or straight off a USB stick, and the benchmark panel shows the real measured numbers |
| 13.2 | **The hosted demo is on Vercel** (<https://chronos-sable-one.vercel.app/>), imported as a single project with `console` as its root | That is the route that was actually tested. `console/public/results.json` is committed and identical to `bench/results.json`, so no build script is needed |
| 13.3 | **(from a failure)** **No `vercel.json`** | One was added, then removed. For a repository shaped like this one, Vercel's multi-application detection ignored it, so it declared a build that never ran |
| 13.4 | **Only the console is hosted; the gateway and verifier are not deployed anywhere public** | Hosting them would put unauthenticated routes (11.2) on the open internet, spending a free Tavily and Groq allowance for whoever finds the URL. The recorded demo shows the cycle honestly, and the real stack runs locally with one command |
| 13.5 | **A GitHub Pages workflow builds the bundle on every push and repeats the safety checks itself:** no absolute paths, results present, nothing shaped like a credential. If Pages is not enabled, the log says so and the run still passes | It is the job that publishes files to the open internet, so it must not depend on someone having run the full suite. Pages has to be switched on once by hand |

## 14. Measuring it

| # | Decision | Why |
| :-- | :--- | :--- |
| 14.1 | **StaleBench: 120 hand-labelled claims, 40 per group**, with a generator and a runner that measures detection | Quality is measured, not asserted |
| 14.2 | **Only measured numbers appear anywhere, and each says which configuration produced it** | Rule engine only: `outdated` precision 0.40, recall 0.20, `version` precision 1.00. With a model, on 18 leadership claims: precision **0.80**, recall **0.667**, F1 **0.727** |
| 14.3 | **The 18-claim figure is labelled as one class, and the hardest one, not a cross-class average** | The dataset is ordered leadership-first, so a balanced subset is entirely leadership |
| 14.4 | **The one false positive is counted against the system, not excused.** "Tim Cook is the CEO of Apple" was labelled `current` and reported `outdated`, with evidence pointing at a successor | A label going out of date is a real failure mode, and `--audit` exists to surface it |
| 14.5 | **Runs use subsets** | To stay inside a free key's daily budget. `--limit 0` runs all 120 and costs about 400 Tavily credits |

## 15. Testing and CI

| # | Decision | Why |
| :-- | :--- | :--- |
| 15.1 | **One script, `scripts/verify-all.sh`, proves the whole stack: 52 checks, the same ones CI runs.** It asserts specifics — `x-chronos-ttl: 21600`, credits unchanged, eviction recorded — rather than the absence of errors | Each rule in [AGENTS.md](AGENTS.md) breaks *quietly*, so the checks look for the specific symptom |
| 15.2 | **The plugin runs `go test -race`** | The race detector matters because one hook writes the verdict store and another reads it |
| 15.3 | **The verifier has pytest: 185 tests, none needing a network** | Scoring, extraction, the rule judge, the claim cache and the pacer are pure logic |
| 15.4 | **The console has Vitest under jsdom: 125 tests** | They cover the data layer, the state hook, what each section renders, and token fidelity to the design reference |
| 15.5 | **Two of the 52 checks depend on live web search and are flaky.** They are documented as flaky, not stubbed | Recorded evidence would steady them, but would stop them testing the live path |
| 15.6 | **(from a failure)** **Screenshots are regenerated live from a shot list, and a person looks at them** (`docs/screenshots/shots.json`) | jsdom reports what components produce, not what a browser paints. On 8 Nov, looking at screenshots found five faults that every test had passed. The three a component test can see now have one, and each was checked to fail without its fix |
| 15.7 | **A documentation gate runs in CI** | Broken links and diagrams that no longer parse fail the build rather than the reader |
| 15.8 | **The guards are also measured against the real judge model**, inside the verifier container, by `scripts/proof/in_verifier.py`. A fixed attack page stands in for search, and the run is made twice, with the guard and without it | It shows what the guard changes instead of asserting it. On the first run the model resisted the attack even without the guard. That is recorded as it happened; what the guard adds is that the result no longer depends on the model resisting |

## 16. Documentation and presentation

| # | Decision | Why |
| :-- | :--- | :--- |
| 16.1 | **The README is built around what was measured, and "what is verified, and what is not" is a full section** listing nine things not yet verified that should be | A reviewer can tell evidence from aspiration |
| 16.2 | **The architecture diagram is rendered from HTML kept in the repository.** Edit the HTML, never the PNG | It can be diffed and regenerated |
| 16.3 | **The plugin's README is written as a pull-request description** | That is the form an upstream Bifrost maintainer would read it in |
| 16.4 | **[AGENTS.md](AGENTS.md) holds the rules the code cannot state** | They break silently, so whoever touches the code, agent or person, reads them first |
| 16.5 | **[TESTING.md](TESTING.md) is an evaluation guide in levels**, from opening the hosted demo to reproducing the numbers | Not every reviewer will run Docker |
| 16.6 | **The README discloses the AI tools used** | The hackathon requires it. Bifrost's hook signatures and cache contract were read from its docs and source, not recalled from memory |
| 16.7 | **A story-and-demo film is in progress.** Its thesis is *"Your cache remembers. Chronos checks."*, told by a cheerful Chatterbox narrator, with a second voice for the cache. Its working folder is gitignored | It is source material, not part of the product |

## 17. Repository conventions

- **British spelling in prose.** Code identifiers use American spelling where an upstream API
  does.
- **Comments say why, not what.** Several carry a measured number because the number is the
  argument; keep them when tidying.
- **Reports and stats are additive.** Add a field rather than repurpose one: the console and
  `bench/runner.py` both read them.
- **One concern per commit**, with the message prefixed by its area: `plugin:`, `verifier:`,
  `console:`, `deploy:`, `docs:`.
- **No attribution or co-author lines in commits.** This is the author's rule.
- **Apache-2.0.**

## 18. Decided against

| Alternative | Why not |
| :--- | :--- |
| A proxy in front of Bifrost | It cannot see the cache's outcome (2.1) |
| Keying the claim cache on the stated value | Wrong answers would never hit it (5.2) |
| Averaging claim scores into an answer score | It hides the one outdated sentence (7.5) |
| Treating missing evidence as `outdated` | A bad search day would delete good entries (1.3) |
| Defaulting unknown prompts to `timeless` | It serves stale answers forever (8.2) |
| `include_domains` on Tavily searches | It is a hard filter that dropped correct verdicts (6.3) |
| A static gateway binary | It loads no plugins (4.3) |
| Two separate Go module graphs | They risk an ABI mismatch that only shows at runtime (4.1) |
| Calling providers directly from the verifier | Chronos's spend would bypass the gateway's budget and logs (2.4) |
| WebGL for the opening scene | Too heavy for a decorative background (12.10) |
| `vercel.json` | Vercel's detection ignored it for this repository (13.3) |
| Deploying the backend publicly | It would put unauthenticated routes online, spending free allowances (13.4) |
| Persisting the Tavily budget | It would block a fresh deployment (10.4) |
| Pruning reports without keeping their totals | Lifetime counters would go backwards, and stats are additive (5.10) |
| Trusting a flagged page if the judge still found it useful | A judge that obeyed the injection would say exactly that (6.8) |
| A feature page written by hand | It would describe the features instead of showing them working (12.19) |

## 19. Open decisions

These are not decided yet. They are written down so they are not decided by accident.

1. **Whether to add a model-based injection classifier** behind the patterns of 6.8, and the
   red-team pages to measure it against. The patterns stop the obvious attacks and nothing
   paraphrased.
2. **Whether to run a second gateway in `guard` for the end-to-end check.** Both halves are
   tested and the verifier's rewrite has run live (3.10), but the gateway's substitute-or-409
   path has only run in Go tests.
3. **How to tell two entities with the same name apart in the claim cache.** 5.9 catches a
   subject that names nothing; it cannot catch one that names two things.
4. **A browser-level test suite**, for what jsdom cannot see (15.6).
5. **Recorded evidence for the two flaky checks**, trading the live path for stability (15.5).
6. **A full 120-claim run with a model**, for a cross-class figure (14.3).
