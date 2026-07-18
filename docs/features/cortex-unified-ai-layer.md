# ICDEV Cortex — Unified AI Intelligence Layer

> CUI // SP-CTI

Cortex is ICDEV's single, governed facade over every AI capability on the
platform: the LLM router, the four retrieval backends (RAG / GraphRAG / Document
Intelligence / Keyword), IQE ask-your-data, and the multi-agent runtime — all
behind one import surface, one TRUST governance chain, and one row-level-security
model. It is the Snowflake-Intelligence / Palo-Alto-Cortex analogue for the
ICDEV stack: callers ask for an *outcome* (search, ask, complete, reason,
classify, extract, govern, agent) and Cortex routes, grounds, redacts, audits, and returns
a typed result — without the caller wiring backends or re-implementing safety.

This document is the capstone reference (ctx-expose-04) for the completed
**ICDEV Cortex — Unified AI Intelligence Layer** initiative. It cross-links the
[Cortex RLS / tenant-isolation audit](cortex-rls-audit.md).

## 1. Why a facade

At 90 % per-step reliability a 5-step AI workflow degrades to ~59 % end-to-end.
Cortex confines probabilistic reasoning to a thin routing/answer layer and makes
everything else deterministic and enforced: the same governance chain, the same
citation grounding, the same redaction, the same audit row, on *every* path —
in-process, REST, or MCP. Add a new consumer and it inherits the guarantees for
free; there is no "unguarded" way to call an LLM through Cortex.

## 2. The eight facades

All eight live in `tools/cortex/api.py` (mirrored to `icdev/tools/cortex/`) and
are the *only* public entry points. Each runs through `GovernancePipeline` via
the `_governed_facade` decorator — a public facade cannot be added without
wrapping (enforced by `tests/cortex/test_api_governed.py`).

| Facade | Purpose | Returns |
|--------|---------|---------|
| `search(query, top_k, strategy, ctx)` | Unified retrieval with agentic strategy routing + CRAG corrective loop across rag/graph/dic/kb | `list[CortexSearchResult]` |
| `ask(question, mode, ctx)` | Ask-your-data — IQE primary, NL→SQL fallback, TRUST-labelled | `CortexResult` (rows + executed IQE/SQL + citations) |
| `complete(prompt, ctx)` | Free-form completion via the config-routed LLM chain | `CortexResult` |
| `reason(prompt, mode, ctx)` | Multi-step reasoning — `cot` / `debate` / `council` over the router's chain orchestration, governed | `CortexResult` (`metadata.reason_mode`) |
| `classify(text, labels, ctx)` | Single-label classification, deterministic air-gap fallback | `CortexResult` |
| `extract(text, schema, ctx)` | Structured extraction to a caller JSON schema | `CortexResult` |
| `govern(text, sources, ctx)` | Run the TRUST chain standalone over already-produced text (incremental-adoption entry for non-Cortex tools) | `GovernanceReport` |
| `agent(goal, roles, ctx)` | Facade over the ACE team runtime / single agent-loop | `CortexResult` |

`CortexContext` carries `tenant_id`, `user_id`, `classification`, `domain`, and
`fail_closed`. Identity fields are **always derived server-side** on the exposed
surfaces (REST / MCP) — never from the client body — so a caller can only narrow
(`domain`), never widen, access.

## 3. The TRUST governance chain

`tools/cortex/governance.py::GovernancePipeline.wrap()` runs one ordered chain
around every call:

1. **Gateway pre-check** (`tools/llm/gateway.py::check_text`) — prompt-injection
   / policy screen. A block fails closed with a typed `GovernanceBlockedError`.
2. **Input redaction** (`tools/redaction` anonymizer).
3. **Wrapped operation** (the facade body).
4. **Citation grounding** (shared `tools/quality/citation_grounding.py`).
5. **Content grounding** cross-check vs injected context.
6. **Output redaction** (`tools/llm/output_redactor`).
7. **Provenance record** (`tools/provenance/registry`) **+ one append-only
   `cortex_audit` row** (NIST AU, in `APPEND_ONLY_TABLES`), written through the
   RLS-aware storage shim, plus a structured logger line.

Non-retrieval calls skip only the grounding gates (recorded as `skip` in the
`GovernanceReport`, not silently). Gate errors fail *open* (warn) unless
`CortexContext.fail_closed`. Every governed call therefore leaves an auditable
`GovernanceReport` (`gates_run`, `outcomes`, `blocked`).

## 4. Persistence & row-level security

Two governance tables (migration 262) — `cortex_sessions` (mutable per-caller
lifecycle) and `cortex_audit` (append-only, one row per governed call) — plus the
canvas chat store (migration 263): `cortex_chat_sessions`, `cortex_messages`,
`cortex_search_history`. All carry `tenant_id` + `classification` and are read
through the RLS-aware `get_connection()`, so per-tenant isolation and
Bell-LaPadula read-down apply on every read.

PostgreSQL is the primary backend; SQLite is an init-only fallback (and the
conftest-forced test backend). Cross-tenant isolation across the connection
primitive, the IQE path, and the NLQ fallback is proved by
`tools/cortex/db/verify_tenant_isolation.py` — see the
[RLS audit](cortex-rls-audit.md).

