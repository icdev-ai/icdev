# CUI // SP-CTI
"""Seed AI Observatory with 200 realistic DoD/IC canvas_ai_decisions.

Spread over 30 days — creates time-series data for Observatory charts.
Distribution matches plan:
  Canvas: aadc(45), aimc(38), mc(28), sdc(22), ndc(18), idc(15), ddc(12), others(22)
  Decision type: compliance_finding(52), risk_score(44), threat_assessment(30),
                 readiness_assessment(26), confabulation_flag(18), narrative(14), others(16)
  Confidence: ≥0.85(110), 0.50–0.84(58), 0.30–0.49(14), <0.30(18)

Run:
    python tools/db/seeds/seed_ai_canvases_observatory.py
    python tools/db/seeds/seed_ai_canvases_observatory.py --reset
"""
from __future__ import annotations

import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402


def _ts(offset_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=offset_hours)).isoformat()


# ── Stable decision templates (content varies; IDs are deterministic) ─────────

_MODELS = [
    "claude-sonnet-4-6", "claude-sonnet-4-6",
    "llama3-70b-ollama", "ibm-granite-13b",
    "gpt-4o-azure-gov", "xgboost-sagemaker",
]

_AADC_DESIGNS = [
    "aadc-dod-001", "aadc-dod-002", "aadc-dod-003",
    "aadc-dod-004", "aadc-dod-005", "aadc-dod-006",
    "aadc-dod-007", "aadc-dod-008",
]

_AIMC_DESIGNS = [
    "aimc-dod-001", "aimc-dod-002", "aimc-dod-003",
    "aimc-dod-004", "aimc-dod-005", "aimc-dod-006",
    "aimc-dod-007", "aimc-dod-008",
]

_COMPLIANCE_DECISIONS = [
    ("NIST AI RMF GOVERN-1.1: AI use policy requires annual review and CISO sign-off.",
     "Policy last reviewed 14 months ago. Trigger review cycle immediately.", 0.93),
    ("OMB M-25-21 §4: Rights-impacting AI system detected without CAIO override node.",
     "Design aadc-dod-006 Personnel Readiness lacks documented CAIO override path.", 0.91),
    ("OWASP LLM01: Prompt injection surface identified in user-facing RAG interface.",
     "Input sanitization layer absent upstream of inference endpoint.", 0.88),
    ("NIST AI RMF MEASURE-2.5: Accuracy threshold 0.90 not met (current: 0.871).",
     "Retrain on expanded corpus or adjust confidence gate to 0.80.", 0.87),
    ("NIST AI 600-1 GAI.1: Confabulation risk flagged — LLM response not grounded.",
     "Response contains dates not present in retrieved context chunks.", 0.35),
    ("CMMC 2.0 Practice AC.2.006: Least privilege not enforced on Bedrock IAM role.",
     "IAM policy grants bedrock:* — restrict to bedrock:InvokeModel on specific ARN.", 0.89),
    ("DoD RAI Equitable: Demographic parity gap detected in classifier outputs.",
     "F1 delta >0.08 between demographic groups. Bias audit required.", 0.86),
    ("EO 14110 §4.2: AI system processing federal data lacks impact assessment.",
     "Complete AI impact assessment before next production deployment.", 0.92),
    ("FedRAMP High: AI inference endpoint not deployed in FedRAMP-authorized environment.",
     "Migrate from commercial to GovCloud endpoint before go-live.", 0.88),
    ("NIST SP 800-53 AU-12: Audit record generation not configured for AI decisions.",
     "Enable decision logging in audit-logger node; route to SIEM.", 0.90),
    ("NIST AI RMF GOVERN-6.2: Third-party model supply chain not documented.",
     "Generate AI BOM with base model provenance, training data, and license info.", 0.85),
    ("OMB M-25-21 §3: AI system not listed in agency AI inventory.",
     "Submit inventory entry within 60 days. Use gov-system-card node output.", 0.89),
]

