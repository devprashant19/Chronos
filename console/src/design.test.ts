/**
 * The console against its design reference.
 *
 * `design/chronos-console.html` is the reference the console is built to match,
 * and `src/instrument.css` is lifted from it rather than reimplemented. Two
 * files holding the same values drift, and the drift is invisible: a token
 * nobody notices is wrong until someone compares a screenshot with the
 * reference, which is how the brand mark lost its red slash.
 *
 * This is not a pixel diff. It compares the things that are literally the same
 * text in both files, which is the part that can drift silently.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const reference = readFileSync(join(here, "..", "..", "design", "chronos-console.html"), "utf8");
const stylesheet = readFileSync(join(here, "instrument.css"), "utf8");

/**
 * Every `--name: value` declaration in the `:root{...}` block that defines the
 * palette.
 *
 * Not simply the first `:root` block: the reference HTML opens with an
 * injected reset whose `:root` sets `color-scheme` and nothing else, and
 * reading that one silently returns no tokens and passes every comparison.
 */
function rootTokens(css: string): Record<string, string> {
  for (const block of css.matchAll(/:root\s*\{([^}]*)\}/g)) {
    const tokens: Record<string, string> = {};
    for (const m of block[1].matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
      tokens[m[1]] = m[2].trim().replace(/\s+/g, " ");
    }
    if (Object.keys(tokens).length) return tokens;
  }
  throw new Error("no :root block declaring custom properties");
}

describe("colour and type tokens", () => {
  const theirs = rootTokens(reference);
  const ours = rootTokens(stylesheet);

  it("the reference defines the tokens this test is about", () => {
    expect(Object.keys(theirs).length).toBeGreaterThan(15);
  });

  it("defines every token the reference does", () => {
    const missing = Object.keys(theirs).filter((name) => !(name in ours));
    expect(missing, `instrument.css is missing ${missing.join(", ")}`).toEqual([]);
  });

  it("gives each one the same value", () => {
    const drifted = Object.keys(theirs)
      .filter((name) => name in ours && ours[name] !== theirs[name])
      .map((name) => `${name}: ${ours[name]} (reference says ${theirs[name]})`);
    expect(drifted, `drifted from design/chronos-console.html:\n  ${drifted.join("\n  ")}`).toEqual([]);
  });

  it("keeps product colour meaning ownership", () => {
    // Bifrost blue, Tavily magenta, Chronos ink. Three colours used for
    // nothing else, which is what lets a reader tell who owns a step.
    expect(ours["--bifrost"]).toBe("#2744E0");
    expect(ours["--tavily"]).toBe("#A23F86");
    expect(ours["--chronos"]).toBe("#13161B");
  });

  it("keeps a verdict colour for each band", () => {
    expect(ours["--ok"]).toBeDefined();
    expect(ours["--warn"]).toBeDefined();
    expect(ours["--bad"]).toBeDefined();
    expect(new Set([ours["--ok"], ours["--warn"], ours["--bad"]]).size).toBe(3);
  });
});

describe("dark mode", () => {
  it("is defined twice, by preference and by the explicit toggle", () => {
    // The header toggle sets data-theme on :root, and a viewer who never
    // touches it gets prefers-color-scheme. Defining only one leaves half the
    // viewers on the light palette in a dark room, or a dead toggle.
    expect(stylesheet).toMatch(/@media\s*\(prefers-color-scheme:\s*dark\)/);
    expect(stylesheet).toMatch(/:root\[data-theme="dark"\]/);
  });

  it("guards the preference block so an explicit light choice wins", () => {
    expect(stylesheet).toContain(':root:not([data-theme="light"])');
  });

  it("redefines the ground and the ink for dark, not just the accents", () => {
    const dark = stylesheet.match(/:root\[data-theme="dark"\]\s*\{([^}]*)\}/);
    expect(dark).not.toBeNull();
    for (const token of ["--paper", "--panel", "--ink", "--rule"]) {
      expect(dark![1], `dark mode must redefine ${token}`).toContain(token);
    }
  });
});

describe("type", () => {
  const ours = rootTokens(stylesheet);

  it("names a fallback for every webfont, so a blocked Google Fonts does not break the layout", () => {
    for (const token of ["--display", "--body", "--mono"]) {
      const stack = ours[token];
      expect(stack, `${token} is not defined`).toBeDefined();
      expect(stack.split(",").length, `${token} has no fallback`).toBeGreaterThan(1);
    }
  });

  it("ends each stack in a generic family", () => {
    for (const token of ["--display", "--body", "--mono"]) {
      const last = ours[token].split(",").pop()!.trim();
      expect(["sans-serif", "serif", "monospace", "ui-monospace"]).toContain(last);
    }
  });
});

describe("the page the reference describes", () => {
  it("has the same seven sections, in the same order", () => {
    const anchors = [...reference.matchAll(/<a href="#([a-z]+)">/g)].map((m) => m[1]);
    expect(anchors).toEqual(["path", "traffic", "inspect", "cache", "playground", "bench", "brief"]);
  });
});
