# [TEMPLATE: CUI // SP-CTI]
# ICDEV™ Pre-Tool-Use Hook — Safety validation before tool execution
# Adapted from ADW pre_tool_use.py

"""
Pre-tool-use hook that validates tool calls before execution.

Blocks:
    - Dangerous rm -rf commands
    - Access to .env files containing secrets
    - UPDATE/DELETE/DROP/TRUNCATE on every append-only table (D6, NIST AU)
      See APPEND_ONLY_TABLES list in is_append_only_table_modification()
    - Direct sqlite3.connect() that bypasses the storage layer
    - Writes/deletes forbidden by the D-ORCH-8 file access tiers
    - Deletion of a remote branch that still holds unmerged commits
    - `git worktree add` outside the sanctioned roots
and self-greens staged changes with review_loop before a `git commit`.

This file is the Claude Code ENTRY POINT, not the implementation. Every check
lives in ``tools/hooks/shared_checks.py``, which ``tools/airgap/hook_compat.py``
— the guard every non-Claude-Code orchestrator calls — imports too, so the two
paths cannot drift apart (hgx-guard-01). What still lives here is DATA: the
canonical APPEND_ONLY_TABLES list, which CLAUDE.md's guardrail, the child-app
generator and coherence_checker's autofix all read from this file.

Exit codes:
    0 = allow tool call
    2 = block tool call (shows error to Claude)
"""

import importlib.util
import json
import sys
from pathlib import Path

# rls-bypass: repo root resolved from __file__, never os.getcwd() — this hook
# runs from whichever worktree invoked it, so cwd is not the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_CHECKS_PATH = REPO_ROOT / "tools" / "hooks" / "shared_checks.py"


