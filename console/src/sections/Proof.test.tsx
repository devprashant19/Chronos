/**
 * The "every feature" section shows what the recorder wrote, including a
 * failure, and re-runs only what a browser can honestly re-run.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LIVE_CHECKS, runLive, tally, type ProofFile } from "../proof";
import Proof from "./Proof";

const FILE: ProofFile = {
  recorded_at: "2026-11-08T18:00:00+00:00",
  recorded_by: "scripts/record-proof.py",
  stack: { evidence: "live Tavily", judge_model: "groq/openai/gpt-oss-20b" },
  summary: { pass: 1, fail: 1, measured: 1 },
  features: [
    { id: "caught", title: "The cached answer is checked, and caught", section: "#run", promise: "outdated on the hit",
      status: "pass", evidence: [{ label: "request 2 · x-chronos-verdict", value: "outdated" }], note: "checked inline" },
    { id: "timeless", title: "A timeless fact costs nothing", section: "#inspect", promise: "0 credits",
      status: "fail", evidence: [{ label: "tavily_credits", value: "2" }], note: "it spent credits" },
    { id: "bench", title: "Measured against labelled claims", section: "#bench", promise: "a measurement",
      status: "measured", evidence: [], note: "from bench/results-model.json" },
  ],
};

function serve(file: ProofFile | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(file ? new Response(JSON.stringify(file), { status: 200 }) : new Response("", { status: 404 })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("every feature, working", () => {
  it("shows each recorded feature with its evidence, and a failure as a failure", async () => {
    serve(FILE);
    render(<Proof mode="demo" />);

    expect(await screen.findByText("The cached answer is checked, and caught")).toBeTruthy();
    expect(screen.getByText("outdated")).toBeTruthy();
    // Counted from the features themselves, so a stale summary cannot hide a fail.
    expect(screen.getByText("1 working")).toBeTruthy();
    expect(screen.getByText("1 failed")).toBeTruthy();
    expect(screen.getByText("1 measured")).toBeTruthy();
    expect(screen.getByText(/recorded 2026-11-08 18:00 UTC/)).toBeTruthy();
    // Every card links to the section that shows the feature.
    const links = screen.getAllByText("See it on this page →").map((a) => a.getAttribute("href"));
    expect(links).toEqual(["#run", "#inspect", "#bench"]);
  });

  it("says plainly when there is no proof file, and shows nothing invented", async () => {
    serve(null);
    render(<Proof mode="demo" />);
    expect(await screen.findByText("No proof file")).toBeTruthy();
    expect(screen.queryByText(/^\d+ working$/)).toBeNull();
  });

  it("offers no live re-run in Demo mode", async () => {
    serve(FILE);
    render(<Proof mode="demo" />);
    await screen.findByText("The cached answer is checked, and caught");
    expect(screen.queryByRole("button", { name: /Re-run/ })).toBeNull();
  });

  it("re-runs the browser-side checks in Live mode and marks them live", async () => {
    serve(FILE);
    for (const id of Object.keys(LIVE_CHECKS)) {
      vi.spyOn(LIVE_CHECKS, id).mockResolvedValue({ status: "pass", evidence: [{ label: "tavily_credits", value: "0" }] });
    }
    render(<Proof mode="live" />);
    const button = await screen.findByRole("button", { name: /Re-run \d+ of them live/ });
    await userEvent.click(button);

    // The recorded failure for "timeless" is replaced by the live result.
    await waitFor(() => expect(screen.queryByText("1 failed")).toBeNull());
    expect(screen.getByText("live, just now")).toBeTruthy();
  });
});

describe("the live checks", () => {
  it("turns an exception into a fail rather than throwing", async () => {
    vi.spyOn(LIVE_CHECKS, "sweeper").mockRejectedValue(new Error("Failed to fetch"));
    const result = await runLive("sweeper");
    expect(result.status).toBe("fail");
    expect(result.evidence[0].value).toBe("Failed to fetch");
  });

  it("are only the ones a browser can make", () => {
    // Stopping a container, running inside the verifier, or sending a header
    // the CORS preflight refuses cannot happen from a page.
    expect(Object.keys(LIVE_CHECKS).sort()).toEqual(
      ["claim-cache", "cors", "observability", "retention-live", "sweeper", "timeless"],
    );
  });

  it("tally counts by status", () => {
    expect(tally(FILE.features)).toEqual({ pass: 1, fail: 1, measured: 1 });
  });
});
