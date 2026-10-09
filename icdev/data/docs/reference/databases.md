# ICDEV™ Database Reference

Database tables, schemas, and memory system architecture. See [CLAUDE.md](../../CLAUDE.md) for behavioral instructions.

---

### Memory System Architecture
Dual storage: markdown files (human-readable) + SQLite databases (searchable).

**Databases:**
- `data/memory.db` — `memory_entries` (with embeddings), `daily_logs`, `memory_access_log`
- `data/activity.db` — `tasks` table for tracking

**Memory types:** fact, preference, event, insight, task, relationship

**Search ranking:** Hybrid search uses 0.7 * BM25 (keyword) + 0.3 * semantic (vector). Configurable via `--bm25-weight` and `--semantic-weight` flags.

**Embeddings:** OpenAI text-embedding-3-small (1536 dims), stored as BLOBs in SQLite.

---

### Databases

| Database | Tables | Purpose |
|----------|--------|---------|
| `data/icdev.db` | 391 tables | Main operational DB: projects, agents, A2A tasks, audit trail, compliance (NIST, FedRAMP, CMMC, CSSP, SbD, IV&V, OSCAL, FIPS 199/200), eMASS, cATO evidence, PI tracking, knowledge, deployments, metrics, alerts, maintenance audit, MBSE, Modernization, RICOAS (intake, boundary, supply chain, simulation, integration), Operations & Automation (hook_events, agent_executions, nlq_queries, ci_worktrees, gitlab_task_claims), Multi-Agent Orchestration (agent_token_usage, agent_workflows, agent_subtasks, agent_mailbox, agent_vetoes, agent_memory, agent_collaboration_history), Agentic Generation (child_app_registry, agentic_fitness_assessments), Security Categorization (fips199_categorizations, project_information_types, fips200_assessments), Marketplace (marketplace_assets, marketplace_versions, marketplace_reviews, marketplace_installations, marketplace_scan_results, marketplace_ratings, marketplace_embeddings, marketplace_dependencies), Universal Compliance (data_classifications, framework_applicability, compliance_detection_log, crosswalk_bridges, framework_catalog_versions, cjis_assessments, hipaa_assessments, hitrust_assessments, soc2_assessments, pci_dss_assessments, iso27001_assessments), DevSecOps/ZTA (devsecops_profiles, zta_maturity_scores, zta_posture_evidence, nist_800_207_assessments, devsecops_pipeline_audit), MOSA (mosa_assessments, icd_documents, tsp_documents, mosa_modularity_metrics), Remote Gateway (remote_user_bindings, remote_command_log, remote_command_allowlist), Schema Migrations (schema_migrations — D150 version tracking), Spec-Kit (project_constitutions, spec_registry — D156-D161), Proactive Monitoring (heartbeat_checks, auto_resolution_log — D162-D166), Dashboard Auth & BYOK (dashboard_users, dashboard_api_keys, dashboard_auth_log, dashboard_user_llm_keys — D169-D178), Dev Profiles (dev_profiles, dev_profile_locks, dev_profile_detections — D183-D188), Innovation Engine (innovation_signals, innovation_triage_log, innovation_solutions, innovation_trends, innovation_competitor_scans, innovation_standards_updates, innovation_feedback — D199-D208), AI Security (prompt_injection_log, ai_telemetry, ai_bom, atlas_assessments, atlas_red_team_results, owasp_llm_assessments, nist_ai_rmf_assessments, iso42001_assessments — D209-D219), Evolutionary Intelligence (child_capabilities, child_telemetry, child_learned_behaviors, genome_versions, capability_evaluations, staging_environments, propagation_log — D209-D214), Cloud-Agnostic (cloud_provider_status, cloud_tenant_csp_config, csp_region_certifications — D225-D233), Translation (translation_jobs, translation_units, translation_dependency_mappings, translation_validations — D242-D256), Innovation Adaptation (chat_contexts, chat_messages, chat_tasks, extension_registry, extension_execution_log, memory_consolidation_log — D257-D279), OWASP Agentic Security (tool_chain_events, agent_trust_scores, agent_output_violations — Phase 45), Observability & XAI (otel_spans, prov_entities, prov_activities, prov_relations, shap_attributions, xai_assessments — D280-D289), Production Readiness (production_audits, remediation_audit_log — D291-D300), OSCAL Ecosystem (oscal_validation_log — D306), AI Transparency (omb_m25_21_assessments, omb_m26_04_assessments, nist_ai_600_1_assessments, gao_ai_assessments, model_cards, system_cards, confabulation_checks, ai_use_case_inventory, fairness_assessments — D307-D315), AI Accountability (ai_oversight_plans, ai_caio_registry, ai_appeals, ai_incident_log, ai_ethics_reviews, ai_reassessment_schedule — D316-D321), Code Intelligence (code_quality_metrics, runtime_feedback — D331-D337), Phases 53-57 (owasp_asi_assessments, eu_ai_act_assessments — D339, D349), Creative Engine (creative_competitors, creative_signals, creative_pain_points, creative_feature_gaps, creative_specs, creative_trends — D351-D360), CPMP (cpmp_contracts, cpmp_clins, cpmp_wbs, cpmp_deliverables, cpmp_status_history, cpmp_evm_periods, cpmp_subcontractors, cpmp_cpars_assessments, cpmp_negative_events, cpmp_small_business_plan, cpmp_cdrl_generations, cpmp_sam_contract_awards, cpmp_cor_access_log — Phase 60, D-CPMP-1 through D-CPMP-10), Phase 61 Orchestration (atlas_critique_sessions, atlas_critique_findings, prompt_chain_executions, dispatcher_mode_overrides, session_purposes — D-DISP-1, D-PC-1, D-ORCH-5), Industry Research Engine (research_verticals, research_sessions, research_signals, research_challenges, research_regulatory_map, research_build_buy, research_dossiers, research_trends, research_capability_map, research_forecasts — Phase 63, D-RES-1 through D-RES-21), RAG Subsystem (rag_chunks, rag_ingestion_log, rag_retrieval_log, rag_parent_cache — Phase 64, D-RAG-1 through D-RAG-14), Fine-Tuning (ft_datasets, ft_dataset_examples, ft_training_jobs, ft_training_job_events, ft_model_versions, ft_active_models, ft_evaluations, ft_promotion_log, ft_hyperparam_results — Phase 64 Extension, D-FT-1 through D-FT-22), File Sync (sync_jobs, sync_state, sync_log, sync_conflicts — D-SYNC-1 through D-SYNC-12), Bayesian Teaching (bayesian_teaching_scores — D-BT-1 through D-BT-6), Workflow Discipline Engine (workflow_loops, workflow_acceptance_criteria, workflow_reconciliations, workflow_handoffs — D-WF-1 through D-WF-7), NemoClaw Sandboxing (credential_broker_log, credential_active_tokens, blueprint_digests, egress_policy_audit, propagation_verifications — D-NC-1 through D-NC-6), Evolution Daemon (evolution_audit, evolution_reflex_state — D-EVO-1), Redaction & Data Protection (redaction_registry, redaction_audit — D-RDT-1), FathomDesk Backtesting (ad_backtest_runs — Phase D, Migration 057: append-only NIST AU; fields: id PK, ticker, strategy_id, backtest_start, backtest_end, sharpe_ratio, calmar_ratio, max_drawdown_pct, win_rate, trade_count, triggered_by, created_at) |
| `data/platform.db` | 6 tables | SaaS platform DB: tenants, users, api_keys, subscriptions, usage_records, audit_platform |
| `data/icdev.db` — FORGE Academy | 25 `fa_*` tables | Learner platform (`apps/forge_academy/`) — see the dedicated section below |
| `data/tenants/{slug}.db` | (per-tenant) | Isolated copy of icdev.db schema per tenant — separate DB per tenant for strongest isolation |
| `data/memory.db` | 3 tables | Memory system: entries, daily logs, access log |
| `data/activity.db` | 1 table | Task tracking |

