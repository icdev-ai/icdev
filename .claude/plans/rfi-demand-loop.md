# RFI Capability-Gap → Demand Signal → Kanban SUGGESTED Pipeline

> **Status:** Implemented (built directly this session; validated on PostgreSQL) · **Branch:** `feat/rfi-demand-loop`
> Core pipeline (store → det → emit → fdry → ui → vv) complete, incl. MCP tool
> `rfi_demand_scan`. Pipeline also runs via the workbench hook + `rfi_demand.py --scan` CLI.
> Note: `projects.yaml` roadmap seeding was skipped — the work was built directly, not via kanban tasks.

## Context

When ICDEV responds to an RFI, its requirements / "shall statements" will rarely be
100% satisfied by ICDEV's current capabilities. Today the RFI Workbench
(`tools/govcon/rfi_workbench.py`, the "six parts" feature at `/rfi/`) judges coverage
only by asking an LLM whether the **drafted prose** addresses each requirement
(`covered: true/false/partial` in `rfi_workbench_sections.requirements`). That answers
"did we write a good response?" — **not** "do we actually have this capability?" A
requirement can be marked covered while representing a genuine product gap.

**Goal:** turn each unmet requirement into a durable **demand signal**, aggregate the
same gap across multiple RFIs so recurring unmet needs rank higher, and decompose the
top gaps into **atomic kanban SUGGESTED tasks** (HITL-gated) so we can build the
missing features and close the gap for future RFI/RFP opportunities.

**Decisions locked with the user:**
- **Gap signal = hybrid** — flag as demand only when the capability-catalog match is
  `N` (coverage < 0.40) **AND** the LLM marks the requirement uncovered/partial.
- **Build pipeline = both** — write a direct atomic SUGGESTED card immediately for
  visibility **and** feed the demand-signal store so the ACF Foundry can later
  cluster / novelty-gate recurring gaps.
- **Aggregate across RFIs** — content-hash dedup with frequency/velocity, mirroring
  the existing `pulse_demand_signals` pattern.
- **HITL gate** — generated tasks land in `status='suggested'`; a human promotes them
  to `backlog` before autonomous build.

## Runtime pipeline (what this builds)

```
RFI Workbench section
  └─ requirements[] (existing LLM extraction + prose-coverage judgment)
        │
        ▼  [NEW] hybrid gap detector
  capability_mapper.map_pattern_to_capabilities(req.text, catalog) → L/M/N
  gap = (grade == 'N')  AND  (req.covered in {False,'partial'})
        │
        ▼  [NEW] aggregate
  rfi_capability_gaps  (content-hash PK, frequency++, first/last_seen, best_coverage,
                        rfi_refs[], priority = frequency × (1 − best_coverage))
        │
        ├──▶ [NEW] emit oracle_predictions gap node
        │      lens_name='rfi_demand', prediction_type='gap::rfi_capability'
        │        │
        │        ├──▶ DIRECT: LLM atomic decomposition → task_factory.create_tasks(
        │        │      status='suggested', source_prediction_id=<pred>,
        │        │      dispatch_source='rfi_demand')            ← HITL-gated cards
        │        │
        │        └──▶ FOUNDRY: harvested by tools/foundry/harvester.py genesis source
        │               (already reads oracle_predictions gap nodes) → novelty-gate →
        │               CoD/SIPA → atomic task graph → SUGGESTED
        │
        └──▶ [NEW] surface on RFI workbench annex + govcon capabilities rollup
```

## Reused components (do NOT reimplement)

