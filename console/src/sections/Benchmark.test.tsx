import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Benchmark from "./Benchmark";

// The two files the runner writes, cut down to what the section reads.
function results(claims: number, p: number, r: number, f1: number, p50: number, warm: number | null) {
  return {
    benchmark: "StaleBench",
    dataset_version: "1.0.0",
    dataset_date: "2026-11-06",
    run: { started_at: "2026-11-06T17:00:00Z", duration_s: 25, claims, verifier: "http://verifier:8000" },
    configuration: { offline_demo: false, tavily_configured: true, claim_cache_backend: "redis", evidence_source: "tavily", note: "measured" },
    modes: {
      annotate: {
        mode: "annotate", label: "cold", n: claims, usable: claims, errors: 0,
        outdated_detection: { precision: p, recall: r, f1, tp: 2, fp: 1, fn: 1 },
        per_class: { leadership: { precision: p, recall: r, f1, tp: 2, fp: 1, fn: 1 } },
        timeless: { n: 6, skipped_without_credits: 6, skip_rate: 1 },
        latency_ms: { p50, p95: p50 * 2, mean: p50, max: p50 * 3 },
        tavily_credits: { total: 90, per_1000_answers: 3000 },
        claim_cache_hits: 0,
        unverifiable: 0,
      },
    },
    claim_cache_effect: {
      cold_credits_per_1000_answers: 3000,
      warm_credits_per_1000_answers: warm,
      credit_saving_fraction: warm === null ? null : 1 - warm / 3000,
      measured: warm !== null,
      note: "cold then warm",
    },
    replay: {},
    label_disagreements: { count: 0, note: "none" },
  };
}

const RULE = results(30, 0.4, 0.2, 0.2667, 841.1, 1833.3);
const MODEL = results(18, 0.8, 0.6667, 0.7273, 15823.9, null);

function serve(files: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const name = url.split("/").pop() as string;
      return Promise.resolve(
        name in files
          ? { ok: true, json: () => Promise.resolve(files[name]) }
          : { ok: false, status: 404, json: () => Promise.reject(new Error("404")) },
      );
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("Benchmark", () => {
  it("shows the rule engine and the model run side by side, each with its own sample", async () => {
    serve({ "results.json": RULE, "results-model.json": MODEL });
    render(<Benchmark />);

    const compare = await screen.findByTestId("bench-compare");
    const rule = within(compare).getByTestId("bench-run-rule");
    const model = within(compare).getByTestId("bench-run-model");

    expect(within(rule).getByText("0.27")).toBeInTheDocument();
    expect(within(rule).getByText(/30 claims/)).toBeInTheDocument();
    expect(within(model).getByText("0.73")).toBeInTheDocument();
    expect(within(model).getByText(/18 claims/)).toBeInTheDocument();
    // The model's latency is stated, not hidden behind its better precision.
    expect(within(model).getByText(/15\.8 s/)).toBeInTheDocument();
    // The sample difference is said where the numbers are.
    expect(compare).toHaveTextContent(/neither is a whole-dataset figure/);
  });

  it("still shows the rule-engine run when the model file is missing, and says so", async () => {
    serve({ "results.json": RULE });
    render(<Benchmark />);

    const compare = await screen.findByTestId("bench-compare");
    expect(within(compare).getByTestId("bench-run-rule")).toBeInTheDocument();
    expect(within(compare).queryByTestId("bench-run-model")).toBeNull();
    expect(compare).toHaveTextContent(/results-model\.json was not found/);
  });

  it("shows no numbers at all when the main results file is missing", async () => {
    serve({});
    render(<Benchmark />);

    expect(await screen.findByText("No results file")).toBeInTheDocument();
    expect(screen.queryByTestId("bench-compare")).toBeNull();
  });
});
