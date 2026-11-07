/**
 * The opening stage.
 *
 * This was inline in App.tsx and static. It is the first and sometimes only
 * thing a judge sees, so it is its own component now, and it moves.
 *
 * The animation is doing a job rather than decorating. Three things:
 *
 *   - The copy reveals in reading order, so the eye is led down to the two
 *     buttons rather than landing in the middle of a wall of type.
 *   - The counters count up. They are real figures from /v1/stats (or the
 *     recorded log in demo mode), and watching them arrive says "this is a
 *     running system" in a way a static number cannot.
 *   - The decay scope sweeps a week of cache age on a loop. That curve IS the
 *     argument -- a fact's value falls off at a rate its class decides -- so
 *     it is the one thing on the page that must not be a still image.
 *
 * All of it is off under prefers-reduced-motion: the copy appears, the
 * counters show their final values, and the scope holds at day three rather
 * than sweeping. Nothing is lost but the movement.
 */

import { useEffect, useMemo, useRef, useState } from "react";

export interface HeroCounters {
  answers: number;
  stalePrevented: number;
  cacheSaving: number;
}

export function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
  } catch {
    return false;
  }
}

/**
 * Count from 0 to `value` once, on a rAF clock.
 *
 * Deliberately re-runs when the value changes: in live mode the counters move
 * as traffic arrives, and a count-up that only ran on mount would freeze at
 * whatever the first poll happened to return.
 */