def _load_shared_checks():
    """Load tools/hooks/shared_checks.py by file path.

    By path rather than ``from tools.hooks import shared_checks`` because
    importing the ``tools`` package executes its compatibility shim, which pulls
    in ``icdev.tools.llm.router`` (~90ms measured). This hook runs before EVERY
    tool call, so that cost would land on every one of them.

    Deliberately not wrapped in try/except: a guard that cannot load must fail
    loudly, not silently stop guarding.
    """
    spec = importlib.util.spec_from_file_location(
        "icdev_hook_shared_checks", SHARED_CHECKS_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Every check below is implemented once, in shared_checks, so this hook and
# tools/airgap/hook_compat.py (the headless path) cannot drift apart again.
shared_checks = _load_shared_checks()

is_dangerous_rm_command = shared_checks.is_dangerous_rm_command
is_env_file_access = shared_checks.is_env_file_access
is_direct_sqlite_usage = shared_checks.is_direct_sqlite_usage
_matches_tier = shared_checks._matches_tier
_worktree_add_target = shared_checks.worktree_add_target
_remote_branch_delete_targets = shared_checks.remote_branch_delete_targets


def is_append_only_table_modification(tool_name: str, tool_input: dict) -> bool:
    """Block UPDATE/DELETE/DROP/TRUNCATE on all append-only tables (NIST 800-53 AU, D6).

    This list must stay in sync with init_icdev_db.py. Run the governance
    validator to detect drift: python tools/testing/claude_dir_validator.py --json
    """
    APPEND_ONLY_TABLES = [
    "web_fetch_provenance",   # oss-cite-01: a fetch is an observation; re-fetch appends
        # === CHILD-INHERITABLE (copied to child apps via step_09c) ===
        # Core audit
        "audit_trail",
        "hook_events",
        # Approval-gate verdicts for irreversible agent actions (ars-appr-01,
        # migration 342). A decision has no lifecycle: someone authorised an
        # irreversible action once, for a stated reason. An UPDATE here rewrites
        # who is answerable for a force-push that already happened, so a
        # correction is a new row.
        "agent_approval_log",
        # Detection findings from the agent rule engine (agov-det-05, migration
        # 20260809201320). A finding is an OBSERVATION — at this time, this rule
        # matched these events — and an observation has no lifecycle. Editing
        # one rewrites what the platform saw, after a reviewer may already have
        # acted on it, so a re-evaluation appends instead. Mutable triage state,
        # if it is ever wanted, belongs in a separate table keyed on finding_id.
        "agent_findings",
        # Cortex canvas governance/facade audit (ctx-canvas-01)
        "cortex_audit",
        # Constitutional AI per-rule critique trail (agx-verify-02, migration 292, NIST AU)
        "constitutional_audit_log",
        # Reproduce-or-drop replay evidence (oss-poc-01, migration 295, NIST AU).
        # Every replay of a dynamic finding, ever — this is what makes a
        # "confirmed" finding auditable and what proves a reproduction
        # discriminates. dynamic_findings itself is mutable (status transitions)
        # and is deliberately NOT listed here.
        "finding_replay_attempts",
        # BOM Evidence Engine (migration 322).
        # bom_match_decisions holds a human's reconciliation verdicts, keyed on a
        # pair of line hashes. Clusters are a projection recomputed OVER these on
        # every run — the decisions are the ONLY durable record of what a person
        # actually approved. Edit one and you have silently rewritten a judgement
        # the customer's budget was signed off against.
        "bom_match_decisions",
        "bom_audit",
        # Phase-E V&V hardening (migration 025) — append-only status transition log
        "kanban_status_transitions",
        # FORGE Academy XP provenance (aca-int-07, migration 315). An award is an
        # event: corrections are new compensating rows, never an UPDATE, or the
        # ledger stops being evidence for the certificates that cite it.
        "fa_xp_ledger",
        # What a certificate was issued against (aca-int-07, migration 317).
        # Revoking a certificate means recording a revocation, not deleting
        # the evidence that it was once issued.
        "fa_certificate_evidence",
        # Who assigned what, and who overrode which grade (aca-trn-04,
        # migration 323). An override that can be edited afterwards is not an
        # audit trail — correcting one means recording the correction.
        "fa_instructor_audit",
        # Assessment attempt ledger (aca-trn-01, migration 324). An attempt limit
        # whose ledger can be UPDATEd away is not a limit, and fa_xp_ledger cites
        # these rows as the provenance of an award. An instructor forgiving a
        # learner's attempts appends a kind='reset' row; it never DELETEs the
        # attempts it forgives, and attempts_used() counts forward from that marker.
        # The one in-place write is closed_at/score flipping NULL -> set exactly
        # once when a served attempt is graded (assessment._close_attempt, guarded
        # by "WHERE closed_at IS NULL") — the intended lifecycle of a row rather
        # than a rewrite of history, the same exception ad_password_reset_tokens
        # carries below.
        "fa_step_attempts",
        # FathomDesk auto-trading (append-only NIST AU)
        "ad_trade_audit",
        "ad_kill_switch",
        "ad_decision_snapshots",
        # FathomDesk news (plan adn-)
        "ad_news_items",
        "ad_news_scenario_links",
        "ad_news_clusters",
        "ad_news_patterns",
        # FathomDesk macro regime classification store (migration 021, NIST AU — append-only signal log)
        "ad_macro_regimes",
        # FathomDesk Trading Oracle (append-only predictions + convergence)
        "ad_trading_predictions",
        "ad_trading_convergence_events",
        "ad_trading_decision_approvals",
        # FathomDesk user-defined alerts (append-only fired-alert log)
        "ad_alerts_log",
        # FathomDesk auth — password-reset audit (used_at flips, never DELETE before purge)
        "ad_password_reset_tokens",
        # FathomDesk auth — MFA attempts audit (NIST AU; required for rate-limit forensics)
        "ad_mfa_attempts",
        # FathomDesk BYOK — credential audit (NIST AU; tracks every set/delete/test/used)
        "ad_credential_audit",
        # FathomDesk tenancy — invitations are append-only (revoked_at + accepted_at flips, never DELETE)
        "ad_tenant_invitations",
        # FathomDesk billing (Phase 5B) — Stripe webhook audit (NIST AU; idempotency + forensics)
        "ad_stripe_events",
        # FathomDesk progression (Phase 6.1) — XP events audit (NIST AU; idempotency + anti-farming audit)
        "ad_xp_events",
        # FathomDesk progression (Phase 6.2) — earned badges (NIST AU; badges never un-award)
        "ad_user_achievements",
        # FathomDesk challenges (Phase 6.3.5) — sandbox order fills (NIST AU; full trade audit)
        "ad_sandbox_orders",
        # FathomDesk challenges (Phase 6.3.5 follow-up) — daily snapshots drive continuously-held predicate; rewriting history would corrupt past-day checks
        "ad_sandbox_daily_snapshots",
        # FathomDesk tax-lots (Phase 7+) — realizations are tax history; must be append-only
        "ad_tax_realizations",
        # FathomDesk tax-lots (Phase 7+) — wash-sale flags audit; must be append-only
        "ad_tax_wash_sale_flags",
        # FathomDesk options coach (Phase 7.6) — event history; recommendation column mutable, rows never deleted
        "ad_options_coach_events",
        # FathomDesk lessons (Phase 6.5) — quiz attempt audit (NIST AU; anti-cheat + learning analytics)
        "ad_user_quiz_attempts",
        # AI-ify Canvas (penta-aiify-04) — PRD provenance/citation lineage for
        # AI-boosted PRDs; the latest row per phase supersedes, never mutated
        "aiify_prd_provenance",
        # Document Modernization Engine (docmod, migration 258) — findings state
        # transitions are superseding rows; scan runs and catalog curation are audit
        "docmod_findings",
        "docmod_scan_runs",
        "docmod_catalog_audit",
        # Document Modernization — semantic claim tracking (dmx-claims-02, migration
        # 283). Claim status transitions are superseding rows (supersedes_id chain);
        # a state change is a NEW row, never a mutation. HITL + deterministic-first.
        "dic_claims",
        # Phase 44 — Innovation Adaptation
        "extension_execution_log",
        "memory_consolidation_log",
        # Phase 29 — Proactive Monitoring
        "auto_resolution_log",
        # Phase 36 — Evolutionary Intelligence
        "propagation_log",
        # Phase 37 — AI Security
        "prompt_injection_log",
        "ai_telemetry",
        # Phase 22 — Marketplace
        "marketplace_reviews",
        "marketplace_scan_results",
        # Phase 69 — OpenClaw Bridge
        "openclaw_exports",
        # Multi-Agent Orchestration
        "agent_vetoes",
        # Dashboard Auth (D169-D172)
        "dashboard_auth_log",
        # Phase 24 — DevSecOps
        "devsecops_pipeline_audit",
        # Phase 28 — Remote Gateway
        "remote_command_log",
        # Phase 35 — Innovation Engine (D206)
        "innovation_signals",
        "innovation_triage_log",
        # ACF normalized innovation engine outputs (append-only)
        "innovation_signal",
        # Phase 39 — Observability
        "agent_executions",
        # Phase 40 — NLQ
        "nlq_queries",
        # Phase 22 — Marketplace (immutable published versions)
        "marketplace_versions",
        # Phase 34 — Dev Profiles (immutable rows, D183)
        "dev_profiles",
        # Phase 45 — OWASP Agentic AI Security (D258, D259, D260)
        "tool_chain_events",
        "agent_trust_scores",
        "agent_output_violations",
        # Agentic AI safety_layer SIEM event sink (append-only, best-effort forwarder)
        "siem_events",
        # Phase 46 — Observability, Traceability & XAI (D280-D290)
        "otel_spans",
        "prov_entities",
        "prov_activities",
        "prov_relations",
        "shap_attributions",
        "xai_assessments",
        # crx-db-03 — Retention framework action log (append-only NIST AU record of every prune/archive)
        "retention_action_log",
        # sag-cron-01 — User-facing cron run log (append-only record of every scheduled job execution)
        "agent_cron_runs",
        # Phase 47 — Production Readiness Audit (D292)
        "production_audits",
        # Phase 47 — Production Remediation (D296-D300)
        "remediation_audit_log",
        # OSCAL Ecosystem (D306 — validation audit trail)
        "oscal_validation_log",
        # Phase 48 — AI Transparency & Accountability (D307-D315)
        "confabulation_checks",
        "fairness_assessments",
        "model_cards",
        "system_cards",
        "ai_use_case_inventory",
        # Phase 49 — AI Accountability (D316-D321)
        "ai_oversight_plans",
        "ai_accountability_appeals",
        "ai_incident_log",
        "ai_ethics_reviews",
        # Phase 52 — Code Intelligence (D332)
        "code_quality_metrics",
        "runtime_feedback",
        # Phase 53 — OWASP ASI + FedRAMP 20x (D339)
        "owasp_asi_assessments",
        # Phase 57 — EU AI Act (D349)
        "eu_ai_act_assessments",
        # Phase 64 — RAG Subsystem (D-RAG-8, D-RAG-11)
        "rag_ingestion_log",
        "rag_retrieval_log",
        # RAG provenance ledger — append-only AIA chain-of-custody (D-AIDP, NIST AU-3)
        "rag_provenance_ledger",
        # ICDEV Cortex governance audit — one append-only row per governed Cortex
        # call (ctx-govern-03, NIST AU). cortex_sessions is intentionally NOT here
        # (mutable session lifecycle: status/updated_at).
        "cortex_audit",
        # Phase 69 — Codebase Assistant (D-CA-6)
        "codebase_qa_cache",
        # Genesis v2.0 (D-GEN-6, D-GEN-10)
        "genesis_audit",
        # Genesis reflex observer (monitoring — append-only NIST AU)
        "reflex_observations",
        # Knowledge Graph (D-KARL-1)
        "kg_retrieval_log",
        # Phase 64 Extension — Fine-Tuning (D-FT-3, D-FT-9, D-FT-14, D-FT-16, D-FT-13)
        "ft_dataset_examples",
        # RAG-to-FT Pipeline (D-KARL-5, D-KARL-8)
        "ft_pipeline_runs",
        "ft_quality_snapshots",
        "ft_training_job_events",
        "ft_evaluations",
        "ft_promotion_log",
        "ft_hyperparam_results",
        # Trajectory-to-Training Pipeline (D-FT-TRAJ)
        "ft_trajectory_steps",
        # === PARENT-ONLY (excluded from child apps — D-CHILD-3) ===
        # Proposal Lifecycle (D-PROP-3 — reviews, findings, status history are immutable)
        "proposal_reviews",
        "proposal_review_findings",
        "proposal_status_history",
        # Creative Engine (D357 — creative_competitors excluded: allows UPDATE for status transitions)
        "creative_signals",
        "creative_pain_points",
        "creative_feature_gaps",
        "creative_specs",
        "creative_trends",
        # ACF normalized creative engine inputs (append-only)
        "creative_gap",
        # GovCon Intelligence (Phase 59, D361-D373)
        "sam_gov_quota_events",
        "rfp_shall_statements",
        "rfp_requirement_patterns",
        "icdev_capability_map",
        "proposal_section_drafts",
        "govcon_awards",
        # Customer Delivery Tracking (D374)
        "customer_deliveries",
        # Phase 59 — Questions to Government (D-QTG-2)
        "proposal_question_responses",
        # Phase 60 — CPMP (D-CPMP-7)
        "cpmp_status_history",
        "cpmp_negative_events",
        "cpmp_evm_periods",
        "cpmp_cdrl_generations",
        "cpmp_cor_access_log",
        # RFI Workbench (migration 236) — export log is append-only
        "rfi_workbench_exports",
        # RFI Capability-Gap Demand Loop (migration 251) — provenance links are
        # immutable (gap -> emitted kanban task). rfi_capability_gaps itself is NOT
        # append-only (frequency/priority update in place).
        "rfi_gap_task_links",
        # Phase 61 — ANVIL Critique (Feature 3)
        "anvil_critique_sessions",
        "anvil_critique_findings",
        # Phase 61 — Prompt Chain Execution (Feature 2)
        "prompt_chain_executions",
        # Phase 61 — Dispatcher Mode (Feature 1)
        "dispatcher_mode_overrides",
        # Phase 63 — Industry Research Engine (D-RES-5)
        # research_sessions excluded: allows UPDATE for status transitions
        # research_verticals excluded: allows UPDATE for activation
        "research_signals",
        "research_challenges",
        "research_regulatory_map",
        "research_build_buy",
        "research_dossiers",
        "research_trends",
        "research_capability_map",
        "research_forecasts",
        # Proposal Genesis (D-PG-1 through D-PG-10)
        "pg_proposal_genesis_audit",
        "pg_amendment_diffs",
        "pg_pulse_proposal_links",
        "pg_proposal_quality_scores",
        "pg_bid_decisions",
        "pg_bid_decision_outcomes",
        "pg_win_loss_records",
        "pg_win_loss_lessons",
        "pg_crm_interactions",
        "pg_crm_engagement_scores",
        "pg_capture_activities",
        "pg_capture_gate_decisions",
        "pg_teaming_assessments",
        "pg_pwin_assessments",
        # Proposal Genesis Enhancement (append-only review/theme/experiment tracking)
        "pg_review_findings",
        "pg_theme_tracking",
        "pg_quality_experiments",
        # File Sync Module (D-SYNC-7 — sync_log is append-only, NIST AU)
        "sync_log",
        # Phase 65 — Quality Design Canvas (D-QDC-5)
        "qdc_audit",
        "qdc_gate_results",
        "qdc_uqs_history",
        # Phase 65 — Adaptive Intelligence (Red Team, Convergence, Stagnation, Benchmarks)
        "red_team_results",
        "genesis_convergence_log",
        "genesis_stagnation_log",
        "agent_benchmark_results",
        # GSD-adapted: 4-Level Verification, Context Pressure, Deviation Rules (D-GSD-1 through D-GSD-9)
        "stub_detection_results",
        "context_pressure_events",
        "deviation_rule_events",
        # Evolution Daemon (D-EVO-1, Phase 36 autonomous lifecycle)
        "evolution_audit",
        # Outcome Verifier (D-EVO-6, self-healing feedback loop)
        "outcome_verification_log",
        # SOAR-lite response playbooks (crx-sec-02) — append-only per-run event log (NIST AU)
        "soar_playbook_audit",
        # NemoClaw-Adapted Agent Sandboxing (D-NC-1, D-NC-2, D-NC-3, D-NC-5)
        "credential_broker_log",
        "egress_policy_audit",
        "blueprint_digests",
        "propagation_verifications",
        # Bayesian Teaching Intelligence Layer (D-BT-1 through D-BT-6)
        "bayesian_teaching_scores",
        # Phase 66 — Workflow Discipline Engine (D-WF-1 through D-WF-7)
        "workflow_reconciliations",
        "workflow_handoffs",
        # WriteGuard (D-WG-9 — analysis results/findings are immutable)
        "wg_analysis_results",
        "wg_analysis_findings",
        # DataBridge (D-DB-6 — sync log, mapping log, messages are append-only)
        "db_sync_log",
        "db_mapping_log",
        "db_messages",
        # Connector Forge (D-CF sandbox/promotion logs are append-only)
        "db_forge_sandbox_log",
        "db_forge_promotions",
        # CloudForge (D-CF-10, D-CF-15, D-CF-20, D-CF-21 — all append-only)
        "cf_provision_log",
        "cf_siem_events",
        "cf_runbook_executions",
        "cf_runbook_task_log",
        # Phase 67 — Engineering Review Board (D-RB-2, D-RB-10 — audit + findings + remediation append-only)
        "review_board_audit",
        "review_board_findings",
        "review_board_remediation_log",
        "review_board_health_history",
        # Phase 68 — Autonomy Engine (D-AE-5, D-AE-10, D-AE-12 — observations, actions, behavior append-only)
        "autonomy_observations",
        "autonomy_actions",
        "autonomy_behavior_log",
        # Phase 67 — Bayesian Autoresearch (D-AR-4)
        "experiment_results",
        "bayesian_experiment_scores",
        # Scout Daemon (D-SCT-1 — daily autonomous scan audit trail)
        "scout_audit",
        # Phase 70 — AIOps/LLMOps Adaptation (append-only audit/event tables)
        "llm_gateway_audit",
        "prompt_audit_log",
        "llm_cost_alerts",
        "model_drift_events",
        "sre_slo_measurements",
        "sre_runbook_executions",
        "sre_incident_events",
        "agent_topology_snapshots",
        # Phase 71 — Sandbox Executor (D-SEC-10)
        "sandbox_execution_log",
        # Phase 71 — CRAG Evaluation (D-RAG-23)
        "rag_evaluation_campaigns",
        "rag_evaluation_results",
        # Phase 70 — Redaction & Data Protection (D-RDT-1)
        "redaction_audit",
        # Phase 72 — Notification Gateway (Hermes adaptation)
        "notification_log",
        # Phase 72 — ICDEV™ Studio (D364, D365 — case history + automation runs)
        "studio_case_history",
        "studio_automation_runs",
        # DWO / dwo-evt-01 — trigger evaluation audit ("why did this run start")
        "studio_trigger_events",
        # DWO / dwo-mcp-02-d5 — every MCP dispatch attempt (allowed, refused,
        # pending approval), with the actor and the gate's decision
        "studio_mcp_dispatch_audit",
        # Cross-canvas KG build audit log (append-only — NIST AU)
        "canvas_kg_build_log",
        # Phase 73 — Findings + Oracle Predictions (NIST AU, append-only)
        "finding_approvals",
        "oracle_convergence_events",
        "oracle_predictions",
        "oracle_remediation_proposals",
        # Phase 4 (Internal Awareness) — Q&A messages are append-only (NIST AU)
        "icdev_qa_messages",
        # Awareness run log + health snapshots (NIST AU, append-only)
        "awareness_run_log",
        "awareness_component_health",
        # IDP per-component scorecard history (idp-score-03, migration
        # 20260802222900). A trend line you can UPDATE is not a trend line —
        # a wrong point is corrected by recording a new one, never by editing
        # the old one, or "is this component getting better" stops being
        # answerable from the data.
        "idp_scorecard_history",
        # IDP rule-level scorecard exemptions (idp-score-04, migration
        # 20260803030514). The log IS the record: an exemption is an authority
        # claim, and the only thing that makes one reviewable later is knowing
        # who approved it and why. An UPDATE would overwrite the approver and a
        # DELETE would erase the fact that anyone waived anything, so every
        # state change — request, approval, denial, revocation — appends.
        "idp_rule_exemptions",
        # Observability Canvas integration (D-OC audit trail, NIST AU)
        "od_audit",
        "nc_audit",
        # ODC Twin — MITRE ATT&CK coverage events (migration 028, append-only NIST AU)
        "mitre_coverage",
        # Passive CVE Watcher — ATO continuous monitoring (NIST SI-4, CA-7)
        "cve_passive_watch_log",
        # BDC cATO Twin — compliance control snapshots (migration 027, NIST AU)
        "compliance_snapshots",
        # SDC Attack Path Twin — append-only attack graph (NIST AU; migration 028)
        "attack_graph_nodes",
        "attack_graph_edges",
        # Network Canvas simulation history (migration 037, NIST AU)
        "nc_simulation_sessions",
        "nc_simulation_runs",
        "nc_simulation_artifacts",
        # Cross-canvas event bus (migration 037, NIST AU — payload history preserved)
        "canvas_events",
        # ODC MITRE ATT&CK technique catalog (migration cvo-odc-01, append-only NIST AU)
        "odc_mitre_techniques",
        # Security Framework (Phase 74 — sec-fnd)
        "security_policies",
        "user_compartments",
        "security_context_log",
        "abac_decisions",
        "mac_violations",
        "rls_audit",
        "column_mask_audit",
        "field_filter_audit",
        # FathomDesk Market Breadth (migration 047 — periodic breadth snapshots, NIST AU)
        "ad_breadth_snapshots",
        # FathomDesk Value Compass (migration 048 — F&G + Buffett snapshots, NIST AU)
        "ad_fear_greed_snapshots",
        "ad_buffett_snapshots",
        # Strategos interdiction analysis results (migration 058, NIST AU — append-only ranked outputs)
        "sg_interdiction_results",
        # Strategos Analyst Annotation Layer (migration 060, NIST AU — append-only annotation store)
        "sg_analyst_annotations",
        # DES execution audit log (NIST AU — append-only)
        "des_execution_events",
        # Strategos SOCMINT signals (migration 023, NIST AU — append-only ingestion log)
        "sg_socmint_signals",
        # FathomDesk analyst panel decision audit (migration 078, SEC Rule 17a-4 / NIST AU)
        "ad_decision_audit",
        # FathomDesk backtest result store (migration 057, NIST AU — append-only)
        "ad_backtest_runs",
        # HITL Workflow Management (migration 079, NIST AU — feedback, submissions, citations append-only)
        "wf_feedback",
        "wf_document_submissions",
        "wf_citations",
        # WNE artifact store (migration 084, NIST AU — append-only)
        "wne_artifacts",
        # Genesis reflex output artifact store (migration 188, NIST AU — append-only reflex output log)
        "genesis_outputs",
        # Genesis design phase-transition log (migration 189, NIST AU — append-only phase audit)
        "genesis_phase_log",
        # Genesis reflex run log (migration 116, NIST AU — cooldown tracking + audit)
        "genesis_reflex_log",
        # NMCE — AI conversation audit trail (migration canvas, NIST AU)
        "mc_net_ai_sessions",
        # Migration Canvas — forced wave-close HITL override audit (crx-mig-01, NIST AU — append-only)
        "mc_wave_close_overrides",
        # STRATEGOS — war readiness event log (migration 118, NIST AU — append-only I&W audit)
        "sg_war_readiness_events",
        # STRATEGOS — adversarial data validation audit (NIST AU-9 — append-only)
        "sg_adversarial_validation_audit",
        # STRATEGOS — INTSUM grounding force-override audit (migration 280, nav-strat-01, NIST AU — append-only HITL override log)
        "sg_intsum_grounding_audit",
        # STRATEGOS — OPORD grounding force-override audit (migration 279, NIST AU — append-only)
        "sg_opord_grounding_audit",
        # NDC↔Migration — topology snapshots (NIST AU; phase-completion history must be immutable)
        "nc_topology_snapshots",
        # Phase 71 — OHC Ops Hub Canvas (migration 120, NIST AU — adapter health log + drift events append-only)
        "ohc_adapter_health_log",
        "ohc_data_drift_events",
        # GovLift DoD IL4 Cloud Migration (NIST AU — audit log append-only)
        "govlift_audit_log",
        # AI Traceability (migration 121 — cross-canvas AI decision audit log, NIST AU-2/AU-3)
        "canvas_ai_decisions",
        # Cross-Agency Data Transfer (NIST AU-2, AU-9 — append-only transfer audit log)
        "cross_agency_transfers",
        # IL5 data ingestion audit (NIST AU-2, AU-12 — 30-second SLA display pipeline)
        "il5_ingestion_log",
        # Canvas Instances — seeding audit (NIST AU — append-only activation log)
        "canvas_instances",
        # Migration Design Canvas — audit trail (NIST AU, append-only)
        "mc_audit",
        # NOC Operations Canvas — audit trail (NIST AU, append-only)
        "noc_audit",
        # Peering Management Canvas — audit trail (NIST AU, append-only)
        "pmc_audit",
        # Circuit & Capacity Canvas — audit trail (NIST AU, append-only)
        "ccc_audit",
        # DDoS & Security Ops Canvas — audit trail (NIST AU, append-only)
        "dsoc_audit",
        # OSINT Privacy Sanitizer — PII detection/redaction audit (NIST AU, migration 159)
        "osint_privacy_audit",
        # AI Augmentation Canvas — scan sessions and audit trail (NIST AU, append-only)
        "aac_scans",
        "aac_audit_log",
        # ISP/Telco — Partner & Agreement Lifecycle (NIST AU, append-only amendment log)
        "nc_agreement_amendments",
        # ISP/Telco — Cross-Connect Order Workflow (NIST AU, append-only order state log)
        "ccc_xc_order_events",
        # DoD/IC Access Control Audit (Phase 163 — G-02/G-05, NIST AU-2/AU-12)
        "canvas_access_grants",
        "user_mfa",
        "mfa_attempts",
        "gateway_rate_limits",
        # DoD/IC PKI + Continuous Auth (G-05/G-11/G-14, NIST AU-2/AU-12/IA-11)
        "abac_audit",
        "session_risk_log",
        # AI Data Mapping — transformation artifact audit (NIST AU-9, append-only)
        "dd_mapping_transforms",
        # Data Design Canvas (dcpr-) — audit trails + immutable run-logs (NIST AU-9, append-only)
        # dd_audit / dm_audit are trigger-protected; the run/scan logs are insert-only.
        # EXCLUDED: dd_lineage (runtime DELETE of edges), ddc_runbook_executions (runtime status UPDATE + DELETE).
        "dd_audit",
        "dm_audit",
        "dm_policy_audit_log",
        "dm_csp_sync_log",
        "dm_contract_test_runs",
        "dd_query_history",
        "dd_anomaly_runs",
        "dd_quality_runs",
        "dd_pii_scans",
        # Slide Deck Generator — generation audit trail (NIST AU, append-only)
        "slides_audit",
        # ACE (Autonomous Collaborative Engine) — step execution audit trail + skill candidates (NIST AU, append-only)
        "ace_audit_log",
        "databridge_agent_access_log",
        "ace_step_audit_log",
        "ace_webhook_log",
        "ace_skill_candidates",
        # SIPA Software Integrity & Provenance Assessor (sipa-, NIST AU — assessment evidence is immutable)
        "integrity_capabilities",
        "integrity_findings",
        "integrity_verdicts",
        "integrity_authorizations",
        # ACF Autonomous Capability Foundry (acf-, append-only signal/concept/spec/ledger)
        "foundry_runs",
        "foundry_signals",
        "foundry_specs",
        "foundry_tasks_emitted",
        "foundry_outcomes",
        # Co-Workers canvas — session links are immutable evidence (NIST AU)
        "cwk_sessions",
        # EQO Centralized Logging (eqo-log-01, migration 181) — log rows are immutable evidence (NIST AU)
        "centralized_logs",
        # Enterprise-configurable platform (Phase 5) — component enable/profile/override audit
        "component_audit_log",
        # MCIP DAT — DTI snapshots are append-only audit trail (NIST AU-9, issue-18)
        "mcip_dti_scores",
        # ECR SSO — session records are append-only NIST AU (sso_providers is mutable)
        "sso_sessions",
        # ECR SOC 2 — evidence records are immutable compliance evidence (NIST AU-9, migration 211)
        "evidence_items",
        # ECR DRES — zone assignments are append-only audit trail (NIST AU-9, migration 212)
        "tenant_zone_assignments",
        # ECR DRES-03 — GDPR erasure audit log (append-only NIST AU, immutable evidence of erasure)
        "erasure_audit",
        # ECR Billing (migration 213) — usage events are immutable billing audit records (NIST AU-9)
        "usage_events",
        # ECR API Keys (migration 215) — keys are append-only; revocation sets revoked_at, never deletes (NIST AU-9)
        "api_keys",
        # IDR — conflict resolution trail is append-only (resolution recorded in-place, rows never deleted — NIST AU)
        "idr_conflicts",
        # IDR — TRUST publish-gate override audit (migration 276 — NIST AU): one row per force_* override
        "idr_publish_audit",
        # Pulse — judge-verdict publish-gate override audit (migration 281 — NIST AU): one row per admin force_publish
        "pulse_publish_audit",
        # NQE / Forward Networks Integration (migration 220, 222 — NIST AU)
        "nc_advisory_assessments",   # impact assessment results — proof chain for ATO
        "nc_nqe_audit_log",          # every translate/run/approve action traced
        "nc_remediation_status_log", # every status transition for remediation actions
        "nc_poam_items",             # formal POAM entries (FedRAMP/DoD format)
        "nc_poam_status_log",        # POAM milestone/status change log
        "nc_exceptions",             # filed exceptions for unmitigated vulnerabilities
        "nc_exception_approvals",    # AO/ISSO/ISSM approval chain for exceptions
        # PVM — Predictive Vulnerability Management (migration 221)
        "nc_vuln_predictions",       # time-series risk scores per CVE (NIST AU)
        "nc_patch_plans",            # AI-generated patch schedules (immutable once created)
        # PNA — Predictive Network Analytics (migration 222)
        "nc_eol_predictions",        # device end-of-life/support risk scores
        "nc_bgp_predictions",        # BGP session instability forecasts
        "nc_compliance_drift",       # STIG/compliance baseline drift predictions
        "nc_capacity_predictions",   # bandwidth saturation forecasts
        "nc_change_risk",            # pre-change failure probability scores
        "nc_supply_chain_risk",      # vendor supply-chain risk aggregation
        # TimesFM Forecasting microservice (migration 219) — forecast audit log append-only NIST AU
        "forecast_audit",
        # ACE QA Agent (NIST AU — test evidence is immutable)
        "ace_qa_runs",
        "ace_qa_failures",
        # BI Dashboard Canvas — AI chart-generation audit trail (NIST AU)
        "bi_generation_log",
        # prop-sec-05 — Aggregation Guard / mosaic-effect rule evaluation log (NIST AU)
        "aggregation_events",
        # Document Modernization Engine (docmod-core, migration 257) — scan runs and
        # findings are append-only (state transitions = new row w/ supersedes_id);
        # catalog curation audit is NIST AU append-only
        "docmod_scan_runs",
        "docmod_findings",
        "docmod_catalog_audit",
        # Pipeline Design Canvas (pdx-sec-04, NIST AU) — write-route audit trail and
        # twin/snapshot history are immutable evidence; rows never UPDATE/DELETE
        # pdc_snapshots deliberately excluded (pdx reconciliation, user-approved
        # 2026-07-18): design-history working store with bounded auto-snapshot
        # retention (review finding P1-7), not an audit record.
        "pc_audit",
        "pipeline_snapshots",
        # Security Design Canvas (migration 272, NIST AU) — sc_audit carries DB-level
        # immutability triggers (sc_audit_no_update/no_delete); non-repudiation trail
        # for ZIG capability/activity/evidence/assessment writes (cnr-zig-03)
        "sc_audit",
        # LPX LLM-proxy virtual-key lifecycle (lpx-keys-03, NIST AU) — issuance,
        # rotation, revocation, and expiry are immutable evidence; rows never
        # UPDATE/DELETE.
        "llm_proxy_key_audit",
        # AI GameDay per-team API-call receipts (lpx-teams-03) — spend attribution
        # for competition integrity; append-only in fact (only engine.log_api_receipt
        # inserts, every other reference is a SELECT). Rows never UPDATE/DELETE.
        "ttx_api_log",
        # AI GameDay League (gdx-aud-01, migration 136, NIST AU) — of the 8 gd_ai_*
        # tables only these three are append-only IN FACT. Every write site was
        # audited; each of the three has INSERT-only call sites and no upsert:
        #   gd_ai_artifacts      — db.py::save_artifact (INSERT ... RETURNING)
        #   gd_ai_llmops_events  — db.py::log_llmops_event (INSERT)
        #   gd_ai_training_pairs — db.py::save_training_pair, nova_hook.py
        #                          ::_persist_to_ft_datasets (both plain INSERT)
        # The other five are MUTABLE BY DESIGN and are deliberately NOT listed —
        # registering them would break the league:
        #   gd_ai_tournaments — db.py::update_tournament UPDATEs status/current_round
        #   gd_ai_teams       — db.py::update_team_scores UPDATEs cumulative deltas
        #   gd_ai_rounds      — db.py::update_round UPDATEs status/started/completed
        #   gd_ai_judge_evals — db.py::save_judge_eval ON CONFLICT DO UPDATE (re-judge)
        #   gd_ai_leaderboard — db.py::upsert_leaderboard ON CONFLICT DO UPDATE
        #                       (a recomputed snapshot, not an audit record)
        # Kept in sync with the migration 136 docstring by
        # tests/test_gdx_gameday_append_only.py.
        "gd_ai_artifacts",
        "gd_ai_llmops_events",
        "gd_ai_training_pairs",
    ]
    # NOTE: runtime_invocations (migration 341) is deliberately NOT listed. It
    # is telemetry with a genuine lifecycle — the recorder opens a row 'running'
    # and UPDATEs it closed with duration and status — so it is not append-only
    # and claiming otherwise here would both misdescribe it and block legitimate
    # repair. Audit EVIDENCE belongs above; operational telemetry does not.

    return shared_checks.is_append_only_table_modification(
        tool_name, tool_input, APPEND_ONLY_TABLES
    )


def check_file_access_tiers(tool_name: str, tool_input: dict) -> str:
    """Check file access tiers. Returns error message if blocked, None if allowed.

    Decision D-ORCH-8: Tiered file access control.
    """
    return shared_checks.check_file_access_tiers(
        tool_name, tool_input, repo_root=REPO_ROOT
    )


def run_review_loop_precommit(tool_name: str, tool_input: dict) -> None:
    """Self-green staged changes with review_loop before a `git commit`.

    Warn-only by default (the commit proceeds). Set ICDEV_REVIEW_LOOP_BLOCK=1 to
    hard-block a non-green commit, or ICDEV_REVIEW_LOOP_PRECOMMIT=0 to disable.
    """
    reason = shared_checks.check_review_loop_precommit(
        tool_name, tool_input, repo_root=REPO_ROOT,
        notify=lambda message: print(message, file=sys.stderr),
    )
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)


