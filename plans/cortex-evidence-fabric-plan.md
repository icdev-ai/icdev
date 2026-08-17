# Cortex Evidence Fabric (CEF)

Make every `/document-intelligence/*` surface answer evidence questions through **one governed
Cortex seam** that federates local data, RAG, KG, a currency store, DataBridge external sources,
and an SME co-worker persona — with citations, provenance, HITL approval, and **no hardcoded
domain knowledge**.

---

## 1. What is actually there today (measured, not assumed)

Probed against the live PostgreSQL board on 2026-08-16.

### Live substrates — real data to build on
| Substrate | Rows | Note |
|---|---|---|
| `rag_chunks` / `rag_ingestion_log` / `rag_retrieval_log` | 4,111 / 2,004 / 2,430 | RAG live and consumed |
| `kg_nodes` / `kg_edges` | 8,919 / 16,630 | KG graph live |
| `docmod_eol_products` | 110 | 105 synced from `endoflife.date` — a **working external path** |
| `mc_net_eol_data` | 101 | network EOL |
| `docmod_findings` / `scan_runs` / `doc_scan_state` | 100 / 40 / 17 | DocDrift genuinely runs |
| `dic_drift_events` / `dic_acoic_regen_queue` | 54 / 54 | drift → regen queue live |
| DI corpus: `dic_documents` / `collections` / `versions` / `chunk_links` / `sections` | 54 / 28 / 30 / 168 / 69 | small but real |
| `docmod_catalog_entries` | 19 | curated, authoritative |
| `ace_coworkers` / `ace_instances` / `ace_audit_log` / `ace_trust_ledger` | 357 / 138 / 871 / 211 | ACE launch engine live, active 2026-08-14 |
| `cortex_audit` | 177 | 5 verbs, **9 real blocks**, 37 rows carry `provenance_id`, active 2026-08-15 |

### Empty or missing — the actual gap
| Substrate | State | Root cause |
|---|---|---|
| `docmod_defacto_standards` | **0** | `defacto_learner` derives from `ni_devices`, which is **0**. Writer ran, had nothing to learn from. |
| `docmod_nist_pubs` | **absent** | migration never ran → `policy_refs` pack has no substrate |
| `databridge_agent_access_log` | **absent** | DDL is SQLite syntax (`INTEGER PRIMARY KEY AUTOINCREMENT`, `datetime('now')`) on a PG-primary deployment. Every external fetch is currently **unauditable**, and `_audit()` swallows the failure. |
| DataBridge manifest | `enabled: false`, `connectors: []` | 33 connectors implemented, **0 authorized**; `db_connections` = 0 |
| `rag_provenance_ledger` | **0** | retrieval happens, provenance not persisted |
| `kg_ontology` | **0** | declared-ontology chain inert beneath a rich graph |
| `tech_radar_entries` / `_history` | **0 / 0** | declared-but-unconsumed; **not** a usable currency provider |
| `dic_ssp_fragments` | **0** | ACOIC SSP generation never produced output |
| `cross_canvas_context.py` | dead code | purpose-built for cross-canvas evidence, called by nothing but its test |

### Five separate evidence chains, none shared
| Surface | Reaches |
|---|---|
| `tech_writing_assist.research_and_draft()` | RAG → KG → web → **`cortex.api.complete()`** |
| `acoic.py` | RAG → KG → `LLMRouter` |
| `search_engine.py` (`DICSearchEngine`) | RAG → filesystem wiki cache → `LLMRouter` |
| `doc_modernization/scanner.py` + packs | local tables only |
| `doc_generator.py` | LLM only; evidence passed in by caller |
| DataBridge | **wired to nothing** — zero DI references |

### What Cortex already is
Not a stub. `tools/cortex/` is already the governed fabric layer:

- **`search_service.py` is already a 4-backend federation** — `CORTEX_BACKENDS = ("rag","graph","dic","kb")`
  (`schemas.py:24`) dispatched through `BACKEND_ADAPTERS` (`search_service.py:583`). Adapter contract:
  `f(query, top_k, ctx) -> list[CortexSearchResult]`, normalizing native hits into a mandatory
  `Citation`, score clamped to [0,1], natives preserved in `raw_scores`, **exception-isolated —
  an adapter must never raise**. Weighted RRF fusion, parallel fan-out under per-backend timeouts,
  CRAG corrective-retrieval loop.
- It **already distinguishes "backend died" from "corpus matched nothing"** via `BackendResults.errors`
  — the honest-zero discipline this plan depends on, already implemented.
