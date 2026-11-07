import { useCallback, useEffect, useState } from "react";

import { BIFROST_URL, VERIFIER_URL } from "./api";
import { useChronos } from "./useChronos";
import Hero from "./sections/Hero";
import RunIt from "./sections/RunIt";
import Traffic from "./sections/Traffic";
import Walkthrough from "./sections/Walkthrough";
import Inspector from "./sections/Inspector";
import Cache from "./sections/Cache";
import Playground from "./sections/Playground";
import Benchmark from "./sections/Benchmark";
import Briefing from "./sections/Briefing";

const SECTIONS = [
  ["path", "Signal path"],
  ["run", "Run it"],
  ["traffic", "Traffic"],
  ["inspect", "Inspector"],
  ["cache", "Cache"],
  ["playground", "Playground"],
  ["bench", "Benchmark"],
  ["brief", "Briefing"],
] as const;

/** The eight steps one request takes, and who owns each. */
const STEPS: Array<{ owner: "b" | "t" | "c"; title: string; body: string }> = [
  { owner: "b", title: "Request reaches the gateway", body: "A client calls /v1/chat/completions. Bifrost resolves the virtual key, the provider and the model." },
  { owner: "c", title: "Chronos classifies the prompt's volatility", body: "HTTPTransportPreHook, rules only: a few regex scans, no model call. The class decides the cache TTL — 15 minutes for a market question, a week for a definition." },
  { owner: "b", title: "The semantic cache is consulted", body: "Bifrost looks the request up under its cache key. A hit returns an answer written at some earlier time, which is the moment staleness can be served." },
  { owner: "b", title: "On a miss, the provider is called", body: "Normal gateway routing, with fallbacks. On a hit this step is skipped entirely — no tokens spent." },
  { owner: "c", title: "Chronos extracts the time-sensitive claims", body: "PostLLMHook, after the cache has had its say. Each claim gets a subject, a predicate, the value the answer stated, and a volatility class. Timeless sentences are skipped." },
  { owner: "t", title: "Tavily brings the evidence", body: "One advanced search per claim-cache miss, official domains first, then extract for the full page text of the top three results. A claim-cache hit costs nothing." },
  { owner: "c", title: "Each claim is judged and scored 0–100", body: "Agreement, recency, source quality and judge confidence, discounted by how fast this kind of fact moves and how long ago it was checked." },
  { owner: "b", title: "The verdict rides back on the response", body: "x-chronos-* headers are written before headers commit, so streaming clients see them too. An outdated cached answer is evicted with DELETE /api/cache/clear/{cache_id}." },
];

/** Decay curves: how much of a cached fact's value survives over 7 days. */

