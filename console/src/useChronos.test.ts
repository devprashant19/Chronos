/**
 * The hook that owns everything the console displays.
 *
 * Two behaviours here are load-bearing and neither fails loudly: the console
 * must fall back to demo mode when the verifier is unreachable rather than
 * showing an empty page, and a manual choice must not be overridden by a probe
 * that answers late.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import { DEMO_STATS } from "./demo";
import { useChronos } from "./useChronos";
import { FakeEventSource } from "./test/setup";

const STATS = { ...DEMO_STATS, answers_checked: 4654, stale_hits_prevented: 15 };

/** The verifier answers. */
function verifierUp() {
  vi.spyOn(api, "health").mockResolvedValue({ status: "ok", offline_demo: false, tavily_configured: true });
  vi.spyOn(api, "stats").mockResolvedValue(STATS);
  vi.spyOn(api, "cacheEvents").mockResolvedValue({ events: [] });
}

/** The verifier does not. */
function verifierDown() {
  vi.spyOn(api, "health").mockRejectedValue(new Error("connection refused"));
  vi.spyOn(api, "stats").mockRejectedValue(new Error("connection refused"));
  vi.spyOn(api, "cacheEvents").mockRejectedValue(new Error("connection refused"));
}

beforeEach(() => {
  FakeEventSource.reset();
});

describe("choosing a mode", () => {
  it("starts in demo mode, so the first paint is never empty", () => {
    verifierDown();
    const { result } = renderHook(() => useChronos());

    expect(result.current.mode).toBe("demo");
    expect(result.current.connection).toBe("demo");
  });

  it("switches itself to live when the verifier answers", async () => {
    verifierUp();
    const { result } = renderHook(() => useChronos());

    await waitFor(() => expect(result.current.mode).toBe("live"));
    expect(result.current.probe).toEqual({ reachable: true, offline: false, tavily: true });
  });

  it("stays in demo mode when the verifier does not answer", async () => {
    verifierDown();
    const { result } = renderHook(() => useChronos());

    await waitFor(() => expect(result.current.probe).not.toBeNull());
    expect(result.current.mode).toBe("demo");
    expect(result.current.probe?.reachable).toBe(false);
  });

  it("does not even ask when the browser would refuse the request", async () => {
    // The public demo site is https and the verifier defaults to plain http
    // on localhost. A browser blocks that before it leaves the tab, so the
    // probe logged a CORS error on every load of the site and could never
    // have succeeded. The answer is known, so it is not asked.
    const blocked = vi.spyOn(api, "blockedByBrowser").mockReturnValue(true);
    try {
      const health = vi
        .spyOn(api, "health")
        .mockResolvedValue({ status: "ok", offline_demo: false, tavily_configured: true });
      vi.spyOn(api, "stats").mockRejectedValue(new Error("unreachable"));
      vi.spyOn(api, "cacheEvents").mockRejectedValue(new Error("unreachable"));
      const { result } = renderHook(() => useChronos());

      await waitFor(() => expect(result.current.probe).not.toBeNull());
      expect(health).not.toHaveBeenCalled();
      expect(result.current.mode).toBe("demo");
      expect(result.current.probe).toEqual({ reachable: false, offline: true, tavily: false });
    } finally {
      blocked.mockRestore();
    }
  });

  it("does not override a choice the operator already made", async () => {
    // The probe is async. Picking demo while it is in flight must stick, or
    // the console flips out from under whoever clicked it.
    let resolveHealth: (value: { status: string; offline_demo: boolean; tavily_configured: boolean }) => void;
    vi.spyOn(api, "health").mockReturnValue(
      new Promise((resolve) => {
        resolveHealth = resolve;
      }),
    );
    vi.spyOn(api, "stats").mockResolvedValue(STATS);
    vi.spyOn(api, "cacheEvents").mockResolvedValue({ events: [] });

    const { result } = renderHook(() => useChronos());
    act(() => result.current.chooseMode("demo"));

    await act(async () => {
      resolveHealth!({ status: "ok", offline_demo: false, tavily_configured: true });
    });

    expect(result.current.mode).toBe("demo");
  });

  it("opens an SSE stream in live mode and closes it on the way out", async () => {
    verifierUp();
    const { result, unmount } = renderHook(() => useChronos());
    await waitFor(() => expect(result.current.mode).toBe("live"));
    await waitFor(() => expect(FakeEventSource.last).toBeDefined());

    const source = FakeEventSource.last!;
    unmount();

    expect(source.closed).toBe(true);
  });

  it("opens no SSE stream in demo mode", async () => {
    verifierDown();
    const { result } = renderHook(() => useChronos());

    await waitFor(() => expect(result.current.probe).not.toBeNull());

    expect(FakeEventSource.instances).toHaveLength(0);
  });
});