_RISK_DECISIONS = [
    ("Composite risk score: 0.74 (HIGH). Adversarial input injection probability elevated.",
     "ATLAS AML.T0043 partially mitigated. Missing data provenance check.", 0.82),
    ("Risk score recalculated after red team: 0.61 (MEDIUM). 2 of 5 STRIDE findings resolved.",
     "Remaining threats: Tampering (data pipeline) + Repudiation (no audit log).", 0.79),
    ("IL5 design risk score: 0.43 (LOW). Air-gap boundary enforces containment.",
     "All 4 AIMC-IL compliance rules passed. Recommend APPROVED state.", 0.95),
    ("Supply chain risk: 0.68 (HIGH). Base model training data provenance unverified.",
     "IBM Granite 13B — request model card from watsonx.ai support.", 0.77),
    ("Model drift risk: 0.71. Prediction distribution shifted >2σ from baseline.",
     "Retrain within 30 days. Activate drift-detector alert to PM.", 0.83),
    ("Insider threat risk: 0.38 (LOW). Behavioral Analyst AADC design ATO ready.",
     "nist_rmf_score=91.5%, CAIO override present, ato_ready=1.", 0.94),
    ("Automated remediation risk: 0.82 (HIGH). Agent has write access to production DB.",
     "Restrict agent permissions to read-only until HITL gate is implemented.", 0.87),
    ("Residual risk after mitigation: 0.29 (LOW). Acceptable for IL4 deployment.",
     "3 risk items closed. 0 critical unmitigated. Recommend gate PASS.", 0.91),
    ("Risk score escalation: 0.65 → 0.79. New ATLAS TTP AML.T0020 disclosed.",
     "Adversarial patch attack applicable to EO change detection model. Patch required.", 0.85),
    ("Financial risk score: 0.55. Legacy DFAS rules engine COBOL conversion increases risk.",
     "Pilot on non-production data first. Rollback plan required.", 0.74),
]

_THREAT_DECISIONS = [
    ("ATLAS AML.T0043 (Adversarial Input): HIGH risk. SIGINT model vulnerable to RF spoofing.",
     "Mitigate: add input validation layer + anomaly detector before inference.", 0.88),
    ("STRIDE Tampering: MEDIUM. Training data pipeline has no integrity check.",
     "Implement SHA-256 checksums on training data manifests.", 0.84),
    ("STRIDE Information Disclosure: HIGH. Model responses may leak training data.",
     "Add differential privacy budget to fine-tuning; audit output for memorization.", 0.86),
    ("ATLAS AML.T0020 (Adversarial Patch): MEDIUM. EO imagery model susceptible.",
     "Adversarial training on 5% augmented patch samples reduces risk by 60%.", 0.83),
    ("Threat actor TTPs mapped: APT40 targeting defense logistics AI systems.",
     "STIX bundle updated. IOC matcher refreshed with 14 new indicators.", 0.79),
    ("ATLAS AML.T0018 (Model Inversion): LOW. Limited by confidence threshold gate.",
     "Confidence gate ≥0.70 prevents full output that enables inversion attack.", 0.91),
    ("STRIDE Denial of Service: MEDIUM. Inference endpoint lacks rate limiting.",
     "Implement API Gateway throttle: 100 req/min per authenticated user.", 0.85),
    ("Threat model updated post-DISA review: 3 new STIG findings applied to AI system.",
     "LLM STIG V1R1: container hardening + model file permissions + audit log encryption.", 0.90),
    ("ATLAS AML.T0019 (LLM Extraction): LOW. Claude Sonnet 4.6 system prompt protected.",
     "System prompt stored in Secrets Manager; not accessible via user turn.", 0.87),
    ("Supply chain threat: malicious dependency in Python ML stack flagged by SBOM scan.",
     "Upgrade transformers==4.40.2 → 4.41.1 (patch for CVE-2024-34892).", 0.88),
]

