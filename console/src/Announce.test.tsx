/**
 * Every click says what it changed, and shows where.
 *
 * The fault these exist for: on the hosted demo, pressing Live opened a stream
 * the browser could never connect, and the header sat on "reconnecting" with
 * no reason given. And nothing told a reader what a nav link or a mode switch
 * had just done to the page.
 */

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App, { DEMO_CHOSEN, LIVE_BLOCKED, LIVE_CHOSEN } from "./App";
import { api } from "./api";
import { RING_MS } from "./announce";
import { SECTIONS, arrivalMessage } from "./sectionInfo";
import { FakeEventSource } from "./test/setup";

beforeEach(() => {
  FakeEventSource.reset();
  vi.spyOn(api, "health").mockRejectedValue(new Error("connection refused"));
  vi.spyOn(api, "stats").mockRejectedValue(new Error("connection refused"));
  vi.spyOn(api, "cacheEvents").mockRejectedValue(new Error("connection refused"));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const notice = () => screen.getByRole("status").textContent ?? "";

describe("Live on a page the browser will not let reach the verifier", () => {
  it("stays in Demo, opens no stream, and says why instead of 'reconnecting'", async () => {
    vi.spyOn(api, "blockedByBrowser").mockReturnValue(true);
    render(<App />);
    const opened = FakeEventSource.instances.length;

    await userEvent.click(screen.getByRole("button", { name: "Live" }));

    expect(notice()).toBe(LIVE_BLOCKED);
    expect(FakeEventSource.instances.length).toBe(opened);
    expect(screen.getByRole("button", { name: "Demo" }).getAttribute("aria-pressed")).toBe("true");
    // The header label, not the explanation, which quotes the old label.
    expect(document.querySelector(".replay")?.textContent).toBe("Recorded demo replay");
    expect(screen.getByRole("button", { name: "Live" }).getAttribute("title")).toContain("localhost:5173");
  });

  it("goes Live and says so where the browser allows it", async () => {
    vi.spyOn(api, "blockedByBrowser").mockReturnValue(false);
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Live" }));
    expect(notice()).toBe(LIVE_CHOSEN);
    await userEvent.click(screen.getByRole("button", { name: "Demo" }));
    expect(notice()).toBe(DEMO_CHOSEN);
  });
});

describe("a nav link", () => {
  it("says what the section shows and rings it for a moment", async () => {
    render(<App />);
    vi.useFakeTimers({ shouldAdvanceTime: true });

    const link = document.querySelector<HTMLAnchorElement>('nav.sections a[href="#cache"]')!;
    await userEvent.click(link);

    expect(notice()).toBe(arrivalMessage("cache"));
    const section = document.getElementById("cache")!;
    expect(section.classList.contains("flash")).toBe(true);

    act(() => vi.advanceTimersByTime(RING_MS + 50));
    expect(section.classList.contains("flash")).toBe(false);
  });

  it("has a sentence for every section, and every section exists", () => {
    const { container } = render(<App />);
    for (const [id, label, what] of SECTIONS) {
      expect(container.querySelector(`#${id}`), `#${id}`).not.toBeNull();
      expect(arrivalMessage(id)).toBe(`${label}: ${what}`);
      expect(what.length).toBeGreaterThan(30);
    }
  });
});

describe("the signal path", () => {
  it("says which step it is holding when one is pressed", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: /Tavily brings the evidence/ }));
    expect(notice()).toMatch(/^Holding step 6 of 8: Tavily brings the evidence\. Tavily owns it/);
  });

  it("no longer claims a search is narrowed to official domains", () => {
    // That filter was removed: it turned correct verdicts into unverifiable ones.
    render(<App />);
    expect(screen.queryByText(/official domains first/)).toBeNull();
  });
});
