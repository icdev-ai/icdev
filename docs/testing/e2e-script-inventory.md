# CUI // SP-CTI

# E2E Standalone Selenium Script Inventory

Static inventory of the standalone `main()`-driven Selenium scripts matching `tests/e2e_*.py`. These scripts are **not** collected by pytest (their `test_*` functions take positional args, not fixtures) and were historically run by no runner. PR #502 added `discover_selenium_scripts()` / `run_selenium_script()` to `tools/testing/e2e_runner.py`; this inventory (oxf-e2e-01) is the discovery + remediation feed.

- **Total scripts:** 76
- **Importable:** 69 (seeded into `args/e2e_script_allowlist.yaml`)
- **Broken-import:** 7 (excluded with reasons — remediation backlog)
- **Superseded (has possible pytest twin):** 15 (informational; independent of import status)

> **Import-health method:** each script is exec'd in an isolated subprocess with `PYTHONPATH` set to the repo root and a 15s timeout. A script that connects to `localhost` at import time (no `if __name__ == "__main__"` guard) fails under the sandbox and is classified `broken-import`. Some `broken-import` reasons (e.g. `no such table: topologies`) reflect top-level DB access against an empty fresh-worktree DB, not a true import defect — treat those as top-level-execution hygiene issues. Scripts were **not** run or modified.

> **Superseded** is a fuzzy name-match heuristic against `tests/test_*.py` — a hint that a pytest twin may cover the same page, not a verified duplication. Do not delete a script on this signal alone.

## Inventory

