# TSR COMP — full-slice before/after verification (tsr-comp-01-d5)

Verification only. No test or source file was modified by this task. Produced 2026-08-01 on branch
`kanban/tsr-comp-01-d5` at `9b31d9560`.

Closes out the COMP epic by re-measuring the whole 152-file compliance/security slice with the epic's
changes in place and with them removed, so every claimed fix is backed by a number.

## Headline

| | files clean | files with ≥1 failing test | tests passing | tests failing |
|---|---:|---:|---:|---:|
| **before** (epic reverted) | 132 | **20** | 2,669 | **130** |
| **after** (epic applied) | **148** | **4** | **2,779** | **24** |
| delta | +16 | **−16** | **+110** | **−106** |

**106 of the 130 baseline failures are cleared, across 16 files. Zero regressions** — no file's failure
count rose, and no file that passed before fails now.

The before arm reproduces the `tsr-comp-01-d1` baseline exactly (132 clean / 20 failing / 2,669 passing
/ 130 failing). That the two independent measurements agree to the test is the evidence that this
sweep's method is equivalent to the baseline's, so the two are safely comparable.

Counting the 3 files the epic touched that sit outside the slice, the epic clears **116 failures
across 19 files**.

## Method

### "Stash / unstash", expressed against commits

The task specifies stashing changes to re-baseline. There was nothing to stash — the COMP work is
already committed and merged (#1058, #1084, and commits `70662693a`, `9b9dd48fe`). The equivalent, and
what was actually run, is a targeted revert:

```bash
git restore --source=5766e4748 -- <the 22 files the epic touched>   # before arm
git restore --source=HEAD      -- <the same 22 files>               # after arm
```

`5766e4748` is the commit the d1 baseline was measured at. This is a sound isolation because **each of
the 22 files has changed exactly once since that commit, and every one of those changes is a COMP-epic
commit** — verified with `git log 5766e4748..HEAD --no-merges -- <file>`. Nothing unrelated is
reverted along with the epic, and nothing of the epic is left behind.

The 22 files are 18 test files plus 4 source files —
`tools/config/core_profile.py`, `tools/devsecops/zta_maturity_scorer.py` and their `icdev/` mirrors.
Reverting the source files too is what makes the comparison honest: `test_ecr_tier.py` was never edited
by the epic but is fixed by it, because the `license_tier` repair landed in `core_profile.py`. A
test-files-only revert would have silently mis-attributed that file as "already passing".

### Execution

Both arms ran the identical 152-file list (`tsr-comp-01-slice.txt`), one file per pytest process:

```bash
python -m pytest <file> --junit-xml=<out> --tb=no -q \
  --timeout=90 --timeout-method=thread --continue-on-collection-errors -p no:cacheprovider
```

with `PYTHONPATH` set to an absolute repo root and `ICDEV_STORAGE_BACKEND=sqlite`, plus a 240 s
**outer** wall-clock bound per file. `ICDEV_DB_PATH` is explicitly unset in the child environment — a
leaked value redirects every read to a dead tmpdir and fakes `no such table`.

Per-file processes, rather than chunks, are what let the d1 report's two traps be avoided outright: a
chunk killed by an outer timeout and a collection error both emit zero `<testcase>` elements, and the
baseline conflated them into 6 false "broken" files. Here a timeout is its own status and can only ever
implicate the one file that caused it.

**Both arms start from a byte-identical DB.** The worktree was seeded once
(`init_icdev_db.py` → `studio/init_db.py` → migration 311, with the `sqlite` pin, giving 541 tables),
that file was snapshotted, and the snapshot was restored before each arm. Without this the second arm
would inherit the first arm's mutations and the comparison would not be like-for-like.

Timings: 634 s for the after arm, 685 s for the before arm.

## The 4 residual failures — every one categorised

All four are **pre-existing defects the epic did not claim to fix**: each fails identically in both
arms (`Δ fail = 0`), and each was already named in the d1 baseline. None is a regression.

### 1. `tests/test_integrity_monitor_reflex.py` — 2 failures — *test/implementation contract disagreement*

```
test_rel_path_strips_quarantine_prefix    AssertionError: assert 'somewhere/else/y.py' == 'y.py'
test_rel_path_dirs_flag_preserves_directories
                                          AssertionError: assert 'tools/sipa/canvas_compliance/posture.py' == 'posture.py'
```

`_rel_path` returns a full relative path where the test expects a bare basename. The two disagree about
the function's contract, so this needs a decision on which is correct — not a mechanical fix. Fixing it
would mean changing behaviour, which is outside a verification task's remit.

### 2. `tests/test_nav_misc_05_evidence_page.py` — 2 errors — *unrunnable under the suite's own config*

```
tools.db.backend_guard.SqliteServerRefused: ICDEV dashboard refuses to start:
ICDEV_STORAGE_BACKEND=sqlite but this install sets ICDEV_PG_NO_FALLBACK=true.
```

The test starts the dashboard; `backend_guard.py` refuses to start it on SQLite; `tests/conftest.py`
unconditionally sets `ICDEV_STORAGE_BACKEND=sqlite`. **The test cannot pass in the default suite
configuration as written** — it needs PostgreSQL (`ICDEV_PYTEST_PG`) or an explicit
`ICDEV_ALLOW_SQLITE_SERVER=1`. This is a genuine defect in the test's environment assumptions, not an
artefact of how this sweep was run, and it is the one residual whose fix is a config decision rather
than a code change.

### 3. `tests/test_proposals_ptw_blackhat_api.py` — 10 failures — *one unauthenticated test client*

```
test_requires_vendor_name        assert 401 == 400
test_profiles_vendor             assert 401 == 200
test_create_then_list_round_trip assert 401 == 201
test_update_blackhat_assessment  TypeError: 'NoneType' object is not subscriptable
test_list_ordered_most_recent_first  IndexError: list index out of range
```

A single root cause, not ten. The test client is not authenticated, so every request returns `401`;
five tests assert the status directly, and the other five then subscript a response body that is
`None`. The d1 baseline recorded only the downstream `TypeError` signature and so under-described this
— the `401` is the actual fault. Repairing the auth fixture should clear all ten.

### 4. `tests/test_xai_assessor.py` — 10 failures — *assessor asserts against data a clean DB lacks*

```
test_xai001_tracing_active     AssertionError: 'not_satisfied' != 'satisfied'
test_xai007_decisions_exist    AssertionError: 'not_assessed'  != 'satisfied'
test_db_error_returns_not_assessed
                               AssertionError: 'not_satisfied' != 'not_assessed'
```

The checks query telemetry, provenance, SHAP and trust-score tables and expect `satisfied`; a freshly
seeded DB has those tables empty, so the assessor correctly returns `not_satisfied` / `not_assessed`.
This is the ambient-data-dependent category. It is also the one file d1 flagged as environment-sensitive
(10 failing in a clean worktree vs 9 in the populated shared checkout), and this sweep reproduces the
clean-worktree number exactly. The fix is to seed the fixtures the assessor reads, not to relax the
assertions.

## Lint

`ruff 0.15.1` — **clean, no findings, nothing to fix**:

- all 152 files in the slice → `All checks passed!`
- all 22 files the epic touched (18 test + 4 source) → `All checks passed!`

`ruff check --fix` was therefore a no-op and no formatting change is included in this PR.

## Note on where this was measured

The sweeps ran in a worktree at `.tmp/worktrees/tsr-comp-01-d5`, **inside** the repo tree. Partway
through, another session pruned that worktree; its `.git` file vanished and git commands from that
directory silently began resolving against the shared checkout on `main` instead — which is why the
post-sweep `git restore` reported `pathspec 'tests/' did not match any file(s) known to git` rather
than restoring anything.

No measurement is affected: both arms had already completed and their JSON lives outside the repo. The
work was moved to a fresh worktree at `C:\AI\.worktrees\tsr-comp-01-d5-r2`, **outside** the repo tree
as `CLAUDE.md` requires, and the 4 residual failures were re-run there and reproduced identically
before this report was written. The practical lesson is the placement rule: a worktree inside `.tmp/`
degrades to the shared checkout when removed, instead of failing loudly.

## Per-file before/after counts — all 152 files

`fail` = failed + errored tests. Rows in **bold** are the 16 files whose numbers moved; all 16 moved
downwards.

| # | file | before pass | before fail | after pass | after fail | Δ fail |
|--:|------|------------:|------------:|-----------:|-----------:|-------:|
| 1 | `tests/cortex/test_audit_persistence.py` | 19 | 0 | 19 | 0 | 0 |
| 2 | `tests/e2e/test_integrity_backdoor_quarantine.py` | 0 | **5** | 5 | **0** | **-5** |
| 3 | `tests/genesis_auto/test_classification_manager.py` | 41 | 0 | 41 | 0 | 0 |
| 4 | `tests/genesis_auto/test_cmmc_assessor.py` | 6 | 0 | 6 | 0 | 0 |
| 5 | `tests/genesis_auto/test_digital_thread.py` | 27 | 0 | 27 | 0 | 0 |
| 6 | `tests/genesis_auto/test_fedramp_assessor.py` | 6 | 0 | 6 | 0 | 0 |
| 7 | `tests/genesis_auto/test_ivv_assessor.py` | 5 | 0 | 5 | 0 | 0 |
| 8 | `tests/genesis_auto/test_ivv_report_generator.py` | 3 | 0 | 3 | 0 | 0 |
| 9 | `tests/genesis_auto/test_oscal_generator.py` | 15 | 0 | 15 | 0 | 0 |
| 10 | `tests/genesis_auto/test_pi_compliance_tracker.py` | 18 | 0 | 18 | 0 | 0 |
| 11 | `tests/genesis_auto/test_reqif_parser.py` | 16 | 0 | 16 | 0 | 0 |
| 12 | `tests/genesis_auto/test_sbd_assessor.py` | 5 | 0 | 5 | 0 | 0 |
| 13 | `tests/genesis_auto/test_sync_engine.py` | 18 | 0 | 18 | 0 | 0 |
| 14 | `tests/genesis_auto/test_xmi_parser.py` | 12 | 0 | 12 | 0 | 0 |
| 15 | `tests/genesis_auto/test_zta_terraform_generator.py` | 15 | 0 | 15 | 0 | 0 |
| 16 | `tests/http/test_fetch_extract.py` | 25 | 0 | 25 | 0 | 0 |
| 17 | `tests/security/test_app_red_team.py` | 21 | 0 | 21 | 0 | 0 |
| 18 | `tests/security/test_llm_red_team.py` | 23 | 0 | 23 | 0 | 0 |
| 19 | `tests/security/test_reproduction.py` | 17 | 0 | 17 | 0 | 0 |
| 20 | `tests/security/test_sandbox_smoke.py` | 9 | 0 | 9 | 0 | 0 |
| 21 | `tests/studio/test_mcp_executor_audit.py` | 11 | 0 | 11 | 0 | 0 |
| 22 | `tests/studio/test_mcp_executor_rbac.py` | 34 | 0 | 34 | 0 | 0 |
| 23 | `tests/test_abac_cross_domain.py` | 20 | 0 | 20 | 0 | 0 |
| 24 | `tests/test_abac_engine.py` | 15 | 0 | 15 | 0 | 0 |
| 25 | `tests/test_abac_pip_ownership.py` | 11 | 0 | 11 | 0 | 0 |
| 26 | `tests/test_accountability_manager.py` | 4 | **21** | 25 | **0** | **-21** |
| 27 | `tests/test_ace_behavioral_monitor.py` | 17 | 0 | 17 | 0 | 0 |
| 28 | `tests/test_ace_skill_promoter.py` | 13 | 0 | 13 | 0 | 0 |
| 29 | `tests/test_agent_output_validator.py` | 21 | 0 | 21 | 0 | 0 |
| 30 | `tests/test_agent_trust_scorer.py` | 21 | 0 | 21 | 0 | 0 |
| 31 | `tests/test_aggregation_guard.py` | 11 | 0 | 11 | 0 | 0 |
| 32 | `tests/test_ai_accountability_audit.py` | 20 | 0 | 20 | 0 | 0 |
| 33 | `tests/test_ai_bom_generator.py` | 14 | 0 | 14 | 0 | 0 |
| 34 | `tests/test_ai_reassessment_scheduler.py` | 18 | 0 | 18 | 0 | 0 |
| 35 | `tests/test_ai_telemetry.py` | 12 | 0 | 12 | 0 | 0 |
| 36 | `tests/test_ai_transparency.py` | 35 | 0 | 35 | 0 | 0 |
| 37 | `tests/test_assessor_accountability_fixes.py` | 24 | 0 | 24 | 0 | 0 |
| 38 | `tests/test_atlas_assessor.py` | 11 | 0 | 11 | 0 | 0 |
| 39 | `tests/test_atlas_red_team.py` | 10 | 0 | 10 | 0 | 0 |
| 40 | `tests/test_audit_posture.py` | 11 | 0 | 11 | 0 | 0 |
| 41 | `tests/test_bdr_supply_chain_crosslink.py` | 8 | 0 | 8 | 0 | 0 |
| 42 | `tests/test_behavioral_drift.py` | 13 | 0 | 13 | 0 | 0 |
| 43 | `tests/test_behavioral_red_team.py` | 13 | 0 | 13 | 0 | 0 |
| 44 | `tests/test_blueprint_verifier.py` | 22 | 0 | 22 | 0 | 0 |
| 45 | `tests/test_canvas_access.py` | 1 | **12** | 13 | **0** | **-12** |
| 46 | `tests/test_canvas_access_integration.py` | 1 | **16** | 17 | **0** | **-16** |
| 47 | `tests/test_citation_promote_gate.py` | 15 | 0 | 15 | 0 | 0 |
| 48 | `tests/test_classification_enforcer.py` | 22 | 0 | 22 | 0 | 0 |
| 49 | `tests/test_cnr_plat.py` | 15 | 0 | 15 | 0 | 0 |
| 50 | `tests/test_code_pattern_scanner.py` | 41 | 0 | 41 | 0 | 0 |
| 51 | `tests/test_column_security.py` | 14 | 0 | 14 | 0 | 0 |
| 52 | `tests/test_column_security_config_cache.py` | 6 | 0 | 6 | 0 | 0 |
| 53 | `tests/test_column_security_pg.py` | 14 | 0 | 14 | 0 | 0 |
| 54 | `tests/test_compliance_exporter.py` | 27 | 0 | 27 | 0 | 0 |
| 55 | `tests/test_component_registry.py` | 40 | 0 | 40 | 0 | 0 |
| 56 | `tests/test_confabulation_wiring.py` | 5 | 0 | 5 | 0 | 0 |
| 57 | `tests/test_container_scanner.py` | 30 | 0 | 30 | 0 | 0 |
| 58 | `tests/test_continuous_auth.py` | 17 | 0 | 17 | 0 | 0 |
| 59 | `tests/test_credential_broker.py` | 22 | 0 | 22 | 0 | 0 |
| 60 | `tests/test_crosswalk_integration.py` | 31 | 0 | 31 | 0 | 0 |
| 61 | `tests/test_crx_insider_risk.py` | 6 | 0 | 6 | 0 | 0 |
| 62 | `tests/test_cve_passive_watcher.py` | 23 | 0 | 23 | 0 | 0 |
| 63 | `tests/test_denylist_seeder.py` | 7 | 0 | 7 | 0 | 0 |
| 64 | `tests/test_dependency_auditor.py` | 2 | 0 | 2 | 0 | 0 |
| 65 | `tests/test_dependency_graph.py` | 22 | 0 | 22 | 0 | 0 |
| 66 | `tests/test_device_trust.py` | 12 | 0 | 12 | 0 | 0 |
| 67 | `tests/test_devsecops_profile.py` | 25 | 0 | 25 | 0 | 0 |
| 68 | `tests/test_dic_confabulation.py` | 4 | 0 | 4 | 0 | 0 |
| 69 | `tests/test_docgen.py` | 133 | 0 | 133 | 0 | 0 |
| 70 | `tests/test_dsyn_emit_compliance.py` | 2 | **4** | 6 | **0** | **-4** |
| 71 | `tests/test_dsyn_emit_devsecops.py` | 1 | **4** | 5 | **0** | **-4** |
| 72 | `tests/test_dsyn_emit_sipa.py` | 4 | 0 | 4 | 0 | 0 |
| 73 | `tests/test_dsyn_emit_zig.py` | 0 | **4** | 4 | **0** | **-4** |
| 74 | `tests/test_dsyn_vv_smoke.py` | 19 | 0 | 19 | 0 | 0 |
| 75 | `tests/test_ecr_dres.py` | 9 | **3** | 12 | **0** | **-3** |
| 76 | `tests/test_ecr_soc2.py` | 5 | **2** | 7 | **0** | **-2** |
| 77 | `tests/test_ecr_tier.py` | 11 | **1** | 12 | **0** | **-1** |
| 78 | `tests/test_egress_policy_manager.py` | 20 | 0 | 20 | 0 | 0 |
| 79 | `tests/test_endpoint_security_scanner.py` | 44 | 0 | 44 | 0 | 0 |
| 80 | `tests/test_event_bus_canvas_rls.py` | 2 | 0 | 2 | 0 | 0 |
| 81 | `tests/test_evidence_collector.py` | 13 | 0 | 13 | 0 | 0 |
| 82 | `tests/test_field_security.py` | 14 | 0 | 14 | 0 | 0 |
| 83 | `tests/test_gao_ai_assessor.py` | 22 | 0 | 22 | 0 | 0 |
| 84 | `tests/test_group_manager.py` | 1 | **12** | 13 | **0** | **-12** |
| 85 | `tests/test_integrity_blueprint.py` | 15 | 0 | 15 | 0 | 0 |
| 86 | `tests/test_integrity_capabilities.py` | 24 | 0 | 24 | 0 | 0 |
| 87 | `tests/test_integrity_claim_parser.py` | 14 | 0 | 14 | 0 | 0 |
| 88 | `tests/test_integrity_engine.py` | 20 | 0 | 20 | 0 | 0 |
| 89 | `tests/test_integrity_engine_hitl.py` | 8 | 0 | 8 | 0 | 0 |
| 90 | `tests/test_integrity_event_emitter.py` | 2 | 0 | 2 | 0 | 0 |
| 91 | `tests/test_integrity_ingest.py` | 6 | 0 | 6 | 0 | 0 |
| 92 | `tests/test_integrity_ingest_git.py` | 11 | 0 | 11 | 0 | 0 |
| 93 | `tests/test_integrity_intent_reconciler.py` | 45 | 0 | 45 | 0 | 0 |
| 94 | `tests/test_integrity_mcp.py` | 10 | 0 | 10 | 0 | 0 |
| 95 | `tests/test_integrity_monitor_reflex.py` | 11 | 2 | 11 | 2 | 0 |
| 96 | `tests/test_integrity_pr_gates.py` | 10 | 0 | 10 | 0 | 0 |
| 97 | `tests/test_integrity_provenance.py` | 8 | 0 | 8 | 0 | 0 |
| 98 | `tests/test_integrity_scanners.py` | 35 | 0 | 35 | 0 | 0 |
| 99 | `tests/test_integrity_scoring.py` | 11 | 0 | 11 | 0 | 0 |
| 100 | `tests/test_integrity_skillspector_cache.py` | 13 | 0 | 13 | 0 | 0 |
| 101 | `tests/test_integrity_tamper.py` | 7 | 0 | 7 | 0 | 0 |
| 102 | `tests/test_mcp_tool_authorizer.py` | 23 | 0 | 23 | 0 | 0 |
| 103 | `tests/test_model_card_generator.py` | 20 | 0 | 20 | 0 | 0 |
| 104 | `tests/test_narrative_generator.py` | 16 | 0 | 16 | 0 | 0 |
| 105 | `tests/test_nav_comp_01_fedramp_scope.py` | 13 | 0 | 13 | 0 | 0 |
| 106 | `tests/test_nav_comp_06_attribution_scoring.py` | 14 | 0 | 14 | 0 | 0 |
| 107 | `tests/test_nav_misc_05_evidence_page.py` | 0 | 2 | 0 | 2 | 0 |
| 108 | `tests/test_ndaa_889_screener.py` | 46 | 0 | 46 | 0 | 0 |
| 109 | `tests/test_nist_ai_600_1_assessor.py` | 22 | 0 | 22 | 0 | 0 |
| 110 | `tests/test_omb_m25_21_assessor.py` | 20 | 0 | 20 | 0 | 0 |
| 111 | `tests/test_omb_m26_04_assessor.py` | 21 | 0 | 21 | 0 | 0 |
| 112 | `tests/test_oscal_tools.py` | 56 | 0 | 56 | 0 | 0 |
| 113 | `tests/test_owasp_agentic_assessor.py` | 16 | 0 | 16 | 0 | 0 |
| 114 | `tests/test_packaged_tools_imports.py` | 10 | 0 | 10 | 0 | 0 |
| 115 | `tests/test_pdp_client.py` | 19 | 0 | 19 | 0 | 0 |
| 116 | `tests/test_penta_aimc_p2.py` | 8 | 0 | 8 | 0 | 0 |
| 117 | `tests/test_pgrt_evidence_chain_pg.py` | 3 | 0 | 3 | 0 | 0 |
| 118 | `tests/test_phase36_phase37_integration.py` | 17 | 0 | 17 | 0 | 0 |
| 119 | `tests/test_placeholder_promote_gate.py` | 19 | 0 | 19 | 0 | 0 |
| 120 | `tests/test_poam_auto_generator.py` | 6 | **9** | 15 | **0** | **-9** |
| 121 | `tests/test_prompt_injection_detector.py` | 47 | 0 | 47 | 0 | 0 |
| 122 | `tests/test_prop_fix_12_rls_reads.py` | 5 | 0 | 5 | 0 | 0 |
| 123 | `tests/test_proposal_genesis_winloss_mac.py` | 6 | 0 | 6 | 0 | 0 |
| 124 | `tests/test_proposals_ptw_blackhat_api.py` | 10 | 10 | 10 | 10 | 0 |
| 125 | `tests/test_ptw_masking.py` | 10 | 0 | 10 | 0 | 0 |
| 126 | `tests/test_ptw_posture_consult.py` | 11 | 0 | 11 | 0 | 0 |
| 127 | `tests/test_redaction_engine.py` | 14 | 0 | 14 | 0 | 0 |
| 128 | `tests/test_redaction_scan_reflex.py` | 11 | 0 | 11 | 0 | 0 |
| 129 | `tests/test_redaction_scope.py` | 3 | 0 | 3 | 0 | 0 |
| 130 | `tests/test_requirement_4.py` | 13 | 0 | 13 | 0 | 0 |
| 131 | `tests/test_resolve_marking.py` | 9 | 0 | 9 | 0 | 0 |
| 132 | `tests/test_rest_api_expansion.py` | 42 | 0 | 42 | 0 | 0 |
| 133 | `tests/test_review_loop.py` | 26 | 0 | 26 | 0 | 0 |
| 134 | `tests/test_rls_integration.py` | 29 | 0 | 29 | 0 | 0 |
| 135 | `tests/test_rls_system_catalog_guard.py` | 26 | 0 | 26 | 0 | 0 |
| 136 | `tests/test_routing_policy.py` | 28 | 0 | 28 | 0 | 0 |
| 137 | `tests/test_row_security.py` | 19 | 0 | 19 | 0 | 0 |
| 138 | `tests/test_sandbox_executor.py` | 63 | 0 | 63 | 0 | 0 |
| 139 | `tests/test_sandbox_scorer.py` | 45 | 0 | 45 | 0 | 0 |
| 140 | `tests/test_sc_artifacts.py` | 15 | 0 | 15 | 0 | 0 |
| 141 | `tests/test_security_context.py` | 24 | **1** | 25 | **0** | **-1** |
| 142 | `tests/test_security_integration.py` | 18 | 0 | 18 | 0 | 0 |
| 143 | `tests/test_slsa_attestation.py` | 16 | 0 | 16 | 0 | 0 |
| 144 | `tests/test_specialist_consult.py` | 10 | **1** | 11 | **0** | **-1** |
| 145 | `tests/test_tenant_request_guard.py` | 3 | **2** | 5 | **0** | **-2** |
| 146 | `tests/test_tool_chain_validator.py` | 20 | 0 | 20 | 0 | 0 |
| 147 | `tests/test_xai_assessor.py` | 22 | 10 | 22 | 10 | 0 |
| 148 | `tests/test_zig_scoring.py` | 43 | 0 | 43 | 0 | 0 |
| 149 | `tests/test_zt_fail_closed_sweep.py` | 29 | 0 | 29 | 0 | 0 |
| 150 | `tests/test_zt_stub_gate.py` | 32 | 0 | 32 | 0 | 0 |
| 151 | `tests/test_zta_maturity_scorer.py` | 12 | **9** | 25 | **0** | **-9** |
| 152 | `tests/testing/test_test_orchestrator.py` | 17 | 0 | 17 | 0 | 0 |

### Addendum — 3 files the epic touched that are outside the 152-file slice

| file | before pass | before fail | after pass | after fail | Δ fail |
|------|------------:|------------:|-----------:|-----------:|-------:|
| `tests/test_ai_governance_intake.py` | 30 | **7** | 37 | **0** | **-7** |
| `tests/test_bdc_poam_generator_fk.py` | 2 | **2** | 4 | **0** | **-2** |
| `tests/test_redaction_fail_closed.py` | 6 | **1** | 8 | **0** | **-1** |
| **total** | **38** | **10** | **49** | **0** | **-10** |
