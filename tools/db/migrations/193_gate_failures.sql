-- CUI // SP-CTI
-- Migration 193: Create compliance_gates and gate_failures tables.
--
-- compliance_gates records lifecycle state for each compliance gate check
-- (triggered, pass/fail status, project association).
--
-- gate_failures records each per-criterion failure for a gate run, with
-- severity ranking so gate-report notifications can order by severity.
--
-- Both tables are referenced in
-- tools/notification_service/render_handler_service.py
-- ::render_and_notify_compliance_gate but lacked standalone migrations.
-- Resolves the orphan_db_table gap detected by tools/awareness/gap_detector.py.
--
-- Carries tenant_id + classification so all queries are RLS-aware via
-- tools.db.storage.get_connection().
--
-- Idempotent (IF NOT EXISTS).

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS compliance_gates (
    id              TEXT    PRIMARY KEY,
    gate_name       TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'pass', 'fail', 'error', 'skipped')),
    triggered_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    project_id      TEXT    NOT NULL DEFAULT '',
    notes           TEXT    NOT NULL DEFAULT '',
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_compliance_gates_project ON compliance_gates (project_id);
CREATE INDEX IF NOT EXISTS idx_compliance_gates_status  ON compliance_gates (status);
CREATE INDEX IF NOT EXISTS idx_compliance_gates_tenant  ON compliance_gates (tenant_id);

CREATE TABLE IF NOT EXISTS gate_failures (
    id              TEXT    PRIMARY KEY,
    gate_id         TEXT    NOT NULL REFERENCES compliance_gates(id) ON DELETE CASCADE,
    criterion       TEXT    NOT NULL DEFAULT '',
    detail          TEXT    NOT NULL DEFAULT '',
    severity        TEXT    NOT NULL DEFAULT 'warning'
                    CHECK (severity IN ('critical', 'high', 'warning', 'info')),
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gate_failures_gate     ON gate_failures (gate_id);
CREATE INDEX IF NOT EXISTS idx_gate_failures_severity ON gate_failures (severity);
CREATE INDEX IF NOT EXISTS idx_gate_failures_tenant   ON gate_failures (tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS compliance_gates (
    id              TEXT        PRIMARY KEY,
    gate_name       TEXT        NOT NULL DEFAULT '',
    status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'pass', 'fail', 'error', 'skipped')),
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    project_id      TEXT        NOT NULL DEFAULT '',
    notes           TEXT        NOT NULL DEFAULT '',
    tenant_id       TEXT        NOT NULL DEFAULT 'default',
    classification  TEXT        NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_compliance_gates_project ON compliance_gates (project_id);
CREATE INDEX IF NOT EXISTS idx_compliance_gates_status  ON compliance_gates (status);
CREATE INDEX IF NOT EXISTS idx_compliance_gates_tenant  ON compliance_gates (tenant_id);

CREATE TABLE IF NOT EXISTS gate_failures (
    id              TEXT        PRIMARY KEY,
    gate_id         TEXT        NOT NULL REFERENCES compliance_gates(id) ON DELETE CASCADE,
    criterion       TEXT        NOT NULL DEFAULT '',
    detail          TEXT        NOT NULL DEFAULT '',
    severity        TEXT        NOT NULL DEFAULT 'warning'
                    CHECK (severity IN ('critical', 'high', 'warning', 'info')),
    tenant_id       TEXT        NOT NULL DEFAULT 'default',
    classification  TEXT        NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gate_failures_gate     ON gate_failures (gate_id);
CREATE INDEX IF NOT EXISTS idx_gate_failures_severity ON gate_failures (severity);
CREATE INDEX IF NOT EXISTS idx_gate_failures_tenant   ON gate_failures (tenant_id);

-- @all
