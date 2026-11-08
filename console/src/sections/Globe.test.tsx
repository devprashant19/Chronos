/**
 * The rotating globe.
 *
 * It is decoration, so there is not much to assert about how it looks. What
 * does matter is that it stays decoration: it must be hidden from assistive
 * technology, it must not intercept a click meant for the button sitting on
 * top of it, and its meridians must actually be staggered — all fourteen
 * sharing one animation offset would be a sphere that pulses rather than
 * turns, which looks like a bug and is easy to introduce by deleting one line.
 */

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Globe from "./Globe";

describe("the globe", () => {
  it("is hidden from assistive technology", () => {
    const { container } = render(<Globe />);
    const root = container.querySelector(".globe")!;
    expect(root.getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelector("svg")!.getAttribute("role")).toBe("presentation");
  });

  it("draws both meridians and parallels", () => {
    const { container } = render(<Globe />);
    expect(container.querySelectorAll(".globe-mer").length).toBeGreaterThan(6);
    expect(container.querySelectorAll(".globe-par").length).toBeGreaterThan(4);
  });

  it("gives every meridian a different start, so it turns rather than pulses", () => {
    const { container } = render(<Globe />);
    const delays = Array.from(container.querySelectorAll<SVGElement>(".globe-mer")).map(
      (el) => el.style.animationDelay,
    );

    expect(delays.length).toBeGreaterThan(6);
    expect(new Set(delays).size).toBe(delays.length);
    // Negative, so the animation starts already in progress rather than every
    // meridian waiting to begin.
    expect(delays.filter((d) => d.startsWith("-")).length).toBe(delays.length - 1);
  });

  it("keeps its parallels inside the sphere", () => {
    // rx = sqrt(R^2 - y^2): a parallel wider than the sphere would poke out of
    // the rim, and the clip path would hide the mistake rather than fix it.
    const { container } = render(<Globe />);
    for (const el of Array.from(container.querySelectorAll<SVGEllipseElement>(".globe-par"))) {
      const cy = Math.abs(Number(el.getAttribute("cy")));
      const rx = Number(el.getAttribute("rx"));
      expect(rx).toBeGreaterThan(0);
      expect(Math.hypot(rx, cy)).toBeLessThanOrEqual(93);
    }
  });

  it("flattens its parallels, so it reads as a sphere and not a dartboard", () => {
    const { container } = render(<Globe />);
    for (const el of Array.from(container.querySelectorAll<SVGEllipseElement>(".globe-par"))) {
      expect(Number(el.getAttribute("ry"))).toBeLessThan(Number(el.getAttribute("rx")));
    }
  });
});
