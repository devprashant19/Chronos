/**
 * What the console actually renders.
 *
 * Before these existed, four checks in scripts/verify-all.sh proved nginx
 * served the bundle and the gateway exposed the headers, and nothing proved a
 * section appeared at all. A component could throw on mount and only a person
 * looking at the page would notice.
 */

import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { api } from "./api";
import { DEMO_CLAIMS } from "./demo";
import { FakeEventSource } from "./test/setup";

/** No verifier: the console must still render, in demo mode. */
function offline() {
  vi.spyOn(api, "health").mockRejectedValue(new Error("connection refused"));
  vi.spyOn(api, "stats").mockRejectedValue(new Error("connection refused"));
  vi.spyOn(api, "cacheEvents").mockRejectedValue(new Error("connection refused"));
}

beforeEach(() => {
  FakeEventSource.reset();
  document.documentElement.removeAttribute("data-theme");
  offline();
});

describe("the page", () => {
  it("renders every section the nav points at", () => {
    const { container } = render(<App />);

    for (const id of ["path", "traffic", "inspect", "cache", "playground", "bench", "brief"]) {
      expect(container.querySelector(`#${id}`), `section #${id} is missing`).not.toBeNull();
    }
  });

  it("has a nav link for every section, and no link that points nowhere", () => {
    const { container } = render(<App />);
    const anchors = Array.from(container.querySelectorAll('nav.sections a[href^="#"]'));

    expect(anchors.length).toBeGreaterThan(0);
    for (const a of anchors) {
      const id = a.getAttribute("href")!.slice(1);
      expect(container.querySelector(`#${id}`), `nav links to #${id}, which does not exist`).not.toBeNull();
    }
  });

  it("names itself, with the struck-through dial the design reference specifies", () => {
    const { container } = render(<App />);
    const brand = container.querySelector(".brand")!;

    expect(brand).toHaveTextContent("Chronos");
    const paths = Array.from(brand.querySelectorAll("path"));
    // The slash is the point of the product, not decoration: it was dropped
    // once and nothing noticed until the screenshots were compared with
    // design/chronos-console.html.
    expect(paths.some((p) => p.getAttribute("stroke") === "var(--bad)")).toBe(true);
  });

  it("renders with no backend at all, rather than showing an error page", () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });
});

describe("demo mode", () => {
  it("says on the page that the traffic is recorded", async () => {
    render(<App />);
    expect(await screen.findByText(/Demo mode\./i)).toBeInTheDocument();
    // Said twice on purpose: once in the header badge, once in the banner
    // over the feed. Neither is enough on its own.
    expect(screen.getAllByText(/recorded replay/i).length).toBeGreaterThanOrEqual(2);
  });

  it("offers both modes, and marks the current one", () => {
    render(<App />);

    const demo = screen.getByRole("button", { name: "Demo" });
    expect(demo).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Live" })).toBeInTheDocument();
  });
});

describe("the theme toggle", () => {
  it("switches the document between the two themes the reference defines", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: /colour scheme/i }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    await user.click(screen.getByRole("button", { name: /colour scheme/i }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });
});

describe("the inspector", () => {
  it("highlights every claim in place, as a button you can select", () => {
    const { container } = render(<App />);
    const inspector = within(container.querySelector("#inspect") as HTMLElement);

    const claims = inspector.getAllByRole("button", { name: /^Claim:/ });
    expect(claims).toHaveLength(DEMO_CLAIMS.length);
  });

  it("names the subject and the score in each claim's label, for a screen reader", () => {
    const { container } = render(<App />);
    const inspector = within(container.querySelector("#inspect") as HTMLElement);

    expect(inspector.getByRole("button", { name: /Claim: Python .*scored 36/ })).toBeInTheDocument();
  });

  it("colours a claim by its verdict, and greys a timeless one", () => {
    const { container } = render(<App />);
    const buttons = Array.from(
      (container.querySelector("#inspect") as HTMLElement).querySelectorAll("button.claim"),
    );

    const outdated = DEMO_CLAIMS.findIndex((c) => c.verdict === "outdated");
    const timeless = DEMO_CLAIMS.findIndex((c) => c.class === "timeless");
    expect(outdated, "the demo log needs an outdated claim").toBeGreaterThanOrEqual(0);
    expect(timeless, "the demo log needs a timeless claim").toBeGreaterThanOrEqual(0);

    expect(buttons[outdated].className).toContain("bad");
    // A timeless claim was never checked, so it must not read as "verified
    // current": it gets its own grey, not the green one.
    expect(buttons[timeless].className).toContain("time");
    expect(buttons[timeless].className).not.toContain("ok");
  });

  it("shows every term of the formula, so the score can be argued with", () => {
    const { container } = render(<App />);
    const inspector = within(container.querySelector("#inspect") as HTMLElement);

    for (const term of [/A agreement/, /R recency/, /Q quality/, /C confidence/, /V volatility/, /D decay/]) {
      expect(inspector.getByText(term)).toBeInTheDocument();
    }
    expect(inspector.getByText(/0\.45A \+ 0\.25R \+ 0\.15Q \+ 0\.15C/)).toBeInTheDocument();
  });

  it("shows the sources that disagreed, with what they said", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    const inspector = within(container.querySelector("#inspect") as HTMLElement);

    // Two of the demo claims are about Python; the first is the stale one.
    await user.click(inspector.getAllByRole("button", { name: /^Claim: Python/ })[0]);

    const contradicting = DEMO_CLAIMS[0].evidence.filter((e) => e.call === "contradicts");
    expect(contradicting.length, "the demo claim needs contradicting evidence").toBeGreaterThan(0);
    expect(inspector.getAllByText(/contradicts/i).length).toBeGreaterThanOrEqual(contradicting.length);
  });

  it("selecting a different claim changes the breakdown", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    const inspector = within(container.querySelector("#inspect") as HTMLElement);
    const claims = inspector.getAllByRole("button", { name: /^Claim:/ });

    const before = (container.querySelector("#inspect") as HTMLElement).textContent;
    await user.click(claims[claims.length - 1]);
    const after = (container.querySelector("#inspect") as HTMLElement).textContent;

    expect(after).not.toBe(before);
  });
});

