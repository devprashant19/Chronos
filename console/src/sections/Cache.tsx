/**
 * The cache section: what TTL Chronos chose, and what it deleted.
 *
 * The argument this makes visually is that one flat TTL fits no fact. Bifrost's
 * cache takes a single TTL; Chronos sets it per request from the answer's
 * volatility class, so a definition stays cached for a week while a stock price
 * expires in fifteen minutes.
 */

import type { CacheEvent, FeedEvent, Stats } from "../api";

const FLAT_DEFAULT_HOURS = 24;

const CLASS_TTL: Array<{ cls: string; hours: number; colour: string }> = [
  { cls: "market", hours: 0.25, colour: "var(--bad)" },
  { cls: "pricing", hours: 6, colour: "var(--warn)" },
  { cls: "version", hours: 6, colour: "var(--warn)" },
  { cls: "events", hours: 12, colour: "var(--bifrost)" },
  { cls: "leadership", hours: 24, colour: "var(--bifrost)" },
  { cls: "law", hours: 24, colour: "var(--bifrost)" },
  { cls: "timeless", hours: 168, colour: "var(--ok)" },
];

const SCALE_HOURS = 72;

export default function Cache({
  mode,
  feed,
  cacheEvents,
  stats,
}: {
  mode: "live" | "demo";
  feed: FeedEvent[];
  cacheEvents: CacheEvent[];
  stats: Stats | null;
}) {
  // In demo mode the eviction timeline is built from the replayed feed, so it
  // fills in as the demo runs rather than sitting empty.
  const evictions =
    mode === "demo"
      ? feed
          .filter((e) => e.type === "eviction")
          .map((e) => ({
            created_at: e.at ? Date.parse(e.at) / 1000 : Date.now() / 1000,
            kind: "evicted",
            cache_id: e.cache_id ?? "",
            cache_key: e.cache_key ?? "",
            report_id: e.report_id ?? "",
            score: e.score ?? 0,
            detail: "served an outdated claim",
          }))
      : cacheEvents.filter((e) => e.kind === "evicted");

  const total = stats?.cache_evictions ?? evictions.length;

  return (
    <section id="cache" className="wrap" style={{ paddingBlock: 36 }}>
      <div className="sec-head">
        <div>
          <p className="eyebrow">Cache · volatility-aware TTL</p>
          <h2 style={{ fontSize: "clamp(24px,3vw,38px)" }}>One flat TTL fits no fact</h2>
        </div>
        <p>
          Bifrost&rsquo;s semantic cache takes one TTL. Chronos sets it per request from how quickly
          the answer&rsquo;s facts change, so stable answers stay cached longer and fast-moving ones
          expire sooner. Grey is a flat {FLAT_DEFAULT_HOURS}-hour default; colour is what Chronos
          chose.
        </p>
      </div>

      <div className="cache-grid">
        <div className="panel ledger">
          <div className="l-row" style={{ borderBottom: "1px solid var(--rule)" }}>
            <span className="eyebrow">Volatility class</span>
            <span className="eyebrow">
              Flat {FLAT_DEFAULT_HOURS} h vs Chronos TTL · scale 0&ndash;{SCALE_HOURS} h
            </span>
            <span className="eyebrow" style={{ textAlign: "right" }}>
              TTL
            </span>
          </div>

          {CLASS_TTL.map((row) => (
            <div className="l-row" key={row.cls}>
              <span className="p">
                <strong>{row.cls}</strong>
              </span>
              <span style={{ display: "grid", gap: 4 }}>
                <span className="tr" style={{ height: 8, background: "var(--grid)", borderRadius: 1, overflow: "hidden" }}>
                  <i
                    style={{
                      display: "block",
                      height: "100%",
                      width: `${Math.min(100, (FLAT_DEFAULT_HOURS / SCALE_HOURS) * 100)}%`,
                      background: "var(--ink-3)",
                    }}
                  />
                </span>
                <span className="tr" style={{ height: 8, background: "var(--grid)", borderRadius: 1, overflow: "hidden" }}>
                  <i
                    style={{
                      display: "block",
                      height: "100%",
                      width: `${Math.max(1.5, Math.min(100, (row.hours / SCALE_HOURS) * 100))}%`,
                      background: row.colour,
                      transition: "width .8s cubic-bezier(.2,.7,.2,1)",
                    }}
                  />
                </span>
              </span>
              <span className="mono" style={{ textAlign: "right", fontSize: 12.5 }}>
                {formatHours(row.hours)}
              </span>
            </div>
          ))}

          <div className="l-row" style={{ borderTop: "1px solid var(--rule)" }}>
            <span className="p" style={{ gridColumn: "1 / -1", color: "var(--ink-2)", fontSize: 13 }}>
              An answer&rsquo;s TTL is the <strong>minimum</strong> across its claims. One stock
              price in an otherwise stable answer pulls the whole answer down to fifteen minutes,
              which is the conservative choice and the correct one.
            </span>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="panel" style={{ padding: "16px" }}>
            <div className="scope-head">
              <h3 style={{ margin: 0, fontSize: 15 }}>Stale hits stopped</h3>
              <span className="eyebrow">{total} evictions</span>
            </div>
            <Timeline events={evictions} />
            <p style={{ margin: "10px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
              Each mark is a cache hit that would have served an outdated fact. Chronos cleared it
              with <span className="mono">DELETE /api/cache/clear/&#123;cache_id&#125;</span>.
            </p>
          </div>

          <div className="panel" style={{ padding: "6px 0" }}>
            {evictions.length === 0 ? (
              <p style={{ padding: "14px 16px", margin: 0, color: "var(--ink-3)", fontSize: 13 }}>
                No evictions recorded yet.
              </p>
            ) : (
              evictions.slice(0, 6).map((e, i) => (
                <div className="l-row" key={`${e.cache_id}-${i}`} style={{ gridTemplateColumns: "72px minmax(0,1fr) 54px" }}>
                  <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
                    {new Date(e.created_at * 1000).toLocaleTimeString([], { hour12: false })}
                  </span>
                  <span className="p" style={{ minWidth: 0 }}>
                    <span className="mono" style={{ fontSize: 12 }}>
                      {e.cache_id.slice(0, 18)}
                    </span>
                    <span style={{ display: "block", fontSize: 12, color: "var(--ink-2)" }}>{e.detail}</span>
                  </span>
                  <span className="pill bad" style={{ justifySelf: "end" }}>
                    {Math.round(e.score)}
                  </span>
                </div>
              ))
            )}
          </div>

          {stats ? (
            <div className="panel" style={{ padding: "14px 16px" }}>
              <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>Sweeper</h3>
              <dl className="vals">
                <dt>Runs</dt>
                <dd className="mono">{stats.sweeper.runs}</dd>
                <dt>Re-verified</dt>
                <dd className="mono">{stats.sweeper.reverified}</dd>
                <dt>Evicted</dt>
                <dd className="mono">{stats.sweeper.evicted}</dd>
                <dt>Interval</dt>
                <dd className="mono">{stats.sweeper.interval_s}s</dd>
              </dl>
              <p style={{ margin: "8px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
                The sweeper re-checks the most-hit entries before their claims expire, so nobody
                pays the full verification latency on arrival.
              </p>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function Timeline({ events }: { events: Array<{ created_at: number; score: number }> }) {
  const W = 320;
  const H = 54;
  const now = Date.now() / 1000;
  const window = 24 * 3600;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cache evictions over the last 24 hours">
      <line x1="0" x2={W} y1={H / 2} y2={H / 2} stroke="var(--grid)" strokeWidth="1.5" />
      {[0, 6, 12, 18, 24].map((h) => {
        const x = W - (h / 24) * W;
        return (
          <g key={h}>
            <line x1={x} x2={x} y1={H / 2 - 4} y2={H / 2 + 4} stroke="var(--rule)" strokeWidth="1" />
            <text x={x} y={H - 2} textAnchor="middle" fontSize="8.5" fill="var(--ink-3)" className="mono">
              {h === 0 ? "now" : `-${h}h`}
            </text>
          </g>
        );
      })}
      {events.map((e, i) => {
        const age = Math.max(0, Math.min(window, now - e.created_at));
        const x = W - (age / window) * W;
        return <line key={i} x1={x} x2={x} y1={H / 2 - 12} y2={H / 2 + 12} stroke="var(--bad)" strokeWidth="2" strokeLinecap="round" />;
      })}
    </svg>
  );
}

function formatHours(h: number): string {
  if (h < 1) return `${Math.round(h * 60)}m`;
  if (h >= 24 && h % 24 === 0) return `${h / 24}d`;
  return `${h}h`;
}
