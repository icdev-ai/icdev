-- Migration 283: SOAR-lite response playbooks — run state + append-only audit.
-- CUI // SP-CTI
--
-- Card crx-sec-02. Backing store for the SOAR-lite composition engine at
-- tools/security_canvas/soar_lite.py. A playbook maps a real alert/finding type
-- (CVE triage SLA breach, insider-risk UBA anomaly, secrets-detection hit) to an
-- ordered list of steps; enrichment steps auto-run, destructive steps block on
-- HITL approval before execution.
--
-- soar_playbook_runs   — MUTABLE run state (status, current_step_index).
-- soar_playbook_audit  — APPEND-ONLY event log (registered in
--                        .claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES; NIST AU).
--
-- Both tables carry tenant_id + classification for row-level security. DDL is
-- idempotent (CREATE ... IF NOT EXISTS) and mirrors the runtime _ensure_tables in
-- the engine, so running either first is safe. On PostgreSQL the runner translates
-- INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL.

CREATE TABLE IF NOT EXISTS soar_playbook_runs (
    run_id             TEXT PRIMARY KEY,
    playbook_id        TEXT NOT NULL,
    finding_type       TEXT,
    entity             TEXT,
    severity           TEXT,
    status             TEXT NOT NULL DEFAULT 'running',
    current_step_index INTEGER NOT NULL DEFAULT 0,
    context_json       TEXT DEFAULT '{}',
    results_json       TEXT DEFAULT '[]',
    actor              TEXT,
    tenant_id          TEXT,
    classification     TEXT DEFAULT 'CUI',
    created_at         TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS soar_playbook_audit (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    playbook_id    TEXT,
    step_id        TEXT,
    action         TEXT,
    kind           TEXT,
    status         TEXT NOT NULL,
    detail         TEXT,
    actor          TEXT,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_soar_audit_run ON soar_playbook_audit(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_soar_runs_status ON soar_playbook_runs(status, created_at);