def check_worktree_path(tool_name: str, tool_input: dict) -> str:
    """Refuse a ``git worktree add`` outside the sanctioned roots.

    Blocks rather than warns — a check is what makes a convention hold. Set
    ICDEV_WORKTREE_GUARD=0 to disable. Fails OPEN on any resolution error.
    """
    return shared_checks.check_worktree_path(
        tool_name, tool_input, repo_root=REPO_ROOT
    ) or ""


def check_branch_deletion(tool_name: str, tool_input: dict) -> str:
    """Refuse to delete a remote branch that still holds unmerged commits.

    Deleting a head branch CLOSES its pull request as closed-not-merged, and
    `gh pr reopen` cannot undo it once the ref is gone. Set
    ICDEV_BRANCH_DELETE_GUARD=0 to disable. Fails OPEN on any error.
    """
    return shared_checks.check_branch_deletion(
        tool_name, tool_input, repo_root=REPO_ROOT
    ) or ""


def check_agent_rules(tool_name: str, tool_input: dict) -> str:
    """Refuse a call an ENFORCING agent rule matched (agov-det-06).

    Additive: runs after every hardcoded block, and only a rule that both sets
    ``enforce: true`` and lives in the operator directory
    (``args/agent_rules_enforce/``, or ``ICDEV_AGENT_ENFORCE_RULES_DIR``) can
    produce a refusal here. Everything else matched is recorded to
    ``agent_findings`` and allowed. Set ICDEV_AGENT_DETECT=0 to disable. Fails
    OPEN on any error.
    """
    return shared_checks.check_agent_rules(
        tool_name, tool_input, repo_root=REPO_ROOT
    ) or ""