_READINESS_DECISIONS = [
    ("Readiness assessment: GCSS-Army AI Modernization — Phase 1 ready to execute.",
     "MaintenanceDecisionMap and SupplyKeywordSearch fully scoped. Effort: 37 days.", 0.88),
    ("Readiness assessment: AIMC Personnel Readiness — CAIO review BLOCKING.",
     "DoD RAI Equitable score 72/100. Demographic parity audit must complete first.", 0.91),
    ("Readiness assessment: SIEM IOC embedding pilot — 10-day timeline confirmed.",
     "FAISS index size 2.4GB fits within IL4 enclave storage allocation.", 0.87),
    ("Readiness assessment: AADC Insider Threat design — ATO ready.",
     "ato_ready=1, nist_rmf=91.5%, caio_override node present.", 0.95),
    ("Readiness assessment: JADC2 Mission Planning — 2 ATO blockers outstanding.",
     "Missing rate-limiter node and HITL escalation path. ETA to resolve: 3 weeks.", 0.89),
    ("Readiness assessment: JTRS Radio Firmware AI — safety gate required.",
     "Safety-critical classifiers need hardware validation on embedded target.", 0.83),
    ("Readiness assessment: FAR/DFARS Q&A — production ready.",
     "89.4% clause accuracy, 62% prompt cache hit rate, COCO review complete.", 0.93),
    ("Readiness assessment: DoD Document Classification — quarterly retrain overdue.",
     "Last retrain FY24Q2. F1 drift from 0.923 → 0.901. Schedule retraining.", 0.86),
    ("Readiness assessment: HR Portal AI modernization — Phase 1 ready.",
     "Semantic search + LLM reports do not touch rights-impacting data. Low risk.", 0.91),
    ("Readiness assessment: DFAS Financial System — high-risk due to legacy COBOL base.",
     "Recommend phased approach. Start with narrative generation (lowest risk).", 0.84),
]

_CONFABULATION_DECISIONS = [
    ("Confabulation detected: LLM cited FAR clause 52.219-9 which was not in retrieved context.",
     "Response discarded. Confidence score 0.22 below 0.30 threshold — HITL queue.", 0.22),
    ("Confabulation flag: Model claimed OMB M-25-21 was signed in 2023 (actual: April 2025).",
     "Date hallucination. Retrieved chunk predates the memo. Needs corpus update.", 0.28),
    ("Confabulation flag: SIGINT entity extraction returned a non-existent frequency band.",
     "Output confidence 0.19. Sent to HITL queue for analyst review.", 0.19),
    ("Confabulation detected: Audit response cited NIST control AC-7.3 which does not exist.",
     "AC-7 has no .3 subcontrol. System prompt updated with control list constraints.", 0.25),
    ("Confabulation flag: Threat actor attribution to APT41 not supported by retrieved STIX data.",
     "No APT41 indicator in the 8 retrieved chunks. Low confidence 0.27 flagged.", 0.27),
    ("Confabulation flag: Personnel readiness narrative included fabricated policy citation.",
     "Cited 'AR 600-9 Paragraph 4-7' — paragraph doesn't exist in current AR 600-9.", 0.23),
    ("Confabulation detected: Model stated supply chain AI system was FedRAMP certified.",
     "FedRAMP certification not in corpus. Confidence 0.26. Sent for review.", 0.26),
    ("Confabulation flag: Financial audit narrative included incorrect DFAS org code.",
     "Org code 'F8500' not in any retrieved chunk. Likely interpolated from context.", 0.24),
    ("Confabulation flag: EO change detection model output included non-existent GPS coord.",
     "Coordinate outside AOI bounding box. Confidence 0.21. Discarded.", 0.21),
    ("Confabulation detected: RAG response cited a CMMC 2.0 practice that was removed in v2.0.",
     "Practice CA.2.004 removed in CMMC 2.0. Corpus version mismatch.", 0.29),
    ("Confabulation flag: STIX report included threat actor TTP not in ATT&CK v14.",
     "T1562.011 not present in MITRE ATT&CK v14. Likely v13 residue in corpus.", 0.27),
    ("Confabulation flag: LLM audit response cited a closed POAM item as still open.",
     "POAM item #47 closed FY24Q3. Stale training data artifact.", 0.23),
    ("Confabulation detected: Model cited non-existent DoDI for AI system accreditation.",
     "DoDI 8510.02 covers RMF, not AI specifically. System prompt updated.", 0.25),
    ("Confabulation flag: Contract compliance response included incorrect DFARS clause number.",
     "DFARS 252.204-7024 cited but clause 252.204-7012 is the applicable one.", 0.28),
    ("Confabulation flag: Readiness forecast referenced a Manning Document from FY19.",
     "Unit structure changed in FY21 MTOE update. Corpus needs refresh.", 0.22),
    ("Confabulation detected: AI Audit Agent cited EO 14110 Section 7 for AI governance.",
     "Section 7 covers Other Requirements. Section 4 covers AI Safety. Wrong section.", 0.26),
    ("Confabulation flag: SIGINT transcript contained entity type not in training taxonomy.",
     "Entity type 'SATCOM_BURST' hallucinated — not in SIGINT entity gazetteer.", 0.19),
    ("Confabulation detected: DoD RAI scoring cited principle 6 (DoD RAI has 5 principles).",
     "Model enumerated 6 principles. Principle 6 fabricated. Output discarded.", 0.21),
]

