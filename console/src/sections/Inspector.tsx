/**
 * The Inspector: paste an answer, watch the claims light up.
 *
 * This is where a score stops being a number and becomes an argument. Every
 * claim is highlighted in place, and selecting one opens the breakdown: each
 * term of the formula, the sources, what they said, and when they said it.
 *
 * In live mode it posts to the real verifier. In demo mode it replays a
 * recorded report — through exactly the same rendering path.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, type ClaimVerdict, type VerifyReport } from "../api";
import { DEMO_ANSWER, DEMO_REPORT } from "../demo";
import { useAnnounce } from "../announce";

const PRESETS: Array<{ label: string; text: string }> = [
  { label: "Stale version", text: DEMO_ANSWER },
  {
    label: "Stale leadership",
    text:
      "Jack Dorsey is the CEO of Twitter. He co-founded the company and returned as chief executive in 2015. Twitter remains a publicly traded company on the New York Stock Exchange.",
  },
  {
    label: "Stale pricing",
    text:
      "OpenAI's GPT-4o is priced at $5.00 per million input tokens and $15.00 per million output tokens. There is no free tier for the API.",
  },
  {
    label: "All timeless",
    text:
      "The capital of France is Paris. Water boils at 100 degrees Celsius at standard atmospheric pressure. A stack is a last-in, first-out data structure.",
  },
];

const CLASS_OF_VERDICT: Record<string, string> = {
  current: "ok",
  check: "warn",
  outdated: "bad",
  unverifiable: "warn",
  pending: "warn",
};

export default function Inspector({ mode }: { mode: "live" | "demo" }) {
  const [text, setText] = useState(DEMO_ANSWER);
  const [report, setReport] = useState<VerifyReport | null>(mode === "demo" ? DEMO_REPORT : null);
  const [selected, setSelected] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [log, setLog] = useState<Array<{ tone: string; line: string }>>([]);

  const answerRef = useRef<HTMLDivElement>(null);
  const { announce } = useAnnounce();

  useEffect(() => {
    if (mode === "demo") {
      setReport(DEMO_REPORT);
      setText(DEMO_ANSWER);
      setSelected(0);
    }
  }, [mode]);

  const run = useCallback(async () => {
    setError(null);
    setBusy(true);
    setScanning(true);
    setLog([{ tone: "b", line: "› chronos: extracting time-sensitive claims" }]);

    try {
      if (mode === "demo") {
        // Pace the recorded report so the scan reads as work being done.
        await new Promise((r) => setTimeout(r, 700));
        setLog((l) => [...l, { tone: "t", line: "› tavily: search + extract (recorded)" }]);
        await new Promise((r) => setTimeout(r, 700));
        setReport(DEMO_REPORT);
        setSelected(0);
        setLog((l) => [
          ...l,
          { tone: "g", line: "› chronos: 4 claims, 2 outdated" },
          { tone: "x", line: "› chronos: cache entry evicted (stale_hit_prevented)" },
        ]);
      } else {
        const result = await api.verify({ answer: text, mode: "annotate", prompt: "console inspector" });
        setReport(result);
        setSelected(0);
        setLog([
          { tone: "b", line: `› chronos: ${result.claim_count} claims, ${result.skipped} skipped` },
          { tone: "t", line: `› tavily: ${result.tavily_credits} credits spent` },
          {
            tone: result.verdict === "outdated" ? "x" : "g",
            line: `› chronos: verdict ${result.verdict}, score ${Math.round(result.score)}, ttl ${result.ttl_seconds}s`,
          },
        ]);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLog((l) => [...l, { tone: "x", line: "› verifier unreachable — the gateway would pass this through as unverifiable" }]);
    } finally {
      setBusy(false);
      window.setTimeout(() => setScanning(false), 900);
    }
  }, [mode, text]);

  const claims = report?.claims ?? [];
  const current = claims[selected] ?? null;

  // Render the answer with each claim's span highlighted in place.
  const segments = useMemo(() => renderSegments(text, claims), [text, claims]);

  return (
    <section id="inspect" className="wrap" style={{ paddingBlock: 36 }}>
      <div className="sec-head">
        <div>
          <p className="eyebrow">Inspector · stale-answer detector</p>
          <h2 style={{ fontSize: "clamp(24px,3vw,38px)" }}>Paste an answer, watch the claims light up</h2>
        </div>
        <p>
          Chronos finds the sentences that depend on today&rsquo;s date, checks each one and scores
          it. Green is current, amber needs a human look, red is outdated. Grey facts never change
          and are skipped, which is what saves Tavily credits. Select any claim to see why it scored
          what it did.
        </p>
      </div>

      <div className="presets">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            className="btn ghost"
            style={{ fontSize: 12.5, padding: "6px 10px" }}
            onClick={() => {
              setText(p.text);
              setReport(null);
              setLog([]);
              announce(`Loaded the "${p.label}" example into the answer box. Press Check this answer to score its claims.`);
            }}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="insp-grid">
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="panel answer" ref={answerRef}>
            {scanning ? <span className="scanline" style={{ animation: "scan 1.6s ease-in-out" }} /> : null}

            <div className="meta">
              <span className="eyebrow">
                {/* The panel opens on the recorded example in either mode. In
                    Live mode, under a header that says LIVE, that needs saying,
                    or a recording reads as a live result until someone presses
                    the button. */}
                {report === DEMO_REPORT && mode === "live"
                  ? "Recorded example · Check again runs it live"
                  : report?.cache_hit
                    ? "Cached answer · served from Bifrost"
                    : "Answer under inspection"}
              </span>
              {report ? (
                <span className={`pill ${CLASS_OF_VERDICT[report.verdict] ?? "mute"}`}>
                  {report.verdict} · {Math.round(report.score)}
                </span>
              ) : null}
            </div>

            {report ? (
              <div className="txt">
                {segments.map((seg, i) =>
                  seg.claimIndex === null ? (
                    <span key={i}>{seg.text}</span>
                  ) : (
                    <button
                      key={i}
                      className={`claim ${
                        claims[seg.claimIndex].class === "timeless"
                          ? "time"
                          : CLASS_OF_VERDICT[claims[seg.claimIndex].verdict] ?? "warn"
                      } ${seg.claimIndex === selected ? "sel" : ""}`}
                      onClick={() => {
                        const c = claims[seg.claimIndex!];
                        setSelected(seg.claimIndex!);
                        announce(
                          `Showing ${c.subject}, ${c.predicate}: ${c.verdict}, scored ${Math.round(c.score)}. The score drawer beside the answer shows every term and the pages behind it.`,
                        );
                      }}
                      aria-label={`Claim: ${claims[seg.claimIndex].subject} ${claims[seg.claimIndex].predicate}, scored ${Math.round(claims[seg.claimIndex].score)}`}
                    >
                      {seg.text}
                    </button>
                  ),
                )}
              </div>
            ) : (
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={7}
                style={{
                  width: "100%",
                  font: "inherit",
                  fontSize: 16,
                  lineHeight: 1.7,
                  background: "transparent",
                  color: "var(--ink)",
                  border: "1px solid var(--rule)",
                  borderRadius: 4,
                  padding: 12,
                  resize: "vertical",
                }}
                aria-label="Answer to check"
              />
            )}

            <div className="log">
              {log.length === 0 ? (
                <span style={{ color: "var(--ink-3)" }}>› idle</span>
              ) : (
                log.map((l, i) => (
                  <div key={i} className={l.tone}>
                    {l.line}
                  </div>
                ))
              )}
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button
              className="btn"
              onClick={() => {
                announce(
                  mode === "live"
                    ? "Checking the answer live: claims are extracted, searched with Tavily and scored. The log below fills in as it goes."
                    : "Replaying a recorded check: the claims light up and the score drawer fills in. Switch to Live to check it for real.",
                );
                void run();
              }}
              disabled={busy}
            >
              {busy ? "Checking…" : report ? "Check again" : "Check this answer"}
            </button>
            {report ? (
              <button
                className="btn ghost"
                onClick={() => {
                  setReport(null);
                  setLog([]);
                  announce("The answer is editable again. Change any sentence, then check it.");
                }}
              >
                Edit the answer
              </button>
            ) : null}
            {report ? (
              <span className="eyebrow">
                {report.claim_count} claims · {report.skipped} skipped · {report.tavily_credits} credits ·{" "}
                {report.latency_ms} ms
              </span>
            ) : null}
            {error ? (
              <span className="pill bad" title={error}>
                verifier unreachable
              </span>
            ) : null}
          </div>
        </div>

        <ScoreDrawer claim={current} />
      </div>
    </section>
  );
}