def main():
    try:
        input_data = json.load(sys.stdin)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Block .env file access
        if is_env_file_access(tool_name, tool_input):
            print(shared_checks.ENV_FILE_BLOCK_REASON, file=sys.stderr)
            sys.exit(2)

        # Block dangerous rm commands
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if is_dangerous_rm_command(command):
                print(shared_checks.DANGEROUS_RM_BLOCK_REASON, file=sys.stderr)
                sys.exit(2)

        # Block modification of all append-only tables (NIST 800-53 AU, D6)
        if is_append_only_table_modification(tool_name, tool_input):
            print(shared_checks.APPEND_ONLY_BLOCK_REASON, file=sys.stderr)
            sys.exit(2)

        # Block direct sqlite3.connect() — use get_connection() instead
        if is_direct_sqlite_usage(tool_name, tool_input):
            print(shared_checks.DIRECT_SQLITE_BLOCK_REASON, file=sys.stderr)
            sys.exit(2)

        # Check tiered file access control (D-ORCH-8)
        tier_error = check_file_access_tiers(tool_name, tool_input)
        if tier_error:
            print(tier_error, file=sys.stderr)
            sys.exit(2)

        # Never delete a remote branch that still holds unmerged work
        branch_error = check_branch_deletion(tool_name, tool_input)
        if branch_error:
            print(branch_error, file=sys.stderr)
            sys.exit(2)

        # Keep worktrees out of shared temp dirs where two sessions collide
        worktree_error = check_worktree_path(tool_name, tool_input)
        if worktree_error:
            print(worktree_error, file=sys.stderr)
            sys.exit(2)

        # AGOV declarative rules — LAST, and additive only (agov-det-06).
        # Every block above is hardcoded and stays that way; this one is the
        # data-driven check, monitor-only unless an operator opted a rule into
        # enforcement in args/agent_rules_enforce/. It fails open.
        rule_error = check_agent_rules(tool_name, tool_input)
        if rule_error:
            print(rule_error, file=sys.stderr)
            sys.exit(2)

        # Self-green staged changes before a git commit (warn-only by default)
        run_review_loop_precommit(tool_name, tool_input)

        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
