Chronos

Recorded demo replay

[Signal path](#path)[Traffic](#traffic)[Inspector](#inspect)[Cache](#cache)[Playground](#playground)[Benchmark](#bench)[Briefing](#brief)

A freshness firewall for the Bifrost AI gateway

# Your cache remembers. Chronos checks.

Semantic caching replays yesterday's answer to today's user. Chronos sits inside Bifrost, pulls the time-sensitive facts out of every answer, checks them against the live web with Tavily, and evicts cache entries whose facts have gone stale.

Bifrost · gateway, cache, routing Tavily · live evidence Chronos · claims, scores, eviction

**0**answers checked

**0**stale cache hits stopped

**0%**Tavily calls saved by claim cache

### How fast a cached fact goes stale

Day 0.0 since cached

Signal path · one request

## Who does what on every answer

Follow one pricing question through the gateway. The colour of each box shows who owns that step: Bifrost runs the gateway, Tavily brings the evidence, Chronos adds the judgement. Pick any step to replay it.

Auto-playing every 5 seconds

Bifrost

### The gateway every app already calls

- Hosts Chronos as a Go plugin on PreLLMHook and PostLLMHook
- Semantic cache with a per-request TTL that Chronos sets
- Cache-clear API that Chronos uses to evict stale entries
- Routes, budgets and falls back for Chronos's own model calls

Per answer: 1 gateway pass + 2 internal model calls

Tavily

### The live web, as evidence

- Search with a recent date window, official domains first
- Extract pulls the actual page text the judge reads
- Research endpoint powers the meeting briefing
- Only called on a claim-cache miss

Per new claim: 1 search + 1 extract

Chronos

### The judgement in between

- Extracts typed, time-sensitive claims with exact spans
- Volatility classes decide re-check time and cache TTL
- Scores each claim 0–100 and explains the score
- Annotates, evicts, regenerates or blocks, per virtual key

Headers: x-chronos-score · -verdict · -ttl · -report

Overview · live traffic

## Every answer, scored as it passes

Requests from three apps behind one gateway. Blue rows came from Bifrost's semantic cache and spent no tokens; those are the ones most likely to be stale. Figures here are a recorded replay.

TimePromptSourceVerdictScore

**0**Requests through gateway

**0**Claims checked

**0**Outdated answers caught

**0**Cache entries evicted

**0**Tavily credits used

**$0.00**Provider spend avoided by cache

### Stale catches per 10 s

last 5 min

Inspector · stale-answer detector

## Paste an answer, watch the claims light up

Chronos finds the sentences that depend on today's date, checks each one, and scores it. Green is current, amber needs a human look, red is outdated. Grey facts never change and are skipped, which saves Tavily credits. Select any claim to see why it scored what it did.

Cached answer · served 9 days after it was written

Cache · volatility-aware TTL

## One flat TTL fits no fact

Bifrost's semantic cache takes one TTL. Chronos sets it per request from how quickly the answer's facts change, so stable answers stay cached longer and fast-moving ones expire sooner. Grey is the flat 24-hour default; blue is what Chronos chose.

Flat default, 24 hChronos TTLscale 0 – 72 h

### Stale hits stopped, last 24 h

0 evictions

Each mark is a cache hit that would have served an outdated fact. Chronos cleared it with DELETE /api/cache/clear/{cache_id}.

Playground · raw gateway traffic

## Same question, twice

This is the proof that Chronos lives in the gateway rather than the UI. The first call is served from Bifrost's cache and caught. Chronos evicts it. The second call misses the cache, goes to the provider, and comes back current.

### Request 1 · cache hit

waiting

```

```

### Request 2 · after eviction

waiting

```

```

Uses x-bf-cache-key so Bifrost's cache engages.

StaleBench · 120 hand-labelled claims

## Measured, not promised

The repo ships a benchmark: 40 claims known to be stale, 40 known to be current, 40 timeless. The figures below are example targets for the layout; the build replaces them with your own run's results before submission.

### Precision and recall on "outdated"

Example values. Dot = precision, ring = recall.

### Added latency by mode

Example values. Bar spans p50 to p95, log scale.

### Tavily credits per 1,000 answers

Example values. Claim cache + timeless skip.

Briefing mode · same engine

## A meeting brief that shows its work

Give Chronos a company and a meeting time. Tavily researches the last 30 days, a model writes the brief through Bifrost, and every line goes back through the verifier before you read it. Contradictions become questions to ask.

Fictional example company

Kestrel Robotics

Meeting

Thu 12 Nov 2026 · 10:30 IST

Chronos · Bifrost plugin + Tavily verification · Global AI Hackathon 2026All traffic, scores and figures on this page are a recorded demo replay.