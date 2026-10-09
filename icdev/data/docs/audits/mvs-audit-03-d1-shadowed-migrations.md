# CUI // SP-CTI

# mvs-audit-03-d1 — the grandfathered shadowed migrations, enumerated

`args/migration_duplicate_versions.yaml` freezes the migration-version
collisions that already existed when the gate in
`tools/db/migration_versions.py` was added. It records them as
`version -> [entry, entry, ...]`, which is not directly readable as "what never
runs": the runner (`MigrationRunner.get_pending_migrations`) dedupes by version
and keeps the FIRST entry by sort order, so only the second and later siblings of
each version are shadowed.

This audit resolves the file into one row per shadowed migration.

## Deliverables

| File | Contents |
|------|----------|
| [mvs-audit-03-d1-shadowed-migrations.json](mvs-audit-03-d1-shadowed-migrations.json) | `metadata` block + 60 rows of `shadowed_migration` / `version` / `shadowed_by` |
| [mvs-audit-03-d1-shadowed-migrations.csv](mvs-audit-03-d1-shadowed-migrations.csv) | the same 60 rows, three columns, no metadata |

## Counts

- **48** duplicated version numbers
- **108** migration entries claim one of those versions
- **60** of those entries are shadowed and never run

Cross-checked two independent ways, which agree exactly (empty symmetric
difference on the `(version, shadowed, applied)` triple):

1. parsing `args/migration_duplicate_versions.yaml` and applying the
   keep-the-first-by-sort-order rule ourselves;
2. `tools.db.migration_versions.shadowed_migrations()`, which scans
   `tools/db/migrations/` on disk and ignores the allowlist entirely.

So the allowlist is currently an accurate picture of the tree — no collision has
been introduced without being grandfathered, and no grandfathered collision has
since been renumbered away.

## The file's own header comment is stale on `main`

`args/migration_duplicate_versions.yaml` line 17 currently reads:

```
# 53 duplicated versions; 70 migrations shadowed.
```

Both numbers are wrong for the data in the same file — it holds 48 versions and
60 shadowed entries, and the live scan confirms 48/60. Anyone sizing the
remediation backlog off that comment overstates it by ten migrations. Read the
data, not the comment.

Already fixed on the open branch below, so this audit does not touch it.

## Prior art — read this before re-investigating

PR **#1296** (`feat/mvs-audit-03-shadowed-audit`, task `mvs-audit-03`) is **open
and unmerged as of 2026-08-07**. It contains a much deeper pass over the same 60
entries: each one rewritten as `name: "<reason>"` with a verdict from rebuilding
both backends from empty (42 benign, 9 not-actually-shadowed, 3 no-DDL, 6 real
schema gaps since fixed), a `tools/db/shadowed_migration_audit.py` to re-derive
it, and a `migration_versions.check()::unexplained_entries` gate that fails on a
reason-less entry.

None of that is on `main`, which is why `main`'s allowlist is still a bare list
of names and why this task was answerable at all. The enumeration here is the
three-field view of `main` as it stands; when #1296 merges, its inline reasons
supersede this file for the "is it harmless?" question, and this file remains
useful only as the flat machine-readable join key.

## What is shadowed

`shadowed_by` is the earlier same-version sibling that wins dedupe and is the
one that actually runs.

