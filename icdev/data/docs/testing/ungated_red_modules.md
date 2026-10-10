# Ungated backlog — what the census recorded as RED

`docs/testing/ungated_test_census.json` measured **1792** grandfathered modules alone and recorded **93** of them `failed`. The promotion batches (rem-tst-02/03/04) consume the green rows; nothing consumed these, and a backlog with no shape to it is just a number.

This groups the reds by **failure shape** — what decides who fixes it, and whether twenty reds are one job or twenty. The census's own `Most common failure signatures` table cannot answer that: it keys on the raw first-failure line, which carries each test's own prose, so it renders as near-singletons.

**93 grouped = 93 recorded failing.** Arithmetic checks.

## Groups

| Modules | Shape | One example |
|---:|---|---|
| 54 | **Behavioural assertion** (`assertion`) | `tests/airgap/test_hook_compat_git_blocklist.py` — E assert True is False |
| 8 | **Missing table or column** (`schema-drift`) | `tests/test_bdr_vv_suite.py` — E AssertionError: {"control_count":2,"error":"insert: table compliance_snapshots has no column named status",… |
| 6 | **SQL dialect / placeholder mismatch** (`sql-dialect`) | `tests/test_aca_pg_native_placeholders.py` — E AssertionError: blueprint.py builds SQL fragments with `?`: [(1345, 'Export Academy completions as xAPI 1.0… |
| 5 | **Unauthenticated test client (401 / 403 / CSRF)** (`http-auth`) | `tests/e2e/test_derivative_classifier_lifecycle.py` — E AssertionError: Preview failed 403: {"code":"CSRF_FAILED","error":"CSRF token missing or invalid","message"… |
| 5 | **DB driver error, reason truncated** (`db-error-unspecified`) | `tests/test_cnr_migration_intel.py` — FAILED tests/test_cnr_migration_intel.py::test_mi_db_writes_postgresql - psyc... |
| 5 | **Errored in setup, never asserted** (`error-dominant`) | `tests/test_dic_techwriter.py` — ERROR tests/test_dic_techwriter.py::test_import_from_docgen_valid_template_type_returns_500_or_doc_id |
| 4 | **Import / missing attribute** (`import-or-attribute`) | `tests/test_engine_nlp_input_classifier.py` — E AttributeError: module 'tools.ai_augmentation.engine' has no attribute '_classify_input_ref_nlp' |
| 4 | **Other runtime exception** (`runtime-exception`) | `tests/test_dcpr_product_registry.py` — FAILED tests/test_dcpr_product_registry.py::test_subscribe_and_approve - KeyE... |
| 2 | **No usable signature recorded** (`unclassified`) | `tests/test_devops_twin_route.py` — FAILED tests/test_devops_twin_route.py::test_twin_list_empty_returns_200 - ji... |

## What each shape means, and the modules in it

### Behavioural assertion — 54 module(s)

A readable assertion that the code under test does not satisfy. The residue after the shared shapes are pulled out, and the bucket with the least leverage: these are individual jobs.

| Module | failed / error | First failure recorded |
|---|---:|---|
| `tests/airgap/test_hook_compat_git_blocklist.py` | 1 / 0 | E assert True is False |
| `tests/browser/test_four_seams.py` | 1 / 0 | E AssertionError: default tool list not found � update this test |
| `tests/dashboard/test_home_tile_gating.py` | 1 / 0 | E AssertionError: assert True is False |
| `tests/docmod/test_regen_quality_gate.py` | 2 / 0 | E assert False is True |
| `tests/e2e_alert_test.py` | 1 / 0 | FAILED tests/e2e_alert_test.py::test_async_db_writer_persists_records - Asser... |
| `tests/git/test_manifest_merge_rehearsal.py` | 1 / 0 | E AssertionError: merge=union applied to 'args/ci_skip_census.txt', which is not in the union-safe allowlist. Union is only safe for flat, line-orien… |
| `tests/security/test_prd_to_plan.py` | 1 / 0 | E AssertionError: skeleton fails its own validator: [LintFinding(line=9, kind='file_name', match='Users\\schuo\\AppData\\Local\\Temp\\icdev-census-ot… |
| `tests/test_aac_llm_http_auth.py` | 1 / 0 | E assert 1 == 2 |
| `tests/test_aca_cert_evidence.py` | 1 / 0 | E assert '"fa_certificate_evidence"' in 'APPEND_ONLY_TABLES list in is_append_only_table_modification()\n - Direct sqlite3.connect() that bypasses th… |
| `tests/test_aca_xp_ledger.py` | 1 / 0 | E assert '"fa_xp_ledger"' in 'APPEND_ONLY_TABLES list in is_append_only_table_modification()\n - Direct sqlite3.connect() that bypasses the stor... #… |
| `tests/test_ace_phase_d_roles.py` | 1 / 0 | FAILED tests/test_ace_phase_d_roles.py::test_zero_gaps_after_generation - Ass... |
| `tests/test_attack_path_twin_predicates.py` | 1 / 0 | E AssertionError: expected a warning to be emitted for an unknown predicate |
| `tests/test_bdc_reflex_smoke.py` | 1 / 0 | E AssertionError: controls-query failure must roll back � otherwise the PG transaction stays aborted and every later query in the cycle cascade-fails |
| `tests/test_bdc_twin_honesty.py` | 1 / 0 | E AssertionError: assert 'error' == 'ok' |
| `tests/test_cloud_monitoring_iam.py` | 1 / 0 | E AssertionError: 0 not greater than or equal to 1 |
| `tests/test_conflict_mesh.py` | 3 / 0 | E assert 0 == 1 |
| `tests/test_dwo_vv_mcp_dispatch.py` | 7 / 0 | E AssertionError: assert 'mcp_tool_exceeds_caller_il' == 'mcp_tool_awa...uman_approval' |
| `tests/test_ecr_api.py` | 1 / 0 | FAILED tests/test_ecr_api.py::test_api_key_ui_routes - AssertionError: <!doct... |
| `tests/test_ecr_bill.py` | 3 / 0 | FAILED tests/test_ecr_bill.py::test_usage_api_returns_data - AssertionError: ... |
| `tests/test_federal_peering_request.py` | 1 / 0 | E AssertionError: Expected pmc_request_id to be set |
| `tests/test_gdx_dead_02_registration_schema.py` | 5 / 0 | E AssertionError: The feature doc lost its designed-not-built section. That section is the only place the two-sided history (penta-gd-03 deleted the … |
| `tests/test_genesis_skill_security_monitor.py` | 4 / 0 | E assert False is True |
| `tests/test_idr_multi_source.py` | 1 / 0 | E AssertionError: CoT should be called when evidence > 500 chars |
| `tests/test_llm_config_single_source.py` | 1 / 0 | E AssertionError: args/llm_config.yaml and icdev/args/llm_config.yaml have drifted |
| `tests/test_lpx_proxy_reconcile.py` | 3 / 0 | FAILED tests/test_lpx_proxy_reconcile.py::test_no_join_uses_aggregates - asse... |
| `tests/test_mcip_dat.py` | 1 / 0 | FAILED tests/test_mcip_dat.py::test_content_hash_deterministic - AssertionErr... |
| `tests/test_module_budget_usage_write.py` | 1 / 0 | E AssertionError: period aggregate did not reflect recorded usage: 0.0 -> 0.3023 |
| `tests/test_nav_sec_07_xss.py` | 1 / 0 | E AssertionError: icdev twin drifted: dashboard/templates/base.html |
| `tests/test_nav_sec_08_xss_sweep.py` | 1 / 0 | E AssertionError: icdev twin drifted: dashboard/templates/base.html |
| `tests/test_nova_reflexion_trigger.py` | 3 / 0 | E AssertionError: task.completed event not found in ace_events |
| `tests/test_pdc_routes_engine.py` | 2 / 0 | E assert ('jobs' in {'jobs': {'build': {'name': 'Build', 'needs': ['source'], 'runs-on': 'ubuntu-latest', 'steps': [{'uses': 'actions/chec...an-bandi… |
| `tests/test_pdc_routes_misc.py` | 1 / 0 | E assert 0 == 1 |
| `tests/test_pg_portability_sweep_guard.py` | 1 / 0 | E Failed: 5 unannotated sqlite_master / introspection-PRAGMA probe(s) found in runtime tools/. Replace with tools.db.storage.table_exists()/list_tabl… |
| `tests/test_pma_credential_reflex.py` | 3 / 0 | E assert False is True |
| `tests/test_pma_e2e_vv.py` | 3 / 0 | E assert False is True |
| `tests/test_preapply_gate.py` | 1 / 0 | E AssertionError: Expected pass but got violations: [{'source': 'iqe', 'check': '03_ai_decisions_recent', 'severity': 'CAT3', 'detail': "IQE query er… |
| `tests/test_production_remediate.py` | 3 / 0 | E AssertionError: CMP-003 suggest but no suggestion |
| `tests/test_prov_recorder.py` | 1 / 0 | E AssertionError: 'Secret content' is not None |
| `tests/test_red_cell_anomaly_threshold.py` | 2 / 0 | E AssertionError: Expected threshold clamped to min_floor=6.0, got 7.0 |
| `tests/test_reflex_registration.py` | 2 / 0 | E AssertionError: names in BOTH EXEMPT and REFLEX_NAMES: ['failure_triage', 'oracle_triage'] |
| `tests/test_register_external_patterns.py` | 7 / 0 | E assert 11 == 10 |
| `tests/test_requirement_1.py` | 2 / 0 | E AssertionError: .env.example must define mandatory variable ICDEV_LLM_PROVIDER |
| `tests/test_saml_auth.py` | 1 / 0 | E assert None is not None |
| `tests/test_sdc_lazy_tables.py` | 1 / 0 | FAILED tests/test_sdc_lazy_tables.py::test_inventory_doc_is_fresh - Assertion... |
| `tests/test_self_debug.py` | 1 / 0 | E AssertionError: Expected ok=True, got msg='pruned stale git state for my-task-abc; worktree rebuild failed (Windows branch lock?) � next dispatch w… |
| `tests/test_slides_tables_are_readable.py` | 1 / 0 | E AssertionError: rows were dropped and nothing said so |
| `tests/test_stig_minimal.py` | 1 / 0 | FAILED tests/test_stig_minimal.py::test_validate_workload_checklist - assert ... |
| `tests/test_stig_nlp_extractor.py` | 4 / 0 | E AssertionError: assert 'regex_fallback' == 'nlp_extractor' |
| `tests/test_tenant_admin_api.py` | 5 / 0 | E assert False is True |
| `tests/test_threshold_anomaly_detector.py` | 1 / 0 | E AssertionError: Expected 9999 in [] |
| `tests/test_vault_exporter.py` | 3 / 0 | FAILED tests/test_vault_exporter.py::test_export_contains_context_files - Ass... |
| `tests/test_write_text_newline_discipline.py` | 2 / 0 | E AssertionError: 1 write_text() call(s) under tools/ omit newline="", so they emit CRLF on Windows and every generated file shows up as a whole-file… |
| `tests/test_zig_sql_placeholders.py` | 1 / 0 | E assert 0 == 1 |
| `tests/usage_analytics/test_analytics_engine.py` | 5 / 0 | E AssertionError: record() must return a non-empty event ID on success |

### Missing table or column — 8 module(s)

The DDL and the database the test actually got have diverged. Fix is a migration, or a `MINIMAL_ICDEV_SCHEMA` entry in `tests/conftest.py` — not the test.

| Module | failed / error | First failure recorded |
|---|---:|---|
| `tests/test_bdr_vv_suite.py` | 1 / 0 | E AssertionError: {"control_count":2,"error":"insert: table compliance_snapshots has no column named status","evidence_count":1,"framework_id":"FedRA… |
| `tests/test_crosswalk_integration.py` | 4 / 0 | E sqlite3.OperationalError: table project_framework_status has no column named implemented_count |
| `tests/test_dic_freshness_engine.py` | 4 / 0 | E AssertionError: dic_doc_freshness table missing from MINIMAL_ICDEV_SCHEMA |
| `tests/test_dic_ingest_workflow_mutations.py` | 1 / 0 | E sqlite3.OperationalError: no such table: dic_documents |
| `tests/test_dwo_event_source_matching.py` | 22 / 0 | E AssertionError: {'error': 'no such table: studio_event_sources', 'status': 'error'} |
| `tests/test_dwo_triggers_panel.py` | 6 / 0 | E sqlite3.OperationalError: no such table: studio_workflows |
| `tests/test_ndc_traffic_flow_schema_reconcile.py` | 2 / 0 | E sqlite3.OperationalError: table nc_traffic_flows has no column named source_zone |
| `tests/test_rag_retention.py` | 1 / 0 | E sqlite3.OperationalError: no such table: rag_chunks |

### SQL dialect / placeholder mismatch — 6 module(s)

PostgreSQL `%s` placeholders reaching SQLite (or `?` reaching PG). Fix is in the runtime call site's SQL, per the CLAUDE.md rule that `translate_sql` is an init-fallback and never load-bearing.

| Module | failed / error | First failure recorded |
|---|---:|---|
| `tests/test_aca_pg_native_placeholders.py` | 1 / 0 | E AssertionError: blueprint.py builds SQL fragments with `?`: [(1345, 'Export Academy completions as xAPI 1.0.3 statements (aca-trn-05).\n\n ')] |
| `tests/test_bm25_translation.py` | 1 / 0 | E AssertionError: assert 'SELECT snipp...ERE t MATCH ?' == 'SELECT snipp...RE t MATCH %s' |
| `tests/test_idc_twin_phase1.py` | 11 / 0 | E sqlite3.OperationalError: near "%": syntax error |
| `tests/test_saas_llm_keys.py` | 10 / 0 | E sqlite3.OperationalError: near "%": syntax error |
| `tests/test_saas_portal.py` | 2 / 0 | E sqlite3.OperationalError: near "%": syntax error |
| `tests/test_traffic_flow_walkthrough.py` | 6 / 0 | E sqlite3.OperationalError: near "%": syntax error |

### Unauthenticated test client (401 / 403 / CSRF) — 5 module(s)

The route is reachable but the test client carries no session or CSRF token. Fixture-shaped — it says nothing about the behaviour under test.

| Module | failed / error | First failure recorded |
|---|---:|---|
| `tests/e2e/test_derivative_classifier_lifecycle.py` | 4 / 9 | E AssertionError: Preview failed 403: {"code":"CSRF_FAILED","error":"CSRF token missing or invalid","message":"This request requires a valid CSRF tok… |
| `tests/test_proposals_detail_action_bar.py` | 9 / 0 | E assert 401 == 200 |
| `tests/test_proposals_detail_ai_drafts_tab.py` | 10 / 0 | E assert 401 == 200 |
| `tests/test_proposals_list_cui_banner.py` | 4 / 0 | E assert 401 == 200 |
| `tests/test_proposals_ptw_blackhat_api.py` | 10 / 0 | E assert 401 == 400 |

### DB driver error, reason truncated — 5 module(s)

A `sqlite3.*` / `psycopg.*` exception whose reason pytest cut out of the short summary line. NOT counted as schema drift or as a dialect bug: those are different fixes and the recorded evidence does not distinguish them. Re-run the module alone to resolve it.

| Module | failed / error | First failure recorded |
|---|---:|---|
| `tests/test_cnr_migration_intel.py` | 1 / 0 | FAILED tests/test_cnr_migration_intel.py::test_mi_db_writes_postgresql - psyc... |
| `tests/test_cross_register.py` | 6 / 0 | FAILED tests/test_cross_register.py::test_pull_normalizes_one_row - sqlite3.O... |
| `tests/test_portfolio_greeks.py` | 4 / 0 | FAILED tests/test_portfolio_greeks.py::test_empty_portfolio - sqlite3.Operati... |
| `tests/test_proposals_annotations.py` | 6 / 0 | FAILED tests/test_proposals_annotations.py::test_delete_annotation - sqlite3.... |
| `tests/test_signal_decay.py` | 1 / 0 | FAILED tests/test_signal_decay.py::test_get_signals_ranked_ordering - sqlite3... |

### Errored in setup, never asserted — 5 module(s)

The module produced more pytest ERRORs than failures, so its tests never reached an assertion. The fixture is the defect.

| Module | failed / error | First failure recorded |
|---|---:|---|
| `tests/test_dic_techwriter.py` | 0 / 1 | ERROR tests/test_dic_techwriter.py::test_import_from_docgen_valid_template_type_returns_500_or_doc_id |
| `tests/test_govlift_runbook_engine.py` | 1 / 17 | ERROR tests/test_govlift_runbook_engine.py::test_create_runbook_returns_id - ... |
| `tests/test_odc_api_hygiene.py` | 0 / 1 | ERROR tests/test_odc_api_hygiene.py::test_update_unknown_id_returns_404_known_succeeds |
| `tests/test_pipeline_snapshot_db.py` | 0 / 11 | ERROR tests/test_pipeline_snapshot_db.py::test_create_snapshot_returns_id - F... |
| `tests/test_zig_external_targets.py` | 0 / 1 | ERROR tests/test_zig_external_targets.py::test_unique_index_rejects_duplicate_activity_target_pair |

### Import / missing attribute — 4 module(s)

The module does not import, or the attribute the test patches no longer exists. Batchable: one import fix commonly clears several files.

| Module | failed / error | First failure recorded |
|---|---:|---|
| `tests/test_engine_nlp_input_classifier.py` | 19 / 0 | E AttributeError: module 'tools.ai_augmentation.engine' has no attribute '_classify_input_ref_nlp' |
| `tests/test_log_triage.py` | 0 / 13 | ERROR tests/test_log_triage.py::TestRun::test_run_no_log_file - ImportError: ... |
| `tests/test_memory_classification.py` | 1 / 0 | E ModuleNotFoundError: No module named 'migration' |
| `tests/test_session_purpose.py` | 1 / 0 | E AttributeError: <module 'tools.agent.session_purpose' from 'C:\\AI\\ICDev\\.tmp\\worktrees\\rem-tst-01\\tools\\agent\\session_purpose.py'> does not… |

### Other runtime exception — 4 module(s)

A non-assertion exception that is not a DB driver error — an unavailable provider, a KeyError on a response, a domain error raised by the code under test.

| Module | failed / error | First failure recorded |
|---|---:|---|
| `tests/test_dcpr_product_registry.py` | 1 / 0 | FAILED tests/test_dcpr_product_registry.py::test_subscribe_and_approve - KeyE... |
| `tests/test_rag_retriever.py` | 2 / 0 | E tools.rag.retriever.EmbeddingUnavailableError: no embedding provider is available � the query was never embedded and no backend was searched |
| `tests/test_siem_alert_forwarder.py` | 1 / 0 | E tools.siem_alert_forwarder.SIEMLatencyExceededError: SIEM delivery latency 0.13 ms exceeds SLA of 5 s |
| `tests/testing/test_health_check.py` | 2 / 0 | E KeyError: 'tables_found' |

### No usable signature recorded — 2 module(s)

The census recorded a first-failure line with the reason truncated away entirely. These need a re-run before they can be grouped; they are reported rather than distributed, because a classifier that always finds a bucket is not measuring.

| Module | failed / error | First failure recorded |
|---|---:|---|
| `tests/test_devops_twin_route.py` | 4 / 0 | FAILED tests/test_devops_twin_route.py::test_twin_list_empty_returns_200 - ji... |
| `tests/test_infra_twin_route.py` | 3 / 0 | FAILED tests/test_infra_twin_route.py::test_twin_snapshot_summary_rendered - ... |

## Neither green nor failing

Carried separately so the group arithmetic above stays exactly the census's `failed` count. These are not promotable either, and they are not the same problem.

| Status | Files | Modules |
|---|---:|---|
| `timeout` | 6 | `tests/test_coherence_checker.py`, `tests/test_dashboard_auth.py`, `tests/test_guardrails.py`, `tests/test_heartbeat_daemon.py`, `tests/test_marketplace_publish.py`, `tests/test_workflow_loop.py` |
| `no-tests` | 2 | `tests/test_mcp_http_e2e.py`, `tests/trading/test_data_quality.py` |
| `collection-error` | 0 | — |

## Recorded red here, gated since

1 module(s) in the groups above are already on `args/ci_test_files/*.txt`. That is not a breach of the promotion rule — it is the census being a SNAPSHOT. Each was fixed and gated in the PR that fixed it, after the census ran, so its row here is stale rather than outstanding. They are named instead of filtered out, because dropping them would make this report's counts stop matching the census's.

- `tests/git/test_manifest_merge_rehearsal.py`

## What this does NOT say

- **It is not a diagnosis.** The group is the shape of the FIRST recorded failure line. A module in `assertion` may also be carrying a schema problem in its second failure; only the first one was recorded.
- **It is a snapshot.** These verdicts are the census run's, measured ALONE. A module here may have been fixed since, and none of them was measured in-suite.
- **`db-error-unspecified` is not a small `schema-drift`.** pytest truncated the reason out of the short summary. Re-run those modules alone before filing them.
- **No module in this report was gated.** Promotion batches take `passed` rows only; adding a red file to `args/ci_test_files/core.txt` turns `main` red, and a red `main` gets the gate switched off.

## Reproducing this

```bash
python tools/ci/ungated_test_census.py --red-report docs/testing/ungated_test_census.json \
  --red-md docs/testing/ungated_red_modules.md
```

Exit 1 if the group counts do not sum to the census's own `failed` count. That is the only thing this mode gates on: a red report whose arithmetic has drifted from the census is worse than no report.
