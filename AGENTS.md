# AGENTS.md

For a coding agent about to change Chronos. Read this first; it is short on purpose.

**What this is:** a Go plugin loaded into the Bifrost AI gateway, plus a Python service.
Together they stop the gateway serving answers whose facts have gone out of date, including
answers replayed from its semantic cache. [README.md](README.md) ·
[ARCHITECTURE.md](ARCHITECTURE.md)

---

## Rules that are easy to break by accident

Each of these breaks **quietly**. The system keeps working and only the quality drops, which
is why they are written down rather than enforced.

**1. `x-chronos-internal` is spelled the same in two languages.**
`plugin/main.go` (`hdrInternal`) and `verifier/chronos/llm.py` (`INTERNAL_HEADER`). The
verifier sets it on every model call; the plugin skips any request carrying it. Chronos's own
model calls go *through* Bifrost, so without this marker Chronos verifies its own
verification calls, and each of those makes more calls that are verified in turn. Nothing
fails — it amplifies until the token budget is gone. Measured before the fix: 4,605
verifications and 37 minutes of rate-limit waiting from one 18-claim run.
`plugin/internal_test.go` pins the string; do not relax that test.

**2. `fallbacks` on the wire is a list of strings.**
Bifrost's Go `Fallback` struct has `provider` and `model` fields, which makes
`[{"provider": "groq", "model": "..."}]` the obvious guess, and the OpenAI-compatible route
answers `400 Invalid request payload`. It wants `["groq/openai/gpt-oss-20b"]`. When this is
wrong, every model call 400s, the verifier falls back to its rule engine, and the only
symptom is that quality is worse than it should be. `verifier/chronos/llm.py: _fallbacks()`.

**3. Plugin placement must stay `pre_builtin` / `order: 0`.**
`deploy/bifrost.config.json`. Post-hooks run in **reverse**, so this is the only arrangement
in which Chronos's `PostLLMHook` runs after the built-in semantic cache's — and seeing cache
hits is the entire point of the project. Move it and the tests still pass; the product stops
working.

**4. The gateway must stay dynamically linked.**
Never add `-extldflags '-static'` or the `sqlite_static` tag to `deploy/Dockerfile.bifrost`.
A static binary loads no plugin at all, starts perfectly, and refuses `chronos.so` at
runtime. The build asserts this with `ldd` and fails loudly; keep that assertion.

**5. The plugin compiles inside Bifrost's own module.**
The Dockerfile copies `plugin/` in as `./chronosplugin` and deletes its `go.mod`, `go.sum`
and `_test.go` files, so it builds as a package of Bifrost's `transports` module with the
same toolchain and flags. That is what makes a Go plugin ABI mismatch structurally
impossible. `plugin/` keeps its own module files so `go test` works outside Docker — both are
needed, neither is redundant.

**6. The claim cache key is subject plus predicate, and must not include the stated value.**
`verifier/chronos/cache.py`. Two answers asserting different prices for the same product are
the same question and must share one verified answer. Adding the stated value means a
*wrong* answer never hits the cache, which is exactly backwards.

**7. Absent evidence is `unverifiable`, never `outdated`.**
`verifier/chronos/score.py: verdict_for()`. Only a claim checked *and found wrong* may evict
a cache entry. Collapse the two and a bad search day starts deleting good cache entries.

**8. An answer's score and TTL are the minimum across its claims, never the mean.**
`verifier/chronos/score.py: answer_score()`, `answer_ttl_seconds()`. One outdated sentence
makes the whole answer unsafe; an average hides it behind nine timeless ones.

**9. An unrecognised prompt classifies as `events`, not `timeless`.**
`plugin/classify.go`. `timeless` means a week-long TTL and never looking again, which is the
one mistake that silently serves stale answers forever.

**10. No hook may return an error that fails a request.**
Every failure path — verifier down, slow, malformed, budget spent — passes the response
through with verdict `unverifiable`. `scripts/verify-all.sh` asserts it by stopping the
verifier mid-run. If you add a hook, add its failure path first.

**11. Every scoring term goes into `ScoreBreakdown`.**
`verifier/chronos/models.py`. A score with a term nobody can see is a number nobody can argue
with, and arguing with the number is what the product is for.

**12. `console/src/instrument.css` is lifted from `design/chronos-console.html`, and a test
holds it there.**
`console/src/design.test.ts` compares every `:root` token in the two files. If you change a
colour, change the reference and re-extract; do not edit one side. The slash in the brand
mark went missing for exactly this reason and nothing caught it.

**13. Never commit a key.** `.env` is gitignored and `scripts/verify-all.sh` fails on a
tracked `.env` or a tracked string shaped like `sk-`, `tvly-` or `gsk_`. Do not read a real
`.env` to write documentation; use `.env.example`.

