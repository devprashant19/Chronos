# Development

Four languages in one repository, each with its own toolchain. You do not need all of them:
the plugin and the verifier can be worked on independently, and the whole stack runs from
Docker without installing any of it.

## Setup

```bash
git clone <this repo> chronos && cd chronos
cp .env.example deploy/.env       # optional; everything defaults to the offline demo
cd deploy && docker compose up --build
```

That is enough to change the verifier or the console. For the plugin you need Go, and for
the tests you need the versions in the table.

| Part | Needs | Why that version |
| :--- | :--- | :--- |
| `plugin/` | Go 1.27.3 | The gateway is built with it, and a Go plugin must match the host's toolchain exactly |
| `verifier/` | Python 3.12, `verifier/requirements-dev.txt` | FastAPI and Pydantic v2 |
| `console/` | Node 22, `npm install` | Vite 5 |
| `bench/` | Python 3.12, a running verifier | |
| the stack | Docker Desktop | |

## Layout

```
plugin/      the Bifrost plugin (Go). Thin and fast. Also a standalone module,
             so go test and go vet work without Docker
verifier/    extraction, Tavily verification, scoring, reports, sweeper (Python)
console/     the operator console (Vite + React + TypeScript)
bench/       StaleBench: 120 hand-labelled claims, the runner, and the results
deploy/      docker compose, the multi-stage image, gateway config, mockllm
scripts/     verify-all.sh and check-model.sh
design/      the console design reference, the icon, the original blueprint
docs/        the pages this one belongs to
```

Inside the verifier, the pipeline reads top to bottom:

| File | Owns |
| :--- | :--- |
| `verifier/chronos/main.py` | The eighteen routes. Nothing else |
| `verifier/chronos/extract.py` | Answer to typed claims, by model or by rules |
| `verifier/chronos/rules.py` | The regex extractor and the volatility classifier |
| `verifier/chronos/verify.py` | The orchestrator: cache, search, extract, judge, score |
| `verifier/chronos/tavily_client.py` | The only place that talks to Tavily, and the credit budget |
| `verifier/chronos/pagetext.py` | Cleaning a page and choosing the window the judge sees |
| `verifier/chronos/judge_rules.py` | The rule-based judge, used when no model is available |
| `verifier/chronos/score.py` | The formula, the verdict bands, and the TTL |
| `verifier/chronos/llm.py` | Every model call, routed back through Bifrost |
| `verifier/chronos/pacer.py` | The tokens-per-minute budget |
| `verifier/chronos/cache.py` | The claim cache, Redis or in-process |
| `verifier/chronos/store.py` | Reports, in SQLite |
| `verifier/chronos/sweeper.py` | Background re-verification of stored reports |
| `verifier/chronos/evictor.py` | Asking the gateway to clear a cache entry |

## The suites

| Suite | Command | Needs | What it covers |
| :--- | :--- | :--- | :--- |
| Go | `go test ./...` in `plugin/` | Go 1.27.3 | 50 test functions, 120 cases counting subtests: config precedence, classification, header assembly, the result store's bounds and TTL, eviction, payload shaping, the loop guard |
| Go race | `go test -race ./...` in `plugin/` | the same | The result store is read from one hook and written from another |
| Python | `pytest tests/` in `verifier/` | `requirements-dev.txt` | 134 tests: scoring, extraction, the rule judge, the claim cache and API, the pacer, page text |
| Console | `npm test` in `console/` | Node 22 | 102 tests: the data layer, the state hook, what each section renders, the animated hero, the walkthrough, and fidelity against the design reference |
| Console types | `npm run typecheck` in `console/` | Node 22 | The app, then the tests under their own config |
| End to end | `./scripts/verify-all.sh` | Docker | 44 checks: the suites above, then the stack |
| Benchmark | `python bench/runner.py` | a running verifier | Quality against StaleBench. Not a pass/fail suite |

`./scripts/verify-all.sh --unit` runs the test suites without bringing the stack up, and
`--no-build` reuses the existing images, which is what you want while iterating.

The two console checks run with your local toolchain rather than in a container, because a
container would mean an `npm install` on every run. In a fresh clone they **skip**, with
`run 'npm install' in console/ first`; everything else still runs. That is deliberate — the
console is the one part a reviewer can leave out without losing the argument.

The end-to-end checks assert specifics rather than the absence of errors: that the gateway
loaded `chronos.so`, that a second identical request is a cache hit, that Chronos set a
six-hour TTL for a `version` claim, that a stale cached answer is caught and evicted and the
next request misses, that the nine headers are CORS-exposed, and that the gateway keeps
answering with the verifier stopped. Results are in the
[README](../README.md#what-is-verified-and-what-is-not).

## Adding the common thing

**A volatility class.** Three places, and they must agree: `defaultTTLByClass()` in
`plugin/config.go`, `ClaimClass` in `verifier/chronos/models.py`, and the policy table in
`verifier/chronos/score.py`. Add a test to `plugin/classify_test.go` and to
`verifier/tests/test_score.py`. The plugin's classifier is rules only — a few regex scans,
no model call — because it runs on the request path before anything else.

**A claim pattern.** `verifier/chronos/rules.py`, with a case in
`verifier/tests/test_extract.py`. Patterns are checked against the answer text and must
produce exact spans; the console highlights by span, so an off-by-one shows up visibly.

**A scoring term.** `verifier/chronos/score.py`, and extend `ScoreBreakdown` in
`models.py` so the new term is reported. A term that is not in the breakdown is a number
nobody can argue with, which defeats the point.

**A route.** `verifier/chronos/main.py`, then [api.md](api.md). The gate
(`check-docs.mjs`) does not know about routes, so this one is on you.

## Rebuilding after a change

| Changed | Do |
| :--- | :--- |
| `verifier/` | `docker compose restart verifier` — the source is mounted |
| `console/` | `docker compose build console && docker compose up -d console`, because the Vite bundle and its two `VITE_` URLs are compiled in |
| `plugin/` | `docker compose build bifrost` — the plugin is compiled inside Bifrost's module, so the gateway image is rebuilt with it |
| `deploy/bifrost.config.json` | `docker compose restart bifrost` |

## Before opening a pull request

1. `./scripts/verify-all.sh` — all 44, from a clean stack.
2. `go vet ./...` in `plugin/`, and `ruff check` in `verifier/`.
3. `npm run typecheck` and `npm test` in `console/` if you touched it.
4. Read [../AGENTS.md](../AGENTS.md). It lists the rules the code cannot enforce — the ones
   that break quietly rather than loudly.
5. No keys, no `.env`, no local paths. `verify-all.sh` checks the first two.
6. Re-run the screenshots if the console changed:
   `node <readme-suite>/scripts/shots.mjs --file docs/screenshots/shots.json`.

[../CONTRIBUTING.md](../CONTRIBUTING.md) has the rest.