| Status | Script | Target page / feature | Last commit | Possible pytest twin | Import error |
|--------|--------|-----------------------|-------------|----------------------|--------------|
| broken-import | `e2e_dual_track_lifecycle` | Dual-Track Full Lifecycle E2E Test. | 2026-05-25 | — | `sqlite3.OperationalError: no such table: topologies` |
| broken-import | `e2e_full_lifecycle_complex` | Full Lifecycle E2E: Complex Multi-Site Enterprise Network. | 2026-05-25 | — | `sqlite3.OperationalError: no such table: topologies` |
| broken-import | `e2e_killswitch_widget` | Selenium E2E for FathomDesk kill-switch widget on /orders. | 2026-05-25 | — | `ModuleNotFoundError: No module named 'tools.trading.risk'` |
| broken-import | `e2e_ndc_full_lifecycle` | E2E Full Lifecycle Test: NDC Diagram Ingestion → RAG → KG → Analysis Scenarios. | 2026-05-25 | — | `sqlite3.OperationalError: no such table: topologies` |
| broken-import | `e2e_ui_full_coverage` | (url) http://localhost:5050 | 2026-06-14 | — | `AttributeError: 'NoneType' object has no attribute '__dict__'. Did you mean: '__dir__'?` |
| broken-import | `e2e_ui_navigation` | E2E UI Navigation Test — menus, tabs, links, and content. | 2026-05-31 | — | `AttributeError: 'NoneType' object has no attribute '__dict__'. Did you mean: '__dir__'?` |
| broken-import | `e2e_zta_lac_deny_assertions` | ZTA LAC Simulator — deny_reasons panel + audit_trail Playwright assertions. | 2026-06-04 | — | `exit 1` |
| importable | `e2e_alert_test` | E2E integration test: alert pipeline under load. | 2026-06-14 | — | — |
| importable | `e2e_bdc_lifecycle` | E2E Selenium lifecycle test — BDC (Boundary Design Canvas). | 2026-05-25 | — | — |
| importable | `e2e_boundary_cato` | Selenium E2E — /boundary/cato (BDC cATO Dashboard). | 2026-05-25 | — | — |
| importable | `e2e_canvas_kg` | E2E — Canvas Knowledge Graph page + REST API (/canvas-kg). | 2026-06-05 | — | — |
| importable | `e2e_canvas_orchestration` | E2E Selenium tests — Canvas Orchestration: event bus + compliance page. | 2026-05-25 | — | — |
| importable | `e2e_chat_hitl_kanban_lifecycle` | Full Lifecycle E2E Test: Chat -> HITL -> Kanban Auto-Intake Pipeline | 2026-05-25 | — | — |
| importable | `e2e_clawhub` | E2E smoke tests — /clawhub route coverage. | 2026-06-28 | `test_clawhub_connector` | — |
| importable | `e2e_cloud_migration_security` | E2E Test: Cloud Migration Security Dashboard Pages (11 pages) | 2026-05-25 | — | — |
| importable | `e2e_components_map` | E2E Selenium test — /components-map (Phase 1g, Internal Awareness Engine). | 2026-05-25 | — | — |
| importable | `e2e_confluence_widget` | Selenium E2E for confluence toggle widget on /orders. | 2026-05-25 | — | — |
| importable | `e2e_cpmp_cor` | E2E tests — /cpmp/cor (COR Portal page). | 2026-06-14 | `test_gcpl_cor_12_cpmp_cor_access_log` | — |
| importable | `e2e_data_lineage` | Selenium E2E — /lineage page: seed lineage data, navigate, assert ≥1 node + 1 edge. | 2026-05-25 | `test_dataset_lineage` | — |
| importable | `e2e_ddc_lifecycle` | E2E Selenium lifecycle test — DDC (Data Design Canvas). | 2026-05-25 | — | — |
| importable | `e2e_ddc_sops` | Selenium E2E — DDC SOPs page + approval workflow API. | 2026-05-25 | — | — |
| importable | `e2e_devops_twin` | E2E Selenium test — PDC Pipeline Twin /devops/twin/<pipe_id>. | 2026-05-25 | `test_devops_twin_route` | — |
| importable | `e2e_election_phase_widget` | Selenium smoke test for FathomDesk election-phase widget. | 2026-05-25 | — | — |
| importable | `e2e_exec_quality_widget` | Selenium E2E for execution-quality widget on /orders. | 2026-05-25 | — | — |
| importable | `e2e_fathomdesk` | Selenium E2E Lifecycle Test for FathomDesk Trading Dashboard. | 2026-05-25 | `test_fathomdesk_trap_sweep_anomaly_detection` | — |
| importable | `e2e_fathomdesk_modal` | Selenium E2E test for FathomDesk analysis modal + ICDEV™'s INTaaS + Pulse. | 2026-05-25 | — | — |
| importable | `e2e_fathomdesk_trap` | E2E integration tests for FathomDesk Phase 7.12 — trap events write path & broker orders. | 2026-05-25 | `test_fathomdesk_trap_sweep_anomaly_detection` | — |
| importable | `e2e_fedramp-20x` | Selenium E2E tests for the /fedramp-20x KSI Dashboard. | 2026-05-25 | — | — |
| importable | `e2e_govcon_proposals_cpmp` | E2E Selenium tests — /govcon, /proposals, /cpmp (prop-vv-02-d1). | 2026-06-06 | — | — |
| importable | `e2e_home_autonomy_panel` | E2E Selenium test — Autonomous Recovery panel on Home (/). | 2026-05-25 | — | — |
| importable | `e2e_home_projects_in_flight` | E2E Selenium test — Home (/) with Projects in Flight partial. | 2026-05-25 | — | — |
| importable | `e2e_idc_lifecycle` | E2E Selenium lifecycle test — IDC (Infrastructure Design Canvas). | 2026-05-25 | — | — |
| importable | `e2e_infra_emit` | E2E Selenium test — /infra/emit IaC generation form. | 2026-05-25 | — | — |
| importable | `e2e_infra_twin` | E2E Selenium test — /infra/twin IDC Twin dashboard. | 2026-05-25 | `test_infra_twin_route` | — |
| importable | `e2e_intake_requirements` | E2E Selenium test — /intake/requirements/<session_id>. | 2026-05-20 | — | — |
| importable | `e2e_kanban_bulk_promote` | E2E Selenium test — Suggested lane bulk promote / dismiss + value sort. | 2026-05-25 | — | — |
| importable | `e2e_kanban_depends_on` | E2E Selenium test — native task dependency / blocked badge on the kanban board. | 2026-05-25 | `test_kanban_depends_on` | — |
| importable | `e2e_kanban_depends_on_full_lifecycle` | E2E Full-Lifecycle test — native task dependency (migration 015). | 2026-05-25 | `test_kanban_depends_on` | — |
| importable | `e2e_mission_canvas` | E2E Selenium lifecycle test — Mission Canvas. | 2026-05-25 | `test_mission_canvas` | — |
| importable | `e2e_ndc_path_reachability` | E2E Selenium test — NDC Path Reachability (dt-ndc-05). | 2026-05-25 | — | — |
| importable | `e2e_ndc_sops` | E2E Selenium test — NDC SOPs dashboard (/ndc/sops). | 2026-05-25 | — | — |
| importable | `e2e_ndc_twin_tfw` | E2E Selenium test — NDC Digital Twin / Traffic Flow Walkthrough (/network/twin/<id>). | 2026-05-25 | — | — |
| importable | `e2e_network_arb_erb` | E2E Selenium test — ARB/ERB Documentation (Architect Workbench). | 2026-05-25 | — | — |
| importable | `e2e_network_canvas` | E2E Selenium test — Network Canvas interactive tests. | 2026-05-25 | — | — |
| importable | `e2e_network_collect` | E2E Selenium test — Connect & Collect + Diagram Data Extraction. | 2026-05-25 | — | — |
| importable | `e2e_network_extended` | E2E Selenium test — Network Canvas Extended Features. | 2026-05-25 | — | — |
| importable | `e2e_network_geo` | E2E Selenium test — Geolocation + Map View (Phase 4). | 2026-05-25 | — | — |
| importable | `e2e_network_import` | E2E Selenium test — Intelligent Import & Stitching (Phase 1 Network Intelligence). | 2026-05-25 | — | — |
| importable | `e2e_network_innovation` | E2E Selenium test — Innovation Flywheel (Phase 7). | 2026-05-25 | `test_innovation` | — |
| importable | `e2e_network_nl_query` | E2E tests for Network Canvas Natural Language Query engine. | 2026-05-25 | — | — |
| importable | `e2e_network_p1` | E2E Selenium test — Network Canvas P1 features. | 2026-05-25 | — | — |
| importable | `e2e_network_p1_rulebook` | E2E Selenium test — Network Design Rulebook (Phase 1 of Workbench). | 2026-05-25 | — | — |
| importable | `e2e_network_p2` | E2E Selenium test — Network Canvas P2 features. | 2026-05-25 | — | — |
| importable | `e2e_network_p2_patterns` | E2E Selenium test — Design Pattern Library (Phase 2 of Workbench). | 2026-05-25 | — | — |
| importable | `e2e_network_p3` | E2E Selenium test — Network Canvas P3 + unprioritized features. | 2026-05-25 | — | — |
| importable | `e2e_network_p345` | E2E Selenium test — Phases 3-5 (Guidance, Case Workflow, Chat Assistant). | 2026-05-25 | — | — |
| importable | `e2e_network_peering_capacity` | E2E Selenium test — Peering + Capacity + Facilities + Readiness. | 2026-05-25 | — | — |
| importable | `e2e_network_phase_a` | E2E Selenium test — Network Canvas Phase A (Review Board Pipeline + SAFe Bridge). | 2026-05-25 | — | — |
| importable | `e2e_network_phase_b` | E2E Selenium test — Network Canvas Phase B | 2026-05-25 | — | — |
| importable | `e2e_network_phase_c` | E2E Selenium test — Network Canvas Phase C (Impact Analysis + Enterprise Summary). | 2026-05-25 | — | — |
| importable | `e2e_network_profiles` | E2E Selenium test — Device Command Profiles + Discovery Config (Phase 2). | 2026-05-25 | — | — |
| importable | `e2e_network_projects` | E2E Selenium test — Network Canvas Project Portfolio (P0 features). | 2026-05-25 | — | — |
| importable | `e2e_network_refresh` | E2E Selenium test — Tech Refresh Planner (Phase 6). | 2026-05-25 | — | — |
| importable | `e2e_network_routing` | E2E Selenium test — Routing Table Topology + Config Sync (Phase 3). | 2026-05-25 | — | — |
| importable | `e2e_network_whatif` | E2E Selenium test — What-If Simulation Bridge (Phase 5). | 2026-05-25 | — | — |
| importable | `e2e_observability_mitre` | Selenium E2E test for /observability/mitre MITRE ATT&CK matrix page. | 2026-05-25 | `test_observability_mitre_route` | — |
| importable | `e2e_odc_lifecycle` | E2E Selenium lifecycle test — ODC (Observability Design Canvas). | 2026-05-25 | — | — |
| importable | `e2e_oracle_insights_widget` | Selenium E2E — Oracle Insights widget on home dashboard. | 2026-05-25 | — | — |
| importable | `e2e_prop_vv02_iqe_smoke` | IQE smoke test for govcon/proposals collections (prop-vv-02). | 2026-07-08 | — | — |
| importable | `e2e_prop_vv02_role_and_new_surfaces` | E2E — role-scoped access + newer role-driven surfaces (prop-vv-02). | 2026-07-08 | — | — |
| importable | `e2e_proposals_reviews_dashboard` | E2E Selenium test — /proposals/reviews-dashboard (prop-rev-07). | 2026-05-29 | — | — |
| importable | `e2e_rfi_canvas` | E2E lifecycle test for the RFI Response Workbench canvas. | 2026-07-12 | `test_rfi_canvas` | — |
| importable | `e2e_route_coverage_gaps` | E2E smoke tests — route_no_e2e gap-detector coverage pass. | 2026-06-15 | — | — |
| importable | `e2e_simulation` | E2E Selenium test — Digital Program Twin /simulation dashboard. | 2026-05-25 | `test_simulation_engine` | — |
| importable | `e2e_simulation_chat` | E2E Selenium test — /simulate/chat — NDC, SDC, EDA canvas types. | 2026-05-25 | — | — |
| importable | `e2e_writeguard` | E2E Selenium test — /writeguard (Content Quality Dashboard). | 2026-05-25 | `test_mcp_writeguard_tool` | — |

## How to run one

Standalone scripts are executed directly (not via pytest) through the `run_selenium_script` path in `tools/testing/e2e_runner.py`. They require a live dashboard at `http://localhost:5050` and a vendored browser driver.

```bash
# Run a single standalone script (via the selenium driver dispatch):
python tools/testing/e2e_runner.py --driver selenium \
    --test-file tests/e2e_odc_lifecycle.py --json

# Or run it directly (identical execution path):
python tests/e2e_odc_lifecycle.py

# Run the pytest selenium suite AND every allowlisted standalone script:
python tools/testing/e2e_runner.py --driver selenium --run-all --include-scripts --json
```

Without `--include-scripts`, `--run-all` behavior is unchanged (pytest `tests/e2e_selenium/` only). The `--include-scripts` opt-in reads `args/e2e_script_allowlist.yaml` and additionally runs each allowlisted script via `run_selenium_script`; excluded (broken) scripts are skipped by name.