| Purpose | Reuse | Path |
|---|---|---|
| Requirement → capability L/M/N grade | `map_pattern_to_capabilities(pattern, capabilities)` | `tools/govcon/capability_mapper.py:172` |
| Capability catalog | `icdev_capability_catalog.json` (+ `load_capability_catalog()`) | `context/govcon/`, `tools/govcon/capability_mapper.py` |
| Existing prose-coverage verdict | `check_requirement_coverage()` / `get_requirements()` `covered` field | `tools/govcon/rfi_workbench.py:1305` |
| Gap priority formula | `frequency × (1 − best_coverage)` | pattern from `tools/govcon/gap_analyzer.py` |
| Demand aggregation pattern | frequency/velocity/is_high_demand | `tools/pulse/db.py`, `tools/pulse/engine/demand_detector.py` |
| Atomic SUGGESTED card write | `create_tasks(list[dict])` (one-arg on-disk signature) | `tools/kanban/task_factory.py` |
| Foundry harvest of gap nodes | genesis source over `oracle_predictions` | `tools/foundry/harvester.py:170` |
| Prediction-node write pattern | `_write_gap_prediction` / stable `op-gap-<hash>` id | `tools/awareness/gap_detector.py:1231` |
| Existing gap UI surface | govcon capabilities page | `tools/dashboard/templates/govcon/capabilities.html` |

## New components

1. **DB migration** — `rfi_capability_gaps` (aggregation store) + `rfi_gap_task_links`
   (gap → emitted kanban task ids, idempotency/traceability). PostgreSQL-authored with
   SQLite fallback. Register append-only table in
   `.claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES`; add schemas to
   `tests/conftest.py MINIMAL_ICDEV_SCHEMA`.
2. **`tools/govcon/rfi_demand.py`** — pipeline module: `detect_gaps_for_section`,
   `aggregate_gap`, `emit_gap_prediction`, `decompose_gap_to_tasks`. Compute-in-Python
   for all JSON filters (no `json_extract` at runtime).
3. **Workbench hook** — call `detect_gaps_for_section` from the existing background
   coverage step in `rfi_workbench.py` (after `check_requirement_coverage`); env-flag
   guarded, never blocks the HITL UI.
4. **Foundry source** — `sources.rfi` in `args/foundry_config.yaml` + `_harvest_rfi` in
   `tools/foundry/harvester.py` (read-only, best-effort).
5. **UI surface (no new page route)** — extend `build_compliance_annex()` with a
   Capability-Gap→Demand block; add a cross-RFI rollup panel to existing
   `govcon/capabilities.html`. Avoids the 8-point new-page gate.
6. **Config** — `args/govcon_config.yaml` `rfi_demand.*` thresholds. No hardcoded models.
7. **MCP + manifest** — register `rfi_demand_scan`; add govcon manifest shard entry;
   foreground `companion.py --sync` + `coherence_checker.py --all --fix --gate`.

## Atomic tasks

Register project in `args/projects.yaml` (`key: rfi-demand`, `task_prefix: rfidem-`,
epics `det, store, emit, fdry, ui, vv`). Seed via `/seed-tasks` or
`task_factory.create_tasks` — never raw INSERT. Each description ≥200 chars with
What&why / Files / Acceptance / Test-verify.

### Epic `store` — aggregation store
- [x] `rfidem-store-01` Migration: `rfi_capability_gaps` (content_hash PK, capability_need,
      keywords, domain, frequency, velocity, first_seen, last_seen, best_coverage,
      priority, is_high_demand, rfi_refs JSON, status, classification). PG + SQLite.
- [x] `rfidem-store-02` Migration: `rfi_gap_task_links` (gap_hash, task_id, emitted_at,
      route['direct'|'foundry']) + idempotency unique index.
- [x] `rfidem-store-03` Register append-only table in `pre_tool_use.py`; add both
      schemas to `conftest.py MINIMAL_ICDEV_SCHEMA`.

### Epic `det` — hybrid detection
- [x] `rfidem-det-01` `detect_gaps_for_section`: `map_pattern_to_capabilities` per req +
      existing `covered` verdict → hybrid rule (N AND uncovered/partial). Pure fn.
- [x] `rfidem-det-02` Wire into `rfi_workbench.py` background coverage step; env-flag
      guarded (default on), non-blocking.
- [x] `rfidem-det-03` `aggregate_gap`: content-hash on normalized need + sorted keywords;
      upsert into `rfi_capability_gaps` (frequency++, recompute priority).