_NARRATIVE_DECISIONS = [
    ("Executive summary generated for AADC DoD AI Inventory Governance Monitor.",
     "ATO ready, 93% overall score, rights-impacting with CAIO oversight. Ready for CAIO briefing.", 0.89),
    ("Technical narrative: AIMC FAR/DFARS Q&A system — 29x ROI analysis.",
     "Labor equivalent: GS-12 at $255K/year vs $9K/month AI cost. ROI confirmed.", 0.91),
    ("Briefing narrative for JADC2 Mission Planning Assistant — executive summary.",
     "Highlights: HITL gate, circuit breaker, real-time decision traceability.", 0.88),
    ("Narrative: GCSS-Army modernization — Phase 1 business case for G-4 leadership.",
     "MaintenanceDecisionMap replacement: 37-day effort, $180K savings/year projected.", 0.87),
    ("AI Observatory summary: 30-day decision trace report for CAIO dashboard.",
     "200 decisions, 18 confabulation flags (9%), 110 high-confidence auto-resolved.", 0.93),
    ("Technical narrative: JTRS waveform classifier — safety gate justification.",
     "Embedded hardware latency <2ms constraint met. HITL for safety-critical channel select.", 0.85),
    ("Executive narrative: SIEM AI modernization — DISA SOC leadership briefing.",
     "IOC embedding pilot (10 days) reduces false positive rate from 23% to 8%.", 0.90),
    ("Narrative: Personnel Readiness Forecast — CAIO review package summary.",
     "Rights-impacting per OMB M-25-21. CAIO hold pending demographic parity audit.", 0.88),
    ("Technical narrative: AI Supply Chain Assessor design — ATO package summary.",
     "2 ATO blockers: cloud model IL gap + missing PII scrubber. ETA to resolve: 2 weeks.", 0.86),
    ("Briefing narrative: DoD AI Governance Monitor — CAIO briefing ready.",
     "93% score, ato_ready=1, rights_impacting=1, CAIO override node satisfies §4.", 0.91),
]


