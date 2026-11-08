# Evaluating Chronos

Written for someone deciding whether this works, who does not want to do my setup. It
answers one question: **what is the least I can do to see the point?**

Level 1 needs nothing installed and no credentials.

---

## For a submission form

> Chronos is a plugin for the Bifrost AI gateway that stops it serving answers whose facts
> have gone out of date, including answers replayed from its semantic cache. For every
> answer it extracts the claims that depend on today's date, checks them against the live web
> with Tavily, scores each 0–100, reports the verdict in response headers, sets the cache TTL
> from how fast the answer's facts actually move, and deletes the cache entry behind any
> answer it finds stale. It runs on free open-weights models and a free Tavily key, and the
> whole stack runs with no credentials at all against a built-in mock provider.
> The console builds to a static folder that runs with no backend, replaying a recorded log
> and labelled as such; it is hosted at https://chronos-sable-one.vercel.app/.
> `./scripts/verify-all.sh` runs 52 checks end to end; on a 18-claim benchmark subset the
> model path scores precision 0.80 and recall 0.667 against a rule-only baseline of 0.40 and
> 0.20. Built during the event with Claude Code; see the README's disclosure.

---

## The levels

| | What you need | Time | What you get |
| :--- | :--- | :--- | :--- |
| **1. Open the demo site** | a browser, nothing installed | 3 min | The real console, and a five-step demo of the whole cycle you drive yourself |
| **2. Run the stack** | Docker | ~8 min first build, 1 min after | Live data, and the miss → hit → evicted → miss cycle by hand |
| **3. Run the checks** | Docker | ~12 min | 52 assertions, end to end |
| **4. Run the benchmark** | Docker + a free Tavily key + a free Groq key | ~6 min | The quality numbers, reproduced |

Levels 1 to 3 need **no API keys**. Only level 4 does, and both are free with no card.

---

## Level 1 — two minutes, nothing installed

Open **<https://chronos-sable-one.vercel.app/>**.

The console is a static bundle, and it works with **no backend at all**: when the verifier is
unreachable it replays a recorded log and says so on the page. The header should read
**"Recorded demo replay"** — that is the fallback working, not a failure. Nothing on that page
is presented as live.

To serve the same folder yourself instead:

```bash
./scripts/build-demo-site.sh          # needs Node; writes console/dist-demo
npx http-server console/dist-demo -p 8099
```

Scroll to **Run it**. Press **Start**, then **Next** four times. That is the whole product
in five steps: ask a question, ask it again and hit the cache, watch the claims get checked,
read the verdict off the response headers, watch the stale entry be deleted. With the stack
running it sends real requests and shows real headers; without it, the same five steps replay
a recorded exchange and say so.

Press **Start the walkthrough**. Ten steps, about two minutes, in the order the product
actually happens: the problem, where Chronos sits, the cycle driven by hand, answers being
checked, the claims and the score, the TTL policy and the eviction ledger, the headers on the
response, the measured numbers, the briefing, and every feature run for real. It scrolls the real page and rings the panel it is talking about —
arrow keys to move, Escape to leave. Step 4 is the one that matters.

The folder is plain static files with relative paths, so it can be dropped on any host, and
it carries `bench/results.json`, which is why the Benchmark panel shows measured numbers
rather than its "no results file" state.

Scroll to **Inspector**. That one panel is the product: a cached answer with its claims lit
up, one scored 36 and marked outdated, beside every term of the score and the two pages that
disagreed, quoted.

