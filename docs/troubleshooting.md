# Troubleshooting

Only things that have actually happened, with the exact text they printed. Most of them are
quiet failures: the system keeps working and the only symptom is that it is worse than it
should be. Those are the ones listed first.

## The quiet ones

These do not raise an error. Something that should be happening silently stops.

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| Verdicts are plausible but worse than the benchmark says, and `"extractor": "rules (model unavailable)"` in every report | Every model call is failing and the verifier is falling back to the rule engine. Usually a bad `GROQ_API_KEY`, or a retired model id | `./scripts/check-model.sh --all`. Groq retires model ids: the Llama chat models most guides still recommend now return **404** |
| The same, with `400 Invalid request payload` in the verifier log | `fallbacks` sent as objects. Bifrost's Go `Fallback` struct has `provider` and `model` fields, so `[{"provider":"groq","model":"..."}]` is the obvious guess, and the OpenAI-compatible route rejects it | It wants strings: `["groq/openai/gpt-oss-20b"]`. `verifier/chronos/llm.py` builds them; `CHRONOS_FALLBACK_MODELS` is comma-separated `provider/model` |
| No `x-chronos-*` headers at all, on any response, but everything else works | The gateway did not load the plugin, and a gateway with no plugin answers perfectly well | `docker logs chronos-bifrost \| grep -i chronos`. Nothing means it never loaded; check `deploy/bifrost.config.json` points at `./plugins/chronos.so` |
| Headers appear on `curl` but the console shows blanks | The nine headers are not on `Access-Control-Expose-Headers`, so the browser hides them | `plugin/main.go` appends them in `HTTPTransportResponseHeadersHook`, preserving the gateway's own entries. `verify-all.sh` asserts it |
| Cache hits are never caught, though evictions used to work | `CHRONOS_SYNC_ON_CACHE_HIT` was turned off. A cache hit is the only answer that can be stale, so this removes the point of the project | Turn it back on |
| `claim_cache.hit_rate` stuck at 0 with a warm cache | A write/read key mismatch. This happened once: entries were written by Pydantic alias and read by field name | Fixed with `AliasChoices` in `verifier/chronos/models.py`. The symptom was p50 3,678 ms where it should have been 28 ms |
| Chronos appears to verify thousands of answers from a handful of requests, and `model_pacing.waits` climbs for half an hour | Chronos is verifying its own verification calls. Its model calls go through Bifrost, where the plugin is attached, so without a marker each one is verified, and each of those makes more calls | The verifier sets `x-chronos-internal: 1` and the plugin skips any request carrying it. If you change the string, change it in both places. `plugin/internal_test.go` pins it. Measured before the fix: 4,605 verifications and 37 minutes of waiting from one 18-claim run |

## Build and start

| Error | Cause | Fix |
| :--- | :--- | :--- |
| `plugin was built with a different version of package ...` | The classic Go plugin ABI mismatch | It should be impossible here: the Dockerfile compiles `plugin/` as a package of Bifrost's own transports module. If you see it, something is building the plugin outside that image |
| The build fails with a message about the binary being statically linked | The gateway was built static, and a static binary can load no plugin | Deliberate. The build asserts `ldd` output is dynamic, because a static gateway starts fine and then refuses every plugin at runtime. Do not add `-extldflags '-static'` or the `sqlite_static` tag |
| `gzip: stdin: unexpected end of file` during the build | The vendored Bifrost tarball in `deploy/vendor/` is truncated | The Dockerfile validates it with `gzip -t` and re-downloads. If the network is also down, delete the file and retry with a working connection |
| `chronos-bifrost` starts and exits immediately, no logs worth reading | The binary is not at `/app/main`. Bifrost's entrypoint execs that exact path and ignores `CMD` | Keep the `COPY --from=builder /out/bifrost-http /app/main` line as it is |
| `pattern all:ui: no matching files found` | `bifrost-http/ui` is missing, and the source has `//go:embed all:ui` | The Dockerfile stubs the directory. Bifrost's web UI is therefore not in this image; the HTTP API is complete |

## At runtime

