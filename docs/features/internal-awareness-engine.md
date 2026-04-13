# Internal Awareness Engine — Turnkey Self-Observation + Q&A

**Status:** Plan — in backlog
**Created:** 2026-04-11
**Owner:** TBD
**Related prior work:** OPT-49 (ruff_lint gate), Phase 64 RAG subsystem, Phase 66 coherence_checker, Phase 48 knowledge_graph, Genesis v2.0 Reflex framework, Oracle predictive engine

---

## Problem

ICDEV has grown to ~900 tools, 21 skills, 24 MCP servers, 8 canvas modules, 60+ goals, 80+ dashboard routes, 19 Genesis reflexes, and 391 DB tables. Operator pain:

1. **Silent regressions** — NDC routes worked Monday, broke Thursday, only caught by manual spot-check
2. **No visual map** — operator has no way to see "if I touch X, what else breaks"
3. **No natural-language Q&A against the codebase** — the RAG infrastructure exists but has never been run on the codebase itself (0 rows in `codebase_index`, 123 rows in `rag_chunks` vs. 900 tools)
4. **Knowledge graph is external-facing only** — 1,638 nodes, zero of them represent ICDEV's own components
5. **Component-level alerts don't surface** — dashboard has a "Firing Alerts" tile but the click was broken (fixed separately in this same session) and the underlying data source was CAT1 canvas findings, not component health

## Goal

A **turnkey internal awareness subsystem** that:
- Auto-discovers every component (skill/agent/mcp/canvas/tool/goal/route/reflex)
- Probes their health every **3 hours** (deterministic, no LLM)
- Detects drift vs. rolling baselines
- Scores findings via **Oracle** → auto-creates **Suggested** Kanban cards (confidence ≥ 0.7)
- Exposes a **/components-map** dashboard page with:
  - JointJS force-directed graph (air-gap safe, already vendored)
  - Collapsible tree sidebar
  - Hover tooltips pulling comprehensive descriptions
  - Natural-language Q&A widget that queries **existing RAG + KG** infrastructure simultaneously
- **100% air-gap compatible**, **zero LLM in hot path** (LLM only optional for answer narration)

## Non-goals

- Not rebuilding RAG or KG — hydrating existing systems
- Not introducing new vector stores — reusing `rag_chunks`
- Not adding model dependencies — uses existing router if narration is requested
- Not automating destructive remediation — humans approve every SUGGESTED → BACKLOG promotion

---

## Module Enablement Awareness (REQUIRED — added 2026-04-11)

**User requirement**: "Not all modules will be enabled (see .env). This process must be cognitive about the state of the modules, components, and etc. Only monitor when they are enabled. The context of the chat and KG must reflect what is enable/disabled."

### Enablement flags (read from `.env`)

Observed in the current dev environment:

| Flag | Default (.env.example) | Scope |
|---|---|---|
| `LLM_TWO_TIER_ENABLED` | true | LLM router two-tier dispatch |
| `RAG_ENABLED` | true | RAG subsystem master switch |
| `FINETUNE_ENABLED` | true | Fine-tuning dashboard + pipeline |
| `ICDEV_CUI_BANNER_ENABLED` | true | CUI classification banner rendering |
| `ICDEV_BYOK_ENABLED` | false | Bring-your-own-key mode |
| `ICDEV_IDC_ENABLED` | false | Infrastructure Design Canvas |
| `ICDEV_NDC_ENABLED` | false | Network Design Canvas |
| `ICDEV_SDC_ENABLED` | false | Security Design Canvas |
| `ICDEV_BDC_ENABLED` | false | Boundary Design Canvas |
| `ICDEV_PDC_ENABLED` | false | Pipeline Design Canvas |
| `ICDEV_ODC_ENABLED` | false | Observability Design Canvas |
| `ICDEV_DDC_ENABLED` | false | Data Design Canvas |
| `ICDEV_QDC_ENABLED` | false | Quality Design Canvas |
| `ICDEV_MIGRATION_CANVAS_ENABLED` | false | Migration canvas |
| `ICDEV_CANVAS_KG_ENABLED` | false | Cross-canvas KG overlay |
| `ICDEV_GOVCON_ENABLED` | false | Government contracting module |
| `ICDEV_FILESYNC_ENABLED` | false | File sync dashboard |
| `ICDEV_OPENCLAW_ENABLED` | false | OpenClaw integration |
| `ICDEV_NETWORK_ENABLED` | false | Network intelligence umbrella |
| `ICDEV_PIPELINE_ENABLED` | false | Pipeline umbrella |
| `ICDEV_SECURITY_ENABLED` | false | Security umbrella |
| `ICDEV_INFRA_ENABLED` | false | Infra umbrella |
| `ICDEV_DATA_CANVAS_ENABLED` | false | Data canvas umbrella |
| `ICDEV_BOUNDARY_ENABLED` | false | Boundary umbrella |
| `ICDEV_OBSERVABILITY_ENABLED` | false | Observability umbrella |

Additional flags may be added over time. The enablement reader must be **tolerant of unknown flags** (any flag matching `*_ENABLED` in `.env` is a candidate).

### Enablement reader — shared helper

**New module**: `tools/awareness/enablement.py` (~150 LOC, Phase 1)

- `load_enablement_flags() -> Dict[str, bool]` — parses `.env` once, returns dict of all `*_ENABLED` flags
- `is_component_enabled(component_id: str, properties: dict) -> bool` — given a component node and its metadata, determines whether it should be monitored. Maps component_id / entity_type / source_path to the relevant enablement flag(s) via a declarative map (e.g., `tools/network/*` → `ICDEV_NETWORK_ENABLED` AND `ICDEV_NDC_ENABLED`).
- `enabled_flags_signature() -> str` — returns a stable hash of current flag values so downstream consumers can detect when the configuration changed (triggers re-index).

**Mapping source**: `args/awareness_enablement_map.yaml` (new). Example:

```yaml
# Maps component entity_types + path patterns to the enablement flags that gate them.
# Component is "enabled" iff ALL required flags are true.
mappings:
  # Canvases
  - entity_type: canvas_module
    path_glob: "tools/boundary_canvas/**"
    requires: [ICDEV_BOUNDARY_ENABLED, ICDEV_BDC_ENABLED]
  - entity_type: canvas_module
    path_glob: "tools/network_canvas/**"
    requires: [ICDEV_NETWORK_ENABLED, ICDEV_NDC_ENABLED]
  # ... (9 more canvases)

  # Subsystems
  - entity_type: tool
    path_glob: "tools/rag/**"
    requires: [RAG_ENABLED]
  - entity_type: tool
    path_glob: "tools/govcon/**"
    requires: [ICDEV_GOVCON_ENABLED]
  - entity_type: tool
    path_glob: "tools/finetuning/**"
    requires: [FINETUNE_ENABLED]

  # Default: no flags required (always enabled)
  # Components not matching any mapping are considered always-on.
```

### How each awareness subsystem uses enablement