export default function App() {
  const { mode, chooseMode, connection, probe, feed, counters, stats, cacheEvents, headline } =
    useChronos();
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);

  // The walkthrough is null when it is not running, so "not started" and
  // "on step 0" are different states.
  const [tourStep, setTourStep] = useState<number | null>(null);

  // Drives the scroll cue at the bottom of the opening screen. It is the only
  // thing telling a first-time viewer that the page continues.
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    if (theme) document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 40);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const scrollOn = useCallback(() => {
    document.getElementById("path")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  return (
    <>
      <header className={`top${scrolled ? "" : " on-dark"}`}>
        <div className="wrap">
          <div className="brand">
            {/* The reference mark, design/chronos-console.html line 249: a dial with
                the hour hand, struck through in --bad. The slash is the point of
                the product and is not decoration, so it is kept here. It fills in
                below about 32px, which is why the favicon and app tile
                (design/icon.svg) redraw the same idea as a red quadrant instead. */}
            <svg width="22" height="22" viewBox="0 0 26 26" aria-hidden="true">
              <circle cx="13" cy="13" r="11.5" fill="none" stroke="currentColor" strokeWidth="2" />
              <path d="M13 5v8l5.5 3.2" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="square" />
              <path d="M3 21 23 5" stroke="var(--bad)" strokeWidth="2" />
            </svg>
            Chronos
          </div>

          <nav className="sections">
            {SECTIONS.map(([id, label]) => (
              <a key={id} href={`#${id}`}>
                {label}
              </a>
            ))}
          </nav>

          <ModeSwitch mode={mode} chooseMode={chooseMode} connection={connection} probe={probe} />

          <button
            className="btn ghost"
            style={{ padding: "5px 9px", fontSize: 12 }}
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            aria-label="Toggle colour scheme"
          >
            {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </header>

      <main>
        <Hero
          headline={headline}
          onStartTour={() => setTourStep(0)}
          onScrollOn={scrollOn}
          cueHidden={scrolled || tourStep !== null}
        />

        <SignalPath />

        <RunIt mode={mode} />

        <Traffic mode={mode} feed={feed} counters={counters} stats={stats} />
        <Inspector mode={mode} />
        <Cache mode={mode} feed={feed} cacheEvents={cacheEvents} stats={stats} />
        <Playground mode={mode} />
        <Benchmark />
        <Briefing mode={mode} />
      </main>

      <footer
        className="wrap"
        style={{
          padding: "40px 0 56px",
          borderTop: "1px solid var(--rule)",
          marginTop: 48,
          // The walkthrough bar is fixed to the bottom; without this it covers
          // the end of the page on the last step.
          paddingBottom: tourStep !== null ? 200 : 56,
        }}
      >
        <p className="eyebrow" style={{ marginBottom: 8 }}>
          Chronos · Bifrost plugin + Tavily verification
        </p>
        <p style={{ color: "var(--ink-2)", fontSize: 13.5, margin: 0 }}>
          {mode === "demo" ? (
            <>
              <strong>Demo mode.</strong> Traffic on this page is a recorded replay, labelled as
              such. The benchmark section reads measured results from{" "}
              <span className="mono">bench/results.json</span>. Switch to Live to drive a running
              verifier.
            </>
          ) : (
            <>
              <strong>Live mode.</strong> Reading <span className="mono">{VERIFIER_URL}</span> and{" "}
              <span className="mono">{BIFROST_URL}</span>.
            </>
          )}
        </p>
      </footer>

      {tourStep !== null ? (
        <Walkthrough index={tourStep} onIndex={setTourStep} onExit={() => setTourStep(null)} />
      ) : null}
    </>
  );
}

function ModeSwitch({
  mode,
  chooseMode,
  connection,
  probe,
}: {
  mode: "live" | "demo";
  chooseMode: (m: "live" | "demo") => void;
  connection: string;
  probe: { reachable: boolean; offline: boolean; tavily: boolean } | null;
}) {
  const label =
    mode === "demo"
      ? "Recorded demo replay"
      : connection === "open"
        ? probe?.offline
          ? "Live · recorded evidence"
          : probe?.tavily
            ? "Live · Tavily"
            : "Live"
        : connection === "error"
          ? "Live · reconnecting"
          : "Live · connecting";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span className="replay">
        <i style={{ background: mode === "demo" ? "var(--warn)" : connection === "open" ? "var(--ok)" : "var(--bad)" }} />
        {label}
      </span>
      <div style={{ display: "flex", gap: 4 }}>
        <button
          className="btn ghost"
          style={{ padding: "5px 9px", fontSize: 12 }}
          aria-pressed={mode === "live"}
          onClick={() => chooseMode("live")}
        >
          Live
        </button>
        <button
          className="btn ghost"
          style={{ padding: "5px 9px", fontSize: 12 }}
          aria-pressed={mode === "demo"}
          onClick={() => chooseMode("demo")}
        >
          Demo
        </button>
      </div>
    </div>
  );
}

/**
 * The decay scope: how much of a cached fact's value survives as it ages.
 *
 * A moving time marker sweeps the window so the comparison is felt rather than
 * read — at day 3 a market fact is worthless and a definition is untouched,
 * which is the whole argument for a per-class TTL.
 */

/** The eight-step signal path, auto-advancing, each step owned by a product. */
function SignalPath() {
  const [active, setActive] = useState(0);
  const [auto, setAuto] = useState(true);

  useEffect(() => {
    if (!auto) return;
    const timer = window.setInterval(() => setActive((i) => (i + 1) % STEPS.length), 3400);
    return () => window.clearInterval(timer);
  }, [auto]);

  const ownerLabel = (owner: "b" | "t" | "c") =>
    owner === "b" ? "Bifrost" : owner === "t" ? "Tavily" : "Chronos";

  return (
    <section id="path" className="wrap" style={{ paddingBlock: 36 }}>
      <div className="sec-head">
        <div>
          <p className="eyebrow">Signal path · one request</p>
          <h2 style={{ fontSize: "clamp(24px,3vw,38px)" }}>Who does what on every answer</h2>
        </div>
        <p>
          Follow one question through the gateway. The colour of each step shows who owns it:
          Bifrost runs the gateway, Tavily brings the evidence, Chronos adds the judgement. Pick any
          step to hold it.
        </p>
      </div>

      <div className="pipe-grid">
        <div className="panel diagram">
          <PathDiagram active={active} />
        </div>

        <div className="panel steps">
          <div className="progress">
            <i style={{ width: `${((active + 1) / STEPS.length) * 100}%`, transition: "width .4s" }} />
          </div>
          <ol>
            {STEPS.map((step, i) => (
              <li key={step.title} className={i === active ? "on" : ""}>
                <button
                  onClick={() => {
                    setActive(i);
                    setAuto(false);
                  }}
                  aria-current={i === active}
                >
                  <span>
                    <span className="st-title">
                      {step.title}
                      <span className={`own ${step.owner}`}>{ownerLabel(step.owner)}</span>
                    </span>
                    <span className="st-body">{step.body}</span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className="steps-foot">
            <button className="btn ghost" onClick={() => setAuto((a) => !a)} aria-pressed={auto}>
              {auto ? "Pause" : "Play"}
            </button>
            <span className="eyebrow">
              {auto ? "Advancing every 3.4 s" : `Holding step ${String(active + 1).padStart(2, "0")}`}
            </span>
          </div>
        </div>
      </div>

      <div className="owners">
        <div>
          <span className="own b">Bifrost</span>
          <h3 style={{ margin: 0, fontSize: 17 }}>The gateway every app already calls</h3>
          <ul>
            <li>Hosts Chronos as a Go plugin on four hooks</li>
            <li>Semantic cache with a per-request TTL Chronos sets</li>
            <li>Cache-clear API that Chronos uses to evict</li>
            <li>Routes, budgets and falls back for Chronos&rsquo;s own model calls</li>
          </ul>
          <p className="per">Per answer: 1 gateway pass + up to 2 internal model calls</p>
        </div>
        <div>
          <span className="own t">Tavily</span>
          <h3 style={{ margin: 0, fontSize: 17 }}>The live web, as evidence</h3>
          <ul>
            <li>Search with a recent date window, official domains first</li>
            <li>Extract pulls the page text the judge actually reads</li>
            <li>Powers the meeting briefing</li>
            <li>Only called on a claim-cache miss</li>
          </ul>
          <p className="per">Per new claim: 1 search + 1 extract</p>
        </div>
        <div>
          <span className="own c">Chronos</span>
          <h3 style={{ margin: 0, fontSize: 17 }}>The judgement in between</h3>
          <ul>
            <li>Extracts typed, time-sensitive claims with exact spans</li>
            <li>Volatility classes decide re-check time and cache TTL</li>
            <li>Scores each claim 0&ndash;100 and explains the score</li>
            <li>Annotates, evicts, regenerates or blocks, per virtual key</li>
          </ul>
          <p className="per">Headers: x-chronos-score · -verdict · -ttl · -report</p>
        </div>
      </div>
    </section>
  );
}

/** The route a request takes, with the active leg lit. */
function PathDiagram({ active }: { active: number }) {
  // Which diagram node each of the eight steps lights up.
  const litNode = [0, 1, 2, 3, 1, 4, 1, 0][active];

  const nodes = [
    { id: 0, x: 20, y: 60, w: 110, h: 54, label: "Client", owner: "c" },
    { id: 1, x: 170, y: 60, w: 130, h: 54, label: "Chronos", owner: "c" },
    { id: 2, x: 170, y: 150, w: 130, h: 48, label: "Semantic cache", owner: "b" },
    { id: 3, x: 350, y: 150, w: 120, h: 48, label: "Provider", owner: "b" },
    { id: 4, x: 350, y: 60, w: 120, h: 54, label: "Tavily", owner: "t" },
  ];

  const colourFor = (owner: string) =>
    owner === "b" ? "var(--bifrost)" : owner === "t" ? "var(--tavily)" : "var(--ink)";

  return (
    <svg viewBox="0 0 500 230" role="img" aria-label="The route one request takes through the gateway">
      <rect x="150" y="34" width="330" height="180" rx="6" fill="none" stroke="var(--rule)" strokeDasharray="4 4" />
      <text x="156" y="28" fontSize="10" className="mono" fill="var(--bifrost)">
        BIFROST GATEWAY
      </text>

      <path className={`wire ${[0, 1, 7].includes(active) ? "lit" : ""}`} d="M130 87 H170" />
      <path className={`wire ${[2, 4].includes(active) ? "lit" : ""}`} d="M235 114 V150" />
      <path className={`wire ${active === 3 ? "lit" : ""}`} d="M300 174 H350" />
      <path className={`wire ${[5, 6].includes(active) ? "lit" : ""}`} d="M300 87 H350" />

      {nodes.map((n) => (
        <g key={n.id} className={`node ${litNode === n.id ? "lit" : ""}`}>
          <rect
            className="box"
            x={n.x}
            y={n.y}
            width={n.w}
            height={n.h}
            rx="5"
            fill="var(--panel)"
            stroke={litNode === n.id ? colourFor(n.owner) : "var(--rule)"}
            strokeWidth={litNode === n.id ? 2.5 : 1.25}
          />
          <text
            x={n.x + n.w / 2}
            y={n.y + n.h / 2 + 4}
            textAnchor="middle"
            fontSize="12.5"
            fontWeight={litNode === n.id ? 600 : 400}
            fill="var(--ink)"
          >
            {n.label}
          </text>
        </g>
      ))}

      {/* A travelling dot on the active leg. */}
      <circle r="4" fill={colourFor(nodes[litNode]?.owner ?? "c")}>
        <animate attributeName="opacity" values="0;1;1;0" dur="3.4s" repeatCount="indefinite" />
        <animateMotion dur="3.4s" repeatCount="indefinite" path="M130 87 H170 V114 V150 H300 H350" />
      </circle>
    </svg>
  );
}