**Audit trail is append-only/immutable** — no UPDATE/DELETE operations. Satisfies NIST 800-53 AU controls.

---

### FORGE Academy (`fa_*`, 25 tables in `data/icdev.db`)

Owned by `apps/forge_academy/db.py`. Runtime SQL is authored for **PostgreSQL** (`%s`
placeholders, aca-hyg-05). Manifest: [tools/manifest/forge-academy.md](../../tools/manifest/forge-academy.md).

| Group | Tables |
|-------|--------|
| Learner & progress | `fa_users`, `fa_mission_progress`, `fa_step_progress`, `fa_daily_logins` |
| Catalogue | `fa_missions`, `fa_mission_steps` |
| **XP provenance** | **`fa_xp_ledger`** (append-only) |
| Certificates | `fa_certificates`, **`fa_certificate_evidence`** (append-only) |
| Gamification | `fa_achievements`, `fa_user_achievements`, `fa_skill_nodes`, `fa_user_skills`, `fa_guilds`, `fa_guild_members`, `fa_leaderboard_cache`, `fa_challenges`, `fa_challenge_entries`, `fa_workflow_submissions` |
| Ontology & competency | `fa_mission_ontology`, `fa_step_ontology`, `fa_competency_levels`, `fa_user_competencies` |
| Oracle | `fa_oracle_predictions`, `fa_oracle_convergence_events` |

