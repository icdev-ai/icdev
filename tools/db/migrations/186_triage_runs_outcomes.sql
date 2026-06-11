-- CUI // SP-CTI
-- Migration 186: triage_runs + triage_outcomes (append-only audit tables)
-- ARC Phase A — one row per triage_once cycle + one row per diagnosis/apply.
-- 'held' is resolved as a NEW resolution row (never UPDATE an audit row).

-- @sqlite-only
CREATE TABLE IF NOT EXISTS triage_runs (
    id              TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    scanned         INTEGER NOT NULL DEFAULT 0,
    applied         INTEGER NOT NULL DEFAULT 0,
    suggested       INTEGER NOT NULL DEFAULT 0,
    autofix_enabled INTEGER NOT NULL DEFAULT 0,
    trace_id        TEXT,
    created_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_triage_runs_started ON triage_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_triage_runs_trace ON triage_runs(trace_id);

CREATE TABLE IF NOT EXISTS triage_outcomes (
    id                          TEXT PRIMARY KEY,
    run_id                      TEXT NOT NULL REFERENCES triage_runs(id) ON DELETE CASCADE,
    task_id                     TEXT NOT NULL,
    signature                   TEXT NOT NULL,
    signature_class             TEXT,
    task_type                   TEXT,
    recommendation              TEXT,
    confidence_raw              REAL,
    confidence_selfconsistency  REAL,
    gate_decision               TEXT,
    applied                     INTEGER NOT NULL DEFAULT 0,
    verify_rc                   INTEGER,
    autofix_branch              TEXT,
    autofix_commit              TEXT,
    merged                      INTEGER NOT NULL DEFAULT 0,
    held                        TEXT,
    resolution_of               TEXT,
    created_at                  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_triage_outcomes_run ON triage_outcomes(run_id);
CREATE INDEX IF NOT EXISTS idx_triage_outcomes_task ON triage_outcomes(task_id);
CREATE INDEX IF NOT EXISTS idx_triage_outcomes_signature ON triage_outcomes(signature);
CREATE INDEX IF NOT EXISTS idx_triage_outcomes_created ON triage_outcomes(created_at);
CREATE INDEX IF NOT EXISTS idx_triage_outcomes_resolution_of ON triage_outcomes(resolution_of);

-- @pg-only
CREATE TABLE IF NOT EXISTS triage_runs (
    id              TEXT PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    scanned         INTEGER NOT NULL DEFAULT 0,
    applied         INTEGER NOT NULL DEFAULT 0,
    suggested       INTEGER NOT NULL DEFAULT 0,
    autofix_enabled INTEGER NOT NULL DEFAULT 0,
    trace_id        TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_triage_runs_started ON triage_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_triage_runs_trace ON triage_runs(trace_id);

CREATE TABLE IF NOT EXISTS triage_outcomes (
    id                          TEXT PRIMARY KEY,
    run_id                      TEXT NOT NULL REFERENCES triage_runs(id) ON DELETE CASCADE,
    task_id                     TEXT NOT NULL,
    signature                   TEXT NOT NULL,
    signature_class             TEXT,
    task_type                   TEXT,
    recommendation              TEXT,
    confidence_raw              REAL,
    confidence_selfconsistency  REAL,
    gate_decision               TEXT,
    applied                     INTEGER NOT NULL DEFAULT 0,
    verify_rc                   INTEGER,
    autofix_branch              TEXT,
    autofix_commit              TEXT,
    merged                      INTEGER NOT NULL DEFAULT 0,
    held                        TEXT,
    resolution_of               TEXT,
    created_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_triage_outcomes_run ON triage_outcomes(run_id);
CREATE INDEX IF NOT EXISTS idx_triage_outcomes_task ON triage_outcomes(task_id);
CREATE INDEX IF NOT EXISTS idx_triage_outcomes_signature ON triage_outcomes(signature);
CREATE INDEX IF NOT EXISTS idx_triage_outcomes_resolution_of ON triage_outcomes(resolution_of);
CREATE INDEX IF NOT EXISTS idx_triage_outcomes_created ON triage_outcomes(created_at);