### Epic `emit` — direct SUGGESTED cards
- [x] `rfidem-emit-01` `emit_gap_prediction`: oracle_predictions node
      (`lens_name='rfi_demand'`, `prediction_type='gap::rfi_capability'`, stable
      `op-gap-<hash>` id, severity from priority). Reuse `_write_gap_prediction` shape.
- [x] `rfidem-emit-02` `decompose_gap_to_tasks`: LLM (`LLMRouter.invoke`) → 3–8 atomic
      specs → `task_factory.create_tasks(status='suggested', source_prediction_id,
      dispatch_source='rfi_demand', idempotency_key=gap_hash)`; record `rfi_gap_task_links`.
- [x] `rfidem-emit-03` Gate on `priority >= min_priority_for_task`; dedup via
      `rfi_gap_task_links` so re-runs never duplicate cards.

### Epic `fdry` — Foundry harvest source
- [x] `rfidem-fdry-01` Add `sources.rfi` to `args/foundry_config.yaml`.
- [x] `rfidem-fdry-02` `_harvest_rfi` in `harvester.py`: high-priority
      `rfi_capability_gaps` → `foundry_signals` `(source_engine='rfi', ...)`; best-effort.

### Epic `ui` — surfacing
- [x] `rfidem-ui-01` Extend `build_compliance_annex()` with Capability-Gap→Demand block.
- [x] `rfidem-ui-02` Cross-RFI demand rollup panel on `govcon/capabilities.html` + IQE
      query widget seed.
- [x] `rfidem-ui-03` Mirror template edits to `icdev/tools/dashboard/templates/...`
      (companion sync).

### Epic `vv` — verification & registration
- [x] `rfidem-vv-01` Unit tests: hybrid truth table, aggregation dedup, priority math.
- [x] `rfidem-vv-02` Integration: fake RFI session w/ known-uncovered requirement →
      assert `rfi_capability_gaps` row + SUGGESTED task `dispatch_source='rfi_demand'`.
- [x] `rfidem-vv-03` MCP `rfi_demand_scan` tool registered (tool_registry.py +
      gap_handlers.py, modes list/scan; 29 MCP tests green); manifest shard entry
      added; ruff clean; coherence gate shows no NEW findings (all failures
      pre-existing, unrelated files).

## Verification (end-to-end)

1. **Unit:** `pytest tests/govcon/test_rfi_demand.py -v` — hybrid truth table, hash
   dedup, priority formula, task-spec shape.
2. **Detection:** create an RFI session with a deliberately unmet requirement (capability
   absent from `icdev_capability_catalog.json`); run the background coverage step; assert
   a `rfi_capability_gaps` row (`best_coverage < 0.40`, `covered != True`).
3. **Direct SUGGESTED:** `kanban_tasks WHERE dispatch_source='rfi_demand'` → atomic
   `status='suggested'` cards with `source_prediction_id`; re-run → no duplicates.
4. **Aggregation:** feed same gap from a 2nd session → `frequency` increments, `priority`
   rises, `is_high_demand` flips at threshold.
5. **Foundry:** `python tools/foundry/harvester.py --run-id <id> --json` shows
   `source_engine='rfi'`; `python tools/foundry/engine.py --run --dry-run --json` →
   novelty-gated concept referencing the gap.
6. **UI:** Playwright `/rfi/` annex + `/govcon` capabilities; screenshot to
   `playwright/screenshots/rfi_demand.png`.
7. **Gates:** `coherence_checker.py --all --gate` green; `ruff check`; append-only table
   registered.

## Guardrail notes
- Branch-first: `feat/rfi-demand-loop`, PR, wait for CI. Never on `main`.
- Runtime SQL is PostgreSQL-authored; parse JSON in Python for filters/grouping.
- No new dashboard page route → 8-point completeness gate avoided by reusing `/rfi/` and
  `/govcon`. If a dedicated `/rfi/demand` page is added later, ship all 8 components.