**`fa_xp_ledger`** (migration `315_fa_xp_ledger.sql`) — **append-only**, registered in
`APPEND_ONLY_TABLES`. One row per XP award. Corrections are new compensating rows, never
`UPDATE`/`DELETE`.

| Column | Meaning |
|--------|---------|
| `user_id`, `xp_delta` | who and how much |
| `reason` | `step_pass` \| `mission_complete` \| `daily_login` \| `achievement` \| `certificate` \| `opening_balance` \| `adjustment`. Keyword-only with **no default** at the `record_xp()` call site, so an unattributed award is a `TypeError`, not a plausible-looking row |
| `source_type`, `source_id` | what earned it. `NULL` only for `opening_balance` — which is the point of it |
| `is_attendance` | `1` for daily logins. The total still reconciles to `fa_users.xp`, but **rank is computed from `SUM(xp_delta) WHERE is_attendance = 0`** — showing up does not buy a promotion |
| `verified` | `0` when the row was reconstructed rather than observed, so a consumer can tell a real award from an accounting artefact |
| `note`, `created_at`, `classification`, `tenant_id` | |

The migration backfills only awards with a **surviving source row** (daily logins, completed
steps); the residual becomes one `opening_balance` row per user flagged `verified=0` rather than
being distributed across invented reasons. Migration `316_fa_rank_from_earned_xp/` recomputes
the stored rank from earned XP.

**`fa_certificate_evidence`** (migration `317_fa_certificate_evidence.sql`) — **append-only**,
registered in `APPEND_ONLY_TABLES`. Snapshots what a certificate was issued against, **at issue
time**, so `/academy/verify/<token>` is checkable by someone who was not there and the claim
cannot drift with the data underneath it.

| Column | Meaning |
|--------|---------|
| `cert_id`, `user_id` | the certificate and its holder |
| `evidence_type` | `gate` (one requirement with the figures that satisfied it) \| `mission` \| `step` |
| `ref_id`, `label`, `detail`, `score` | the cited item |
| `demonstrated_at` | when the **evidence** happened, not when the certificate was issued |
| `classification`, `tenant_id`, `created_at` | |

No backfill: zero certificates had been issued, and there is no honest way to reconstruct what a
past issuance relied on — that was the defect. Revoking a certificate means recording a
revocation, not deleting the evidence that it was once issued.

Other academy migrations: `313_fa_mission_progress_reconcile.sql` (undo the attempts recorded by
the GET write), `314_fa_retire_duplicate_roi_mission.sql`.
