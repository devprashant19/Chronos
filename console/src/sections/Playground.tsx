/**
 * The playground: the same question, twice, with the real response headers.
 *
 * This section exists to answer the obvious scepticism about a demo like this
 * — that the checking is happening in the UI. It is not. These are the headers
 * the browser received from the gateway, printed verbatim. The first call is
 * served from Bifrost's cache and caught; Chronos evicts it; the second call
 * misses the cache and comes back fresh.
 *
 * In demo mode it replays a recorded exchange, labelled as such, because
 * without a running gateway there are no real headers to show and inventing
 * them would defeat the point of the section.
 */

import { useCallback, useState } from "react";

import { BIFROST_URL, chatThroughGateway } from "../api";

interface Exchange {
  label: string;
  status: number;
  ms: number;
  headers: Record<string, string>;
  answer: string;
  recorded?: boolean;
}

const PROMPT = "What is the latest version of Python?";

const RECORDED: Exchange[] = [
  {
    label: "Request 1 · cache hit",
    status: 200,
    ms: 3180,
    recorded: true,
    headers: {
      "x-bifrost-cache-hit": "true",
      "x-bifrost-cache-id": "2238699d-3c9e-5bca-97a3-88a55a682498",
      "x-bifrost-cache-hit-type": "direct",
      "x-chronos-mode": "annotate",
      "x-chronos-verdict": "outdated",
      "x-chronos-score": "36",
      "x-chronos-claims": "1",
      "x-chronos-stale": "1",
      "x-chronos-class": "version",
      "x-chronos-ttl": "21600",
      "x-chronos-report": "/v1/reports/9f3af38efa7243de",
      "x-chronos-latency-ms": "28",
    },
    answer: "The latest stable release of Python is 3.11, which came out in October 2022…",
  },
  {
    label: "Request 2 · after eviction",
    status: 200,
    ms: 214,
    recorded: true,
    headers: {
      "x-bifrost-cache-hit": "false",
      "x-bifrost-cache-id": "2238699d-3c9e-5bca-97a3-88a55a682498",
      "x-chronos-mode": "annotate",
      "x-chronos-verdict": "pending",
      "x-chronos-class": "version",
      "x-chronos-ttl": "21600",
    },
    answer: "(fresh answer from the provider)",
  },
];

