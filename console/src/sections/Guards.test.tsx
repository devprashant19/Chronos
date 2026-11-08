/**
 * The verifier's guards are only worth having if a person can see them fire.
 * A source set aside for injection, or a claim kept out of the claim cache,
 * changes a verdict; the score drawer has to say so, or the number it shows
 * is one nobody can argue with.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ClaimVerdict } from "../api";
import { ScoreDrawer } from "./Inspector";

function claim(overrides: Partial<ClaimVerdict> = {}): ClaimVerdict {
  return {
    text: "Larry Page is the CEO of Google.",
    span_start: 0,
    span_end: 32,
    subject: "Google",
    predicate: "holder of role (CEO)",
    stated_value: "Larry Page",
    class: "leadership",
    verdict: "unverifiable",
    score: 0,
    current_value: null,
    source_url: null,
    publish_date: null,
    quote: null,
    breakdown: null,
    evidence: [],
    from_claim_cache: false,
    tavily_credits: 2,
    ...overrides,
  };
}

describe("the score drawer shows the guards", () => {
  it("marks a source that tried to instruct the judge, and says why it was set aside", () => {
    render(
      <ScoreDrawer
        claim={claim({
          evidence: [
            {
              url: "https://attacker.example/ceo",
              title: "",
              snippet: "",
              published_date: null,
              tier: "other",
              call: "insufficient",
              quote: "",
              injection_flags: ["instruction override", "dictated verdict"],
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("set aside")).toBeTruthy();
    expect(screen.getByText(/tried to instruct the judge \(instruction override, dictated verdict\)/)).toBeTruthy();
  });

  it("marks a claim kept out of the claim cache because its subject names nothing", () => {
    render(<ScoreDrawer claim={claim({ subject: "the company", claim_cache_bypassed: "subject names no particular thing ('the company')" })} />);
    const pill = screen.getByText("claim cache bypassed · ambiguous subject");
    expect(pill.getAttribute("title")).toContain("the company");
  });

  it("shows neither for an ordinary claim, or for a report from before the guards existed", () => {
    render(
      <ScoreDrawer
        claim={claim({
          evidence: [
            { url: "https://news.example/g", title: "", snippet: "", published_date: null, tier: "news", call: "contradicts", quote: "Sundar Pichai is the CEO." },
          ],
        })}
      />,
    );
    expect(screen.queryByText("set aside")).toBeNull();
    expect(screen.queryByText(/claim cache bypassed/)).toBeNull();
  });
});
