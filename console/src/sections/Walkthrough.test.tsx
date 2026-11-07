/**
 * The opening stage and the guided walkthrough.
 *
 * The thing worth guarding here is that the tour drives the *real* page. Every
 * step has to point at a section that exists, and the ring has to end up on
 * exactly one of them — a tour that silently points at nothing still renders a
 * confident Next button.
 */

import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { api } from "../api";
import { FakeEventSource } from "../test/setup";
import { STEPS } from "./Walkthrough";

function offline() {
  vi.spyOn(api, "health").mockRejectedValue(new Error("connection refused"));
  vi.spyOn(api, "stats").mockRejectedValue(new Error("connection refused"));
  vi.spyOn(api, "cacheEvents").mockRejectedValue(new Error("connection refused"));
}

beforeEach(() => {
  FakeEventSource.reset();
  document.documentElement.removeAttribute("data-theme");
  offline();
  // jsdom has no layout, so scrollIntoView and scrollTo are absent.
  Element.prototype.scrollIntoView = vi.fn();
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
});

const startTour = async (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole("button", { name: /start the walkthrough/i }));

describe("the opening stage", () => {
  it("fills the first screen, so the demo opens on the argument", () => {
    const { container } = render(<App />);
    const hero = container.querySelector("section.hero");

    expect(hero).not.toBeNull();
    expect(hero!.className).toContain("stage");
  });

  it("offers both a guided and an unguided way in", () => {
    render(<App />);
    expect(screen.getByRole("button", { name: /start the walkthrough/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /explore on your own/i })).toBeInTheDocument();
  });

  it("shows a scroll cue, because nothing else says the page continues", () => {
    const { container } = render(<App />);
    const cue = container.querySelector(".cue");

    expect(cue).not.toBeNull();
    expect(cue!.getAttribute("data-hidden")).toBe("false");
  });

  it("hides the cue once the reader has scrolled", async () => {
    const { container } = render(<App />);

    await act(async () => {
      Object.defineProperty(window, "scrollY", { value: 400, configurable: true });
      window.dispatchEvent(new Event("scroll"));
    });

    expect(container.querySelector(".cue")!.getAttribute("data-hidden")).toBe("true");
  });

  it("the cue scrolls to the first section rather than nowhere", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    const spy = vi.spyOn(document.getElementById("path")!, "scrollIntoView");

    await user.click(within(container.querySelector(".cue") as HTMLElement).getByRole("button"));

    expect(spy).toHaveBeenCalled();
  });
});

describe("the walkthrough", () => {
  it("every step points at a section that exists", () => {
    const { container } = render(<App />);

    for (const step of STEPS) {
      if (step.target === null) continue;
      expect(
        container.querySelector(`#${step.target}`),
        `step "${step.title}" points at #${step.target}, which is not on the page`,
      ).not.toBeNull();
    }
  });

  it("is not running until it is started", () => {
    const { container } = render(<App />);
    expect(container.querySelector(".tour")).toBeNull();
  });

  it("opens on step 1 and says how far through it is", async () => {
    const user = userEvent.setup();
    render(<App />);

    await startTour(user);

    expect(screen.getByText(new RegExp(`Step 1 of ${STEPS.length}`))).toBeInTheDocument();
    expect(screen.getByRole("region", { name: /guided walkthrough/i })).toBeInTheDocument();
  });

  it("moves forward and back", async () => {
    const user = userEvent.setup();
    render(<App />);
    await startTour(user);

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText(new RegExp(`Step 2 of ${STEPS.length}`))).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByText(new RegExp(`Step 1 of ${STEPS.length}`))).toBeInTheDocument();
  });

  it("cannot go back off the start", async () => {
    const user = userEvent.setup();
    render(<App />);
    await startTour(user);

    expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
  });

  it("scrolls to each step's section", async () => {
    const user = userEvent.setup();
    render(<App />);
    const spy = vi.spyOn(document.getElementById(STEPS[1].target!)!, "scrollIntoView");

    await startTour(user);
    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(spy).toHaveBeenCalled();
  });

  it("rings exactly one section at a time", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await startTour(user);

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(container.querySelectorAll(".tour-on")).toHaveLength(1);
    expect(container.querySelector(`#${STEPS[1].target}`)!.className).toContain("tour-on");

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(container.querySelectorAll(".tour-on")).toHaveLength(1);
    expect(container.querySelector(`#${STEPS[2].target}`)!.className).toContain("tour-on");
  });

  it("leaves nothing lit when it closes", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await startTour(user);
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(container.querySelectorAll(".tour-on")).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: /close the walkthrough/i }));

    expect(container.querySelector(".tour")).toBeNull();
    expect(container.querySelectorAll(".tour-on")).toHaveLength(0);
  });

  it("the last step finishes rather than running off the end", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await startTour(user);

    for (let i = 0; i < STEPS.length - 1; i += 1) {
      await user.click(screen.getByRole("button", { name: "Next" }));
    }
    expect(screen.getByText(new RegExp(`Step ${STEPS.length} of ${STEPS.length}`))).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Finish" }));

    expect(container.querySelector(".tour")).toBeNull();
  });

  it("is a card clear of the page, not a bar across the foot of it", async () => {
    // It used to be fixed to the bottom and covered whatever step one was
    // pointing at.
    const user = userEvent.setup();
    const { container } = render(<App />);
    await startTour(user);

    const card = container.querySelector(".tour") as HTMLElement;
    expect(card).not.toBeNull();
    expect(card.querySelector(".tour-grip")).not.toBeNull();
  });

  it("can be dragged, and stays on screen", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await startTour(user);

    const card = container.querySelector(".tour") as HTMLElement;
    const grip = card.querySelector(".tour-grip") as HTMLElement;

    await act(async () => {
      grip.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 500, clientY: 300 }));
      grip.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 200, clientY: 200 }));
      grip.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
    });

    // It moved off its default anchor and onto explicit coordinates.
    expect(card.style.left).not.toBe("");
    expect(card.style.top).not.toBe("");
    expect(Number.parseFloat(card.style.left)).toBeGreaterThanOrEqual(8);
    expect(Number.parseFloat(card.style.top)).toBeGreaterThanOrEqual(64);
  });

  it("refuses to be dragged off the edge", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await startTour(user);

    const card = container.querySelector(".tour") as HTMLElement;
    const grip = card.querySelector(".tour-grip") as HTMLElement;

    await act(async () => {
      grip.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 100, clientY: 100 }));
      grip.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: -9999, clientY: -9999 }));
      grip.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
    });

    expect(Number.parseFloat(card.style.left)).toBeGreaterThanOrEqual(8);
    expect(Number.parseFloat(card.style.top)).toBeGreaterThanOrEqual(64);
  });

  it("is driveable from the keyboard", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await startTour(user);

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight" }));
    });
    expect(screen.getByText(new RegExp(`Step 2 of ${STEPS.length}`))).toBeInTheDocument();

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft" }));
    });
    expect(screen.getByText(new RegExp(`Step 1 of ${STEPS.length}`))).toBeInTheDocument();

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(container.querySelector(".tour")).toBeNull();
  });

  it("says nothing the page does not already show", () => {
    // A tour that claims something the product cannot do is a lie with a Next
    // button. Every step names a real panel, and the one step with no target
    // is the opening framing.
    const withoutTarget = STEPS.filter((s) => s.target === null);
    expect(withoutTarget).toHaveLength(1);
    expect(STEPS[0].target).toBeNull();
    for (const step of STEPS) {
      expect(step.title.length).toBeGreaterThan(0);
      expect(step.look.length).toBeGreaterThan(20);
    }
  });
});
