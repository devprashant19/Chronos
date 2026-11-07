# Deployment

Five containers, one compose file, one image that is harder to build than the rest of the
project put together. Everything here was run on Windows 11 with Docker Desktop; it has
never been run on macOS or Linux.

## What runs

```bash
cp .env.example deploy/.env      # optional: it starts with nothing set
cd deploy && docker compose up --build
```

| Service | Image | Port | What it is |
| :--- | :--- | :--- | :--- |
| `bifrost` | built from `deploy/Dockerfile.bifrost` | 8080 | The gateway, with `chronos.so` loaded |
| `verifier` | built from `verifier/Dockerfile` | 8000 | FastAPI: extract, verify, score, report |
| `console` | built from `console/Dockerfile` | 5173 | The Vite bundle behind nginx |
| `redis` | `redis/redis-stack-server:7.4.0-v3` | 6379 | db0 the cache's vector store, db1 the claim cache |
| `mockllm` | built from `deploy/mockllm/` | 8900 | OpenAI-compatible test double, so the stack runs with no keys |

A sixth, `ollama`, sits behind a profile and is off unless asked for:
`docker compose --profile local up -d ollama`.

Named volumes: `bifrost-data` (the gateway's config store), `verifier-data` (the report
SQLite), `ollama-models`. Removing them is safe and loses only history.

**The first build takes several minutes.** It downloads Bifrost's source and compiles the
gateway *and* the plugin together. Later builds reuse the layers.

---

## Why the image is built this way

Go's plugin loader rejects any `.so` whose toolchain, build flags or shared dependency
versions differ from the host binary's. There is no diagnostic worth having: the gateway
starts normally and refuses the plugin at runtime, with a message about a package being
built with a different version of something.

Rather than maintain two module graphs and hope they resolve identically,
`deploy/Dockerfile.bifrost` compiles `plugin/` **as a package of Bifrost's own `transports`
module** — same `go.mod`, same `go.sum`, same toolchain, same flags, same libc. An ABI
mismatch is then structurally impossible rather than merely unlikely.

The five stages:

1. **`bifrost-src`** downloads the pinned tarball for `BIFROST_REF`. It prefers a vendored
   copy in `deploy/vendor/` and validates it with `gzip -t` first, falling back to a fresh
   download — a truncated tarball otherwise fails much later with a confusing error.
2. **`builder`** stubs `bifrost-http/ui` so the `//go:embed all:ui` directive has something
   to embed. Bifrost's web UI is therefore **not** in this image. The HTTP API is complete.
3. It builds the gateway **without** `-extldflags '-static'` and without the `sqlite_static`
   tag. That is what makes the binary able to `dlopen` a plugin at all. The build then runs
   `ldd` and **fails if the result is a static binary**, because a static gateway starts
   fine and refuses every plugin afterwards.
4. `plugin/` is copied in as `./chronosplugin`, its own `go.mod`, `go.sum` and `_test.go`
   files removed so it compiles as a package of the host module, and built with
   `-buildmode=plugin`.
5. The runtime stage is `debian:bookworm-slim` with the gateway at **`/app/main`** and the
   plugin at `/app/plugins/chronos.so`.

> The binary has to be called `/app/main`. Bifrost's own `docker-entrypoint.sh` execs that
> path and ignores `CMD`, so a differently named binary produces a container that starts and
> immediately exits.

A `BUILDINFO` file is written into the image recording what was used:

```
bifrost_ref   transports/v2.4.0
go            1.27.3
core          v1.14.1
framework     v1.11.1
semanticcache v1.7.4
```

`plugin/` is *also* a standalone Go module, so `go test ./...` and `go vet` work normally
outside Docker. The Dockerfile deletes the module files on the way in; it does not ask you to
choose.

## How the plugin is attached

`deploy/bifrost.config.json` names it:

```json
{ "enabled": true, "path": "./plugins/chronos.so",
  "placement": "pre_builtin", "order": 0 }
```

`placement: "pre_builtin"` with `order: 0` is the load-bearing part. Pre-hooks run in order
and post-hooks run in **reverse**, so Chronos's `PostLLMHook` runs *after* the built-in
semantic cache's. That is the only arrangement in which Chronos can see that an answer came
from the cache — and seeing cache hits is the entire point.

## Checking a deployment

```bash
curl http://localhost:8000/health
curl -s http://localhost:8080/v1/chat/completions -i \
  -H 'Content-Type: application/json' -H 'x-bf-cache-key: smoke' \
  -d '{"model":"mockllm/chronos-demo","messages":[{"role":"user","content":"hello"}]}' \
  | grep -i x-chronos
docker logs chronos-bifrost 2>&1 | grep -i chronos | head
```

The gateway's log should contain the plugin's own init line. If it does not, the plugin was
not loaded, and the gateway will keep answering perfectly well without it — which is why the
check is explicit. `./scripts/verify-all.sh` asserts the same thing.

## The console on its own, with no backend

The console is a static bundle and the only thing it needs at runtime is itself. When the
verifier does not answer its health probe it falls back to replaying a recorded log, and
labels the page *"Recorded demo replay"* rather than showing an empty dashboard. So it can be
hosted as a plain static site, which is the cheapest way to let someone see what Chronos does.

```bash
./scripts/build-demo-site.sh [outdir]     # default console/dist-demo
npx http-server console/dist-demo -p 8099
```

Two things differ from the image build, both in that script:

- `--base=./` makes every asset URL relative, so the folder works at any path rather than
  only at a domain root. The benchmark panel's fetch of `results.json` resolves against
  `import.meta.env.BASE_URL` for the same reason.
- `bench/results.json` is copied in beside the bundle, so the benchmark panel shows the real
  measured numbers instead of its "no results file" state.

Hosting it changes nothing about honesty: the recorded traffic is labelled on the page, and
the only numbers on it that are measurements come from `bench/results.json`.

## What does not work

- **No Kubernetes manifests, no Helm chart, no cloud deployment of any kind.** Compose is
  the only arrangement that has ever been run.
- **Not run on macOS or Linux.** The Go plugin path is linker-sensitive and the `ldd` check
  is Debian-specific; neither has been exercised elsewhere.
- **Not run behind a reverse proxy or TLS.** Both surfaces speak plain HTTP.
- **No horizontal scaling.** The Tavily credit budget and the claim-cache counters live in a
  single verifier process; two replicas would each have their own day's budget.
- **The console's URLs are compiled in.** `VITE_VERIFIER_URL` and `VITE_BIFROST_URL` are
  baked at build time, so changing them needs `docker compose build console`.
- **Bifrost's web dashboard is stubbed out**, deliberately, to keep the image small.

Nothing here is authenticated inside the compose network. Read
[security.md](security.md) before putting any of it on a network you do not own.
