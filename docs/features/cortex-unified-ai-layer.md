<!-- CUI // SP-CTI -->
# ICDEV Cortex — Unified AI Layer

> Feature documentation for the ICDEV Cortex initiative (project `ctx`, 26 tasks
> across 6 epics: core, search, analyst, govern, canvas, expose).
> Cortex is a Snowflake-Cortex-style **facade** over the platform's existing AI
> subsystems — it unifies, it does not rebuild.

## Summary

ICDEV Cortex is a single import surface (`tools.cortex`) that fronts the
platform's fragmented AI capabilities behind one normalization contract and one
enforced TRUST governance chain. Before Cortex, callers wired `LLMRouter` /
`LLMRequest` per call site and chose between four incompatible search APIs
(`rag_search`, `kg_search`, `dic_search`, `search_knowledge`) and two NL-query
engines (IQE DSL vs. NLQ→SQL). Cortex collapses that into seven facade functions
returning one result shape.

Design decisions locked at approval (2026-07-10):

- **Unify, don't rebuild** — Cortex is a thin facade; the retrieval/LLM/graph
  backends stay where they are and are wrapped read-only behind adapters.
- **Internal-first, but multi-tenant from day one** — `CortexContext` carries
  `tenant_id` and `classification` on every call so RLS and read-down filtering
  key off it without a retrofit.
- **Air-gap / Ollama-only from day one** — every routing chain keeps a local
  provider tier; the invariant is enforced at import, not at first offline call.
- **Retrieval beyond plain RAG** — a strategy router classifies each query and
  fans out to GraphRAG / vector+BM25 / DIC, fuses cross-backend, and runs a CRAG
  corrective loop on low-confidence results.
- **Hybrid model** — a general layer plus a security-intelligence (XSIAM-style)
  domain lens configured, not forked.

## Package layout

Everything lives under `tools/cortex/` (canonical mirror `icdev/tools/cortex/`):

| Module | Role |
|--------|------|
| `schemas.py` | The normalization contract: `CortexContext`, `CortexResult`, `CortexSearchResult`, `Citation`, `GovernanceReport`, `CORTEX_BACKENDS`. |
| `api.py` | Facade functions `complete` / `classify` / `extract`, and re-exports of `ask` and search. Guaranteed free of any provider/model-id literal. |
| `analyst.py` | Cortex Analyst — natural-language ask-your-data over IQE (primary) with an NL→SQL fallback. |
| `search_service.py` | Backend adapters, the strategy router (`search`), and the CRAG corrective loop. |
| `governance.py` | `GovernancePipeline` — the single enforced pre/post TRUST chain. |
| `config.py` | `args/cortex_config.yaml` loader + the air-gap readiness invariant. |
| `__init__.py` | Public export surface (`from tools.cortex import ...`). |

