# CUI // SP-CTI
"""Single source of truth for pre-tool-use safety checks (hgx-guard-01/02).

Two orchestration paths reach the same tools:

* ``.claude/hooks/pre_tool_use.py`` — Claude Code spawns it once per tool call.
* ``tools/airgap/hook_compat.py::run_pre_tool_check`` — what the Studio agent
  loop, the kanban runner, the MCP gateway and every other non-Claude-Code
  orchestrator actually call.

Before this module they were separate implementations. Claude Code ran eight
checks; the headless path ran two, and had one (the destructive-git blocklist)
that Claude Code did not — so neither was a superset of the other and an agent
running outside Claude Code was materially *less* guarded. For an IL5/IL6
platform that is exactly backwards.

Every check now lives here as a pure function over a :class:`ToolCall`, and both
paths iterate the same :data:`CHECKS` registry. A check declares which paths it
applies to, so any remaining difference is declared and tested rather than
accidental (``tests/test_hgx_guard_parity.py`` asserts the headless path is a
superset of the Claude Code path).

Design constraints
------------------
**Stdlib-only at import time.** The Claude Code hook is a short-lived process on
the critical path of every tool call. ``import tools`` eagerly constructs an
``LLMRouter`` and costs ~80 ms, so the hook loads this file directly from disk
with ``importlib`` instead. Anything heavier than the stdlib (``yaml``,
``tools.git.worktree_paths``, ``tools.quality.review_loop``) is imported lazily,
inside the one check that needs it.

**No ``os.getcwd()``.** These checks run from git worktrees, where the current
working directory is the worktree root and not the repository root. The repo
root is resolved from ``__file__`` (see :func:`repo_root`).

**Tool names are not a security boundary.** ``run_pre_tool_check`` used to
short-circuit to *allowed* for any tool whose name was not one of six known
strings, so an unrecognised mutating tool was waved through unscanned. Checks
here gate on the *shape* of the call — does it carry text that will be executed,
a path that will be written — rather than on a name allowlist. Known tools
classify exactly as they did before; unknown ones are scanned rather than
trusted. (hgx-guard-03 replaces the remaining name-based read-only heuristic
with a declared ``read_only`` flag on the tool definition.)
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess  # nosec B404 — fixed argv, shell=False, used for `git cherry`
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

__all__ = [
    "APPEND_ONLY_TABLES",
    "CHECKS",
    "PATH_CLAUDE_CODE",
    "PATH_HEADLESS",
    "Check",
    "CheckOutcome",
    "ToolCall",
    "classify",
    "evaluate",
    "repo_root",
]


# ── Repository root ────────────────────────────────────────────────────────
#
# rls-bypass: repo root is resolved from __file__ and never from os.getcwd() —
# required for hgx-guard-02 because these checks run from git worktrees, where
# cwd is the worktree root rather than the canonical repository root.

_HERE = Path(__file__).resolve()
_REPO_ROOT: Optional[Path] = None


def repo_root() -> Path:
    """Repository root, resolved from this file's location.

    Walks up looking for a checkout marker. ``.git`` is a directory in the main
    checkout and a *file* in a worktree, so both are probed with ``.exists()``.

    ``.claude/`` alone is NOT a marker: companion sync writes a ``tools/.claude``
    directory, so a lone ``.claude`` probe stops one level short and resolves the
    repo root to ``<repo>/tools``. The checkout-less fallback therefore requires
    ``.claude`` *and* ``args`` *and* ``CLAUDE.md`` together, which only the real
    root carries.

    Falls back to the package root (``<pkg>/tools/hooks/`` -> ``<pkg>``) for a
    pip-installed tree that has no checkout markers at all.
    """
    global _REPO_ROOT
    if _REPO_ROOT is None:
        for parent in _HERE.parents:
            if (parent / ".git").exists() or (
                (parent / ".claude").is_dir()
                and (parent / "args").is_dir()
                and (parent / "CLAUDE.md").exists()
            ):
                _REPO_ROOT = parent
                break
        else:
            _REPO_ROOT = _HERE.parents[2]
    return _REPO_ROOT


# ── Append-only tables (NIST 800-53 AU, D6) ────────────────────────────────
#
# CANONICAL LOCATION. When you add an append-only/immutable table, add it HERE.
# `.claude/hooks/pre_tool_use.py` re-exports this list; it used to hold the
# literal and the headless path carried a 22-entry copy that had drifted ~330
# tables behind it.
#
# This list must stay in sync with init_icdev_db.py. Run the governance
# validator to detect drift: python tools/testing/claude_dir_validator.py --json

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


# Cheap pre-guard for the append-only table check. Every protected-table pattern
# requires one of these verbs, so a command containing none of them cannot match
# and we can skip the (much larger) alternation entirely. The overwhelming
# majority of tool calls are not SQL at all.
_SQL_MUTATION_VERB_RE = re.compile(r"\b(?:update|delete|drop|truncate)\b")

# Built once per process from APPEND_ONLY_TABLES, then reused. Previously this
# check compiled three f-string regexes per table per call — 350+ tables, so
# ~1000 patterns per Bash call. re's internal cache holds 512, so it was blown
# and flushed on every single invocation and nothing was ever reused.
_APPEND_ONLY_PATTERNS: Optional[tuple] = None


def _append_only_patterns(tables: Sequence[str]) -> tuple:
    """Compile (and memoize) the protected-table patterns.

    One alternation per SQL shape instead of three regexes per table. Matching
    is equivalent: the original returned True if ANY table matched ANY shape.
    """
    global _APPEND_ONLY_PATTERNS
    if _APPEND_ONLY_PATTERNS is None:
        # De-duplicated (the source list has a few repeats) and longest-first so
        # the alternation prefers the most specific table name.
        alt = "|".join(
            re.escape(t) for t in sorted(set(tables), key=lambda t: (-len(t), t))
        )
        _APPEND_ONLY_PATTERNS = (
            re.compile(rf"(?:update|delete)\s+(?:from\s+)?(?:{alt})"),
            re.compile(rf"drop\s+table\s+.*(?:{alt})"),
            re.compile(rf"truncate\s+.*(?:{alt})"),
        )
    return _APPEND_ONLY_PATTERNS


# ── Tool-call classification ───────────────────────────────────────────────

PATH_CLAUDE_CODE = "claude_code"
PATH_HEADLESS = "headless"

# Keys whose value is text that will be EXECUTED — a shell command or a SQL
# statement. Order matters: the first non-empty one wins.
_COMMAND_KEYS = ("command", "cmd", "script", "shell_command", "bash_command")
_SQL_KEYS = ("sql", "sql_query", "query", "statement")

# Keys whose value is a filesystem path the tool will touch.
_PATH_KEYS = ("file_path", "notebook_path", "filepath", "target_file", "filename")

# Keys whose value is a file BODY the tool will write. Deliberately distinct
# from _SQL_KEYS: writing a file that contains "DELETE FROM audit_trail" does
# not delete anything, executing it does. Scanning file bodies for protected-
# table SQL flags nine legitimate files in this repo (the audit tests that
# assert the DELETE is refused, chief among them), so the append-only check
# reads executed text only. Writes are covered by the file-tier and direct-
# sqlite checks instead.
_CONTENT_KEYS = ("content", "new_string", "new_str", "contents", "text", "body")

# Tools that neither execute text nor touch a specific file. Classifying these
# as no-ops is what keeps read-only traffic cheap: nothing is extracted, so
# every check exits on its first guard.
KNOWN_INERT_TOOLS = frozenset({
    "Glob", "Grep", "LS", "WebFetch", "WebSearch", "TodoWrite", "Task",
    "BashOutput", "KillBash", "ExitPlanMode", "ListMcpResourcesTool",
})

# Tools that read one named file. `path` is deliberately NOT in _PATH_KEYS,
# because for Glob/Grep it is a search root rather than a file being accessed.
KNOWN_READ_TOOLS = frozenset({"Read", "NotebookRead", "ReadMcpResourceTool"})

# Tools that write one named file.
KNOWN_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# Tools that execute a shell command or SQL.
KNOWN_COMMAND_TOOLS = frozenset({"Bash", "bash", "shell", "sh", "sql", "PowerShell"})

_KNOWN_TOOLS = (
    KNOWN_INERT_TOOLS | KNOWN_READ_TOOLS | KNOWN_WRITE_TOOLS | KNOWN_COMMAND_TOOLS
)


@dataclass
class ToolCall:
    """A tool invocation, normalised so checks never branch on a tool name.

    Attributes:
        name: The raw tool name, kept for audit records only.
        tool_input: The raw input dict.
        command: Text that will be executed (shell command or SQL). Empty
            when the call executes nothing.
        file_path: Path the call reads or writes. Empty when it touches none.
        content: File body the call would write. Empty for reads.
        is_write: The call creates or modifies a file.
        recognised: The tool name is one this repo knows. Unrecognised tools
            are classified from their input shape and scanned — never trusted
            because their name was unfamiliar.
    """

    name: str
    tool_input: Dict[str, Any] = field(default_factory=dict)
    command: str = ""
    file_path: str = ""
    content: str = ""
    is_write: bool = False
    recognised: bool = False


def _first_str(source: Dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        val = source.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def classify(tool_name: str, tool_input: Optional[Dict[str, Any]]) -> ToolCall:
    """Normalise ``(tool_name, tool_input)`` into a :class:`ToolCall`.

    Known tools classify exactly as the Claude Code hook always treated them,
    so its behaviour is unchanged. Unknown tools are classified by the shape of
    their input: a ``command``/``sql``/``query`` key means executed text, a
    ``file_path`` plus a body means a write.
    """
    name = tool_name or ""
    data = tool_input if isinstance(tool_input, dict) else {}
    call = ToolCall(name=name, tool_input=data, recognised=name in _KNOWN_TOOLS)

    if name in KNOWN_INERT_TOOLS:
        return call

    if name in KNOWN_READ_TOOLS:
        call.file_path = _first_str(data, _PATH_KEYS)
        return call

    if name in KNOWN_WRITE_TOOLS:
        call.file_path = _first_str(data, _PATH_KEYS)
        call.content = _first_str(data, _CONTENT_KEYS)
        call.is_write = True
        return call

    if name in KNOWN_COMMAND_TOOLS:
        call.command = _first_str(data, _COMMAND_KEYS) or _first_str(data, _SQL_KEYS)
        return call

    # Unrecognised tool. Scan whatever it carries rather than waving it through.
    call.command = _first_str(data, _COMMAND_KEYS) or _first_str(data, _SQL_KEYS)
    call.file_path = _first_str(data, _PATH_KEYS)
    call.content = _first_str(data, _CONTENT_KEYS)
    call.is_write = bool(call.content)
    return call


# ── Checks ─────────────────────────────────────────────────────────────────
#
# Each returns a block reason, or "" / None to allow. All of them must fail
# OPEN on an internal error: a guard that cannot evaluate must never be the
# reason a session cannot work.


# OPT-51 — git destructive-command blocklist (adapted from
# mattpocock/skills/git-guardrails-claude-code, MIT). See
# _ATTRIBUTION_REGISTRY in tools/workflow/coherence_checker.py.
#
# These commands can silently destroy work in a non-recoverable way.
_GIT_DANGER_PATTERNS = [
    # Force push — direct or via shorthand
    (r"\bgit\s+push\s+(?:[^\n]*\s)?(?:--force\b|--force-with-lease\b|-f\b)",
     "git push --force is destructive — rewrites remote history"),
    # Hard reset anything
    (r"\bgit\s+reset\s+(?:[^\n]*\s)?--hard\b",
     "git reset --hard discards uncommitted changes"),
    # Force delete branches. -D is case-sensitive (distinct from safe -d).
    (r"\bgit\s+branch\s+(?:[^\n]*\s)?(?-i:-D)\b",
     "git branch -D force-deletes branches (use -d for safe delete)"),
    (r"\bgit\s+branch\s+(?:[^\n]*\s)?--delete\s+--force\b",
     "git branch --delete --force force-deletes branches"),
    # Dangerous clean
    (r"\bgit\s+clean\s+(?:[^\n]*\s)?-[a-zA-Z]*f[a-zA-Z]*\b",
     "git clean -f permanently deletes untracked files"),
    # Checkout/restore of working tree — can wipe uncommitted edits
    (r"\bgit\s+checkout\s+(?:--\s*\.|\.\s*$|\.\s)",
     "git checkout . discards uncommitted working-tree changes"),
    (r"\bgit\s+restore\s+(?:[^\n]*\s)?(?:--\s*\.|\.\s*$|\.\s)",
     "git restore . discards uncommitted working-tree changes"),
    # Commit amend on already-pushed commits is risky (heuristic — block
    # only when combined with no-edit or with --force elsewhere)
    (r"\bgit\s+rebase\s+(?:[^\n]*\s)?-i\b",
     "interactive git rebase requires manual approval (no automation)"),
]


def check_git_danger(call: ToolCall) -> str:
    """Block destructive git commands (OPT-51).

    Headless-only by declaration, not by accident. These patterns refuse
    ``git push --force``, ``git reset --hard`` and ``git rebase -i`` outright,
    which is right for an unattended agent and wrong for an interactive session
    where a human is watching and can authorise a rewrite. Claude Code sessions
    opt in with ICDEV_GIT_DANGER_GUARD=1.
    """
    if not call.command:
        return ""
    for pattern, reason in _GIT_DANGER_PATTERNS:
        if re.search(pattern, call.command, re.IGNORECASE):
            return reason
    return ""


def check_env_file_access(call: ToolCall) -> str:
    """Block reads and writes of .env files containing secrets."""
    if call.file_path:
        if ".env" in call.file_path and not call.file_path.endswith(".env.sample"):
            return (
                "BLOCKED: Access to .env files is prohibited. "
                "Use AWS Secrets Manager."
            )

    if call.command:
        env_patterns = [
            r"\b\.env\b(?!\.sample)",
            r"cat\s+.*\.env\b(?!\.sample)",
            r"echo\s+.*>\s*\.env\b(?!\.sample)",
        ]
        for pattern in env_patterns:
            if re.search(pattern, call.command):
                return (
                    "BLOCKED: Access to .env files is prohibited. "
                    "Use AWS Secrets Manager."
                )

    return ""


def check_dangerous_rm(call: ToolCall) -> str:
    """Block recursive-force rm, and any recursive rm at a dangerous path."""
    if not call.command:
        return ""
    normalized = " ".join(call.command.lower().split())

    patterns = [
        r"\brm\s+.*-[a-z]*r[a-z]*f",
        r"\brm\s+.*-[a-z]*f[a-z]*r",
        r"\brm\s+--recursive\s+--force",
        r"\brm\s+--force\s+--recursive",
        r"\brm\s+-r\s+.*-f",
        r"\brm\s+-f\s+.*-r",
    ]
    for pattern in patterns:
        if re.search(pattern, normalized):
            return "BLOCKED: Dangerous rm command detected and prevented"

    # Recursive rm targeting a dangerous path
    dangerous_paths = [r"/", r"/\*", r"~", r"~/", r"\$HOME", r"\.\.", r"\*", r"\."]
    if re.search(r"\brm\s+.*-[a-z]*r", normalized):
        for path in dangerous_paths:
            if re.search(path, normalized):
                return "BLOCKED: Dangerous rm command detected and prevented"

    return ""


def check_append_only_tables(call: ToolCall) -> str:
    """Block UPDATE/DELETE/DROP/TRUNCATE on append-only tables (NIST AU, D6).

    Scans executed text only — a shell command or a SQL statement. See
    ``_CONTENT_KEYS`` for why file bodies are deliberately excluded.
    """
    if not call.command:
        return ""
    command = call.command.lower()
    # Every pattern below requires one of these verbs, so a command without any
    # of them cannot match. Skipping the alternation here is what keeps this
    # check off the critical path for ordinary (non-SQL) commands.
    if not _SQL_MUTATION_VERB_RE.search(command):
        return ""
    for pattern in _append_only_patterns(APPEND_ONLY_TABLES):
        match = pattern.search(command)
        if match:
            return (
                "BLOCKED: Append-only table (D6, NIST 800-53 AU). "
                "No UPDATE/DELETE/DROP/TRUNCATE allowed. "
                f"Matched: {match.group(0)[:120]}"
            )
    return ""


# Files that legitimately need raw sqlite3.
_SQLITE_EXEMPT_PATTERNS = [
    "tools/db/storage.py",
    "tools/db/init_icdev_db.py",
    "tools/db/migration_runner.py",
    "tools/db/backup_manager.py",
    "tools/db/pg_init.py",
    "tools/db/migrate_to_storage.py",
    "tools/db/migrate_add_missing_columns.py",
    "tools/compat/db_utils.py",
    "tools/saas/",
]


def check_direct_sqlite(call: ToolCall) -> str:
    """Block direct sqlite3.connect() that bypasses the storage layer.

    Production backend is PostgreSQL. Writing to sqlite3 directly means the
    data is invisible to the dashboard and API. This has caused repeated
    confusion — data written to SQLite never appears in the UI.

    ALWAYS use: from tools.db.storage import get_connection
    """
    message = (
        "BLOCKED: Direct sqlite3.connect() bypasses the storage layer. "
        "Production backend is PostgreSQL. Use: "
        "from tools.db.storage import get_connection; conn = get_connection(). "
        "See MEMORY: feedback_always_use_get_connection.md"
    )

    if call.is_write and call.file_path:
        file_path = call.file_path.replace("\\", "/")

        # Only check files under tools/ (absolute or relative)
        if "/tools/" not in file_path and not file_path.startswith("tools/"):
            return ""
        for exempt in _SQLITE_EXEMPT_PATTERNS:
            if exempt in file_path:
                return ""
        # Canvas / child-app isolated DB initialisation
        if file_path.endswith("/init_db.py"):
            return ""
        if "sqlite3.connect(" in call.content:
            return message
        return ""

    if call.command:
        # Block ad-hoc sqlite3.connect to icdev.db in python one-liners
        if "sqlite3.connect" in call.command and "icdev.db" in call.command:
            if any(
                x in call.command
                for x in ("init_icdev_db", "migration_runner", "migrate_to_storage", "backup")
            ):
                return ""
            return message

    return ""


# ── D-ORCH-8 tiered file access ────────────────────────────────────────────

_FILE_ACCESS_TIERS_CACHE: Optional[dict] = None
_FILE_ACCESS_TIERS_LOADED = False


def _read_file_access_tiers() -> Optional[dict]:
    """Uncached read of the tier config. Returns None when absent or disabled."""
    try:
        import yaml
    except ImportError:
        return None
    config_path = repo_root() / "args" / "file_access_tiers.yaml"
    if not config_path.exists():
        return None
    try:
        with open(config_path, "r", encoding="utf-8", newline="") as fh:
            config = yaml.safe_load(fh)
        tiers = (config or {}).get("file_access_tiers", {})
        if not tiers.get("enabled", False):
            return None
        return tiers
    except Exception:
        return None


def _load_file_access_tiers() -> Optional[dict]:
    """Memoized tier config.

    This is consulted on every tool call, and re-parsing the YAML from disk each
    time bought nothing — the Claude Code hook is a short-lived process, so the
    config cannot change underneath a single invocation.
    """
    global _FILE_ACCESS_TIERS_CACHE, _FILE_ACCESS_TIERS_LOADED
    if _FILE_ACCESS_TIERS_LOADED:
        return _FILE_ACCESS_TIERS_CACHE
    _FILE_ACCESS_TIERS_LOADED = True
    _FILE_ACCESS_TIERS_CACHE = _read_file_access_tiers()
    return _FILE_ACCESS_TIERS_CACHE


def _matches_tier(file_path: str, patterns: List[str]) -> bool:
    """Check if file_path matches any pattern in the tier (glob-style)."""
    if not file_path:
        return False
    # Normalize to forward slashes and strip leading ./
    fp = file_path.replace("\\", "/")
    if fp.startswith("./"):
        fp = fp[2:]
    for pattern in patterns:
        if pattern.startswith("!"):
            continue  # exclusion patterns handled separately
        # Check exclusions first
        excluded = False
        for exc in patterns:
            if exc.startswith("!") and fnmatch(fp, exc[1:]):
                excluded = True
                break
        if excluded:
            continue
        if fnmatch(fp, pattern) or fnmatch(os.path.basename(fp), pattern):
            return True
    return False


def check_file_access_tiers(call: ToolCall) -> str:
    """Tiered file access control (D-ORCH-8): zero_access, read_only, no_delete."""
    tiers = _load_file_access_tiers()
    if not tiers:
        return ""

    file_path = call.file_path
    is_write = call.is_write
    is_delete = False

    if call.command:
        # rm/delete targeting a protected file
        rm_match = re.search(r"\brm\s+(?:-[a-z]*\s+)*([^\s|;&]+)", call.command)
        if rm_match:
            file_path = rm_match.group(1)
            is_delete = True
        # write redirection
        redir_match = re.search(r">\s*([^\s|;&]+)", call.command)
        if redir_match and not is_delete:
            file_path = redir_match.group(1)
            is_write = True

    if not file_path:
        return ""

    zero_patterns = [p for t in [tiers.get("zero_access", {})] for p in t.get("patterns", [])]
    if _matches_tier(file_path, zero_patterns):
        return f"BLOCKED: File '{file_path}' is in zero_access tier (D-ORCH-8). No access allowed."

    ro_patterns = [p for t in [tiers.get("read_only", {})] for p in t.get("patterns", [])]
    if (is_write or is_delete) and _matches_tier(file_path, ro_patterns):
        return f"BLOCKED: File '{file_path}' is in read_only tier (D-ORCH-8). Write/delete prohibited."

    nd_patterns = [p for t in [tiers.get("no_delete", {})] for p in t.get("patterns", [])]
    if is_delete and _matches_tier(file_path, nd_patterns):
        return f"BLOCKED: File '{file_path}' is in no_delete tier (D-ORCH-8). Deletion prohibited."

    return ""


# ── Remote branch deletion ─────────────────────────────────────────────────


def _remote_branch_delete_targets(command: str) -> List[str]:
    """Remote branches a command would DELETE. Empty when it deletes nothing.

    Recognises the three forms that actually reach GitHub:
        git push <remote> --delete <branch> [<branch>...]
        git push <remote> :<branch>
        gh api -X DELETE .../git/refs/heads/<branch>

    ``git branch -D`` is deliberately NOT included: it deletes a LOCAL ref, which
    cannot close a PR or remove anything from the remote.
    """
    if "push" not in command and "DELETE" not in command:
        return []
    try:
        tokens = shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        return []

    out: List[str] = []
    for i, tok in enumerate(tokens):
        if tok == "push":
            rest = tokens[i + 1:]
            positional = [t for t in rest if not t.startswith("-")]
            if any(t in ("--delete", "-d") for t in rest):
                # everything positional after the remote is a branch
                out += positional[1:] if len(positional) > 1 else []
            out += [t[1:] for t in rest if t.startswith(":") and len(t) > 1]
        elif tok == "DELETE":
            for t in tokens[i + 1:]:
                if "git/refs/heads/" in t:
                    out.append(t.split("git/refs/heads/", 1)[1].strip("/"))
    return [b for b in out if b]


def check_branch_deletion(call: ToolCall) -> str:
    """Refuse to delete a remote branch that still holds unmerged commits.

    On 2026-08-07 an agent "cleaning up duplicates" deleted four remote branches
    matching one task id in a six-second burst. One was PR #1332 with every check
    green: deleting a head branch makes GitHub close the PR as CLOSED-not-merged,
    so the work vanished with no review and no failure — and `gh pr reopen` then
    fails because the head ref is gone.

    A task legitimately has several branches — a retry, a rebase, a rival
    implementation, a human's fix. Sharing a task id is never grounds for
    deletion. Unmerged commits are grounds for refusal.

    Compared locally with `git cherry` so the check stays fast and works offline,
    and fails OPEN on any error.
    """
    if not call.command:
        return ""
    if _env_off("ICDEV_BRANCH_DELETE_GUARD", default="1"):
        return ""
    branches = _remote_branch_delete_targets(call.command)
    if not branches:
        return ""
    try:
        root = repo_root()
        blocked = []
        for br in branches:
            res = subprocess.run(  # nosec B603 B607 — fixed argv, shell=False
                ["git", "cherry", "origin/main", f"origin/{br}"],
                cwd=str(root), capture_output=True, text=True, timeout=30,
                shell=False,
            )
            if res.returncode != 0:
                continue  # cannot compare -> allow (fail open)
            unique = [ln for ln in res.stdout.splitlines() if ln.startswith("+")]
            if unique:
                blocked.append((br, len(unique)))
        if not blocked:
            return ""
        detail = "\n".join(
            f"    origin/{b}: {n} commit(s) not on origin/main" for b, n in blocked)
        return (
            "BLOCKED: refusing to delete a remote branch that still holds "
            "unmerged work.\n" + detail + "\n"
            "  Deleting a head branch CLOSES its pull request as closed-not-merged,\n"
            "  and `gh pr reopen` cannot undo it once the ref is gone.\n"
            "  Merge or explicitly close the PR first. A shared task id is not a\n"
            "  reason to delete a branch — a task legitimately has several.\n"
            "  Deliberate discard: ICDEV_BRANCH_DELETE_GUARD=0"
        )
    except Exception:
        return ""  # fail open — never block on a broken guard


# ── Worktree path enforcement ──────────────────────────────────────────────


def _worktree_add_target(command: str, posix: Optional[bool] = None) -> Optional[str]:
    """Target path of a ``git worktree add`` in *command*, or None.

    Returns None whenever the path cannot be determined with confidence — the
    caller treats that as "allow". A parser that guesses would block legitimate
    work, which is strictly worse than failing to catch a stray worktree.

    The shlex mode must follow the OS, because neither mode is correct on both:

        "git worktree add C:\\Users\\u\\wt"   posix=True  -> "C:Usersuwt"     WRONG
        "git worktree add /tmp/my\\ dir"      posix=False -> ["/tmp/my\\","dir"] WRONG

    On Windows a backslash is a path separator; on POSIX it is an escape. Pick
    by platform rather than hardcoding either. *posix* is overridable so both
    branches stay testable on a single host.
    """
    if "worktree" not in command or "add" not in command:
        return None
    if posix is None:
        posix = os.name != "nt"
    try:
        tokens = shlex.split(command, posix=posix)
        if not posix:
            # Non-posix mode leaves quotes attached to the token.
            tokens = [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t
                      for t in tokens]
    except ValueError:
        return None

    for i in range(len(tokens) - 2):
        if tokens[i].endswith("git") and tokens[i + 1] == "worktree" and tokens[i + 2] == "add":
            rest = tokens[i + 3:]
            break
    else:
        return None

    skip_next = False
    for tok in rest:
        if skip_next:
            skip_next = False
            continue
        if tok in ("-b", "-B", "--reason", "--lock-reason"):
            skip_next = True          # these take a value that is NOT the path
            continue
        if tok.startswith("-"):
            continue                  # --detach, --no-checkout, --force, ...
        return tok                    # first positional is the worktree path
    return None


def check_worktree_path(call: ToolCall) -> str:
    """Refuse a ``git worktree add`` outside the sanctioned roots.

    Measured 2026-08-07: 150 registered worktrees across 22 parent directories,
    118 of them stray — 33 flat in %TEMP%\\claude, 28 in C:\\AI\\.worktrees, 27
    nested inside another worktree. Five basenames collided across parents, and
    two simultaneous ``wt-wake2`` checkouts on different branches are how one
    session's edits appeared in another session's working tree.

    CLAUDE.md has asked for a worktree convention in prose since the beginning
    and produced those 150. A check is what makes a convention hold, so this
    blocks rather than warns. Set ICDEV_WORKTREE_GUARD=0 to disable.

    Fails OPEN: any resolution error allows the command. A guard that cannot
    parse a path must not be the reason a session cannot work.
    """
    if not call.command:
        return ""
    if _env_off("ICDEV_WORKTREE_GUARD", default="1"):
        return ""
    target = _worktree_add_target(call.command)
    if not target:
        return ""
    try:
        root = repo_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.git.worktree_paths import describe_violation, is_sanctioned

        if is_sanctioned(target, repo_root=root):
            return ""
        return "BLOCKED: " + describe_violation(target)
    except Exception:
        return ""  # fail open — never block on a broken guard


# ── Pre-commit review loop (warn-only) ─────────────────────────────────────


def check_review_loop_precommit(call: ToolCall) -> str:
    """Self-green staged changes with review_loop before a `git commit`.

    Fires only on `git commit` calls. Runs the fast, staged, ruff-only review
    loop (coherence/SIPA are too slow for a commit gate — they run in the pre-PR
    preflight + CI), applies deterministic ruff autofixes to the staged `.py`
    files, and re-stages them so the commit lands lint-clean.

    Registered with severity "warn", so a non-green commit proceeds. Set
    ICDEV_REVIEW_LOOP_BLOCK=1 to hard-block, or ICDEV_REVIEW_LOOP_PRECOMMIT=0 to
    disable. Best-effort: any error is swallowed so it can never break a commit.
    """
    if not call.command or "git commit" not in call.command:
        return ""
    if _env_off("ICDEV_REVIEW_LOOP_PRECOMMIT", default="1"):
        return ""

    try:
        root = repo_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.quality.review_loop import preflight

        report = preflight(
            base=None, autofix=True, staged=True,
            only_gates=["ruff"], coherence_scope="changed",
            audit=False, max_iterations=1, repo_root=root,
        )
        # Re-stage whatever ruff --fix rewrote so the fixes are committed.
        if report.changed_files:
            subprocess.run(  # nosec B603 B607 — fixed argv, shell=False
                ["git", "add", *report.changed_files],
                cwd=str(root), capture_output=True, text=True, timeout=30,
                shell=False,
            )
        if not report.green:
            n = len(report.fix_brief)
            return (
                f"review_loop (pre-commit): {n} unfixable lint finding(s) remain "
                f"in staged files — {report.reason}"
            )
        return ""
    except Exception:
        return ""  # never break a commit


# ── Registry ───────────────────────────────────────────────────────────────


def _env_off(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("0", "false", "no", "off")


def _env_on(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Check:
    """One guardrail.

    Attributes:
        name: Stable identifier, recorded on the ``hook_events`` audit row.
        fn: ``(ToolCall) -> str`` returning a block reason, or "" to allow.
        severity: ``"block"`` refuses the call; ``"warn"`` records and proceeds.
        paths: Which orchestration paths run this check. A check that is not
            universal must say so here — that is what makes the difference
            testable instead of accidental.
        opt_in_env: When set, the check also runs on paths outside ``paths`` if
            this environment variable is truthy.
        escalate_env: When set and truthy, a ``"warn"`` finding hard-blocks.
    """

    name: str
    fn: Callable[[ToolCall], str]
    severity: str = "block"
    paths: frozenset = frozenset({PATH_CLAUDE_CODE, PATH_HEADLESS})
    opt_in_env: str = ""
    escalate_env: str = ""

    def blocks(self) -> bool:
        """True when a finding from this check should refuse the tool call."""
        return self.severity == "block" or (
            bool(self.escalate_env) and _env_on(self.escalate_env)
        )


CHECKS: tuple = (
    # Headless-only by declaration: these refuse force-push/hard-reset outright,
    # which suits an unattended agent but not an interactive session where a
    # human can authorise a history rewrite. Claude Code opts in via the env var.
    Check(
        "git_danger",
        check_git_danger,
        paths=frozenset({PATH_HEADLESS}),
        opt_in_env="ICDEV_GIT_DANGER_GUARD",
    ),
    Check("env_file_access", check_env_file_access),
    Check("dangerous_rm", check_dangerous_rm),
    Check("append_only_tables", check_append_only_tables),
    Check("direct_sqlite", check_direct_sqlite),
    Check("file_access_tiers", check_file_access_tiers),
    Check("branch_deletion", check_branch_deletion),
    Check("worktree_path", check_worktree_path),
    Check(
        "review_loop_precommit",
        check_review_loop_precommit,
        severity="warn",
        escalate_env="ICDEV_REVIEW_LOOP_BLOCK",
    ),
)


@dataclass
class CheckOutcome:
    """Result of running the registry over one tool call."""

    allowed: bool = True
    reason: str = ""
    check: str = ""
    warnings: List[Dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason or ("passed safety checks" if self.allowed else ""),
            "check": self.check,
            "warnings": list(self.warnings),
        }


def checks_for(path: str) -> tuple:
    """Checks that run on *path*, honouring per-check opt-in env vars."""
    return tuple(
        c for c in CHECKS
        if path in c.paths or (c.opt_in_env and _env_on(c.opt_in_env))
    )


def evaluate(
    tool_name: str,
    tool_input: Optional[Dict[str, Any]] = None,
    path: str = PATH_HEADLESS,
) -> CheckOutcome:
    """Run every check registered for *path* against one tool call.

    Stops at the first blocking check. ``severity="warn"`` findings are
    collected and returned alongside an allow, so the caller can surface them
    without refusing the call.

    A check that raises is treated as a pass (fail open) — a broken guard must
    never be the reason a session cannot work — but the failure is recorded as
    a warning so it is visible rather than silent.
    """
    call = classify(tool_name, tool_input)
    outcome = CheckOutcome()

    for check in checks_for(path):
        try:
            reason = check.fn(call) or ""
        except Exception as exc:  # noqa: BLE001 — fail open, but visibly
            outcome.warnings.append({
                "check": check.name,
                "reason": f"check raised {type(exc).__name__}: {exc}",
                "severity": "error",
            })
            continue
        if not reason:
            continue
        if not check.blocks():
            outcome.warnings.append({
                "check": check.name, "reason": reason, "severity": "warn",
            })
            continue
        outcome.allowed = False
        outcome.reason = reason if reason.startswith("BLOCKED") else f"BLOCKED: {reason}"
        outcome.check = check.name
        return outcome

    return outcome