- **`_governed_facade()`** wraps every public verb in the 8-gate TRUST chain
  (`pre_check → input_redaction → operation → citation_grounding → content_grounding →
  kg_grounding (opt-in) → output_redaction → provenance`), with
  `MANDATORY_GATES = (operation, output_redaction, provenance)` enforced at profile load. It records
  to `cortex_audit` and the provenance gate writes `source_citation_registry` rows with
  `citation_type="cortex"` — an already-registered vocabulary value.
  `search` and `ask` are registered exactly this way. **This is the sanctioned extension point.**
- **`_apply_domain_persona()`**, `_get_ace_controller()`, `_run_single_agent()` — ACE hooks present,
  but ACE is reachable only as an **action** via `cortex.agent`. It is **not** a retrieval backend today.
- **`args/cortex_config.yaml` has data-driven domain lenses** (`ctx-canvas-04`): backends, row-level
  source prefixes, weights, timeouts, persona. "A new lens is a YAML addition here."

**Two honest limits found:**
1. **There is no data-driven provider plugin point.** Adding a *new evidence source type* is a
   5-point code change (adapter fn, `CORTEX_BACKENDS`, `BACKEND_ADAPTERS`, optional route label,
   then config). Only the *domain lens* layer on top is YAML. So "no hardcoding" holds fully for
   **domains** (any field/industry/topic — via docmod pack YAML + a cortex lens) but **not** for
   source types, which stay a small, reviewed code surface. That is the right boundary and this
   plan keeps it.
2. **Cross-source synthesis is string concatenation.** There is no semantic entity resolution or
   join across backends — so today nothing can notice that RAG and the catalog *disagree* about an
   entity. That absence is precisely the "unseen data" gap this plan closes.

### What the pack system already is
`tools/doc_modernization/` is already the right extensibility shape:
- Packs are **YAML in `args/docmod/packs/`** naming a dotted `evaluator`; 8 exist
  (`crypto_protocols` = the TLS case, `network_hardware` = the Nortel case, plus `software`,
  `policy_refs`, `change_control`, `evidence_currency`, `architecture_patterns`, `sop_workflows`).
- A **generic rulebook evaluator** driven by `args/docmod/rulebook_*.yaml` means a new domain needs
  **zero Python**.
- `base_pack.py` **TRUST rule 1**: `evaluate()` must derive its verdict from deterministic evidence,
  never an LLM. The LLM only words redline prose around a verdict produced there.
- `Verdict.evidence` is citation-shaped for `tools/quality/citation_grounding.validate_citations`.
- Packs publish extraction regexes into `knowledge_graph.text_network.EXTRA_ENTITY_PATTERNS`.

**Conclusion: most of what was asked for already exists as separate, unconnected parts.
This is an integration and activation job, not a greenfield build.**

---

## 2. Decisions taken

| Decision | Choice |
|---|---|
| Egress when local/RAG/KG are silent | **Auto-fetch from configured + authorized sources, log every fetch**; degrade to local-only when air-gapped |
| Stale-document handling | **Propose with evidence via TRUST; HITL approves.** Never auto-apply |
| SME persona vocabulary | **Open** — generate an SME for any domain encountered |
| Currency knowledge | **Build a cached, domain-agnostic entity-currency store** |
| Cortex role | **Mandatory chokepoint** — all DI evidence retrieval routes through Cortex |
| Empty substrates | **Fix foundations first** |
| Unanswerable entities | **Unknown is a visible finding** |
| Delivery | **Project card + seeded kanban** |

---

## 3. Architecture

**Principle:** DI surfaces stop retrieving evidence themselves. They ask Cortex. Cortex federates
registered backends, governs which may be consulted, normalizes to citations, records provenance,
and returns a deterministic verdict plus an explicit gap report.

### The ladder — every rung registered, none hardcoded in DI
| # | Backend | Substrate | Status |
|---|---|---|---|
| 1 | `dic` | DI corpus (54 docs / 69 sections) | exists |
| 2 | `currency` **NEW** | catalog 19, EOL 110, net-EOL 101, defacto 0 → new general store | build |
| 3 | `rag` | `rag_chunks` 4,111 | exists |
| 4 | `graph` | `kg_nodes` 8,919 / `kg_edges` 16,630 | exists |
| 5 | `kb` | knowledge patterns | exists |
| 6 | `external` **NEW** | DataBridge: 33 connectors, 0 authorized | build + enable |
| 7 | `sme` **NEW** | ACE: 357 coworkers, open-vocab `ensure_sme` | build |

