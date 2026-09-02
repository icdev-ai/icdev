# CUI // SP-CTI
# Cortex Service Exposure — External Consumers (ctx-expose-02/05/06)

**Status:** complete (this change) · **Project:** `ctx` epic `expose` · **Date:** 2026-07-12

## What shipped

ICDEV's Cortex unified AI layer is now consumable as a **shared service** by external
standalone applications (compass at `:8010`, idea_lab at `:8000`, future premium child
apps) over the existing `/cortex/api/v1/*` REST surface, with dedicated scoped
credentials, plus a DataBridge feeds surface with first-party local-DB
connector stub.

Built on top of what the expose epic already had on main: the MCP `cortex_*` family
(ctx-expose-01, `tools/mcp/cortex_server.py`) and the session-authenticated REST v1
(ctx-expose-02 first half, `tools/cortex/rest_v1.py`).

## Components

| Piece | File | Notes |
|---|---|---|
| Service keys | `tools/cortex/service_keys.py` + migration `265_cortex_service_keys.sql` | `icdev_ctx_` keys: SHA-256 at rest, revocable, scoped, tenant-bound, classification ceiling. CLI create/list/revoke. |
| Central auth branch | `tools/dashboard/auth.py` | `icdev_ctx_` keys honored ONLY on `/cortex/api/v1/` + `/api/databridge/v1/`; fills `g.current_user` (role `service`), `g.security_context`, `g.cortex_binding`. Invalid → 401 + auth event. |
| REST scope gate | `tools/cortex/rest_v1.py` `_scope_denied()` | Service callers need `cortex:<op>` per endpoint; session users unaffected. New unauthenticated `GET /cortex/api/v1/health` (status only, in `PUBLIC_ENDPOINTS`). |
| Feeds API | `tools/dashboard/api/databridge_feeds.py` | `GET/POST /api/databridge/v1/<connector>/<table>`; connector allowlist `{icdev_demand, icdev_cpmp}`; scopes `databridge:<connector>:read|write`; service keys only. |
| Client SDK | `tools/cortex/client.py` | Stdlib-only, vendored into compass/idea_lab. Never raises: dict on 2xx, 4xx body returned (blocked is an answer), None when unreachable. |
| Leak guard | `tests/ci/test_premium_leak_guard.py` | Fails CI if premium product identifiers enter the open repo. |

## Security bindings (the contract)

- `tenant_id` comes from the key row — a request body can NEVER override it.
- `classification` = clamp(requested, key ceiling) via `classifications_dominated_by`.
- `air_gap` / `fail_closed`: caller may raise strictness, never lower it.
- `trusted_content` is force-cleared for network callers (no injection-screen skip).
- No `agent` over REST — team launches remain same-machine MCP (`cortex_agent_launch`).
- Feeds expose ONLY allowlisted connectors regardless of scopes (v1: `icdev_demand`, `icdev_cpmp`).

## Consumer wiring (compass / idea_lab)

> **Cortex is never inherited, only reached.** No child app, canvas or descendant gets a
> copy of `tools/cortex` — consumers vendor the stdlib-only client and point it at a
> host. The access pattern, the full degradation contract and the in-repo-consumer
> decision are in
> [cortex-child-app-access-pattern.md](cortex-child-app-access-pattern.md).


1. Issue a key: `python -m tools.cortex.service_keys create --label compass --tenant compass --json`
2. Vendor `tools/cortex/client.py` → app `tools/integrations/cortex_client.py` (provenance header).
3. App env: `ICDEV_CORTEX_BASE_URL=http://<icdev-host>:5050`, `COMPASS_CORTEX_API_KEY=icdev_ctx_…`
4. Every AI feature must degrade when `client.ask(...)` returns `None` (Cortex down) and
   surface—not hide—`{"blocked": True}` refusals.

## Tests

`tests/cortex/test_service_keys.py`, `test_rest_scopes.py`, `test_client.py`,
`tests/dashboard/test_cortex_service_key_auth.py`, `test_databridge_feeds.py`,
`tests/dashboard/test_databridge_feeds.py`, `tests/ci/test_premium_leak_guard.py`
(48 new tests; full cortex+databridge suites green: 570 passed).
