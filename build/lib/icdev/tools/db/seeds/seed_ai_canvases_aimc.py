# CUI // SP-CTI
"""Seed DoD/IC synthetic demo data for the AI/ML Model Canvas (AIMC).

Populates: aiml_designs, aiml_nodes, aiml_edges,
           aiml_assessments, aiml_artifacts

Run:
    python tools/db/seeds/seed_ai_canvases_aimc.py
    python tools/db/seeds/seed_ai_canvases_aimc.py --reset
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.aiml_canvas.db.init_db import get_connection, init_db  # noqa: E402


def _now(offset_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()


def _id() -> str:
    return str(uuid.uuid4())


def _n(nid: str, ntype: str, label: str, x: int, y: int, props: dict | None = None) -> dict:
    return {"id": nid, "type": ntype, "label": label, "x": x, "y": y, "properties": props or {}}


def _e(eid: str, src: str, tgt: str, etype: str = "data-flow", label: str = "") -> dict:
    return {"id": eid, "source": src, "target": tgt, "type": etype, "label": label}


# ── 8 AIMC Designs ────────────────────────────────────────────────────────────

DESIGNS = [
    # ── 1. DoD Document Classification ────────────────────────────────────────
    {
        "id": "aimc-dod-001",
        "name": "DoD Document Classification",
        "description": (
            "Fine-tuned IBM Granite 13B model for automated classification of DoD acquisition "
            "documents (DD-250, SF-1449, RFP, SOW) at IL4. Reduces manual review by 78%."
        ),
        "il_level": "IL4",
        "adaptation_strategy": "finetune",
        "primary_use_case": "document_classification",
        "classification": "CUI // SP-CTI",
        "rights_impacting": 0,
        "offset_hours": 480,
        "nodes": [
            _n("n1a", "doc-ingestion", "DD-250 / SF-1449 Ingest", 50, 100, {"source": "SharePoint GovCloud", "format": "PDF, DOCX"}),
            _n("n1b", "preprocessing", "OCR + Normalizer", 250, 100, {"tool": "Tesseract 5.x", "pii_scrub": True}),
            _n("n1c", "base-model", "IBM Granite 13B", 450, 100, {"provider": "watsonx.ai GovCloud", "il": "IL4"}),
            _n("n1d", "fine-tune-adapter", "LoRA Fine-Tune Adapter", 450, 220, {"dataset": "25K labeled DoD docs", "epochs": 3}),
            _n("n1e", "inference-endpoint", "Classification API", 650, 100, {"latency_p99_ms": 320, "throughput_rps": 45}),
            _n("n1f", "eval-rubric", "F1 / Precision Eval", 650, 220, {"f1_score": 0.923, "accuracy": 0.941}),
            _n("n1g", "output-handler", "Routing + Metadata Tagger", 850, 100, {"output": "S3 GovCloud bucket"}),
            _n("n1h", "audit-logger", "Audit Log — AU-2", 850, 220, {"immutable": True, "retention_days": 365}),
        ],
        "assessments": [
            {
                "fid": "nist-ai-rmf", "fname": "NIST AI RMF 1.0",
                "score": 91.0, "passed": 1,
                "findings": [
                    {"control": "GOVERN-1.1", "status": "PASS", "note": "AI use policy documented and approved"},
                    {"control": "MAP-1.5", "status": "PASS", "note": "Classification task mapped to intended context"},
                    {"control": "MEASURE-2.5", "status": "PASS", "note": "F1=0.923 meets ≥0.90 threshold"},
                    {"control": "MANAGE-1.3", "status": "PASS", "note": "Retraining cadence defined (quarterly)"},
                    {"control": "GOVERN-6.2", "status": "WARN", "note": "Supply chain provenance for training data — partial"},
                ],
            },
            {
                "fid": "dod-rai", "fname": "DoD Responsible AI Principles",
                "score": 88.0, "passed": 1,
                "findings": [
                    {"principle": "Responsible", "status": "PASS", "note": "Human review on low-confidence predictions (<0.75)"},
                    {"principle": "Equitable", "status": "PASS", "note": "No PII or demographic features in input"},
                    {"principle": "Traceable", "status": "PASS", "note": "AU-2 audit log captures all classification decisions"},
                    {"principle": "Reliable", "status": "PASS", "note": "Accuracy 0.941 validated on held-out test set"},
                    {"principle": "Governable", "status": "WARN", "note": "Model card v1.0 — missing adversarial robustness section"},
                ],
            },
        ],
        "model_card": {
            "base_model": "IBM Granite 13B Chat v2",
            "provider": "watsonx.ai GovCloud (Dallas)",
            "il_suitability": "IL4",
            "adaptation": "LoRA fine-tune, rank=16, alpha=32",
            "training_data": "25,000 labeled DoD acquisition documents (FY22–FY24)",
            "metrics": {"accuracy": 0.941, "f1_macro": 0.923, "latency_p99_ms": 320},
            "refresh_cadence": "Quarterly or on major FAR/DFARS change",
            "rights_impacting": False,
            "approved_environments": ["watsonx.ai GovCloud", "SageMaker GovCloud (fallback)"],
        },
    },

    # ── 2. SIGINT NLP — Signal-to-Text ────────────────────────────────────────
    {
        "id": "aimc-dod-002",
        "name": "SIGINT NLP — Signal-to-Text",
        "description": (
            "IL5 air-gapped hybrid fine-tune + RAG pipeline for SIGINT signal-to-text transcription "
            "and entity extraction. Runs fully offline via Ollama on accredited hardware."
        ),
        "il_level": "IL5",
        "adaptation_strategy": "hybrid",
        "primary_use_case": "nlp_extraction",
        "classification": "CUI // SP-CTI",
        "rights_impacting": 0,
        "offset_hours": 600,
        "nodes": [
            _n("n2a", "doc-ingestion", "SIGINT Signal Feed", 50, 120, {"source": "Classified SIGINT Collector", "format": "audio/RF telemetry"}),
            _n("n2b", "preprocessing", "Audio Normalizer + Transcriber", 250, 120, {"tool": "Whisper-local", "language": "multi"}),
            _n("n2c", "bnd-air-gap", "Air-Gap Boundary", 420, 60, {"il_enforcement": "IL5", "no_internet": True, "nsa_type1_enc": True}),
            _n("n2d", "base-model", "Llama3-70B (Ollama Local)", 450, 150, {"provider": "Ollama on-prem", "quantization": "Q4_K_M", "vram_gb": 48}),
            _n("n2e", "fine-tune-adapter", "LoRA SIGINT Domain Adapter", 450, 270, {"dataset": "12K classified SIGINT transcripts", "clearance": "TS//SI"}),
            _n("n2f", "vector-store", "FAISS Air-Gap Index", 650, 150, {"index_size_gb": 2.4, "corpus": "SIGINT entity gazetteer"}),
            _n("n2g", "rag-retriever", "Semantic Retriever", 650, 270, {"top_k": 5, "mmr_lambda": 0.7}),
            _n("n2h", "inference-endpoint", "Entity Extraction API (Local)", 850, 150, {"latency_p99_ms": 890, "offline_only": True}),
            _n("n2i", "confidence-threshold", "Confidence Gate ≥0.72", 850, 270, {"threshold": 0.72, "hitl_below": True}),
            _n("n2j", "audit-logger", "Immutable Audit Log — AU-12", 1050, 150, {"classification": "TS//SI", "retention_years": 25}),
        ],
        "assessments": [
            {
                "fid": "nist-ai-rmf", "fname": "NIST AI RMF 1.0",
                "score": 90.0, "passed": 1,
                "findings": [
                    {"control": "GOVERN-1.7", "status": "PASS", "note": "AI risk policy covers classified SIGINT processing"},
                    {"control": "MAP-3.5", "status": "PASS", "note": "Air-gap boundary enforced — no external data path"},
                    {"control": "MEASURE-2.6", "status": "PASS", "note": "Adversarial red team completed; 0 critical findings"},
                    {"control": "MANAGE-2.2", "status": "WARN", "note": "Model drift detection cadence not yet automated"},
                    {"control": "GOVERN-4.2", "status": "PASS", "note": "ODNI AI principles documented for SIGINT mission use"},
                ],
            },
            {
                "fid": "aimc-il-compliance", "fname": "AIMC IL Compliance Rules",
                "score": 100.0, "passed": 1,
                "findings": [
                    {"rule": "AIMC-IL-001", "status": "PASS", "note": "IL5 design uses air-gap boundary — bnd-air-gap node present"},
                    {"rule": "AIMC-IL-002", "status": "PASS", "note": "No cloud API calls permitted; Ollama local confirmed"},
                    {"rule": "AIMC-IL-003", "status": "PASS", "note": "NSA Type 1 encryption enforced at boundary node"},
                    {"rule": "AIMC-IL-004", "status": "PASS", "note": "Audit log classification matches IL5 classification"},
                ],
            },
        ],
        "model_card": {
            "base_model": "Meta Llama 3 70B Instruct",
            "provider": "Ollama 0.3.x (air-gapped on-prem GPU cluster)",
            "il_suitability": "IL5 / IL6",
            "adaptation": "LoRA fine-tune + FAISS RAG, rank=32, Q4_K_M quantization",
            "training_data": "12,000 classified SIGINT transcripts — clearance TS//SI",
            "metrics": {"entity_f1": 0.879, "transcript_wer": 0.082, "latency_p99_ms": 890},
            "refresh_cadence": "Bi-annually or on SIGINT vocabulary update",
            "rights_impacting": False,
            "approved_environments": ["Ollama on-prem (IL5 accredited enclave)"],
        },
    },

    # ── 3. Equipment Predictive Maintenance ───────────────────────────────────
    {
        "id": "aimc-dod-003",
        "name": "Equipment Predictive Maintenance",
        "description": (
            "Tabular fine-tune XGBoost + LLM explanation layer for GCSS-Army vehicle and equipment "
            "failure prediction. Reduces unplanned maintenance by 34% in FY24 pilot."
        ),
        "il_level": "IL4",
        "adaptation_strategy": "finetune",
        "primary_use_case": "anomaly_detection",
        "classification": "CUI // SP-CTI",
        "rights_impacting": 0,
        "offset_hours": 420,
        "nodes": [
            _n("n3a", "doc-ingestion", "GCSS-Army Sensor Telemetry", 50, 120, {"source": "GCSS-Army SFTP", "frequency": "hourly", "records_per_day": 18000}),
            _n("n3b", "preprocessing", "Feature Engineer + Normalizer", 250, 120, {"features": 47, "missing_imputation": "median"}),
            _n("n3c", "base-model", "XGBoost Classifier v1.7", 450, 100, {"provider": "SageMaker GovCloud", "n_estimators": 800, "max_depth": 7}),
            _n("n3d", "fine-tune-adapter", "Domain Calibration Layer", 450, 220, {"calibration": "Platt scaling", "train_rows": 142000}),
            _n("n3e", "inference-endpoint", "Failure Prediction API", 650, 100, {"latency_p99_ms": 85, "recall_class1": 0.91}),
            _n("n3f", "llm-explainer", "GPT-4o Explanation Node", 650, 220, {"provider": "Azure OpenAI GovCloud", "role": "maintenance_advisor"}),
            _n("n3g", "eval-rubric", "Precision-Recall @ 0.85", 850, 100, {"precision": 0.88, "recall": 0.91, "f1": 0.895}),
            _n("n3h", "audit-logger", "Audit Trail — AU-2 Maintenance", 850, 220, {}),
        ],
        "assessments": [
            {
                "fid": "nist-ai-rmf", "fname": "NIST AI RMF 1.0",
                "score": 87.0, "passed": 1,
                "findings": [
                    {"control": "GOVERN-5.1", "status": "PASS", "note": "PM sponsor documented in AI Inventory"},
                    {"control": "MAP-1.1", "status": "PASS", "note": "Failure prediction mapped to CBM+ maintenance doctrine"},
                    {"control": "MEASURE-1.1", "status": "PASS", "note": "Recall 0.91 — meets Army CBM+ ≥0.88 threshold"},
                    {"control": "MANAGE-3.1", "status": "WARN", "note": "Model card lacks environmental impact section"},
                    {"control": "MEASURE-2.3", "status": "PASS", "note": "Cross-validation 10-fold on 142K training rows"},
                ],
            },
            {
                "fid": "dod-rai", "fname": "DoD Responsible AI Principles",
                "score": 84.0, "passed": 1,
                "findings": [
                    {"principle": "Reliable", "status": "PASS", "note": "F1=0.895 exceeds 0.85 operational threshold"},
                    {"principle": "Traceable", "status": "PASS", "note": "SHAP feature importance logged per prediction"},
                    {"principle": "Governable", "status": "PASS", "note": "Maintainer retrain workflow documented"},
                    {"principle": "Equitable", "status": "PASS", "note": "No demographic or PII features in model inputs"},
                    {"principle": "Responsible", "status": "WARN", "note": "Human-in-the-loop for high-cost component predictions only"},
                ],
            },
        ],
        "model_card": {
            "base_model": "XGBoost 1.7 + Azure OpenAI GPT-4o (explanation layer)",
            "provider": "SageMaker GovCloud us-gov-east-1",
            "il_suitability": "IL4",
            "adaptation": "Domain calibration on 142K GCSS-Army telemetry rows",
            "training_data": "FY22–FY24 GCSS-Army vehicle sensor data (CUI)",
            "metrics": {"precision": 0.88, "recall": 0.91, "f1": 0.895, "latency_p99_ms": 85},
            "refresh_cadence": "Quarterly retraining with new sensor data",
            "rights_impacting": False,
            "approved_environments": ["SageMaker GovCloud", "Azure OpenAI GovCloud"],
        },
    },

    # ── 4. Threat Actor Scoring — STIX 2.1 ───────────────────────────────────
    {
        "id": "aimc-dod-004",
        "name": "Threat Actor Scoring — STIX 2.1",
        "description": (
            "RAG pipeline over STIX 2.1 threat intelligence bundles to score threat actors, "
            "TTPs, and campaign patterns against DoD SIEM rules. Powered by Llama3-70B on AWS Bedrock."
        ),
        "il_level": "IL4",
        "adaptation_strategy": "rag",
        "primary_use_case": "threat_intelligence",
        "classification": "CUI // SP-CTI",
        "rights_impacting": 0,
        "offset_hours": 360,
        "nodes": [
            _n("n4a", "doc-ingestion", "STIX 2.1 Bundle Ingest", 50, 120, {"source": "CISA AIS + ISAC feeds", "format": "JSON-LD"}),
            _n("n4b", "preprocessing", "STIX Entity Extractor", 250, 120, {"entities": ["threat-actor", "campaign", "attack-pattern", "indicator"]}),
            _n("n4c", "vector-store", "OpenSearch GovCloud Index", 450, 100, {"index": "stix-threat-intel", "dimensions": 1536, "shards": 3}),
            _n("n4d", "rag-retriever", "Top-K Semantic Retriever", 450, 220, {"top_k": 8, "reranker": "cohere-rerank-v3"}),
            _n("n4e", "base-model", "Llama3-70B (Bedrock GovCloud)", 650, 100, {"provider": "AWS Bedrock us-gov-west-1", "context_window": 8192}),
            _n("n4f", "confidence-threshold", "Score Confidence Gate ≥0.70", 650, 220, {"threshold": 0.70, "low_conf_action": "escalate"}),
            _n("n4g", "output-handler", "STIX Scoring Report Generator", 850, 100, {"format": "STIX 2.1 Relationship + Score bundle"}),
            _n("n4h", "audit-logger", "Decision Audit — NIST AU-3", 850, 220, {"trace_id": True, "linked_to": "canvas_ai_decisions"}),
        ],
        "assessments": [
            {
                "fid": "nist-ai-rmf", "fname": "NIST AI RMF 1.0",
                "score": 85.0, "passed": 1,
                "findings": [
                    {"control": "GOVERN-1.2", "status": "PASS", "note": "Threat scoring use policy reviewed by ISSM"},
                    {"control": "MAP-3.3", "status": "PASS", "note": "RAG retrieval grounded in authoritative STIX bundles"},
                    {"control": "MEASURE-2.1", "status": "PASS", "note": "Scoring accuracy 83% vs analyst baseline"},
                    {"control": "MANAGE-1.1", "status": "WARN", "note": "Stale STIX feeds — refresh SLA not formalized"},
                    {"control": "GOVERN-2.2", "status": "PASS", "note": "Output marked as AI-assisted, not authoritative"},
                ],
            },
            {
                "fid": "owasp-llm", "fname": "OWASP LLM Top 10",
                "score": 82.0, "passed": 1,
                "findings": [
                    {"item": "LLM01", "status": "PASS", "note": "Prompt injection: STIX inputs sanitized before injection"},
                    {"item": "LLM06", "status": "PASS", "note": "Excessive agency: output is advisory, no auto-action"},
                    {"item": "LLM07", "status": "WARN", "note": "Data freshness: STIX feeds up to 72h delayed"},
                    {"item": "LLM08", "status": "PASS", "note": "Excessive permissions: read-only Bedrock IAM role"},
                ],
            },
        ],
        "model_card": {
            "base_model": "Meta Llama 3 70B Instruct",
            "provider": "AWS Bedrock GovCloud (us-gov-west-1)",
            "il_suitability": "IL4",
            "adaptation": "Zero-shot RAG over STIX 2.1 bundles; no fine-tuning",
            "training_data": "N/A (RAG only) — corpus: CISA AIS + FS-ISAC + DoD JWICS STIX feeds",
            "metrics": {"scoring_accuracy_vs_analyst": 0.83, "latency_p99_ms": 1850},
            "refresh_cadence": "STIX index refreshed every 4 hours from live feeds",
            "rights_impacting": False,
            "approved_environments": ["AWS Bedrock GovCloud", "OpenSearch GovCloud"],
        },
    },

    # ── 5. FAR/DFARS Compliance Q&A ───────────────────────────────────────────
    {
        "id": "aimc-dod-005",
        "name": "FAR/DFARS Compliance Q&A",
        "description": (
            "Claude Sonnet 4.6-powered RAG assistant for contracting officers answering FAR/DFARS "
            "clause questions. 89.4% clause accuracy vs GS-12 legal review. 29x ROI vs manual review."
        ),
        "il_level": "IL4",
        "adaptation_strategy": "rag",
        "primary_use_case": "compliance_qa",
        "classification": "CUI // SP-CTI",
        "rights_impacting": 0,
        "offset_hours": 300,
        "nodes": [
            _n("n5a", "doc-ingestion", "FAR/DFARS Corpus Ingest", 50, 120, {"source": "acquisition.gov + DFARS.mil", "pages": 4200, "update_freq": "weekly"}),
            _n("n5b", "preprocessing", "Clause Chunker + Metadata Tagger", 250, 120, {"chunk_size": 512, "overlap": 64, "metadata": ["clause_id", "part", "subpart"]}),
            _n("n5c", "vector-store", "OpenSearch GovCloud — FAR Index", 450, 100, {"index": "far-dfars-clauses", "dimensions": 1536, "docs": 14200}),
            _n("n5d", "rag-retriever", "Clause Semantic Retriever", 450, 220, {"top_k": 6, "filter_by": "clause_id", "prompt_cache": True}),
            _n("n5e", "base-model", "Claude Sonnet 4.6 (Bedrock)", 650, 100, {"provider": "AWS Bedrock", "prompt_caching": True, "cache_hit_rate": 0.62}),
            _n("n5f", "confidence-threshold", "Confidence Gate ≥0.80", 650, 220, {"threshold": 0.80, "hitl_below": True}),
            _n("n5g", "output-handler", "Citation-Grounded Response", 850, 100, {"format": "answer + clause citations", "disclaimer": True}),
            _n("n5h", "audit-logger", "Compliance Q&A Audit Log", 850, 220, {"linked_to": "canvas_ai_decisions"}),
        ],
        "assessments": [
            {
                "fid": "nist-ai-rmf", "fname": "NIST AI RMF 1.0",
                "score": 92.0, "passed": 1,
                "findings": [
                    {"control": "GOVERN-1.1", "status": "PASS", "note": "COCO reviewed use for acquisition advisory only"},
                    {"control": "MAP-2.2", "status": "PASS", "note": "Use case: advisory only — authoritative decision remains with KO"},
                    {"control": "MEASURE-2.5", "status": "PASS", "note": "Clause accuracy 89.4% > 85% threshold; citation recall 94%"},
                    {"control": "MANAGE-4.1", "status": "PASS", "note": "Prompt caching reduces token cost 62% — documented"},
                    {"control": "GOVERN-6.1", "status": "PASS", "note": "AI-generated responses include disclaimer per OMB M-25-21"},
                ],
            },
            {
                "fid": "dod-rai", "fname": "DoD Responsible AI Principles",
                "score": 90.0, "passed": 1,
                "findings": [
                    {"principle": "Responsible", "status": "PASS", "note": "KO retains final authority; AI is advisory"},
                    {"principle": "Equitable", "status": "PASS", "note": "No demographic data in inputs; clause accuracy uniform across FAR parts"},
                    {"principle": "Traceable", "status": "PASS", "note": "Every response logged with retrieved clauses and confidence"},
                    {"principle": "Reliable", "status": "PASS", "note": "89.4% clause accuracy replicated on 3 independent test sets"},
                    {"principle": "Governable", "status": "PASS", "note": "Retrieval corpus versioned; rollback to prior FAR version supported"},
                ],
            },
        ],
        "model_card": {
            "base_model": "Claude Sonnet 4.6",
            "provider": "AWS Bedrock (us-east-1)",
            "il_suitability": "IL4",
            "adaptation": "Zero-shot RAG with prompt caching; no fine-tuning",
            "training_data": "N/A — corpus: FAR + DFARS + DFARSPGI (14,200 clause chunks, updated weekly)",
            "metrics": {
                "clause_accuracy": 0.894, "citation_recall": 0.940,
                "latency_p99_ms": 2100, "prompt_cache_hit_rate": 0.62,
                "cost_per_query_usd": 0.0031, "labor_equiv_gs12_annual_usd": 255000,
                "roi_x": 29,
            },
            "refresh_cadence": "Weekly corpus refresh from acquisition.gov",
            "rights_impacting": False,
            "approved_environments": ["AWS Bedrock (us-east-1 / us-gov-west-1)"],
        },
    },

    # ── 6. Personnel Readiness Forecast ───────────────────────────────────────
    {
        "id": "aimc-dod-006",
        "name": "Personnel Readiness Forecast",
        "description": (
            "XGBoost + LLM judge pipeline forecasting unit personnel readiness scores 30/60/90 "
            "days out. RIGHTS-IMPACTING per OMB M-25-21 — CAIO review required. DoD RAI score 72."
        ),
        "il_level": "IL4",
        "adaptation_strategy": "finetune",
        "primary_use_case": "personnel_readiness",
        "classification": "CUI // SP-CTI",
        "rights_impacting": 1,
        "offset_hours": 240,
        "nodes": [
            _n("n6a", "doc-ingestion", "DCPDS Personnel Feed", 50, 120, {"source": "DCPDS + MedPros", "pii_fields": ["SSN_hash", "MOS", "deployment_history"]}),
            _n("n6b", "preprocessing", "PII Scrubber + Tokenizer", 250, 120, {"scrub": ["SSN", "DOB", "name"], "pseudonymization": True}),
            _n("n6c", "base-model", "XGBoost Readiness Model", 450, 100, {"provider": "SageMaker GovCloud", "n_estimators": 600, "train_rows": 380000}),
            _n("n6d", "fine-tune-adapter", "Unit-Level Calibration", 450, 220, {"calibration": "isotonic regression", "units": 47}),
            _n("n6e", "llm-judge", "LLM Readiness Narrative", 650, 100, {"provider": "Claude Sonnet 4.6 (Bedrock)", "prompt": "explain_readiness_gap"}),
            _n("n6f", "caio-override", "CAIO Review Gate (Rights-Impacting)", 650, 220, {"required": True, "omb_m2521_sec4": True, "review_threshold": "any_personnel_decision"}),
            _n("n6g", "confidence-threshold", "Confidence Gate ≥0.75", 850, 100, {"threshold": 0.75, "hitl_below": True}),
            _n("n6h", "output-handler", "Readiness Dashboard Feed", 1050, 100, {"output": "S3 + BI dashboard", "aggregated_only": True}),
            _n("n6i", "audit-logger", "CAIO + Decision Audit Log", 1050, 220, {"caio_sign_off": True, "retention_years": 7}),
        ],
        "assessments": [
            {
                "fid": "nist-ai-rmf", "fname": "NIST AI RMF 1.0",
                "score": 78.0, "passed": 1,
                "findings": [
                    {"control": "GOVERN-1.3", "status": "PASS", "note": "Rights-impacting flag set; CAIO review node present"},
                    {"control": "MAP-5.1", "status": "PASS", "note": "Personnel impact assessment completed by HR G-1"},
                    {"control": "MEASURE-2.8", "status": "WARN", "note": "Equity audit incomplete — demographic parity not yet measured"},
                    {"control": "MANAGE-2.4", "status": "WARN", "note": "Bias monitoring cadence undefined — recommended monthly"},
                    {"control": "GOVERN-4.1", "status": "PASS", "note": "CAIO override node documented and tested"},
                ],
            },
            {
                "fid": "dod-rai", "fname": "DoD Responsible AI Principles",
                "score": 72.0, "passed": 0,
                "findings": [
                    {"principle": "Equitable", "status": "FAIL", "note": "Demographic parity analysis not completed — CAIO HOLD"},
                    {"principle": "Responsible", "status": "PASS", "note": "CAIO review gate required before any personnel action"},
                    {"principle": "Traceable", "status": "PASS", "note": "All predictions logged with feature importance (SHAP)"},
                    {"principle": "Reliable", "status": "PASS", "note": "RMSE 0.082 on held-out FY24 data"},
                    {"principle": "Governable", "status": "WARN", "note": "Override audit trail exists; escalation path needs SLA"},
                ],
            },
        ],
        "model_card": {
            "base_model": "XGBoost 1.7 + Claude Sonnet 4.6 (judge layer)",
            "provider": "SageMaker GovCloud + AWS Bedrock",
            "il_suitability": "IL4",
            "adaptation": "Unit-level calibration on 380K DCPDS personnel records",
            "training_data": "FY22–FY24 DCPDS + MedPros pseudonymized records (CUI//PRVCY)",
            "metrics": {"rmse": 0.082, "mae": 0.061, "latency_p99_ms": 450},
            "refresh_cadence": "Monthly retraining; CAIO re-review before go-live",
            "rights_impacting": True,
            "caio_review_required": True,
            "approved_environments": ["SageMaker GovCloud — pending CAIO equitable audit"],
        },
    },

    # ── 7. EO Change Detection — SAR/Optical ──────────────────────────────────
    {
        "id": "aimc-dod-007",
        "name": "EO Change Detection — SAR/Optical",
        "description": (
            "IL5 LoRA fine-tune of LLaVA-Phi3 Vision for multi-modal SAR and optical imagery "
            "change detection. Identifies facility, vehicle, and infrastructure changes at 0.91 mAP."
        ),
        "il_level": "IL5",
        "adaptation_strategy": "finetune",
        "primary_use_case": "image_analysis",
        "classification": "CUI // SP-CTI",
        "rights_impacting": 0,
        "offset_hours": 180,
        "nodes": [
            _n("n7a", "doc-ingestion", "SAR/Optical Imagery Ingest", 50, 120, {"source": "NRO Classified Feed + Maxar", "resolution": "0.3m GSD", "format": "GeoTIFF"}),
            _n("n7b", "preprocessing", "Geospatial Preprocessor", 250, 120, {"tool": "GDAL + rasterio", "chip_size": "512x512", "augmentation": "random_flip+rotate"}),
            _n("n7c", "bnd-air-gap", "IL5 Air-Gap Boundary", 420, 60, {"il_enforcement": "IL5", "no_internet": True}),
            _n("n7d", "base-model", "LLaVA-Phi3 Vision (vLLM)", 450, 150, {"provider": "vLLM on-prem GPU cluster", "parameters": "3.8B", "modalities": ["SAR", "EO"]}),
            _n("n7e", "fine-tune-adapter", "LoRA Change Detection Adapter", 450, 270, {"dataset": "82K labeled image pairs", "rank": 32, "target_modules": "q_proj,v_proj"}),
            _n("n7f", "inference-endpoint", "Change Detection API (Air-Gap)", 650, 150, {"latency_p99_ms": 1200, "offline_only": True}),
            _n("n7g", "eval-rubric", "mAP @ IoU 0.50", 650, 270, {"map_50": 0.91, "categories": ["facility", "vehicle", "infrastructure"]}),
            _n("n7h", "confidence-threshold", "Confidence Gate ≥0.82", 850, 150, {"threshold": 0.82, "hitl_below": True}),
            _n("n7i", "audit-logger", "Geospatial Decision Log — AU-12", 850, 270, {"classification": "TS//SI", "retention_years": 25}),
        ],
        "assessments": [
            {
                "fid": "nist-ai-rmf", "fname": "NIST AI RMF 1.0",
                "score": 88.0, "passed": 1,
                "findings": [
                    {"control": "GOVERN-1.7", "status": "PASS", "note": "Use policy covers NTM imagery processing at IL5"},
                    {"control": "MAP-1.5", "status": "PASS", "note": "Change detection task scoped to facility/vehicle categories"},
                    {"control": "MEASURE-2.5", "status": "PASS", "note": "mAP 0.91 exceeds NGA ≥0.88 operational threshold"},
                    {"control": "MANAGE-2.2", "status": "WARN", "note": "Model drift detection for seasonal imagery variation not implemented"},
                    {"control": "GOVERN-4.2", "status": "PASS", "note": "NGA GEOINT AI governance policy documented"},
                ],
            },
            {
                "fid": "aimc-il-compliance", "fname": "AIMC IL Compliance Rules",
                "score": 100.0, "passed": 1,
                "findings": [
                    {"rule": "AIMC-IL-001", "status": "PASS", "note": "IL5 design has bnd-air-gap enforcement node"},
                    {"rule": "AIMC-IL-002", "status": "PASS", "note": "vLLM on-prem — no internet egress"},
                    {"rule": "AIMC-IL-003", "status": "PASS", "note": "NSA Type 1 encryption at air-gap boundary"},
                    {"rule": "AIMC-IL-004", "status": "PASS", "note": "Decision log at IL5 classification"},
                ],
            },
        ],
        "model_card": {
            "base_model": "LLaVA-Phi3 Vision 3.8B",
            "provider": "vLLM 0.4.x (air-gapped on-prem GPU cluster — A100 80GB x4)",
            "il_suitability": "IL5 / IL6",
            "adaptation": "LoRA rank=32 fine-tune on 82K labeled SAR/EO image pairs",
            "training_data": "82,000 labeled NTM imagery pairs (facility/vehicle/infrastructure) — clearance TS//SI",
            "metrics": {"map_50": 0.91, "precision": 0.89, "recall": 0.93, "latency_p99_ms": 1200},
            "refresh_cadence": "Annual retraining with new labeled imagery; seasonal bias review",
            "rights_impacting": False,
            "approved_environments": ["vLLM on-prem (IL5 accredited enclave)"],
        },
    },

    # ── 8. AI Audit Response Agent ─────────────────────────────────────────────
    {
        "id": "aimc-dod-008",
        "name": "AI Audit Response Agent",
        "description": (
            "Claude Sonnet 4.6 RAG agent that auto-generates DoD AI audit response packages. "
            "Covers NIST AI RMF, DoD RAI 5 Principles, OMB M-25-21. DoD RAI score 95/100."
        ),
        "il_level": "IL4",
        "adaptation_strategy": "rag",
        "primary_use_case": "audit_response",
        "classification": "CUI // SP-CTI",
        "rights_impacting": 0,
        "offset_hours": 120,
        "nodes": [
            _n("n8a", "doc-ingestion", "Governance Corpus Ingest", 50, 120, {"sources": ["NIST AI RMF", "DoD RAI", "OMB M-25-21", "EO 14110"], "docs": 8, "pages": 1200}),
            _n("n8b", "preprocessing", "Policy Chunker + Control Extractor", 250, 120, {"chunk_size": 512, "extract": ["control_id", "principle", "requirement"]}),
            _n("n8c", "vector-store", "OpenSearch — Gov Policy Index", 450, 100, {"index": "gov-ai-policy", "dimensions": 1536, "docs": 8600}),
            _n("n8d", "rag-retriever", "Policy Retriever (Top-8)", 450, 220, {"top_k": 8, "reranker": "cross-encoder"}),
            _n("n8e", "base-model", "Claude Sonnet 4.6 (Bedrock)", 650, 100, {"provider": "AWS Bedrock", "prompt_caching": True, "cache_hit_rate": 0.71}),
            _n("n8f", "output-handler", "Audit Package Generator", 850, 100, {"outputs": ["SSP narrative", "AI BOM", "model card", "evidence matrix"]}),
            _n("n8g", "eval-rubric", "Coverage + Citation Accuracy", 850, 220, {"control_coverage": 0.97, "citation_accuracy": 0.96}),
            _n("n8h", "audit-logger", "Audit Response Log", 1050, 100, {"linked_to": "canvas_ai_decisions", "immutable": True}),
        ],
        "assessments": [
            {
                "fid": "nist-ai-rmf", "fname": "NIST AI RMF 1.0",
                "score": 95.0, "passed": 1,
                "findings": [
                    {"control": "GOVERN-1.1", "status": "PASS", "note": "AI audit assistant policy reviewed and approved by CISO"},
                    {"control": "MAP-2.3", "status": "PASS", "note": "Use case bounded to advisory audit drafts — human AO reviews"},
                    {"control": "MEASURE-2.6", "status": "PASS", "note": "Control coverage 97%; citation accuracy 96% on test corpus"},
                    {"control": "MANAGE-4.2", "status": "PASS", "note": "Corpus versioned; responses reproducible per corpus version"},
                    {"control": "GOVERN-6.2", "status": "PASS", "note": "Supply chain: AWS Bedrock + OpenSearch — both FedRAMP High"},
                ],
            },
            {
                "fid": "dod-rai", "fname": "DoD Responsible AI Principles",
                "score": 95.0, "passed": 1,
                "findings": [
                    {"principle": "Responsible", "status": "PASS", "note": "Output labeled as AI-draft; AO retains authority to accept/reject"},
                    {"principle": "Equitable", "status": "PASS", "note": "No demographic data; audit response quality uniform across control families"},
                    {"principle": "Traceable", "status": "PASS", "note": "All 5 DoD RAI principles with NIST control cross-references in output"},
                    {"principle": "Reliable", "status": "PASS", "note": "97% control coverage replicated on 3 independent audit packages"},
                    {"principle": "Governable", "status": "PASS", "note": "Corpus updates require ISSM approval; version pinned at audit time"},
                ],
            },
        ],
        "model_card": {
            "base_model": "Claude Sonnet 4.6",
            "provider": "AWS Bedrock (us-east-1)",
            "il_suitability": "IL4",
            "adaptation": "Zero-shot RAG with prompt caching; 71% cache hit rate on policy corpus",
            "training_data": "N/A — corpus: NIST AI RMF + DoD RAI + OMB M-25-21 + EO 14110 (8,600 chunks)",
            "metrics": {
                "control_coverage": 0.97, "citation_accuracy": 0.96,
                "latency_p99_ms": 2300, "prompt_cache_hit_rate": 0.71,
                "dod_rai_score": 95,
            },
            "refresh_cadence": "On new policy release (typically quarterly)",
            "rights_impacting": False,
            "approved_environments": ["AWS Bedrock (us-east-1 / us-gov-west-1)", "OpenSearch GovCloud"],
        },
    },
]


# ── Seed Functions ─────────────────────────────────────────────────────────────

def _build_graph_json(d: dict) -> str:
    nodes = d["nodes"]
    edges = []
    for i in range(len(nodes) - 1):
        eid = f"e-{uuid.uuid4().hex[:8]}"
        edges.append({
            "id": eid,
            "source": nodes[i]["id"],
            "target": nodes[i + 1]["id"],
            "type": "data-flow",
            "label": "",
        })
    return json.dumps({"nodes": nodes, "edges": edges})


def seed_designs(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aiml_designs "
        "(id, name, description, graph_json, classification, il_level, "
        "primary_use_case, adaptation_strategy, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        ts = _now(d["offset_hours"])
        graph = _build_graph_json(d)
        conn.execute(sql, (
            d["id"], d["name"], d["description"], graph,
            d["classification"], d["il_level"],
            d["primary_use_case"], d["adaptation_strategy"],
            ts, ts,
        ))
        count += 1
    conn.commit()
    return count


def seed_nodes(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aiml_nodes "
        "(id, design_id, node_type, label, x, y, classification, properties_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        ts = _now(d["offset_hours"])
        for n in d["nodes"]:
            conn.execute(sql, (
                n["id"], d["id"], n["type"], n["label"],
                n["x"], n["y"],
                d["classification"],
                json.dumps(n.get("properties", {})),
                ts,
            ))
            count += 1
    conn.commit()
    return count


def seed_edges(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aiml_edges "
        "(id, design_id, source_node_id, target_node_id, edge_type, label, "
        "classification, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        ts = _now(d["offset_hours"])
        nodes = d["nodes"]
        for i in range(len(nodes) - 1):
            eid = f"e-{uuid.uuid4().hex[:8]}"
            conn.execute(sql, (
                eid, d["id"],
                nodes[i]["id"], nodes[i + 1]["id"],
                "data-flow", "",
                d["classification"], ts,
            ))
            count += 1
    conn.commit()
    return count


def seed_assessments(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aiml_assessments "
        "(id, design_id, framework_id, framework_name, findings_json, score, passed, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        ts = _now(d["offset_hours"])
        for a in d["assessments"]:
            aid = f"assess-{d['id']}-{a['fid']}"
            conn.execute(sql, (
                aid, d["id"],
                a["fid"], a["fname"],
                json.dumps(a["findings"]),
                a["score"], a["passed"],
                ts,
            ))
            count += 1
    conn.commit()
    return count


def seed_artifacts(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO aiml_artifacts "
        "(id, design_id, artifact_type, title, content_json, format, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    count = 0
    for d in DESIGNS:
        ts = _now(d["offset_hours"])
        mc_id = f"artifact-{d['id']}-modelcard"
        mc_content = {
            "design_id": d["id"],
            "design_name": d["name"],
            "rights_impacting": d["rights_impacting"],
            **d["model_card"],
        }
        conn.execute(sql, (
            mc_id, d["id"],
            "model_card",
            f"Model Card — {d['name']}",
            json.dumps(mc_content),
            "json",
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
            for tbl in [
                "aiml_artifacts", "aiml_assessments",
                "aiml_edges", "aiml_nodes", "aiml_designs",
            ]:
                conn.execute(f"DELETE FROM {tbl} WHERE id LIKE 'aimc-dod-%' OR id LIKE 'assess-aimc-%' OR id LIKE 'artifact-aimc-%'")
            conn.commit()
            print("[reset] Cleared existing AIMC demo records.")

        n_d = seed_designs(conn)
        n_n = seed_nodes(conn)
        n_e = seed_edges(conn)
        n_a = seed_assessments(conn)
        n_ar = seed_artifacts(conn)

        print(f"[AIMC] Seeded: {n_d} designs, {n_n} nodes, {n_e} edges, "
              f"{n_a} assessments, {n_ar} artifacts")
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Seed AIMC DoD/IC demo data")
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
        stats = {}
        for line in lines:
            for kv in line.replace("[AIMC] Seeded: ", "").split(", "):
                parts = kv.strip().split(" ")
                if len(parts) == 2:
                    stats[parts[1]] = int(parts[0])
        print(json.dumps({"status": "ok", "stats": stats}))
    else:
        main(reset=args.reset)
