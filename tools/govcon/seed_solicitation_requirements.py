#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed realistic shall statements for 3 target solicitations.

Inserts shall/must/will requirements for:
  - DHS-FY26-541511-0002  Cloud Migration & Modernization
  - DHS-FY26-541511-0008  Cloud Migration (ZTA emphasis)
  - DHS-FY26-541511-0304  AI/ML Platform Development

Idempotent: deletes existing rows for these opportunities before re-inserting.

Usage:
    python tools/govcon/seed_solicitation_requirements.py [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Requirement definitions per solicitation
# ---------------------------------------------------------------------------

_SOLICITATIONS: dict[str, dict] = {
    "DHS-FY26-541511-0002": {
        "opp_id": "47294739-614f-f3d7-19db-3ad0ddd1dfb2",
        "requirements": [
            # Cloud Migration
            {"text": "The Contractor shall develop a zero-downtime cloud migration strategy for all production workloads, including rollback procedures for each migration wave.", "type": "shall", "domain": "cloud", "keywords": ["migration", "zero-downtime", "rollback"]},
            {"text": "The Contractor shall obtain FedRAMP High authorization for the target cloud environment within 12 months of contract award.", "type": "shall", "domain": "ato_rmf", "keywords": ["fedramp", "high", "authorization"]},
            {"text": "The Contractor shall deploy a Kubernetes-based container orchestration platform (STIG-hardened) with Istio service mesh for mutual TLS inter-service communication.", "type": "shall", "domain": "devsecops", "keywords": ["kubernetes", "stig", "istio", "mtls"]},
            {"text": "The Contractor shall implement Infrastructure as Code (IaC) using Terraform and Ansible, with all changes subject to automated security gates before deployment.", "type": "shall", "domain": "cloud", "keywords": ["iac", "terraform", "ansible", "security gates"]},
            {"text": "The Contractor shall perform DISA STIG hardening on all operating systems and produce OpenSCAP-compatible compliance checklists.", "type": "shall", "domain": "compliance", "keywords": ["stig", "openscap", "hardening"]},
            {"text": "The Contractor shall classify all migrated data using automated classification tools, applying CUI and PII labels with enhanced access controls.", "type": "shall", "domain": "security", "keywords": ["classification", "cui", "pii", "access controls"]},
            {"text": "The Contractor shall deploy a centralized observability stack with SIEM integration, targeting Mean Time to Detect (MTTD) under 15 minutes for critical alerts.", "type": "shall", "domain": "data", "keywords": ["observability", "siem", "mttd"]},
            {"text": "The Contractor shall integrate DevSecOps pipelines with SLSA Level 3 attestation, including SBOM generation and image signing.", "type": "shall", "domain": "devsecops", "keywords": ["devsecops", "slsa", "sbom", "image signing"]},
            {"text": "The Contractor shall align the cloud architecture with the DoD Zero Trust Strategy across all seven pillars.", "type": "shall", "domain": "security", "keywords": ["zero trust", "zta", "dod", "strategy"]},
            {"text": "The Contractor shall develop a comprehensive ATO package including SSP, POAM, STIG checklists, and ConMon plans within 180 days.", "type": "shall", "domain": "ato_rmf", "keywords": ["ato", "ssp", "poam", "conmon"]},
            {"text": "The Contractor shall provide a detailed transition plan with knowledge transfer sessions and documentation handover.", "type": "shall", "domain": "management", "keywords": ["transition", "knowledge transfer", "documentation"]},
            {"text": "The Contractor shall guarantee 99.9% system availability during migration and 99.95% availability post-migration.", "type": "shall", "domain": "cloud", "keywords": ["availability", "sla", "uptime"]},
            {"text": "The Contractor shall implement automated disaster recovery and backup procedures with Recovery Point Objective (RPO) ≤1 hour and Recovery Time Objective (RTO) ≤4 hours.", "type": "shall", "domain": "cloud", "keywords": ["disaster recovery", "backup", "rpo", "rto"]},
            {"text": "The Contractor shall deploy an API gateway and service mesh with centralized authentication, rate limiting, and traffic encryption.", "type": "shall", "domain": "devsecops", "keywords": ["api gateway", "service mesh", "authentication", "encryption"]},
            {"text": "The Contractor shall maintain supply chain security via SBOM attestation in SPDX and CycloneDX formats, compliant with EO 14028 and NDAA Section 889.", "type": "shall", "domain": "compliance", "keywords": ["sbom", "supply chain", "eo 14028", "ndaa 889"]},
        ],
    },
    "DHS-FY26-541511-0008": {
        "opp_id": "dd56cc94-3c9a-d14c-ee0c-aeb5ecfedb99",
        "requirements": [
            # Cloud Migration with ZTA / Security emphasis
            {"text": "The Contractor shall implement a Zero Trust Architecture (ZTA) across all cloud and on-premises environments, assessed against NIST SP 800-207.", "type": "shall", "domain": "security", "keywords": ["zta", "zero trust", "nist 800-207"]},
            {"text": "The Contractor shall enforce microsegmentation via Kubernetes NetworkPolicies and Istio service mesh with mutual TLS for all service-to-service communication.", "type": "shall", "domain": "security", "keywords": ["microsegmentation", "networkpolicies", "istio", "mtls"]},
            {"text": "The Contractor shall deploy AI-powered threat detection using MITRE ATLAS v5.4 techniques with automated incident response playbooks.", "type": "shall", "domain": "security", "keywords": ["ai threat detection", "mitre atlas", "incident response"]},
            {"text": "The Contractor shall automate NIST SP 800-53 Rev 5 control assessment using a compliance crosswalk engine mapping to FedRAMP, CMMC, and CJIS.", "type": "shall", "domain": "compliance", "keywords": ["nist 800-53", "fedramp", "cmmmc", "cjis", "crosswalk"]},
            {"text": "The Contractor shall generate SBOMs and supply chain attestation packages in SPDX and CycloneDX formats for every software build, achieving SLSA Level 3.", "type": "shall", "domain": "compliance", "keywords": ["sbom", "spdx", "cyclonedx", "slsa"]},
            {"text": "The Contractor shall implement IL5 data display and ingestion services with SLA enforcement for all CUI and SECRET data handling.", "type": "shall", "domain": "security", "keywords": ["il5", "cui", "secret", "sla"]},
            {"text": "The Contractor shall produce automated ConMon plans with continuous STIG drift detection and auto-remediation for non-compliant configurations.", "type": "shall", "domain": "compliance", "keywords": ["conmon", "stig", "drift detection", "auto-remediation"]},
            {"text": "The Contractor shall harden all container images against DISA STIG benchmarks and scan with OpenSCAP before deployment.", "type": "shall", "domain": "compliance", "keywords": ["stig", "hardening", "openscap", "containers"]},
            {"text": "The Contractor shall establish a Security Operations Center (SOC) integration with centralized logging, alerting, and case management.", "type": "shall", "domain": "security", "keywords": ["soc", "logging", "alerting", "case management"]},
            {"text": "The Contractor shall develop and deliver a Cybersecurity Workforce Development program aligned to NICE Framework specialty areas.", "type": "shall", "domain": "management", "keywords": ["workforce", "nice framework", "training", "cybersecurity"]},
            {"text": "The Contractor shall implement a Vulnerability Management Program with automated scanning, risk scoring, and patch orchestration.", "type": "shall", "domain": "security", "keywords": ["vulnerability", "scanning", "risk scoring", "patch"]},
            {"text": "The Contractor shall maintain an immutable audit trail for all security events, satisfying NIST 800-53 AU controls and FedRAMP evidence requirements.", "type": "shall", "domain": "compliance", "keywords": ["audit trail", "immutable", "nist au", "fedramp"]},
            {"text": "The Contractor shall conduct quarterly Red Team exercises using ATLAS-based adversarial tests and produce remediation reports.", "type": "shall", "domain": "security", "keywords": ["red team", "atlas", "adversarial", "remediation"]},
            {"text": "The Contractor shall provide a Phased Transition Plan with parallel operation for 90 days before full production cutover.", "type": "shall", "domain": "management", "keywords": ["transition", "parallel operation", "cutover"]},
        ],
    },
    "DHS-FY26-541511-0304": {
        "opp_id": "bff9507d-cd14-a03e-8359-9af65d01f55f",
        "requirements": [
            # AI/ML Platform Development
            {"text": "The Contractor shall deploy a Multi-Agent Orchestration framework supporting at least 15 autonomous agents across 3 tiers with A2A protocol communication over mutual TLS.", "type": "shall", "domain": "ai_ml", "keywords": ["multi-agent", "orchestration", "a2a", "mtls"]},
            {"text": "The Contractor shall implement a Retrieval-Augmented Generation (RAG) pipeline with two-stage retrieval, BM25 lexical boost, and citation grounding to actual source chunks.", "type": "shall", "domain": "ai_ml", "keywords": ["rag", "retrieval", "bm25", "citations"]},
            {"text": "The Contractor shall provide AI Security capabilities including prompt injection detection (5 categories), OWASP LLM Top 10 v2025 assessment, and MITRE ATLAS v5.4 mapping.", "type": "shall", "domain": "security", "keywords": ["ai security", "prompt injection", "owasp", "atlas"]},
            {"text": "The Contractor shall deliver a Synthetic Data Engine capable of generating domain-specific datasets for cyber, finance, and healthcare with quality validation and differential privacy.", "type": "shall", "domain": "ai_ml", "keywords": ["synthetic data", "privacy", "quality validation", "domains"]},
            {"text": "The Contractor shall ensure AI governance compliance with NIST AI RMF 1.0 and ISO 42001, including bias detection, explainability, and audit trail.", "type": "shall", "domain": "compliance", "keywords": ["ai governance", "nist ai rmf", "iso 42001", "bias"]},
            {"text": "The Contractor shall implement IL5 data ingestion services with SLA enforcement for all AI/ML training and inference data.", "type": "shall", "domain": "data", "keywords": ["il5", "ingestion", "sla", "training", "inference"]},
            {"text": "The Contractor shall deploy scalable model inference with auto-scaling, blue-green deployment, and one-command rollback capabilities.", "type": "shall", "domain": "ai_ml", "keywords": ["inference", "auto-scaling", "blue-green", "rollback"]},
            {"text": "The Contractor shall integrate Human-in-the-Loop (HITL) review gates for all AI-generated content, with mandatory Accept/Reject/Revise workflows.", "type": "shall", "domain": "ai_ml", "keywords": ["hitl", "review gates", "accept", "reject", "revise"]},
            {"text": "The Contractor shall provide continuous model monitoring with drift detection, performance degradation alerts, and automatic retraining triggers.", "type": "shall", "domain": "ai_ml", "keywords": ["model monitoring", "drift detection", "retraining"]},
            {"text": "The Contractor shall develop an ATO/RMF package for the AI/ML system within 180 days, including AI-specific security controls and risk assessments.", "type": "shall", "domain": "ato_rmf", "keywords": ["ato", "rmf", "ai security controls", "risk assessment"]},
            {"text": "The Contractor shall ensure all LLM outputs include inline citations linking to actual source documents in the RAG index.", "type": "shall", "domain": "ai_ml", "keywords": ["llm", "citations", "rag", "grounding"]},
            {"text": "The Contractor shall implement explainability mechanisms for all AI-driven decisions, producing human-readable rationale logs.", "type": "shall", "domain": "ai_ml", "keywords": ["explainability", "rationale", "transparency"]},
            {"text": "The Contractor shall integrate the AI platform with existing SOAR and SIEM systems for automated incident response and threat correlation.", "type": "shall", "domain": "security", "keywords": ["soar", "siem", "integration", "incident response"]},
            {"text": "The Contractor shall deliver a Model Versioning and Governance system tracking lineage, training data provenance, and deployment artifacts.", "type": "shall", "domain": "ai_ml", "keywords": ["model versioning", "lineage", "provenance", "governance"]},
            {"text": "The Contractor shall provide a 90-day Pilot Program with measurable performance benchmarks before full production deployment.", "type": "shall", "domain": "management", "keywords": ["pilot", "benchmarks", "production"]},
        ],
    },
}


def _delete_existing(conn, opp_ids: list[str]) -> int:
    placeholders = ",".join("?" for _ in opp_ids)
    result = conn.execute(
        f"DELETE FROM rfp_shall_statements WHERE proposal_opportunity_id IN ({placeholders})",
        opp_ids,
    )
    return result.rowcount if hasattr(result, "rowcount") else 0


def _insert_requirement(conn, opp_id: str, req: dict) -> str:
    req_id = str(uuid.uuid4())
    now = _utcnow_iso()
    conn.execute(
        """
        INSERT INTO rfp_shall_statements (
            id, proposal_opportunity_id, statement_text, statement_type,
            domain_category, keywords, keyword_fingerprint, source_section,
            content_hash, extracted_at, classification
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            req_id,
            opp_id,
            req["text"],
            req["type"],
            req.get("domain"),
            json.dumps(req.get("keywords", [])),
            None,
            "Section C",
            _hash(req["text"]),
            now,
            "CUI",
        ),
    )
    return req_id


def seed(dry_run: bool = False) -> dict:
    conn = get_connection()

    opp_ids = [s["opp_id"] for s in _SOLICITATIONS.values()]
    deleted = _delete_existing(conn, opp_ids)

    inserted: dict[str, list[str]] = {}
    for sol_num, spec in _SOLICITATIONS.items():
        ids = []
        for req in spec["requirements"]:
            if dry_run:
                ids.append("dry-run")
                continue
            req_id = _insert_requirement(conn, spec["opp_id"], req)
            ids.append(req_id)
        inserted[sol_num] = ids

    if not dry_run:
        conn.commit()

    return {
        "deleted": deleted,
        "inserted": {k: len(v) for k, v in inserted.items()},
        "total": sum(len(v) for v in inserted.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed solicitation shall statements")
    parser.add_argument("--dry-run", action="store_true", help="Preview without inserting")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = seed(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Solicitation requirements seeded:")
        print(f"  Deleted previous: {result['deleted']}")
        for sol, cnt in result["inserted"].items():
            print(f"  {sol}: {cnt} requirements")
        print(f"  Total: {result['total']}")


if __name__ == "__main__":
    main()