export default function Playground({ mode }: { mode: "live" | "demo" }) {
  const [exchanges, setExchanges] = useState<Exchange[]>(mode === "demo" ? RECORDED : []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    setExchanges([]);

    if (mode === "demo") {
      setExchanges([RECORDED[0]]);
      await new Promise((r) => setTimeout(r, 1200));
      setExchanges(RECORDED);
      setBusy(false);
      return;
    }

    const cacheKey = `console-${Date.now()}`;
    try {
      // Prime the cache, then ask again: the second call is the cache hit that
      // Chronos checks and evicts, and the third shows the miss that follows.
      await chatThroughGateway(PROMPT, cacheKey);
      // The semantic cache stores the answer after the response is sent, so a
      // request issued the instant the first returns can still miss. Measured:
      // back to back, the "hit" came back x-bifrost-cache-hit: false.
      await new Promise((r) => setTimeout(r, 1200));

      const hit = await chatThroughGateway(PROMPT, cacheKey);
      setExchanges([
        {
          label: `Request 1 · ${cacheOutcome(hit.headers)}`,
          status: hit.status,
          ms: Math.round(hit.ms),
          headers: hit.headers,
          answer: answerOf(hit.body),
        },
      ]);

      // Eviction is issued as the verdict lands, so give it a moment.
      await new Promise((r) => setTimeout(r, 2500));

      const after = await chatThroughGateway(PROMPT, cacheKey);
      setExchanges((prev) => [
        ...prev,
        {
          label: `Request 2 · ${cacheOutcome(after.headers)} · after eviction`,
          status: after.status,
          ms: Math.round(after.ms),
          headers: after.headers,
          answer: answerOf(after.body),
        },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [mode]);

  return (
    <section id="playground" className="wrap" style={{ paddingBlock: 36 }}>
      <div className="sec-head">
        <div>
          <p className="eyebrow">Playground · raw gateway traffic</p>
          <h2 style={{ fontSize: "clamp(24px,3vw,38px)" }}>Same question, twice</h2>
        </div>
        <p>
          This is the proof that Chronos lives in the gateway rather than in this page. The first
          call is served from Bifrost&rsquo;s cache and caught. Chronos evicts it. The second call
          misses the cache and goes to the provider for a fresh answer. The headers below are
          printed exactly as the browser received them.
        </p>
      </div>

      <div className="panel" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn" onClick={run} disabled={busy}>
            {busy ? "Running…" : "Send the same question twice"}
          </button>
          {/* The URL is one unbreakable word: at 360px it pushed the page 3px wide. */}
          <span className="eyebrow" style={{ minWidth: 0, overflowWrap: "anywhere" }}>
            POST {mode === "demo" ? "bifrost" : BIFROST_URL}/v1/chat/completions · the plugin
            derives the cache key from the prompt
          </span>
          {error ? (
            <span className="pill bad" title={error}>
              gateway unreachable
            </span>
          ) : null}
        </div>
        <p className="mono" style={{ margin: "10px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
          &gt; {PROMPT}
        </p>
      </div>

      <div className="term-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))", gap: 20 }}>
        {(exchanges.length === 0 ? [null, null] : exchanges).map((exchange, i) => (
          <div className="panel" key={i} style={{ padding: 0, overflow: "hidden" }}>
            <div
              className="term-title"
              style={{
                padding: "10px 14px",
                borderBottom: "1px solid var(--rule)",
                display: "flex",
                justifyContent: "space-between",
                gap: 8,
                flexWrap: "wrap",
                alignItems: "center",
              }}
            >
              <span className="eyebrow">{exchange?.label ?? `Request ${i + 1}`}</span>
              {exchange ? (
                <span
                  className={`pill ${
                    exchange.headers["x-bifrost-cache-hit"] === "true" ? "warn" : "ok"
                  }`}
                >
                  {exchange.headers["x-bifrost-cache-hit"] === "true" ? "cache hit" : "cache miss"}
                </span>
              ) : (
                <span className="pill mute">waiting</span>
              )}
            </div>

            <pre
              className="mono"
              style={{
                margin: 0,
                padding: 14,
                fontSize: 12,
                lineHeight: 1.7,
                overflowX: "auto",
                color: "var(--ink-2)",
                background: "transparent",
              }}
            >
              {exchange
                ? [
                    `HTTP ${exchange.status}  (${exchange.ms} ms)`,
                    ...Object.entries(exchange.headers)
                      .sort(([a], [b]) => a.localeCompare(b))
                      .map(([k, v]) => `${k}: ${v}`),
                    "",
                    exchange.answer,
                  ].join("\n")
                : "(no request sent yet)"}
            </pre>

            {exchange?.recorded ? (
              <p style={{ margin: 0, padding: "8px 14px", borderTop: "1px dashed var(--rule)", fontSize: 12, color: "var(--ink-3)" }}>
                Recorded exchange. Switch to Live mode to send real requests.
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function answerOf(body: unknown): string {
  try {
    const content = (body as { choices?: Array<{ message?: { content?: string } }> })?.choices?.[0]
      ?.message?.content;
    return content ? `${content.slice(0, 160)}${content.length > 160 ? "…" : ""}` : "(no content)";
  } catch {
    return "(no content)";
  }
}

/**
 * What the cache actually did, read off the response rather than assumed.
 * The labels used to be fixed strings, and on a live run the panel titled
 * "cache hit" printed x-bifrost-cache-hit: false underneath itself.
 */
export function cacheOutcome(headers: Record<string, string>): string {
  return headers["x-bifrost-cache-hit"] === "true" ? "cache hit" : "cache miss";
}
