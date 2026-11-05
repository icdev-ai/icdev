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

## Roadmap (kanban card `owui`)

| Phase | Tasks |
|---|---|
| 0 Decision & boundary | `owui-dec-01` (this doc, D-OWUI-1, sandbox-coverage Gap 78) |
| 1 Governed model endpoint | `owui-api-01` ✅, `owui-api-02` streaming parity, `owui-api-03` Open WebUI configurator + bypass `--check` |
| 2 Tools via MCP | `owui-mcp-01` read-only toolset profile, `owui-mcp-02` caller_source + authorizer enforce survey |
| 3 Identity | `owui-idp-01` shared-IdP OIDC + token passthrough (held by `owui-gate-00`) |
| 4 Knowledge stays in ICDEV | `owui-rag-01` citations → Open WebUI `sources`, `owui-rag-02` upload filter → document intelligence |
| 5 Governed workflows from chat | `owui-flow-01` `icdev/intake` + `icdev/kanban`, `owui-flow-02` per-exchange audit |
| 6 Productize | `owui-ship-01` compose profile + Helm, digest pin; `owui-ship-02` Playwright E2E |

## Operator decisions held by `owui-gate-00`

1. **Identity provider.** Choose the IdP that Open WebUI and ICDEV will both trust (Keycloak,
   Entra, or a CAC-backed IdP). Put its issuer, client id and secret in `.env`.
2. **Licence review.** Review Open WebUI's licence (it has a branding clause) before
   redistributing the image in `deploy/offline/` or Helm.
