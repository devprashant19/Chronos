/**
 * The guided walkthrough.
 *
 * The console is one long page, and someone seeing it for the first time has
 * no idea which panel carries the argument. This walks them through it in the
 * order the product actually happens: classify, serve from cache, pull the
 * claims, check them, score, evict.
 *
 * It drives the real page rather than being a separate slideshow -- each step
 * scrolls to a real section and rings it. There is nothing here that is not
 * already on the page, which is deliberate: a tour that shows something the
 * product cannot is a lie with a Next button.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface Step {
  /** The id of the section to scroll to, or null for the hero. */
  target: string | null;
  owner: "b" | "t" | "c" | null;
  title: string;
  /** One sentence: what to look at, not what it is. */
  look: string;
}

export const STEPS: Step[] = [
  {
    target: null,
    owner: null,
    title: "The problem, in one line",
    look: "A semantic cache is keyed on what was asked, not on what the answer contained — so it replays a stock price as happily as a definition.",
  },
  {
    target: "path",
    owner: "b",
    title: "Where Chronos sits",
    look: "Follow one request through the gateway. Chronos is a plugin inside Bifrost, on four hooks — not a proxy in front of it.",
  },
  {
    target: "run",
    owner: "c",
    title: "Drive the cycle yourself",
    look: "Five steps with a button that advances them: ask, ask again and hit the cache, watch the claims get checked, read the verdict off the headers, watch the entry be deleted.",
  },
  {
    target: "traffic",
    owner: "c",
    title: "Every answer gets checked",
    look: "Each row is one answer through the gateway, with the verdict Chronos reached. Watch the 'cache' rows: those are the replays.",
  },
  {
    target: "inspect",
    owner: "c",
    title: "This is the product",
    look: "A cached answer with its time-sensitive claims lit up. One is scored 36 and marked outdated — and every term of that score is shown, with the pages that disagreed, quoted.",
  },
  {
    target: "cache",
    owner: "b",
    title: "One flat TTL fits no fact",
    look: "Chronos sets the cache TTL per request from how fast the answer's facts move. Underneath is the ledger of entries it actually deleted.",
  },
  {
    target: "playground",
    owner: "b",
    title: "The verdict rides on the response",
    look: "Real headers from the gateway. The second request is a cache hit caught as outdated; the third misses, because the entry was deleted in between.",
  },
  {
    target: "bench",
    owner: "t",
    title: "Measured, not promised",
    look: "Read from bench/results.json, written by the benchmark runner. Nothing on this panel is a target, and the configuration is stated with every number.",
  },
  {
    target: "brief",
    owner: "t",
    title: "What you would actually be told",
    look: "The same verdicts, written as a briefing: what is confirmed, what is not, and what nobody could check.",
  },
  {
    target: "proof",
    owner: "c",
    title: "Every feature, run for real",
    look: "Each card is one feature run against the live stack, with the headers and counters that came back and the date of the run. In Live mode, press the button to re-run the browser-side ones yourself.",
  },
];

const OWNER_LABEL: Record<"b" | "t" | "c", string> = {
  b: "Bifrost",
  t: "Tavily",
  c: "Chronos",
};

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
  } catch {
    return false;
  }
}

export default function Walkthrough({
  index,
  onIndex,
  onExit,
}: {
  index: number;
  onIndex: (next: number) => void;
  onExit: () => void;
}) {
  const step = STEPS[index];
  const ringed = useRef<Element | null>(null);

  // Scroll to this step's section and ring it. The ring is removed from the
  // previous one first, so two sections are never lit at once.
  useEffect(() => {
    if (ringed.current) ringed.current.classList.remove("tour-on");
    ringed.current = null;

    const behavior = prefersReducedMotion() ? "auto" : "smooth";

    if (!step.target) {
      window.scrollTo({ top: 0, behavior });
      return;
    }
    const el = document.getElementById(step.target);
    if (!el) return;
    el.scrollIntoView({ behavior, block: "start" });
    el.classList.add("tour-on");
    ringed.current = el;
  }, [step]);

  // Nothing should stay lit after the tour closes.
  useEffect(
    () => () => {
      ringed.current?.classList.remove("tour-on");
    },
    [],
  );

  const next = useCallback(() => {
    if (index < STEPS.length - 1) onIndex(index + 1);
    else onExit();
  }, [index, onIndex, onExit]);

  const back = useCallback(() => {
    if (index > 0) onIndex(index - 1);
  }, [index, onIndex]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") next();
      else if (e.key === "ArrowLeft") back();
      else if (e.key === "Escape") onExit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, back, onExit]);

  const last = index === STEPS.length - 1;

  // --- dragging ----------------------------------------------------------
  // It used to be a bar across the foot of the page, which covered whatever
  // step 1 was pointing at. It is a card on the right now, and draggable,
  // because "the clear part of the screen" depends on the section.
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const drag = useRef<{ dx: number; dy: number } | null>(null);
  const card = useRef<HTMLDivElement>(null);

  const clamp = useCallback((x: number, y: number) => {
    const el = card.current;
    const w = el?.offsetWidth ?? 360;
    const h = el?.offsetHeight ?? 260;
    return {
      x: Math.min(Math.max(8, x), Math.max(8, window.innerWidth - w - 8)),
      y: Math.min(Math.max(64, y), Math.max(64, window.innerHeight - h - 8)),
    };
  }, []);

  const onPointerDown = (e: React.PointerEvent) => {
    const el = card.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    drag.current = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    setPos(clamp(e.clientX - drag.current.dx, e.clientY - drag.current.dy));
  };
  const onPointerUp = (e: React.PointerEvent) => {
    drag.current = null;
    (e.target as Element).releasePointerCapture?.(e.pointerId);
  };

  // Keep it on screen when the window changes size.
  useEffect(() => {
    const onResize = () => setPos((p) => (p ? clamp(p.x, p.y) : p));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [clamp]);

  return (
    <aside
      ref={card}
      className="tour"
      role="region"
      aria-label="Guided walkthrough"
      style={pos ? { left: pos.x, top: pos.y, right: "auto", bottom: "auto" } : undefined}
    >
      <div
        className="tour-grip"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        title="Drag to move"
      >
        <span className="tour-dots" aria-hidden="true" />
        <p className="eyebrow">
          Step {index + 1} of {STEPS.length}
          {step.owner ? (
            <>
              {" · "}
              <span className={`own ${step.owner}`} style={{ padding: "2px 6px" }}>
                {OWNER_LABEL[step.owner]}
              </span>
            </>
          ) : null}
        </p>
        <button className="tour-x" onClick={onExit} aria-label="Close the walkthrough" title="Esc">
          ✕
        </button>
      </div>

      <div className="tour-meter" aria-hidden="true">
        {STEPS.map((_, i) => (
          <i key={i} className={i <= index ? "on" : ""} />
        ))}
      </div>

      <div className="tour-body">
        <h3>{step.title}</h3>
        <p className="tour-look">{step.look}</p>
      </div>

      <div className="tour-nav">
        <button className="btn ghost" onClick={back} disabled={index === 0}>
          Back
        </button>
        <button className="btn" onClick={next}>
          {last ? "Finish" : "Next"}
        </button>
      </div>
    </aside>
  );
}
