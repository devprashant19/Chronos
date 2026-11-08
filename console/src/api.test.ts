/**
 * The data layer.
 *
 * These are the console's quiet failure modes: a header the gateway did not
 * expose, a malformed SSE frame, a failed poll. None of them throws anywhere a
 * user can see, which is exactly why they need tests.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, chatThroughGateway, subscribe, VERIFIER_URL, BIFROST_URL } from "./api";
import { FakeEventSource } from "./test/setup";

const ok = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  FakeEventSource.reset();
});

describe("base URLs", () => {
  it("has no trailing slash, so paths do not become //v1/stats", () => {
    expect(VERIFIER_URL.endsWith("/")).toBe(false);
    expect(BIFROST_URL.endsWith("/")).toBe(false);
  });

  it("points the two surfaces at different places", () => {
    // They are different services on different ports. Collapsing them was a
    // real misconfiguration during development: the console silently polled
    // the gateway for /v1/stats and showed nothing.
    expect(VERIFIER_URL).not.toBe(BIFROST_URL);
  });
});

describe("blockedByBrowser", () => {
  it("knows an https page cannot reach a plain-http verifier", () => {
    // Mixed content, and for localhost a private-network request: the browser
    // refuses before anything leaves the tab. On the public demo site this
    // was a CORS error in the console on every load, for a probe that could
    // never have succeeded.
    expect(api.blockedByBrowser("https:")).toBe(true);
    expect(api.blockedByBrowser("http:")).toBe(false);
  });

  it("reads the page's own protocol by default", () => {
    // jsdom serves these tests over http, which is the local stack's case.
    expect(api.blockedByBrowser()).toBe(false);
  });
});

describe("api", () => {
  it("sends JSON and parses the report", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ report_id: "abc", verdict: "outdated", score: 39 }));
    vi.stubGlobal("fetch", fetchMock);

    const report = await api.verify({ answer: "x" });

    expect(report.verdict).toBe("outdated");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${VERIFIER_URL}/v1/verify`);
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("throws with the status and the body when the verifier refuses", async () => {
    // A fresh Response per call: a body can only be read once, and reusing one
    // makes the second assertion see an empty string.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response("answer must not be empty", { status: 422, statusText: "Unprocessable Entity" }),
      ),
    );

    await expect(api.verify({ answer: "" })).rejects.toThrow(/422/);
    await expect(api.verify({ answer: "" })).rejects.toThrow(/answer must not be empty/);
  });

  it("asks for a bounded number of reports rather than all of them", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ reports: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await api.reports();

    expect(fetchMock.mock.calls[0][0]).toMatch(/\/v1\/reports\?limit=\d+$/);
  });
});

describe("chatThroughGateway", () => {
  /**
   * The playground exists to prove the check happens in the gateway rather
   * than in this UI, so it must show headers the browser actually received.
   * If the gateway stops publishing them on Access-Control-Expose-Headers the
   * browser hides them and this panel goes blank, while curl still looks fine.
   */
  it("keeps only the gateway's own headers, and drops the rest", async () => {
    const headers = new Headers({
      "content-type": "application/json",
      "x-chronos-verdict": "outdated",
      "x-chronos-score": "39",
      "x-bifrost-cache-hit": "true",
      "x-bf-cache-key": "demo",
      "set-cookie": "session=secret",
      server: "nginx",
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200, headers })));

    const result = await chatThroughGateway("hello", "demo");

    expect(result.headers["x-chronos-verdict"]).toBe("outdated");
    expect(result.headers["x-bifrost-cache-hit"]).toBe("true");
    expect(result.headers["x-bf-cache-key"]).toBe("demo");
    expect(result.headers).not.toHaveProperty("server");
    expect(result.headers).not.toHaveProperty("content-type");
    expect(result.headers).not.toHaveProperty("set-cookie");
  });

  /**
   * The browser must NOT send x-bf-cache-key.
   *
   * The gateway's CORS preflight publishes a fixed Access-Control-Allow-Headers
   * list and that header is not on it, so a browser refuses the request outright
   * -- "Failed to fetch", before anything reaches the gateway. curl never sees
   * it, which is exactly why it survived: every end-to-end check sends the
   * header happily from a shell.
   *
   * The key is derived from the prompt by the plugin instead
   * (CHRONOS_AUTO_CACHE_KEY), so two identical prompts still land on one cache
   * entry and the miss/hit/evict cycle still works from a page.
   */
  it("does not send a header the gateway's CORS policy would refuse", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await chatThroughGateway("What is the latest version of Python?", "demo-42");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BIFROST_URL}/v1/chat/completions`);
    expect(init.headers).not.toHaveProperty("x-bf-cache-key");
    expect(Object.keys(init.headers)).toEqual(["Content-Type"]);
    expect(JSON.parse(init.body).messages[0].content).toMatch(/latest version of Python/);
  });

  it("survives a response that is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("502 Bad Gateway", { status: 502 })));

    const result = await chatThroughGateway("hello", "demo");

    expect(result.status).toBe(502);
    expect(result.body).toBeNull();
  });
});

describe("subscribe", () => {
  it("reports connecting, then open", () => {
    const status = vi.fn();
    subscribe(() => {}, status);

    expect(status).toHaveBeenCalledWith("connecting");
    FakeEventSource.last!.open();
    expect(status).toHaveBeenCalledWith("open");
  });

  it("asks for a replay, so a console opened mid-run is not empty", () => {
    subscribe(() => {});
    expect(FakeEventSource.last!.url).toContain("/v1/stream");
    expect(FakeEventSource.last!.url).toMatch(/replay=\d+/);
  });

  it("hands parsed events upward", () => {
    const onEvent = vi.fn();
    subscribe(onEvent);

    FakeEventSource.last!.send({ seq: 1, type: "verdict", verdict: "outdated", score: 39 });

    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ type: "verdict", score: 39 }));
  });

  it("drops a malformed frame instead of taking the feed down", () => {
    const onEvent = vi.fn();
    subscribe(onEvent);

    expect(() => FakeEventSource.last!.send("{not json")).not.toThrow();
    expect(onEvent).not.toHaveBeenCalled();

    // and the stream keeps working afterwards
    FakeEventSource.last!.send({ seq: 2, type: "verdict" });
    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it("closes the stream when unsubscribed", () => {
    const stop = subscribe(() => {});
    const source = FakeEventSource.last!;

    stop();

    expect(source.closed).toBe(true);
  });

  it("reports an error rather than throwing when EventSource cannot be constructed", () => {
    vi.stubGlobal(
      "EventSource",
      class {
        constructor() {
          throw new Error("blocked");
        }
      },
    );
    const status = vi.fn();

    expect(() => subscribe(() => {}, status)).not.toThrow();
    expect(status).toHaveBeenCalledWith("error");
  });
});
