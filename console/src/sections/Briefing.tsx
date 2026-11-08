/**
 * Briefing mode: the same engine, pointed at a different problem.
 *
 * Instead of checking someone else's answer, Chronos writes one and then checks
 * itself. The section that matters is "Open questions": a brief that silently
 * drops what it could not confirm is more dangerous than one that admits it,
 * because the reader cannot tell absence from ignorance.
 */

import { useCallback, useState } from "react";

import { api, type Brief, type BriefLine } from "../api";
import { DEMO_BRIEF } from "../demo";
import { useAnnounce } from "../announce";

export default function Briefing({ mode }: { mode: "live" | "demo" }) {
  const { announce } = useAnnounce();
  const [subject, setSubject] = useState("Bifrost (Maxim AI)");
  const [meeting, setMeeting] = useState("Thu 12 Nov 2026 · 10:30 IST");
  const [brief, setBrief] = useState<Brief | null>(mode === "demo" ? DEMO_BRIEF : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(0);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    setBrief(null);
    setRevealed(0);
    announce(
      mode === "demo"
        ? "Replaying a recorded brief: it assembles line by line into verified, unconfirmed and open questions."
        : "Building the brief live: searching the last 30 days and checking each fact. This takes a minute or two on a free tier.",
    );

    try {
      const result = mode === "demo" ? DEMO_BRIEF : await api.brief(subject, meeting);
      if (mode === "demo") await new Promise((r) => setTimeout(r, 600));
      setBrief(result);
      announce(
        `Brief ready: ${result.verified.length} verified, ${result.unconfirmed.length} unconfirmed, ${result.open_questions.length} open questions to ask.`,
      );

      // Reveal line by line, so the brief visibly assembles itself.
      const total = result.verified.length + result.unconfirmed.length + result.open_questions.length;
      for (let i = 1; i <= total; i += 1) {
        await new Promise((r) => setTimeout(r, 180));
        setRevealed(i);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [mode, subject, meeting, announce]);

  let cursor = 0;
  const show = () => {
    cursor += 1;
    return cursor <= revealed;
  };

  return (
    <section id="brief" className="wrap" style={{ paddingBlock: 36 }}>
      <div className="sec-head">
        <div>
          <p className="eyebrow">Briefing mode · same engine</p>
          <h2 style={{ fontSize: "clamp(24px,3vw,38px)" }}>A meeting brief that shows its work</h2>
        </div>
        <p>
          Give Chronos a subject and a meeting time. Tavily researches the last 30 days across four
          angles, a model drafts the brief through Bifrost, and <em>every line goes back through the
          verifier</em> before you read it. Contradictions become questions to ask in the room.
        </p>
      </div>

      <div className="panel brief-head" style={{ padding: 16, marginBottom: 16, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 220px" }}>
          <span className="eyebrow">Subject</span>
          <input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            style={inputStyle}
            aria-label="Briefing subject"
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 200px" }}>
          <span className="eyebrow">Meeting</span>
          <input
            value={meeting}
            onChange={(e) => setMeeting(e.target.value)}
            style={inputStyle}
            aria-label="Meeting time"
          />
        </label>
        <button className="btn" onClick={run} disabled={busy} style={{ alignSelf: "flex-end" }}>
          {busy ? "Researching…" : "Build the brief"}
        </button>
        {error ? (
          <span className="pill bad" title={error}>
            briefing failed
          </span>
        ) : null}
      </div>

      {brief ? (
        <div className="brief-cols" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(300px,1fr))", gap: 20 }}>
          <div className="panel" style={{ padding: 18 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", marginBottom: 10 }}>
              <p className="eyebrow" style={{ margin: 0 }}>
                Verified · 80+
              </p>
              <span className="pill ok">{brief.verified.length}</span>
            </div>
            {brief.verified.length === 0 ? (
              <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
                Nothing cleared the confirmation threshold.
              </p>
            ) : (
              brief.verified.map((line) => (show() ? <Line key={line.text} line={line} tone="ok" /> : null))
            )}
          </div>

          <div className="panel" style={{ padding: 18 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", marginBottom: 10 }}>
              <p className="eyebrow" style={{ margin: 0 }}>
                Unconfirmed · with the reason
              </p>
              <span className="pill warn">{brief.unconfirmed.length}</span>
            </div>
            {brief.unconfirmed.length === 0 ? (
              <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>Everything confirmed.</p>
            ) : (
              brief.unconfirmed.map((line) =>
                show() ? <Line key={line.text} line={line} tone={line.verdict === "unverifiable" ? "mute" : "warn"} /> : null,
              )
            )}
          </div>

          <div className="panel" style={{ padding: 18 }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline", marginBottom: 10 }}>
              <p className="eyebrow" style={{ margin: 0 }}>
                Open questions
              </p>
              <span className="pill mute">{brief.open_questions.length}</span>
            </div>
            {brief.open_questions.length === 0 ? (
              <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>No contradictions found.</p>
            ) : (
              // The design's .brief-cols li is a two-column grid: a 34px mark,
              // then the text. A bare string lands in the 34px column and wraps
              // one word to a line, which is how this column rendered until
              // the screenshots were looked at. The mark is the reference's "?".
              <ul>
                {brief.open_questions.map((q) =>
                  show() ? (
                    <li key={q} style={{ fontSize: 13.5, color: "var(--ink-2)" }}>
                      <span className="sc" aria-hidden="true" style={{ color: "var(--ink-3)" }}>
                        ?
                      </span>
                      <span>{q}</span>
                    </li>
                  ) : null,
                )}
              </ul>
            )}
            <p style={{ margin: "14px 0 0", paddingTop: 10, borderTop: "1px dashed var(--rule)", fontSize: 12, color: "var(--ink-3)" }}>
              {brief.tavily_credits} Tavily credits · 4 parallel searches over 30 days
              {brief.reason ? ` · ${brief.reason}` : ""}
            </p>
          </div>
        </div>
      ) : (
        <div className="panel" style={{ padding: 20 }}>
          <p style={{ margin: 0, color: "var(--ink-3)", fontSize: 13.5 }}>
            No brief built yet. In live mode this spends Tavily credits; in demo mode it replays a
            recorded brief.
          </p>
        </div>
      )}
    </section>
  );
}

function Line({ line, tone }: { line: BriefLine; tone: string }) {
  return (
    <div style={{ padding: "10px 0", borderTop: "1px solid var(--grid)" }}>
      <p style={{ margin: "0 0 6px", fontSize: 14, lineHeight: 1.55 }}>{line.text}</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <span className={`pill ${tone}`}>
          {line.verdict} {line.score > 0 ? Math.round(line.score) : ""}
        </span>
        {line.publish_date ? <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{line.publish_date}</span> : null}
        {line.source_url ? (
          <a className="mono" style={{ fontSize: 11.5 }} href={line.source_url} target="_blank" rel="noreferrer noopener">
            source
          </a>
        ) : null}
      </div>
      {line.reason ? (
        <p style={{ margin: "6px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>{line.reason}</p>
      ) : null}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  font: "inherit",
  fontSize: 14,
  padding: "8px 10px",
  background: "var(--paper)",
  color: "var(--ink)",
  border: "1px solid var(--rule)",
  borderRadius: 4,
  width: "100%",
};