### New verb: `cortex.resolve(entity, question, ctx)`
Registered through `_governed_facade` exactly as `search` and `ask` are, so it inherits the TRUST
gate chain, `cortex_audit`, `provenance_id`, and real blocking for free.

Returns:
- **`verdict`** — deterministic, produced by pack `evaluate()`, never by an LLM:
  `current | deprecated | superseded | unknown`
- **`citations[]`** — validated through `tools/quality/citation_grounding.py`
- **`gaps[]`** — no backend could answer → **unknown is a finding**
- **`conflicts[]`** — backends disagreed (the "unseen data")
- **`backend_errors[]`** — died vs. matched-nothing, reusing the distinction `search_service` already makes

### How the examples work with zero hardcoding
- **TLS 1.1 in an SOP** → `crypto_protocols` pack extracts the entity → `currency` backend consults
  the rulebook + currency store → deterministic `deprecated`, superseded-by from data → redline
  proposed with citations → HITL approves. TLS versions live in `args/docmod/rulebook_crypto.yaml`
  and DB rows, never in code.
- **Nortel in an architecture doc** → `network_hardware` pack extracts → catalog/EOL/de-facto rungs
  are silent → `external` rung asks an authorized DataBridge source → if still silent, `unknown`
  becomes a finding, and the `sme` rung attaches an advisory opinion.
- **Any other field/industry/topic** → drop a pack YAML (+ rulebook YAML) in `args/docmod/`.
  No Python. Add a domain lens in `args/cortex_config.yaml` to scope it.

### SME co-worker — advisory only
When rungs are silent or conflict, Cortex calls ACE `ensure_sme(domain)` (open vocabulary for
identity; capability bundle closed to `advisory`, which ships `trust_tier: red` with empty
`icdev_tools` and `folder_access`, so a generated SME **cannot write or execute**) and
`persona_query` to adjudicate. The SME opinion is attached to the finding as advisory metadata and
**never becomes the deterministic verdict** — preserving `base_pack` TRUST rule 1.

Note: `ace_ensure_sme` has **never successfully produced a role** (no file in `args/ace/roles/`
carries the `generated_at` stamp, `_generated_smes.json` does not exist). Phase 1 must prove it
works, not assume it.

### Governance and egress
The `external` rung reuses DataBridge's existing, well-built authorization model rather than
inventing one: air-gap interlock, connector + table allowlist, per-agent role grants, classification
ceiling over `UNCLASSIFIED → CUI → SECRET → TOP SECRET`, read-only enforcement, fail-closed
`GovConSanitizer` egress redaction, and row caps (200 default / 1000 hard).

Two Cortex-side controls apply on top:
- **`governance.fail_closed` defaults to `false`** platform-wide. The `external` rung must set
  `CortexContext.fail_closed = True` per call, so a governance gate that cannot run blocks the
  fetch rather than letting it through.
- **`service_keys.py` already defines `databridge:*` scopes that are never granted by default.**
  Authorizing the external rung is a scope grant, not a new permission system.

---

## 4. Risk

Making Cortex a mandatory chokepoint is a real refactor across five modules on a canvas that is
live (54 docs, 100 findings, 54 drift events, 54 queued regens). Mitigations:
- Every migrated surface goes behind a config toggle so the legacy chain can be restored without a revert.
- Phase 3 migrates one surface per task, each independently shippable.
- Phase 5 arms liveness gates so this cannot itself become another declared-but-unconsumed capability.

Constraints honored throughout: no model IDs in code or YAML (route by `llm_function` through
`LLMRouter`); PostgreSQL-authored SQL; worktree-first branch-per-task; new tests gated in the PR
that adds them.

---

## 5. Phases and tasks

Project key `cef`, prefix `cef-`. Verified free in `args/projects.yaml` (164 registered prefixes)
**and** on the live board (0 rows). Epic `gate` is deliberately avoided — it is reserved for manual
hold sentinels and would filter every task out of dispatch forever; CI work uses epic `ci`.

### Phase 0 — Foundations (`cef-fnd-`) — fix the empty substrates first
- `cef-fnd-01` Create `databridge_agent_access_log` via a timestamped migration; port the SQLite DDL
  to PostgreSQL. Make `_audit()` failure loud rather than swallowed. **Without this, external
  fetches cannot be logged — which the egress decision requires.**