def _build_records() -> list[dict]:
    """Build 200 deterministic records spread over 30 days."""
    rng = random.Random(42)  # deterministic

    # Canvas distribution (total=200)
    canvas_pool = (
        ["aadc"] * 45 + ["aimc"] * 38 + ["mc"] * 28 +
        ["sdc"] * 22 + ["ndc"] * 18 + ["idc"] * 15 +
        ["ddc"] * 12 + ["odc"] * 8 + ["pdc"] * 6 + ["genesis"] * 8
    )
    rng.shuffle(canvas_pool)

    # Decision type distribution (total=200)
    dtype_pool = (
        ["compliance_finding"] * 52 + ["risk_score"] * 44 +
        ["threat_assessment"] * 30 + ["readiness_assessment"] * 26 +
        ["confabulation_flag"] * 18 + ["narrative"] * 14 +
        ["boundary_impact"] * 6 + ["anomaly_detection"] * 5 +
        ["classification"] * 3 + ["remediation"] * 2
    )
    rng.shuffle(dtype_pool)

    # Confidence distribution (total=200)
    conf_pool = (
        [rng.uniform(0.85, 0.99) for _ in range(110)] +
        [rng.uniform(0.50, 0.84) for _ in range(58)] +
        [rng.uniform(0.30, 0.49) for _ in range(14)] +
        [rng.uniform(0.10, 0.29) for _ in range(18)]
    )
    rng.shuffle(conf_pool)

    # Decision content by type
    _content_map: dict[str, list] = {
        "compliance_finding": _COMPLIANCE_DECISIONS,
        "risk_score": _RISK_DECISIONS,
        "threat_assessment": _THREAT_DECISIONS,
        "readiness_assessment": _READINESS_DECISIONS,
        "confabulation_flag": _CONFABULATION_DECISIONS,
        "narrative": _NARRATIVE_DECISIONS,
    }

    # Record IDs per canvas
    _canvas_records: dict[str, list] = {
        "aadc": _AADC_DESIGNS,
        "aimc": _AIMC_DESIGNS,
    }

    records = []
    for i in range(200):
        canvas = canvas_pool[i]
        dtype = dtype_pool[i]
        conf = round(conf_pool[i], 3)

        # Pick decision + rationale content
        content_list = _content_map.get(dtype, _COMPLIANCE_DECISIONS)
        dec_text, rationale, _ = content_list[i % len(content_list)]

        # For confabulation flags force confidence below 0.30
        if dtype == "confabulation_flag":
            _, _, conf = _CONFABULATION_DECISIONS[i % len(_CONFABULATION_DECISIONS)]

        # Stable record_id
        record_id = None
        if canvas == "aadc":
            record_id = _AADC_DESIGNS[i % len(_AADC_DESIGNS)]
        elif canvas == "aimc":
            record_id = _AIMC_DESIGNS[i % len(_AIMC_DESIGNS)]

        # Spread over 30 days (720 hours), denser near the present
        hour_offset = rng.uniform(0, 720)

        # Deterministic UUID from index
        record_uuid = str(uuid.UUID(int=i + 1))

        records.append({
            "id": record_uuid,
            "canvas_type": canvas,
            "record_id": record_id,
            "decision_type": dtype,
            "decision": dec_text,
            "rationale": rationale,
            "model_used": rng.choice(_MODELS),
            "confidence": conf,
            "alternatives": json.dumps([]),
            "trace_id": f"trace-{uuid.uuid4().hex[:12]}",
            "span_id": f"span-{uuid.uuid4().hex[:8]}",
            "actor": "icdev-system",
            "classification": "CUI // SP-CTI",
            "created_at": _ts(hour_offset),
        })

    return records


def seed_decisions(conn) -> int:
    sql = (
        "INSERT OR IGNORE INTO canvas_ai_decisions "
        "(id, canvas_type, record_id, decision_type, decision, rationale, "
        "model_used, confidence, alternatives, trace_id, span_id, actor, "
        "classification, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    records = _build_records()
    count = 0
    for r in records:
        conn.execute(sql, (
            r["id"], r["canvas_type"], r["record_id"],
            r["decision_type"], r["decision"], r["rationale"],
            r["model_used"], r["confidence"], r["alternatives"],
            r["trace_id"], r["span_id"], r["actor"],
            r["classification"], r["created_at"],
        ))
        count += 1
    conn.commit()
    return count


def main(reset: bool = False) -> None:
    conn = get_connection()
    try:
        if reset:
            ids = [str(uuid.UUID(int=i + 1)) for i in range(200)]
            for uid in ids:
                conn.execute("DELETE FROM canvas_ai_decisions WHERE id = ?", (uid,))
            conn.commit()
            print("[reset] Cleared 200 Observatory demo records.")

        n = seed_decisions(conn)
        print(f"[Observatory] Seeded: {n} canvas_ai_decisions")
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Seed AI Observatory DoD/IC demo data")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if args.as_json:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(reset=args.reset)
        lines = buf.getvalue().strip().split("\n")
        count = 0
        for line in lines:
            for part in line.split(": "):
                try:
                    count = int(part.split()[0])
                    break
                except (ValueError, IndexError):
                    pass
        print(json.dumps({"status": "ok", "decisions_seeded": count}))
    else:
        main(reset=args.reset)
