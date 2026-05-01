# CUI // SP-CTI
"""Migration 080 — WF Report Ingestion: file ingest tracking, section defs, generated reports."""
from __future__ import annotations

MIGRATION_ID = "080"
MIGRATION_NAME = "wf_report_ingestion"

_DDL = """
-- Uploaded/ingested files linked to wf_document_templates
CREATE TABLE IF NOT EXISTS wf_ingested_files (
    id TEXT PRIMARY KEY,
    doc_template_id TEXT NOT NULL REFERENCES wf_document_templates(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size_bytes INTEGER,
    content_hash TEXT NOT NULL,
    storage_path TEXT,
    page_count INTEGER,
    section_count INTEGER,
    ingestion_status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    chunk_count INTEGER DEFAULT 0,
    ingested_by TEXT,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wf_ingest_doc ON wf_ingested_files(doc_template_id, ingestion_status);
CREATE INDEX IF NOT EXISTS idx_wf_ingest_hash ON wf_ingested_files(content_hash);

-- Section schema for each built-in and custom report type
CREATE TABLE IF NOT EXISTS wf_report_section_defs (
    id TEXT PRIMARY KEY,
    report_type TEXT NOT NULL,
    section_key TEXT NOT NULL,
    section_name TEXT NOT NULL,
    description TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    required INTEGER NOT NULL DEFAULT 1,
    source_hints TEXT,
    min_chunks INTEGER DEFAULT 1,
    max_chunks INTEGER DEFAULT 8,
    max_words INTEGER DEFAULT 800,
    UNIQUE(report_type, section_key)
);

-- Generated reports attached to workflow instances
CREATE TABLE IF NOT EXISTS wf_generated_reports (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES wf_instances(id),
    report_type TEXT NOT NULL,
    style_guide_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    content_html TEXT,
    content_json TEXT,
    word_count INTEGER,
    section_count INTEGER,
    citation_count INTEGER,
    error_message TEXT,
    generated_by TEXT,
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wf_reports_instance ON wf_generated_reports(instance_id, status);

-- Chunk-to-section assignments with relevance scores
CREATE TABLE IF NOT EXISTS wf_report_section_chunks (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES wf_generated_reports(id) ON DELETE CASCADE,
    section_key TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    relevance_score REAL,
    rank INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wf_rsc_report ON wf_report_section_chunks(report_id, section_key);
"""

_SEED_SECTION_DEFS = [
    # standard_audit
    ("ssd-sa-01", "standard_audit", "exec_summary",    "Executive Summary",
     "High-level overview of audit scope, key findings, and overall risk posture for executive audience.", 1, 1, '["standard","sop_reference"]', 2, 5, 400),
    ("ssd-sa-02", "standard_audit", "scope_methodology","Scope and Methodology",
     "Audit boundaries, systems in scope, methodologies applied, standards referenced, and limitations.", 2, 1, '["standard","sop_reference"]', 2, 8, 600),
    ("ssd-sa-03", "standard_audit", "findings",         "Findings",
     "Detailed audit findings including observed gaps, non-conformances, and evidence collected.", 3, 1, '["checklist","sop_reference","standard"]', 3, 10, 1200),
    ("ssd-sa-04", "standard_audit", "risk_assessment",  "Risk Assessment",
     "Risk rating for each finding using likelihood and impact dimensions; overall risk score.", 4, 1, '["standard"]', 2, 8, 800),
    ("ssd-sa-05", "standard_audit", "recommendations",  "Recommendations",
     "Prioritized corrective actions and recommendations to address identified findings.", 5, 1, '["sop_reference","standard"]', 2, 8, 800),
    ("ssd-sa-06", "standard_audit", "appendix",         "Appendix",
     "Supporting evidence, reference documents, data tables, and glossary.", 6, 0, '["sop_reference","standard","form"]', 1, 6, 600),

    # compliance_assessment
    ("ssd-ca-01", "compliance_assessment", "exec_summary",       "Executive Summary",
     "Summary of compliance posture, overall pass/fail status, and key risk areas for leadership.", 1, 1, '["standard"]', 2, 5, 400),
    ("ssd-ca-02", "compliance_assessment", "control_assessment",  "Control Assessment",
     "Per-control evaluation: control objective, current state, evidence reviewed, and conformance status.", 2, 1, '["standard","sop_reference","checklist"]', 3, 10, 1200),
    ("ssd-ca-03", "compliance_assessment", "gap_analysis",        "Gap Analysis",
     "Controls or requirements not currently met, with root cause analysis and gap severity.", 3, 1, '["standard","sop_reference"]', 2, 8, 800),
    ("ssd-ca-04", "compliance_assessment", "remediation_plan",    "Remediation Plan",
     "Prioritized remediation actions with owners, timelines, and success criteria for each gap.", 4, 1, '["sop_reference","standard"]', 2, 8, 800),
    ("ssd-ca-05", "compliance_assessment", "evidence_summary",    "Evidence Summary",
     "Catalog of evidence collected during assessment with source, date, and relevance to controls.", 5, 1, '["checklist","form","sop_reference"]', 1, 6, 600),

    # security_review
    ("ssd-sr-01", "security_review", "exec_summary",          "Executive Summary",
     "High-level security posture, critical vulnerabilities, and immediate action items.", 1, 1, '["standard"]', 2, 5, 400),
    ("ssd-sr-02", "security_review", "threat_model",           "Threat Model",
     "Threat actors, attack vectors, system assets at risk, and trust boundary analysis.", 2, 1, '["standard","sop_reference"]', 2, 8, 800),
    ("ssd-sr-03", "security_review", "vulnerability_summary",  "Vulnerability Summary",
     "Enumerated vulnerabilities with CVE references, affected components, and exploitation likelihood.", 3, 1, '["standard","sop_reference"]', 3, 10, 1000),
    ("ssd-sr-04", "security_review", "risk_rating",            "Risk Rating",
     "CVSS-based risk ratings for each vulnerability with business impact contextualization.", 4, 1, '["standard"]', 2, 6, 600),
    ("ssd-sr-05", "security_review", "remediation_priority",   "Remediation Priority",
     "Ordered remediation actions by risk level with patch/mitigation guidance and verification steps.", 5, 1, '["sop_reference","standard"]', 2, 8, 800),
    ("ssd-sr-06", "security_review", "sign_off",               "Sign-Off",
     "Reviewer attestations, conditions for approval, and re-assessment criteria.", 6, 1, '["form","checklist"]', 1, 3, 300),
]


def up(conn) -> None:
    conn.executescript(_DDL)

    for row in _SEED_SECTION_DEFS:
        (sid, rtype, skey, sname, desc, sort, req, hints, minc, maxc, maxw) = row
        conn.execute(
            """INSERT OR IGNORE INTO wf_report_section_defs
               (id, report_type, section_key, section_name, description,
                sort_order, required, source_hints, min_chunks, max_chunks, max_words)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, rtype, skey, sname, desc, sort, req, hints, minc, maxc, maxw),
        )

    try:
        conn.commit()
    except Exception:
        pass
