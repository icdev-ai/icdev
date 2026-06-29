-- CUI // SP-CTI
-- Migration 221: PVM — Predictive Vulnerability Management
-- Requires migration 220 (nc_advisories, nc_advisory_assessments, nc_nqe_audit_log)
--
-- APPEND-ONLY tables: nc_vuln_predictions, nc_patch_plans
-- Mutable tables:     nc_attack_surface, nc_triage_queue, nc_maintenance_windows

-- ─────────────────────────────────────────────────────────────────────────────
-- Expand nc_nqe_audit_log action CHECK to include PVM actions (PostgreSQL only)
-- SQLite: constraint expanded in init_db.py CREATE TABLE for fresh installs
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE nc_nqe_audit_log DROP CONSTRAINT IF EXISTS nc_nqe_audit_log_action_check;
ALTER TABLE nc_nqe_audit_log ADD CONSTRAINT nc_nqe_audit_log_action_check
    CHECK(action IN (
        'translate','explain','run','assess','approve','export','upload','simulate',
        'predict','triage_score','triage_approve','plan_create'
    ));

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. nc_vuln_predictions  (APPEND-ONLY — never UPDATE/DELETE per NIST AU)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_vuln_predictions (
    id                      SERIAL PRIMARY KEY,
    advisory_id             INTEGER NOT NULL,
    assessment_id           INTEGER,            -- soft ref to nc_advisory_assessments (no FK — init ordering)
    risk_score_composite    FLOAT NOT NULL CHECK(risk_score_composite BETWEEN 0.0 AND 1.0),
    risk_score_30d          FLOAT NOT NULL CHECK(risk_score_30d BETWEEN 0.0 AND 1.0),
    risk_score_90d          FLOAT NOT NULL CHECK(risk_score_90d BETWEEN 0.0 AND 1.0),
    trend                   VARCHAR(20) NOT NULL CHECK(trend IN ('rising','stable','declining')),
    confidence              FLOAT NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
    cvss_base               FLOAT,
    exploit_weight          FLOAT,
    patch_lag_norm          FLOAT,
    impacted_trend          FLOAT,
    model_version           VARCHAR(20) NOT NULL DEFAULT '1.0',
    predicted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_vuln_predictions_advisory
    ON nc_vuln_predictions(advisory_id);
CREATE INDEX IF NOT EXISTS idx_nc_vuln_predictions_predicted_at
    ON nc_vuln_predictions(predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_vuln_predictions_risk
    ON nc_vuln_predictions(risk_score_composite DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. nc_attack_surface  (mutable — refreshed per NQE run, UPSERT on device+cve)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_attack_surface (
    id              SERIAL PRIMARY KEY,
    device_id       VARCHAR(200),
    device_name     VARCHAR(200) NOT NULL,
    ip              VARCHAR(45),
    cve_id          VARCHAR(50) NOT NULL,
    advisory_id     INTEGER,
    exposure_type   VARCHAR(20) NOT NULL DEFAULT 'unknown'
                        CHECK(exposure_type IN ('network','local','combined','unknown')),
    reachable       BOOLEAN NOT NULL DEFAULT FALSE,
    criticality     SMALLINT NOT NULL DEFAULT 3 CHECK(criticality BETWEEN 1 AND 5),
    surface_score   FLOAT NOT NULL CHECK(surface_score BETWEEN 0.0 AND 1.0),
    nqe_source      VARCHAR(30) NOT NULL DEFAULT 'local_mapping'
                        CHECK(nqe_source IN ('nqe_api','local_mapping','local_heuristic','llm_translation')),
    nessus_scan_id  INTEGER,
    assessed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_attack_surface_device  ON nc_attack_surface(device_name);
CREATE INDEX IF NOT EXISTS idx_nc_attack_surface_cve     ON nc_attack_surface(cve_id);
CREATE INDEX IF NOT EXISTS idx_nc_attack_surface_score   ON nc_attack_surface(surface_score DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nc_attack_surface_device_cve
    ON nc_attack_surface(device_name, cve_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. nc_triage_queue  (mutable — status transitions allowed)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_triage_queue (
    id                      SERIAL PRIMARY KEY,
    advisory_id             INTEGER NOT NULL,
    priority_score          FLOAT NOT NULL CHECK(priority_score BETWEEN 0.0 AND 1.0),
    kev_exploited           BOOLEAN NOT NULL DEFAULT FALSE,
    asset_criticality_norm  FLOAT,
    network_exposure_norm   FLOAT,
    temporal_urgency        FLOAT,
    rank                    INTEGER,
    rationale_json          JSONB,
    status                  VARCHAR(20) NOT NULL DEFAULT 'pending'
                                CHECK(status IN ('pending','approved','deferred','scheduled')),
    auto_approved           BOOLEAN NOT NULL DEFAULT FALSE,
    approved_by             VARCHAR(200),
    approved_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_triage_queue_advisory  ON nc_triage_queue(advisory_id);
CREATE INDEX IF NOT EXISTS idx_nc_triage_queue_status    ON nc_triage_queue(status);
CREATE INDEX IF NOT EXISTS idx_nc_triage_queue_priority  ON nc_triage_queue(priority_score DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nc_triage_queue_advisory_unique
    ON nc_triage_queue(advisory_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. nc_maintenance_windows  (mutable — operator configured)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_maintenance_windows (
    id                  SERIAL PRIMARY KEY,
    site                VARCHAR(200) NOT NULL,
    label               VARCHAR(200),
    start_utc           TIMESTAMPTZ NOT NULL,
    end_utc             TIMESTAMPTZ NOT NULL,
    recurrence          VARCHAR(20) CHECK(recurrence IN ('none','weekly','biweekly','monthly')),
    blackout_days_json  JSONB DEFAULT '[]',     -- days of week [0=Mon..6=Sun] to skip
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_maintenance_windows_site   ON nc_maintenance_windows(site);
CREATE INDEX IF NOT EXISTS idx_nc_maintenance_windows_active ON nc_maintenance_windows(active);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. nc_patch_plans  (APPEND-ONLY — immutable once created per NIST AU)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_patch_plans (
    id                      SERIAL PRIMARY KEY,
    plan_id                 VARCHAR(100) NOT NULL,
    batch_id                VARCHAR(100) NOT NULL,
    advisory_id             INTEGER,
    device_name             VARCHAR(200) NOT NULL,
    action                  VARCHAR(100) NOT NULL,
    scheduled_at            TIMESTAMPTZ,
    maintenance_window_id   INTEGER,
    blast_radius_json       JSONB,
    simulation_status       VARCHAR(20) DEFAULT 'pending'
                                CHECK(simulation_status IN ('pending','pass','warn','fail','skipped')),
    risk_reduction          FLOAT,
    approved_by             VARCHAR(200),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_nc_patch_plans_plan_id   ON nc_patch_plans(plan_id);
CREATE INDEX IF NOT EXISTS idx_nc_patch_plans_advisory  ON nc_patch_plans(advisory_id);
CREATE INDEX IF NOT EXISTS idx_nc_patch_plans_scheduled ON nc_patch_plans(scheduled_at);
