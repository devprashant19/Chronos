import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import Inspector from "./Inspector";
import { AnnounceProvider } from "../announce";
import { DEMO_ANSWER, LEADERSHIP_ANSWER, PRICING_ANSWER, TIMELESS_ANSWER, demoReportFor } from "../demo";

function mount() {
  return render(
    <AnnounceProvider>
      <Inspector mode="demo" />
    </AnnounceProvider>,
  );
}

async function checkExample(label: string) {
  const user = userEvent.setup();
  const section = document.getElementById("inspect") as HTMLElement;
  await user.click(within(section).getByRole("button", { name: label }));
  await user.click(within(section).getByRole("button", { name: "Check this answer" }));
  await within(section).findByRole("button", { name: "Edit the answer" }, { timeout: 4000 });
  return section;
}

describe("Inspector recorded examples", () => {
  it("every example has its own report, and every span lands on its own sentence", () => {
    for (const answer of [DEMO_ANSWER, LEADERSHIP_ANSWER, PRICING_ANSWER, TIMELESS_ANSWER]) {
      const report = demoReportFor(answer);
      expect(report).not.toBeNull();
      for (const c of report!.claims) {
        expect(answer.slice(c.span_start, c.span_end)).toBe(c.text);
      }
    }
  });

  it("each checked score is the formula worked through with its own terms", () => {
    for (const answer of [LEADERSHIP_ANSWER, PRICING_ANSWER]) {
      for (const c of demoReportFor(answer)!.claims) {
        if (!c.breakdown) continue;
        const b = c.breakdown;
        const s = 100 * (0.45 * b.agreement + 0.25 * b.recency + 0.15 * b.quality + 0.15 * b.confidence) * (1 - 0.5 * b.volatility * b.decay);
        expect(c.score).toBeCloseTo(s, 1);
      }
    }
  });

  it("the answer's score is the minimum over checkable claims; unverifiable never counts as outdated", () => {
    const pricing = demoReportFor(PRICING_ANSWER)!;
    expect(pricing.claims.map((c) => c.verdict)).toEqual(["outdated", "unverifiable"]);
    expect(pricing.stale_count).toBe(1);
    expect(pricing.score).toBe(pricing.claims[0].score);
  });

  it("an all-timeless answer costs no credits and is current", () => {
    const r = demoReportFor(TIMELESS_ANSWER)!;
    expect(r.tavily_credits).toBe(0);
    expect(r.verdict).toBe("current");
    expect(r.evicted).toBe(false);
  });

  it("Stale leadership highlights its own claims and opens on Twitter's chief executive", async () => {
    mount();
    const section = await checkExample("Stale leadership");
    const claims = within(section).getAllByRole("button", { name: /^Claim:/ });
    expect(claims.map((b) => b.textContent)).toEqual([
      "Jack Dorsey is the CEO of Twitter.",
      "He co-founded the company and returned as chief executive in 2015.",
      "Twitter remains a publicly traded company on the New York Stock Exchange.",
    ]);
    expect(section).toHaveTextContent(/stepped down in November 2021/);
    expect(section).toHaveTextContent(/3 claims · 0 skipped · 10 credits/i);
  });

  it("All timeless spends nothing and says Tavily was not called", async () => {
    mount();
    const section = await checkExample("All timeless");
    expect(section).toHaveTextContent(/3 claims · 0 skipped · 0 credits/i);
    expect(section).toHaveTextContent(/tavily: not called, every claim is timeless/);
  });

  it("text nobody recorded gets no report, and says why", async () => {
    const user = userEvent.setup();
    mount();
    const section = document.getElementById("inspect") as HTMLElement;
    await user.click(within(section).getByRole("button", { name: "Stale pricing" }));
    const box = within(section).getByLabelText("Answer to check");
    await user.clear(box);
    await user.type(box, "Some answer nobody recorded.");
    await user.click(within(section).getByRole("button", { name: "Check this answer" }));
    expect(await screen.findByText(/only the four examples are recorded/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: /^Claim:/ })).toBeNull();
  });
});
