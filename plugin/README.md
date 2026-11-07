# Chronos: a freshness plugin for Bifrost

*This document is written as the pull request description it would be if
proposed to [`maximhq/bifrost`](https://github.com/maximhq/bifrost). It
describes the plugin only; the verifier service it talks to lives in
`verifier/`.*

---

## What this adds

A plugin that stops the semantic cache from serving answers whose facts have
gone out of date.

Bifrost's semantic cache is keyed on *what the user asked*. It has no way to
know that the answer it stored contains a stock price, a per-token price or the
name of a chief executive — facts whose shelf life ranges from minutes to
"until someone resigns". A single TTL has to be set for all of them, so it is
either too short to be worth having or long enough to serve a wrong answer.

Chronos makes the TTL a function of the answer's content, and checks cached
answers on the way out:

1. **`HTTPTransportPreHook`** — classifies the prompt's volatility with rules
   only (a handful of regex scans, no model call, no allocation beyond the
   parsed prompt) and sets `x-bf-cache-ttl` from the result: 15 minutes for a
   market question, a week for a definition.
2. **`PreLLMHook`** — sets the cache's `CacheTTLKey` context value, which is
   what the plugin actually reads.
3. **`PostLLMHook`** — hands the answer to the verifier, which extracts
   time-sensitive claims, checks them against the live web and scores them.
4. **`HTTPTransportResponseHeadersHook`** — writes `x-chronos-*` headers before
   headers commit, so streaming clients see the verdict too.
5. On an outdated **cache hit**, issues `DELETE /api/cache/clear/{cache_id}`
   and logs a `stale_hit_prevented` event.

## Headers

| Header | Meaning |
|---|---|
| `x-chronos-verdict` | `current` · `check` · `outdated` · `unverifiable` · `pending` |
| `x-chronos-score` | 0–100 freshness score for the answer |
| `x-chronos-claims` | time-sensitive claims found |
| `x-chronos-stale` | how many are outdated |
| `x-chronos-class` | the prompt's volatility class |
| `x-chronos-ttl` | the cache TTL Chronos chose, in seconds |
| `x-chronos-report` | URL of the full report, with per-claim score breakdowns |
| `x-chronos-mode` | `observe` · `annotate` · `guard` |
| `x-chronos-latency-ms` | time spent verifying inline, when it was inline |

The hook also appends these names to `Access-Control-Expose-Headers`,
preserving the gateway's existing entries. Without that a browser cannot read
them: the transport publishes its own `x-bifrost-*` headers on that list but
has no way to know a plugin's.

## Configuration

```json
{
  "plugins": [
    {
      "enabled": true,
      "name": "chronos",
      "path": "/app/plugins/chronos.so",
      "placement": "pre_builtin",
      "order": 0,
      "config": {
        "verifier_url": "http://verifier:8000",
        "bifrost_admin_url": "http://127.0.0.1:8080",
        "admin_auth_header": "X-Bifrost-Setup-Token",
        "mode": "annotate",
        "guard_threshold": 50,
        "verify_timeout_ms": 4000,
        "sync_on_cache_hit": true,
        "evict_stale": true,
        "default_ttl": "24h",
        "ttl_by_class": {
          "market": "15m", "pricing": "6h", "version": "6h",
          "leadership": "24h", "law": "24h", "events": "12h",
          "timeless": "168h"
        }
      }
    },
    { "enabled": true, "name": "semantic_cache", "config": { "dimension": 1, "ttl": "24h" } }
  ]
}
```

`placement: "pre_builtin"` is required, not cosmetic. Post-hooks run in reverse
order, so a `pre_builtin` plugin's `PostLLMHook` runs *after* the built-in
cache's — which is the only way to observe a cache hit. Placed
`post_builtin`, Chronos would see fresh answers only and miss the entire
problem it exists to solve.

Every field is also settable from the environment (`CHRONOS_MODE`,
`CHRONOS_VERIFY_TIMEOUT_MS`, `CHRONOS_ADMIN_TOKEN`, …), so one image can be
configured per deployment.

## Design constraints

In priority order, because they conflict and the order is how they were
resolved.

**1. Never break the gateway.** Every failure path — verifier down, slow,
malformed, budget spent — passes the response through with verdict
`unverifiable`. No hook returns an error that fails a request; `Init` does not
return an error for a recoverable misconfiguration, because a gateway that
refuses to boot over a misspelled knob is strictly worse than one that boots
degraded and says so. There is a test that stops the verifier mid-run and
requires the gateway to keep answering.

**2. Do not slow down the expensive path.** Fresh answers are verified in a
background goroutine, so the added latency is a spawn. Only **cache hits** are
verified inline, and that is deliberate: a cache hit costs no provider latency
and is the only answer that can be stale, so it is the one worth waiting for.
With a warm claim cache that inline check measured 28 ms.

**3. Stay thin.** No model calls, no HTTP to third parties, no storage, no
scoring in this package. It is transport glue and policy; everything expensive
or stateful lives in the verifier service. The plugin's only dependency is
`github.com/maximhq/bifrost/core` plus the standard library — deliberately,
since a plugin shares its dependency graph with the gateway and every import is
a version constraint.

**4. Bounded memory.** The background verification queue is capped (a full
queue drops a report rather than spawning unbounded goroutines), and the
verdict store is bounded by both count and TTL.

## Three findings that are not in the docs

Each cost real debugging time and each is commented at the site.

**Context values propagate downward only.** A value set in
`HTTPTransportPreHook` is visible in `PreLLMHook` and `PostLLMHook`. The
reverse is not true: a verdict set in `PostLLMHook` is **not** visible to
`HTTPTransportResponseHeadersHook`, so no headers appeared at first. The hook
ran and the verdict existed; `ctx.Value` in the headers hook came back
empty, and `BifrostContextKeyRequestID` was absent there too, so it could not
even be used as a correlation key.

Chronos therefore mints its own correlation id in `HTTPTransportPreHook` — a
*transport* hook, so the value reaches both `PostLLMHook` and the sibling
transport hook that commits headers — and parks the verdict in a bounded,
TTL'd map keyed on it. If a future Bifrost propagates upward, the context path
is still checked first and the map becomes redundant.

*If this is considered worth fixing upstream, exposing the committing hook's
context as a descendant of the transport context would remove the need for the
map entirely.*

**`ctx.Log` from a `.so` plugin did not reach the gateway's log stream.**
Nothing logged through `ctx.Log` appeared in the container's output, at any
level. `stale_hit_prevented` is the one event an operator must be able to see,
so it is written to stderr as well as through `ctx.Log`.

**The management API needs a setup token.** While dashboard auth is inactive —
the usual OSS deployment — every `/api` call must carry
`X-Bifrost-Setup-Token`, or `DELETE /api/cache/clear/{id}` returns 403 with a
message about `setup_token`. Worth a line in the cache-invalidation docs.

## Building

Go's plugin loader rejects any `.so` whose toolchain, build flags or shared
dependency versions differ from the host binary's. Rather than hoping two
module graphs resolve identically, `deploy/Dockerfile.bifrost` compiles this
package **as a package of Bifrost's own `transports` module** — same `go.mod`,
same `go.sum`, same toolchain, same flags, same libc — which makes an ABI
mismatch structurally impossible.

The gateway must be built **without** `-extldflags '-static'` and without the
`sqlite_static` tag, or it cannot `dlopen` anything. The build asserts this and
fails if it produces a static binary, since that would start cleanly and then
refuse every plugin at runtime.

Verified against Go 1.27.3, `core v1.14.1`, `framework v1.11.1`,
`semanticcache v1.7.4`, built from `transports/v2.4.0`.

```bash
# development: standalone module
cd plugin && go vet ./... && go test -race ./...
go build -buildmode=plugin -o chronos.so .

# production: alongside the gateway
docker build -f deploy/Dockerfile.bifrost -t chronos/bifrost .
```

## Tests

`go test -race ./...` covers:

- the TTL mapping, including that the policy's **ordering** holds, so an edit
  that inverts it fails rather than silently caching a stock price for a week
- volatility classification, including that an unrecognised prompt is **not**
  classified timeless (guessing timeless is the one mistake that silently
  serves stale answers)
- eviction against a mock HTTP server: method, path, escaping, auth header, and
  that an empty cache id never reaches the gateway
- the degrade path: 5xx, malformed JSON, timeout and connection-refused all
  return a plain error so the response can pass through
- request-body parsing against malformed input, since the pre-hook sees every
  request
- the verdict store's bounds, expiry and concurrency
- the `Access-Control-Expose-Headers` merge, including that it preserves the
  gateway's entries and is idempotent

## What is not here

- **Streaming answers are not content-verified.** A stream is assembled by the
  client, so there is no complete text to check inside the gateway. Streams
  still get headers, with verdict `pending`.
- **Only the last user turn drives classification**, so a long conversation may
  be classified from too little context.
- The verifier is a separate service. Running Chronos means running it; a
  single-binary build with claim extraction in-process would be a much larger
  change and would break constraint 3.