| Error | Cause | Fix |
| :--- | :--- | :--- |
| `404` from every model call, and the path in the log reads `/v1/v1/chat/completions` | `CHRONOS_BIFROST_URL` ends in `/v1`, and the SDK appends its own | Drop the suffix: `http://bifrost:8080` |
| Eviction logs `403` and `evictor.failed` climbs in `/v1/stats` | The management API needs a token while dashboard auth is inactive | Every `/api` call must carry `X-Bifrost-Setup-Token`. Set `BIFROST_SETUP_TOKEN`, or `CHRONOS_ADMIN_TOKEN` to override it |
| The verifier authenticates but Bifrost behaves as though it is a different key | A virtual key without the `sk-bf-` prefix is regenerated by Bifrost, which then ignores yours | `CHRONOS_VK` must start with `sk-bf-` |
| Verification takes 15–40 seconds and `model_pacing.waits` is climbing | Working as designed. The pacer holds calls back to stay inside 8,000 tokens a minute rather than being refused | Nothing. It delays a background report, not a response. On a paid tier, `CHRONOS_MODEL_TPM=0` |
| Half the claims come back `unverifiable` and the log is full of 429s | Pacing is off, or the budget is set too high for the key | This is what the pacer exists for: before it, a 30-claim run produced 42 rate-limit refusals and 40 deadline misses |
| `degraded: true` with a reason, and verdicts stop being useful | The daily Tavily credit budget is spent | `CHRONOS_TAVILY_DAILY_CREDITS`. The budget is per process and resets on restart |
| A model rejects `response_format` or `reasoning_effort` | Support varies by provider and by model | Handled: `verifier/chronos/llm.py` retries once without the parameter. A rate-limit or auth failure deliberately does *not* trigger that retry, because it would hit the same wall twice |
| `ctx.Log` output from the plugin never appears | It did not reach the gateway's log stream from a `.so`. Never diagnosed further | `stale_hit_prevented` is also written to stderr, where it is observable. `CHRONOS_DEBUG=1` turns the rest on |

## Verification and benchmarking

| Symptom | Cause | What to do |
| :--- | :--- | :--- |
| `P4: the cached stale answer was caught` fails with `verdict=unverifiable` | A live Tavily search for the claim returned nothing that addressed it. Two of the 44 checks depend on live search | Re-run. On 7 Nov this failed once and passed on the next run, verdict `outdated`, score 39. The script prints "live search results vary; re-run if transient" |
| A claim is `outdated` on one run and `check` on the next | The same cause: a different result set | Expected. Single-run benchmark figures move by several points |
| The benchmark reports a false positive on a claim labelled `current` | StaleBench's `current` labels decay by construction. "Tim Cook is the CEO of Apple" is the known one, where the evidence pointed at a successor | `python bench/runner.py --audit` prints every disagreement with its label confidence and source. A decayed label and a real error look identical in a score |
| An official source is in the evidence and was called `insufficient` | The judge sees an 800-character window of each page, and the sentence that settles the claim can fall outside it | Observed on `python.org/downloads/windows`, whose fallback page is mostly navigation. `CHRONOS_JUDGE_SNIPPET_CHARS` widens the window at the cost of tokens |
| Evidence quotes contain `â€œ` and similar | Page text from Tavily is not always decoded cleanly before it reaches the report | Cosmetic, not fixed. It affects the quote shown in the console, not the judgement |

## Telling two lookalikes apart

**"The verifier is down" and "the verifier is rate limited" look the same from the gateway.**
Both give `unverifiable`. Check `/v1/stats`: if it answers at all, the verifier is up, and
`model_pacing.waits` or the Tavily counters say which budget ran out.

**"The model is failing" and "the model is bad" look the same in the benchmark.** Both lower
precision. Read `extractor` in any report: a model id means the model ran, `rules` or
`rules (model unavailable)` means it did not. That field exists for exactly this.

**Stopping the gateway looks like a rate limit.** The verifier's own model calls route
through Bifrost, so `docker compose stop bifrost` makes every model call fail with a
connection error while the verifier stays up and healthy. I misread this once and
invalidated three measurements with it.
