/**
 * The hero.
 *
 * Two things here can go wrong without anyone noticing. A count-up that never
 * reaches its target leaves a permanently wrong number on the most prominent
 * part of the page, and a motion effect that ignores prefers-reduced-motion is
 * an accessibility fault that no screenshot shows.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Hero from "./Hero";

const COUNTS = { answers: 4687, stalePrevented: 19, cacheSaving: 0.7 };

function reducedMotion(on: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: on && query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

const renderHero = (over: Partial<Parameters<typeof Hero>[0]> = {}) =>
  render(
    <Hero
      headline={COUNTS}
      onStartTour={over.onStartTour ?? (() => {})}
      onScrollOn={over.onScrollOn ?? (() => {})}
      cueHidden={over.cueHidden ?? false}
    />,
  );

afterEach(() => vi.unstubAllGlobals());

describe("what it says", () => {
  it("leads with the claim, not the product name", () => {
    renderHero();
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent(/Your cache remembers/i);
    expect(h1).toHaveTextContent(/Chronos checks/i);
  });

  it("carries one line of supporting copy, not a paragraph", () => {
    // The hero is the first and sometimes only thing a judge reads. The
    // ownership chips that used to live here say the same thing the signal
    // path section says at length, so they moved there and this stayed short.
    const { container } = renderHero();
    const lede = container.querySelector(".lede")!;

    expect(lede.textContent!.length).toBeLessThan(180);
    expect(container.querySelectorAll(".own")).toHaveLength(0);
  });

  it("offers both ways in, and a scroll cue", () => {
    const { container } = renderHero();
    expect(screen.getByRole("button", { name: /start the walkthrough/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /explore on your own/i })).toBeInTheDocument();
    expect(container.querySelector(".cue")).not.toBeNull();
  });

  it("hides the cue when told to", () => {
    const { container } = renderHero({ cueHidden: true });
    expect(container.querySelector(".cue")!.getAttribute("data-hidden")).toBe("true");
  });
});

describe("the counters", () => {
  it("count up and land on the real figures", async () => {
    reducedMotion(false);
    const { container } = renderHero();
    const counters = container.querySelector(".counters") as HTMLElement;

    await waitFor(
      () => {
        expect(within(counters).getByText("4,687")).toBeInTheDocument();
        expect(within(counters).getByText("19")).toBeInTheDocument();
        expect(within(counters).getByText("70%")).toBeInTheDocument();
      },
      { timeout: 4000 },
    );
  });

  it("never overshoot the real figure", async () => {
    reducedMotion(false);
    const { container } = renderHero();
    const first = container.querySelector(".counter b") as HTMLElement;

    // A count-up that eases past its target and settles back looks like a
    // glitch, and on a number like "stale hits stopped" it is a lie for a
    // frame or two.
    const seen: number[] = [];
    for (let i = 0; i < 25; i += 1) {
      seen.push(Number(first.textContent!.replace(/,/g, "")));
      await new Promise((r) => setTimeout(r, 40));
    }
    expect(Math.max(...seen)).toBeLessThanOrEqual(COUNTS.answers);
  });

  it("show their final value immediately when motion is reduced", () => {
    reducedMotion(true);
    const { container } = renderHero();
    const counters = container.querySelector(".counters") as HTMLElement;

    expect(within(counters).getByText("4,687")).toBeInTheDocument();
    expect(within(counters).getByText("70%")).toBeInTheDocument();
  });
});

describe("the decay scope", () => {
  it("draws one curve per volatility class", () => {
    const { container } = renderHero();
    const svg = container.querySelector(".scope svg")!;
    // Four classes, each a path; the axes and the flat-TTL reference are
    // <line>, not <path>.
    expect(svg.querySelectorAll("path")).toHaveLength(4);
  });

  it("describes itself for a reader who cannot see it", () => {
    renderHero();
    expect(
      screen.getByRole("img", { name: /decay of cached fact value by volatility class/i }),
    ).toBeInTheDocument();
  });

  it("reads off a value for every class", () => {
    const { container } = renderHero();
    const readout = container.querySelector(".readout")!;
    expect(readout.querySelectorAll("div")).toHaveLength(4);
    for (const label of ["Market", "Pricing", "Leadership", "Timeless"]) {
      expect(readout).toHaveTextContent(label);
    }
  });

  it("holds still at a representative day when motion is reduced", async () => {
    reducedMotion(true);
    const { container } = renderHero();
    const head = container.querySelector(".scope-head .eyebrow")!;
    const before = head.textContent;

    await new Promise((r) => setTimeout(r, 300));

    expect(head.textContent).toBe(before);
    // Day three is the point the argument is clearest: market is gone,
    // leadership is well down, timeless has not moved.
    expect(before).toMatch(/Day 3\.0/);
  });

  it("a timeless fact never decays, and a market one is gone within the day", () => {
    reducedMotion(true);
    const { container } = renderHero();
    const rows = Array.from(container.querySelectorAll(".readout div"));

    const read = (label: string) =>
      Number(rows.find((r) => r.textContent?.includes(label))!.querySelector("b")!.textContent!.replace("%", ""));

    expect(read("Timeless")).toBe(100);
    expect(read("Market")).toBe(0);
    expect(read("Leadership")).toBeLessThan(20);
  });
});
