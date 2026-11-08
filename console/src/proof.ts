/**
 * "Every feature, working": the record, and the part a browser can re-run.
 *
 * `console/public/proof.json` is written by `scripts/record-proof.py`, which
 * runs every feature against a live stack and records what came back — the
 * headers, the report fields, the counters — and whether that was what the
 * feature promises. The section replays that file, so the hosted demo shows a
 * real run rather than a mock-up, with the date it was recorded.
 *
 * In Live mode a subset is re-run from this browser. Only the checks a browser
 * can make honestly are here: anything that needs to stop a container, run
 * inside the verifier, or send a header the gateway's CORS preflight refuses
 * stays recorded, and says so.
 */

import { api, chatThroughGateway } from "./api";

export type ProofStatus = "pass" | "fail" | "measured";

export interface ProofEvidence {
  label: string;
  value: string;
}

export interface ProofFeature {
  id: string;
  title: string;
  /** The console section that shows this feature, as an anchor. */
  section: string;
  /** What the feature promises, in a few words. */
  promise: string;
  status: ProofStatus;
  evidence: ProofEvidence[];
  note: string;
  seconds?: number;
}

export interface ProofFile {
  recorded_at: string;
  recorded_by: string;
  stack: Record<string, string | null>;
  summary: Partial<Record<ProofStatus, number>>;
  features: ProofFeature[];
}

export interface LiveResult {
  status: Exclude<ProofStatus, "measured">;
  evidence: ProofEvidence[];
}

const STALE_ANSWER = "The latest stable release of Python is 3.11.";

const show = (value: unknown): string => (typeof value === "string" ? value : JSON.stringify(value));
const ev = (label: string, value: unknown): ProofEvidence => ({ label, value: show(value) });

/** The checks a browser can make against the stack itself, keyed by feature id. */
export const LIVE_CHECKS: Record<string, () => Promise<LiveResult>> = {
  cors: async () => {
    const res = await chatThroughGateway("hello", "");
    const verdict = res.headers["x-chronos-verdict"];
    return {
      status: res.status === 200 && Boolean(verdict) ? "pass" : "fail",
      evidence: [ev("status", res.status), ev("x-chronos-verdict, read by this browser", verdict ?? "not readable")],
    };
  },
  timeless: async () => {
    const r = await api.verify({ answer: "The capital of France is Paris.", mode: "annotate" });
    return {
      status: r.tavily_credits === 0 && r.verdict === "current" ? "pass" : "fail",
      evidence: [ev("verdict", r.verdict), ev("tavily_credits", r.tavily_credits), ev("claims skipped", r.skipped)],
    };
  },
  "claim-cache": async () => {
    await api.verify({ answer: STALE_ANSWER });
    const second = await api.verify({ answer: STALE_ANSWER });
    const fromCache = second.claims[0]?.from_claim_cache === true;
    return {
      status: fromCache && second.tavily_credits === 0 ? "pass" : "fail",
      evidence: [ev("second check · from_claim_cache", fromCache), ev("second check · credits", second.tavily_credits)],
    };
  },
  observability: async () => {
    const s = (await api.stats()) as unknown as Record<string, unknown>;
    const ok = "guards" in s && "model_pacing" in s;
    return { status: ok ? "pass" : "fail", evidence: [ev("guards", s.guards ?? "missing"), ev("claim_cache", s.claim_cache)] };
  },
  "retention-live": async () => {
    const s = (await api.stats()) as unknown as Record<string, number | Record<string, unknown>>;
    const stored = Number(s.reports_stored);
    const pruned = Number(s.reports_pruned);
    const ok = Number(s.answers_checked) === stored + pruned;
    return {
      status: ok ? "pass" : "fail",
      evidence: [ev("answers_checked", s.answers_checked), ev("reports_stored + reports_pruned", `${stored} + ${pruned}`),
        ev("retention_days", (s.sweeper as Record<string, unknown> | undefined)?.retention_days ?? "missing")],
    };
  },
  sweeper: async () => {
    const r = await api.sweep();
    return { status: r.ok ? "pass" : "fail", evidence: [ev("POST /v1/sweep", r)] };
  },
};

/** Run one live check, turning any failure into a recorded `fail`. */
export async function runLive(id: string): Promise<LiveResult> {
  const check = LIVE_CHECKS[id];
  if (!check) return { status: "fail", evidence: [ev("error", `no live check for ${id}`)] };
  try {
    return await check();
  } catch (err) {
    return { status: "fail", evidence: [ev("error", err instanceof Error ? err.message : String(err))] };
  }
}

/** Count features by status, from the features themselves rather than a stored summary. */
export function tally(features: ProofFeature[]): Record<ProofStatus, number> {
  const out: Record<ProofStatus, number> = { pass: 0, fail: 0, measured: 0 };
  for (const f of features) out[f.status] += 1;
  return out;
}
