# Phase TWX — Twin Core Unification

> Turns 8 isolated canvas digital twins into a **system twin**: one registry, one
> canonical schema, one observer, one event fabric, plus federation (air-gap +
> target presets) and two new twins. Additive over the working twins — none
> rewritten. Card `twx-`. Manifest: `tools/manifest/twin-core.md`.

## What shipped

| Task | PR | Summary |
|------|----|---------|
| twx-core-01 | #659 | `tools/twin_core/` — `TwinRegistry` (data-driven, filesystem-discovered adapters) + canonical `schema.py` (verdict `pass\|warn\|fail\|unknown`, severity `blocker\|critical\|high\|medium\|low`, category, `target_csp`); NDC + PDC reference adapters. |
| twx-core-02 | #671 | Remaining adapters (BDC/SDC/DDC/ODC/IDC/Mission) + cross-canvas `observer.py` (snapshot freshness, verdict distribution, violation counts, reflex adherence) with library + CLI. |
| twx-bus-01 | #676 | `event_bridge.py` — twins publish `twin_snapshot_taken` / `twin_simulation_completed`; cross-canvas subs PDC `pipeline_deployed`→SDC refresh, SDC `sdc_threat_model_changed`→BDC crosswalk drift; honors bus security-context propagation. |
| twx-cov-01 | #680 | New minimal twins: **QDC** (quality-gate topology, reuses `qdc_gate_breach` read) + **AADC** (agent-failure cascade). |
| twx-cov-02 | #690 | Coverage audit + `twin_freshness_sweep` reflex (generic staleness fill) + wave-2 **AIML** twin. |
| twx-fed-01 | #695 | `airgap_rules.py` — config-driven air-gap validation (`deployment_blocker` violations) wired into NDC/PDC/IDC adapters. |
| twx-fed-02 | #701 | `target_presets.py` — run a sim against a target env (GovCloud/Azure Gov/IL5/air-gapped); flags services not available in target (`service_parity`) + staleness guard. |
| twx-spk-01 | #703 | LocalStack go/no-go (docs only). |
| twx-spk-02 | #704 | Batfish-for-NDC go/no-go (docs only). |
| twx-obs-01 | #707 | **Twin Observatory** dashboard `/twin-observatory` — health grid + drift event stream (8-component gate). |

## Event taxonomy (deliberately small — extend later)

Twin lifecycle (published by twins via the registry facade):
- `twin_snapshot_taken` — a twin froze a snapshot.
- `twin_simulation_completed` — a twin produced a canonical verdict (payload carries verdict + counts + top violations).

Cross-canvas triggers (twins react to each other):
- `pipeline_deployed` (PDC) → refresh the SDC attack-path twin.
- `sdc_threat_model_changed` (SDC) → re-run the BDC crosswalk-drift twin.
- `twin.snapshot.stale` (twin_freshness_sweep) → nudge a canvas whose twin is stale.

## Registered twins (12)

ndc, pdc, bdc, sdc, ddc, odc, idc, mission_canvas, qdc, aadc, aimc — surfaced with
per-twin health on the **Twin Observatory** (`/twin-observatory`): grid (latest
verdict, snapshot age, violations, refresh status, click-through) + the twin_*
drift event stream. Data is read-only from `observer.observe()` +
`event_bridge.recent_twin_events()`.

## Honesty invariants preserved

The canonical schema **wraps, never obscures**, each twin's `method` provenance —
BDC heuristic-vs-`llm_debate` labeling and unknown-verdict honesty, PDC dedup/
retention, ODC `estimate=True` basis, DDC lineage-grounding, IDC STIG-CAT scale,
QDC gate-reuse, AIML assessment-reuse. No verdict is ever fabricated;
`unknown` stays `unknown`.

## Retention / append-only posture

All new snapshot tables (`qdc_twin_snapshots`, `aadc_twin_snapshots`,
`aiml_twin_snapshots`) follow the **PDC retention pattern** — sha256 dedup +
bounded auto-snapshot retention, **NOT append-only** (deliberately absent from
`APPEND_ONLY_TABLES`; they are prunable like `pdc_snapshots`). The `*_simulations`
tables persist the verdict and are likewise non-append-only. No new append-only
audit tables were introduced by TWX.

## Federation

- **Air-gap (fed-01):** `args/twin_airgap_rules.yaml` — no public egress / external
  API / public package index / public registry; violations are `deployment_blocker`.
- **Target presets (fed-02):** `args/twin_target_presets.yaml` over the existing
  `context/cloud/csp_service_registry.json`; PUBLIC-DATA-ONLY, customer catalogs via
  `ICDEV_CSP_CATALOG_PATH`; 180-day staleness guard.

## Corrections to the source analysis docs

See the TWX ADR (Phase 75 in `docs/reference/adrs.md`).