- `cef-fnd-02` Create `docmod_nist_pubs` and run `nist_pubs_sync` so the `policy_refs` pack has a substrate.
- `cef-fnd-03` Enable DataBridge: authorize the first connector in `args/databridge_agent_access.yaml`,
  seed its `db_connections` row, verify a real `fetch()` round-trip and its audit row.
- `cef-fnd-04` Domain-agnostic entity-currency store: table + writer. Seed from the 110 EOL + 101
  net-EOL + 19 catalog rows; fix the `ni_devices`=0 root cause or supply an alternative feed so
  `docmod_defacto_standards` stops being empty.
- `cef-fnd-05` Persist `rag_provenance_ledger` rows on retrieval (TRUST invariant; currently 0).

### Phase 1 — Cortex backends (`cef-bck-`)
Each follows the same 5-point registration: adapter fn in `search_service.py` → `CORTEX_BACKENDS`
(`schemas.py:24`) → `BACKEND_ADAPTERS` (`search_service.py:583`) → optional `ROUTE_LABEL_BACKENDS`
(`:628`) → `args/cortex_config.yaml` weights / timeouts / fan-out. Adapters are exception-isolated
and return `BackendResults([], errors=[...])` rather than raising.

- `cef-bck-01` `currency` adapter over catalog + EOL + net-EOL + de-facto + the new entity-currency store.
- `cef-bck-02` `external` adapter over the DataBridge broker, inheriting its full authz chain and
  forcing `fail_closed=True`.
- `cef-bck-03` `sme` advisory adapter over ACE `ensure_sme` + `persona_query` — ACE is action-only
  today, so this is a genuinely new seam. Must prove SME generation works end to end, since
  `ace_ensure_sme` has never successfully produced a role.
- `cef-bck-04` `document_intelligence` domain lens in `args/cortex_config.yaml` — populate `sources:`
  for row-level scoping (only the `security` lens does today).

### Phase 2 — `cortex.resolve()` (`cef-rsv-`)
- `cef-rsv-01` `tools/cortex/resolver.py` + `_governed_facade` registration + `CORTEX_FACADES` entry
  + schemas.
- `cef-rsv-02` **Semantic entity resolution across backends** — the piece that does not exist today
  (cross-source synthesis is currently string concatenation). This is what makes a *conflict*
  detectable and is the core of "reveal unseen data". Plus gap reporting: unknown as a first-class finding.
- `cef-rsv-03` Citation validation and provenance persistence on every resolve, via the existing
  provenance gate (`citation_type="cortex"`).

### Phase 3 — DI migration to the chokepoint (`cef-di-`)
- `cef-di-01` `doc_modernization/scanner.py` + packs → `resolve()`
- `cef-di-02` `tech_writing_assist.research_and_draft()` → `resolve()`; **fix `collection_id` being
  accepted but never passed to retrieval**, which today lets a draft pull chunks from any collection
  in the tenant.
- `cef-di-03` `acoic.py` → `resolve()`
- `cef-di-04` `search_engine.py` → `resolve()`; decide the fate of the filesystem wiki cache that
  currently sits outside both the DB and the vector store.
- `cef-di-05` `doc_generator.py` and the blueprint `generate` routes → `resolve()`
- `cef-di-06` Activate `cross_canvas_context.py` as a resolve source, or delete it.

### Phase 4 — Surfaces (`cef-ui-`)
- `cef-ui-01` DocDrift: verdict + citations + SME opinion + unknowns
- `cef-ui-02` Explorer: conflicts and gaps — the "unseen data" surface
- `cef-ui-03` HITL approve/reject wired to the existing
  `/api/modernization/findings/<id>/resolve` and `/api/review/*` routes

### Phase 5 — Gates and proof (`cef-ci-`)
- `cef-ci-01` `capability_liveness` + `substrate_liveness` entries for the new backends
- `cef-ci-02` Tests added to `args/ci_test_files/core.txt` in their own PR, red-first proof, E2E
  against a second dashboard on a Chrome-safe port

**23 tasks across 6 epics** (fnd 5, bck 4, rsv 3, di 6, ui 3, ci 2).

---

## 6. Deliverables on approval
1. Register the `cef` project card in `args/projects.yaml` (key, name, `task_prefix: cef-`, briefs, 6 epics).
2. Seed the 24 tasks via `tools.kanban.task_factory.create_tasks`, each with a description rich
   enough for a cold session, and `depends_on` set so phases order correctly and siblings cannot
   both build the same module.
3. Begin Phase 0 in a worktree off `origin/main`, one branch and PR per task.
