/**
 * The console's data layer.
 *
 * Two sources, one shape. Live mode talks to the verifier's HTTP API and its
 * SSE stream; demo mode replays a recorded log at wall-clock speed. Everything
 * above this file is written against the same types, so no component knows or
 * cares which one is feeding it — which is what keeps demo mode honest: it
 * exercises the real rendering path, it just does not need credentials.
 */

export const VERIFIER_URL: string =
  (import.meta.env.VITE_VERIFIER_URL as string | undefined)?.replace(/\/$/, "") ??
  "http://localhost:8000";

export const BIFROST_URL: string =
  (import.meta.env.VITE_BIFROST_URL as string | undefined)?.replace(/\/$/, "") ??
  "http://localhost:8080";

export type Verdict = "current" | "check" | "outdated" | "unverifiable" | "pending";

export type ClaimClass =
  | "market"
  | "pricing"
  | "version"
  | "leadership"
  | "law"
  | "events"
  | "timeless";

export interface ScoreBreakdown {
  agreement: number;
  recency: number;
  quality: number;
  confidence: number;
  volatility: number;
  decay: number;
  base: number;
  decay_multiplier: number;
  score: number;
  supporting: number;
  contradicting: number;
  insufficient: number;
  formula: string;
}

export interface EvidenceItem {
  url: string;
  title: string;
  snippet: string;
  published_date: string | null;
  tier: "official" | "news" | "other";
  call: "supports" | "contradicts" | "insufficient";
  quote: string;
}

export interface ClaimVerdict {
  text: string;
  span_start: number;
  span_end: number;
  subject: string;
  predicate: string;
  stated_value: string;
  class: ClaimClass;
  verdict: Verdict;
  score: number;
  current_value: string | null;
  source_url: string | null;
  publish_date: string | null;
  quote: string | null;
  breakdown: ScoreBreakdown | null;
  evidence: EvidenceItem[];
  from_claim_cache: boolean;
  tavily_credits: number;
}

export interface VerifyReport {
  report_id: string;
  score: number;
  verdict: Verdict;
  claims: ClaimVerdict[];
  claim_count: number;
  stale_count: number;
  skipped: number;
  ttl_seconds: number;
  tavily_credits: number;
  degraded: boolean;
  reason: string | null;
  latency_ms: number;
  request_id: string;
  cache_hit: boolean;
  cache_id: string;
  evicted: boolean;
}

/** One row in the live feed. */
export interface FeedEvent {
  seq: number;
  type: "verdict" | "eviction" | "sweep";
  report_id?: string;
  request_id?: string;
  verdict?: Verdict;
  score?: number;
  claims?: number;
  stale?: number;
  skipped?: number;
  cache_hit?: boolean;
  cache_id?: string;
  cache_key?: string;
  ttl_seconds?: number;
  latency_ms?: number;
  credits?: number;
  model?: string;
  prompt?: string;
  degraded?: boolean;
  reason?: string;
  at?: string;
  /** sweeper */
  reverified?: number;
  evicted?: number;
}

export interface Stats {
  answers_checked: number;
  claims_checked: number;
  claims_skipped: number;
  outdated_answers: number;
  cache_evictions: number;
  stale_hits_prevented: number;
  tavily_credits_used: number;
  verify_p50_ms: number;
  verify_p95_ms: number;
  claim_cache: { backend: string; hits: number; misses: number; hit_rate: number };
  tavily: { limit: number; used: number; remaining: number; configured: boolean; live: boolean };
  evictor: { cleared: number; failed: number };
  sweeper: { runs: number; reverified: number; evicted: number; interval_s: number; enabled: boolean };
  mode: { offline_demo: boolean; extract_model: string | null; judge_model: string | null };
  thresholds: { current: number; check: number };
  uptime_s: number;
}

export interface CacheEvent {
  created_at: number;
  kind: string;
  cache_id: string;
  cache_key: string;
  report_id: string;
  score: number;
  detail: string;
}

export interface BriefLine {
  text: string;
  verdict: Verdict;
  score: number;
  source_url: string | null;
  publish_date: string | null;
  reason: string | null;
}

export interface Brief {
  subject: string;
  meeting_time: string;
  verified: BriefLine[];
  unconfirmed: BriefLine[];
  open_questions: string[];
  tavily_credits: number;
  degraded: boolean;
  reason: string | null;
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${VERIFIER_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${body.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => json<{ status: string; offline_demo: boolean; tavily_configured: boolean }>("/health"),
  stats: () => json<Stats>("/v1/stats"),
  verify: (body: Record<string, unknown>) =>
    json<VerifyReport>("/v1/verify", { method: "POST", body: JSON.stringify(body) }),
  report: (id: string) => json<VerifyReport>(`/v1/reports/${id}`),
  reports: (limit = 40) => json<{ reports: Record<string, unknown>[] }>(`/v1/reports?limit=${limit}`),
  cacheEvents: () => json<{ events: CacheEvent[] }>("/v1/cache/events"),
  brief: (subject: string, meetingTime: string) =>
    json<Brief>("/v1/brief", {
      method: "POST",
      body: JSON.stringify({ subject, meeting_time: meetingTime, days: 30 }),
    }),
  sweep: () => json<{ ok: boolean; reverified: number; evicted: number }>("/v1/sweep", { method: "POST" }),
};

/**
 * A chat completion through the gateway, returning the RAW response headers.
 *
 * The playground exists to show that the check happens in the gateway rather
 * than in this UI, so it has to display headers the browser actually received.
 * Only headers Bifrost exposes via CORS are readable; the gateway's
 * `allowed_origins` covers the console's origin.
 */
export async function chatThroughGateway(
  prompt: string,
  cacheKey: string,
  model = "mockllm/chronos-demo",
): Promise<{ status: number; headers: Record<string, string>; body: unknown; ms: number }> {
  const started = performance.now();
  const res = await fetch(`${BIFROST_URL}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-bf-cache-key": cacheKey },
    body: JSON.stringify({ model, messages: [{ role: "user", content: prompt }] }),
  });
  const headers: Record<string, string> = {};
  res.headers.forEach((value, key) => {
    if (/^x-(chronos|bifrost|bf)-/i.test(key)) headers[key] = value;
  });
  const body = await res.json().catch(() => null);
  return { status: res.status, headers, body, ms: performance.now() - started };
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

/**
 * Subscribe to the verifier's event stream.
 *
 * EventSource reconnects on its own, so this only reports status changes and
 * hands parsed events upward. Unparseable frames are dropped rather than
 * thrown: a malformed event must not take the feed down.
 */
export function subscribe(
  onEvent: (event: FeedEvent) => void,
  onStatus?: (status: "connecting" | "open" | "error") => void,
): () => void {
  let source: EventSource | null = null;
  try {
    onStatus?.("connecting");
    source = new EventSource(`${VERIFIER_URL}/v1/stream?replay=20`);
  } catch {
    onStatus?.("error");
    return () => {};
  }

  source.onopen = () => onStatus?.("open");
  source.onerror = () => onStatus?.("error");
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as FeedEvent);
    } catch {
      /* ignore a malformed frame */
    }
  };

  return () => source?.close();
}
