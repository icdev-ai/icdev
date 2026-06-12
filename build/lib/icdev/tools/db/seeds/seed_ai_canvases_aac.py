# CUI // SP-CTI
"""Seed DoD/IC synthetic demo data for the AI Augmentation Canvas (AAC).

Populates: aac_scans, aac_opportunities, aac_scores, aac_roadmaps

All PKs are SERIAL/AUTOINCREMENT — we use lastrowid to chain FKs.
composite_score = value*0.45 + feasibility*0.35 + (1-risk)*0.20

Run:
    python tools/db/seeds/seed_ai_canvases_aac.py
    python tools/db/seeds/seed_ai_canvases_aac.py --reset
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.ai_augmentation.db.init_db import get_connection, init_db  # noqa: E402
from tools.ai_augmentation.constants import SCORING_WEIGHTS  # noqa: E402


def _now(offset_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()


def _composite(value: float, feasibility: float, risk: float) -> float:
    w = SCORING_WEIGHTS["composite"]
    return round(value * w["value"] + feasibility * w["feasibility"] + (1 - risk) * w["risk_inverted"], 3)


# ── 5 AAC Scan Definitions ────────────────────────────────────────────────────
# Each dict defines: scan metadata, list of opportunities (each with scores),
# and roadmap phases with cross-links to AIMC/AADC designs.

SCANS = [
    # ── 1. GCSS-Army Logistics ─────────────────────────────────────────────────
    {
        "ref": "gcss-army-logistics-v1",
        "input_type": "git_repo",
        "input_ref": "git@gitlab.army.mil/gcss-army/logistics-core.git@main",
        "language_profile": {"java": 0.87, "xml": 0.09, "sql": 0.04},
        "total_files": 1842,
        "total_loc": 287000,
        "offset_hours": 504,
        "opportunities": [
            {
                "module_path": "com/army/gcss/maintenance/EquipmentClassifier.java",
                "function_name": "classifyEquipmentStatus",
                "line_start": 412, "line_end": 589,
                "language": "java",
                "pattern_type": "nested_conditionals",
                "ai_paradigm": "ml_classifier",
                "il_model": "qwen3-local",
                "value": 0.88, "feasibility": 0.82, "risk": 0.28,
                "pattern_detail": {
                    "nesting_depth": 7,
                    "conditions_count": 43,
                    "test_coverage_pct": 61,
                    "change_frequency_per_month": 4.2,
                },
                "data_requirements": {
                    "historical_labels": "142K labeled maintenance records",
                    "retrain_cadence": "quarterly",
                    "label_source": "G-4 maintenance technicians",
                },
            },
            {
                "module_path": "com/army/gcss/maintenance/MaintenanceDecisionMap.java",
                "function_name": "resolveMaintenanceAction",
                "line_start": 87, "line_end": 214,
                "language": "java",
                "pattern_type": "large_rule_table",
                "ai_paradigm": "decision_agent",
                "il_model": "claude-sonnet-4-6",
                "value": 0.85, "feasibility": 0.79, "risk": 0.19,
                "pattern_detail": {
                    "map_keys": 187,
                    "static_rules": True,
                    "last_updated": "FY21Q3",
                    "annual_exceptions": 34,
                },
                "data_requirements": {
                    "training": "FY20–FY24 maintenance decision logs",
                    "feedback_loop": "technician override signals",
                },
            },
            {
                "module_path": "com/army/gcss/parts/PartAvailabilityService.java",
                "function_name": "forecastPartDemand",
                "line_start": 23, "line_end": 89,
                "language": "java",
                "pattern_type": "hardcoded_threshold",
                "ai_paradigm": "anomaly_detection",
                "il_model": "sagemaker-custom",
                "value": 0.76, "feasibility": 0.71, "risk": 0.24,
                "pattern_detail": {
                    "threshold_count": 12,
                    "values": [0.2, 0.4, 0.6, 0.8],
                    "context": "reorder point calculation",
                },
                "data_requirements": {
                    "training": "NSN part consumption history FY18–FY24",
                    "seasonality": True,
                },
            },
            {
                "module_path": "com/army/gcss/reporting/ReadinessReportBuilder.java",
                "function_name": "buildReadinessNarrative",
                "line_start": 198, "line_end": 267,
                "language": "java",
                "pattern_type": "string_template_rendering",
                "ai_paradigm": "llm_generation",
                "il_model": "claude-sonnet-4-6",
                "value": 0.71, "feasibility": 0.80, "risk": 0.15,
                "pattern_detail": {
                    "template_vars": 18,
                    "output_format": "AR 220-1 readiness narrative",
                    "monthly_volume": 2400,
                },
                "data_requirements": {
                    "corpus": "500 historical AR 220-1 examples",
                    "tone_guidelines": "Army writing standards",
                },
            },
            {
                "module_path": "com/army/gcss/alerts/MaintenanceAlertScheduler.java",
                "function_name": "triggerScheduledAlerts",
                "line_start": 55, "line_end": 120,
                "language": "java",
                "pattern_type": "scheduled_cron",
                "ai_paradigm": "agentic_trigger",
                "il_model": "claude-haiku-4-5",
                "value": 0.68, "feasibility": 0.74, "risk": 0.20,
                "pattern_detail": {
                    "cron_expression": "0 6 * * 1",
                    "downstream_systems": ["G-4 GCSS portal", "email"],
                    "false_positive_rate": 0.23,
                },
                "data_requirements": {
                    "training": "Alert outcome labels (escalated / closed / false_positive)",
                },
            },
            {
                "module_path": "com/army/gcss/supply/SupplyKeywordSearch.java",
                "function_name": "findNSNByDescription",
                "line_start": 301, "line_end": 356,
                "language": "java",
                "pattern_type": "keyword_list_search",
                "ai_paradigm": "embedding_search",
                "il_model": "text-embedding-3-small",
                "value": 0.73, "feasibility": 0.84, "risk": 0.12,
                "pattern_detail": {
                    "keyword_list_size": 14200,
                    "exact_match_only": True,
                    "nsn_catalog_size": 6000000,
                },
                "data_requirements": {
                    "embeddings": "NSN catalog descriptions",
                    "index": "FAISS / OpenSearch",
                },
            },
        ],
        "roadmap": {
            "roadmap_id": "rm-gcss-001",
            "title": "GCSS-Army AI Modernization Roadmap",
            "phases": [
                {
                    "phase": 1, "title": "High-Value Quick Wins",
                    "items": [
                        {"opp": "MaintenanceDecisionMap.java — resolveMaintenanceAction", "effort_days": 25, "model": "decision_agent"},
                        {"opp": "SupplyKeywordSearch.java — findNSNByDescription", "effort_days": 12, "model": "embedding_search"},
                    ],
                    "effort_days": 37,
                },
                {
                    "phase": 2, "title": "Classifier + Anomaly Detection",
                    "items": [
                        {"opp": "EquipmentClassifier.java — classifyEquipmentStatus", "effort_days": 42, "model": "ml_classifier"},
                        {"opp": "PartAvailabilityService.java — forecastPartDemand", "effort_days": 30, "model": "anomaly_detection"},
                    ],
                    "effort_days": 72,
                },
                {
                    "phase": 3, "title": "LLM + Agentic Triggers",
                    "items": [
                        {"opp": "ReadinessReportBuilder.java — buildReadinessNarrative", "effort_days": 18, "model": "llm_generation"},
                        {"opp": "MaintenanceAlertScheduler.java — triggerScheduledAlerts", "effort_days": 22, "model": "agentic_trigger"},
                    ],
                    "effort_days": 40,
                },
            ],
            "total_effort_days": 149,
            "aimc_links": ["aimc-dod-003"],
            "aadc_links": [],
        },
    },

    # ── 2. Legacy SIEM Rules Engine ────────────────────────────────────────────
    {
        "ref": "legacy-siem-rules-v1",
        "input_type": "git_repo",
        "input_ref": "git@gitlab.disa.mil/soc/siem-rules-engine.git@main",
        "language_profile": {"python": 0.91, "yaml": 0.07, "json": 0.02},
        "total_files": 312,
        "total_loc": 63000,
        "offset_hours": 432,
        "opportunities": [
            {
                "module_path": "siem/rules/sigma_mapper.py",
                "function_name": "map_sigma_rule",
                "line_start": 45, "line_end": 187,
                "language": "python",
                "pattern_type": "large_rule_table",
                "ai_paradigm": "embedding_search",
                "il_model": "text-embedding-3-small",
                "value": 0.93, "feasibility": 0.89, "risk": 0.18,
                "pattern_detail": {
                    "map_keys": 88000,
                    "lookup_type": "IOC exact match",
                    "maintenance_overhead_hrs_per_month": 40,
                    "false_negative_rate": 0.14,
                },
                "data_requirements": {
                    "corpus": "88K IOC entries + STIX threat bundles",
                    "index": "FAISS local (IL4 enclave)",
                    "update_freq": "every 4 hours",
                },
            },
            {
                "module_path": "siem/rules/pattern_classifier.py",
                "function_name": "classify_alert_pattern",
                "line_start": 88, "line_end": 201,
                "language": "python",
                "pattern_type": "nested_conditionals",
                "ai_paradigm": "ml_classifier",
                "il_model": "sagemaker-custom",
                "value": 0.87, "feasibility": 0.82, "risk": 0.22,
                "pattern_detail": {
                    "nesting_depth": 5,
                    "alert_types": 23,
                    "daily_alert_volume": 120000,
                    "analyst_triage_time_min": 4.2,
                },
                "data_requirements": {
                    "training": "18 months SIEM alert logs + SOC analyst verdicts",
                    "classes": ["true_positive", "false_positive", "escalate"],
                },
            },
            {
                "module_path": "siem/correlation/threshold_engine.py",
                "function_name": "evaluate_thresholds",
                "line_start": 12, "line_end": 89,
                "language": "python",
                "pattern_type": "hardcoded_threshold",
                "ai_paradigm": "anomaly_detection",
                "il_model": "sagemaker-custom",
                "value": 0.80, "feasibility": 0.75, "risk": 0.25,
                "pattern_detail": {
                    "threshold_count": 34,
                    "static_values": [5, 10, 100, 1000],
                    "context": "login failure + port scan triggers",
                    "tuning_freq_months": 6,
                },
                "data_requirements": {
                    "training": "Baseline network + auth telemetry (FY23–FY24)",
                    "unsupervised": True,
                },
            },
            {
                "module_path": "siem/reporting/incident_narrator.py",
                "function_name": "generate_incident_summary",
                "line_start": 33, "line_end": 91,
                "language": "python",
                "pattern_type": "string_template_rendering",
                "ai_paradigm": "llm_generation",
                "il_model": "claude-sonnet-4-6",
                "value": 0.78, "feasibility": 0.86, "risk": 0.13,
                "pattern_detail": {
                    "template_vars": 11,
                    "output_format": "CISA IR report (TLP:WHITE)",
                    "monthly_incidents": 380,
                },
                "data_requirements": {
                    "corpus": "200 historical IR reports for style transfer",
                    "few_shot": 5,
                },
            },
            {
                "module_path": "siem/scheduler/rule_updater.py",
                "function_name": "schedule_rule_refresh",
                "line_start": 5, "line_end": 48,
                "language": "python",
                "pattern_type": "scheduled_cron",
                "ai_paradigm": "agentic_trigger",
                "il_model": "claude-haiku-4-5",
                "value": 0.70, "feasibility": 0.77, "risk": 0.17,
                "pattern_detail": {
                    "cron_expression": "0 */4 * * *",
                    "downstream": ["SIEM rule push", "SOC Slack alert"],
                    "stale_rule_risk": "HIGH",
                },
                "data_requirements": {
                    "training": "Rule age + hit rate + false positive history",
                },
            },
        ],
        "roadmap": {
            "roadmap_id": "rm-siem-001",
            "title": "SIEM Rules Engine AI Modernization",
            "phases": [
                {
                    "phase": 1, "title": "IOC Embedding Search Pilot",
                    "items": [
                        {"opp": "sigma_mapper.py — map_sigma_rule", "effort_days": 10, "model": "embedding_search"},
                        {"opp": "incident_narrator.py — generate_incident_summary", "effort_days": 8, "model": "llm_generation"},
                    ],
                    "effort_days": 18,
                },
                {
                    "phase": 2, "title": "Alert Classifier + Anomaly Baselines",
                    "items": [
                        {"opp": "pattern_classifier.py — classify_alert_pattern", "effort_days": 35, "model": "ml_classifier"},
                        {"opp": "threshold_engine.py — evaluate_thresholds", "effort_days": 28, "model": "anomaly_detection"},
                    ],
                    "effort_days": 63,
                },
                {
                    "phase": 3, "title": "Autonomous Rule Update Agent",
                    "items": [
                        {"opp": "rule_updater.py — schedule_rule_refresh", "effort_days": 20, "model": "agentic_trigger"},
                    ],
                    "effort_days": 20,
                },
            ],
            "total_effort_days": 101,
            "aimc_links": ["aimc-dod-004"],
            "aadc_links": ["aadc-dod-005"],
        },
    },

    # ── 3. Defense Financial System ─────────────────────────────────────────────
    {
        "ref": "dfs-financial-system-v1",
        "input_type": "git_repo",
        "input_ref": "git@gitlab.dfas.mil/finance/dfs-core.git@main",
        "language_profile": {"java": 0.78, "sql": 0.14, "xml": 0.08},
        "total_files": 7841,
        "total_loc": 1240000,
        "offset_hours": 360,
        "opportunities": [
            {
                "module_path": "gov/dfas/audit/PaymentAuditClassifier.java",
                "function_name": "classifyPaymentAnomaly",
                "line_start": 1022, "line_end": 1291,
                "language": "java",
                "pattern_type": "nested_conditionals",
                "ai_paradigm": "ml_classifier",
                "il_model": "sagemaker-custom",
                "value": 0.82, "feasibility": 0.63, "risk": 0.41,
                "pattern_detail": {
                    "nesting_depth": 9,
                    "conditions_count": 78,
                    "legacy_cobol_converted": True,
                    "last_refactored": "FY11",
                    "test_coverage_pct": 38,
                },
                "data_requirements": {
                    "training": "5.2M payment audit records FY15–FY24",
                    "compliance": "FISCAM, DoD FMR",
                    "interpretability_required": True,
                },
            },
            {
                "module_path": "gov/dfas/rules/AuditRuleEngine.java",
                "function_name": "evaluateAuditRules",
                "line_start": 234, "line_end": 512,
                "language": "java",
                "pattern_type": "large_rule_table",
                "ai_paradigm": "decision_agent",
                "il_model": "claude-sonnet-4-6",
                "value": 0.79, "feasibility": 0.60, "risk": 0.38,
                "pattern_detail": {
                    "map_keys": 342,
                    "rule_types": ["disbursement", "travel", "contract"],
                    "expert_time_to_update_hrs": 16,
                },
                "data_requirements": {
                    "training": "Audit decision history + DoD FMR guidance",
                    "hitl_required": True,
                },
            },
            {
                "module_path": "gov/dfas/reporting/FinancialNarrativeBuilder.java",
                "function_name": "buildMonthlyNarrative",
                "line_start": 88, "line_end": 165,
                "language": "java",
                "pattern_type": "string_template_rendering",
                "ai_paradigm": "llm_generation",
                "il_model": "claude-sonnet-4-6",
                "value": 0.72, "feasibility": 0.76, "risk": 0.16,
                "pattern_detail": {
                    "template_vars": 24,
                    "output": "Treasury MAX A-136 narrative",
                    "monthly_reports": 890,
                },
                "data_requirements": {
                    "corpus": "3 years A-136 narrative examples",
                    "style": "Federal accounting standards",
                },
            },
            {
                "module_path": "gov/dfas/audit/ThresholdAuditTrigger.java",
                "function_name": "checkAuditThresholds",
                "line_start": 44, "line_end": 108,
                "language": "java",
                "pattern_type": "hardcoded_threshold",
                "ai_paradigm": "anomaly_detection",
                "il_model": "sagemaker-custom",
                "value": 0.74, "feasibility": 0.65, "risk": 0.33,
                "pattern_detail": {
                    "threshold_count": 19,
                    "values": [2500.00, 10000.00, 100000.00],
                    "context": "improper payment triggers",
                },
                "data_requirements": {
                    "training": "DFAS payment anomaly labels",
                    "explainability": "SHAP required for FISCAM audit",
                },
            },
            {
                "module_path": "gov/dfas/search/VendorSearchService.java",
                "function_name": "findVendorsByDescription",
                "line_start": 67, "line_end": 113,
                "language": "java",
                "pattern_type": "keyword_list_search",
                "ai_paradigm": "embedding_search",
                "il_model": "text-embedding-3-small",
                "value": 0.65, "feasibility": 0.72, "risk": 0.14,
                "pattern_detail": {
                    "keyword_list_size": 48000,
                    "vendor_catalog_size": 290000,
                    "exact_match": True,
                },
                "data_requirements": {
                    "embeddings": "SAM.gov vendor descriptions",
                    "update_freq": "weekly",
                },
            },
        ],
        "roadmap": {
            "roadmap_id": "rm-dfs-001",
            "title": "DFAS Financial System AI Modernization",
            "phases": [
                {
                    "phase": 1, "title": "LLM Narrative + Vendor Search",
                    "items": [
                        {"opp": "FinancialNarrativeBuilder.java — buildMonthlyNarrative", "effort_days": 22, "model": "llm_generation"},
                        {"opp": "VendorSearchService.java — findVendorsByDescription", "effort_days": 14, "model": "embedding_search"},
                    ],
                    "effort_days": 36,
                },
                {
                    "phase": 2, "title": "Anomaly Detection + Classifier",
                    "items": [
                        {"opp": "ThresholdAuditTrigger.java — checkAuditThresholds", "effort_days": 38, "model": "anomaly_detection"},
                        {"opp": "PaymentAuditClassifier.java — classifyPaymentAnomaly", "effort_days": 55, "model": "ml_classifier"},
                    ],
                    "effort_days": 93,
                },
            ],
            "total_effort_days": 129,
            "aimc_links": [],
            "aadc_links": ["aadc-dod-007"],
        },
    },

    # ── 4. JTRS Radio Firmware ─────────────────────────────────────────────────
    {
        "ref": "jtrs-radio-firmware-v1",
        "input_type": "git_repo",
        "input_ref": "git@gitlab.disa.mil/jtrs/radio-firmware.git@main",
        "language_profile": {"rust": 0.89, "c": 0.09, "asm": 0.02},
        "total_files": 843,
        "total_loc": 189000,
        "offset_hours": 300,
        "opportunities": [
            {
                "module_path": "src/radio/channel_optimizer.rs",
                "function_name": "select_optimal_channel",
                "line_start": 88, "line_end": 210,
                "language": "rust",
                "pattern_type": "hardcoded_threshold",
                "ai_paradigm": "ml_classifier",
                "il_model": "qwen3-local",
                "value": 0.81, "feasibility": 0.76, "risk": 0.31,
                "pattern_detail": {
                    "threshold_count": 8,
                    "values": [-90.0, -80.0, -70.0],
                    "context": "RF signal quality RSSI thresholds",
                    "safety_critical": True,
                },
                "data_requirements": {
                    "training": "22K RF telemetry samples (JTRS field data)",
                    "safety_gate": "HITL required for safety-critical decisions",
                    "il_suitability": "qwen3-local (IL5 capable)",
                },
            },
            {
                "module_path": "src/crypto/waveform_classifier.rs",
                "function_name": "classify_waveform_type",
                "line_start": 34, "line_end": 178,
                "language": "rust",
                "pattern_type": "nested_conditionals",
                "ai_paradigm": "ml_classifier",
                "il_model": "qwen3-local",
                "value": 0.76, "feasibility": 0.70, "risk": 0.35,
                "pattern_detail": {
                    "nesting_depth": 6,
                    "waveform_types": 14,
                    "latency_budget_ms": 2,
                    "safety_critical": True,
                },
                "data_requirements": {
                    "training": "8K labeled waveform captures (TS//SI)",
                    "latency_constraint": "must run <2ms on embedded hardware",
                    "il_suitability": "qwen3-local or ONNX quantized model",
                },
            },
            {
                "module_path": "src/routing/mesh_route_table.rs",
                "function_name": "select_mesh_route",
                "line_start": 201, "line_end": 344,
                "language": "rust",
                "pattern_type": "large_rule_table",
                "ai_paradigm": "decision_agent",
                "il_model": "qwen3-local",
                "value": 0.72, "feasibility": 0.67, "risk": 0.27,
                "pattern_detail": {
                    "map_keys": 64,
                    "topology_variants": 8,
                    "degraded_mode": True,
                },
                "data_requirements": {
                    "training": "JTRS mesh topology logs FY22–FY24",
                    "edge_inference": True,
                },
            },
            {
                "module_path": "src/monitoring/link_health_monitor.rs",
                "function_name": "detect_link_degradation",
                "line_start": 55, "line_end": 112,
                "language": "rust",
                "pattern_type": "hardcoded_threshold",
                "ai_paradigm": "anomaly_detection",
                "il_model": "sagemaker-custom",
                "value": 0.69, "feasibility": 0.72, "risk": 0.22,
                "pattern_detail": {
                    "threshold_count": 6,
                    "metrics": ["packet_loss", "jitter", "rssi"],
                    "alert_latency_ms": 50,
                },
                "data_requirements": {
                    "training": "Link health telemetry (FY23–FY24)",
                    "unsupervised": True,
                },
            },
        ],
        "roadmap": {
            "roadmap_id": "rm-jtrs-001",
            "title": "JTRS Radio Firmware AI Modernization",
            "phases": [
                {
                    "phase": 1, "title": "Anomaly + Route Intelligence",
                    "items": [
                        {"opp": "link_health_monitor.rs — detect_link_degradation", "effort_days": 18, "model": "anomaly_detection"},
                        {"opp": "mesh_route_table.rs — select_mesh_route", "effort_days": 25, "model": "decision_agent"},
                    ],
                    "effort_days": 43,
                },
                {
                    "phase": 2, "title": "Safety-Critical Classifiers",
                    "items": [
                        {"opp": "channel_optimizer.rs — select_optimal_channel", "effort_days": 40, "model": "ml_classifier"},
                        {"opp": "waveform_classifier.rs — classify_waveform_type", "effort_days": 35, "model": "ml_classifier"},
                    ],
                    "effort_days": 75,
                },
            ],
            "total_effort_days": 118,
            "aimc_links": [],
            "aadc_links": [],
        },
    },

    # ── 5. HR Readiness Portal ─────────────────────────────────────────────────
    {
        "ref": "hr-readiness-portal-v1",
        "input_type": "git_repo",
        "input_ref": "git@gitlab.army.mil/g1/hr-readiness-portal.git@main",
        "language_profile": {"typescript": 0.82, "python": 0.14, "sql": 0.04},
        "total_files": 287,
        "total_loc": 41000,
        "offset_hours": 240,
        "opportunities": [
            {
                "module_path": "src/eligibility/TrainingEligibilityChecker.ts",
                "function_name": "checkTrainingEligibility",
                "line_start": 44, "line_end": 167,
                "language": "typescript",
                "pattern_type": "nested_conditionals",
                "ai_paradigm": "ml_classifier",
                "il_model": "claude-sonnet-4-6",
                "value": 0.84, "feasibility": 0.80, "risk": 0.22,
                "pattern_detail": {
                    "nesting_depth": 5,
                    "criteria_count": 19,
                    "policy_updates_per_year": 6,
                    "rights_impacting": True,
                },
                "data_requirements": {
                    "training": "3 years eligibility decisions + outcomes",
                    "fairness_audit": "demographic parity required",
                    "hitl_required": True,
                },
            },
            {
                "module_path": "src/search/PersonnelSearchService.ts",
                "function_name": "searchPersonnelBySkill",
                "line_start": 22, "line_end": 78,
                "language": "typescript",
                "pattern_type": "keyword_list_search",
                "ai_paradigm": "embedding_search",
                "il_model": "text-embedding-3-small",
                "value": 0.80, "feasibility": 0.88, "risk": 0.12,
                "pattern_detail": {
                    "keyword_list_size": 3400,
                    "personnel_records": 124000,
                    "search_freq_per_day": 8200,
                },
                "data_requirements": {
                    "embeddings": "Skill taxonomy + MOS codes",
                    "privacy": "PII minimization required",
                },
            },
            {
                "module_path": "api/reports/ReadinessNarrativeAPI.py",
                "function_name": "generate_readiness_report",
                "line_start": 15, "line_end": 67,
                "language": "python",
                "pattern_type": "string_template_rendering",
                "ai_paradigm": "llm_generation",
                "il_model": "claude-sonnet-4-6",
                "value": 0.75, "feasibility": 0.83, "risk": 0.14,
                "pattern_detail": {
                    "template_vars": 9,
                    "output": "G-1 readiness summary memo",
                    "monthly_volume": 580,
                },
                "data_requirements": {
                    "corpus": "200 historical readiness memos",
                    "pii_scrub": True,
                },
            },
            {
                "module_path": "src/rules/EligibilityRuleMap.ts",
                "function_name": "resolveEligibilityRule",
                "line_start": 88, "line_end": 201,
                "language": "typescript",
                "pattern_type": "large_rule_table",
                "ai_paradigm": "decision_agent",
                "il_model": "claude-sonnet-4-6",
                "value": 0.71, "feasibility": 0.74, "risk": 0.19,
                "pattern_detail": {
                    "map_keys": 47,
                    "policy_driven": True,
                    "exception_rate_pct": 8.4,
                },
                "data_requirements": {
                    "training": "G-1 policy decision logs + exception justifications",
                    "hitl_required": True,
                },
            },
        ],
        "roadmap": {
            "roadmap_id": "rm-hr-001",
            "title": "HR Readiness Portal AI Modernization",
            "phases": [
                {
                    "phase": 1, "title": "Semantic Search + LLM Reports",
                    "items": [
                        {"opp": "PersonnelSearchService.ts — searchPersonnelBySkill", "effort_days": 10, "model": "embedding_search"},
                        {"opp": "ReadinessNarrativeAPI.py — generate_readiness_report", "effort_days": 15, "model": "llm_generation"},
                    ],
                    "effort_days": 25,
                },
                {
                    "phase": 2, "title": "Eligibility Classifier + Decision Agent",
                    "items": [
                        {"opp": "TrainingEligibilityChecker.ts — checkTrainingEligibility", "effort_days": 38, "model": "ml_classifier"},
                        {"opp": "EligibilityRuleMap.ts — resolveEligibilityRule", "effort_days": 28, "model": "decision_agent"},
                    ],
                    "effort_days": 66,
                },
            ],
            "total_effort_days": 91,
            "aimc_links": ["aimc-dod-006"],
            "aadc_links": ["aadc-dod-007"],
        },
    },
]


# ── Seed Functions ─────────────────────────────────────────────────────────────

def seed_scans(conn) -> dict:
    """Insert aac_scans and return {ref: scan_id} map via lastrowid."""
    sql = (
        "INSERT OR IGNORE INTO aac_scans "
        "(input_type, input_ref, language_profile, total_files, total_loc, status, created_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    scan_ids: dict = {}
    for s in SCANS:
        ts = _now(s["offset_hours"])
        conn.execute(sql, (
            s["input_type"], s["input_ref"],
            json.dumps(s["language_profile"]),
            s["total_files"], s["total_loc"],
            "completed", ts, ts,
        ))
        scan_ids[s["ref"]] = conn.execute(
            "SELECT scan_id FROM aac_scans WHERE input_ref = ?", (s["input_ref"],)
        ).fetchone()[0]
    conn.commit()
    return scan_ids


def seed_opportunities(conn, scan_ids: dict) -> dict:
    """Insert aac_opportunities and return {(ref, fn_name): opp_id} map."""
    sql = (
        "INSERT OR IGNORE INTO aac_opportunities "
        "(scan_id, module_path, function_name, line_start, line_end, language, "
        "pattern_type, pattern_detail, ai_paradigm, il_recommended_model, "
        "data_requirements, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    opp_ids: dict = {}
    for s in SCANS:
        sid = scan_ids[s["ref"]]
        ts = _now(s["offset_hours"])
        for o in s["opportunities"]:
            conn.execute(sql, (
                sid,
                o["module_path"], o["function_name"],
                o["line_start"], o["line_end"],
                o["language"], o["pattern_type"],
                json.dumps(o.get("pattern_detail", {})),
                o["ai_paradigm"], o["il_model"],
                json.dumps(o.get("data_requirements", {})),
                ts,
            ))
            row = conn.execute(
                "SELECT opportunity_id FROM aac_opportunities "
                "WHERE scan_id = ? AND function_name = ? AND module_path = ?",
                (sid, o["function_name"], o["module_path"]),
            ).fetchone()
            if row:
                opp_ids[(s["ref"], o["function_name"])] = row[0]
    conn.commit()
    return opp_ids


def seed_scores(conn, opp_ids: dict) -> int:
    sql = (
        "INSERT OR IGNORE INTO aac_scores "
        "(opportunity_id, value_score, feasibility_score, risk_score, composite_score, score_detail, scored_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for s in SCANS:
        ts = _now(s["offset_hours"])
        for o in s["opportunities"]:
            key = (s["ref"], o["function_name"])
            oid = opp_ids.get(key)
            if oid is None:
                continue
            v, f, r = o["value"], o["feasibility"], o["risk"]
            comp = _composite(v, f, r)
            detail = {
                "value_score": v, "feasibility_score": f, "risk_score": r,
                "composite_score": comp,
                "scoring_weights": SCORING_WEIGHTS["composite"],
            }
            # Check if score already exists
            existing = conn.execute(
                "SELECT score_id FROM aac_scores WHERE opportunity_id = ?", (oid,)
            ).fetchone()
            if not existing:
                conn.execute(sql, (oid, v, f, r, comp, json.dumps(detail), ts))
                count += 1
    conn.commit()
    return count


def seed_roadmaps(conn, scan_ids: dict) -> int:
    sql = (
        "INSERT OR IGNORE INTO aac_roadmaps "
        "(scan_id, roadmap_id, title, phases, total_effort_days, aimc_links, aadc_links, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for s in SCANS:
        sid = scan_ids[s["ref"]]
        rm = s["roadmap"]
        ts = _now(s["offset_hours"])
        conn.execute(sql, (
            sid, rm["roadmap_id"], rm["title"],
            json.dumps(rm["phases"]),
            rm["total_effort_days"],
            json.dumps(rm["aimc_links"]),
            json.dumps(rm["aadc_links"]),
            ts,
        ))
        count += 1
    conn.commit()
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def main(reset: bool = False) -> None:
    init_db()
    conn = get_connection()
    try:
        if reset:
            refs = [s["input_ref"] for s in SCANS]
            for ref in refs:
                row = conn.execute(
                    "SELECT scan_id FROM aac_scans WHERE input_ref = ?", (ref,)
                ).fetchone()
                if row:
                    conn.execute("DELETE FROM aac_scans WHERE scan_id = ?", (row[0],))
            conn.commit()
            print("[reset] Cleared existing AAC demo records.")

        scan_ids = seed_scans(conn)
        opp_ids = seed_opportunities(conn, scan_ids)
        n_scores = seed_scores(conn, opp_ids)
        n_rm = seed_roadmaps(conn, scan_ids)

        total_opps = sum(len(s["opportunities"]) for s in SCANS)
        print(
            f"[AAC] Seeded: {len(SCANS)} scans, {total_opps} opportunities, "
            f"{n_scores} scores, {n_rm} roadmaps"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Seed AAC DoD/IC demo data")
    parser.add_argument("--reset", action="store_true", help="Delete and reseed existing records")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if args.as_json:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(reset=args.reset)
        lines = buf.getvalue().strip().split("\n")
        stats: dict = {}
        for line in lines:
            for kv in line.replace("[AAC] Seeded: ", "").split(", "):
                parts = kv.strip().split(" ")
                if len(parts) == 2:
                    try:
                        stats[parts[1]] = int(parts[0])
                    except ValueError:
                        pass
        print(json.dumps({"status": "ok", "stats": stats}))
    else:
        main(reset=args.reset)