describe("the cache section", () => {
  it("lists every volatility class", () => {
    const { container } = render(<App />);
    const cache = within(container.querySelector("#cache") as HTMLElement);

    for (const cls of ["market", "pricing", "version", "leadership", "law", "events", "timeless"]) {
      expect(cache.getByText(cls)).toBeInTheDocument();
    }
  });

  it("shows the TTL each class allows", () => {
    const { container } = render(<App />);
    const cache = container.querySelector("#cache") as HTMLElement;

    // These are the policy, and they are stated in three places: the plugin,
    // the verifier and here. If this list drifts the console argues for a
    // policy the gateway does not implement.
    for (const ttl of ["15m", "6h", "12h", "1d", "7d"]) {
      expect(cache.textContent, `the cache table should show ${ttl}`).toContain(ttl);
    }
  });

  it("says that an answer's TTL is the minimum across its claims", () => {
    const { container } = render(<App />);
    const cache = container.querySelector("#cache") as HTMLElement;
    expect(cache.textContent).toMatch(/minimum/i);
  });
});

describe("the live feed", () => {
  it("renders a row for an event that arrives on the stream", async () => {
    vi.spyOn(api, "health").mockResolvedValue({ status: "ok", offline_demo: false, tavily_configured: true });
    vi.spyOn(api, "stats").mockRejectedValue(new Error("not needed for this test"));
    vi.spyOn(api, "cacheEvents").mockRejectedValue(new Error("not needed for this test"));

    const { container } = render(<App />);
    await screen.findByRole("button", { name: "Live" });
    await vi.waitFor(() => expect(FakeEventSource.last).toBeDefined());

    const traffic = container.querySelector("#traffic") as HTMLElement;
    // Assert on something only this event could have put there. Matching a
    // verdict word alone passes without any event at all, because the panel's
    // own labels read "Answers checked" and "Claims checked".
    expect(traffic.textContent).not.toContain("Who is the CEO of Twitter?");

    await act(async () => {
      FakeEventSource.last!.send({
        seq: 1,
        type: "verdict",
        verdict: "outdated",
        score: 33,
        claims: 1,
        stale: 1,
        prompt: "Who is the CEO of Twitter?",
        model: "groq/openai/gpt-oss-20b",
        cache_hit: true,
      });
    });

    await vi.waitFor(() => expect(traffic.textContent).toContain("Who is the CEO of Twitter?"));
    expect(traffic.textContent).toContain("33");
  });

  it("keeps evictions out of the verdict feed, and in the cache ledger", async () => {
    // Two panels, two sources, on purpose: the traffic feed lists answers that
    // were checked, and the cache ledger lists entries that were deleted. In
    // demo mode the ledger is built from the replayed feed; in live mode it
    // comes from GET /v1/cache/events.
    const { container } = render(<App />);

    const traffic = container.querySelector("#traffic") as HTMLElement;
    const cache = container.querySelector("#cache") as HTMLElement;

    await vi.waitFor(() => expect(cache.textContent).toMatch(/demo-[0-9a-f]+/));
    expect(cache.textContent).toMatch(/served an outdated claim|evicted/i);
    expect(traffic.textContent).not.toMatch(/demo-[0-9a-f]{6,}/);
  });
});
