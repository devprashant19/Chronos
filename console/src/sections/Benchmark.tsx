/**
 * The benchmark section.
 *
 * It reads bench/results.json — the file the runner writes — and renders
 * whatever is in it. There are no placeholder figures in this component: if the
 * file is missing it says so rather than showing something plausible, because a
 * project about stale facts cannot ship invented numbers about itself.
 *
 * The caveats are rendered as prominently as the numbers, from the results
 * file's own `configuration` block, since a precision figure measured with
 * rule-based judging means something different from one measured with a model.
 */

import { useEffect, useState } from "react";

interface Detection {
  precision: number;
  recall: number;
  f1: number;
  tp: number;
  fp: number;
  fn: number;
  tn?: number;
  accuracy?: number;
  n?: number;
}

interface ModeResult {
  mode: string;
  label: string;
  n: number;
  usable: number;
  errors: number;
  outdated_detection: Detection;
  per_class: Record<string, Detection>;
  timeless: { n: number; skipped_without_credits: number; skip_rate: number };
  latency_ms: { p50: number; p95: number; mean: number; max: number };
  tavily_credits: { total: number; per_1000_answers: number };
  claim_cache_hits: number;
  unverifiable: number;
}

interface Results {
  benchmark: string;
  dataset_version: string;
  dataset_date: string;
  run: { started_at: string; duration_s: number; claims: number; verifier: string };
  configuration: {
    offline_demo: boolean | null;
    tavily_configured: boolean | null;
    claim_cache_backend: string | null;
    evidence_source: string;
    note: string;
  };
  modes: Record<string, ModeResult>;
  claim_cache_effect: {
    cold_credits_per_1000_answers: number;
    warm_credits_per_1000_answers: number | null;
    credit_saving_fraction: number | null;
    measured: boolean;
    note: string;
  };
  replay: { replayed_cache_hits?: number; stale_hits_prevented?: number; prevention_rate?: number; measured?: boolean; note?: string };
  label_disagreements: { count: number; note: string };
}

export default function Benchmark() {
  const [results, setResults] = useState<Results | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    // Served as a static asset next to the console build.
    fetch("/results.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: Results) => setResults(data))
      .catch(() => setMissing(true));
  }, []);

  return (
    <section id="bench" className="wrap" style={{ paddingBlock: 36 }}>
      <div className="sec-head">
        <div>
          <p className="eyebrow">StaleBench · 120 hand-labelled claims</p>
          <h2 style={{ fontSize: "clamp(24px,3vw,38px)" }}>Measured, not promised</h2>
        </div>
        <p>
          The repo ships a benchmark: 40 claims known to be stale, 40 believed current, 40 timeless.
          Everything below is read from <span className="mono">bench/results.json</span>, written by{" "}
          <span className="mono">bench/runner.py</span>. Nothing here is a target.
        </p>
      </div>

      {missing ? (
        <div className="panel" style={{ padding: 20 }}>
          <p className="eyebrow" style={{ marginBottom: 6 }}>
            No results file
          </p>
          <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 14 }}>
            <span className="mono">bench/results.json</span> was not found next to this build, so
            there is nothing measured to show. Run{" "}
            <span className="mono">python bench/runner.py --measure-cache --replay</span> and
            rebuild. This panel deliberately shows no placeholder numbers.
          </p>
        </div>
      ) : !results ? (
        <div className="panel" style={{ padding: 20 }}>
          <p style={{ margin: 0, color: "var(--ink-3)" }}>Loading measured results…</p>
        </div>
      ) : (
        <Rendered results={results} />
      )}
    </section>
  );
}

