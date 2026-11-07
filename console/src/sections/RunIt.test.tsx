/**
 * The five-step driveable demo.
 *
 * Its whole value is that a judge can press a button and watch the cycle. So
 * the thing worth guarding is that the button always does something: a demo
 * that dead-ends on a failed request, with no explanation, is worse than one
 * that says it is replaying.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RunIt, { STAGES } from "./RunIt";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
});
afterEach(() => vi.unstubAllGlobals());

const start = async (u: ReturnType<typeof userEvent.setup>) =>
  u.click(screen.getByRole("button", { name: /^Start$/ }));

describe("the rail", () => {
  it("lists every stage", () => {
    const { container } = render(<RunIt mode="demo" />);
    expect(container.querySelectorAll(".run-rail li")).toHaveLength(STAGES.length);
    for (const s of STAGES) expect(screen.getByText(s.title)).toBeInTheDocument();
  });

  it("marks the current stage, and the ones already done", async () => {
    const user = userEvent.setup();
    const { container } = render(<RunIt mode="demo" />);
    await start(user);

    await waitFor(() => expect(container.querySelector(".run-rail li.now")).not.toBeNull());
    await user.click(screen.getByRole("button", { name: /^Next/ }));

    await waitFor(() => expect(container.querySelectorAll(".run-rail li.done")).toHaveLength(1));
    expect(container.querySelectorAll(".run-rail li.now")).toHaveLength(1);
  });
});

describe("driving it in demo mode", () => {
  it("starts not started, and says so", () => {
    render(<RunIt mode="demo" />);
    expect(screen.getByText(/Not started/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Start$/ })).toBeInTheDocument();
  });

  it("sends no request at all", async () => {
    const user = userEvent.setup();
    render(<RunIt mode="demo" />);
    await start(user);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows the headers once the first step has run", async () => {
    const user = userEvent.setup();
    const { container } = render(<RunIt mode="demo" />);
    await start(user);

    await waitFor(() =>
      expect(container.querySelector(".run-headers")!.textContent).toMatch(/x-chronos-/),
    );
  });

  it("reaches a verdict and an eviction by the last step", async () => {
    const user = userEvent.setup();
    const { container } = render(<RunIt mode="demo" />);
    await start(user);

    for (let i = 0; i < STAGES.length - 1; i += 1) {
      await user.click(screen.getByRole("button", { name: /^Next/ }));
    }

    expect(container.querySelector(".run-answer.gone")).not.toBeNull();
    expect(container.textContent).toMatch(/the entry is gone/i);
    expect(screen.getByRole("img", { name: /Score \d+ out of 100/ })).toBeInTheDocument();
  });

  it("offers to run again rather than running off the end", async () => {
    const user = userEvent.setup();
    const { container } = render(<RunIt mode="demo" />);
    await start(user);
    for (let i = 0; i < STAGES.length - 1; i += 1) {
      await user.click(screen.getByRole("button", { name: /^Next/ }));
    }

    const again = screen.getByRole("button", { name: /Run it again/ });
    await user.click(again);

    expect(screen.getByText(/Not started/i)).toBeInTheDocument();
    expect(container.querySelector(".run-answer")).toBeNull();
  });

  it("resets cleanly", async () => {
    const user = userEvent.setup();
    const { container } = render(<RunIt mode="demo" />);
    await start(user);
    await user.click(screen.getByRole("button", { name: /^Next/ }));

    await user.click(screen.getByRole("button", { name: /^Reset$/ }));

    expect(screen.getByText(/Not started/i)).toBeInTheDocument();
    expect(container.querySelectorAll(".run-rail li.done")).toHaveLength(0);
  });
});

describe("live mode when the gateway is not reachable", () => {
  it("falls back to the recorded exchange instead of dead-ending", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const user = userEvent.setup();
    const { container } = render(<RunIt mode="live" />);

    await start(user);

    // It advanced: the stage panel is showing, not the empty state.
    await waitFor(() => expect(container.querySelector(".run-line")).not.toBeNull());
    expect(screen.getByText(/Recorded\./)).toBeInTheDocument();
    expect(container.textContent).toMatch(/Showing the recorded exchange instead/);
    // And the button still moves forward.
    expect(screen.getByRole("button", { name: /^Next/ })).toBeEnabled();
  });

  it("treats a response with no x-chronos headers as a failure worth saying out loud", async () => {
    // A 200 with no Chronos headers means the plugin did not run, or CORS hid
    // them. Showing an empty panel would look like "nothing to report".
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    const user = userEvent.setup();
    render(<RunIt mode="live" />);

    await start(user);

    await waitFor(() => expect(screen.getByText(/Recorded\./)).toBeInTheDocument());
  });

  it("does send a request in live mode", async () => {
    const user = userEvent.setup();
    render(<RunIt mode="live" />);
    await start(user);
    expect(fetch).toHaveBeenCalled();
  });
});

describe("what it claims", () => {
  it("says which mode it is in, so a replay is never passed off as live", () => {
    const { rerender, container } = render(<RunIt mode="demo" />);
    expect(container.textContent).toMatch(/replays a recorded exchange/i);

    rerender(<RunIt mode="live" />);
    expect(container.textContent).toMatch(/sends a real request/i);
  });

  it("every stage has something to say", () => {
    for (const s of STAGES) {
      expect(s.title.length).toBeGreaterThan(0);
      expect(s.says.length).toBeGreaterThan(30);
    }
  });
});