**14. Page text is scanned before any judge sees it, and a flagged page is disqualified after.**
`verifier/chronos/verify.py: _fresh_verdict()` runs every page through
`injection.neutralise`, and once the judge has answered, forces any source with
`injection_flags` to `insufficient`. Both halves matter: the first keeps the instruction out
of the prompt, the second means a judge that obeyed it anyway changes nothing. Move the forcing
before the judge call, or drop it, and every test still passes except the one in
`tests/test_guards.py` written to catch exactly that. Before tightening a pattern, add the
ordinary page it was flagging to `ORDINARY` in that file. A false positive disqualifies real
evidence, and nothing else would notice.

**15. Pruning folds before it deletes.** `verifier/chronos/store.py: _prune_sync()` adds the
rows it is about to delete to `pruned_totals` in the same transaction, and the counters read
both. Delete without folding and `answers_checked` goes backwards on the next sweep.

---

## Processes and ports

| | Port | Container |
| :--- | :--- | :--- |
| Bifrost gateway, with `chronos.so` | 8080 | `chronos-bifrost` |
| Verifier, FastAPI | 8000 | `chronos-verifier` |
| Console, nginx | 5173 | `chronos-console` |
| Redis, db0 vectors and db1 claims | 6379 | `chronos-redis` |
| mockllm, the keyless test double | 8900 | `chronos-mockllm` |

## Source map

| Path | What it is | Warning |
| :--- | :--- | :--- |
| `plugin/main.go` | The four hooks | Context values propagate **downward only**; a verdict set in `PostLLMHook` is invisible to the headers hook. Hence the correlation id and `plugin/state.go` |
| `plugin/classify.go` | Volatility classification | Rules only. It runs on the request path; no model call, no network |
| `plugin/evict.go` | `DELETE /api/cache/clear/{id}` | Needs `X-Bifrost-Setup-Token` or it is 403 |
| `plugin/state.go` | Verdict store between hooks | Bounded at 4096 with a 60 s TTL. Both bounds matter; it is in a long-lived process |
| `verifier/chronos/verify.py` | The orchestrator | Do not add `include_domains` to the Tavily call. It is a hard filter, and it turned a correct `outdated` into `unverifiable` |
| `verifier/chronos/score.py` | The formula | Agreement divides by the sources that **addressed** the claim, not by everything returned |
| `verifier/chronos/pagetext.py` | Page cleaning and the judge's window | The window centres on the **predicate**, not the stated value. Centring on the value misses exactly the claims that are wrong, because the wrong value is not on the page |
| `verifier/chronos/llm.py` | Every model call | Routed through Bifrost on purpose. See rules 1 and 2 |
| `verifier/chronos/pacer.py` | Tokens per minute, per model | The free-tier limit is tokens, not requests |
| `verifier/chronos/injection.py` | Scans evidence for text aimed at the judge | Patterns, not a classifier. See rule 14 |
| `deploy/Dockerfile.bifrost` | The hard part | See rules 4 and 5. The binary must be at `/app/main`; the entrypoint execs that path and ignores `CMD` |
| `deploy/mockllm/jsonmode.py` | Answers Chronos's own prompts with strict JSON | This is what makes the model path testable with no key. It finds sources by the judge prompt's `[n] url:` lines, so change that header and the keyless path quietly judges nothing |

## Data model

A **claim** is one span of an answer that depends on today's date: subject, predicate, stated
value, volatility class, and exact character offsets. The console highlights by those offsets,
so an off-by-one is visible rather than silent.

Seven volatility classes, each fixing a re-check interval and a maximum answer TTL. Four
verdicts plus `pending`. The full shapes are in `verifier/chronos/models.py` and
[docs/api.md](docs/api.md).

## Flows that must keep working

1. miss → hit → **caught and evicted** → miss again. This is the product.
2. A fresh answer adds no measurable latency.
3. The gateway keeps answering with the verifier stopped.
4. A timeless claim costs no Tavily credit.
5. The nine headers reach a browser, which means staying on
   `Access-Control-Expose-Headers`.

All five are checks in `scripts/verify-all.sh`.

## Conventions that differ from the default

- **British spelling** in prose. American in code identifiers where an upstream API uses it.
- **Comments say why, not what.** Several files carry a measured number in a comment because
  the number is the argument; do not delete those while tidying.
- `plugin/` is a Go module *and* a package of someone else's module. Both, deliberately.
- Reports and stats are additive: add a field, do not repurpose one. The console and
  `bench/runner.py` both read them.

## Commands

```bash
./scripts/verify-all.sh              # 51 checks, needs Docker
./scripts/verify-all.sh --unit       # the test suites only, no stack
./scripts/verify-all.sh --no-build   # reuse images while iterating
./scripts/check-model.sh --all       # which models your key reaches today
python bench/runner.py --limit 18 --audit
```

Two of the 51 depend on a live Tavily search and are genuinely flaky; see
[docs/troubleshooting.md](docs/troubleshooting.md#verification-and-benchmarking) before
assuming you broke something.

**Do not add attribution or co-author lines to commits.** The author's rule, not mine.
