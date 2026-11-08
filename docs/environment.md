# Environment

Chronos's own code reads **45** environment variables across four languages: Go in the
plugin, Python in the verifier, Vite in the console, Python again in the benchmark runner.
Four more are read by Bifrost itself from `deploy/bifrost.config.json`. Every one of them has
a working default, and **the whole stack starts with none of them set** — that is what makes
`docker compose up` work with no credentials.

`.env.example` carries the dozen you are likely to want. This page is the complete list.

## Which files are read, in what order

1. `deploy/.env` — read by `docker compose`, which substitutes into
   `deploy/docker-compose.yml`. This is the file you create.
2. `deploy/docker-compose.yml` — supplies a default for every variable it passes on, with
   `${NAME:-default}`, so an unset variable is not an empty one.
3. The process environment inside each container.
4. For the plugin only, a third layer: `deploy/bifrost.config.json` has a `config` object per
   plugin. The precedence is **defaults, then that JSON object, then `CHRONOS_*` from the
   environment** (`plugin/config.go`). The environment wins, so you can override a
   checked-in config without editing it.

```bash
cp .env.example deploy/.env     # then edit deploy/.env
```

`.env` is in `.gitignore` and `scripts/verify-all.sh` fails if one is ever committed.

---

## Credentials

Nothing here is required. Each one switches a real path on.

| Variable | Default | What it does |
| :--- | :--- | :--- |
| `GROQ_API_KEY` | `gsk-unset-offline-demo` | Free, no card: <https://console.groq.com/keys>. Read by Bifrost, not by Chronos |
| `TAVILY_API_KEY` | empty | Evidence comes from the live web instead of `verifier/fixtures/evidence.json` |
| `OPENAI_API_KEY` | `sk-unset-offline-demo` | Paid. Only used if you configure a model on it |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | A fully local provider. `docker compose --profile local up -d ollama` |
| `BIFROST_ENCRYPTION_KEY` | `chronos-dev-encryption-key-32byte` | Any 32-byte string. Encrypts Bifrost's config store at rest |
| `BIFROST_SETUP_TOKEN` | `chronos-dev-setup-token` | Bifrost requires this on every `/api` call while dashboard auth is inactive. Chronos uses it to evict |

Set a real value for both Bifrost variables before exposing the gateway. See
[security.md](security.md).

## Which model does which job

| Variable | Default | Notes |
| :--- | :--- | :--- |
| `CHRONOS_EXTRACT_MODEL` | `groq/openai/gpt-oss-120b` | One call per answer |
| `CHRONOS_JUDGE_MODEL` | `groq/openai/gpt-oss-20b` | One call per claim-cache miss, so the more frequent of the two |
| `CHRONOS_FALLBACK_MODELS` | `groq/openai/gpt-oss-20b,groq/qwen/qwen3.8-27b,ollama/llama3.1` | Comma-separated `provider/model`. Bifrost tries them in order, including on a 429 |
| `CHRONOS_LIVE_MODELS` | empty, meaning follow `CHRONOS_OFFLINE_DEMO` | `1` forces model extraction and judging, `0` forces the rule engine |

**Extraction and judging deliberately use different models.** Groq meters tokens per minute
*per model*, and its refusal names the model — "Rate limit reached for model
`openai/gpt-oss-120b`" — so two models draw on two budgets instead of competing for one.

**Check the ids before trusting any guide, including this one.** Queried live, Groq serves
`openai/gpt-oss-120b`, `openai/gpt-oss-20b` and `qwen/qwen3.8-27b` for chat. The Llama chat
models most tutorials still recommend return **404**. `./scripts/check-model.sh --all` prints
what your key reaches today.

## Staying inside a free tier

| Variable | Default | What it does |
| :--- | :--- | :--- |
| `CHRONOS_MODEL_TPM` | `8000` | The tokens-per-minute budget the pacer works to. `0` turns pacing off |
| `CHRONOS_REASONING_EFFORT` | `low` | Sent as `reasoning_effort`. Dropped automatically if a model rejects it |
| `CHRONOS_EXTRACT_MAX_TOKENS` | `750` | Also the pacer's reservation for the call |
| `CHRONOS_JUDGE_MAX_TOKENS` | `500` | Same |

The limit that binds on a free Groq key is **tokens per minute, not requests**: the key's own
headers report `x-ratelimit-limit-requests: 1000` against
`x-ratelimit-limit-tokens: 8000`. And `gpt-oss` are reasoning models whose chain of thought
is charged against that budget even though Chronos never reads it. For one extraction call:

| `reasoning_effort` | Tokens | Latency |
| :--- | :--- | :--- |
| default | ~2,400 | 12–24 s |
| `low` | **192** | **1.6 s** |

Filling a fixed JSON schema does not benefit from deliberation, so `low` costs nothing in
quality and turns roughly three calls a minute into forty.

`verifier/chronos/pacer.py` estimates each call's tokens and waits for room rather than
discovering the limit with a 429 — a 429 costs the round trip and returns nothing. Pacing is
visible in `/v1/stats` under `model_pacing`, so the slowdown is explained rather than
mysterious. On a paid tier set `CHRONOS_MODEL_TPM=0`.

## Evidence and budget

| Variable | Default | What it does |
| :--- | :--- | :--- |
| `CHRONOS_TAVILY_DAILY_CREDITS` | `400` | When spent, the verifier degrades to observe mode and says so in `degraded`/`reason` |
| `CHRONOS_TAVILY_MAX_RESULTS` | `5` | Search results per claim |
| `CHRONOS_TAVILY_EXTRACT_TOP_N` | `3` | How many of those get their full page text pulled |
| `CHRONOS_TAVILY_CONCURRENCY` | `3` | Parallel Tavily calls |
| `CHRONOS_JUDGE_SNIPPET_CHARS` | `800` | The size of the page window the judge is shown |
| `CHRONOS_JUDGE_MAX_SOURCES` | `4` | Sources shown to the judge per claim |

