# CUI // SP-CTI

# Open WebUI as a Governed Sidecar Client (card `owui`, ADR D-OWUI-1)

Open WebUI supplies the chat UX: streaming, a model picker, voice, mobile, conversation search.
ICDEV keeps everything that makes a turn governed: routing, redaction, classification policy,
retrieval, tool authorization, audit, kanban and human review. **ICDEV is the only backend
Open WebUI may talk to; Open WebUI's own equivalents are switched off.**

## Architecture

```
 Open WebUI (sidecar, pinned image)          ICDEV SaaS API gateway (:8443)
 ┌──────────────────────────────┐   /v1    ┌────────────────────────────────────────┐
 │ chat UI · model picker       │ ───────▶ │ tools/saas/openai_compat.py            │
 │ ENABLE_OLLAMA_API=false      │          │   └─ LLMRouter.invoke  (redaction,     │
 │ RAG off · telemetry off      │          │      routing policy, budgets, telemetry)│
 │                              │ /mcp/v1  │ tools/saas/mcp_http.py                 │
 │ tool servers ────────────────┼────────▶ │   └─ toolset profile + MCPToolAuthorizer│
 └──────────────────────────────┘          └────────────────────────────────────────┘
```

## Forbidden configurations

Each of these is a governance bypass:

- An Open WebUI connection straight to Ollama, OpenAI or any other provider.
- A connection to ICDEV's LiteLLM proxy (`litellm-proxy`). It sits downstream of the router, so
  it skips redaction and routing policy.
- Open WebUI's built-in RAG or vector store holding CUI.
- Identity forwarded in `X-OpenWebUI-User-*` headers. Those headers can be spoofed.

## Phase 1 — governed model endpoint (owui-api-01, shipped)

`GET /v1/models` and `POST /v1/chat/completions` run on the SaaS gateway.

**Auth.** The gateway middleware handles it with an `icdev_` API key or OAuth JWT. Scope
`chat:write` covers completions and `models:read` covers the model list.

**Models.** The ids are ICDEV capabilities from `args/openai_compat.yaml`. Each maps to a
declared router function in `args/llm_config.yaml`:

| Capability | Router function | Note |
|---|---|---|
| `icdev/general` | `openai_compat_general` | Local first. Cloud only after redaction and policy. |
| `icdev/code` | `openai_compat_code` | |
| `icdev/compliance` | `openai_compat_compliance` | |
| `icdev/local-only` | `openai_compat_local` | `force_local: true`. Never leaves the host. |

**Classification.** Send it in `X-ICDEV-Classification` (`UNCLASSIFIED`, `CUI` or `SECRET`).
The default is `CUI`, and an unknown value is a 400. A request that policy cannot serve
locally is refused with a 403, never quietly sent to the cloud.

**Fail closed when a capability is unrouted.** If `ICDEV_LLM_CONFIG` points the router at an
operator file that lacks the `openai_compat_*` routes, those capabilities are hidden and
refused with a 503. Add the four routing entries to that file to enable them.

**Streaming.** `stream: true` returns the finished answer as SSE chunks. Token-by-token
streaming waits on owui-api-02, because `invoke_streaming` does not yet de-anonymize its
output or write telemetry.

**Errors** use the OpenAI shape `{"error": {"message", "type", "code"}}`:

| Status | Code | Meaning |
|---|---|---|
| 404 | `model_not_found` | Unknown model id |
| 400 | `bad_classification` | Unknown classification value |
| 400 | `tools_unsupported` | Native `tools` were sent |
| 403 | `routing_policy` | Policy refused the request |
| 503 | `capability_unrouted` | The function is missing from the active llm_config |
| 503 | `redaction_unavailable` | Redaction is down and fail-closed is on |
| 503 | `llm_unavailable` | No model in the chain could serve |

### Pointing Open WebUI at it

Go to Admin → Settings → Connections and set:

- **OpenAI API:** URL `http://<gateway-host>:9443/v1`, key = a scoped `icdev_` API key.
- **Ollama API:** off.

Remove every other connection.

### Verified (2026-11-05, local stack)

- `icdev/local-only` was served by Ollama `qwen2.5:7b` and wrote one `ai_telemetry` row.
- A `SECRET` request on `icdev/general` refused the `claude-cli` entry and fell back to local
  Ollama.
- With the operator's `ICDEV_LLM_CONFIG`, which lacks the new routes, both capabilities
  returned 503 `capability_unrouted` instead of using the cloud-first default chain.

### Known gaps (tracked)

**`ai_telemetry`:**
- A router response-cache hit writes no row.
- The row records the router's default classification, not the request's.
- owui-flow-02 adds a per-exchange audit row that covers both.

**`AITelemetryLogger.log_ai_interaction` silently drops rows.** It returns `None` when the
SQLite file `data/icdev.db` is absent, even on the PostgreSQL backend. A fresh, PG-only
deployment therefore records no `ai_telemetry` at all. This is tracked as a separate fix.

**SaaS auth on SQLite.** `tools/saas/auth/api_key_auth.py` and `tenant_manager.create_tenant`
issue `%s`-placeholder SQL against SQLite, so API-key auth works only when the platform DB is
PostgreSQL (`PLATFORM_DB_URL`). The behaviour predates this card.

## Identity (owui-idp-01) — each person reaches ICDEV as themselves

With one shared `icdev_` key every Open WebUI user is the SAME ICDEV principal, so
RBAC, workflow roles and tenant RLS cannot tell them apart. In OIDC mode:

1. Open WebUI logs users in through the shared IdP (its own login form is off).
2. Its ICDEV connections use `auth_type: system_oauth`, which forwards the signed-in
   user's own access token as `Authorization: Bearer`.
