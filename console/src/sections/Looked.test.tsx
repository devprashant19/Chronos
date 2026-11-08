/**
 * Layout and labelling faults that every unit test passed straight
 * through, because each only showed up when the screenshots were looked at.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DEMO_BRIEF } from "../demo";
import Briefing from "./Briefing";
import Inspector from "./Inspector";
import { cacheOutcome } from "./Playground";

describe("the briefing's open questions", () => {
  it("gives each question the two cells the design's grid expects", async () => {
    // .brief-cols li is grid-template-columns: 34px 1fr. A bare string fell
    // into the 34px column and wrapped one word to a line.
    const { container } = render(<Briefing mode="demo" />);
    await userEvent.click(screen.getByRole("button", { name: "Build the brief" }));

    const column = await waitFor(
      () => {
        const heading = screen.getByText("Open questions");
        const panel = heading.closest(".panel") as HTMLElement;
        expect(within(panel).getAllByRole("listitem")).toHaveLength(DEMO_BRIEF.open_questions.length);
        return panel;
      },
      { timeout: 6000 },
    );

    for (const li of within(column).getAllByRole("listitem")) {
      expect(li.children).toHaveLength(2);
      expect(li.children[0].classList.contains("sc")).toBe(true);
      expect(li.children[1].textContent!.length).toBeGreaterThan(10);
    }
    expect(container.querySelector(".brief-cols")).not.toBeNull();
  });
});

describe("the playground's labels", () => {
  it("reads the cache outcome off the response instead of assuming it", () => {
    // The panel titled "cache hit" once printed x-bifrost-cache-hit: false.
    expect(cacheOutcome({ "x-bifrost-cache-hit": "true" })).toBe("cache hit");
    expect(cacheOutcome({ "x-bifrost-cache-hit": "false" })).toBe("cache miss");
    expect(cacheOutcome({})).toBe("cache miss");
  });
});

describe("the inspector's opening report", () => {
  it("says it is a recording when the console has switched to Live", () => {
    // The console mounts in Demo and the probe switches it to Live, so the
    // inspector keeps the recorded example it started with. Under a LIVE
    // header that read as a live result until Check again was pressed.
    const { rerender } = render(<Inspector mode="demo" />);
    expect(screen.queryByText(/Recorded example/)).toBeNull();

    rerender(<Inspector mode="live" />);
    expect(screen.getByText("Recorded example · Check again runs it live")).toBeTruthy();
  });
});
