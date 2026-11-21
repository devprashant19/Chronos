/**
 * Demo mode: a recorded log, replayed.
 *
 * This exists so the console can be judged with no API keys and no running
 * backend. It is deliberately NOT a separate rendering path — it emits exactly
 * the `FeedEvent` shape the SSE stream emits, so every component downstream is
 * the real one. If the live feed renders correctly, so does this, and vice
 * versa.
 *
 * Everything here is labelled as recorded in the UI. The numbers are a
 * plausible traffic mix for the demo, not measurements: the measured figures
 * live in bench/results.json and are shown in the benchmark section.
 */

import type { Brief, ClaimVerdict, FeedEvent, Stats, VerifyReport } from "./api";

const PROMPTS: Array<{
  prompt: string;
  model: string;
  verdict: FeedEvent["verdict"];
  score: number;
  claims: number;
  stale: number;
  skipped: number;
  cls: string;
  ttl: number;
  cacheHit: boolean;
  credits: number;
  latency: number;
}> = [
  { prompt: "What is the latest version of Python?", model: "openai/gpt-4o-mini", verdict: "outdated", score: 36, claims: 1, stale: 1, skipped: 1, cls: "version", ttl: 21600, cacheHit: true, credits: 0, latency: 28 },
  { prompt: "How much does GPT-4o cost per million tokens?", model: "openai/gpt-4o-mini", verdict: "outdated", score: 41, claims: 2, stale: 1, skipped: 0, cls: "pricing", ttl: 21600, cacheHit: true, credits: 5, latency: 3180 },
  { prompt: "Write a function that reverses a string", model: "openai/gpt-4o-mini", verdict: "current", score: 100, claims: 0, skipped: 3, stale: 0, cls: "timeless", ttl: 604800, cacheHit: false, credits: 0, latency: 7 },
  { prompt: "Who is the CEO of Twitter?", model: "groq/llama-3.1-8b-instant", verdict: "outdated", score: 33, claims: 1, stale: 1, skipped: 2, cls: "leadership", ttl: 86400, cacheHit: true, credits: 5, latency: 2940 },
  { prompt: "What is the capital of France?", model: "openai/gpt-4o-mini", verdict: "current", score: 100, claims: 1, stale: 0, skipped: 1, cls: "timeless", ttl: 604800, cacheHit: false, credits: 0, latency: 6 },
  { prompt: "What is the price of Bitcoin right now?", model: "openai/gpt-4o-mini", verdict: "outdated", score: 22, claims: 1, stale: 1, skipped: 1, cls: "market", ttl: 900, cacheHit: true, credits: 5, latency: 3420 },
  { prompt: "When does the EU AI Act come into force?", model: "openai/gpt-4o-mini", verdict: "check", score: 63, claims: 1, stale: 0, skipped: 1, cls: "law", ttl: 86400, cacheHit: false, credits: 5, latency: 4100 },
  { prompt: "Explain the difference between TCP and UDP", model: "openai/gpt-4o-mini", verdict: "current", score: 100, claims: 2, stale: 0, skipped: 2, cls: "timeless", ttl: 604800, cacheHit: false, credits: 0, latency: 9 },
  { prompt: "What is the newest iPhone?", model: "openai/gpt-4o-mini", verdict: "check", score: 71, claims: 1, stale: 0, skipped: 0, cls: "version", ttl: 21600, cacheHit: false, credits: 5, latency: 3650 },
  { prompt: "Who is the current prime minister of the UK?", model: "groq/llama-3.1-8b-instant", verdict: "unverifiable", score: 0, claims: 1, stale: 0, skipped: 1, cls: "leadership", ttl: 86400, cacheHit: false, credits: 5, latency: 5200 },
];

/** The answer shown in the Inspector, with its claims already scored. */
export const DEMO_ANSWER =
  "The latest stable release of Python is 3.11, which came out in October 2022. " +
  "Python follows an annual release cadence, so a new feature release ships each October. " +
  "GPT-4o costs $5.00 per million input tokens. " +
  "The capital of France is Paris.";