function useCountUp(value: number, ms = 900): number {
  const [shown, setShown] = useState(() => (prefersReducedMotion() ? value : 0));
  const from = useRef(0);

  useEffect(() => {
    if (prefersReducedMotion()) {
      setShown(value);
      return;
    }
    const start = performance.now();
    const base = from.current;
    let raf = 0;

    // Elapsed is read from the same clock `start` came from, rather than
    // from the timestamp the host hands rAF. The two are not guaranteed to
    // share an origin -- under jsdom they do not -- and when they disagree the
    // count never reaches its target and the most prominent number on the page
    // stays quietly wrong.
    const tick = () => {
      const t = Math.min(1, (performance.now() - start) / ms);
      // Ease out: fast at first, settling onto the number rather than
      // slamming into it.
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(base + (value - base) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
      else from.current = value;
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, ms]);

  return shown;
}

/** One line of copy, revealed in order. */
function Reveal({
  step,
  children,
  as: Tag = "div",
  ...rest
}: {
  step: number;
  children: React.ReactNode;
  as?: "div" | "p" | "h1";
  [key: string]: unknown;
}) {
  return (
    <Tag className="reveal" style={{ animationDelay: `${step * 90}ms` }} {...rest}>
      {children}
    </Tag>
  );
}

const CURVES: Array<{ label: string; cls: string; colour: string; halfLifeHours: number }> = [
  { label: "Market", cls: "market", colour: "var(--bad)", halfLifeHours: 0.25 },
  { label: "Pricing", cls: "pricing", colour: "var(--warn)", halfLifeHours: 6 },
  { label: "Leadership", cls: "leadership", colour: "var(--bifrost)", halfLifeHours: 24 },
  { label: "Timeless", cls: "timeless", colour: "var(--ok)", halfLifeHours: Infinity },
];

const value = (halfLife: number, h: number) =>
  halfLife === Infinity ? 1 : Math.pow(0.5, h / halfLife);

/**
 * The decay scope: the hero's one moving picture.
 *
 * It sweeps seven days of cache age on a loop and reads off what each
 * volatility class is still worth at that moment. The whole product is in
 * this chart: at day three a market fact is worth nothing, a leadership fact
 * is down to a tenth, and a timeless one has not moved.
 */
function DecayScope() {
  const reduced = prefersReducedMotion();
  const [hours, setHours] = useState(reduced ? 72 : 0);

  useEffect(() => {
    if (reduced) return;
    const timer = window.setInterval(() => {
      setHours((h) => (h >= 168 ? 0 : h + 1.4));
    }, 60);
    return () => window.clearInterval(timer);
  }, [reduced]);

  const W = 520;
  const H = 230;
  const PAD = { l: 34, r: 12, t: 12, b: 26 };
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;

  const x = (h: number) => PAD.l + (h / 168) * plotW;
  const y = (v: number) => PAD.t + (1 - v) * plotH;

  const paths = useMemo(
    () =>
      CURVES.map((curve) => {
        const points: string[] = [];
        for (let h = 0; h <= 168; h += 1.5) {
          points.push(`${x(h).toFixed(1)},${y(value(curve.halfLifeHours, h)).toFixed(1)}`);
        }
        return { ...curve, d: `M${points.join("L")}` };
      }),
    [],
  );

  return (
    <div className="panel scope">
      <div className="scope-head">
        <h2 style={{ fontSize: 20 }}>How fast a cached fact goes stale</h2>
        <span className="eyebrow">Day {(hours / 24).toFixed(1)} since cached</span>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Decay of cached fact value by volatility class over seven days">
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} stroke="var(--grid)" strokeWidth="1" />
            <text x={PAD.l - 6} y={y(v) + 3.5} textAnchor="end" fontSize="9" fill="var(--ink-3)" className="mono">
              {Math.round(v * 100)}
            </text>
          </g>
        ))}
        {[0, 1, 2, 3, 4, 5, 6, 7].map((d) => (
          <text key={d} x={x(d * 24)} y={H - 8} textAnchor="middle" fontSize="9" fill="var(--ink-3)" className="mono">
            {d}d
          </text>
        ))}

        {/* A flat 7-day cache lifetime, for contrast: it never declines. */}
        <line
          x1={PAD.l}
          x2={W - PAD.r}
          y1={y(1)}
          y2={y(1)}
          stroke="var(--ink-3)"
          strokeWidth="1.5"
          strokeDasharray="3 3"
        />

        {paths.map((p) => (
          <path key={p.cls} d={p.d} fill="none" stroke={p.colour} strokeWidth="2" strokeLinecap="round" />
        ))}

        <line x1={x(hours)} x2={x(hours)} y1={PAD.t} y2={H - PAD.b} stroke="var(--ink)" strokeWidth="1.5" />
        {paths.map((p) => (
          <circle key={`${p.cls}-dot`} cx={x(hours)} cy={y(value(p.halfLifeHours, hours))} r="3.5" fill={p.colour} />
        ))}
      </svg>

      <div className="readout">
        {CURVES.map((c) => (
          <div key={c.cls} style={{ ["--c" as string]: c.colour }}>
            <b className="tab">{Math.round(value(c.halfLifeHours, hours) * 100)}%</b>
            {c.label}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Hero({
  headline,
  onStartTour,
  onScrollOn,
  cueHidden,
}: {
  headline: HeroCounters;
  onStartTour: () => void;
  onScrollOn: () => void;
  cueHidden: boolean;
}) {
  const answers = useCountUp(headline.answers);
  const stale = useCountUp(headline.stalePrevented);
  const saving = useCountUp(headline.cacheSaving * 100);

  return (
    <section className="hero stage cinematic">
      <div className="wrap cine-in">
        <Reveal step={0} as="p" className="eyebrow">
          A freshness firewall for the Bifrost AI gateway
        </Reveal>

        <h1>
          <span className="reveal line" style={{ animationDelay: "90ms" }}>
            Your cache remembers.
          </span>
          <span className="reveal line cut" style={{ animationDelay: "210ms" }}>
            Chronos checks.
          </span>
        </h1>

        <Reveal step={4} as="p" className="lede">
          Semantic caching replays yesterday&rsquo;s answer to today&rsquo;s user. Chronos sits
          inside Bifrost, pulls the time-sensitive facts out of every answer, checks them
          against the live web with Tavily, and evicts cache entries whose facts have gone
          stale.
        </Reveal>

        <Reveal step={5} className="owners-row">
          <span className="own b">Bifrost &middot; gateway, cache, routing</span>
          <span className="own t">Tavily &middot; live evidence</span>
          <span className="own c">Chronos &middot; claims, scores, eviction</span>
        </Reveal>

        <Reveal step={6} className="hero-cta">
          <button className="btn" onClick={onStartTour}>
            Start the walkthrough
          </button>
          <button className="btn ghost" onClick={onScrollOn}>
            Explore on your own
          </button>
          <span className="hint">8 steps &middot; about 2 minutes</span>
        </Reveal>

        <Reveal step={7} className="counters">
          <div className="counter">
            <b className="tab">{Math.round(answers).toLocaleString()}</b>
            <span>answers checked</span>
          </div>
          <div className="counter">
            <b className="tab">{Math.round(stale).toLocaleString()}</b>
            <span>stale cache hits stopped</span>
          </div>
          <div className="counter">
            <b className="tab">{Math.round(saving)}%</b>
            <span>Tavily calls saved by claim cache</span>
          </div>
        </Reveal>
      </div>

      {/* The instrument, cropped by the fold. Enough to say there is a real
          thing down there; not enough to read. */}
      <div className="cine-peek reveal" style={{ animationDelay: "300ms" }}>
        <DecayScope />
      </div>

      <div className="cue" data-hidden={cueHidden}>
        <button onClick={onScrollOn} aria-label="Scroll to the signal path">
          Scroll
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M8 3v9M4.5 8.5 8 12l3.5-3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
    </section>
  );
}
