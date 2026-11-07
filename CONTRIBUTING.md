# Contributing to Chronos

Thanks for looking. Chronos is Apache-2.0 and contributions are welcome.

## Getting set up

```bash
git clone <repo> chronos && cd chronos
cp .env.example deploy/.env   # optional; it works with no keys at all
cd deploy && docker compose up --build
```

The first build is slow: it downloads Bifrost's source and compiles the gateway
and the plugin together. Afterwards the layers are cached.

```bash
./scripts/verify-all.sh --unit     # fast: tests only, no stack
./scripts/verify-all.sh            # everything, end to end
```

## Working on each piece

### `plugin/` — the Go plugin

```bash
cd plugin
go vet ./... && gofmt -l . && go test -race ./...
go build -buildmode=plugin -o /tmp/chronos.so .
```

It is a standalone module for development, but the image builds it as a package
of Bifrost's own `transports` module so the ABI matches exactly. If you change
the Bifrost version, change `BIFROST_REF` in `deploy/Dockerfile.bifrost` and
rebuild — do not pin `core` by hand in `plugin/go.mod` and expect the `.so` to
load.

**Keep it thin.** The only dependency is `bifrost/core` plus the standard
library. A plugin shares its dependency graph with the gateway, so every import
is a version constraint on everyone who loads it. Model calls, HTTP to third
parties, scoring and storage belong in the verifier.

**Never fail a request.** Every hook has to degrade. If you add a path that can
error, make sure the response still goes out, and add a test that proves it.

### `verifier/` — the Python service

```bash
cd verifier
pip install -r requirements-dev.txt
ruff check chronos/
CHRONOS_OFFLINE_DEMO=1 python -m pytest tests/ -q
```

Tests run offline by design: evidence comes from `verifier/fixtures/`, so the
suite spends no Tavily credits and needs no model key. **Please keep it that
way** — a test suite that costs money per run stops being run.

### `console/` — the operator console

```bash
cd console
npm install
npm run typecheck && npm run dev
```

The stylesheet in `src/instrument.css` is lifted from
`design/chronos-console.html` deliberately, so the built console is the design
reference rather than an approximation. If you restyle, change the design file
and re-extract rather than letting the two drift.

Demo mode must emit the same `FeedEvent` shape the SSE stream does. It is not a
parallel rendering path, and it should not become one: that is what keeps it
honest.

### Working against a free model tier

The defaults target a free Groq key, and that shapes the code in ways worth
knowing before you change them:

- **The limit is tokens per minute, per model.** Not requests. If you add a
  model call, add it to the pacer's accounting (`chronos/pacer.py`) or it will
  cause 429s for everything else.
- **Keep prompts small.** The extraction prompt was cut by more than half
  because its size, not the answer's, dominated the token cost of a call.
- **Send `reasoning_effort=low` for structured output.** A reasoning model's
  chain of thought is charged to the same budget and Chronos never reads it.
  This was worth ~12x in tokens and latency.
- **Do not retry a rate-limited call.** Retrying into a spent minute makes it
  worse. `ModelUnavailable.rate_limited` distinguishes it from a validation
  failure, which *is* worth one retry.
- **Model ids rot.** Read them from the provider, not from documentation.
  `./scripts/check-model.sh --all` is the check; Groq retired the Llama chat
  models that most guides still recommend.

If you have a paid key, set `CHRONOS_MODEL_TPM=0` to switch pacing off — but
please make sure the free path still works before submitting.

### `bench/` — StaleBench

```bash
python bench/build_dataset.py                       # regenerate the dataset
python bench/runner.py --offline                    # free, fixtures only
python bench/runner.py --measure-cache --replay     # live; spends credits
```

The dataset is generated, and CI fails if the committed copy has drifted. Edit
`build_dataset.py`, not `stalebench.json`.

## House rules

**No invented numbers.** This is a project about stale facts; it cannot ship
figures about itself that nobody measured. Every number in the README and the
console comes from a results file in `bench/`, written by the runner. If you have not
measured something, say so, or say nothing. The console's benchmark panel shows
"no results file" rather than a placeholder for exactly this reason.

**Report the configuration with the number.** A precision figure measured with
rule-based judging means something different from one measured with a model.
The runner records the configuration alongside the results; keep it accurate.

**Absence is not evidence.** A claim Chronos could not check is `unverifiable`,
never `outdated`. Only a checked-and-refuted claim may evict a cache entry.
Conflating the two deletes good cache entries on a bad search day, and several
tests pin this.

**Silence must not look like approval.** The worst failure in a staleness
detector is a claim it never extracted: it scores 100 and reports `current`.
When you touch extraction, check what happens to a sentence your patterns do
*not* match.

**Write the test as the bug.** Most tests here are named after a real defect —
insufficient sources diluting the agreement term, verdicts stored under one
spelling and read under another, a percentage falling through to the version
comparison. Following that style makes regressions legible.

**Comment the why, not the what.** Several decisions in this codebase look
arbitrary and are not: the `pre_builtin` placement, the correlation id, the
agreement denominator, the two-source corroboration rule. If you change one,
update the comment explaining why it was that way.

## Secrets

Never commit a key. `.env` is gitignored, CI greps for credential-shaped
strings, and so does `scripts/verify-all.sh`. Use `.env.example` for
placeholders.

## Submitting

1. `./scripts/verify-all.sh` passes.
2. `ruff check chronos/`, `gofmt -l .` and `npx tsc --noEmit` are clean.
3. The commit message says what changed **and why** — if a number moved,
   include the before and after.
4. If you changed behaviour the README describes, update the README in the same
   commit.
