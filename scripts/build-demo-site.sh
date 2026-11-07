#!/usr/bin/env bash
#
# Build the keyless demo site: the console as a plain folder of static files,
# with no backend behind it.
#
#   ./scripts/build-demo-site.sh [outdir]      # default: console/dist-demo
#
# Why this exists: a judge should be able to see what Chronos does without
# installing Docker. The console already falls back to demo mode when the
# verifier is unreachable -- it replays a recorded log and labels itself
# "Recorded demo replay" on the page -- so a static build of it is a complete,
# honest demo that needs nothing running.
#
# Two differences from the image build:
#   --base=./   so asset URLs are relative and the folder works at any path,
#               not only at a domain root.
#   results.json copied from bench/ so the benchmark panel shows the real
#               measured numbers rather than its "no results file" state.
#
# Serve it with anything:
#   npx http-server console/dist-demo -p 8099
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Resolve the output directory against the caller's directory BEFORE changing
# into console/. Without this a relative argument -- which is what a hosting
# provider's build command passes -- lands in console/console/dist-demo, the
# build "succeeds", and the publish directory is empty.
OUT="${1:-$REPO/console/dist-demo}"
case "$OUT" in
  /*|[A-Za-z]:[\/]*) ;;                 # already absolute, POSIX or Windows
  *) OUT="$(pwd)/$OUT" ;;
esac
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

cd "$REPO/console"

if [ ! -d node_modules ]; then
  echo "installing console dependencies"
  npm install --no-audit --no-fund
fi

# The benchmark panel reads this next to the build. Without it the panel says
# so rather than inventing numbers, which is correct but not what we want to
# show.
if [ -f "$REPO/bench/results.json" ]; then
  cp "$REPO/bench/results.json" public/results.json
  echo "copied bench/results.json"
else
  echo "warning: bench/results.json is missing; the benchmark panel will say so" >&2
fi

npx vite build --base=./ --outDir "$OUT" --emptyOutDir

echo
echo "built: $OUT"
echo "serve: npx http-server \"$OUT\" -p 8099"
echo
echo "Open it with nothing else running. The header should read"
echo "\"Recorded demo replay\" -- that is the fallback working, not a failure."