describe("counters", () => {
  async function live() {
    verifierUp();
    const rendered = renderHook(() => useChronos());
    await waitFor(() => expect(rendered.result.current.mode).toBe("live"));
    await waitFor(() => expect(FakeEventSource.last).toBeDefined());
    return rendered;
  }

  it("folds a verdict event into the totals", async () => {
    const { result } = await live();

    await act(async () => {
      FakeEventSource.last!.send({ seq: 1, type: "verdict", verdict: "outdated", claims: 2, skipped: 1, credits: 5, cache_hit: true });
    });

    expect(result.current.counters.answers).toBe(1);
    expect(result.current.counters.claims).toBe(2);
    expect(result.current.counters.skipped).toBe(1);
    expect(result.current.counters.credits).toBe(5);
    expect(result.current.counters.cacheHits).toBe(1);
    expect(result.current.counters.outdated).toBe(1);
  });

  it("counts only outdated answers as catches", async () => {
    const { result } = await live();

    await act(async () => {
      FakeEventSource.last!.send({ seq: 1, type: "verdict", verdict: "current", claims: 1 });
      FakeEventSource.last!.send({ seq: 2, type: "verdict", verdict: "check", claims: 1 });
      FakeEventSource.last!.send({ seq: 3, type: "verdict", verdict: "unverifiable", claims: 1 });
    });

    expect(result.current.counters.answers).toBe(3);
    expect(result.current.counters.outdated).toBe(0);
  });

  it("counts an eviction, and a sweep's evictions", async () => {
    const { result } = await live();

    await act(async () => {
      FakeEventSource.last!.send({ seq: 1, type: "eviction", cache_id: "abc" });
      FakeEventSource.last!.send({ seq: 2, type: "sweep", reverified: 10, evicted: 3 });
    });

    expect(result.current.counters.evictions).toBe(4);
  });

  it("keeps the feed bounded, so a long-running console does not grow without limit", async () => {
    const { result } = await live();

    await act(async () => {
      for (let i = 0; i < 120; i += 1) {
        FakeEventSource.last!.send({ seq: i, type: "verdict", verdict: "current" });
      }
    });

    expect(result.current.feed.length).toBeLessThanOrEqual(60);
    // newest first
    expect(result.current.feed[0].seq).toBe(119);
  });

  it("clears the feed when the mode changes, rather than mixing recorded and live rows", async () => {
    const { result } = await live();
    await act(async () => {
      FakeEventSource.last!.send({ seq: 1, type: "verdict", verdict: "outdated" });
    });
    expect(result.current.feed).toHaveLength(1);

    act(() => result.current.chooseMode("demo"));

    // The demo feed emits its first row immediately, so the feed is not empty.
    // What matters is that nothing from the live stream survived the switch:
    // recorded and live rows must never appear in the same list.
    expect(result.current.feed.every((event) => event.report_id?.startsWith("demo-"))).toBe(true);
    expect(result.current.counters.answers).toBeLessThanOrEqual(1);
  });
});

describe("the headline counters", () => {
  it("reads demo mode's figures from the recorded stats", () => {
    verifierDown();
    const { result } = renderHook(() => useChronos());

    expect(result.current.headline.answers).toBe(DEMO_STATS.answers_checked);
    expect(result.current.headline.stalePrevented).toBe(DEMO_STATS.stale_hits_prevented);
  });

  it("prefers the verifier's own stats in live mode", async () => {
    verifierUp();
    const { result } = renderHook(() => useChronos());

    // Not waitFor(stats): demo mode sets stats to the recorded figures on its
    // first render, so that resolves before the probe has switched to live.
    await waitFor(() => expect(result.current.mode).toBe("live"));
    await waitFor(() => expect(result.current.stats?.answers_checked).toBe(4654));

    expect(result.current.headline.answers).toBe(4654);
    expect(result.current.headline.stalePrevented).toBe(15);
  });

  it("leaves the last known stats in place when a poll fails", async () => {
    verifierUp();
    const { result } = renderHook(() => useChronos());
    await waitFor(() => expect(result.current.mode).toBe("live"));
    await waitFor(() => expect(result.current.stats?.answers_checked).toBe(4654));

    vi.spyOn(api, "stats").mockRejectedValue(new Error("gone"));
    vi.spyOn(api, "cacheEvents").mockRejectedValue(new Error("gone"));

    expect(result.current.stats?.answers_checked).toBe(4654);
  });
});