If you cannot run even that, the same three things are in the repository as images:
[the inspector](docs/screenshots/console-inspector.png), the real response
[headers](README.md#what-it-looks-like) from the second of three identical requests, and
[the whole system on one page](docs/architecture.png).

Then scroll to **Every feature**, the last section. Each card is one feature run against the
live stack by `scripts/record-proof.py`, with the headers, report fields and counters that
came back, and the date of the run. A feature that failed would be shown in red.

---

## Level 2 — five minutes that show the point

```bash
git clone <this repo> chronos && cd chronos/deploy
docker compose up --build        # several minutes the first time
```

No `.env`, no keys. Answers come from a built-in mock provider and evidence from recorded
fixtures.

Then open **<http://localhost:5173>** and scroll to **Inspector**. Press *Check again* on the
stale-version example. Watch the claim spans light up and the score break down into its
terms.

Now the cycle, by hand. Run this **three times**, pausing ten seconds between:

```bash
curl -i http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'x-bf-cache-key: demo-1' \
  -d '{"model":"mockllm/chronos-demo",
       "messages":[{"role":"user","content":"What is the latest version of Python?"}]}'
```

| Request | What to look for |
| :--- | :--- |
| 1 | `x-bifrost-cache-hit: false`, `x-chronos-verdict: pending`, `x-chronos-ttl: 21600`. The answer did not wait to be verified, and Chronos has already decided this class of fact may be cached for six hours, not the default day |
| 2 | `x-bifrost-cache-hit: true` — **and** `x-chronos-verdict: outdated`, `x-chronos-score: 39`, `x-chronos-stale: 1`. The cached answer was checked inline and found wrong |
| 3 | `x-bifrost-cache-hit: false` again. **This is the moment.** The entry was deleted between request 2 and request 3, so the next caller gets a fresh answer instead of the stale one |

That third line is the whole product: a semantic cache that stops replaying an answer once
the facts underneath it have moved.

Then check the ledger:

```bash
curl -s http://localhost:8000/v1/cache/events | head -c 400
```

With live keys set (level 4) the evidence is real pages rather than fixtures, and the same
three requests behave the same way.

---

## Level 3 — the checks

```bash
./scripts/verify-all.sh            # everything, needs Docker
./scripts/verify-all.sh --unit     # the test suites only, no stack
```

52 checks. Each one prints what it asserted, so a pass means something specific rather than
"no errors": that the gateway loaded `chronos.so`, that a second identical request is a cache
hit, that Chronos set a six-hour TTL for a `version` claim, that a stale cached answer is
caught and evicted and the next request misses, that the nine headers are exposed to a
browser, and that **the gateway keeps answering with the verifier stopped**.

Results and what they do and do not establish:
[README.md](README.md#what-is-verified-and-what-is-not).

---

## Level 4 — reproduce the numbers

Two free keys, neither needing a card:

```bash
echo 'GROQ_API_KEY=gsk_...'   >> deploy/.env     # https://console.groq.com/keys
echo 'TAVILY_API_KEY=tvly-...' >> deploy/.env     # https://app.tavily.com
echo 'CHRONOS_LIVE_MODELS=1'   >> deploy/.env
echo 'CHRONOS_OFFLINE_DEMO=0'  >> deploy/.env
cd deploy && docker compose up -d --build

./scripts/check-model.sh --all   # confirm which models your key reaches today
python bench/runner.py --verifier http://localhost:8000 --limit 18 --concurrency 1 --audit
```

`--audit` prints every disagreement with its label's confidence and source, which is how you
tell a real error from a label that has gone stale. Read
[README.md](README.md#measured-results) before quoting any figure from this: the 18-claim
subset is entirely `leadership`, the hardest class, and it is not a cross-class average.

---

## Known limits, before you find them

- **Two of the 52 checks depend on a live web search and are genuinely flaky.** On 7 Nov one
  run had them fail with `verdict=unverifiable`; the next run passed with `outdated`, score
  39. The script says so itself: *"live search results vary; re-run if transient"*.
- **The benchmark numbers move between runs** for the same reason.
- **The 18-claim model-path subset is one class**, `leadership`. `--limit 0` runs all 120 and
  costs considerably more credits.
- **`guard` mode has never run through a gateway configured for it.** Both halves are tested,
  and the verifier's rewrite has run live, but the gateway's substitute-or-409 path has run only
  in Go tests, because the demo gateway runs in `annotate`.
- **Streaming answers are not content-verified.** They get headers with verdict `pending`.
- **Never run on macOS or Linux.** Windows with Docker Desktop throughout.
- **Nothing renders the console in a real browser engine.** Its 125 tests run under jsdom,
  so a CSS mistake passes all of them. The screenshots are the only evidence of that.

The full list is in [README.md](README.md#not-verified-and-should-be).

---

## If something does not work

| Symptom | Likely cause |
| :--- | :--- |
| No `x-chronos-*` headers on any response | The gateway did not load the plugin. `docker logs chronos-bifrost \| grep -i chronos` |
| Request 2 says `pending` rather than a verdict | You did not wait long enough between requests, or `CHRONOS_SYNC_ON_CACHE_HIT` is off |
| Request 2 is `unverifiable` with live keys | The live search found nothing that addressed the claim. Re-run |
| Verdicts are poor and reports say `"extractor": "rules (model unavailable)"` | Every model call is failing. `./scripts/check-model.sh --all` |
| Everything is slow and `/v1/stats` shows `model_pacing.waits` climbing | Working as designed: it is staying inside a free tier's token budget |
| The container starts and exits immediately | The gateway binary is not at `/app/main` |

Longer list, with the exact error strings:
[docs/troubleshooting.md](docs/troubleshooting.md).
