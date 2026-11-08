/**
 * The console's single source of state.
 *
 * One hook owns the mode (live or demo), the feed, the counters and the
 * connection status, so no component has to know where its data came from.
 * Switching modes tears down one source and starts the other; nothing else in
 * the tree changes.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, subscribe, type CacheEvent, type FeedEvent, type Stats } from "./api";
import { DEMO_STATS, startDemoFeed } from "./demo";

export type Mode = "live" | "demo";
export type Connection = "connecting" | "open" | "error" | "demo";

const MAX_FEED = 60;

/** Counters the console derives from the feed, so they move as events arrive. */
export interface LiveCounters {
  answers: number;
  claims: number;
  skipped: number;
  outdated: number;
  evictions: number;
  credits: number;
  cacheHits: number;
  /** Stale catches bucketed into 10-second slots, for the sparkline. */
  catchesPerBucket: number[];
}

const EMPTY_COUNTERS: LiveCounters = {
  answers: 0,
  claims: 0,
  skipped: 0,
  outdated: 0,
  evictions: 0,
  credits: 0,
  cacheHits: 0,
  catchesPerBucket: new Array(30).fill(0),
};

export function useChronos() {
  // Demo mode is the default when the verifier is not reachable, which is
  // decided by the probe below rather than guessed.
  const [mode, setMode] = useState<Mode>("demo");
  const [connection, setConnection] = useState<Connection>("demo");

  // A ref, not state: the probe below is async, and a value captured in its
  // closure would be the one from the first render, which is always false.
  // That made the lock a no-op and let a late probe response overrule a mode
  // the operator had already picked.
  const modeLocked = useRef(false);

  const [feed, setFeed] = useState<FeedEvent[]>([]);
  const [counters, setCounters] = useState<LiveCounters>(EMPTY_COUNTERS);
  const [stats, setStats] = useState<Stats | null>(null);
  const [cacheEvents, setCacheEvents] = useState<CacheEvent[]>([]);
  const [probe, setProbe] = useState<{ reachable: boolean; offline: boolean; tavily: boolean } | null>(
    null,
  );

  const bucketStart = useRef<number>(Date.now());

  // --- probe the verifier once, and prefer live if it answers -------------
  useEffect(() => {
    let cancelled = false;
    // Known in advance on the public demo site: an https page cannot reach a
    // plain-http verifier, so asking would only log a browser error on every
    // load. Settle on demo without the round trip.
    if (api.blockedByBrowser()) {
      setProbe({ reachable: false, offline: true, tavily: false });
      return;
    }
    (async () => {
      try {
        const health = await api.health();
        if (cancelled) return;
        setProbe({ reachable: true, offline: health.offline_demo, tavily: health.tavily_configured });
        if (!modeLocked.current) setMode("live");
      } catch {
        if (cancelled) return;
        setProbe({ reachable: false, offline: true, tavily: false });
      }
    })();
    return () => {
      cancelled = true;
    };
    // Runs once. The lock it reads is a ref for the reason given at its
    // declaration, so this effect has no reactive dependency on it.
  }, []);

  // --- fold an event into the counters ------------------------------------
  const ingest = useCallback((event: FeedEvent) => {
    setFeed((previous) => [event, ...previous].slice(0, MAX_FEED));

    setCounters((previous) => {
      const next = { ...previous, catchesPerBucket: [...previous.catchesPerBucket] };

      // Advance the sparkline one slot per 10 seconds elapsed.
      const elapsed = Math.floor((Date.now() - bucketStart.current) / 10_000);
      if (elapsed > 0) {
        bucketStart.current += elapsed * 10_000;
        for (let i = 0; i < Math.min(elapsed, next.catchesPerBucket.length); i += 1) {
          next.catchesPerBucket.shift();
          next.catchesPerBucket.push(0);
        }
      }

      if (event.type === "verdict") {
        next.answers += 1;
        next.claims += event.claims ?? 0;
        next.skipped += event.skipped ?? 0;
        next.credits += event.credits ?? 0;
        if (event.cache_hit) next.cacheHits += 1;
        if (event.verdict === "outdated") {
          next.outdated += 1;
          const last = next.catchesPerBucket.length - 1;
          next.catchesPerBucket[last] = (next.catchesPerBucket[last] ?? 0) + 1;
        }
      } else if (event.type === "eviction") {
        next.evictions += 1;
      } else if (event.type === "sweep") {
        next.evictions += event.evicted ?? 0;
      }

      return next;
    });
  }, []);

  // --- run whichever source the mode selects ------------------------------
  useEffect(() => {
    setFeed([]);
    setCounters({ ...EMPTY_COUNTERS, catchesPerBucket: new Array(30).fill(0) });
    bucketStart.current = Date.now();

    if (mode === "demo") {
      setConnection("demo");
      setStats(DEMO_STATS);
      setCacheEvents([]);
      return startDemoFeed(ingest);
    }

    const unsubscribe = subscribe(ingest, setConnection);

    let cancelled = false;
    const poll = async () => {
      try {
        const [s, c] = await Promise.all([api.stats(), api.cacheEvents()]);
        if (cancelled) return;
        setStats(s);
        setCacheEvents(c.events);
      } catch {
        /* a failed poll leaves the last known values in place */
      }
    };
    void poll();
    const timer = window.setInterval(poll, 5000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
      unsubscribe();
    };
  }, [mode, ingest]);

  const chooseMode = useCallback((next: Mode) => {
    modeLocked.current = true;
    setMode(next);
  }, []);

  /** Counters shown in the hero: live ones in live mode, stats in demo mode. */
  const headline = useMemo(() => {
    if (mode === "demo") {
      return {
        answers: DEMO_STATS.answers_checked,
        stalePrevented: DEMO_STATS.stale_hits_prevented,
        cacheSaving: DEMO_STATS.claim_cache.hit_rate,
      };
    }
    return {
      answers: stats?.answers_checked ?? counters.answers,
      stalePrevented: stats?.stale_hits_prevented ?? counters.evictions,
      cacheSaving: stats?.claim_cache.hit_rate ?? 0,
    };
  }, [mode, stats, counters]);

  return {
    mode,
    chooseMode,
    connection,
    probe,
    feed,
    counters,
    stats,
    cacheEvents,
    headline,
  };
}