export const DEMO_CLAIMS: ClaimVerdict[] = [
  {
    ...span(DEMO_ANSWER, "The latest stable release of Python is 3.11, which came out in October 2022."),
    subject: "Python",
    predicate: "latest version",
    stated_value: "3.11",
    class: "version",
    verdict: "outdated",
    score: 36.2,
    current_value: "a later 3.x release",
    source_url: "https://www.python.org/downloads/",
    publish_date: "2026-11-03",
    quote:
      "Python ships a new feature release every October, so 3.11 (October 2022) is several releases behind.",
    breakdown: {
      agreement: 0, recency: 1.0, quality: 1.0, confidence: 0.75,
      volatility: 0.7, decay: 0, base: 0.5125, decay_multiplier: 1,
      score: 36.2, supporting: 0, contradicting: 4, insufficient: 1,
      formula: "S = 100*(0.45A + 0.25R + 0.15Q + 0.15C)*(1 - 0.5*V*D)",
    },
    evidence: [
      { url: "https://www.python.org/downloads/", title: "Download Python", snippet: "The current stable release is listed at the top of this page.", published_date: "2026-11-03", tier: "official", call: "contradicts", quote: "Python ships a new feature release every October." },
      { url: "https://devguide.python.org/versions/", title: "Status of Python versions", snippet: "A table of versions with end-of-support dates.", published_date: "2026-10-27", tier: "official", call: "contradicts", quote: "3.11 is not the newest feature release." },
    ],
    from_claim_cache: false,
    tavily_credits: 5,
  },
  {
    ...span(DEMO_ANSWER, "Python follows an annual release cadence, so a new feature release ships each October."),
    subject: "Python",
    predicate: "release cadence",
    stated_value: "annual, each October",
    class: "timeless",
    verdict: "current",
    score: 100,
    current_value: "annual, each October",
    source_url: null,
    publish_date: null,
    quote: "Timeless claim: skipped without spending a lookup.",
    breakdown: null,
    evidence: [],
    from_claim_cache: false,
    tavily_credits: 0,
  },
  {
    ...span(DEMO_ANSWER, "GPT-4o costs $5.00 per million input tokens."),
    subject: "OpenAI GPT-4o",
    predicate: "input token price",
    stated_value: "$5.00 per million",
    class: "pricing",
    verdict: "outdated",
    score: 41.0,
    current_value: "see OpenAI's current pricing page",
    source_url: "https://openai.com/api/pricing/",
    publish_date: "2026-11-01",
    quote: "Prices on this page supersede any earlier rate card.",
    breakdown: {
      agreement: 0, recency: 1.0, quality: 1.0, confidence: 0.8,
      volatility: 0.8, decay: 0, base: 0.52, decay_multiplier: 1,
      score: 41.0, supporting: 0, contradicting: 2, insufficient: 2,
      formula: "S = 100*(0.45A + 0.25R + 0.15Q + 0.15C)*(1 - 0.5*V*D)",
    },
    evidence: [
      { url: "https://openai.com/api/pricing/", title: "API Pricing", snippet: "Per-model prices are revised periodically.", published_date: "2026-11-01", tier: "official", call: "contradicts", quote: "The figures published here supersede any earlier rate card." },
    ],
    from_claim_cache: false,
    tavily_credits: 5,
  },
  {
    ...span(DEMO_ANSWER, "The capital of France is Paris."),
    subject: "France",
    predicate: "capital",
    stated_value: "Paris",
    class: "timeless",
    verdict: "current",
    score: 100,
    current_value: "Paris",
    source_url: null,
    publish_date: null,
    quote: "Timeless claim: skipped without spending a lookup.",
    breakdown: null,
    evidence: [],
    from_claim_cache: false,
    tavily_credits: 0,
  },
];