Behavior is config-driven via `args/cortex_config.yaml`; routing is config-driven
via `args/llm_config.yaml`. See [[llm-config-single-source]] for the shared
`resolve_llm_config_path()` resolution rule (PR #139).

## Architecture

Cortex is layered so each concern maps to one module and every answer crosses
exactly one governance chain.

```
caller
  │  CortexContext(tenant_id, classification, domain, air_gap, fail_closed)
  ▼
tools.cortex facade  (complete / classify / extract / search / ask / agent / govern)
  │
  ├─ LLM path ── api._invoke ─► LLMRouter.invoke(function, request)  ─► args/llm_config.yaml routing
  │                                    (air-gap → exclude_model_ids forces local tier)
  │
  ├─ Search path ─ search_service.search ─► classify_route ─► fan-out adapters ─┐
  │        rag │ graph │ dic │ kb   (each normalized to CortexSearchResult)     │
  │        └─ per-backend timeouts ─ RRF/score fusion ─ CRAG corrective loop ◄──┘
  │
  └─ Analyst path ─ analyst.ask ─► nl_to_iqe → parse → authorize → execute (IQE)
                                    └─ fallback ─► NL→SQL (nlq_processor, read-only)
  ▼
GovernancePipeline  (pre-check → input redaction → operation → citation grounding
                     → content grounding → output redaction → provenance + audit)
  ▼
CortexResult(text, citations[], governance, provider, model, cost, latency_ms, grounded, data, metadata)
```

The four retrieval backends return four incompatible native shapes; each adapter
in `search_service.py` maps its backend into `CortexSearchResult` with the score
clamped to `[0, 1]` (native scores preserved verbatim in `raw_scores`) so results
from different backends are directly comparable. Adapters are exception-isolated:
a failing backend logs a warning and returns `[]`, never breaking the others.

- **rag** — `tools/rag/retriever.py` `RAGRetriever.search()` (vector + BM25 +
  time-decay + rerank; `tenant_id` scoped).
- **graph** — `tools/knowledge_graph/graph_rag.py` `retrieve()` (sqlite3-backed,
  wrapped read-only; additive node scores peak-normalized).
- **dic** — `tools/document_intelligence/search_engine.py` `DICSearchEngine`
  (clearance-aware ranking; document classification preserved as
  `Citation.clearance_required`).
- **kb** — `search_knowledge` keyword patterns (`tools/mcp/knowledge_server.py`).

The strategy router (`classify_route`) checks deterministic pattern rules first
(exact-term/identifier → kb, relational → graph, document/clearance → dic), and
only consults the RAG taxonomy classifier for pattern-free queries: a confident
`fact_single` routes to rag alone, everything else is ambiguous and fans out to
the configured backend set in parallel. When the top fused score falls below
`search.crag_threshold`, one corrective iteration rewrites the query (routing
function `cortex_search_rewrite`) and re-runs routing + fusion; the outcome is
always observable in result metadata (`corrective_pass` / `corrective_skipped`).

Cortex builds on the retrieval work in [[project_rag_mcp_agentic_rag_kg]] and the
grounded-search + citation model from [[project_dic_canvas]].

## API Reference

Import the whole facade from one namespace: `from tools.cortex import ...`.
Every function returns a `CortexResult` (except `search`, which returns a
`list[CortexSearchResult]`).

### `complete(prompt, function="cortex_complete", ctx=None, *, system_prompt="", max_tokens=None, temperature=None) -> CortexResult`
Free-form completion via the config-routed LLM chain. `ctx.tenant_id` /
`ctx.classification` are threaded into the `LLMRequest` for RLS and redaction
policy. Router errors propagate (no deterministic equivalent).

### `classify(text, labels, ctx=None, function="cortex_classify") -> CortexResult`
Classify `text` into exactly one of `labels`. Tries the LLM chain first; when the
router raises (offline / air-gap / exhausted chain) or the answer maps to no
label, **degrades** to the deterministic heuristics in
`tools/rag/query_classifier.py` (marked `provider="deterministic"`).

### `extract(text, schema, ctx=None, function="cortex_extract") -> CortexResult`
Extract structured data conforming to a JSON `schema`. The schema is passed both
as `LLMRequest.output_schema` (native structured output) and inline in the prompt
(providers without). `text` is the extracted JSON serialized as a string.

### `search(query, top_k=5, ctx=None, strategy="auto", config=None) -> list[CortexSearchResult]`
Unified search with agentic strategy routing + CRAG correction. `strategy` is one
of `CORTEX_STRATEGIES` (`auto`, `all`, or a backend name). `ctx.domain`
intersects the selection with that domain's allowed backends. `top_k` applies
per backend, so fan-out can return more than `top_k` results.

### `ask(question, mode="auto", ctx=None, *, canvas=None, collections=None, conn=None, summarize=False) -> CortexResult`
Cortex Analyst — answer a natural-language data question. IQE-primary
(`foreach … where … select`), with an NL→SQL fallback in `mode="auto"` only when
IQE cannot resolve/translate/authorize (never on execution failures, never when
scope is pinned). The LLM only ever produces the *query*, never the answer, so
every returned row is real data — TRUST by construction.

### `govern` / `agent` (governance + agentic loop)
`GovernancePipeline.wrap(fn, ctx, prompt=..., context_sources=..., retrieval=...)`
runs any operation inside the enforced TRUST chain and returns
`(result, GovernanceReport)`. The `@governed(...)` decorator is the sugar form.
The agentic capability builds on the shared agent-loop primitive
([[agent-loop-primitive-shipped]], [[agent-loop-budget-guardrails]]).

### Core dataclasses (`schemas.py`)
- `CortexContext` — caller identity/policy: `tenant_id`, `user_id`,
  `classification` (default `CUI`), `domain`, `air_gap`, `fail_closed`.
- `CortexResult` — `text`, `citations[]`, `governance`, `provider`, `model`,
  `cost`, `latency_ms`, `grounded`, `data`, `metadata`.
- `CortexSearchResult` — `content`, `score` (clamped `[0,1]`), `backend`,
  `strategy`, `citation`, `raw_scores`, `metadata`.
- `Citation`, `GovernanceReport` — provenance pointer and gate record.

All dataclasses round-trip through `to_dict()` / `from_dict()` (unknown keys
ignored) so they cross MCP / dashboard / audit boundaries losslessly.

## Governance Chain

Every Cortex invocation runs through `GovernancePipeline` instead of each caller
wiring ad-hoc governance. The chain, in enforced order (`GATE_ORDER`):

| # | Gate | Backing module | Notes |
|---|------|----------------|-------|
| 1 | `pre_check` | `tools/llm/gateway.check_text()` | Injection/PII/length/rate/cost. A block **always fails closed** — the wrapped operation never runs. |
| 2 | `input_redaction` | `tools/redaction` anonymizer | Masks PII in the prompt before any provider sees it. Never skipped. |
| 3 | `operation` | the wrapped callable | LLM budget guardrails apply inside `LLMRouter`. Provider errors are recorded, then re-raised (the pipeline governs, it does not swallow). |
| 4 | `citation_grounding` | `tools/quality/citation_grounding` | Validates inline `[source: N]` tags against injected sources. Hallucinated citations fail here. |
| 5 | `content_grounding` | `tools/quality/content_grounding` | Placeholder scan + token-overlap cross-check of the output against injected context. |
| 6 | `output_redaction` | `tools/llm/output_redactor` | Masks PII/secrets in the response. Never skipped. |
| 7 | `provenance` | `tools/provenance/registry.register_citation` | Unified source-citation record + append-only audit row. Never skipped, never blocking. |

Non-retrieval calls (`retrieval=False`, e.g. plain `complete()`) may skip the two
grounding gates — **never** redaction or provenance — and the skip is recorded
explicitly in the `GovernanceReport` (outcome `"skip"`) so governance stays
observable, not implied.

**Fail-open vs. fail-closed:** gate errors degrade to `"warn"` by default
(mirroring `redaction.fail_closed: false`); when `CortexContext.fail_closed` is
True (or `governance.fail_closed` in `args/cortex_config.yaml`), any gate error or
`"fail"` outcome blocks the response with a typed `GovernanceBlockedError`.

The Analyst path adds its own recorded gates (`collection_resolution`,
`iqe_translation`, `collection_authorization`, `iqe_execution`, plus a shared
`safety_screen` / `sql_readonly` / `table_allowlist` layer and a
`citation_validation` gate on LLM-summarized answers). This satisfies the CLAUDE.md
TRUST invariant that every LLM-generated artifact carry validated `[source: …]`
citations built on the shared `tools/quality/citation_grounding.py`. See
[[feedback-trust-explicit-in-ai-drafting-plans]].

## Security Lens

- **Tenant + classification threading** — `CortexContext.tenant_id` /
  `classification` flow into `LLMRequest` (LLM path), into `RAGRetriever` /
  `DICSearchEngine` filters (search path), and into the DB `SecurityContext` on
  the Analyst IQE connection so RLS predicates and read-down filtering apply.
  Classifications map to impact levels (`CUI→IL4`, `CUI//SP-CTI→IL5`,
  `SECRET→IL6`) for the input redaction gate.
- **Clearance-aware retrieval** — the DIC adapter drops above-clearance documents
  before the cap and preserves each document's classification as
  `Citation.clearance_required`; uncited answer fragments are never returned.
- **Analyst safety layer** — before either engine executes, the raw question is
  screened for deterministic SQL-injection shapes (stacked statements, DDL,
  `UNION SELECT` exfil, tautologies, comment terminators) plus gateway
  guardrails; generated SQL is gated SELECT-only + single-statement + a
  table-allowlist derived from registered IQE collections. Every rejection is
  audited (`nlq_queries` row, status `blocked`) and raised as
  `CortexQueryBlocked`. The e2e suite exercises injection attempts explicitly.
- **Security domain lens** — `search.domains.security` in `args/cortex_config.yaml`
  scopes the router to `[rag, graph, kb]` for `ctx.domain == "security"`, the
  hybrid security-intelligence lens locked at approval.
- **Redaction posture** — input and output redaction are always in the chain and
  honor the platform `redaction.fail_closed` / `mask_at_ingestion` toggles; the
  gates are never removed (CLAUDE.md TRUST invariant).

## Air-Gap Behavior

Air-gap support is a day-one invariant (`ctx-core-03`), not a retrofit:

- **Import-time assertion** — `config.assert_airgap_ready()` runs when `api.py`
  is imported and verifies every routing function in `CORTEX_ROUTING_FUNCTIONS`
  (`cortex_complete`, `cortex_classify`, `cortex_extract`,
  `cortex_search_rewrite`, `cortex_analyst`) keeps at least one local-tier model
  in its `args/llm_config.yaml` chain. A violation raises `CortexAirgapError`
  listing every missing entry — you fail at import, not at the first offline call.
- **Local tier definition** — a local model resolves through an `ollama`-type
  provider with **no** `api_key_env` (the presence of `api_key_env` is what
  distinguishes the local daemon from the cloud variant; see
  [[cli-bridge-cui-egress]]).
- **Per-request forcing** — when `ICDEV_AIRGAP=1` or `CortexContext.air_gap` is
  set, `api._invoke` computes `airgap_exclusions()` (every model-id that resolves
  only through a non-local provider) and passes them as
  `LLMRouter.invoke(exclude_model_ids=...)`, so chain-walking skips straight to
  the local tier. The kwarg is omitted otherwise, keeping plain calls
  signature-compatible.
- **Graceful degradation** — `classify()` falls back to deterministic
  `query_classifier` heuristics with no LLM at all; the CRAG rewrite path raises
  cleanly (surfaced as `corrective_skipped`) rather than failing the search when
  no provider is reachable.

This aligns with the platform `air-gap` core profile in
[[enterprise-configurable-platform]], which disables cloud LLM providers by env
override.

## Configuration

`args/cortex_config.yaml` is the behavior source of truth (loaded via
`config.load_cortex_config()` — defaults deep-merged with the file, cached by
path+mtime). Key knobs:

```yaml
search:
  router:
    factual_confidence: 0.75      # min taxonomy confidence to route fact_single → rag alone
  strategy_weights: {rag: 1.0, graph: 0.8, dic: 0.9, kb: 0.6}
  rrf_k: 60                       # Reciprocal Rank Fusion constant
  crag_threshold: 0.55            # below this top score → corrective rewrite + retry
  timeouts: {default: 10.0, rag: 10.0, graph: 8.0, dic: 10.0, kb: 5.0}
  fan_out: {backends: [rag, graph, dic], max_workers: 4}
  domains:
    security: {backends: [rag, graph, kb], collections: [security]}
governance:
  fail_closed: true               # block CUI+ content that can't be grounded
  skip_grounding_for_plain_complete: true
analyst:
  nlq_fallback_enabled: true
```

`icdev/args/cortex_config.yaml` is a generated mirror — edit the root file and
copy across, same convention as `args/llm_config.yaml`. Resolution order:
`$ICDEV_CORTEX_CONFIG` → `cortex_config.yaml` next to the resolved
`args/llm_config.yaml`.

## Verification Results

Backend epics are merged to `main` (core, search incl. CRAG, govern, analyst).
Verification coverage as of 2026-07-11:

- **Unit / integration** — the `tests/cortex/` suite covers the schema
  round-trips, the facade functions, the `test_no_model_id_literals_in_module`
  guard (proves `api.py` carries no provider/model literal), the air-gap
  readiness assertion (`test_airgap_assertion.py` pins
  `CORTEX_ROUTING_FUNCTIONS` against the `api.py` constants), the strategy router,
  and the CRAG corrective loop.
- **Analyst e2e** — the analyst epic e2e suite (`ctx-analyst-04`, merged in #172)
  exercises the IQE happy path, the NL→SQL fallback, and injection attempts that
  must be refused and audited.
- **Governance** — gate ordering and skip semantics are asserted via monkeypatched
  `_gate_*` seams; a blocked pre-check raises `GovernanceBlockedError` and the
  wrapped operation never runs.

Run the Cortex tests:

```bash
pytest tests/cortex/ -v --tb=short
python tools/workflow/coherence_checker.py --all
```

**Known gap (not yet shipped):** the **canvas epic** is incomplete — there is no
`/cortex` dashboard page, blueprint, or nav link, so `/cortex` currently returns
HTTP 404. The e2e spec `.claude/commands/e2e/cortex.md` was authored ahead of the
page (structural check green) and the `ctx-canvas-05` task remains blocked on the
page shipping. Treat any claim that the canvas is live as unverified until the
route renders (verify pattern: [[feedback-verify-abstraction-is-live-before-reusing]],
[[feedback_done_artifact_audit]]).

## Related documentation

- [[project-cortex-unified-ai-layer]] — project memory (epics, decisions, gotchas).
- [[project_rag_mcp_agentic_rag_kg]] — the agentic RAG + KG retrieval Cortex fronts.
- [[project_dic_canvas]] — Document Intelligence Canvas (grounded search + citations).
- [[enterprise-configurable-platform]] — registry-driven canvases + core profiles
  (air-gap profile).
- [[llm-config-single-source]] — `resolve_llm_config_path()` single-source rule.
- [docs/reference/compliance-security.md](../reference/compliance-security.md) —
  security gates (RAG, AI Security, Coherence).
