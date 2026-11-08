/**
 * Every feature, working.
 *
 * Reads console/public/proof.json, written by scripts/record-proof.py against a
 * live stack, and shows each feature with the evidence that it works: the real
 * headers, report fields and counters that came back, and the date of the run.
 * A failed feature is shown failed. Nothing here is a mock-up.
 *
 * One row of cards, scrolled sideways, so twenty-one features take the height
 * of one. Each card shows its first three values with long ones cut to three
 * lines; the rest are one click away inside the card.
 *
 * In Live mode the checks a browser can make are re-run against your stack and
 * replace the recorded evidence, marked "live, just now".
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api";
import { useAnnounce } from "../announce";
import { LIVE_CHECKS, runLive, tally, type LiveResult, type ProofFeature, type ProofFile } from "../proof";
import { arrivalMessage } from "../sectionInfo";

const TONE: Record<string, string> = { pass: "ok", fail: "bad", measured: "mute" };

/** Values shown on a card before "All N values". */
export const SHOWN = 3;

export default function Proof({ mode }: { mode: "live" | "demo" }) {
  const [file, setFile] = useState<ProofFile | null>(null);
  const [missing, setMissing] = useState(false);
  const [live, setLive] = useState<Record<string, LiveResult>>({});
  const [running, setRunning] = useState(false);
  const [view, setView] = useState({ first: 1, last: 1 });
  const rail = useRef<HTMLDivElement>(null);
  const { announce } = useAnnounce();

  useEffect(() => {
    // Beside the bundle, resolved against the build's base, like results.json.
    fetch(`${import.meta.env.BASE_URL}proof.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: ProofFile) => setFile(data))
      .catch(() => setMissing(true));
  }, []);

  const canRunLive = mode === "live" && !api.blockedByBrowser();

  const features: ProofFeature[] = (file?.features ?? []).map((f) =>
    live[f.id] ? { ...f, status: live[f.id].status, evidence: live[f.id].evidence } : f,
  );
  const counts = tally(features);

  // Which cards are in view, for the "4–6 of 21" counter.
  const measure = useCallback(() => {
    const el = rail.current;
    if (!el || !el.children.length) return;
    const box = el.getBoundingClientRect();
    let first = 0;
    let last = 0;
    Array.from(el.children).forEach((child, i) => {
      const r = (child as HTMLElement).getBoundingClientRect();
      const visible = r.right > box.left + 8 && r.left < box.right - 8;
      if (visible && !first) first = i + 1;
      if (visible) last = i + 1;
    });
    if (first) setView({ first, last });
    el.classList.toggle("more-left", el.scrollLeft > 4);
    el.classList.toggle("at-end", el.scrollLeft + el.clientWidth >= el.scrollWidth - 4);
  }, []);

  useEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure, features.length]);

  const step = (direction: 1 | -1) => {
    const el = rail.current;
    if (!el) return;
    const card = el.firstElementChild as HTMLElement | null;
    const width = card ? card.getBoundingClientRect().width + 14 : 300;
    el.scrollBy?.({ left: direction * width * Math.max(1, Math.floor(el.clientWidth / width)), behavior: "smooth" });
  };

  async function rerun() {
    setRunning(true);
    const ids = Object.keys(LIVE_CHECKS);
    announce(`Re-running ${ids.length} checks against your stack from this browser. Each card turns amber as its live result replaces the recorded one.`);
    let passed = 0;
    for (const id of ids) {
      const result = await runLive(id);
      if (result.status === "pass") passed += 1;
      setLive((prev) => ({ ...prev, [id]: result }));
    }
    setRunning(false);
    announce(`Done: ${passed} of ${ids.length} passed live, just now. The other cards are still the recorded run.`);
    // Bring the first re-run card into view.
    const first = rail.current?.querySelector<HTMLElement>(`[data-feature="${ids[0]}"]`);
    first?.scrollIntoView?.({ behavior: "smooth", inline: "start", block: "nearest" });
  }

  return (
    <section id="proof" className="wrap" style={{ paddingBlock: 36 }}>
      <div className="sec-head">
        <div>
          <p className="eyebrow">Every feature, working</p>
          <h2 style={{ fontSize: "clamp(24px,3vw,38px)" }}>Run against the real stack, recorded as it came back</h2>
        </div>
        <p>
          One card per feature, with the headers and counters the stack actually returned. Written by{" "}
          <span className="mono">scripts/record-proof.py</span>; a feature that failed would show in red. Scroll
          sideways, or use the arrows.
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
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 12 }}>
            <span className="pill ok">{counts.pass} working</span>
            {counts.fail ? <span className="pill bad">{counts.fail} failed</span> : null}
            {counts.measured ? <span className="pill mute">{counts.measured} measured</span> : null}
            <span className="pill mute">recorded {file.recorded_at.slice(0, 16).replace("T", " ")} UTC</span>
            <span className="pill mute">evidence: {file.stack.evidence}</span>
            {file.stack.judge_model ? <span className="pill mute">judge: {file.stack.judge_model}</span> : null}
            <span style={{ flex: "1 1 0" }} />
            {canRunLive ? (
              <button className="btn" onClick={rerun} disabled={running} style={{ padding: "6px 12px", fontSize: 13 }}>
                {running ? "Running…" : `Re-run ${Object.keys(LIVE_CHECKS).length} live`}
              </button>
            ) : null}
            <div className="rail-nav">
              <span className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }} aria-live="polite">
                {view.first === view.last ? view.first : `${view.first}–${view.last}`} of {features.length}
              </span>
              <button className="btn ghost" onClick={() => step(-1)} aria-label="Previous features" style={{ padding: "5px 9px" }}>
                ←
              </button>
              <button className="btn ghost" onClick={() => step(1)} aria-label="Next features" style={{ padding: "5px 9px" }}>
                →
              </button>
            </div>
          </div>

          <div
            className="proof-rail"
            ref={rail}
            onScroll={measure}
            tabIndex={0}
            role="region"
            aria-label="Every feature, one card each; scroll sideways"
          >
            {features.map((f) => (
              <Card
                key={f.id}
                feature={f}
                isLive={Boolean(live[f.id])}
                onSee={() => announce(arrivalMessage(f.section), f.section.replace(/^#/, ""))}
              />
            ))}
          </div>

          <p style={{ margin: "6px 0 0", fontSize: 12.5, color: "var(--ink-3)" }}>
            {canRunLive
              ? "The re-run covers what a browser can check; the rest need to stop a container or run inside the verifier, so they stay as recorded."
              : "Recorded run. To re-run the browser-side checks against your own stack, start it and open http://localhost:5173 in Live mode."}
          </p>
        </>
      )}
    </section>
  );
}

function Card({ feature: f, isLive, onSee }: { feature: ProofFeature; isLive: boolean; onSee: () => void }) {
  const shown = f.evidence.slice(0, SHOWN);
  const rest = f.evidence.slice(SHOWN);
  return (
    <article className={`panel proof-card${isLive ? " is-live" : ""}`} data-feature={f.id}>
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginBottom: 6 }}>
        <span className={`pill ${TONE[f.status]}`}>{f.status === "pass" ? "working" : f.status}</span>
        {isLive ? <span className="pill warn">live, just now</span> : null}
      </div>
      <h3 style={{ margin: "0 0 4px", fontSize: 15.5, lineHeight: 1.25 }}>{f.title}</h3>
      <p className="mono" style={{ margin: "0 0 10px", fontSize: 11.5, color: "var(--ink-3)", overflowWrap: "anywhere" }}>
        {f.promise}
      </p>
      <dl style={{ margin: 0, display: "grid", gap: 5 }}>
        {shown.map((e, i) => (
          <Value key={`${e.label}-${i}`} label={e.label} value={e.value} />
        ))}
      </dl>
      {rest.length || f.note ? (
        <details>
          <summary>{rest.length ? `All ${f.evidence.length} values, and why` : "Why"}</summary>
          <dl style={{ margin: "6px 0 0", display: "grid", gap: 5 }}>
            {rest.map((e, i) => (
              <Value key={`${e.label}-r${i}`} label={e.label} value={e.value} />
            ))}
          </dl>
          <p style={{ margin: "8px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>{f.note}</p>
        </details>
      ) : null}
      <a
        className="d"
        href={f.section}
        onClick={(e) => {
          e.preventDefault();
          onSee();
        }}
        style={{ display: "inline-block", marginTop: 8, fontSize: 12.5 }}
      >
        See it on this page →
      </a>
    </article>
  );
}

function Value({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ minWidth: 0 }}>
      <dt style={{ fontSize: 11, color: "var(--ink-3)" }}>{label}</dt>
      <dd className="mono clamp" style={{ margin: 0, fontSize: 12, overflowWrap: "anywhere" }} title={value}>
        {value}
      </dd>
    </div>
  );
}