export const DEMO_REPORT: VerifyReport = {
  report_id: "demo-report",
  score: 36.2,
  verdict: "outdated",
  claims: DEMO_CLAIMS,
  claim_count: DEMO_CLAIMS.length,
  stale_count: 2,
  skipped: 1,
  ttl_seconds: 21600,
  tavily_credits: 10,
  degraded: false,
  reason: null,
  latency_ms: 3180,
  request_id: "demo",
  cache_hit: true,
  cache_id: "demo-cache-id",
  evicted: true,
};


/*
 * One recorded report per Inspector example. Before these existed, every
 * example replayed DEMO_REPORT, whose claim offsets belong to DEMO_ANSWER: on
 * any other text the wrong words lit up and the drawer explained the Python
 * claim, and "All timeless" reported 10 credits on a page that says timeless
 * facts cost nothing.
 *
 * Spans are computed from the text, never typed, because the console
 * highlights by offset and an off-by-one shows. Each score is the formula
 * worked through with the terms in its breakdown: an undated page scores
 * R = 0.4, Wikipedia is tier "other" (Q = 0.4), and D = 0 for a fresh check.
 * Answer-level fields follow the verifier: the score is the minimum over
 * checkable claims, the TTL the minimum over classes, and "skipped" counts
 * sentences that are not claims at all.
 */

export const LEADERSHIP_ANSWER =
  "Jack Dorsey is the CEO of Twitter. He co-founded the company and returned as chief executive in 2015. " +
  "Twitter remains a publicly traded company on the New York Stock Exchange.";

export const PRICING_ANSWER =
  "OpenAI's GPT-4o is priced at $5.00 per million input tokens and $15.00 per million output tokens. " +
  "There is no free tier for the API.";

export const TIMELESS_ANSWER =
  "The capital of France is Paris. Water boils at 100 degrees Celsius at standard atmospheric pressure. " +
  "A stack is a last-in, first-out data structure.";

const FORMULA = "S = 100*(0.45A + 0.25R + 0.15Q + 0.15C)*(1 - 0.5*V*D)";

function span(answer: string, sentence: string): { text: string; span_start: number; span_end: number } {
  const start = answer.indexOf(sentence);
  if (start < 0) throw new Error(`demo claim not in its answer: ${sentence}`);
  return { text: sentence, span_start: start, span_end: start + sentence.length };
}

/** A checked claim, with S computed from its own terms rather than typed. */
function checked(
  answer: string,
  sentence: string,
  c: Omit<ClaimVerdict, "text" | "span_start" | "span_end" | "score" | "breakdown"> & {
    terms: { A: number; R: number; Q: number; C: number; V: number; supporting: number; contradicting: number; insufficient: number };
  },
): ClaimVerdict {
  const { terms, ...rest } = c;
  const base = 0.45 * terms.A + 0.25 * terms.R + 0.15 * terms.Q + 0.15 * terms.C;
  const score = Math.round(base * 1000) / 10;
  return {
    ...span(answer, sentence),
    ...rest,
    score,
    breakdown: {
      agreement: terms.A, recency: terms.R, quality: terms.Q, confidence: terms.C,
      volatility: terms.V, decay: 0, base: Math.round(base * 10000) / 10000, decay_multiplier: 1,
      score, supporting: terms.supporting, contradicting: terms.contradicting, insufficient: terms.insufficient,
      formula: FORMULA,
    },
  };
}

function timeless(answer: string, sentence: string, subject: string, predicate: string, value: string): ClaimVerdict {
  return {
    ...span(answer, sentence),
    subject, predicate, stated_value: value, class: "timeless", verdict: "current", score: 100,
    current_value: value, source_url: null, publish_date: null,
    quote: "Timeless claim: skipped without spending a lookup.",
    breakdown: null, evidence: [], from_claim_cache: false, tavily_credits: 0,
  };
}