The budget is held per process and is not persisted, so a restart resets the day's count.
That is deliberate: a budget that blocks a fresh deployment is worse than one that slightly
overspends after a restart, at this scale.

## Behaviour

| Variable | Default | What it does |
| :--- | :--- | :--- |
| `CHRONOS_MODE` | `annotate` | `observe`, `annotate` or `guard`. A per-key policy overrides it |
| `CHRONOS_GUARD_THRESHOLD` | `50` | In `guard`, scores below this regenerate once or block with 409 |
| `CHRONOS_VERIFY_TIMEOUT_MS` | `4000` | The plugin's inline deadline. Past it the response goes out unannotated |
| `CHRONOS_VERIFY_DEADLINE_S` | `90` | The verifier's own wall clock for one answer |
| `CHRONOS_SYNC_ON_CACHE_HIT` | on | Check cache hits inline. Turning this off removes the point of the project |
| `CHRONOS_AUTO_CACHE_KEY` | off | Derive `x-bf-cache-key` from the prompt when the caller sends none |
| `CHRONOS_CURRENT_THRESHOLD` | `80` | At or above this, `current` |
| `CHRONOS_CHECK_THRESHOLD` | `50` | At or above this, `check`; below it, `outdated` |
| `CHRONOS_MAX_CLAIMS` | `12` | Claims verified per answer |
| `CHRONOS_CLAIM_CONCURRENCY` | `2` | Claims verified in parallel |
| `CHRONOS_SWEEP_INTERVAL_S` | `600` | How often the background sweeper re-checks stored reports |
| `CHRONOS_SWEEP_BATCH` | `50` | Reports per sweep |
| `CHRONOS_SWEEPER_ENABLED` | on | Turn the sweeper off entirely |

## Addresses and storage

| Variable | Default | Read by |
| :--- | :--- | :--- |
| `CHRONOS_VERIFIER_URL` | `http://verifier:8000` | The plugin |
| `CHRONOS_BIFROST_URL` | `http://bifrost:8080` | The verifier, for its own model calls |
| `CHRONOS_BIFROST_ADMIN_URL` | the gateway's own base | The plugin, for `DELETE /api/cache/clear/{id}` |
| `CHRONOS_ADMIN_TOKEN` | falls back to `BIFROST_SETUP_TOKEN` | The plugin |
| `CHRONOS_VK` | `sk-bf-chronos-verifier-dev` | Both. **Must start with `sk-bf-`** or Bifrost mints its own and ignores yours | <!-- check-docs: ignore -->
| `CHRONOS_REDIS_URL` | `redis://redis:6379/1` | The verifier's claim cache. db0 is the semantic cache's vector store |
| `CHRONOS_DB_PATH` | `./chronos.sqlite` | The verifier's report store |
| `CHRONOS_REPORT_RETENTION_DAYS` | `30` | Reports and cache events older than this are deleted, at startup and on every sweep. `0` keeps everything; any other value is at least `2`, because the sweeper finds cached answers through their reports and an answer can stay cached for 24 hours. Lifetime counters in `/v1/stats` survive pruning |
| `CHRONOS_LOG_LEVEL` | `INFO` | The verifier |
| `CHRONOS_DEBUG` | `0` | The plugin. `1` also writes its log lines to stderr |
| `BIFROST_LOG_LEVEL` | `info` | Bifrost |
| `BIFROST_REF` | `transports/v2.4.0` | The image build. Changing it rebuilds the gateway from a different tag |
| `CHRONOS_VERSION` | `0.1.0` | Reported by `/health` |

## Offline, console and benchmark

| Variable | Default | What it does |
| :--- | :--- | :--- |
| `CHRONOS_OFFLINE_DEMO` | `1` | Nothing leaves the machine: answers from mockllm, evidence from the recorded fixtures |
| `MOCKLLM_LATENCY_MS` | `0` | Make the mock provider feel like a real one |
| `VITE_VERIFIER_URL` | `http://localhost:8000` | Baked into the console bundle **at build time** |
| `VITE_BIFROST_URL` | `http://localhost:8080` | Same |
| `CHRONOS_VERIFIER` | `http://localhost:8000` | `bench/runner.py`, overridden by `--verifier` |

That `CHRONOS_VK` default is the checked-in development value, not a credential: it is in
`.env.example` and in `deploy/docker-compose.yml`, and it is shown here because the `sk-bf-`
prefix is the whole point of the row. The leak check is told to skip that one line for that
reason. Give it a real value along with the two Bifrost tokens before exposing the gateway.

`CHRONOS_LIVE_MODELS` is kept separate from `CHRONOS_OFFLINE_DEMO` on purpose. A Tavily key
and a model key rarely arrive at the same time, and live evidence with rule-based reasoning
is a genuinely useful middle setting.

Because the two `VITE_` variables are compiled in, changing them needs
`docker compose build console`, not a restart.

## A minimal file that turns everything on

```bash
# deploy/.env
GROQ_API_KEY=gsk_...
TAVILY_API_KEY=tvly-...
CHRONOS_LIVE_MODELS=1
CHRONOS_OFFLINE_DEMO=0
BIFROST_SETUP_TOKEN=<something long and random>
BIFROST_ENCRYPTION_KEY=<32 bytes>
```
