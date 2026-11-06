#!/usr/bin/env bash
#
# Check that the configured model actually works through the gateway.
#
#   ./scripts/check-model.sh                               # the configured default
#   ./scripts/check-model.sh groq/llama-3.3-70b-versatile  # a specific model
#   ./scripts/check-model.sh --all                         # every free candidate
#
# Tests the three things Chronos needs from a model, in order of how likely
# they are to be the problem:
#
#   1. it answers at all           (key present, model id valid, quota left)
#   2. it accepts response_format  (JSON mode; Chronos degrades without it)
#   3. it returns parseable JSON   (the actual requirement)
#
# A model that fails 2 but passes 3 is still usable — Chronos retries without
# JSON mode. A model that fails 3 will silently push the verifier onto its rule
# engine, which is the failure worth catching here.
#
set -uo pipefail

GATEWAY="${CHRONOS_BIFROST_URL:-http://localhost:8080}"
ENDPOINT="$GATEWAY/v1/chat/completions"

# Free, open-weights candidates. Groq needs a key (no card); ollama needs the
# local service and a pulled model.
CANDIDATES=(
  "groq/llama-3.3-70b-versatile"
  "groq/llama-3.1-8b-instant"
  "groq/openai/gpt-oss-120b"
  "ollama/llama3.1"
)

c_green=$'\033[32m'; c_red=$'\033[31m'; c_yellow=$'\033[33m'
c_bold=$'\033[1m'; c_dim=$'\033[2m'; c_reset=$'\033[0m'

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

ask() {
  # ask <model> <json_mode:0|1> -> writes body to $tmp/out.json, echoes status
  local model="$1" json_mode="$2" payload
  payload=$(cat <<JSON
{
  "model": "$model",
  "messages": [
    {"role": "system", "content": "Reply with JSON only: {\\"ok\\": true, \\"model\\": \\"<your model family>\\"}"},
    {"role": "user", "content": "Reply with the JSON object and nothing else."}
  ],
  "max_tokens": 120,
  "temperature": 0
  $([ "$json_mode" = "1" ] && printf ',"response_format": {"type": "json_object"}')
}
JSON
)
  curl -s -m 60 -o "$tmp/out.json" -w '%{http_code}' "$ENDPOINT" \
    -H 'Content-Type: application/json' -d "$payload"
}

content_of() {
  python - "$tmp/out.json" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print(""); raise SystemExit
print((d.get("choices") or [{}])[0].get("message", {}).get("content") or "")
PY
}

error_of() {
  python - "$tmp/out.json" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("unreadable response"); raise SystemExit
err = d.get("error") or {}
print(str(err.get("message") or err.get("error") or d)[:160])
PY
}

parses_as_json() {
  python - <<'PY' 2>/dev/null
import json, sys
raw = sys.stdin.read().strip()
if raw.startswith("```"):
    raw = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("```")).strip()
try:
    json.loads(raw)
except Exception:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise SystemExit(1)
    try:
        json.loads(raw[start:end + 1])
    except Exception:
        raise SystemExit(1)
PY
}

check_one() {
  local model="$1"
  printf '%s%-34s%s' "$c_bold" "$model" "$c_reset"

  local status
  status=$(ask "$model" 1)

  local json_mode="yes"
  if [ "$status" != "200" ]; then
    # Retry without JSON mode: plenty of models reject response_format.
    status=$(ask "$model" 0)
    json_mode="no"
  fi

  if [ "$status" != "200" ]; then
    printf '  %sFAIL%s  HTTP %s\n' "$c_red" "$c_reset" "$status"
    printf '        %s%s%s\n' "$c_dim" "$(error_of)" "$c_reset"
    return 1
  fi

  local content
  content=$(content_of)
  if [ -z "$content" ]; then
    printf '  %sFAIL%s  answered 200 but with no content\n' "$c_red" "$c_reset"
    return 1
  fi

  if printf '%s' "$content" | parses_as_json; then
    printf '  %sOK%s    json_mode=%s  json=yes\n' "$c_green" "$c_reset" "$json_mode"
    printf '        %s%s%s\n' "$c_dim" "$(printf '%s' "$content" | tr -d '\n' | cut -c1-90)" "$c_reset"
    return 0
  fi

  printf '  %sWARN%s  answers, but not with JSON (json_mode=%s)\n' "$c_yellow" "$c_reset" "$json_mode"
  printf '        %sChronos would fall back to its rule engine for this model%s\n' "$c_dim" "$c_reset"
  printf '        %s%s%s\n' "$c_dim" "$(printf '%s' "$content" | tr -d '\n' | cut -c1-90)" "$c_reset"
  return 2
}

# ---------------------------------------------------------------------------

if ! curl -s -m 5 -o /dev/null "$GATEWAY/health"; then
  printf '%sThe gateway is not answering at %s%s\n' "$c_red" "$GATEWAY" "$c_reset"
  printf 'Start it with:  cd deploy && docker compose up -d\n'
  exit 1
fi

printf 'gateway: %s\n\n' "$GATEWAY"

if [ "${1:-}" = "--all" ]; then
  printf 'Testing every free candidate. A failure here usually means the key is\n'
  printf 'absent, the model id has changed, or the daily allowance is spent.\n\n'
  ok=0
  for model in "${CANDIDATES[@]}"; do
    check_one "$model" && ok=$((ok + 1))
  done
  printf '\n%d of %d usable.\n' "$ok" "${#CANDIDATES[@]}"
  [ "$ok" -gt 0 ] || exit 1
  exit 0
fi

MODEL="${1:-${CHRONOS_EXTRACT_MODEL:-groq/llama-3.3-70b-versatile}}"
check_one "$MODEL"
status=$?

if [ "$status" -ne 0 ]; then
  printf '\n%sNext steps%s\n' "$c_bold" "$c_reset"
  printf '  1. Get a free Groq key (no card): https://console.groq.com/keys\n'
  printf '  2. Put it in deploy/.env as GROQ_API_KEY=gsk_...\n'
  printf '  3. cd deploy && docker compose up -d bifrost\n'
  printf '  4. Check the current model ids: https://console.groq.com/docs/models\n'
  printf '  5. Or run a model locally, with no key and no quota:\n'
  printf '       docker compose --profile local up -d ollama\n'
  printf '       docker compose exec ollama ollama pull llama3.1\n'
  printf '       ./scripts/check-model.sh ollama/llama3.1\n'
fi
exit "$status"