export function ScoreDrawer({ claim }: { claim: ClaimVerdict | null }) {
  if (!claim) {
    return (
      <div className="panel drawer">
        <p className="eyebrow">Score breakdown</p>
        <p style={{ color: "var(--ink-2)", fontSize: 13.5, margin: 0 }}>
          Check an answer, then select a highlighted claim to see every term that produced its
          score.
        </p>
      </div>
    );
  }

  const b = claim.breakdown;
  const tone =
    claim.class === "timeless" ? "mute" : CLASS_OF_VERDICT[claim.verdict] ?? "warn";

  return (
    <div className="panel drawer">
      <div className="dial-row">
        <Dial score={claim.score} tone={tone} />
        <div>
          <p className="eyebrow" style={{ marginBottom: 4 }}>
            {claim.class} · {claim.verdict}
          </p>
          <p style={{ margin: 0, fontWeight: 600 }}>
            {claim.subject} — {claim.predicate}
          </p>
          {claim.from_claim_cache ? (
            <span className="pill mute" style={{ marginTop: 6 }}>
              from claim cache · 0 credits
            </span>
          ) : null}
          {claim.claim_cache_bypassed ? (
            <span className="pill warn" style={{ marginTop: 6 }} title={claim.claim_cache_bypassed}>
              claim cache bypassed · ambiguous subject
            </span>
          ) : null}
        </div>
      </div>

      <dl className="vals">
        <dt>Stated</dt>
        <dd>{claim.stated_value || <em style={{ color: "var(--ink-3)" }}>—</em>}</dd>
        <dt>Current</dt>
        <dd>{claim.current_value || <em style={{ color: "var(--ink-3)" }}>not established</em>}</dd>
        {claim.publish_date ? (
          <>
            <dt>Dated</dt>
            <dd className="mono">{claim.publish_date}</dd>
          </>
        ) : null}
      </dl>

      {b ? (
        <>
          <div className="bars">
            <Bar label="A agreement" value={b.agreement} />
            <Bar label="R recency" value={b.recency} />
            <Bar label="Q quality" value={b.quality} />
            <Bar label="C confidence" value={b.confidence} />
            <Bar label="V volatility" value={b.volatility} />
            <Bar label="D decay" value={b.decay} />
          </div>
          <p className="formula">{b.formula}</p>
          <p style={{ margin: 0, fontSize: 12.5, color: "var(--ink-2)" }}>
            {b.supporting} supporting · {b.contradicting} contradicting · {b.insufficient} said
            nothing either way
          </p>
        </>
      ) : (
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>
          {claim.class === "timeless"
            ? "Timeless claim: skipped without spending a Tavily credit. That skip is the cheapest correctness win in the system."
            : "No breakdown: this claim could not be checked, so it is reported unverifiable rather than outdated."}
        </p>
      )}

      {claim.evidence.length > 0 ? (
        <div className="sources">
          {claim.evidence.slice(0, 4).map((e) => (
            <div className="src-item" key={e.url}>
              <span className={`pill ${e.call === "supports" ? "ok" : e.call === "contradicts" ? "bad" : "mute"}`}>
                {e.call}
              </span>
              <span style={{ minWidth: 0 }}>
                <a className="d" href={e.url} target="_blank" rel="noreferrer noopener">
                  {hostOf(e.url)}
                </a>
                <span className="pill mute" style={{ marginLeft: 6 }}>
                  {e.tier}
                </span>
                {e.injection_flags && e.injection_flags.length > 0 ? (
                  <>
                    <span className="pill bad" style={{ marginLeft: 6 }}>
                      set aside
                    </span>
                    <span className="s" style={{ display: "block" }}>
                      This page tried to instruct the judge ({e.injection_flags.join(", ")}), so it is
                      not counted as evidence.
                    </span>
                  </>
                ) : null}
                {e.quote ? <span className="s" style={{ display: "block" }}>“{e.quote}”</span> : null}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Bar({ label, value }: { label: string; value: number }) {
  return (
    <div className="bar">
      <span>{label}</span>
      <span className="tr">
        <i style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
      </span>
      <span className="v">{value.toFixed(2)}</span>
    </div>
  );
}

function Dial({ score, tone }: { score: number; tone: string }) {
  const R = 30;
  const C = 2 * Math.PI * R;
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const colour =
    tone === "ok" ? "var(--ok)" : tone === "bad" ? "var(--bad)" : tone === "warn" ? "var(--warn)" : "var(--ink-3)";

  return (
    <span className="dial">
      <svg width="78" height="78" viewBox="0 0 78 78" role="img" aria-label={`Score ${Math.round(score)} out of 100`}>
        <circle cx="39" cy="39" r={R} fill="none" stroke="var(--grid)" strokeWidth="7" />
        <circle
          cx="39"
          cy="39"
          r={R}
          fill="none"
          stroke={colour}
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={`${C * pct} ${C}`}
          transform="rotate(-90 39 39)"
          style={{ transition: "stroke-dasharray .8s cubic-bezier(.2,.7,.2,1)" }}
        />
        <text x="39" y="44" textAnchor="middle" fontSize="20" fontWeight="700" fill="var(--ink)" className="tab">
          {Math.round(score)}
        </text>
      </svg>
    </span>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 40);
  }
}

/**
 * Split the answer into plain and claim-highlighted segments.
 *
 * Spans are clamped and sorted, and overlaps are dropped rather than nested:
 * a model occasionally returns overlapping spans, and a half-open highlight
 * would corrupt the rendered answer.
 */
function renderSegments(
  text: string,
  claims: ClaimVerdict[],
): Array<{ text: string; claimIndex: number | null }> {
  if (claims.length === 0) return [{ text, claimIndex: null }];

  const spans = claims
    .map((c, i) => ({
      start: Math.max(0, Math.min(text.length, c.span_start)),
      end: Math.max(0, Math.min(text.length, c.span_end)),
      i,
    }))
    .filter((s) => s.end > s.start)
    .sort((a, b) => a.start - b.start);

  const out: Array<{ text: string; claimIndex: number | null }> = [];
  let cursor = 0;

  for (const span of spans) {
    if (span.start < cursor) continue; // overlapping: keep the first
    if (span.start > cursor) out.push({ text: text.slice(cursor, span.start), claimIndex: null });
    out.push({ text: text.slice(span.start, span.end), claimIndex: span.i });
    cursor = span.end;
  }
  if (cursor < text.length) out.push({ text: text.slice(cursor), claimIndex: null });

  return out;
}