3. `tools/saas/auth/oauth_auth.py` verifies that token -- signature against the
   tenant IdP's JWKS, issuer, audience, expiry -- and refuses on any doubt.
4. The role comes from the token's group claim through the tenant's `group_role_map`
   (most privileged mapped group wins; no mapped group -> refused).

Tenant `idp_config` (platform DB, `tenants.idp_config`):

```json
{"issuer_url": "https://idp.example/realms/icdev",
 "jwks_uri": "https://idp.example/realms/icdev/protocol/openid-connect/certs",
 "audience": "icdev-gateway", "groups_claim": "groups",
 "group_role_map": {"icdev-admins": "tenant_admin", "icdev-developers": "developer",
                    "icdev-compliance": "compliance_officer"}}
```

Each user row needs `users.oauth_sub` = their IdP `sub` (column added by
`platform_db.py --init`). IdP side: an audience mapper putting `icdev-gateway` in the
access token's `aud`, and a group-membership mapper emitting `groups`.

Open WebUI side: start it with `configure.py --env --identity oidc`, then once
`configure.py --apply --identity oidc --with-tools` (Open WebUI has no env for a
connection's `auth_type`). `--check --identity oidc` reports `shared_identity` while
any ICDEV connection still presents a shared key.

**Fixed on the way (fail-open):** `validate_oauth_token` used to skip signature
verification when PyJWT was not installed or the IdP had no `jwks_uri`, and then
trusted the unverified payload -- any token naming a tenant's issuer got in. PyJWT
was commented out of `requirements.txt`, so that was the deployed behaviour. It now
fails closed and PyJWT is a requirement. `/v1/*` also has its own RBAC category
(`chat`): it fell to `projects`, which refused compliance officers a chat POST.

**Verified live (2026-11-05):** local Keycloak 26.3 + gateway on a throwaway PG
database. `alice` (`icdev-admins`) lists `icdev/kanban`; `bob` (`icdev-developers`)
does not; forged `X-OpenWebUI-User-*` headers and a tampered token are 401.

**Not done:** `tenant_manager.py --add-user` cannot set `oauth_sub`, and on
PostgreSQL that CLI fails before reaching it (`connection` has no `execute`; it also
writes non-UUID user ids into a UUID column) -- pre-existing, tracked separately.
Link users with SQL until then.

## End-to-end test (owui-ship-02)

`tests/e2e/openwebui_governed.spec.ts` signs in through the IdP, picks an ICDEV
capability, checks the reply renders, then reads ICDEV's database for an audit row
whose actor is that user and an `openai_compat_*` telemetry row. It skips unless
`OWUI_E2E_URL` is set; the stack it needs is: an IdP realm as above, the gateway on
a THROWAWAY PostgreSQL database (`ICDEV_PG_DATABASE` and `PLATFORM_DB_URL` both
pointing at it, schema from `tools/db/bootstrap_pg.py` + `platform_db.py --init`),
and an Open WebUI started from the pinned image with the `--identity oidc` env.

## Uploads (owui-rag-02) — a narrowed path, not a wall

Open WebUI stores an uploaded file on its own data volume **before** any filter or
pipeline sees it, so a "forward uploads to ICDEV" filter cannot keep CUI out of
Open WebUI. What ships instead:

- `USER_PERMISSIONS_CHAT_FILE_UPLOAD=false` in the compose profile, and
  `configure.py --check` reports `user_file_upload` when the permission is on
  (`--apply` turns it off and keeps every other permission).
- `BYPASS_EMBEDDING_AND_RETRIEVAL=true`: nothing is embedded into Open WebUI's
  vector store.
- Documents go to ICDEV document intelligence (dashboard) where entitlement,
  provenance and markings apply.

**Residual (record in the ATO boundary):** Open WebUI 0.13 enforces the upload
permission in the UI only — `POST /api/v1/files/` does not check it — so an
authenticated user calling the API directly can still store a file. The
`openwebui-data` volume (chat history, any such file) is therefore **inside the
boundary**: encrypted at rest, backed up and retained under the same policy as
ICDEV data.

## Roadmap (kanban card `owui`)

| Phase | Tasks |
|---|---|
| 0 Decision & boundary | `owui-dec-01` (this doc, D-OWUI-1, sandbox-coverage Gap 78) |
| 1 Governed model endpoint | `owui-api-01` ✅, `owui-api-02` streaming parity, `owui-api-03` Open WebUI configurator + bypass `--check` |
| 2 Tools via MCP | `owui-mcp-01` read-only toolset profile, `owui-mcp-02` caller_source + authorizer enforce survey |
| 3 Identity | `owui-idp-01` ✅ shared-IdP OIDC + token passthrough |
| 4 Knowledge stays in ICDEV | `owui-rag-01` citations → Open WebUI `sources`, `owui-rag-02` upload filter → document intelligence |
| 5 Governed workflows from chat | `owui-flow-01` `icdev/intake` + `icdev/kanban`, `owui-flow-02` per-exchange audit |
| 6 Productize | `owui-ship-01` compose profile + Helm, digest pin; `owui-ship-02` Playwright E2E |

## Operator decisions held by `owui-gate-00` (recorded 2026-11-05)

1. **Identity provider: Keycloak (OIDC).** Open WebUI and ICDEV trust the same Keycloak
   realm. A local lab realm (`icdev`, loopback only, dev mode) proves the path; a
   production deployment points the same settings at the enterprise Keycloak (or any
   OIDC IdP: Entra, a CAC-backed broker) -- only issuer, client and group names change.
2. **Licence review: accepted by the operator.** Open WebUI is deployed unmodified from
   the pinned upstream image, so its branding clause is satisfied; re-review before
   rebranding or redistributing a modified image.