const LEADERSHIP_CLAIMS: ClaimVerdict[] = [
  checked(LEADERSHIP_ANSWER, "Jack Dorsey is the CEO of Twitter.", {
    subject: "Twitter", predicate: "chief executive", stated_value: "Jack Dorsey", class: "leadership",
    verdict: "outdated", current_value: "not Jack Dorsey: he stepped down in November 2021",
    source_url: "https://en.wikipedia.org/wiki/Jack_Dorsey", publish_date: null,
    quote: "Dorsey stepped down as CEO of Twitter in November 2021.",
    evidence: [
      { url: "https://en.wikipedia.org/wiki/Jack_Dorsey", title: "Jack Dorsey", snippet: "American businessman, co-founder and former CEO of Twitter.", published_date: null, tier: "other", call: "contradicts", quote: "Dorsey stepped down as CEO of Twitter in November 2021." },
      { url: "https://en.wikipedia.org/wiki/Twitter,_Inc.", title: "Twitter, Inc.", snippet: "The company was acquired in October 2022 and merged into X Corp.", published_date: null, tier: "other", call: "contradicts", quote: "Twitter, Inc. no longer exists as an independent company." },
    ],
    from_claim_cache: false, tavily_credits: 5,
    terms: { A: 0, R: 0.4, Q: 0.4, C: 0.9, V: 0.4, supporting: 0, contradicting: 2, insufficient: 0 },
  }),
  timeless(LEADERSHIP_ANSWER, "He co-founded the company and returned as chief executive in 2015.", "Jack Dorsey", "returned as CEO", "2015"),
  checked(LEADERSHIP_ANSWER, "Twitter remains a publicly traded company on the New York Stock Exchange.", {
    subject: "Twitter", predicate: "stock listing", stated_value: "publicly traded on the NYSE", class: "events",
    verdict: "outdated", current_value: "private since its acquisition in October 2022",
    source_url: "https://en.wikipedia.org/wiki/Acquisition_of_Twitter_by_Elon_Musk", publish_date: null,
    quote: "The acquisition closed in October 2022 and Twitter was delisted from the New York Stock Exchange.",
    evidence: [
      { url: "https://en.wikipedia.org/wiki/Acquisition_of_Twitter_by_Elon_Musk", title: "Acquisition of Twitter by Elon Musk", snippet: "The acquisition was completed on October 27, 2022.", published_date: null, tier: "other", call: "contradicts", quote: "Twitter was delisted from the New York Stock Exchange." },
      { url: "https://en.wikipedia.org/wiki/Twitter,_Inc.", title: "Twitter, Inc.", snippet: "Formerly traded on the NYSE under TWTR.", published_date: null, tier: "other", call: "contradicts", quote: "Formerly traded as NYSE: TWTR." },
    ],
    from_claim_cache: false, tavily_credits: 5,
    terms: { A: 0, R: 0.4, Q: 0.4, C: 0.92, V: 0.6, supporting: 0, contradicting: 2, insufficient: 0 },
  }),
];

const PRICING_CLAIMS: ClaimVerdict[] = [
  checked(PRICING_ANSWER, "OpenAI's GPT-4o is priced at $5.00 per million input tokens and $15.00 per million output tokens.", {
    subject: "OpenAI GPT-4o", predicate: "API token price", stated_value: "$5.00 in, $15.00 out per million", class: "pricing",
    verdict: "outdated", current_value: "below the launch price; see OpenAI's current pricing page",
    source_url: "https://openai.com/api/pricing/", publish_date: "2026-11-01",
    quote: "Prices on this page supersede any earlier rate card.",
    evidence: [
      { url: "https://openai.com/api/pricing/", title: "API Pricing", snippet: "Per-model prices are revised periodically.", published_date: "2026-11-01", tier: "official", call: "contradicts", quote: "The figures published here supersede any earlier rate card." },
      { url: "https://platform.openai.com/docs/models/gpt-4o", title: "GPT-4o model", snippet: "Model page with current pricing per million tokens.", published_date: null, tier: "official", call: "contradicts", quote: "Current pricing is listed per million tokens on this page." },
    ],
    from_claim_cache: false, tavily_credits: 5,
    terms: { A: 0, R: 1.0, Q: 1.0, C: 0.85, V: 0.8, supporting: 0, contradicting: 2, insufficient: 0 },
  }),
  {
    ...span(PRICING_ANSWER, "There is no free tier for the API."),
    subject: "OpenAI API", predicate: "free tier", stated_value: "none", class: "pricing",
    verdict: "unverifiable", score: 0, current_value: null, source_url: null, publish_date: null,
    quote: "No source addressed this either way, so it is unverifiable, which never evicts.",
    breakdown: null,
    evidence: [
      { url: "https://openai.com/api/pricing/", title: "API Pricing", snippet: "Per-model prices are revised periodically.", published_date: "2026-11-01", tier: "official", call: "insufficient", quote: "Nothing on this page addresses a free tier either way." },
    ],
    from_claim_cache: false, tavily_credits: 5,
  },
];

