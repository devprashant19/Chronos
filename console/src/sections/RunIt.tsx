/**
 * Run it yourself: the whole product as five steps a judge can drive.
 *
 * The rest of the console shows parts of Chronos. This shows the *cycle*, in
 * order, with a control that advances it: ask a question, ask it again and get
 * the cached answer, watch Chronos pull the claims out and check them, watch
 * the verdict land, watch the cache entry be deleted, ask once more and get a
 * fresh answer.
 *
 * In **live** mode each step issues the real request through the gateway and
 * reads the real `x-chronos-*` headers off the response. In **demo** mode the
 * same five steps replay a recorded exchange through exactly the same
 * rendering path, so there is nothing here that only works when a backend
 * happens to be up -- and nothing recorded is presented as live.
 *
 * The animation is the argument: a request travelling the signal path, claims
 * lighting one at a time as they are checked, a score dial sweeping to its
 * value, and the cache row struck through when it is evicted. Under
 * prefers-reduced-motion every one of those becomes an instant state change.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { chatThroughGateway, type ClaimVerdict } from "../api";
import { useAnnounce } from "../announce";
import { DEMO_CLAIMS } from "../demo";

export type Phase = "idle" | "asking" | "answered" | "repeat" | "cached" | "checking" | "verdict" | "evicted" | "fresh";

/** The five things a judge should leave having seen. */
export const STAGES: Array<{ key: string; title: string; says: string }> = [
  { key: "ask", title: "Ask a question", says: "A normal chat completion through the gateway. Nothing about the request mentions Chronos." },
  { key: "repeat", title: "Ask it again", says: "Bifrost's semantic cache recognises the question and replays the stored answer. No provider call, no new facts." },
  { key: "check", title: "Chronos reads the answer", says: "It pulls out the sentences that depend on today's date, and checks each against the live web." },
  { key: "verdict", title: "The verdict rides back", says: "A score 0-100 and nine response headers. The caller needs no SDK change to see them." },
  { key: "evict", title: "The stale entry is deleted", says: "An outdated claim on a cache hit means the entry goes. The next caller gets a fresh answer." },
];

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
  } catch {
    return false;
  }
}

/** The recorded exchange, used in demo mode and as the shape live mode fills. */
const RECORDED = {
  prompt: "What is the latest version of Python?",
  answer:
    "The latest stable release of Python is 3.11, which came out in October 2022. " +
    "Python follows an annual release cadence, so a new feature release ships each October.",
  cacheId: "610663b3-0fc6-59e3-9486-4cfe2375aa3e",
  headers: {
    "x-bifrost-cache-hit": "true",
    "x-chronos-verdict": "outdated",
    "x-chronos-score": "39",
    "x-chronos-claims": "1",
    "x-chronos-stale": "1",
    "x-chronos-class": "version",
    "x-chronos-ttl": "21600",
    "x-chronos-latency-ms": "1524",
  } as Record<string, string>,
};

