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

### 3.1 What the chain costs (ctx-obs-02)

`CortexResult.latency_ms` is the **LLM call only** — it comes from
`LLMResponse.duration_ms`, or the `perf_counter` around the router invoke. For a
long time nothing timed the chain *around* it, so the question that decides
whether the seven gates are worth their cost — and whether perf work should
target the gates or the model call — had no answer.

`wrap()` now times itself and records three fields on the `GovernanceReport`:

| Field | Meaning |
|---|---|
| `total_ms` | the whole governed call (gates + operation) |
| `operation_ms` | the wrapped operation alone |
| `gate_ms` | per-gate wall time, keyed like `outcomes` |
| `governance_ms` *(derived)* | `total_ms - operation_ms` — the chain's own cost |

Per-gate timing extends `gates_json`, which already carried the per-gate
outcomes, so there is no schema migration. Two deliberate properties:

- **`total_ms` excludes the audit write.** A write cannot be inside the
  measurement it persists, so the split is taken before it — which also makes
  `sum(gate_ms) == total_ms` on a call that ran the chain to completion. A call
  blocked mid-gate sums to less: the interrupted segment is never closed.
- **`0.0` means *not measured*, never *free*.** Rows written before ctx-obs-02,
  and cache hits (which never enter the pipeline), carry no timing.
  `/cortex/metrics` therefore averages over `summary.timed_calls`, not
  `summary.calls`, and says on the panel when the two differ. The timing fields
  ride the same `gates_json` blob as the spend accounting, so they inherit its
  `_DETAIL_ROW_LIMIT` sampling cap and its `detail.truncated` flag — adding a
  field is not a reason to widen the cap.

The panel surfaces **Avg latency** (LLM only), **Avg governance**, **Avg wall
time** and a **By gate** table, so "governance is 40% of the call" can be
narrowed to "…and it is one gate". Pinned by
`tests/cortex/test_governance_timing.py`.

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
maintain.

**Two entry points, one tool set (ctx-reach-03).** `.mcp.json` configures only
`icdev-unified` (`tools/mcp/unified_server.py`), which registers all eight
`cortex_*` tools from `TOOL_REGISTRY` — that is how they are reached in this
repo. `python tools/mcp/cortex_server.py` runs the same handlers standalone over
stdio and is kept deliberately, as a **bounded** surface for an external or
air-gapped MCP client that must see only the Cortex family rather than the full
unified registry. The risk of two entry points is drift, so it is gated: every
tool in `CORTEX_TOOLS` must also be in `TOOL_REGISTRY`
(`tests/cortex/test_cortex_reach_decisions.py`), or it would be reachable only
via the server nobody launches.

Neither `cortex_govern` nor `cortex_agent_launch` has an ungoverned fallback.
Both shipped ahead of the ctx-govern-04 facades behind a
`getattr(cortex_api, ..., None)` probe with a standalone
`GovernancePipeline` / `ACEController` + `run_agent_loop` branch behind it; the
facades landed, so the probe could never fail, and the branch it guarded was
deleted in ctx-reach-03. See
[phase-ctx-reach-03-cortex-reach-decisions.md](phase-ctx-reach-03-cortex-reach-decisions.md).

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

### Import namespace (cxo-doc-02)

Most imports inside `tools/cortex/` are spelled `tools.*`, but three are spelled
`icdev.tools.*` — `api.py::_run_single_agent` (`llm.agent_loop`, twice) and
`blueprint.py::_propose_roles` (`ace.problem_classifier`). That is **not** drift
to be normalised away; the canonical spelling is `icdev.tools.*` and those three
sites are the ones that are already right.

The two roots are not interchangeable, and which one you get depends on how
ICDEV was installed:

| Environment | Resolution | Effect |
|-------------|-----------|--------|
| Wheel / `pip install icdev` | `icdev/__init__.py::_alias_tools_namespace()` binds `sys.modules["tools"] = icdev.tools` | `tools.X` **is** `icdev.tools.X` — one object |
| Source checkout (this repo) | A real top-level `tools/` package exists, so the alias deliberately stands down | `tools.X` and `icdev.tools.X` are **separate module objects** with separate state |

That second row is the whole point. In a source checkout `tools.iqe.executor`,
`tools.db.storage` and `tools.ace.problem_classifier` each load a second time
under the `tools.` name, producing distinct classes and distinct module-level
caches. `icdev.tools.*` is therefore the only spelling that binds the same
object in both environments — which is exactly why CLAUDE.md names it canonical.

Consequences for the two call sites:

- **`ace.problem_classifier`** — load-bearing. ACE's own modules (`tools/ace/controller.py`, `problem_classifier.py`, …) import through `icdev.tools.*`. Spelling this one `tools.*` would hand Cortex a *different* `ProblemClassifierLens` class with its own role-loader state.
- **`llm.agent_loop`** — identity happens to survive either spelling today, because `tools/llm/agent_loop.py` was collapsed into a pure re-export shim (`dba8d4b59`) after the physical copy it replaced drifted out of sync and silently served a stale loop. That is a property of one file, not of the namespace, so the canonical spelling still stands.

What was explicitly **not** done: converting the rest of `tools/cortex/` to
`icdev.tools.*`. Cortex is hosted by the Flask dashboard, which reaches it
through `tools.*`; flipping ~100 sibling imports would have Cortex binding a
different `db.storage` (and therefore a different connection pool and RLS
predicate state) from its own host. The mixed spelling is the safe state, and
`tests/cortex/test_import_namespace.py` pins it so the three canonical sites are
not "tidied" back.

## 9. Verification

- Unit suite: `pytest tests/cortex/` (SQLite, conftest-forced).
- Live-PG isolation: `ICDEV_STORAGE_BACKEND=postgresql python tools/cortex/db/verify_tenant_isolation.py --json`.
- Air-gap: `python -c "from tools.cortex import assert_airgap_ready; assert_airgap_ready()"`.
- Coherence + TRUST coverage: `python tools/workflow/coherence_checker.py --all --gate`.