## 5. Exposure surfaces

The same eight facades are reachable three ways — one governance chain behind all
of them:

### 5.1 `/cortex` dashboard canvas
Registry-driven (`args/component_registry.yaml` key `cortex`, env flag
`ICDEV_CORTEX_ENABLED`, `min_il: IL4`). A mode + domain-lens picker, a governed
chat surface (`POST /cortex/api/chat`) with intent routing to the right facade,
thin session reuse (`GET /cortex/api/session/<id>`), and IQE integration
(`POST /cortex/api/iqe-query` over the `cortex.*` collections). Blueprint:
`tools/cortex/blueprint.py`.

### 5.2 REST API v1 — `/cortex/api/v1/*` (ctx-expose-02)
`tools/cortex/rest_v1.py` folds seven governed POST-JSON core-facade endpoints
(`search`, `ask`, `complete`, `reason`, `classify`, `extract`, `govern`) onto the **same**
canvas blueprint via `register_rest_v1(cortex_bp)` — one Blueprint, one
`url_prefix`, one auth path. Requests are validated by
`tools/cortex/validators.py`; identity is derived from `g.security_context`
only. Error envelopes are stable: 401 unauthenticated, 400 validation,
403 governance/analyst block (+ serialized `GovernanceReport`), 422 unanswerable,
500 otherwise. The `/v1/` prefix is an additive-only version contract.

### 5.3 MCP tool family — `cortex_*` (ctx-expose-01)
`tools/mcp/cortex_server.py` exposes eight MCP tools registered under category
`cortex` in `tools/mcp/tool_registry.py`: `cortex_search`, `cortex_ask`,
`cortex_complete`, `cortex_reason`, `cortex_classify`, `cortex_extract`, `cortex_govern`,
`cortex_agent_launch`. Handlers are thin calls into `tools/cortex`; the gateway
`security_chain` (D284) wraps traffic, so there is no per-server auth to
maintain. Run standalone over stdio with `python tools/mcp/cortex_server.py`.

## 6. Domain lenses (ctx-canvas-04)

Domain lenses (`tools/cortex/domains/`) are **data-driven configuration profiles
over the facade, not new machinery**. A `DomainProfile` (scope backends +
collections + source prefixes, a persona system prompt, intent bias, and a triage
formatter) is loaded from `args/cortex_config.yaml` `search.domains.<name>` with a
code-side fallback (YAML wins field-by-field).

`security.py` is the Palo Alto Cortex **XSIAM** analogue: it scopes `search` to
threat / vuln / incident sources (`pvm_`, `dsoc_`, `incident_`, `cve`, `threat`,
`vuln` prefixes), injects a SOC-analyst persona (severity-first, evidence-cited),
and emits an XSIAM-style `triage_summary()` (top risks / blast radius /
recommended actions — deterministic, grounded by construction). The strategy
router (`search_service.py`) consumes the lens: it intersects backend selection
with the lens's allowed backends and drops hits outside the source scope,
recording `domain / collections / sources / filtered_out` in
`metadata.router.domain_scope` so the scoping is observable, never silent.

A new lens (compliance, program-management, …) is a YAML block plus an optional
sibling module — no new plumbing.

## 7. Air-gap invariant

Every `cortex_*` routing chain (`args/llm_config.yaml`) retains a local Ollama
tier, so the whole facade is air-gap safe. `assert_airgap_ready()` raises
`CortexAirgapError` if any chain loses its local tier — a guard, not a hope.

## 8. Where things live

| Concern | Path |
|---------|------|
| Facades | `tools/cortex/api.py` |
| Governance chain | `tools/cortex/governance.py` |
| Analyst (ask-your-data) | `tools/cortex/analyst.py` |
| Strategy router / search | `tools/cortex/search_service.py` |
| Domain lenses | `tools/cortex/domains/` |
| Canvas + REST blueprint | `tools/cortex/blueprint.py`, `tools/cortex/rest_v1.py` |
| Request validators | `tools/cortex/validators.py` |
| MCP server | `tools/mcp/cortex_server.py` |
| Persistence / RLS | `tools/cortex/db/init_db.py`, `db/verify_tenant_isolation.py` |
| Behaviour config | `args/cortex_config.yaml` (`$ICDEV_CORTEX_CONFIG`) |
| IQE adapter + seeds | `tools/iqe/adapters/cortex.py`, `context/iqe/queries/cortex/` |

Everything under `tools/cortex/`, `tools/mcp/cortex_server.py`, and the IQE
adapter is mirrored to `icdev/tools/…`.

## 9. Verification

- Unit suite: `pytest tests/cortex/` (SQLite, conftest-forced).
- Live-PG isolation: `ICDEV_STORAGE_BACKEND=postgresql python tools/cortex/db/verify_tenant_isolation.py --json`.
- Air-gap: `python -c "from tools.cortex import assert_airgap_ready; assert_airgap_ready()"`.
- Coherence + TRUST coverage: `python tools/workflow/coherence_checker.py --all --gate`.