| Subsystem | Behavior when component is DISABLED |
|---|---|
| **component_indexer** (Phase 1) | Still indexes the node (so operator can see it exists), but tags `properties.enabled=false`. Tree/graph UI renders disabled nodes dimmed. |
| **post-tool hook** (Phase 1) | Edits to disabled module's files DO update the component_indexer entry but do NOT trigger a health probe. |
| **health_prober** (Phase 2) | **Skips** probes against disabled components entirely. `http_head` never hits routes of disabled canvases. `module_import` doesn't try to import disabled modules. |
| **drift_detector** (Phase 2) | Skips drift analysis on disabled components — they can't regress if they aren't running. |
| **gap_detector** (Phase 3) | Still runs gap rules but excludes disabled modules from `route_no_e2e`, `orphan_db_table`, etc. — prevents noise from inactive code. |
| **suggested_card_writer** (Phase 2) | Does not create suggested kanban cards for disabled components. |
| **/components-map** (Phase 1) | Visual dimming: disabled nodes render in gray with hatched border; tree sidebar shows a toggle "Show disabled modules" (off by default). |
| **/api/components-map/ask** (Phase 4) | Filters RAG + graph results to only enabled components by default. Adds a `?include_disabled=true` query param to opt in. Every disabled hit is clearly marked `"enabled": false` in the response. |
| **/ask-icdev** chat (Phase 4.5) | Same filter as above. Response citations include an enablement badge. If a user asks "tell me about X" where X is disabled, the assistant responds with "X is currently disabled in .env via FLAG; its metadata shows..." rather than describing it as active. |
| **Genesis awareness reflex** (Phase 5) | Reads enablement signature at cycle start; if changed since last cycle, triggers a full re-index to refresh `enabled` tags. |

### Reloading when `.env` changes

Two paths:

1. **Post-tool hook detection** (Phase 1.1.7) — if an Edit/Write/NotebookEdit targets `.env`, synchronously call `load_enablement_flags()` and re-tag affected component nodes.
2. **Reflex cycle detection** (Phase 5) — each cycle compares `enabled_flags_signature()` to the last run; if changed, run the full indexer + re-probe.

This ensures the KG and chat context always reflect the live `.env` state without operator action.

## Sequential Execution via `promote_next_phase.py` (REQUIRED — added 2026-04-11)

**Problem discovered 2026-04-11**: When all 6 phases sit in `status='backlog'` with same priority, the kanban listener's serialization guard has a race at `tools/genesis/reflexes/kanban.py:1438` — the stale-entry cleanup can remove a task from `_running` when its DB status changes (e.g., the running Claude CLI POST'd `/move` itself), freeing the guard and allowing the next task to dispatch even if the first subprocess is still alive. Observed twice in one session: Phases 1 and 3 both ended up in_progress simultaneously, with Phase 2 skipped entirely.