| Version | Shadowed migration (never runs) | Shadowed by (runs) |
|---------|---------------------------------|--------------------|
| 10 | `010_network_intelligence_schema` | `010_kanban_executor_schema` |
| 18 | `018_reflex_observations.py` | `018_memory_db_consolidation` |
| 19 | `019_kanban_verifications` | `019_backlog_task_reassign.py` |
| 20 | `020_nc_topologies_schema.py` | `020_kanban_failure_count` |
| 20 | `020_options_coach_events` | `020_kanban_failure_count` |
| 21 | `021_dispatch_source` | `021_ad_macro_regimes` |
| 21 | `021_sg_sigint_events.py` | `021_ad_macro_regimes` |
| 22 | `022_sg_eo_signals.py` | `022_ad_event_stack_tables` |
| 23 | `023_sg_socmint_signals.py` | `023_ad_news_patterns` |
| 24 | `024_telegram_inbox.py` | `024_ad_news_catalysts` |
| 27 | `027_compliance_snapshots` | `027_ad_coach_alerts` |
| 28 | `028_idc_infra_tables` | `028_attack_graph` |
| 31 | `031_network_twin_snapshots` | `031_ddc_twin_tables` |
| 43 | `043_memory_fingerprint` | `043_memory_entity_relationships` |
| 50 | `050_theater_supply_chain` | `050_sg_sio_assessments` |
| 52 | `052_sg_raw_signals` | `052_sg_conflict_events` |
| 55 | `055_sg_conflict_events_cyber_op` | `055_cta_scores_cache.sql` |
| 55 | `055_sg_information_signals` | `055_cta_scores_cache.sql` |
| 56 | `056_sg_prioritized_signals` | `056_historical_cases` |
| 56 | `056_win_loss_analysis_tables.sql` | `056_historical_cases` |
| 57 | `057_sg_raw_signals_processed` | `057_ad_backtest_runs.sql` |
| 57 | `057_sg_sc_graph` | `057_ad_backtest_runs.sql` |
| 57 | `057_sg_signals_actions` | `057_ad_backtest_runs.sql` |
| 64 | `064_sg_pattern_learner_log` | `064_sg_hitl_items_type` |
| 78 | `078_workflow_hitl` | `078_ad_decision_audit` |
| 83 | `083_sg_multidomain_tracks` | `083_cyber_ext_columns` |
| 84 | `084_wne_sessions` | `084_aisg_wizard.sql` |
| 85 | `085_aisg_learning_tracks` | `085_aadc_versions.sql` |
| 85 | `085_sg_ccir_trigger_events` | `085_aadc_versions.sql` |
| 86 | `086_sg_intsums` | `086_aadc_events.sql` |
| 107 | `107_sg_theaters` | `107_aadc_phase5.sql` |
| 108 | `108_sg_war_council_briefs` | `108_aadc_phase6.sql` |
| 113 | `113_kanban_vibe_tier1` | `113_aadc_compliance.sql` |
| 120 | `120_ops_hub` | `120_kanban_alert_queue` |
| 135 | `135_sdc_designs` | `135_ohc_runbooks` |
| 136 | `136_qdc_metrics` | `136_gameday_ai_league` |
| 139 | `139_govlift_map_assessment.sql` | `139_fisma_ir` |
| 139 | `139_govlift_rbac_roles` | `139_fisma_ir` |
| 139 | `139_mfa_enforcement` | `139_fisma_ir` |
| 139 | `139_qdc_metrics.sql` | `139_fisma_ir` |
| 158 | `158_sg_leadership_briefs` | `158_conflict_predictions` |
| 161 | `161_sdc_rag_stigs` | `161_sdc_compliance_timeline` |
| 163 | `163_groups_canvas_access.sql` | `163_domain_coverage` |
| 173 | `173_white_team_review_type.py` | `173_cpmp_obligation_periods.py` |
| 179 | `179_kanban_task_revivals.py` | `179_integrity_tables.py` |
| 184 | `184_creative_gap_innovation_signal.sql` | `184_coworkers_canvas_tables.sql` |
| 184 | `184_memory_fts5` | `184_coworkers_canvas_tables.sql` |
| 188 | `188_genesis_phase_log.sql` | `188_genesis_outputs` |
| 189 | `189_genesis_phase_log` | `189_dd_pii_scans.sql` |
| 207 | `207_tenant_component_overrides` | `207_mcip_dat_tables.sql` |
| 210 | `210_sso` | `210_showcase_apps.sql` |
| 212 | `212_idr_suggested_classification.sql` | `212_data_residency` |
| 215 | `215_user_preferences` | `215_api_keys` |
| 223 | `223_user_identity.sql` | `223_agent_evals.sql` |
| 223 | `223_wfc_doc_regen.sql` | `223_agent_evals.sql` |
| 236 | `236_rfi_workbench.sql` | `236_personal_rag.sql` |
| 247 | `247_dashboard_users_role_check` | `247_cpmp_int_coverage_tenant_id.sql` |
| 257 | `257_idr_dic_doc_link.sql` | `257_doc_modernization.sql` |
| 269 | `269_kg_empty_graph_counts.sql` | `269_kg_embedding_vec_backfill` |
| 282 | `282_insider_risk_uba.sql` | `282_docmod_nist_pubs.sql` |

## Regenerating

```bash
python tools/db/migration_versions.py --shadowed --json
```

That command reads the filesystem, not the allowlist, so it is the check to run
when asking whether this audit has drifted.
