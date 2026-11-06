/**
 * The live traffic feed.
 *
 * Driven entirely by the event stream, so it is the same component in live and
 * demo mode. Cache hits are marked, because those are the rows that can be
 * stale — a fresh answer is wrong only if the model was wrong, while a cached
 * answer can be wrong simply because time passed.
 */

import type { FeedEvent, Stats } from "../api";
import type { LiveCounters } from "../useChronos";

const VERDICT_PILL: Record<string, string> = {
  current: "ok",
  check: "warn",
  outdated: "bad",
  unverifiable: "mute",
  pending: "mute",
};

export default function Traffic({
  mode,
  feed,
  counters,
  stats,
}: {
  mode: "live" | "demo";
  feed: FeedEvent[];
  counters: LiveCounters;
  stats: Stats | null;
}) {
  const verdicts = feed.filter((e) => e.type === "verdict");

  // Prefer the service's own totals when it has them; the feed-derived
  // counters only cover what this page has seen since it loaded.
  const answers = stats?.answers_checked ?? counters.answers;
  const claims = stats?.claims_checked ?? counters.claims;
  const outdated = stats?.outdated_answers ?? counters.outdated;
  const evictions = stats?.cache_evictions ?? counters.evictions;
  const credits = stats?.tavily_credits_used ?? counters.credits;

  return (
    <section id="traffic" className="wrap" style={{ paddingBlock: 36 }}>
      <div className="sec-head">
        <div>
          <p className="eyebrow">Overview · live traffic</p>
          <h2 style={{ fontSize: "clamp(24px,3vw,38px)" }}>Every answer, scored as it passes</h2>
        </div>
        <p>
          Requests through one gateway. Blue rows came from Bifrost&rsquo;s semantic cache and spent
          no tokens &mdash; those are the ones most likely to be stale.{" "}
          {mode === "demo" ? (
            <strong>Figures here are a recorded replay.</strong>
          ) : (
            <>Figures are read from the running verifier.</>
          )}
        </p>
      </div>

      <div className="feed-grid">
        <div className="panel feed">
          <div className="feed-head">
            <span>Time</span>
            <span>Prompt</span>
            <span>Source</span>
            <span>Verdict</span>
            <span style={{ textAlign: "right" }}>Score</span>
          </div>
          <div className="feed-body">
            {verdicts.length === 0 ? (
              <p style={{ padding: "18px 14px", color: "var(--ink-3)", fontSize: 13 }}>
                Waiting for traffic. Send a request through the gateway, or switch to Demo mode.
              </p>
            ) : (
              verdicts.map((event) => (
                <div className="feed-row" key={event.seq}>
                  <span className="t">
                    {event.at ? new Date(event.at).toLocaleTimeString([], { hour12: false }) : "--:--:--"}
                  </span>
                  <span className="q" title={event.prompt ?? ""}>
                    {event.prompt || <em style={{ color: "var(--ink-3)" }}>(no prompt recorded)</em>}
                  </span>
                  <span className={`src ${event.cache_hit ? "hit" : ""}`}>
                    {event.cache_hit ? "cache" : "provider"}
                  </span>
                  <span>
                    <span className={`pill ${VERDICT_PILL[event.verdict ?? "pending"] ?? "mute"}`}>
                      {event.verdict}
                    </span>
                  </span>
                  <span className="sc">
                    {event.verdict === "pending" || event.verdict === "unverifiable"
                      ? "—"
                      : Math.round(event.score ?? 0)}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="panel metrics">
            <div className="metric">
              <b className="tab">{answers.toLocaleString()}</b>
              <span>Answers checked</span>
            </div>
            <div className="metric">
              <b className="tab">{claims.toLocaleString()}</b>
              <span>Claims checked</span>
            </div>
            <div className="metric">
              <b className="tab">{outdated.toLocaleString()}</b>
              <span>Outdated answers caught</span>
            </div>
            <div className="metric">
              <b className="tab">{evictions.toLocaleString()}</b>
              <span>Cache entries evicted</span>
            </div>
            <div className="metric">
              <b className="tab">{credits.toLocaleString()}</b>
              <span>Tavily credits used</span>
            </div>
            <div className="metric">
              <b className="tab">
                {stats ? `${Math.round(stats.claim_cache.hit_rate * 100)}%` : "—"}
              </b>
              <span>Claim cache hit rate</span>
            </div>
          </div>

          <div className="panel spark">
            <div className="scope-head">
              <h3 style={{ margin: 0, fontSize: 15 }}>Stale catches per 10 s</h3>
              <span className="eyebrow">last 5 min</span>
            </div>
            <Spark values={counters.catchesPerBucket} />
            <p style={{ margin: "8px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
              {counters.outdated === 0
                ? "No stale answers caught yet in this session."
                : `${counters.outdated} caught since this page loaded.`}
            </p>
          </div>

          {stats ? (
            <div className="panel" style={{ padding: "14px 16px" }}>
              <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>Verification latency</h3>
              <dl className="vals">
                <dt>p50</dt>
                <dd className="mono">{Math.round(stats.verify_p50_ms)} ms</dd>
                <dt>p95</dt>
                <dd className="mono">{Math.round(stats.verify_p95_ms)} ms</dd>
                <dt>Budget</dt>
                <dd className="mono">
                  {stats.tavily.remaining}/{stats.tavily.limit} credits left
                </dd>
                <dt>Claim cache</dt>
                <dd className="mono">{stats.claim_cache.backend}</dd>
              </dl>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function Spark({ values }: { values: number[] }) {
  const W = 320;
  const H = 64;
  const max = Math.max(1, ...values);
  const barW = W / values.length;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Stale answers caught, per ten-second bucket">
      <line x1="0" x2={W} y1={H - 1} y2={H - 1} stroke="var(--grid)" strokeWidth="1" />
      {values.map((v, i) => {
        const h = v === 0 ? 0 : Math.max(2, (v / max) * (H - 6));
        return (
          <rect
            key={i}
            x={i * barW + 0.6}
            y={H - 1 - h}
            width={Math.max(1.5, barW - 1.2)}
            height={h}
            fill={v > 0 ? "var(--bad)" : "transparent"}
            rx="1"
          />
        );
      })}
    </svg>
  );
}