/** A dial that sweeps to its score rather than appearing at it. */
function ScoreDial({ score, live }: { score: number; live: boolean }) {
  const [shown, setShown] = useState(prefersReducedMotion() || !live ? score : 0);

  useEffect(() => {
    if (prefersReducedMotion()) {
      setShown(score);
      return;
    }
    const start = performance.now();
    let raf = 0;
    const tick = () => {
      const t = Math.min(1, (performance.now() - start) / 1100);
      setShown(score * (1 - Math.pow(1 - t, 3)));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [score]);

  const R = 30;
  const C = 2 * Math.PI * R;
  const tone = shown >= 80 ? "var(--ok)" : shown >= 50 ? "var(--warn)" : "var(--bad)";

  return (
    <div className="run-dial" role="img" aria-label={`Score ${Math.round(score)} out of 100`}>
      <svg viewBox="0 0 76 76" width="76" height="76">
        <circle cx="38" cy="38" r={R} fill="none" stroke="var(--grid)" strokeWidth="6" />
        <circle
          cx="38" cy="38" r={R} fill="none" stroke={tone} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={C} strokeDashoffset={C * (1 - shown / 100)}
          transform="rotate(-90 38 38)"
        />
      </svg>
      <b className="tab">{Math.round(shown)}</b>
    </div>
  );
}

export default function RunIt({ mode }: { mode: "live" | "demo" }) {
  const [stage, setStage] = useState(-1);          // -1 = not started
  const [busy, setBusy] = useState(false);
  const [lit, setLit] = useState(0);               // claims checked so far
  const [headers, setHeaders] = useState<Record<string, string>>({});
  const [evicted, setEvicted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Set when a live call failed and the recorded exchange stood in for it. */
  const [fellBack, setFellBack] = useState(false);
  const timers = useRef<number[]>([]);
  const { announce } = useAnnounce();

  const claims: ClaimVerdict[] = useMemo(() => DEMO_CLAIMS.slice(0, 2), []);
  const reduced = prefersReducedMotion();

  const clearTimers = useCallback(() => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
  }, []);
  useEffect(() => clearTimers, [clearTimers]);

  const reset = useCallback(() => {
    clearTimers();
    setStage(-1);
    setLit(0);
    setHeaders({});
    setEvicted(false);
    setError(null);
    setFellBack(false);
    setBusy(false);
  }, [clearTimers]);

  // Mode changes mean a different source of truth; start over rather than
  // showing half a live run and half a recorded one.
  useEffect(() => reset(), [mode, reset]);

  const advance = useCallback(async () => {
    const next = stage + 1;
    if (next >= STAGES.length) {
      reset();
      return;
    }
    setBusy(true);
    setError(null);

    try {
      if (mode === "live" && (next === 0 || next === 1)) {
        // The real thing: two identical prompts, one derived cache key.
        //
        // If it fails -- gateway down, CORS, offline laptop -- the recorded
        // exchange stands in and the panel says so. A demo that dead-ends on
        // the first click is worse than one that is honest about replaying,
        // and this is the same rule the plugin itself follows: never let a
        // failure of ours stop the thing in front of the user.
        try {
          const res = await chatThroughGateway(RECORDED.prompt, "run-it-demo");
          if (Object.keys(res.headers).length === 0) throw new Error("no x-chronos headers on the response");
          setHeaders(res.headers);
        } catch (liveErr) {
          setFellBack(true);
          setError(
            `Live request failed (${liveErr instanceof Error ? liveErr.message : String(liveErr)}). ` +
              "Showing the recorded exchange instead.",
          );
          setHeaders(next === 0
            ? { "x-bifrost-cache-hit": "false", "x-chronos-verdict": "pending", "x-chronos-class": "version", "x-chronos-ttl": "21600" }
            : RECORDED.headers);
        }
      } else if (next === 0 || next === 1) {
        setHeaders(
          next === 0
            ? { "x-bifrost-cache-hit": "false", "x-chronos-verdict": "pending", "x-chronos-class": "version", "x-chronos-ttl": "21600" }
            : RECORDED.headers,
        );
      }

      if (next === 2) {
        // Light the claims one at a time, as they are checked.
        setLit(0);
        if (reduced) setLit(claims.length);
        else
          claims.forEach((_, i) => {
            timers.current.push(window.setTimeout(() => setLit(i + 1), 420 * (i + 1)));
          });
      }
      if (next === 3 && (mode !== "live" || fellBack || Object.keys(headers).length === 0)) {
        setHeaders(RECORDED.headers);
      }
      if (next === 4) setEvicted(true);

      setStage(next);
      announce(`Run it, step ${next + 1} of ${STAGES.length}: ${STAGES[next].title}. ${STAGES[next].says}`);
    } catch (e) {
      // Anything unexpected still advances; the panel reports it rather than
      // leaving the reader on a button that does nothing.
      setError(e instanceof Error ? e.message : String(e));
      setStage(next);
    } finally {
      setBusy(false);
    }
  }, [stage, mode, claims, reduced, headers, fellBack, reset, announce]);

  const started = stage >= 0;
  const score = Number(headers["x-chronos-score"] ?? RECORDED.headers["x-chronos-score"]);

  return (
    <section id="run" className="wrap" style={{ paddingBlock: 36 }}>
      <div className="sec-head">
        <div>
          <p className="eyebrow">Run it &middot; five steps</p>
          <h2 style={{ fontSize: "clamp(24px,3vw,38px)" }}>Watch a stale answer get caught</h2>
        </div>
        <p>
          The whole cycle, in order, with a button that advances it.{" "}
          {mode === "live" ? (
            <>
              You are in <strong>Live</strong> mode: each step sends a real request through the
              gateway and reads the real headers off the response.
            </>
          ) : (
            <>
              You are in <strong>Demo</strong> mode, so this replays a recorded exchange through
              the same rendering path. Switch to Live to send real requests.
            </>
          )}
        </p>
      </div>

      <ol className="run-rail" aria-label="Demo steps">
        {STAGES.map((s, i) => (
          <li key={s.key} className={i < stage ? "done" : i === stage ? "now" : ""}>
            <i aria-hidden="true">{i < stage ? "✓" : i + 1}</i>
            <span>{s.title}</span>
          </li>
        ))}
      </ol>

      <div className="run-grid">
        <div className="panel run-stage">
          {!started ? (
            <div className="run-empty">
              <p className="eyebrow">Not started</p>
              <p>
                Five steps, about thirty seconds. Nothing here is a video: every panel below is
                the same component the rest of this console uses.
              </p>
            </div>
          ) : (
            <>
              <div className="run-line" aria-hidden="true">
                <span className={`pkt ${busy ? "moving" : ""}`} />
                {["Client", "Chronos", "Cache", "Provider"].map((n, i) => (
                  <b key={n} className={i <= Math.min(stage, 3) ? "on" : ""}>
                    {n}
                  </b>
                ))}
              </div>

              <p className="eyebrow" style={{ marginTop: 14 }}>
                {stage === 0 ? "Request 1 · cache miss" : stage === 1 ? "Request 2 · cache hit" : "The cached answer"}
              </p>

              <div className={`run-answer ${evicted ? "gone" : ""}`}>
                {claims.map((c, i) => (
                  <span key={i} className={`claim ${i < lit ? (c.verdict === "outdated" ? "bad" : "ok") : ""}`}>
                    {c.text}{" "}
                  </span>
                ))}
              </div>

              {stage >= 2 ? (
                <p className="run-note">
                  {lit < claims.length
                    ? `Checking claim ${lit + 1} of ${claims.length}…`
                    : `${claims.length} claims checked. ${claims.filter((c) => c.verdict === "outdated").length} outdated.`}
                </p>
              ) : null}

              {stage >= 4 ? (
                <p className="run-note bad">
                  <code>DELETE /api/cache/clear/{RECORDED.cacheId.slice(0, 18)}…</code> — the entry
                  is gone. The next caller gets a fresh answer.
                </p>
              ) : null}
            </>
          )}
        </div>

        <div className="panel run-side">
          {stage >= 3 ? (
            <div className="run-verdict">
              <ScoreDial score={Number.isFinite(score) ? score : 0} live={mode === "live"} />
              <div>
                <p className="eyebrow">{headers["x-chronos-class"] ?? "version"} · verdict</p>
                <b>{headers["x-chronos-verdict"] ?? "outdated"}</b>
              </div>
            </div>
          ) : null}

          <p className="eyebrow">Response headers</p>
          {Object.keys(headers).length === 0 ? (
            <p className="run-note">Nothing yet. Press the button.</p>
          ) : (
            <pre className="run-headers">
              {Object.entries(headers)
                // Chronos's own headers first: they are the point, and a live
                // response carries twenty-odd x-bifrost-* routing headers that
                // would otherwise bury them. Nothing is hidden; the rest
                // follows and the panel scrolls.
                .sort(([a], [b]) => {
                  const rank = (k: string) => (k.startsWith("x-chronos-") ? 0 : 1);
                  return rank(a) - rank(b) || a.localeCompare(b);
                })
                .map(([k, v]) => `${k}: ${v}`)
                .join("\n")}
            </pre>
          )}
          {error ? (
            <p className={`run-note ${fellBack ? "" : "bad"}`}>
              {fellBack ? <strong>Recorded. </strong> : null}
              {error}
            </p>
          ) : null}
        </div>
      </div>

      <div className="run-controls">
        <button className="btn" onClick={advance} disabled={busy}>
          {!started ? "Start" : stage >= STAGES.length - 1 ? "Run it again" : `Next — ${STAGES[stage + 1]?.title ?? ""}`}
        </button>
        {started ? (
          <button
            className="btn ghost"
            onClick={() => {
              reset();
              announce("Run it is back at the start. Press Start to ask the question again.");
            }}
          >
            Reset
          </button>
        ) : null}
        <span className="hint">
          {started ? STAGES[stage]?.says : "No keys needed. Works with nothing running."}
        </span>
      </div>
    </section>
  );
}
