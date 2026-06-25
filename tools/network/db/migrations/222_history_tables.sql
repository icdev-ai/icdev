-- Migration 222: Append-only history tables for remediation status and POAM tracking
-- Depends on: nc_remediation_actions (init_db.py)
-- advisory_id / assessment_id are soft references; hard FKs added by the advisory/assessment
-- migration once those tables land (nc_advisories, nc_assessments).

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Remediation status change log (append-only; never UPDATE/DELETE)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_remediation_status_log (
    id                       SERIAL PRIMARY KEY,
    action_id                INTEGER NOT NULL REFERENCES nc_remediation_actions(id) ON DELETE CASCADE,
    old_status               VARCHAR(40),
    new_status               VARCHAR(40) NOT NULL,
    note                     TEXT,
    updated_by               VARCHAR(200),
    nql_verification_run     TEXT,
    verification_result_hash VARCHAR(64),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_reme_status_log_action
    ON nc_remediation_status_log(action_id);

CREATE INDEX IF NOT EXISTS idx_nc_reme_status_log_created
    ON nc_remediation_status_log(created_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. POAM items (append-only; never UPDATE/DELETE)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_poam_items (
    id                       SERIAL PRIMARY KEY,
    poam_id                  VARCHAR(30) NOT NULL UNIQUE,   -- e.g. 'poam-2026-001'

    -- soft references (hard FKs added once advisory/assessment tables land)
    advisory_id              INTEGER,
    assessment_id            INTEGER,

    weakness_name            TEXT NOT NULL,
    weakness_description     TEXT,
    severity                 VARCHAR(20) NOT NULL DEFAULT 'medium'
                                 CHECK(severity IN ('critical','high','medium','low','informational')),
    detection_source         VARCHAR(200),

    scheduled_completion     DATE,
    milestone_changes        TEXT,

    status                   VARCHAR(30) NOT NULL DEFAULT 'open'
                                 CHECK(status IN ('open','in_progress','completed','risk_accepted','false_positive')),
    responsible_party        VARCHAR(200),
    resources_required       TEXT,
    estimated_cost           NUMERIC(12,2),
    raw_risk                 VARCHAR(20),
    residual_risk            VARCHAR(20),

    remediation_plan         TEXT,
    poam_notes               TEXT,

    closed_at                TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_poam_advisory
    ON nc_poam_items(advisory_id) WHERE advisory_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_nc_poam_status
    ON nc_poam_items(status);

CREATE INDEX IF NOT EXISTS idx_nc_poam_severity
    ON nc_poam_items(severity);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. POAM status change log (append-only; never UPDATE/DELETE)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_poam_status_log (
    id          SERIAL PRIMARY KEY,
    poam_id     INTEGER NOT NULL REFERENCES nc_poam_items(id) ON DELETE CASCADE,
    old_status  VARCHAR(30),
    new_status  VARCHAR(30) NOT NULL,
    note        TEXT,
    updated_by  VARCHAR(200),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_poam_status_log_poam
    ON nc_poam_status_log(poam_id);
CREATE INDEX IF NOT EXISTS idx_nc_poam_status_log_created
    ON nc_poam_status_log(created_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Exception registry (append-only; never UPDATE/DELETE)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_exceptions (
    id                       SERIAL PRIMARY KEY,
    advisory_id              INTEGER,            -- soft ref to nc_advisories
    remediation_action_id    INTEGER,            -- soft ref to nc_remediation_actions
    device_name              VARCHAR(200) NOT NULL,
    exception_type           VARCHAR(40) NOT NULL
                                 CHECK(exception_type IN (
                                     'operational_requirement','eol_hardware','vendor_delay',
                                     'risk_accepted','compensating_control')),
    justification            TEXT NOT NULL,
    compensating_controls    TEXT,
    risk_acceptance_level    VARCHAR(20) NOT NULL DEFAULT 'medium'
                                 CHECK(risk_acceptance_level IN ('critical','high','medium','low')),
    expiry_date              DATE,               -- null = indefinite (requires AO approval)
    filed_by                 VARCHAR(200),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_exceptions_advisory
    ON nc_exceptions(advisory_id) WHERE advisory_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_nc_exceptions_device
    ON nc_exceptions(device_name);
CREATE INDEX IF NOT EXISTS idx_nc_exceptions_expiry
    ON nc_exceptions(expiry_date) WHERE expiry_date IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Exception approval chain (append-only; never UPDATE/DELETE)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_exception_approvals (
    id             SERIAL PRIMARY KEY,
    exception_id   INTEGER NOT NULL REFERENCES nc_exceptions(id) ON DELETE CASCADE,
    approver       VARCHAR(200) NOT NULL,
    approver_role  VARCHAR(100) NOT NULL,   -- ISSO | ISSM | AO
    decision       VARCHAR(20) NOT NULL
                       CHECK(decision IN ('approved','denied','conditional')),
    conditions     TEXT,                    -- for conditional approvals
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_exception_approvals_exception
    ON nc_exception_approvals(exception_id);
CREATE INDEX IF NOT EXISTS idx_nc_exception_approvals_created
    ON nc_exception_approvals(created_at DESC);
