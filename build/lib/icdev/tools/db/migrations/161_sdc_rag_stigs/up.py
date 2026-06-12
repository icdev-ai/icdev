# CUI // SP-CTI
"""Migration 161 — create sdc_rag_stigs and sdc_rag_attack_techniques tables.

Backing tables for the two new RAG source entries added in SDC-02:
  sdc_rag_stigs              — DISA STIG vulnerability rules ingested for RAG
  sdc_rag_attack_techniques  — MITRE ATT&CK Enterprise techniques (STIX 2.1)
"""

SQL = """
CREATE TABLE IF NOT EXISTS sdc_rag_stigs (
    vuln_id      TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    severity     TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    fix_text     TEXT NOT NULL DEFAULT '',
    nist_control TEXT NOT NULL DEFAULT '',
    benchmark    TEXT NOT NULL DEFAULT '',
    ingested_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sdc_rag_stigs_severity
    ON sdc_rag_stigs (severity);

CREATE INDEX IF NOT EXISTS idx_sdc_rag_stigs_nist
    ON sdc_rag_stigs (nist_control);

CREATE TABLE IF NOT EXISTS sdc_rag_attack_techniques (
    technique_id    TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    detection       TEXT NOT NULL DEFAULT '',
    mitigations     TEXT NOT NULL DEFAULT '',
    tactic          TEXT NOT NULL DEFAULT '',
    platform        TEXT NOT NULL DEFAULT '',
    is_subtechnique INTEGER NOT NULL DEFAULT 0,
    source_url      TEXT NOT NULL DEFAULT '',
    ingested_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sdc_rag_attack_tactic
    ON sdc_rag_attack_techniques (tactic);

CREATE INDEX IF NOT EXISTS idx_sdc_rag_attack_platform
    ON sdc_rag_attack_techniques (platform);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