const TIMELESS_CLAIMS: ClaimVerdict[] = [
  timeless(TIMELESS_ANSWER, "The capital of France is Paris.", "France", "capital", "Paris"),
  timeless(TIMELESS_ANSWER, "Water boils at 100 degrees Celsius at standard atmospheric pressure.", "water", "boiling point", "100 °C"),
  timeless(TIMELESS_ANSWER, "A stack is a last-in, first-out data structure.", "stack", "ordering", "last-in, first-out"),
];

function report(id: string, claims: ClaimVerdict[], ttl: number, latency: number, cacheHit: boolean): VerifyReport {
  const checkable = claims.filter((c) => c.verdict !== "unverifiable");
  const stale = claims.filter((c) => c.verdict === "outdated").length;
  return {
    report_id: id,
    score: checkable.length ? Math.min(...checkable.map((c) => c.score)) : 0,
    verdict: !checkable.length ? "unverifiable" : stale ? "outdated" : "current",
    claims,
    claim_count: claims.length,
    stale_count: stale,
    skipped: 0,
    ttl_seconds: ttl,
    tavily_credits: claims.reduce((n, c) => n + c.tavily_credits, 0),
    degraded: false,
    reason: null,
    latency_ms: latency,
    request_id: "demo",
    cache_hit: cacheHit,
    cache_id: cacheHit ? "demo-cache-id" : "",
    evicted: cacheHit && stale > 0,
  };
}

/** The recorded report for an Inspector example, or null for text nobody recorded. */
export function demoReportFor(answer: string): VerifyReport | null {
  switch (answer.trim()) {
    case DEMO_ANSWER:
      return DEMO_REPORT;
    case LEADERSHIP_ANSWER:
      // leadership 24 h and events 12 h: the answer gets the shorter.
      return report("demo-report-leadership", LEADERSHIP_CLAIMS, 43200, 2940, true);
    case PRICING_ANSWER:
      return report("demo-report-pricing", PRICING_CLAIMS, 21600, 3310, true);
    case TIMELESS_ANSWER:
      // Only timeless claims: no ceiling, so the default TTL stands, and no lookup at all.
      return report("demo-report-timeless", TIMELESS_CLAIMS, 86400, 7, false);
    default:
      return null;
  }
}

// The replay's rates follow the measured runs so the page never contradicts its
// own benchmark: claim-cache hit rate 39% (bench/results.json, credits saved on
// a repeat pass), verifier p50 841 ms and p95 4318 ms (the rule-engine run),
// 3 Tavily credits per claim-cache miss (search plus extract), and the models
// the measured runs used. Counts of answers are still a traffic mix, not data.
export const DEMO_STATS: Stats = {
  answers_checked: 1284,
  claims_checked: 1631,
  claims_skipped: 2140,
  outdated_answers: 143,
  cache_evictions: 97,
  stale_hits_prevented: 97,
  tavily_credits_used: 2985,
  verify_p50_ms: 841,
  verify_p95_ms: 4318,
  claim_cache: { backend: "redis", hits: 636, misses: 995, hit_rate: 0.39 },
  tavily: { limit: 4000, used: 2985, remaining: 1015, configured: true, live: true },
  evictor: { cleared: 97, failed: 0 },
  sweeper: { runs: 38, reverified: 412, evicted: 23, interval_s: 600, enabled: true },
  mode: { offline_demo: false, extract_model: "groq/openai/gpt-oss-120b", judge_model: "groq/openai/gpt-oss-20b" },
  thresholds: { current: 80, check: 50 },
  uptime_s: 23040,
};

