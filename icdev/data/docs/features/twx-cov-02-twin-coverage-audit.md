# TWX Twin Coverage Audit + Wave-2 Decision (twx-cov-02)

> Feeds the TWX ADR (twx-xcut-01). Audit-then-decide: which additional canvas
> digital twins to build, and which twins still lack an auto-refresh reflex.

## 1. Twin coverage after wave-1

`tools/twin_core/` (twx-core-01/02) unifies the working twins behind one registry
+ canonical schema. **12 twins are now registered** (10 pre-existing + QDC and
AADC added in twx-cov-01, + AIML added here in wave-2):

| Canvas | Twin | Registered key |
|--------|------|----------------|
| NDC | tools/network/twin.py | ndc |
| PDC | tools/pipeline/twin.py | pdc |
| BDC | tools/boundary_canvas/twin.py | bdc |
| SDC | tools/security_canvas/twin.py | sdc |
| DDC | tools/data_canvas/twin.py | ddc |
| ODC | tools/observability_canvas/twin.py | odc |
| IDC | tools/infra_canvas/ (snapshot_writer + preapply_gate) | idc |
| Mission | tools/mission_canvas/twin.py | mission_canvas |
| QDC | tools/qdc_canvas/twin.py (twx-cov-01) | qdc |
| AADC | tools/agentic_ai_canvas/twin.py (twx-cov-01) | aadc |
| **AIML** | **tools/aiml_canvas/twin.py (twx-cov-02, this task)** | **aimc** |

> Note: an earlier automated audit run read a **stale shared checkout** and
> reported twin_core / QDC / AADC as absent. That is a stale-tree artifact — all
> three are on `origin/main`. This document reflects the live tree.

## 2. Auto-refresh reflex coverage

The old docs' "cATO-only refresh" claim is **stale**. Per-twin refresh reflexes
already exist for most twins:

| Twin | Auto-refresh reflex |
|------|---------------------|
| NDC | `ndc_topology_drift` |
| PDC | `pdc_pipeline_stale` |
| BDC | `bdc_isa_expiry` + `cato_twin` + `cato_monitor` |
| SDC | `sdc_control_expiry` |
| DDC | `freshness_guardian` |
| ODC | `odc_coverage_refresh` (+ `observability_retention`) |
| IDC | `idc_cloud_drift` |
| QDC | `qdc_gate_breach` (gate-result refresh) |
| AADC | `aadc_reflex` (assessment scoring) |
| Mission | **NONE (gap)** |
| AIML | **NONE (gap — new)** |

**Gap fill (this task): a single cross-canvas `twin_freshness_sweep` reflex** —
`tools/genesis/reflexes/twin_freshness_sweep.py`, 6h, registered in
`daemon.py` REFLEX_NAMES + `reflex_registry.py` + `args/genesis_config.yaml`. It
runs the twin_core observer and publishes `twin.snapshot.stale` for any
registered twin whose newest snapshot is stale/absent. This covers Mission,
AIML, and any future twin **generically** — a data-driven, single-reflex fill
rather than one near-duplicate reflex per canvas (the system-twin payoff of
twin_core). Read-only (green tier), air-gap safe.

## 3. Wave-2 twin decision (build ≤2; park the rest)

Twin-less canvases were audited for a **design-graph what-if surface** (a
`graph_json` design + a meaningful delta to simulate). Verdict:

### Built this task (1 of the ≤2 budget)
- **AIML (aimc)** — cleanest net-new design twin. Substrate: `aiml_designs.graph_json`
  + `aiml_nodes`/`aiml_edges` + `aiml_versions` baseline + `aiml_assessments`.
  Snapshot = AI/ML architecture graph; `simulate_delta` reuses the real
  governance assessments (latest per framework, failing = `passed=0`) — removing
  an architecture node on a design that already fails a framework = `fail`.
  Mirrors the QDC pattern exactly (grounded, not re-derived).

### Parked with rationale (for the ADR)
- **MDC (Migration Canvas)** — *justified but deferred to wave-2b.* Has the
  richest what-if demand already in code (`simulate_commit_check`, version
  snapshots, "what-if" RAG, `mdc_cutover_countdown` reflex) but a **larger
  surface** (wave plans, cutover steps, app-dependency topology). Building it well
  exceeds the "minimal twin" budget here; it is the top wave-2b candidate.
- **OHC (Ops Hub)** — MLOps registry, not a design topology. Drift already
  covered by `ohc_*` MCP tools + `model_monitor_drift` / `llmops_drift_sweep` /
  `mlops_data_drift_sweep`. **No snapshot+delta semantics beyond existing tooling.**
- **DSOC / CCC / PMC / NOCC** — operational network/ops state (flowspec, circuits,
  peering, alarms). Topology-flavored but **no design graph**; already served by
  domain monitor reflexes (`bgp_hijack_monitor`, `circuit_capacity_monitor`,
  `peering_health_monitor`, `nocc_alarm_triage`, …). Defer to a possible wave-3
  "cold-snapshot + drift" rather than a full what-if twin.
- **BI Studio, AISG, Cortex, DIC, AI-ify, logs, docgen, slides, demo_runner,
  ACE, foundry, integrity, canvas_health, coworkers, second_brain, wfc,
  rfi_canvas** — content / AI-service / reporting canvases with **no design-graph
  or snapshot-worthy state + delta**. No twin justified.

## 4. Net outcome
- Twin coverage: 10 → **12** (QDC/AADC in wave-1; AIML in wave-2).
- Refresh coverage: every twin now covered, either by a dedicated reflex or by
  the new generic `twin_freshness_sweep`.
- Wave-2 budget: 1 of 2 spent (AIML); MDC is the documented wave-2b candidate;
  all operational/service canvases parked with rationale above.
