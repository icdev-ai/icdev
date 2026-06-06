# CUI // SP-CTI
"""Seed DoD/IC synthetic demo data for the Agentic AI Design Canvas (AADC).

Populates: aadc_designs, aadc_assessments, aadc_threat_models,
           aadc_ato_reports, aadc_risk_items, aadc_red_team_reports,
           aadc_lifecycle_states, aadc_scorecard_snapshots, aadc_deploy_gates

Run:
    python tools/db/seeds/seed_ai_canvases_aadc.py
    python tools/db/seeds/seed_ai_canvases_aadc.py --reset
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.agentic_ai_canvas.db.init_db import get_connection, init_db  # noqa: E402


def _now(offset_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


def _n(ntype: str, label: str, x: int, y: int) -> dict:
    return {"id": f"n-{uuid.uuid4().hex[:8]}", "type": ntype, "label": label, "x": x, "y": y}


def _e(src_id: str, tgt_id: str, label: str = "") -> dict:
    return {"id": f"e-{uuid.uuid4().hex[:8]}", "source": src_id, "target": tgt_id, "label": label}


def _graph(*rows) -> str:
    nodes, edges = [], []
    for row in rows:
        nodes.append(row)
    for i in range(len(nodes) - 1):
        edges.append(_e(nodes[i]["id"], nodes[i + 1]["id"]))
    return json.dumps({"nodes": nodes, "edges": edges})


# ── Design Definitions ────────────────────────────────────────────────────────

DESIGNS = [
    {
        "id": "aadc-dod-001",
        "name": "SIGINT Multi-INT Fusion Agent",
        "description": "Multi-source intelligence fusion pipeline with human-supervised NLP at IL5. Processes SIGINT feeds through NLP classification, RAG retrieval, and HITL gating before dissemination.",
        "domain": "sigint",
        "classification": "CUI // SP-CTI",
        "autonomy_max": 2,
        "safety_impacting": 1,
        "rights_impacting": 0,
        "hitl_required": 1,
        "offset_hours": 720,
        "lifecycle": [("DRAFT", "REVIEW", "AI Governance Team", 696)],
        "assessment": {
            "score": 87.0, "nist_rmf_score": 88.0, "owasp_score": 82.0, "omb_compliant": 1,
            "findings": ["NIST-08: Missing PII field detector on SIGINT content ingestion path",
                         "NIST-12: Embedding model (nomic-embed) not certified for IL5 data"],
            "atlas": ["AML.T0043 — Craft adversarial data via forged SIGINT feeds",
                      "AML.T0048 — Extract ML model via repeated inference queries"]
        },
        "ato": {"score_pct": 82.0, "ato_ready": 0, "passed": 12, "failed": 3, "critical_failed": 1},
        "risks": [
            ("No PII field detector on SIGINT ingestion path", "PII on SIGINT stream may enter embedding store", "operational", "HIGH", "MEDIUM"),
            ("Embedding model not IL5-certified", "nomic-embed-text has no IL5 data handling certification", "compliance", "MEDIUM", "LOW"),
            ("Audit retention period unspecified", "AU-11 requires 3-year audit log retention; not documented", "operational", "LOW", "LOW"),
        ],
        "red_team": {"overall_risk": "HIGH", "applicable": 5, "unmitigated": 2, "critical_unmitigated": 1, "avg_exploitability": 0.62},
        "scorecard": {"overall_score": 72.0, "health": "AT_RISK"},
        "gate": {"verdict": "BLOCKED", "blocker_count": 1, "warning_count": 3},
        "nodes": [
            _n("inference-input", "SIGINT Feed Ingest", 50, 150),
            _n("input-sanitizer", "Input Sanitizer", 200, 150),
            _n("pii-detector", "PII Detector", 350, 150),
            _n("classifier", "Intent Router", 500, 150),
            _n("embedding-model", "Embedder (nomic)", 500, 280),
            _n("vector-db", "ChromaDB (IL5 enclave)", 650, 280),
            _n("llm", "Llama3-70B (Ollama)", 650, 150),
            _n("confidence-threshold", "Confidence Gate (0.75)", 800, 150),
            _n("hitl-gate", "HITL Review Gate", 950, 150),
            _n("audit-logger", "Audit Logger", 950, 280),
            _n("compliance-reporter", "NIST AI RMF Reporter", 1100, 280),
            _n("siem-forwarder", "SIEM Forwarder", 1100, 150),
            _n("output-validator", "Output Validator", 1250, 150),
        ],
    },
    {
        "id": "aadc-dod-002",
        "name": "Insider Threat Behavioral Analyst",
        "description": "Rights-impacting behavioral anomaly detection system approved for IL5 deployment. OMB M-25-21 compliant with CAIO override node for accountability.",
        "domain": "cybersecurity",
        "classification": "CUI // SP-CTI",
        "autonomy_max": 1,
        "safety_impacting": 1,
        "rights_impacting": 1,
        "hitl_required": 1,
        "offset_hours": 600,
        "lifecycle": [
            ("DRAFT", "REVIEW", "Security Architect", 576),
            ("REVIEW", "APPROVED", "Authorizing Official", 480),
        ],
        "assessment": {
            "score": 93.0, "nist_rmf_score": 95.0, "owasp_score": 90.0, "omb_compliant": 1,
            "findings": ["MINOR: OMB M-25-21 AI use case inventory submission pending CAIO filing"],
            "atlas": ["AML.T0056 — LLM prompt injection via behavioral data metadata",
                      "AML.T0040 — ML model inference attack on behavioral baseline"]
        },
        "ato": {"score_pct": 91.5, "ato_ready": 1, "passed": 16, "failed": 1, "critical_failed": 0},
        "risks": [
            ("Rights-impacting per OMB M-25-21 §5", "Personnel decisions require CAIO sign-off; CAIO override node present and active", "compliance", "CRITICAL", "LOW"),
            ("Behavioral data requires AC-3 access enforcement", "AC-3 access controls not yet documented in SSP Appendix A", "compliance", "HIGH", "LOW"),
            ("FIPS 140-3 HSM required for key management", "Key management policy references HSM but procurement not yet completed", "operational", "MEDIUM", "MEDIUM"),
        ],
        "red_team": {"overall_risk": "LOW", "applicable": 6, "unmitigated": 0, "critical_unmitigated": 0, "avg_exploitability": 0.22},
        "scorecard": {"overall_score": 91.5, "health": "HEALTHY"},
        "gate": {"verdict": "PASS", "blocker_count": 0, "warning_count": 1},
        "nodes": [
            _n("inference-input", "User Behavior Feed", 50, 150),
            _n("input-sanitizer", "Input Sanitizer", 200, 150),
            _n("pii-detector", "PII Detector", 350, 150),
            _n("redaction-engine", "Redaction Engine", 500, 150),
            _n("anomaly-detection", "Anomaly Detector (ML)", 650, 150),
            _n("long-term-mem", "Behavioral Baseline Store", 650, 280),
            _n("llm", "Qwen3 (Ollama air-gap)", 800, 150),
            _n("confidence-threshold", "Confidence Gate (0.80)", 950, 150),
            _n("hitl-gate", "HITL Gate", 1100, 150),
            _n("caio-override", "CAIO Override Node", 1100, 280),
            _n("audit-logger", "Audit Logger (AU-2)", 1250, 280),
            _n("compliance-reporter", "OMB M-25-21 Reporter", 1250, 150),
            _n("output-validator", "Output Validator", 1400, 150),
        ],
    },
    {
        "id": "aadc-dod-003",
        "name": "JADC2 Mission Planning Assistant",
        "description": "Multi-agent mission planning assistant for JADC2. Generates courses of action over classified doctrine corpus with mandatory HITL at commander approval gate.",
        "domain": "mission_planning",
        "classification": "CUI // SP-CTI",
        "autonomy_max": 2,
        "safety_impacting": 1,
        "rights_impacting": 0,
        "hitl_required": 1,
        "offset_hours": 480,
        "lifecycle": [("DRAFT", "REVIEW", "Requirements Team", 456)],
        "assessment": {
            "score": 83.0, "nist_rmf_score": 85.0, "owasp_score": 78.0, "omb_compliant": 1,
            "findings": ["CRITICAL: No rate-limiter on orchestrator node — unbounded LLM call loop possible",
                         "HIGH: Web-search node not isolated in SIGINT-protected enclave",
                         "NIST-15: Missing circuit breaker on agent spawn path",
                         "OWASP-LLM03: Doctrine RAG corpus not access-controlled per AC-3"],
            "atlas": ["AML.T0054 — LLM plugin compromise via doctrine manipulation",
                      "AML.T0043 — Poisoned COA injection via adversarial doctrine document"]
        },
        "ato": {"score_pct": 79.0, "ato_ready": 0, "passed": 11, "failed": 4, "critical_failed": 1},
        "risks": [
            ("No rate-limiter on orchestrator", "Orchestrator can spawn unlimited LLM calls causing DoS or uncontrolled action loop", "operational", "CRITICAL", "MEDIUM"),
            ("Web-search node not isolated", "Researcher agent web search not proxied through SIGINT-isolated network segment", "security", "HIGH", "MEDIUM"),
            ("Circuit breaker missing on agent spawn", "Multi-agent spawn path has no circuit breaker to halt cascading failures", "operational", "HIGH", "LOW"),
        ],
        "red_team": {"overall_risk": "HIGH", "applicable": 7, "unmitigated": 3, "critical_unmitigated": 1, "avg_exploitability": 0.58},
        "scorecard": {"overall_score": 63.0, "health": "AT_RISK"},
        "gate": {"verdict": "BLOCKED", "blocker_count": 2, "warning_count": 4},
        "nodes": [
            _n("inference-input", "Commander Query", 50, 150),
            _n("input-sanitizer", "Input Sanitizer", 200, 150),
            _n("orchestrator", "Mission Planner Orchestrator", 350, 150),
            _n("researcher-agent", "COA Research Agent", 500, 80),
            _n("writer-agent", "COA Writer Agent", 500, 220),
            _n("vector-db", "Doctrine RAG Store (IL5)", 650, 280),
            _n("llm", "Llama3-70B (Ollama)", 650, 150),
            _n("knowledge-graph", "JADC2 Mission KG", 800, 280),
            _n("circuit-breaker", "Circuit Breaker", 800, 150),
            _n("confidence-threshold", "Confidence Gate (0.80)", 950, 150),
            _n("hitl-gate", "Commander Approval Gate", 1100, 150),
            _n("audit-logger", "Audit Logger", 1100, 280),
            _n("output-validator", "Output Validator", 1250, 150),
        ],
    },
    {
        "id": "aadc-dod-004",
        "name": "FAR/DFARS Acquisition Compliance Analyzer",
        "description": "RAG-based acquisition compliance assistant that checks contract clauses against FAR/DFARS corpus. Non-rights-impacting at IL4 on Bedrock GovCloud.",
        "domain": "acquisition",
        "classification": "CUI",
        "autonomy_max": 2,
        "safety_impacting": 0,
        "rights_impacting": 0,
        "hitl_required": 0,
        "offset_hours": 360,
        "lifecycle": [("DRAFT", "REVIEW", "Acquisition Team", 336)],
        "assessment": {
            "score": 73.0, "nist_rmf_score": 72.0, "owasp_score": 74.0, "omb_compliant": 1,
            "findings": ["HIGH: LLM hallucination risk on contract clause citation numbers — no citation verification step",
                         "MEDIUM: No HITL for binding procurement determinations",
                         "OWASP-LLM01: Prompt injection possible via contract text field"],
            "atlas": ["AML.T0043 — Adversarial contract text injection into prompt"]
        },
        "ato": {"score_pct": 70.0, "ato_ready": 0, "passed": 9, "failed": 5, "critical_failed": 0},
        "risks": [
            ("Hallucination on FAR clause citation numbers", "LLM may cite incorrect clause numbers; contracting officer must verify independently", "operational", "HIGH", "MEDIUM"),
            ("No HITL for binding contract determinations", "Acquisition decisions should not be automated without contracting officer review", "compliance", "MEDIUM", "LOW"),
            ("Prompt injection via contract text field", "Malicious contract text could override system prompt context", "security", "MEDIUM", "MEDIUM"),
        ],
        "red_team": {"overall_risk": "MEDIUM", "applicable": 4, "unmitigated": 1, "critical_unmitigated": 0, "avg_exploitability": 0.44},
        "scorecard": {"overall_score": 58.0, "health": "AT_RISK"},
        "gate": {"verdict": "BLOCKED", "blocker_count": 0, "warning_count": 5},
        "nodes": [
            _n("inference-input", "Contract Query", 50, 150),
            _n("input-sanitizer", "Input Sanitizer", 200, 150),
            _n("embedding-model", "Embedder", 350, 280),
            _n("vector-db", "FAR/DFARS Vector Store", 500, 280),
            _n("llm", "Claude Sonnet 4.6 (Bedrock)", 500, 150),
            _n("reranker", "Reranker", 650, 280),
            _n("compliance-reporter", "Compliance Reporter", 650, 150),
            _n("prompt-registry", "Prompt Registry", 800, 280),
            _n("audit-logger", "Audit Logger", 800, 150),
            _n("output-validator", "Output Validator", 950, 150),
        ],
    },
    {
        "id": "aadc-dod-005",
        "name": "Threat Intel Fusion — Multi-Agent",
        "description": "18-node multi-agent pipeline fusing OSINT, STIX/TAXII, and HUMINT streams into prioritized threat assessments. Fan-out/fan-in pattern with ATLAS-mapped threat mitigations.",
        "domain": "threat_intel",
        "classification": "CUI // SP-CTI",
        "autonomy_max": 3,
        "safety_impacting": 1,
        "rights_impacting": 0,
        "hitl_required": 0,
        "offset_hours": 240,
        "lifecycle": [("DRAFT", "REVIEW", "CISO Office", 216)],
        "assessment": {
            "score": 80.0, "nist_rmf_score": 82.0, "owasp_score": 76.0, "omb_compliant": 1,
            "findings": ["CRITICAL: No chain-of-custody on OSINT source attribution (Repudiation — STRIDE-R)",
                         "CRITICAL: STIX/TAXII connector not rate-limited — model extraction via repeated queries possible",
                         "HIGH: Fan-in aggregator has no deduplication logic for conflicting assessments",
                         "MEDIUM: Long-term memory store not classified-at-rest per IL5 requirements"],
            "atlas": ["AML.T0043 — Poisoned OSINT feed injection",
                      "AML.T0048 — Model extraction via STIX/TAXII repeated queries",
                      "AML.T0056 — Prompt injection via threat description field"]
        },
        "ato": {"score_pct": 77.0, "ato_ready": 0, "passed": 13, "failed": 5, "critical_failed": 2},
        "risks": [
            ("No OSINT source attribution chain-of-custody", "STRIDE-R: OSINT attribution cannot be verified; adversary can inject false attribution", "security", "CRITICAL", "HIGH"),
            ("STIX/TAXII connector not rate-limited", "Repeated queries can extract model behavior patterns (AML.T0048)", "security", "CRITICAL", "MEDIUM"),
            ("Conflicting assessments not deduplicated in fan-in", "Fan-in aggregator may produce inconsistent threat scores from conflicting sub-agents", "operational", "HIGH", "MEDIUM"),
        ],
        "red_team": {"overall_risk": "HIGH", "applicable": 8, "unmitigated": 3, "critical_unmitigated": 2, "avg_exploitability": 0.67},
        "scorecard": {"overall_score": 61.0, "health": "AT_RISK"},
        "gate": {"verdict": "BLOCKED", "blocker_count": 2, "warning_count": 3},
        "nodes": [
            _n("inference-input", "Intel Query", 50, 150),
            _n("orchestrator", "Intel Fusion Orchestrator", 200, 150),
            _n("researcher-agent", "OSINT Collector", 350, 80),
            _n("researcher-agent", "STIX/TAXII Analyst", 350, 220),
            _n("fan-out-coordinator", "Fan-Out Coordinator", 500, 150),
            _n("fan-in-aggregator", "Fan-In Aggregator", 650, 150),
            _n("vector-db", "Threat Intel Vector Store", 650, 280),
            _n("long-term-mem", "Threat Actor Memory", 800, 280),
            _n("knowledge-graph", "MITRE ATT&CK KG", 800, 150),
            _n("llm", "Llama3-70B (Ollama)", 950, 150),
            _n("circuit-breaker", "Circuit Breaker", 950, 280),
            _n("confidence-threshold", "Confidence Gate (0.75)", 1100, 150),
            _n("audit-logger", "Audit Logger", 1100, 280),
            _n("siem-forwarder", "SIEM Forwarder", 1250, 150),
            _n("compliance-reporter", "NIST AI RMF Reporter", 1250, 280),
            _n("output-validator", "Output Validator", 1400, 150),
        ],
    },
    {
        "id": "aadc-dod-006",
        "name": "Autonomous Cyber Hunt — SIEM Integration",
        "description": "Automated cyber threat hunting pipeline that maps alerts to MITRE ATT&CK TTPs and escalates P1/P2 detections to human analysts. IL4 GovCloud deployment.",
        "domain": "cyber_hunting",
        "classification": "CUI // SP-CTI",
        "autonomy_max": 3,
        "safety_impacting": 1,
        "rights_impacting": 0,
        "hitl_required": 0,
        "offset_hours": 168,
        "lifecycle": [("DRAFT", "REVIEW", "SOC Lead", 144)],
        "assessment": {
            "score": 81.0, "nist_rmf_score": 79.0, "owasp_score": 85.0, "omb_compliant": 1,
            "findings": ["HIGH: sandbox-exec node requires STIG hardening documentation (SI-7 integrity check)",
                         "MEDIUM: drift-detector not wired to SIEM for real-time model drift alerting",
                         "LOW: Baseline snapshot schedule not defined in ops runbook"],
            "atlas": ["AML.T0043 — Adversarial SIEM event injection to poison TTP classifier",
                      "AML.T0040 — Model inference attack via repeated SIEM query patterns"]
        },
        "ato": {"score_pct": 78.0, "ato_ready": 0, "passed": 11, "failed": 3, "critical_failed": 0},
        "risks": [
            ("sandbox-exec requires STIG hardening documentation", "SI-7 requires integrity verification of sandbox execution environment; not yet documented", "compliance", "HIGH", "LOW"),
            ("drift-detector not wired to SIEM", "Model drift will not generate SIEM alerts; manual review required", "operational", "MEDIUM", "MEDIUM"),
            ("Baseline snapshot schedule undefined", "No schedule for refreshing behavioral baseline; drift detection will degrade over time", "operational", "LOW", "LOW"),
        ],
        "red_team": {"overall_risk": "MEDIUM", "applicable": 6, "unmitigated": 1, "critical_unmitigated": 0, "avg_exploitability": 0.38},
        "scorecard": {"overall_score": 74.0, "health": "NEEDS_ATTENTION"},
        "gate": {"verdict": "BLOCKED", "blocker_count": 1, "warning_count": 2},
        "nodes": [
            _n("inference-input", "SIEM Alert Stream", 50, 150),
            _n("classifier", "TTP Mapper (MITRE ATT&CK)", 200, 150),
            _n("llm", "Qwen3 (Ollama local)", 350, 150),
            _n("tool-chain", "SIEM API Tool Chain", 500, 150),
            _n("function-caller", "Function Caller", 500, 280),
            _n("sandbox-exec", "Sandbox Executor", 650, 150),
            _n("knowledge-graph", "MITRE ATT&CK KG", 650, 280),
            _n("circuit-breaker", "Circuit Breaker", 800, 150),
            _n("rate-limiter", "Rate Limiter", 800, 280),
            _n("confidence-threshold", "Confidence Gate (0.70)", 950, 150),
            _n("drift-detector", "Drift Detector", 950, 280),
            _n("baseline-snapshot", "Baseline Snapshot", 1100, 280),
            _n("audit-logger", "Audit Logger (AU-2)", 1100, 150),
            _n("siem-forwarder", "SIEM Forwarder", 1250, 150),
            _n("output-validator", "Output Validator", 1400, 150),
        ],
    },
    {
        "id": "aadc-dod-007",
        "name": "DoD AI Inventory Governance Monitor",
        "description": "ATO-ready governance monitor that audits all production AI systems for compliance drift, confabulation events, and OMB M-25-21 inventory completeness. Rights-impacting.",
        "domain": "ai_governance",
        "classification": "CUI",
        "autonomy_max": 1,
        "safety_impacting": 0,
        "rights_impacting": 1,
        "hitl_required": 1,
        "offset_hours": 120,
        "lifecycle": [
            ("DRAFT", "REVIEW", "CAIO Office", 96),
            ("REVIEW", "APPROVED", "Authorizing Official", 48),
        ],
        "assessment": {
            "score": 94.0, "nist_rmf_score": 96.0, "owasp_score": 88.0, "omb_compliant": 1,
            "findings": ["MINOR: OMB M-25-21 AI use case inventory formal submission pending CAIO signature"],
            "atlas": ["AML.T0043 — Adversarial compliance evidence to spoof audit results"]
        },
        "ato": {"score_pct": 93.0, "ato_ready": 1, "passed": 15, "failed": 1, "critical_failed": 0},
        "risks": [
            ("OMB M-25-21 inventory submission pending", "AI use case inventory filed internally but CAIO formal submission to OMB not yet completed", "compliance", "MEDIUM", "LOW"),
            ("Alert fatigue risk in CAIO review queue", "Low-confidence decisions may overflow HITL queue without triage priority rules", "operational", "LOW", "LOW"),
            ("Model card version drift", "Model cards for monitored systems may fall out of sync with production versions", "operational", "LOW", "MEDIUM"),
        ],
        "red_team": {"overall_risk": "LOW", "applicable": 3, "unmitigated": 0, "critical_unmitigated": 0, "avg_exploitability": 0.18},
        "scorecard": {"overall_score": 93.0, "health": "HEALTHY"},
        "gate": {"verdict": "PASS", "blocker_count": 0, "warning_count": 1},
        "nodes": [
            _n("inference-input", "AI System Feed", 50, 150),
            _n("classifier", "Compliance Classifier", 200, 150),
            _n("llm", "Claude Sonnet 4.6 (Bedrock)", 350, 150),
            _n("compliance-reporter", "OMB M-25-21 Reporter", 500, 150),
            _n("approval-workflow", "Approval Workflow", 650, 150),
            _n("hitl-gate", "CAIO Review Gate", 800, 150),
            _n("caio-override", "CAIO Override Node", 800, 280),
            _n("alert-manager", "Alert Manager", 950, 150),
            _n("audit-logger", "Audit Logger (Append-Only)", 950, 280),
            _n("prompt-registry", "Prompt Registry", 1100, 280),
            _n("output-validator", "Output Validator", 1100, 150),
        ],
    },
    {
        "id": "aadc-dod-008",
        "name": "Defense Supply Chain AI Risk Assessor",
        "description": "Automated SCRM pipeline that scans vendor profiles against CMMC Level 2 corpus, scores supply chain risk, and routes HIGH findings to contracting officers.",
        "domain": "supply_chain",
        "classification": "CUI // SP-CTI",
        "autonomy_max": 2,
        "safety_impacting": 0,
        "rights_impacting": 0,
        "hitl_required": 1,
        "offset_hours": 72,
        "lifecycle": [("DRAFT", "REVIEW", "Supply Chain Risk Mgmt", 48)],
        "assessment": {
            "score": 80.0, "nist_rmf_score": 81.0, "owasp_score": 77.0, "omb_compliant": 1,
            "findings": ["HIGH: GPT-4o (Azure GovCloud) not air-gap-ready; acceptable at IL4 only",
                         "MEDIUM: Vendor data sourced from unclassified registries may have classification mismatch",
                         "LOW: Circuit breaker threshold not tuned for SCRM query volume"],
            "atlas": ["AML.T0043 — Adversarial vendor profile to evade risk scoring",
                      "AML.T0054 — Plugin compromise via CMMC corpus manipulation"]
        },
        "ato": {"score_pct": 75.0, "ato_ready": 0, "passed": 10, "failed": 4, "critical_failed": 0},
        "risks": [
            ("GPT-4o not air-gap-ready (acceptable at IL4 only)", "Must not use GPT-4o on Azure GovCloud for IL5+ data; current classification is IL4", "compliance", "HIGH", "LOW"),
            ("Vendor data classification mismatch", "Unclassified SAM.gov vendor data may be combined with CUI CMMC findings without explicit classification control", "security", "MEDIUM", "MEDIUM"),
            ("Circuit breaker threshold not tuned", "Default circuit breaker may not trip appropriately for high-volume SCRM API calls", "operational", "LOW", "LOW"),
        ],
        "red_team": {"overall_risk": "MEDIUM", "applicable": 5, "unmitigated": 1, "critical_unmitigated": 0, "avg_exploitability": 0.35},
        "scorecard": {"overall_score": 67.0, "health": "NEEDS_ATTENTION"},
        "gate": {"verdict": "BLOCKED", "blocker_count": 0, "warning_count": 4},
        "nodes": [
            _n("inference-input", "Vendor Profile Feed", 50, 150),
            _n("input-sanitizer", "Input Sanitizer", 200, 150),
            _n("researcher-agent", "SCRM Research Agent", 350, 150),
            _n("llm", "GPT-4o (Azure GovCloud)", 500, 150),
            _n("vector-db", "CMMC L2 Corpus Store", 500, 280),
            _n("knowledge-graph", "Vendor Risk KG", 650, 280),
            _n("confidence-threshold", "Confidence Gate (0.70)", 650, 150),
            _n("hitl-gate", "Contracting Officer Gate", 800, 150),
            _n("audit-logger", "Audit Logger", 800, 280),
            _n("compliance-reporter", "CMMC Reporter", 950, 280),
            _n("pii-detector", "PII Detector", 950, 150),
            _n("circuit-breaker", "Circuit Breaker", 1100, 150),
            _n("output-validator", "Output Validator", 1250, 150),
        ],
    },
]

# ── Seed Functions ────────────────────────────────────────────────────────────

def seed_designs(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aadc_designs "
        "(id, name, description, domain, classification, graph_json, "
        "autonomy_max, safety_impacting, rights_impacting, hitl_required, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        nodes = d["nodes"]
        edges = []
        for i in range(len(nodes) - 1):
            edges.append(_e(nodes[i]["id"], nodes[i + 1]["id"]))
        graph = json.dumps({"nodes": nodes, "edges": edges})
        ts = _now(d["offset_hours"])
        conn.execute(sql, (
            d["id"], d["name"], d["description"], d["domain"],
            d["classification"], graph,
            d["autonomy_max"], d["safety_impacting"],
            d["rights_impacting"], d["hitl_required"],
            ts, ts,
        ))
        count += 1
    return count


def seed_assessments(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aadc_assessments "
        "(id, design_id, score, nist_rmf_score, owasp_score, omb_compliant, "
        "autonomy_max, safety_impacting, rights_impacting, "
        "findings_json, atlas_threats, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        a = d["assessment"]
        conn.execute(sql, (
            f"assess-{d['id']}", d["id"],
            a["score"], a["nist_rmf_score"], a["owasp_score"], a["omb_compliant"],
            d["autonomy_max"], d["safety_impacting"], d["rights_impacting"],
            json.dumps(a["findings"]), json.dumps(a["atlas"]),
            _now(d["offset_hours"]),
        ))
        count += 1
    return count


def seed_threat_models(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aadc_threat_models "
        "(id, design_id, stride_json, atlas_threats, threat_count, high_count, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    stride_by_domain = {
        "sigint": [{"category": "Spoofing", "threat": "Forged SIGINT feeds injected into ingestion pipeline"},
                   {"category": "Tampering", "threat": "Adversarial signal injection corrupts embedding store"},
                   {"category": "Information Disclosure", "threat": "Classification marking stripped from disseminated output"}],
        "cybersecurity": [{"category": "Elevation of Privilege", "threat": "Behavioral model accessed by unauthorized analyst tier"},
                          {"category": "Repudiation", "threat": "AI decision audit log tampered post-incident"}],
        "mission_planning": [{"category": "Spoofing", "threat": "Fake COA injection via poisoned doctrine document"},
                             {"category": "Elevation of Privilege", "threat": "Agent escalates mission authority beyond authorization"}],
        "acquisition": [{"category": "Tampering", "threat": "Contract text field used to override system prompt"},
                        {"category": "Repudiation", "threat": "No audit trail for binding contract AI determinations"}],
        "threat_intel": [{"category": "Repudiation", "threat": "OSINT attribution chain-of-custody not verifiable"},
                         {"category": "Spoofing", "threat": "Adversary injects false threat indicators into STIX feed"},
                         {"category": "Denial of Service", "threat": "Fan-out spawns unbounded sub-agent calls"}],
        "cyber_hunting": [{"category": "Tampering", "threat": "Adversarial SIEM events poisoning TTP classifier training data"},
                          {"category": "Elevation of Privilege", "threat": "Sandbox-exec node exploited for privilege escalation"}],
        "ai_governance": [{"category": "Tampering", "threat": "Adversarial compliance evidence spoofs audit results"},
                          {"category": "Repudiation", "threat": "AI governance decisions not signed by CAIO override node"}],
        "supply_chain": [{"category": "Spoofing", "threat": "Adversarial vendor profile evades risk scoring model"},
                         {"category": "Tampering", "threat": "CMMC corpus poisoning via manipulated plugin"},
                         {"category": "Information Disclosure", "threat": "CUI vendor data leaked through unclassified API path"}],
    }
    for d in DESIGNS:
        stride = stride_by_domain.get(d["domain"], [])
        atlas = d["assessment"]["atlas"]
        threat_count = len(stride) + len(atlas)
        high_count = max(1, len([f for f in d["assessment"]["findings"] if "CRITICAL" in f or "HIGH" in f]))
        conn.execute(sql, (
            f"threat-{d['id']}", d["id"],
            json.dumps(stride), json.dumps(atlas),
            threat_count, high_count,
            _now(d["offset_hours"]),
        ))
        count += 1
    return count


def seed_ato_reports(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aadc_ato_reports "
        "(id, design_id, score_pct, ato_ready, passed, failed, critical_failed, report_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        a = d["ato"]
        report = {"findings": d["assessment"]["findings"], "atlas_mitigated": len(d["assessment"]["atlas"]) - a["critical_failed"]}
        conn.execute(sql, (
            f"ato-{d['id']}", d["id"],
            a["score_pct"], a["ato_ready"], a["passed"], a["failed"], a["critical_failed"],
            json.dumps(report),
            _now(d["offset_hours"]),
        ))
        count += 1
    return count


def seed_risk_items(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aadc_risk_items "
        "(id, design_id, title, description, risk_category, severity, likelihood, impact, "
        "status, owner, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        for i, (title, desc, category, severity, likelihood) in enumerate(d["risks"]):
            conn.execute(sql, (
                f"risk-{d['id']}-{i}", d["id"],
                title, desc, category, severity, likelihood,
                "HIGH" if severity in ("HIGH", "CRITICAL") else "MEDIUM",
                "open",
                "AI Governance Team",
                _now(d["offset_hours"]),
                _now(d["offset_hours"]),
            ))
            count += 1
    return count


def seed_red_team_reports(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aadc_red_team_reports "
        "(id, design_id, overall_risk, applicable, unmitigated, critical_unmitigated, "
        "avg_exploitability, report_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        r = d["red_team"]
        report = {
            "techniques_tested": d["assessment"]["atlas"],
            "mitigations_implemented": r["applicable"] - r["unmitigated"],
            "open_items": d["risks"][:r["unmitigated"]] if r["unmitigated"] > 0 else [],
        }
        conn.execute(sql, (
            f"redteam-{d['id']}", d["id"],
            r["overall_risk"], r["applicable"], r["unmitigated"], r["critical_unmitigated"],
            r["avg_exploitability"],
            json.dumps(report),
            _now(d["offset_hours"]),
        ))
        count += 1
    return count


def seed_lifecycle_states(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aadc_lifecycle_states "
        "(id, design_id, from_state, to_state, actor, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        for j, (from_s, to_s, actor, offset) in enumerate(d["lifecycle"]):
            conn.execute(sql, (
                f"lc-{d['id']}-{j}", d["id"],
                from_s, to_s, actor,
                f"Transition approved after completing {from_s.lower()} phase review",
                _now(offset),
            ))
            count += 1
    return count


def seed_scorecards(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aadc_scorecard_snapshots "
        "(id, design_id, overall_score, health, snapshot_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        s = d["scorecard"]
        snapshot = {
            "nist_rmf_score": d["assessment"]["nist_rmf_score"],
            "owasp_score": d["assessment"]["owasp_score"],
            "ato_score": d["ato"]["score_pct"],
            "red_team_risk": d["red_team"]["overall_risk"],
            "open_risks": len(d["risks"]),
        }
        conn.execute(sql, (
            f"sc-{d['id']}", d["id"],
            s["overall_score"], s["health"],
            json.dumps(snapshot),
            _now(d["offset_hours"]),
        ))
        count += 1
    return count


def seed_deploy_gates(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aadc_deploy_gates "
        "(id, design_id, verdict, blocker_count, warning_count, gate_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        g = d["gate"]
        gate_detail = {
            "blockers": [f[0] for f in d["risks"] if f[3] == "CRITICAL"][:g["blocker_count"]],
            "warnings": [f[0] for f in d["risks"] if f[3] in ("HIGH", "MEDIUM")][:g["warning_count"]],
            "ato_score": d["ato"]["score_pct"],
            "ato_ready": d["ato"]["ato_ready"],
        }
        conn.execute(sql, (
            f"gate-{d['id']}", d["id"],
            g["verdict"], g["blocker_count"], g["warning_count"],
            json.dumps(gate_detail),
            _now(d["offset_hours"]),
        ))
        count += 1
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def main(reset: bool = False) -> None:
    init_db()
    conn = get_connection()
    try:
        if reset:
            for table in [
                "aadc_deploy_gates", "aadc_scorecard_snapshots", "aadc_lifecycle_states",
                "aadc_red_team_reports", "aadc_risk_items", "aadc_ato_reports",
                "aadc_threat_models", "aadc_assessments",
            ]:
                conn.execute(f"DELETE FROM {table} WHERE id LIKE 'aadc-dod-%' OR design_id LIKE 'aadc-dod-%'")
            conn.execute("DELETE FROM aadc_designs WHERE id LIKE 'aadc-dod-%'")

        n_d = seed_designs(conn)
        n_a = seed_assessments(conn)
        n_t = seed_threat_models(conn)
        n_ato = seed_ato_reports(conn)
        n_r = seed_risk_items(conn)
        n_rt = seed_red_team_reports(conn)
        n_lc = seed_lifecycle_states(conn)
        n_sc = seed_scorecards(conn)
        n_gd = seed_deploy_gates(conn)
        conn.commit()
        print("Seeded AADC demo data:")
        print(f"  aadc_designs:            {n_d}")
        print(f"  aadc_assessments:        {n_a}")
        print(f"  aadc_threat_models:      {n_t}")
        print(f"  aadc_ato_reports:        {n_ato}")
        print(f"  aadc_risk_items:         {n_r}")
        print(f"  aadc_red_team_reports:   {n_rt}")
        print(f"  aadc_lifecycle_states:   {n_lc}")
        print(f"  aadc_scorecard_snapshots:{n_sc}")
        print(f"  aadc_deploy_gates:       {n_gd}")
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    main(reset=args.reset)
