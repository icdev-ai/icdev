"""Migration 139 — FISMA IR-6 incident tracking tables.

Creates four tables to support automated Splunk SIEM detection with DISA
threat signatures, ISSO/US-CERT notification SLA tracking, and annual
tabletop exercise records per FISMA IR-6.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS disa_threat_signatures (
    id          TEXT PRIMARY KEY,
    sig_id      TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK(severity IN ('critical','high','medium','low')),
    mitre_tactic TEXT,
    mitre_technique TEXT,
    splunk_query TEXT,
    ioc_pattern TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_disa_sig_severity
    ON disa_threat_signatures (severity, active);

CREATE TABLE IF NOT EXISTS fisma_ir_incidents (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    sig_id          TEXT,
    title           TEXT NOT NULL,
    severity        TEXT NOT NULL CHECK(severity IN ('p1','p2','p3','p4')),
    status          TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','investigating','contained','eradicated','recovered','closed')),
    detection_source TEXT NOT NULL DEFAULT 'splunk',
    raw_alert_json  TEXT,
    isso_notified_at TEXT,
    isso_sla_met    INTEGER,
    uscert_notified_at TEXT,
    uscert_sla_met  INTEGER,
    emass_tracking_id TEXT,
    emass_synced_at TEXT,
    root_cause      TEXT,
    lessons_learned TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_fisma_ir_project
    ON fisma_ir_incidents (project_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fisma_ir_severity
    ON fisma_ir_incidents (severity, status);

CREATE TABLE IF NOT EXISTS ir_notifications (
    id              TEXT PRIMARY KEY,
    incident_id     TEXT NOT NULL,
    recipient_type  TEXT NOT NULL CHECK(recipient_type IN ('isso','uscert','ao','ciso','us_cert')),
    channel         TEXT NOT NULL DEFAULT 'email',
    sent_at         TEXT NOT NULL DEFAULT (datetime('now')),
    sla_deadline_at TEXT,
    sla_met         INTEGER,
    message_summary TEXT,
    notified_by     TEXT
);

CREATE INDEX IF NOT EXISTS idx_ir_notif_incident
    ON ir_notifications (incident_id, recipient_type);

CREATE TABLE IF NOT EXISTS ir_tabletop_exercises (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    exercise_date   TEXT NOT NULL,
    scenario        TEXT NOT NULL,
    participants    TEXT,
    facilitator     TEXT,
    findings_json   TEXT,
    action_items_json TEXT,
    next_exercise_due TEXT,
    completed       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ttx_project
    ON ir_tabletop_exercises (project_id, exercise_date DESC);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
