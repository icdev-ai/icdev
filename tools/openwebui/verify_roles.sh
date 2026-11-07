#!/usr/bin/env bash
# Per-role check of a governed Open WebUI demo, straight against the ICDEV gateway with each demo
# user's OWN Keycloak token (owui-idp-02, versioned in owui-ship-09). Prints verdicts, never a secret.
#
# For each user in $USERS_FILE ("<username> <password> <group>" per line): the model list, a local chat,
# chat to the admin-only icdev/kanban, two read-only MCP tools, and one write tool (not called for the
# admin group, where it would really create a project). Then three negative controls: a tampered
# token, forged X-OpenWebUI-User-* headers and no auth must all be 401.
#
# Tokens come from the Keycloak password grant, which the client keeps DISABLED: Open WebUI never uses
# it, and left on it is a way to guess passwords around the login page. This script enables it for its
# own run only, and an EXIT trap turns it back off whatever happens.
#
# Settings (environment):
#   GATEWAY_URL         default http://127.0.0.1:8443
#   KEYCLOAK_URL        default http://localhost:8180          REALM      default owui-demo
#   KEYCLOAK_CONTAINER  default icdev-keycloak-demo           CLIENT_ID  default open-webui
#   KEYCLOAK_ADMIN_FILE default ~/.config/icdev/owui_demo_keycloak_admin  (master-realm "admin")
#   USERS_FILE          default ~/.config/icdev/owui_demo_users
#   OIDC_ENV_FILE       default ~/.config/icdev/owui_demo_oidc.env        (holds OAUTH_CLIENT_SECRET)
#   ADMIN_GROUP         default icdev-admins
set -uo pipefail
GW="${GATEWAY_URL:-http://127.0.0.1:8443}"
KC="${KEYCLOAK_URL:-http://localhost:8180}"
REALM="${REALM:-owui-demo}"
CONTAINER="${KEYCLOAK_CONTAINER:-icdev-keycloak-demo}"
CLIENT="${CLIENT_ID:-open-webui}"
USERS="${USERS_FILE:-$HOME/.config/icdev/owui_demo_users}"
ADMIN_GROUP="${ADMIN_GROUP:-icdev-admins}"
SECRET="$(grep '^OAUTH_CLIENT_SECRET=' "${OIDC_ENV_FILE:-$HOME/.config/icdev/owui_demo_oidc.env}" | cut -d= -f2-)"
TOK_URL="$KC/realms/$REALM/protocol/openid-connect/token"

kc() { docker exec "$CONTAINER" /opt/keycloak/bin/kcadm.sh "$@"; }
kc config credentials --server http://localhost:8080 --realm master --user admin \
    --password "$(cat "${KEYCLOAK_ADMIN_FILE:-$HOME/.config/icdev/owui_demo_keycloak_admin}")" >/dev/null \
    || { echo "cannot log in to Keycloak in $CONTAINER" >&2; exit 2; }
CID="$(kc get clients -r "$REALM" -q clientId="$CLIENT" --fields id --format csv --noquotes)"
[ -n "$CID" ] || { echo "no client $CLIENT in realm $REALM" >&2; exit 2; }
grants() { kc update "clients/$CID" -r "$REALM" -s "directAccessGrantsEnabled=$1"; }
trap 'grants false && echo "direct grants: off"' EXIT
grants true

token() {
    curl -s "$TOK_URL" -d grant_type=password -d client_id="$CLIENT" --data-urlencode "client_secret=$SECRET" \
        --data-urlencode "username=$1" --data-urlencode "password=$2" -d scope=openid \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))'
}

mcp_call() {  # token query tool args -> short verdict
    local T=$1 MCP="$GW/mcp/v1/$2"
    local H=(-H "Authorization: Bearer $T" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H 'X-ICDEV-Client: verify-roles')
    local init='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"verify-roles","version":"0"}}}'
    local SID code
    code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$MCP" "${H[@]}" -d "$init")"
    SID="$(curl -s -D - -o /dev/null -X POST "$MCP" "${H[@]}" -d "$init" | awk 'tolower($1)=="mcp-session-id:"{print $2}' | tr -d '\r')"
    [ -n "$SID" ] || { echo "no session (HTTP $code)"; return; }
    curl -s -X POST "$MCP" "${H[@]}" -H "Mcp-Session-Id: $SID" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
    curl -s -X POST "$MCP" "${H[@]}" -H "Mcp-Session-Id: $SID" \
        -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"$3\",\"arguments\":$4}}" \
        | python3 -c '
import json,sys
raw=sys.stdin.read()
try: d=json.loads(raw[raw.index("{"):])
except Exception: print("unparsed: "+raw[:80]); sys.exit()
if "error" in d: print("DENY %s %s" % (d["error"].get("code"), d["error"].get("message","")[:60]))
else: print("ALLOW" + (" (tool error)" if d["result"].get("isError") else ""))'
}

chat() {  # token model -> HTTP status
    curl -s -m 240 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $1" -H 'Content-Type: application/json' \
        "$GW/v1/chat/completions" -d "{\"model\":\"$2\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: ok\"}]}"
}

while read -r user pass group; do
    [ -n "$user" ] || continue
    T="$(token "$user" "$pass")"
    [ -n "$T" ] || { echo "== $user: NO TOKEN"; continue; }
    models="$(curl -s -H "Authorization: Bearer $T" "$GW/v1/models" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(",".join(sorted(m["id"].replace("icdev/","") for m in d.get("data",[]))) or d)')"
    echo "== $user ($group)"
    echo "   models:          $models"
    echo "   chat local-only: HTTP $(chat "$T" icdev/local-only)"
    echo "   chat kanban:     HTTP $(chat "$T" icdev/kanban)"
    echo "   nist_lookup:     $(mcp_call "$T" '?toolset=chat_readonly' nist_lookup '{"control_id":"AC-2"}')"
    echo "   project_list:    $(mcp_call "$T" '?toolset=chat_readonly' project_list '{}')"
    if [ "$group" = "$ADMIN_GROUP" ]; then
        echo "   project_create:  (not called for $ADMIN_GROUP: it would write)"
    else
        echo "   project_create:  $(mcp_call "$T" '' project_create '{"name":"verify-roles-probe","project_type":"api"}')"
    fi
done < "$USERS"

echo "== negative controls"
read -r u p _ < "$USERS"
T="$(token "$u" "$p")"
echo "   tampered token:  HTTP $(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${T%?????}AAAAA" "$GW/v1/models")"
echo "   forged headers:  HTTP $(curl -s -o /dev/null -w '%{http_code}' -H "X-OpenWebUI-User-Email: $u@forged.local" -H 'X-OpenWebUI-User-Role: admin' "$GW/v1/models")"
echo "   no auth:         HTTP $(curl -s -o /dev/null -w '%{http_code}' "$GW/v1/models")"
