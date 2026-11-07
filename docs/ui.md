# The console

One page, seven sections, no routing. It is an instrument panel rather than a dashboard:
everything on it is either a live reading or a recorded replay, and it says which.

The design is not mine to change. `design/chronos-console.html` is the reference the console
is built to match — tokens, type scale, layout and motion — and `console/src/instrument.css`
is lifted from it verbatim rather than reimplemented. When the two disagree, the reference is
right. [design/chronos-console.md](../design/chronos-console.md) is that reference as plain
text, which is the readable way to diff the copy.

## What it looks like

| | |
|---|---|
| ![The console hero: a headline reading "Your cache remembers. Chronos checks.", three live counters, and a chart of how fast a cached fact goes stale by volatility class](screenshots/console-hero.png) | ![The inspector: a cached answer with four highlighted claim spans, one scored 36 and marked outdated, beside its score breakdown and two contradicting sources](screenshots/console-inspector.png) |
| **The hero.** Live counters from `/v1/stats`, and the decay curves that explain why one TTL cannot fit every fact. | **The inspector.** The signature mechanism, visible: claims lit up inside the answer, the score with every term of the formula, and the pages that disagreed, quoted. |
| ![The cache section: seven volatility classes with their TTLs as bars against a flat 24-hour default, beside a ledger of fifteen evictions with their cache ids and scores](screenshots/console-cache.png) | ![The signal path in dark mode: five boxes from Client through Chronos, the semantic cache and the provider to Tavily, with an eight-step walkthrough advancing on the right](screenshots/console-dark.png) |
| **The cache.** What Chronos chose against a flat default, and a ledger of real evictions with their cache ids. | **The signal path, in dark mode.** Eight steps through one request, colour-coded by who owns each — Bifrost, Tavily, Chronos. |
| ![The traffic feed: a live SSE stream of verified answers with verdicts and scores](screenshots/console-traffic.png) | ![The benchmark section showing a recorded request pair — a cache hit with x-chronos-verdict outdated, then a miss after eviction — above the StaleBench results read from bench/results.json](screenshots/console-bench.png) |
| **Traffic.** Every verification as it finishes, over SSE. | **Playground and benchmark.** A real recorded exchange with its headers, then the numbers read straight from `bench/results.json`. |

Every one of these is regenerated from the running console by
`node <readme-suite>/scripts/shots.mjs --file docs/screenshots/shots.json`, with the shot
list kept in the repo at `docs/screenshots/shots.json`. They are captures, not mock-ups.

## The sections

| Anchor | Section | What it reads | Source |
| :--- | :--- | :--- | :--- |
| `#path` | Signal path | nothing; it is an explanation | `console/src/App.tsx` |
| `#traffic` | Traffic | `GET /v1/stream` over SSE, `GET /v1/stats` | `console/src/sections/Traffic.tsx` |
| `#inspect` | Inspector | `POST /v1/verify` on demand | `console/src/sections/Inspector.tsx` |
| `#cache` | Cache | `GET /v1/cache/events`, `GET /v1/stats` | `console/src/sections/Cache.tsx` |
| `#playground` | Playground | `POST /v1/chat/completions` through the gateway | `console/src/sections/Playground.tsx` |
| `#bench` | Benchmark | `public/results.json`, copied from `bench/results.json` | `console/src/sections/Benchmark.tsx` |
| `#brief` | Briefing | `POST /v1/brief` | `console/src/sections/Briefing.tsx` |

`console/src/useChronos.ts` owns the mode, the feed, the counters and the connection. Every
section is a reader; none of them fetches on its own except on a click.

## Live and demo

The switch in the header picks between two sources of everything on the page.

- **Live** opens the SSE stream and shows real verifications. The dot beside it is green when
  the stream is open and red when it is not.
- **Demo** replays `console/src/demo.ts`, a recorded log, and labels itself: *"Demo mode.
  Traffic on this page is a recorded replay."* Nothing in demo mode is presented as live.

**Demo is the default**, and it is the default on purpose: judging and screenshots have to
work with no stack running and no keys. The console falls back to it whenever the verifier
cannot be reached, rather than showing an empty page or an error.

## Colour means ownership

Three product colours, used for nothing else. If a box is blue, Bifrost owns that step.

| Token | Light | Dark | Means |
| :--- | :--- | :--- | :--- |
| `--bifrost` | `#2744E0` | `#7088FF` | Bifrost owns it |
| `--tavily` | `#A23F86` | `#E27CC5` | Tavily owns it |
| `--chronos` | `#13161B` | `#E6E9EE` | Chronos owns it |
| `--ok` | `#1C8752` | `#4CC38A` | `current` |
| `--warn` | `#B57600` | `#E5AE3B` | `check` |
| `--bad` | `#C93A28` | `#F06B56` | `outdated` |
| `--paper` / `--panel` | `#ECEEF1` / `#F8F9FA` | `#0E1115` / `#151920` | Ground, and panels on it |

Dark mode follows `prefers-color-scheme` and is overridable with the header toggle, which
sets `data-theme` on `:root`. Both are in the reference and both are in
`console/src/instrument.css`.

Type: `Big Shoulders Display` for headings, `Instrument Sans` for body, `IBM Plex Mono` for
anything that is a value — ids, headers, scores, formulas. All three from Google Fonts, with
system fallbacks named so the layout holds if the fonts do not load.

## The mark

Two drawings of one idea, and the reason is small sizes.

The header mark is the reference's: a dial with an hour hand, struck through in `--bad`
(`design/chronos-console.html`, line 249). At 22 px beside the word *Chronos* it reads
cleanly, and the slash is the point of the product rather than decoration.

Below about 32 px the slash crosses both the hands and the ring and the whole mark fills in.
So the favicon and the app tile are a redraw of the same idea — a dial with the last quarter
filled in `--bad` — which survives 16 px. The source is `design/icon.svg`;
`console/public/favicon.svg` is a copy of it, and `console/public/icon-512.png` and
`docs/icon.png` are rendered from it.

## How it is checked

There are **no tests for the console**. It is checked three ways, none of which is a unit
test, and that is a real gap:

- `npm run typecheck` in `console/`, which `scripts/verify-all.sh` does not currently run.
- Four checks in `scripts/verify-all.sh`: that nginx serves the shell, that the built bundle
  is referenced, that `public/results.json` is served, and that the gateway publishes the
  nine `x-chronos-*` headers on `Access-Control-Expose-Headers` — without which the console
  would show blanks in a browser while `curl` looked fine.
- The screenshots above, which are looked at.

No rendering assertion, no interaction test, no visual diff against the reference. A section
could break and only a person would notice.

## Changing it

```bash
cd console
npm install
npm run dev        # Vite on :5173, talking to the stack's :8000 and :8080
npm run typecheck
npm run build
```

`VITE_VERIFIER_URL` and `VITE_BIFROST_URL` are compiled in, so changing either needs
`docker compose build console` rather than a restart. See
[environment.md](environment.md).