export const DEMO_BRIEF: Brief = {
  subject: "Bifrost (Maxim AI)",
  meeting_time: "Thu 12 Nov 2026 · 10:30 IST",
  verified: [
    { text: "Bifrost is an open-source AI gateway written in Go, distributed under Apache-2.0.", verdict: "current", score: 94, source_url: "https://github.com/maximhq/bifrost", publish_date: "2026-11-04", reason: null },
    { text: "The gateway exposes an OpenAI-compatible /v1/chat/completions endpoint.", verdict: "current", score: 91, source_url: "https://docs.getbifrost.ai/quickstart/gateway/setting-up", publish_date: "2026-10-30", reason: null },
    { text: "Custom plugins are Go shared objects and require a dynamically linked build.", verdict: "current", score: 88, source_url: "https://docs.getbifrost.ai/plugins/building-dynamic-binary", publish_date: "2026-10-22", reason: null },
  ],
  unconfirmed: [
    { text: "The semantic cache plugin supports Redis, Weaviate, Qdrant and Pinecone vector stores.", verdict: "check", score: 72, source_url: "https://docs.getbifrost.ai/features/semantic-caching", publish_date: null, reason: "no dated source confirmed the full store list" },
    { text: "Bifrost is substantially faster than comparable gateways under load.", verdict: "unverifiable", score: 0, source_url: null, publish_date: null, reason: "a performance comparison needs a benchmark, not a search result" },
  ],
  open_questions: [
    "Which vector stores are supported in the release we would deploy, specifically?",
    "Could not confirm: Bifrost is substantially faster than comparable gateways under load. Worth asking directly.",
  ],
  tavily_credits: 8,
  degraded: false,
  reason: "Recorded demo. Replace the subject with a real one before presenting.",
};

/**
 * Replay the recorded log as a live feed.
 *
 * Emits a verdict every `intervalMs`, and an eviction event right after any
 * verdict that found a stale claim on a cache hit — the same ordering the real
 * verifier produces, because the eviction follows the verdict that caused it.
 */
export function startDemoFeed(
  onEvent: (event: FeedEvent) => void,
  intervalMs = 2600,
): () => void {
  let seq = 0;
  let index = 0;

  const emit = () => {
    const row = PROMPTS[index % PROMPTS.length];
    index += 1;
    seq += 1;

    const cacheId = `demo-${(seq * 7919).toString(16).slice(0, 8)}`;
    onEvent({
      seq,
      type: "verdict",
      report_id: `demo-${seq}`,
      request_id: `demo-req-${seq}`,
      verdict: row.verdict,
      score: row.score,
      claims: row.claims,
      stale: row.stale,
      skipped: row.skipped,
      cache_hit: row.cacheHit,
      cache_id: cacheId,
      ttl_seconds: row.ttl,
      latency_ms: row.latency,
      credits: row.credits,
      model: row.model,
      prompt: row.prompt,
      degraded: false,
      at: new Date().toISOString(),
    });

    if (row.stale > 0 && row.cacheHit) {
      seq += 1;
      onEvent({
        seq,
        type: "eviction",
        cache_id: cacheId,
        cache_key: "demo-session",
        report_id: `demo-${seq - 1}`,
        score: row.score,
        reason: "stale_hit",
        stale: row.stale,
        at: new Date().toISOString(),
      });
    }
  };

  emit();
  const timer = window.setInterval(emit, intervalMs);
  return () => window.clearInterval(timer);
}