function Rendered({ results }: { results: Results }) {
  const primary = Object.values(results.modes)[0];
  const det = primary.outdated_detection;
  const cache = results.claim_cache_effect;

  return (
    <>
      <div className="panel" style={{ padding: "14px 16px", marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span className="pill mute">dataset {results.dataset_version}</span>
          <span className="pill mute">{results.run.claims} claims</span>
          <span className={`pill ${results.configuration.evidence_source === "tavily" ? "ok" : "warn"}`}>
            evidence: {results.configuration.evidence_source}
          </span>
          <span className="pill mute">mode: {primary.mode}</span>
          <span className="pill mute">{Math.round(results.run.duration_s)}s run</span>
        </div>
        <p style={{ margin: "10px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
          {results.configuration.note}
        </p>
      </div>

      <div className="bench-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))", gap: 20 }}>
        <div className="panel bench-card" style={{ padding: 18 }}>
          <p className="eyebrow" style={{ marginBottom: 10 }}>
            Detecting &ldquo;outdated&rdquo;
          </p>
          <div className="bars">
            <Bar label="precision" value={det.precision} />
            <Bar label="recall" value={det.recall} />
            <Bar label="F1" value={det.f1} />
          </div>
          <p style={{ margin: "12px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
            {det.tp} true positives · {det.fp} false positives · {det.fn} missed
          </p>
        </div>

        <div className="panel bench-card" style={{ padding: 18 }}>
          <p className="eyebrow" style={{ marginBottom: 10 }}>
            Timeless skip rate
          </p>
          <b className="tab" style={{ font: "800 46px/1 var(--display)" }}>
            {Math.round(primary.timeless.skip_rate * 100)}%
          </b>
          <p style={{ margin: "8px 0 0", fontSize: 13, color: "var(--ink-2)" }}>
            {primary.timeless.skipped_without_credits} of {primary.timeless.n} timeless claims cost
            no Tavily credit. This is the cheapest correctness win in the system.
          </p>
        </div>

        <div className="panel bench-card" style={{ padding: 18 }}>
          <p className="eyebrow" style={{ marginBottom: 10 }}>
            Latency
          </p>
          <dl className="vals">
            <dt>p50</dt>
            <dd className="mono">{Math.round(primary.latency_ms.p50)} ms</dd>
            <dt>p95</dt>
            <dd className="mono">{Math.round(primary.latency_ms.p95)} ms</dd>
            <dt>mode</dt>
            <dd className="mono">{primary.mode}</dd>
          </dl>
          <p style={{ margin: "10px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
            Measured at the verifier. In <span className="mono">annotate</span> mode a fresh answer
            is verified asynchronously, so the gateway adds a goroutine spawn, not this.
          </p>
        </div>

        <div className="panel bench-card" style={{ padding: 18 }}>
          <p className="eyebrow" style={{ marginBottom: 10 }}>
            Tavily credits per 1,000 answers
          </p>
          {cache.measured && cache.warm_credits_per_1000_answers !== null ? (
            <>
              <div className="bars">
                <Bar
                  label="cold cache"
                  value={1}
                  display={String(Math.round(cache.cold_credits_per_1000_answers))}
                />
                <Bar
                  label="warm cache"
                  value={
                    cache.cold_credits_per_1000_answers
                      ? cache.warm_credits_per_1000_answers / cache.cold_credits_per_1000_answers
                      : 0
                  }
                  display={String(Math.round(cache.warm_credits_per_1000_answers))}
                />
              </div>
              <p style={{ margin: "12px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
                The claim cache avoided{" "}
                <strong>{Math.round((cache.credit_saving_fraction ?? 0) * 100)}%</strong> of credits
                on a repeat pass.
              </p>
            </>
          ) : (
            <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>{cache.note}</p>
          )}
        </div>

        {Object.keys(primary.per_class).length > 0 ? (
          <div className="panel bench-card" style={{ padding: 18, gridColumn: "span 2" }}>
            <p className="eyebrow" style={{ marginBottom: 10 }}>
              Precision and recall by volatility class
            </p>
            <div style={{ display: "grid", gap: 10 }}>
              {Object.entries(primary.per_class).map(([cls, d]) => (
                <div key={cls} className="bar" style={{ gridTemplateColumns: "92px minmax(0,1fr) 92px" }}>
                  <span>{cls}</span>
                  <span className="tr">
                    <i style={{ width: `${d.precision * 100}%` }} />
                  </span>
                  <span className="v">
                    P{d.precision.toFixed(2)} R{d.recall.toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
            <p style={{ margin: "12px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
              Mechanical comparisons (versions, prices) score well. Deciding who currently holds a
              role is reading comprehension, so it is where a rule-based judge loses the most.
            </p>
          </div>
        ) : null}

        {results.replay?.measured !== false && results.replay?.replayed_cache_hits ? (
          <div className="panel bench-card" style={{ padding: 18 }}>
            <p className="eyebrow" style={{ marginBottom: 10 }}>
              Replayed traffic
            </p>
            <b className="tab" style={{ font: "800 46px/1 var(--display)" }}>
              {results.replay.stale_hits_prevented}
            </b>
            <p style={{ margin: "8px 0 0", fontSize: 13, color: "var(--ink-2)" }}>
              stale cache hits caught out of {results.replay.replayed_cache_hits} replayed, each one
              an answer a user would otherwise have been given.
            </p>
          </div>
        ) : null}

        <div className="panel bench-card" style={{ padding: 18 }}>
          <p className="eyebrow" style={{ marginBottom: 10 }}>
            Label disagreements
          </p>
          <b className="tab" style={{ font: "800 46px/1 var(--display)" }}>
            {results.label_disagreements.count}
          </b>
          <p style={{ margin: "8px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
            {results.label_disagreements.note}
          </p>
        </div>
      </div>
    </>
  );
}

function Bar({ label, value, display }: { label: string; value: number; display?: string }) {
  return (
    <div className="bar">
      <span>{label}</span>
      <span className="tr">
        <i style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
      </span>
      <span className="v">{display ?? value.toFixed(2)}</span>
    </div>
  );
}
