/**
 * Every feature, working.
 *
 * Reads console/public/proof.json, written by scripts/record-proof.py against a
 * live stack, and shows each feature with the evidence that it works: the real
 * headers, report fields and counters that came back, and the date of the run.
 * A failed feature is shown failed. Nothing here is a mock-up.
 *
 * In Live mode the checks a browser can make are re-run against your stack and
 * replace the recorded evidence, marked "live, just now".
 */

import { useEffect, useState } from "react";

import { api } from "../api";
import { LIVE_CHECKS, runLive, tally, type LiveResult, type ProofFeature, type ProofFile } from "../proof";

const TONE: Record<string, string> = { pass: "ok", fail: "bad", measured: "mute" };

export default function Proof({ mode }: { mode: "live" | "demo" }) {
  const [file, setFile] = useState<ProofFile | null>(null);
  const [missing, setMissing] = useState(false);
  const [live, setLive] = useState<Record<string, LiveResult>>({});
  const [running, setRunning] = useState(false);

  useEffect(() => {
    // Beside the bundle, resolved against the build's base, like results.json.
    fetch(`${import.meta.env.BASE_URL}proof.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: ProofFile) => setFile(data))
      .catch(() => setMissing(true));
  }, []);

  const canRunLive = mode === "live" && !api.blockedByBrowser();

  async function rerun() {
    setRunning(true);
    for (const id of Object.keys(LIVE_CHECKS)) {
      const result = await runLive(id);
      setLive((prev) => ({ ...prev, [id]: result }));
    }
    setRunning(false);
  }

  const features: ProofFeature[] = (file?.features ?? []).map((f) =>
    live[f.id] ? { ...f, status: live[f.id].status, evidence: live[f.id].evidence } : f,
  );
  const counts = tally(features);

  return (
    <section id="proof" className="wrap" style={{ paddingBlock: 36 }}>
      <div className="sec-head">
        <div>
          <p className="eyebrow">Every feature, working</p>
          <h2 style={{ fontSize: "clamp(24px,3vw,38px)" }}>Run against the real stack, recorded as it came back</h2>
        </div>
        <p>
          Each card is one feature, with the headers, report fields and counters the stack actually returned.
          Written by <span className="mono">scripts/record-proof.py</span>; a feature that failed would be shown
          failed, in red.
        </p>
      </div>

      {missing ? (
        <div className="panel" style={{ padding: 20 }}>
          <p className="eyebrow" style={{ marginBottom: 6 }}>No proof file</p>
          <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 14 }}>
            <span className="mono">proof.json</span> was not found next to this build. Run{" "}
            <span className="mono">python scripts/record-proof.py</span> against a running stack and rebuild. This
            panel shows nothing it did not record.
          </p>
        </div>
      ) : !file ? (
        <div className="panel" style={{ padding: 20 }}>
          <p style={{ margin: 0, color: "var(--ink-3)" }}>Loading the recorded run…</p>
        </div>
      ) : (
        <>
          <div className="panel" style={{ padding: "14px 16px", marginBottom: 16 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <span className="pill ok">{counts.pass} working</span>
              {counts.fail ? <span className="pill bad">{counts.fail} failed</span> : null}
              {counts.measured ? <span className="pill mute">{counts.measured} measured</span> : null}
              <span className="pill mute">recorded {file.recorded_at.slice(0, 16).replace("T", " ")} UTC</span>
              <span className="pill mute">evidence: {file.stack.evidence}</span>
              {file.stack.judge_model ? <span className="pill mute">judge: {file.stack.judge_model}</span> : null}
            </div>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", marginTop: 12 }}>
              {canRunLive ? (
                <button className="btn" onClick={rerun} disabled={running}>
                  {running ? "Running…" : `Re-run ${Object.keys(LIVE_CHECKS).length} of them live, from this browser`}
                </button>
              ) : null}
              <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)", minWidth: 0, flex: "1 1 280px" }}>
                {canRunLive
                  ? "The rest need to stop a container or run inside the verifier, so they stay as recorded."
                  : "Recorded run. To re-run the browser-side checks against your own stack, start it and open http://localhost:5173 in Live mode."}
              </p>
            </div>
          </div>

          <div
            style={{ display: "grid", gap: 14, gridTemplateColumns: "repeat(auto-fill, minmax(min(320px, 100%), 1fr))" }}
          >
            {features.map((f) => (
              <Card key={f.id} feature={f} isLive={Boolean(live[f.id])} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function Card({ feature: f, isLive }: { feature: ProofFeature; isLive: boolean }) {
  return (
    <article className="panel" style={{ padding: 16, minWidth: 0 }} data-feature={f.id}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
        <span className={`pill ${TONE[f.status]}`}>{f.status === "pass" ? "working" : f.status}</span>
        {isLive ? <span className="pill warn">live, just now</span> : null}
        <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)", overflowWrap: "anywhere" }}>
          {f.promise}
        </span>
      </div>
      <h3 style={{ margin: "0 0 10px", fontSize: 17 }}>{f.title}</h3>
      <dl style={{ margin: 0, display: "grid", gap: 6 }}>
        {f.evidence.map((e, i) => (
          <div key={`${e.label}-${i}`} style={{ minWidth: 0 }}>
            <dt style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{e.label}</dt>
            <dd className="mono" style={{ margin: 0, fontSize: 12.5, overflowWrap: "anywhere" }}>
              {e.value}
            </dd>
          </div>
        ))}
      </dl>
      <p style={{ margin: "10px 0 0", fontSize: 13, color: "var(--ink-2)" }}>{f.note}</p>
      <a className="d" href={f.section} style={{ display: "inline-block", marginTop: 8, fontSize: 13 }}>
        See it on this page →
      </a>
    </article>
  );
}
