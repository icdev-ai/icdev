# Federated Data Mesh + Conflict-Escalation Prediction (issue #19)

> CUI // SP-CTI

## Summary
ICDEV™ provides a federated data mesh that ingests multi-provider intelligence
signals, standardizes them via ETL, identifies escalation patterns in
unstructured text with deterministic ML, and forecasts conflict escalation —
surfaced through the Strategos intel-brief workflow. This capability was already
implemented on `main`; this doc maps issue #19's four components to the code,
and records the verification + the regression test added for the core logic.

## Component → implementation map

| # | Component | Implementation |
|---|-----------|----------------|
| 1 | **Federated mesh / multi-provider connectors** | `icdev/tools/data_canvas/csp.py` (orchestrator: `get_csp_status`, `run_sync`) + `icdev/tools/data_canvas/data_mesh/csp/{aws_datazone,azure_purview,gcp_dataplex}.py` (CSP adapters, graceful SDK degradation, dry-run). Domain/product/contract CRUD in `data_mesh.py`. |
| 2 | **ETL pipeline (format standardization)** | `icdev/tools/strategos/federated_mesh.py` — provider pulls `_pull_acled` / `_pull_osint_signals` / `_pull_dat` → `_normalize()` (standard schema: provider, event_date, theater, event_type, description, lat/lon, fatalities) → `ingest()` upserts into `sg_mesh_signals`. |
| 3 | **ML pattern identification (unstructured data)** | `federated_mesh.py` — `_simple_tfidf()` + `detect_patterns()` over signal descriptions against `_ESCALATION_PATTERNS` (5 types: force_buildup, offensive_action, diplomatic_breakdown, civilian_impact, wmd_concern) → confidence-scored rows in `sg_mesh_patterns`. |
| 4 | **Predictive analytics (escalation forecasting)** | `icdev/tools/strategos/intel_report_engine.py` (`_compute_escalation_score`, `generate_leadership_brief`), `icdev/tools/strategos/predictive_analysis.py` (PMESII-PT War Readiness Index + Bayesian `p_war_posterior`), `icdev/tools/intelligence/war_readiness/information_scorer.py` (rhetoric/dehumanization/cyber). Multi-horizon forecasts + escalation rung. |

## Surfaces
- **Pages:** `/strategos/intel-brief` (leadership briefing), `/strategos/briefs` (history), `/strategos/dat` (Diplomatic Tension Index). DAT is also aliased at `/dat` + `GET /api/dat/dti` (issue #18).
- **API:** `POST /api/strategos/intel-brief/run` → full pipeline → JSON `{dti_score, escalation_rung, forecasts, goldstein_avg, iw_triggered, narrative_md, brief_id, ...}`; `GET /api/strategos/dat/score`, `/history`.
- **Refresh:** `tools/genesis/reflexes/dat_refresh.py` — 6-hour DTI recompute cadence.
- **Tables:** `sg_mesh_signals`, `sg_mesh_patterns`, `sg_intel_reports`, `sg_leadership_briefs`, `sg_conflict_events`, `sg_dat_*`.

## Verification (2026-06-05)
- `POST /api/strategos/intel-brief/run` → **200** on a fresh bootstrapped DB, returning a DTI score, escalation rung, forecasts, and narrative.
- `GET /strategos/intel-brief` → **200**.
- Regression test `tests/test_federated_mesh.py` (8 cases) covers the deterministic, no-network core: `_normalize` schema standardization, `detect_patterns` escalation-pattern detection (force_buildup + offensive_action; benign text yields none; empty → none), and `_compute_escalation_score` (empty → 0, monotonic in confidence, capped at 1.0).

## Notes
- The mesh/intel modules live under the canonical `icdev.tools.*` namespace (no root-`tools` shim copy) — import accordingly.
- Provider pulls degrade gracefully (empty list) when source tables/SDKs are absent, so the pipeline is safe to run on a bare DB (returns a zero-signal brief rather than erroring).