**Fix**: keep exactly ONE phase in backlog at any time. Park the others as `status='scheduled'` with `scheduled_at` set to year-2099 dates (the listener's scheduled query only picks up tasks with `scheduled_at <= NOW()`). When a phase completes, the executor calls:

```bash
python tools/awareness/promote_next_phase.py --after <completed_task_id>
```

The promoter finds the next phase (by title `Phase N+1/6`), sets its `status` to `backlog`, clears `scheduled_at`, and bumps `updated_at` to 11 minutes ago (bypassing the 10-minute cooldown so it is immediately eligible). Every phase task description includes this step in its post-completion checklist.

**Tool**: `tools/awareness/promote_next_phase.py` (created 2026-04-11, validated via `--list`). Commands:

- `python tools/awareness/promote_next_phase.py --list` — show all 6 phases with current status
- `python tools/awareness/promote_next_phase.py --after task-<id>` — promote the next phase after the given completed task
- `python tools/awareness/promote_next_phase.py --phase N` — explicit form (promote Phase N+1)

**Execution order guarantee**: With at most one phase in backlog, the listener's promotion query is trivially deterministic — there is only ever one row to choose from. The serialization bug cannot trigger because there is no "next task in priority queue" to race ahead.

## Max-Turns Handling (added 2026-04-11)

**Problem discovered 2026-04-11**: Phase 1's first run exited with `Error: Reached max turns (50)` — the Claude CLI ran out of conversation turns before completing its ~1,100 LOC scope. Subprocess exit was non-zero, the reflex failure path ran, but the task ended up in an inconsistent state.

**Mitigation in each phase task description**:

1. **Commit progress incrementally** — do not wait to commit at the end. Every logical chunk (new module file, completed test class, migration applied) gets its own `git add . && git commit -m "progress: <chunk>"` immediately.
2. **Use targeted file edits over rewrites** — the OPT-49 style of minimal surgical edits is far cheaper in turn budget than wholesale rewrites.
3. **Resume capability** — before starting, check `git log --oneline kanban/<task_id>` to see what was already done in a prior attempt. Continue from where the last commit left off.
4. **Checkpoint every ~20 turns** — write a short progress note to `.tmp/kanban/<task_id>.progress.md` so a resumed executor knows the state.
5. **Split recovery** — if a phase's scope is still too large even with these, the executor can split the remaining work into a follow-up sub-task by creating a new kanban row tagged `parent_task_id=<current_task_id>` and parking it in backlog BEFORE calling `promote_next_phase.py`.

## Constraints (from user sign-off 2026-04-11)

| Constraint | Decision |
|---|---|
| **Library** | JointJS (already in `tools/dashboard/static/vendor/jointjs/joint.js` — air-gap proven by NDC). Cytoscape.js NOT vendored, don't use. |
| **Probe cadence** | **Every 3 hours**, not hourly |
| **LLM in hot path** | **Zero.** Rules deterministic. Optional LLM only for "explain this gap" narration via existing `tools.llm.router.LLMRouter.invoke(function="narrative_generation")` — works with any Scanner-tier model configured in `args/llm_config.yaml` (portable across rollout LLM changes) |
| **Oracle integration** | Approved. Threshold ≥ 0.7 → auto-create `kanban_tasks.status='suggested'` card |
| **Gap rules enabled by default** | All 7 from the plan: route-not-listed, tool-not-in-manifest, skill-references-missing-goal, orphan-db-table, broken-test-reference, route-no-e2e, empty-mcp-server. `stale_code` rule OFF (noisy) |
| **Build mechanism** | Kanban task, let the listener pick up |
| **RAG + KG integration** | **Turnkey** — hydrate existing infra rather than build new |

---

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────┐
 │                  INTERNAL AWARENESS ENGINE                        │
 │                                                                    │
 │  ┌─────────────────┐  ┌──────────────────┐ ┌──────────────────┐  │
 │  │ component_      │  │ health_prober    │ │ drift_detector   │  │
 │  │ indexer         │→ │ (3h cadence)     │→│ (baseline diff)  │  │
 │  │ (static walker) │  │ • route HEAD     │ │ • regression    │  │
 │  │ • .agents/skills│  │ • import check   │ │ • schema drift  │  │
 │  │ • tools/mcp     │  │ • schema query   │ │ • unit-test     │  │
 │  │ • tools/*canvas │  │ • unit-test flag │ │   delta          │  │
 │  │ • goals/        │  │ • api_surface    │ └────────┬─────────┘  │
 │  │ • tools/manifest│  └──────────────────┘          │             │
 │  └────────┬────────┘                                │             │
 │           │                                          │             │
 │           ↓                                          ↓             │
 │  ┌────────────────────────┐           ┌─────────────────────────┐ │
 │  │  kg_nodes / kg_edges   │           │  oracle_predictions     │ │
 │  │  graph_id=icdev-self   │           │  lens=internal_aware    │ │
 │  │  (component structure) │           │  (health issues)        │ │
 │  └────────┬───────────────┘           └───────────┬─────────────┘ │
 │           │                                        │                │
 │           ↓                                        ↓                │
 │  ┌────────────────────────┐           ┌─────────────────────────┐ │
 │  │  rag_chunks            │           │  kanban_tasks           │ │
 │  │  source=components     │           │  status=suggested       │ │
 │  │  (SKILL.md, goals,     │           │  (auto-created on       │ │
 │  │   docstrings, routes)  │           │   confidence ≥ 0.7)     │ │
 │  └────────┬───────────────┘           └─────────────────────────┘ │
 │           │                                                        │
 │           └─────────────┬──────────────────────────────────────────┘
 │                         ↓
 │  ┌────────────────────────────────────────────────────────────┐
 │  │  /components-map Dashboard Page                             │
 │  │                                                              │
 │  │  [Tree Sidebar]  [JointJS Graph]  [Q&A Widget]             │
 │  │   Skills ▸      ╔══════════════╗  ┌──────────────┐         │
 │  │   Agents ▸      ║ ● ─── ● ─── ●║  │ Ask: "why    │         │
 │  │   MCP ▸         ║ │    ╱       ║  │ did NDC      │         │
 │  │   Canvases ▼    ║ ● ─ ●        ║  │ routes break?│         │
 │  │    ▪ security   ╚══════════════╝  │ [Ask]         │         │
 │  │    ▪ boundary   (hover for tip)   └──────┬───────┘         │
 │  │    ▪ network    ↕ syncs to tree           ↓                 │
 │  │   Goals ▸                            RAG + GraphRAG         │
 │  │   Routes ▸                           unified results        │
 │  └────────────────────────────────────────────────────────────┘
 └──────────────────────────────────────────────────────────────────┘
```

---

## Pre-Phase Discovery (executed 2026-04-11, logged here for executor)

Live validation confirmed the turnkey thesis. Key findings:

1. **`codebase_indexer.py` works but has two gotchas**:
   - **Bug**: full-repo `--scan` reports `indexed_files: 7251` but 0 rows land — silent transaction abort mid-loop. Scoped scans work. **Fix**: per-file try/commit or savepoints in `scan_codebase()` (tools/rag/codebase_indexer.py:576).
   - **Limitation**: stores only file metadata (path, symbols list, chunk_count) — not the actual chunk text. So `codebase_index` is a symbol index, NOT a full-text RAG source. Phase 4 must either (a) extend indexer to persist chunks into new `codebase_chunks` table, or (b) register `codebase_index` as a metadata source with `content_cols=['file_path','module','symbols']`.

2. **Incremental scoped scan populated 468 files / 4,616 tracked chunks** across: tools/{rag, knowledge_graph, network, mcp, dashboard/api, genesis, workflow, memory, db, a2a}, .agents/skills, goals, docs/features.

3. **All three retrieval paths verified live on :5050**:
   - `SELECT FROM codebase_index WHERE file_path LIKE ...` → works (SQL, deterministic)
   - `POST /api/rag/search` → works, returns scored ndc_designs hits from existing 123 rag_chunks
   - `POST /api/knowledge-graph/search` → works, returns nodes + edges with auto-profile detection

4. **Hook infrastructure is 80% ready**: `.claude/hooks/post_tool_use.py` already fires on every tool call, already dispatches to `extension_manager` Phase 44 extension points. Phase 1 must add an awareness subscriber.

## Phase 1 — SPLIT INTO 7 SUB-PHASES (updated 2026-04-11)

**Update 2026-04-11**: The original monolithic Phase 1 (~1,100 LOC) was attempted 3 times by the kanban listener. Every attempt hit the Claude CLI 50-turn limit BEFORE reaching the first commit checkpoint, producing zero artifacts on each run. Root cause: one Claude CLI invocation cannot complete 1,100 LOC of work — 50 turns × ~22 LOC/turn is theoretical max with zero slack. Empirically impossible.

**Fix**: Phase 1 is now split into 7 self-contained sub-phases, each sized to fit comfortably within the 50-turn budget. Each sub-phase produces a merge-able artifact and chains to the next via `promote_next_phase.py`.

| Sub-phase | Title | LOC | Kanban task ID |
|---|---|---|---|
| **1a** | Fix codebase_indexer transaction abort bug + resilience test | ~80 | `task-38c9063678` |
| **1b** | Register codebase_index in SOURCE_REGISTRY + run ingestion smoke test | ~30 | `task-0398a4787a` |
| **1c** | tools/awareness module skeleton + enablement helper + map config | ~200 | `task-021e5b8040` |
| **1d** | component_indexer.py — static component scanner producing kg_nodes/kg_edges | ~450 | `task-cd404f65c0` |
| **1e** | Post-tool hook subscriber — auto-reindex on every Edit/Write | ~100 | `task-e0f6bd1d80` |
| **1f** | /components-map dashboard page — JointJS tree + graph + hover + detail drawer | ~400 | `task-16f89a1b9d` |
| **1g** | E2E Selenium test + manifest/docs/start registration + companion sync + impl doc | ~300 | `task-1ca7cb9ffc` |

**Total**: ~1,560 LOC across 7 tasks (slightly more than the original 1,100 due to per-sub-phase test + doc overhead, but individually much safer).

**Chain**: `1a → 1b → 1c → 1d → 1e → 1f → 1g → 2 → 3 → 4 → 5 → 6`. Each sub-phase calls `python tools/awareness/promote_next_phase.py --after <task_id>` as its final step.

The promoter's `_phase_sort_key()` handles both plain phases (`Phase N/6`) and sub-phases (`Phase Na/6`, `Phase Nb/6`, ...) via regex `Phase (\\d)([a-z])?/6`. Sub-phase split works for any future phase too (e.g., Phase 4 could be split into 4a..4f later without changing the promoter).

### Original monolithic Phase 1 content (kept for reference below)

The sections below describe the same work that is now split into 1a..1g. The sub-phase kanban task descriptions contain their own focused scope; this section remains as the unified plan.

## Phase 1 (monolithic reference) — Component Indexer + Visual Map + Hook Integration (~1,100 LOC)

Goal: give operator the visual map immediately, before any runtime probing.

### 1.1 — `tools/awareness/component_indexer.py` (~400 LOC, new)

Deterministic filesystem walker, pure stdlib (no LLM, no network). Produces typed nodes and edges into `kg_nodes` and `kg_edges` under a new graph `kg-icdev-self-awareness`.

**Node types extracted:**

| Type | Source | Per-node metadata |
|---|---|---|
| `skill` | `.agents/skills/*/SKILL.md` frontmatter | name, description, model, allowed-tools, file_path |
| `mcp_server` | `tools/mcp/*_server.py` — class docstring + `tool_registry.py` | class_name, tool_count, tool_names[], port, file_path |
| `mcp_tool` | `tools/mcp/tool_registry.py` | tool_id, description, handler_module |
| `canvas_module` | `tools/{boundary,data,infra,migration,network,observability,pipeline,qdc,security,devops}_canvas/` + `tools/canvas/` | domain, agent_class, db_tables[], blueprint_routes[], file_count, LOC |
| `a2a_agent` | `tools/a2a/agent_registry.py` DB + `tools/a2a/*.py` | name, port, skills[], tier (core/domain/support) |
| `goal` | `goals/*.md` frontmatter + `goals/manifest.md` row | title, description, phase, tools_referenced[] |
| `tool` | `tools/manifest.md` table rows parsed with regex | name, category, file, description, input, output |
| `tool_category` | `tools/manifest.md` section headers | name, section_index |
| `reflex` | `tools/genesis/reflexes/*.py` docstring + imports | name, docstring_summary, trust_tier, db_tables_used[] |
| `dashboard_route` | Flask `app.url_map.iter_rules()` at import time | rule, methods, endpoint, template, blueprint, last_seen |
| `db_table` | `tools/db/init_icdev_db.py` + all `*/db/init_db.py` | name, ddl_source, column_count, is_append_only |
| `goal_workflow` | `goals/manifest.md` → goals that reference other goals | — |

**Edges (relationships):**

| Edge type | From → To | Derivation rule |
|---|---|---|
| `executes` | skill → goal | SKILL.md YAML references `goals/xxx.md` |
| `uses_tool` | skill → mcp_server / mcp_tool | SKILL.md `tools:` list |
| `exposes` | mcp_server → mcp_tool | `tool_registry.py` mapping |
| `registers_blueprint` | canvas_module → dashboard_route | `blueprint.py` `@bp.route` grep per module |
| `owns_table` | canvas_module → db_table | `db/init_db.py` `CREATE TABLE` in-module match |
| `referenced_by_goal` | tool → goal | goal markdown body grep for `tools/...` path |
| `implemented_by` | goal → tool | same as above (reverse) |
| `in_category` | tool → tool_category | manifest section header proximity |
| `has_agent` | canvas_module → a2a_agent | filesystem + registry join |
| `wires_route` | canvas_module → dashboard_route | matching blueprint prefix |
| `reads_table` | reflex → db_table | AST import + SELECT/INSERT grep in reflex module |
| `coherence_violation` | tool → coherence_rule | optional — link to existing `coherence_checker` results |

**Persistence (per-run, idempotent):**

```python
# Pseudo:
graph_id = "kg-icdev-self-awareness"
upsert kg_graphs (graph_id, name="ICDEV Self-Awareness", entity_count=0, edge_count=0)
for node in discovered_nodes:
    upsert kg_nodes (graph_id, entity_type, label, properties_json)
for edge in discovered_edges:
    upsert kg_edges (graph_id, source_node, target_node, relationship_type, weight)
# Update graph counters
update kg_graphs SET entity_count, edge_count, updated_at
# Write audit row
insert awareness_run_log (run_id, phase='index', nodes_added, edges_added, elapsed_ms)
```

**CLI:**
```bash
python tools/awareness/component_indexer.py --scan --json
python tools/awareness/component_indexer.py --scan --scope tools/network --json
python tools/awareness/component_indexer.py --scan --dry-run --json  # show diff vs last run
```

### 1.1.5 — Fix codebase_indexer transaction abort bug (~30 LOC patch)

In `tools/rag/codebase_indexer.py` `scan_codebase()` (line 576):

- Wrap each per-file block (file hash check + `_store_record` call) in its own try/except
- On exception: log, increment `errors`, call `conn.rollback()` to clear the aborted transaction state, continue to next file
- Commit in batches of 100 files rather than one final commit at end
- Alternative: use Postgres SAVEPOINTs around each upsert
- Add a unit test that feeds a synthetic bad file (raises during `index_python_file`) and asserts the next good file still persists

This is a prerequisite — without it, the full-repo scan the awareness engine depends on silently drops all rows.

### 1.1.6 — Decide codebase chunk persistence strategy (design call)

Two options, executor picks:

**Option X — Extend `codebase_indexer` to persist chunks**
- Add new table `codebase_chunks` (chunk_id, file_id FK, symbol_type, symbol_name, content, line_start, line_end, embedding_ref)
- `_store_record` also upserts each chunk
- Register `codebase_chunks` in SOURCE_REGISTRY
- Run `ingestion_manager` to copy to `rag_chunks` with embeddings
- Pros: proper full-text RAG; Cons: more disk, more upserts per file

**Option Y — Register `codebase_index` as shallow metadata source**
- Add entry to SOURCE_REGISTRY: `content_cols=['file_path', 'module', 'symbols']`
- Queries hit file paths + symbol names — "where is X defined" works, "show me the docstring of Y" doesn't
- Pros: zero new tables, fastest to ship; Cons: can't retrieve function bodies

**Recommended**: ship **Y** first (Phase 4.1 — 10 LOC of YAML-ish config change), then build **X** as Phase 4.6 if symbol-name retrieval proves insufficient. Y covers 80% of "where is this component defined" queries.

### 1.1.7 — Post-tool hook integration — `tools/awareness/hooks.py` (~80 LOC)

New module implementing a Phase 44 `TOOL_EXECUTE_AFTER` subscriber:

```python
from tools.extensions.extension_manager import extension_manager, ExtensionPoint

def on_tool_execute_after(context):
    tool_name = context.data.get('tool_name', '')
    if tool_name not in ('Edit', 'Write', 'NotebookEdit'):
        return
    # Recover file_path from tool_input (Edit/Write have file_path; NotebookEdit has notebook_path)
    file_path = _extract_file_path(context.data)
    if not file_path or not _is_tracked_extension(file_path):
        return
    try:
        _upsert_single_file(file_path)   # component_indexer single-file path
        _upsert_codebase_index(file_path) # codebase_indexer single-file path
        _write_health_snapshot(file_path, probe_type='code_change')
    except Exception as exc:
        _log_hook_failure(file_path, exc)  # never propagate

extension_manager.subscribe(ExtensionPoint.TOOL_EXECUTE_AFTER, on_tool_execute_after)
```

**Wiring**: import `tools.awareness.hooks` at process startup (via `post_tool_use.py` top-level import) so the subscriber registers once per Python interpreter.

**Performance target**: ≤ 30 ms per hook call on a typical Python file. Never blocks tool execution.

**Tracked extensions**: `.py`, `.md`, `.html`, `.jinja2`, `.j2`, `.yaml`, `.yml`, `.json` (scoped to project tree).

**Test**: `tests/test_awareness_hooks.py` simulates a post_tool_use event with a synthetic Edit on a .py file and asserts kg_nodes, codebase_index, and awareness_component_health all updated within 100 ms.

### 1.2 — New dashboard page `/components-map` (~300 LOC HTML + 200 LOC Flask)

Route: `@app.route("/components-map")` in `tools/dashboard/app.py`.

**Three-pane layout:**

| Pane | Width | Content |
|---|---|---|
| Left: Tree sidebar | 20% | Collapsible `<details>` hierarchy: root → category → component. Click = filter graph to that subtree. Plain CSS, no JS library. |
| Center: JointJS graph | 60% | Force-directed layout by default; tree layout toggle. Node color = entity_type. Edge style = relationship. Hover tooltip from `kg_nodes.properties.description`. |
| Right: Detail drawer | 20% | Shows selected node: type, description, file_path, relationships count, last_indexed_at, health status. Links to source file and relevant dashboard pages. |

**APIs (Flask):**
```
GET  /api/components-map/tree                    — hierarchical JSON
GET  /api/components-map/graph?scope=skills      — JointJS-compatible {cells: [...]}
GET  /api/components-map/node/<node_id>          — full node detail
GET  /api/components-map/neighbors/<node_id>     — subgraph for hover expansion
POST /api/components-map/ask                     — natural-language Q&A (Phase 4 — placeholder in Phase 1)
```

**JointJS setup:**
- Import: `<script src="/static/vendor/jointjs/joint.js"></script>`
- Create `joint.dia.Graph` + `joint.dia.Paper`
- Node shapes: `joint.shapes.standard.Rectangle` per entity_type with color legend
- Hover: `paper.on('element:mouseenter', ...)` → position tooltip div, content from AJAX `/node/<id>`
- Click: `paper.on('element:pointerclick', ...)` → update right drawer + highlight edges

**Data source:** reads from `kg_nodes`, `kg_edges` WHERE `graph_id = 'kg-icdev-self-awareness'` (plus joined `awareness_component_health` for health badges).

---

## Phase 2 — Health Prober + Drift Detector + Oracle → Suggested (~1,200 LOC)

Goal: automatic regression detection, NDC-routes-style.

### 2.1 — `tools/awareness/health_prober.py` (~400 LOC, new)

Runs deterministic probes every 3 hours. Writes to new `awareness_component_health` table.

**Probes:**

| Probe | Target | Result recorded | Cadence |
|---|---|---|---|
| `http_head` | Every Flask route in `app.url_map` | status_code, response_time_ms | 3h |
| `module_import` | Every Python module in `tools/` | import_success, error_message | 3h |
| `db_table_present` | Every table in `component_indexer` output | exists, row_count | 3h |
| `test_green` | Latest pytest run for each touched module | passed, failed, skipped | post-test-run (hook) |
| `api_surface_match` | `api_surface_extractor.py` output vs. last snapshot | additions[], removals[], breaking[] | 3h |
| `coherence_status` | `coherence_checker.py --json` per check | status, hits[] | 3h |

**Probe implementation:**
- `http_head`: `urllib.request` HEAD with 5s timeout. Flask routes reachable at `localhost:5050`.
- `module_import`: subprocess `python -c "import tools.xxx"` per module; non-zero = import broken.
- Write `awareness_component_health` row per probe: `(component_id, probe_type, status, details_json, sampled_at)`

**CLI:**
```bash
python tools/awareness/health_prober.py --run-all --json
python tools/awareness/health_prober.py --probe http_head --json
python tools/awareness/health_prober.py --schedule 3h  # enable scheduler loop (dry: just prints plan)
```

### 2.2 — `tools/awareness/drift_detector.py` (~350 LOC, new)

Reads `awareness_component_health` snapshots, computes rolling 7-day baseline per probe, emits drift findings.

**Drift rules (deterministic):**

| Rule | Signal | Confidence |
|---|---|---|
| `route_regression` | HTTP status flipped 2xx → 4xx/5xx for ≥ 2 consecutive cycles; previously stable ≥ 3 days | 0.85 |
| `module_import_broken` | `import_success` flipped True → False | 0.95 |
| `db_table_missing` | Table existed 3 days ago, now absent | 0.90 |
| `api_surface_breaking` | Public function/class removed vs. last snapshot | 0.80 |
| `test_regression` | Previously green test now red for ≥ 2 cycles | 0.75 |
| `coherence_new_fail` | New failing check_id that was passing 3 days ago | 0.80 |

Each finding → writes row to `oracle_predictions` with `lens_name='internal_awareness'` and the confidence.

### 2.3 — Oracle lens + Kanban Suggested writer (~150 LOC, additions to existing)

**Two tiny extensions:**

1. Register a new lens in Oracle's lens registry: `internal_awareness` (if registry exists — otherwise bypass and write directly).
2. New helper `tools/awareness/suggested_card_writer.py`:
   - Reads `oracle_predictions WHERE lens_name='internal_awareness' AND status='new' AND confidence >= 0.7`
   - For each: INSERT into `kanban_tasks` with `status='suggested'`, `source_prediction_id=<oracle_row_id>`, pre-filled title and description. Uses valid task_type enum.
   - Marks the oracle prediction as `status='promoted'` so it's not re-written on next run.

**Example auto-generated card:**
```
Title: NDC route regression: /network/api/topologies/<id>
Type: fix
Priority: high  (from severity mapping)
Status: suggested
Description:
  Route `/network/api/topologies/<id>` regressed from 200 (stable 7 days)
  to 500 sometime between 2026-04-10 02:00 UTC and 03:00 UTC.
  Baseline response time was 45ms; current probes fail at connection level.

  Suggested investigation:
  - Commits to tools/network/blueprint.py in that window: [commit-abc, commit-def]
  - Related modules: tools/network/routes/projects.py (import chain)
  - Last green probe: 2026-04-10T02:00:00Z (sample 168)
  - Oracle confidence: 0.85

  [Evidence pulled from: awareness_component_health run 2026-04-10 03:00 UTC]
```

### 2.4 — DB migration `013_awareness_schema` (new)

```sql
-- Append-only health snapshot log
CREATE TABLE awareness_component_health (
    id              SERIAL PRIMARY KEY,
    component_id    TEXT NOT NULL,        -- FK kg_nodes.id
    probe_type      TEXT NOT NULL,        -- http_head|module_import|db_table_present|test_green|api_surface|coherence
    status          TEXT NOT NULL,        -- pass|fail|warn|error
    details_json    JSONB NOT NULL,       -- probe-specific fields
    sampled_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id          TEXT NOT NULL         -- groups probes from same cycle
);
CREATE INDEX idx_ach_component ON awareness_component_health (component_id, sampled_at DESC);
CREATE INDEX idx_ach_run ON awareness_component_health (run_id);
CREATE INDEX idx_ach_status ON awareness_component_health (status) WHERE status IN ('fail','error');

-- Append-only run log
CREATE TABLE awareness_run_log (
    run_id          TEXT PRIMARY KEY,
    phase           TEXT NOT NULL,        -- index|probe|drift|gap|suggest
    started_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,
    status          TEXT NOT NULL,        -- running|success|error
    nodes_added     INTEGER DEFAULT 0,
    nodes_updated   INTEGER DEFAULT 0,
    edges_added     INTEGER DEFAULT 0,
    probes_ok       INTEGER DEFAULT 0,
    probes_fail     INTEGER DEFAULT 0,
    drift_found     INTEGER DEFAULT 0,
    suggestions     INTEGER DEFAULT 0,
    elapsed_ms      INTEGER,
    details_json    JSONB
);

-- Derived relationships (discovered over time by observing runs)
CREATE TABLE awareness_learned_edges (
    id              SERIAL PRIMARY KEY,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    relation_type   TEXT NOT NULL,
    evidence_count  INTEGER DEFAULT 1,    -- how many observations
    confidence      REAL NOT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL,
    last_seen_at    TIMESTAMPTZ NOT NULL,
    UNIQUE (source_node_id, target_node_id, relation_type)
);

-- Oracle lens registration (if lens table exists)
-- INSERT INTO oracle_lenses (name, description, enabled) VALUES ('internal_awareness', '...', TRUE);
```

Add `awareness_component_health` and `awareness_run_log` to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`. Add to `tests/conftest.py` MINIMAL_ICDEV_SCHEMA.

---

## Phase 3 — Gap Detector (~300 LOC)

### 3.1 — `tools/awareness/gap_detector.py` (new)

Reads `kg_nodes` (component graph) + filesystem + `tools/manifest.md` + `.claude/commands/start.md` → emits gap findings via `oracle_predictions`.

**Gap rules (all enabled by default per user sign-off):**

| Rule ID | Description | Confidence |
|---|---|---|
| `route_not_listed` | Flask route exists but not in `.claude/commands/start.md` Pages: line | 0.9 |
| `tool_not_in_manifest` | Python tool file exists in `tools/` but no row in `tools/manifest.md` (reuses `coherence_checker.check_manifest`) | 0.95 |
| `skill_references_missing_goal` | SKILL.md references `goals/xxx.md` that doesn't exist | 0.95 |
| `orphan_db_table` | DB table referenced in INSERT/SELECT but no `CREATE TABLE` in any migration | 0.85 |
| `broken_test_reference` | Test file imports a function that no longer exists (AST-based) | 0.90 |
| `route_no_e2e` | Dashboard route not covered by any `tests/e2e_*.py` or `tools/testing/e2e_*.py` | 0.70 |
| `empty_mcp_server` | MCP server module registers zero tools with tool_registry | 0.85 |

**Rule `stale_code` DEFAULT-OFF** per user decision (noisy).

Each gap → row in `oracle_predictions` → promoted to `kanban_tasks.suggested` if confidence ≥ 0.7. Same writer as Phase 2.

---

## Phase 4 — RAG + KG Turnkey Integration (~600 LOC)

This is the **highest-leverage phase** — we are not building new RAG or KG infrastructure, we are hydrating existing infrastructure that is 90% complete.

### 4.1 — Run the existing codebase indexer

`tools/rag/codebase_indexer.py` already exists but `codebase_index` table has 0 rows. First action:

```bash
python tools/rag/codebase_indexer.py --scan --json
python tools/rag/codebase_indexer.py --background --interval 30  # optional for continuous
```

This populates `codebase_index` with AST-parsed Python symbols, HTML templates, markdown docs, and jinja2 templates across all of `tools/`, `goals/`, `.agents/skills/`, `docs/`.

### 4.2 — Register internal component sources in `SOURCE_REGISTRY` (~150 LOC)

Add entries to `tools/rag/source_registry.py`:

```python
# --- Internal Awareness (NEW — Phase 4.2) ---
"icdev_skills": {
    "table": "kg_nodes",
    "db": "icdev",
    "pk": "id",
    "content_cols": ["label", "properties.description", "properties.allowed_tools"],
    "metadata_cols": ["graph_id", "entity_type", "updated_at"],
    "priority": 1,
    "mode": "batch",
    "description": "ICDEV skills (Claude Code commands) — from kg-icdev-self-awareness",
    "where_clause": "graph_id='kg-icdev-self-awareness' AND entity_type='skill'",
},
"icdev_mcp_servers": { ... entity_type='mcp_server' ... },
"icdev_canvas_modules": { ... entity_type='canvas_module' ... },
"icdev_goals": { ... entity_type='goal' ... },
"icdev_tools": { ... entity_type='tool' ... },
"icdev_a2a_agents": { ... entity_type='a2a_agent' ... },
"icdev_dashboard_routes": { ... entity_type='dashboard_route' ... },
"icdev_reflexes": { ... entity_type='reflex' ... },
"icdev_db_tables": { ... entity_type='db_table' ... },
"icdev_codebase": {  # Bridges codebase_index into rag_chunks
    "table": "codebase_index",
    "db": "icdev",
    "pk": "id",
    "content_cols": ["symbol_name", "docstring", "content"],
    "metadata_cols": ["file_path", "file_type", "symbol_type", "line_start", "line_end"],
    "priority": 2,
    "mode": "batch",
    "description": "ICDEV codebase (AST-parsed Python + HTML/md/jinja)",
},
```

This requires extending `source_registry.py` schema to support `where_clause` (may already — check). And extending `ingestion_manager.py` to honor `where_clause` during SELECT.

**After registration, run ingestion:**
```bash
python tools/rag/ingestion_manager.py --source icdev_skills --batch
python tools/rag/ingestion_manager.py --source icdev_mcp_servers --batch
python tools/rag/ingestion_manager.py --source icdev_canvas_modules --batch
# ... etc for all 10 new sources
python tools/rag/ingestion_manager.py --source icdev_codebase --batch
```

Expected outcome: `rag_chunks` populated with thousands of internal ICDEV chunks, searchable via existing `/api/rag/search`.

### 4.3 — Wire component graph into `kg_edges` with scoring profile (~50 LOC)

Add `internal_awareness` profile to `SCORING_PROFILES` in `tools/knowledge_graph/graph_rag.py`:

```python
"internal_awareness": {
    "edge_weight": 0.4,    # edge type matters (executes, uses_tool, owns_table)
    "centrality": 0.4,     # hub components (canvases, goals) score higher
    "recency": 0.2,        # recent health snapshots weigh less than structural edges
},
```

Add profile keywords so `/api/knowledge-graph/search?q="why did NDC routes break"` auto-routes to this profile:
```python
"internal_awareness": [
    "icdev", "component", "skill", "mcp", "canvas", "goal", "tool",
    "route", "reflex", "break", "regress", "dependency", "imports",
],
```

### 4.4 — Unified Q&A endpoint (~200 LOC)

New route `POST /api/components-map/ask` that:

1. Takes `{query: "why did NDC routes break?"}`
2. **In parallel** (`ThreadPoolExecutor`):
   - Calls `tools.rag.retriever.RAGRetriever.search(query, top_k=10)` — text context
   - Calls `tools.knowledge_graph.graph_rag.retrieve(query, profile='internal_awareness', top_k=10)` — relationship context
3. Merges results into unified response:
   ```json
   {
     "query": "...",
     "rag_hits": [{"source_type": "icdev_skills", "content": "...", "score": 0.87, "metadata": {...}}],
     "graph_hits": [{"node_id": "...", "label": "NDC Canvas", "entity_type": "canvas_module", "score": 0.91, "neighbors": [...]}],
     "health_hits": [{"component_id": "...", "probe_type": "http_head", "status": "fail", "sampled_at": "...", "evidence": "..."}],
     "suggested_next_actions": [
       {"kanban_task_id": "task-xxx", "title": "NDC route regression", "confidence": 0.85}
     ]
   }
   ```
4. **Optional narration** (off by default):
   - If `?narrate=true`, call `LLMRouter.invoke(function="narrative_generation", prompt=...)` to synthesize a human answer from the merged evidence
   - If LLM unavailable → return raw evidence only, still usable
   - Router-based = portable across whatever LLM is configured at rollout

### 4.5.5 — Dedicated ICDEV Q&A chat page `/ask-icdev` (~400 LOC)

**User requirement (2026-04-11):** "Make sure I have a place and ability to chat and ask questions about ICDEV in the new dashboard."

The existing `/chat` route is requirements-intake specific. Need a dedicated page for codebase/component Q&A with conversation history.

**New route**: `GET /ask-icdev` → `tools/dashboard/templates/ask_icdev.html`

**New DB table**: `icdev_qa_sessions` (session_id, user_id, title, created_at, updated_at), `icdev_qa_messages` (session_id, turn, role, content, citations_json, created_at). Append-only messages, sessions mutable for title rename.

**New APIs**:
- `GET  /api/ask-icdev/sessions` — list prior Q&A sessions
- `POST /api/ask-icdev/sessions` — create session
- `GET  /api/ask-icdev/sessions/<id>` — load session + messages
- `POST /api/ask-icdev/sessions/<id>/message` — post new user question, returns assistant response (calls existing `/api/components-map/ask` internally, persists both turns, attaches citations to message row)
- `DELETE /api/ask-icdev/sessions/<id>` — delete session

**UI** (three-pane):
- Left: session list (collapsible, searchable)
- Center: chat transcript with user/assistant bubbles, markdown rendering via marked.js (vendored)
- Right: citation sidebar — shows RAG sources, KG nodes, health hits for the latest assistant message, each clickable to open the source file or the /components-map graph

**Accessibility**:
- Add "Ask ICDEV" link to `tools/dashboard/templates/base.html` nav
- Add "Ask ICDEV" button to `/components-map` header that pre-fills a question
- Add "Ask About This Component" link on each node's detail drawer in `/components-map`

**Narration toggle** (off by default): same as /components-map/ask — opt-in via request body `narrate=true`.

### 4.6 — Q&A widget in `/components-map` (~200 LOC HTML/JS)

Right-pane Q&A box in `components_map.html`:
- Text input + "Ask" button
- Shows RAG hits with source type + snippet + score
- Shows graph hits as clickable nodes (clicking focuses graph on that node)
- Shows health hits as red badges (clickable → drift timeline)
- Shows suggested actions as Kanban cards (clickable → task detail)
- Narration toggle (off by default; if on, calls `?narrate=true`)

**This becomes the turnkey solution** — one page, one query, visual + textual + structured, all from hydrated existing infrastructure.

---

## Phase 5 — Genesis Reflex + Scheduling (~200 LOC)

### 5.1 — `tools/genesis/reflexes/awareness.py` (new reflex)

Follows existing reflex pattern (`audit.py`, `heal.py` shape). Runs on schedule:

```python
def run(config, trust):
    run_id = uuid4()
    _log_phase(run_id, 'index', start=now())
    component_indexer.scan(incremental=True)
    _log_phase(run_id, 'index', done=now())

    _log_phase(run_id, 'probe', start=now())
    health_prober.run_all()
    _log_phase(run_id, 'probe', done=now())

    _log_phase(run_id, 'drift', start=now())
    drift = drift_detector.detect()
    _log_phase(run_id, 'drift', done=now(), count=len(drift))

    _log_phase(run_id, 'gap', start=now())
    gaps = gap_detector.scan()
    _log_phase(run_id, 'gap', done=now(), count=len(gaps))

    _log_phase(run_id, 'suggest', start=now())
    cards = suggested_card_writer.promote(confidence_threshold=0.7)
    _log_phase(run_id, 'suggest', done=now(), count=len(cards))

    return {"run_id": run_id, "drift": len(drift), "gaps": len(gaps), "cards": len(cards)}
```

**Cadence**: 3 hours (per user decision).

### 5.2 — `args/awareness_config.yaml` (new)

```yaml
schedule:
  interval_hours: 3
  cooldown_minutes: 60

probes:
  http_head:
    enabled: true
    timeout_seconds: 5
    concurrency: 10
  module_import: { enabled: true }
  db_table_present: { enabled: true }
  api_surface_match: { enabled: true }
  test_green: { enabled: false }  # requires hook integration, Phase 5.3
  coherence_status: { enabled: true }

drift:
  consecutive_fails_for_regression: 2
  baseline_window_days: 7

gaps:
  route_not_listed: { enabled: true, confidence: 0.9 }
  tool_not_in_manifest: { enabled: true, confidence: 0.95 }
  skill_references_missing_goal: { enabled: true, confidence: 0.95 }
  orphan_db_table: { enabled: true, confidence: 0.85 }
  broken_test_reference: { enabled: true, confidence: 0.90 }
  route_no_e2e: { enabled: true, confidence: 0.70 }
  empty_mcp_server: { enabled: true, confidence: 0.85 }
  stale_code: { enabled: false }  # DEFAULT OFF — noisy

oracle:
  lens_name: internal_awareness
  promotion_threshold: 0.7

narrative:
  enabled: false  # narration is opt-in per query
  function: narrative_generation  # routes to scanner-tier via args/llm_config.yaml
```

---

## LLM Rollout Portability (CRITICAL — per repeated user constraint)

**Zero LLM in the hot path:**
- Component indexing: filesystem + AST + regex only
- Health probing: `urllib.request`, `subprocess`, SQL
- Drift detection: numeric comparison of snapshots
- Gap detection: static analysis rules
- Kanban promotion: deterministic SQL INSERT

**LLM only optional, only for narration:**
- `/api/components-map/ask?narrate=true` calls `LLMRouter.invoke(function="narrative_generation")`
- Function name is generic ("narrative_generation") — configured in `args/llm_config.yaml` Scanner tier
- At rollout, whatever local model is available gets routed there
- **No hardcoded model IDs, no model-specific prompt formatting**
- Graceful fallback: if LLM unavailable, Q&A returns raw evidence (RAG + graph + health) without narration — still fully usable

**Verification criterion**: swap `args/llm_config.yaml` Scanner tier to a non-qwen model and re-run all awareness CLIs and the `/components-map` page — everything still works.

---

## File-by-File Work Summary

| File | Phase | LOC | Type |
|---|---|---|---|
| `tools/awareness/__init__.py` | 1 | 5 | NEW |
| `tools/awareness/component_indexer.py` | 1 | 400 | NEW |
| `tools/awareness/health_prober.py` | 2 | 400 | NEW |
| `tools/awareness/drift_detector.py` | 2 | 350 | NEW |
| `tools/awareness/suggested_card_writer.py` | 2 | 150 | NEW |
| `tools/awareness/gap_detector.py` | 3 | 300 | NEW |
| `tools/genesis/reflexes/awareness.py` | 5 | 150 | NEW |
| `tools/db/migrations/013_awareness_schema/up.py` | 2 | 120 | NEW |
| `tools/db/migrations/013_awareness_schema/down.py` | 2 | 40 | NEW |
| `tools/db/migrations/013_awareness_schema/meta.json` | 2 | 10 | NEW |
| `args/awareness_config.yaml` | 5 | 60 | NEW |
| `tools/rag/source_registry.py` | 4 | +150 | EXTEND |
| `tools/rag/ingestion_manager.py` | 4 | +50 | EXTEND (where_clause support if missing) |
| `tools/knowledge_graph/graph_rag.py` | 4 | +50 | EXTEND (new scoring profile) |
| `tools/dashboard/app.py` | 1+4 | +300 | EXTEND (new routes) |
| `tools/dashboard/templates/components_map.html` | 1 | 400 | NEW |
| `tools/dashboard/templates/_components_qa_widget.html` | 4 | 100 | NEW partial |
| `.claude/hooks/pre_tool_use.py` | 2 | +3 | EXTEND (APPEND_ONLY_TABLES) |
| `tests/conftest.py` | 2 | +60 | EXTEND (MINIMAL_ICDEV_SCHEMA) |
| `tests/test_awareness_component_indexer.py` | 1 | 200 | NEW |
| `tests/test_awareness_health_prober.py` | 2 | 180 | NEW |
| `tests/test_awareness_drift_detector.py` | 2 | 180 | NEW |
| `tests/test_awareness_gap_detector.py` | 3 | 150 | NEW |
| `tests/test_awareness_oracle_writer.py` | 2 | 120 | NEW |
| `tests/e2e_components_map.py` | 1 | 250 | NEW |
| `tools/manifest.md` | 1 | +8 | EXTEND |
| `.claude/commands/start.md` | 1 | +1 | EXTEND (Pages: add /components-map) |
| `docs/features/internal-awareness-engine-impl.md` | 5 | 300 | NEW (post-impl) |

**Total new LOC: ~3,500 across 18 new files + 8 extensions.**

---

## Implementation Checklist (in executor order)

### Phase 1 — Visual Map (~900 LOC)
- [ ] Create `tools/awareness/__init__.py`
- [ ] Build `component_indexer.py` — parsers for each node type
- [ ] Unit tests `test_awareness_component_indexer.py` — one class per parser
- [ ] Run `component_indexer.py --scan` against dev repo, verify kg_nodes/kg_edges populated
- [ ] Add `kg-icdev-self-awareness` entry to kg_graphs
- [ ] Add `/components-map` route to `tools/dashboard/app.py`
- [ ] Build `components_map.html` with three-pane layout, JointJS graph
- [ ] APIs: `/api/components-map/tree`, `/graph`, `/node/<id>`, `/neighbors/<id>`
- [ ] E2E test `e2e_components_map.py` — Selenium loads page, verifies tree renders, graph has ≥ 100 nodes, hover shows tooltip
- [ ] Update `tools/manifest.md` and `.claude/commands/start.md` Pages: line

### Phase 2 — Health + Drift + Oracle (~1,200 LOC)
- [ ] Migration `013_awareness_schema/up.py` + down.py + meta.json
- [ ] Apply migration: `python tools/db/init_icdev_db.py`
- [ ] Add tables to `APPEND_ONLY_TABLES` and conftest MINIMAL_ICDEV_SCHEMA
- [ ] Build `health_prober.py` with 6 probe types
- [ ] Unit tests `test_awareness_health_prober.py` — mock targets for each probe
- [ ] Run `health_prober.py --run-all` and verify snapshots written
- [ ] Build `drift_detector.py` with 6 drift rules
- [ ] Unit tests `test_awareness_drift_detector.py` — synthetic snapshot baselines
- [ ] Build `suggested_card_writer.py`
- [ ] Unit tests `test_awareness_oracle_writer.py` — mock oracle_predictions rows → kanban_tasks
- [ ] Integration test: full pipeline (probe → drift → card) with synthetic regression

### Phase 3 — Gap Detector (~300 LOC)
- [ ] Build `gap_detector.py` with 7 enabled rules (+ stale_code default-off)
- [ ] Unit tests `test_awareness_gap_detector.py` — one test per rule
- [ ] Integration: gap_detector → oracle_predictions → kanban_tasks.suggested

### Phase 4 — RAG + KG Turnkey (~600 LOC)
- [ ] Run existing `tools/rag/codebase_indexer.py --scan --json` (zero new code — just run it)
- [ ] Verify `codebase_index` table populated (expect thousands of rows)
- [ ] Extend `tools/rag/source_registry.py` with 10 new `icdev_*` sources
- [ ] Add `where_clause` support to `ingestion_manager.py` if missing
- [ ] Run ingestion for each new source → verify `rag_chunks` populated
- [ ] Add `internal_awareness` profile to `graph_rag.py SCORING_PROFILES` + keywords
- [ ] Build `/api/components-map/ask` endpoint (parallel RAG + GraphRAG + health lookup)
- [ ] Add Q&A widget to `components_map.html`
- [ ] Smoke test: query "how does NDC canvas work?" — expect RAG hits from codebase + graph hits from component nodes
- [ ] Test narration toggle: with LLM available AND without — both paths return usable results

### Phase 5 — Genesis Reflex + Scheduling (~200 LOC)
- [ ] Build `tools/genesis/reflexes/awareness.py` reflex
- [ ] Register reflex in Genesis daemon config
- [ ] Create `args/awareness_config.yaml` with user-approved defaults
- [ ] Integration test: trigger one full reflex cycle, verify all 5 phases log to `awareness_run_log`
- [ ] Document cadence in `goals/genesis_daemon.md`

### Phase 6 — Mandatory validation suite (per CLAUDE.md)
- [ ] `python -m py_compile` on all new/modified .py files
- [ ] `python -m ruff check` on all new/modified files — zero findings
- [ ] `python tools/workflow/coherence_checker.py --check ruff_lint --gate` (OPT-49 gate from earlier this session)
- [ ] `python -m pytest tests/test_awareness_*.py -q` — all pass
- [ ] `python -m pytest tests/e2e_components_map.py -q` — Selenium E2E passes
- [ ] `python -m bandit -r tools/awareness/ --severity-level medium` — zero medium+
- [ ] `python tools/db/init_icdev_db.py` — new migration applies cleanly
- [ ] Run full awareness reflex end-to-end, verify kanban_tasks.suggested gets populated
- [ ] Swap LLM config to non-qwen model, re-run `/api/components-map/ask?narrate=true` — verify portability
- [ ] `python tools/dx/companion.py --sync --write --json`
- [ ] `python tools/workflow/coherence_checker.py --all --fix --gate`
- [ ] Create `docs/features/internal-awareness-engine-impl.md` post-impl summary

---

## Success Criteria

- [ ] `/components-map` renders ≥ 900 nodes (tools) + ≥ 20 skills + 24 MCP servers + 8 canvases + 60 goals + 80 routes + 19 reflexes
- [ ] Hovering any node shows a comprehensive description (≥ 50 chars from `kg_nodes.properties.description`)
- [ ] Tree sidebar correctly filters the graph when category is clicked
- [ ] Full reflex cycle completes in < 5 minutes
- [ ] Synthetic NDC-route regression (manually break a route, run cycle) produces a `kanban_tasks.suggested` card with confidence ≥ 0.7 within one cycle
- [ ] Q&A query "how does NDC canvas work" returns ≥ 5 RAG hits from `icdev_canvas_modules` + `icdev_codebase` + graph hits showing the canvas_module's neighbors
- [ ] `rag_chunks` count grows from 123 → ≥ 5,000 after turnkey hydration
- [ ] All mandatory validation steps pass
- [ ] Works with LLM router configured to any Scanner-tier model (verified by config swap)
- [ ] No LLM calls in the reflex hot path (verified by running with Ollama offline)
- [ ] Dashboard Firing Alerts / CAT1 Findings click-through works (fixed separately in this session)

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Component indexer floods kg_nodes with thousands of noise entries | Entity type whitelist + `kg-icdev-self-awareness` graph isolation (doesn't pollute other graphs) |
| Health prober crashes on unreachable routes | Per-probe try/except, 5s timeout, always write a row (even as status='error') |
| Drift detector false positives on flaky tests | `consecutive_fails_for_regression=2` + `baseline_window_days=7` gate |
| Oracle → Suggested floods the board | Confidence threshold 0.7 + `cooldown_minutes=60` per component (no duplicate cards within 1h) |
| RAG ingestion takes hours on first hydration | `--batch` mode, prioritized by `priority` field, run overnight or chunked |
| JointJS can't render 900+ nodes smoothly | Default view = tree layout with virtualized rendering; force-directed layout only on focused subset (≤ 50 nodes at a time) |
| Internal codebase chunks leak via shared RAG search UI | Tag `rag_chunks.source_type='icdev_*'` → filter in existing `/knowledge-search` results to non-admin users (classification already CUI) |
| LLM rollout swap breaks narration | Narration is opt-in + graceful fallback; core Q&A works without any LLM |

---

## Why this is truly turnkey

1. **RAG: 90% built, 10% hydration** — run the existing indexer, register 10 new sources in SOURCE_REGISTRY, existing retrieval/rerank/citation all work
2. **KG: 90% built, 10% hydration** — existing schema, existing graph_rag scoring, just add one profile + populate with typed component nodes
3. **Dashboard: existing pages + one new page** — `/knowledge-search` and `/knowledge-graph` already exist for power-user query; `/components-map` adds visual navigation
4. **Oracle: existing prediction table + new lens** — doesn't require rewriting Oracle, just registering a lens
5. **Kanban: existing suggested column** — `'suggested'` is already a valid status per CHECK constraint, UI already renders the column on index.html
6. **Genesis: existing reflex framework** — just add one reflex file
7. **Air-gap + LLM-portable by construction** — no hardcoded models, no external network, uses libraries already vendored

Net: ~3,500 LOC new, ~300 LOC extensions to existing files. Delivers visual map, auto-regression detection, gap suggestions, and natural-language Q&A over the entire ICDEV codebase as a single integrated system.
