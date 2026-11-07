#!/usr/bin/env bash
# Refresh a governed Open WebUI demo's ICDEV gateway to origin/main (owui-ship-05, versioned in owui-ship-09).
#
#   1. export origin/main to $GATEWAY_DIR/src.new
#   2. smoke-test it on a spare port with the demo key: /v1/models, one local chat, one MCP tool
#   3. only then swap: src -> src.prev-<old rev>, src.new -> src, restart the service
#
# A failed smoke test leaves the running gateway untouched and src.new in place for inspection.
# Previous exports are kept (src.prev-*) for rollback; remove old ones by hand.
#
# Settings (environment):
#   GATEWAY_DIR      gateway deployment directory   (default ~/apps/icdev-owui-gateway)
#   GATEWAY_SERVICE  systemd --user unit             (default icdev-owui-gateway)
#   GATEWAY_URL      the live gateway                (default http://127.0.0.1:8443)
#   SMOKE_PORT       spare port for the smoke test   (default 8544)
#   ICDEV_KEY_FILE   0600 file holding an icdev_ key with chat/models/mcp scopes
#                                                    (default ~/.config/icdev/owui_demo_icdev_key)
#   ICDEV_REPO       the checkout to export from     (default: the repo holding this script)
#
# $GATEWAY_DIR/run.sh must honour GATEWAY_SRC and GATEWAY_PORT (the source directory and port to
# serve); that is how the smoke instance runs beside the live one. The key is read from its file
# and never printed. Pass --force to re-export even when REVISION already matches.
set -euo pipefail
REPO="${ICDEV_REPO:-$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)}"
D="${GATEWAY_DIR:-$HOME/apps/icdev-owui-gateway}"
SERVICE="${GATEWAY_SERVICE:-icdev-owui-gateway}"
LIVE="${GATEWAY_URL:-http://127.0.0.1:8443}"
PORT="${SMOKE_PORT:-8544}"
KEY="$(cat "${ICDEV_KEY_FILE:-$HOME/.config/icdev/owui_demo_icdev_key}")"
SMOKE_LOG="$D/refresh-smoke.log"

git -C "$REPO" fetch -q origin main
NEW="$(git -C "$REPO" rev-parse origin/main)"
OLD="$(cat "$D/REVISION" 2>/dev/null || echo unknown)"
if [ "$NEW" = "$OLD" ] && [ "${1:-}" != "--force" ]; then
    echo "already at $NEW"; exit 0
fi
[ -e "$D/src.new" ] && { echo "src.new exists from an earlier run; move it aside first" >&2; exit 2; }

mkdir "$D/src.new"
git -C "$REPO" archive "$NEW" | tar -x -C "$D/src.new"

GATEWAY_SRC=src.new GATEWAY_PORT=$PORT "$D/run.sh" > "$SMOKE_LOG" 2>&1 &
PID=$!
cleanup() { kill "$PID" 2>/dev/null || true; wait "$PID" 2>/dev/null || true; }
trap cleanup EXIT
fail() { echo "SMOKE FAILED: $1 (log: $SMOKE_LOG); src.new left for inspection, service untouched" >&2; exit 1; }

up=""
for _ in $(seq 1 60); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/v1/models")" != 000 ] && { up=1; break; }
    sleep 2
done
[ -n "$up" ] || fail "gateway did not start"

AUTH=(-H "Authorization: Bearer $KEY")
curl -s -m 20 "${AUTH[@]}" "http://127.0.0.1:$PORT/v1/models" | grep -q '"icdev/local-only"' || fail "/v1/models"
curl -s -m 240 "${AUTH[@]}" -H 'Content-Type: application/json' "http://127.0.0.1:$PORT/v1/chat/completions" \
    -d '{"model":"icdev/local-only","messages":[{"role":"user","content":"Reply with exactly: ok"}]}' \
    | grep -q '"choices"' || fail "chat completion"

MCP="http://127.0.0.1:$PORT/mcp/v1/?toolset=chat_readonly"
H=("${AUTH[@]}" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H 'X-ICDEV-Client: refresh-smoke')
SID="$(curl -s -D - -o /dev/null -X POST "$MCP" "${H[@]}" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"refresh","version":"0"}}}' \
    | awk 'tolower($1)=="mcp-session-id:"{print $2}' | tr -d '\r')"
[ -n "$SID" ] || fail "MCP initialize"
curl -s -X POST "$MCP" "${H[@]}" -H "Mcp-Session-Id: $SID" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' > /dev/null
curl -s -X POST "$MCP" "${H[@]}" -H "Mcp-Session-Id: $SID" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"nist_lookup","arguments":{"control_id":"AC-2"}}}' \
    | grep -q 'Account Management' || fail "MCP nist_lookup"

# Every smoke check passed: only now touch the running deployment.
cleanup; trap - EXIT
mv "$D/src" "$D/src.prev-${OLD:0:9}"
mv "$D/src.new" "$D/src"
echo "$NEW" > "$D/REVISION"
systemctl --user restart "$SERVICE"
for _ in $(seq 1 60); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "$LIVE/v1/models")" != 000 ] && break
    sleep 2
done
systemctl --user is-active --quiet "$SERVICE" || { echo "service not active after swap; roll back: mv src src.bad && mv src.prev-${OLD:0:9} src" >&2; exit 1; }
echo "refreshed ${OLD:0:9} -> ${NEW:0:9} (previous export kept as src.prev-${OLD:0:9})"
