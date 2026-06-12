#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 011: Oracle remediation proposals + finding approvals (RESTORED).

The original 011 migration files were lost from disk but the migration is
recorded in schema_migrations as applied (2026-04-04). This file restores
the migration as fully idempotent so a fresh checkout reproduces the same
schema.

Tables created (all append-only, registered in pre_tool_use.py):
  - oracle_predictions          — cross-canvas predictions from oracle lenses
  - oracle_remediation_proposals — proposed fixes with approval workflow
  - oracle_convergence_events    — when multiple lenses agree
  - finding_approvals            — canvas finding approval/triage workflow
"""

import sqlite3

MIGRATION_ID = "011"
MIGRATION_NAME = "oracle_remediation_proposals"
DESCRIPTION = "Oracle observability + finding approval tables"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS oracle_predictions (
    id              TEXT PRIMARY KEY,
    lens_id         TEXT NOT NULL,
    lens_name       TEXT NOT NULL,
    subject_type    TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    prediction_type TEXT NOT NULL,
    prediction_text TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 0.0,
    severity        TEXT NOT NULL DEFAULT 'medium',
    horizon_days    INTEGER NOT NULL DEFAULT 7,
    evidence_json   TEXT DEFAULT '{}',
    scoring_weights TEXT DEFAULT '{}',
    outcome         TEXT,
    outcome_at      TEXT,
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oracle_remediation_proposals (
    id                   TEXT PRIMARY KEY,
    source_lens          TEXT NOT NULL,
    source_prediction_id TEXT NOT NULL DEFAULT '',
    subject_type         TEXT NOT NULL
        CHECK(subject_type IN ('canvas','tool','pipeline','system')),
    subject_id           TEXT NOT NULL,
    title                TEXT NOT NULL,
    severity             TEXT NOT NULL DEFAULT 'medium'
        CHECK(severity IN ('critical','high','medium','low')),
    current_score        REAL,
    projected_score      REAL,
    proposal_json        TEXT NOT NULL DEFAULT '[]',
    impact_summary       TEXT NOT NULL DEFAULT '',
    status               TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','rejected','executed','failed','expired')),
    reviewed_by          TEXT NOT NULL DEFAULT '',
    reviewed_at          TEXT NOT NULL DEFAULT '',
    review_notes         TEXT NOT NULL DEFAULT '',
    execution_result     TEXT NOT NULL DEFAULT '{}',
    executed_at          TEXT NOT NULL DEFAULT '',
    expires_at           TEXT NOT NULL DEFAULT '',
    classification       TEXT NOT NULL DEFAULT 'CUI',
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS oracle_convergence_events (
    id                  TEXT PRIMARY KEY,
    convergence_type    TEXT NOT NULL,
    subject_type        TEXT NOT NULL,
    subject_id          TEXT NOT NULL,
    lens_count          INTEGER NOT NULL DEFAULT 2,
    consensus_score     REAL NOT NULL DEFAULT 0.0,
    severity            TEXT NOT NULL DEFAULT 'medium',
    summary             TEXT NOT NULL,
    recommended_action  TEXT,
    action_taken        INTEGER NOT NULL DEFAULT 0,
    resolved_at         TEXT,
    classification      TEXT NOT NULL DEFAULT 'CUI',
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding_approvals (
    finding_hash TEXT PRIMARY KEY,
    canvas_source TEXT NOT NULL,
    rule_id TEXT,
    severity TEXT,
    title TEXT NOT NULL,
    affected_entity TEXT,
    decision TEXT DEFAULT 'pending'
        CHECK(decision IN ('pending', 'approved', 'declined', 'accepted_risk', 'remediated')),
    decision_by TEXT,
    decision_at TIMESTAMP,
    decision_rationale TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_oracle_pred_subject ON oracle_predictions(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_oracle_pred_lens ON oracle_predictions(lens_id);
CREATE INDEX IF NOT EXISTS idx_oracle_remed_status ON oracle_remediation_proposals(status);
CREATE INDEX IF NOT EXISTS idx_oracle_remed_subject ON oracle_remediation_proposals(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_oracle_conv_subject ON oracle_convergence_events(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_finding_approvals_decision ON finding_approvals(decision);
CREATE INDEX IF NOT EXISTS idx_finding_approvals_canvas ON finding_approvals(canvas_source);
"""


def up(conn: sqlite3.Connection) -> dict:
    conn.executescript(_SCHEMA)
    conn.commit()
    return {
        "status": "applied",
        "tables_ensured": [
            "oracle_predictions",
            "oracle_remediation_proposals",
            "oracle_convergence_events",
            "finding_approvals",
        ],
    }
