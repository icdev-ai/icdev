-- CUI // SP-CTI
-- Migration 219: Forecast Jobs + Audit
-- Adds forecast_jobs (mutable job state) and forecast_audit (append-only NIST AU)
-- for TimesFM-backed time-series forecasting microservice.

CREATE TABLE IF NOT EXISTS forecast_jobs (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT 'manual',
    context         TEXT DEFAULT '',
    input_rows      INTEGER NOT NULL,
    input_summary   JSONB DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending',
    prediction      JSONB DEFAULT '{}',
    model_id        TEXT DEFAULT 'timesfm-2.5-200m',
    error_message   TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    classification  TEXT DEFAULT 'CUI',
    tenant_id       TEXT
);

CREATE INDEX IF NOT EXISTS idx_forecast_jobs_status ON forecast_jobs(status);
CREATE INDEX IF NOT EXISTS idx_forecast_jobs_tenant_id ON forecast_jobs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_forecast_jobs_created_at ON forecast_jobs(created_at);

CREATE TABLE IF NOT EXISTS forecast_audit (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES forecast_jobs(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,
    actor           TEXT DEFAULT 'system',
    details         JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    classification  TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_forecast_audit_job_id ON forecast_audit(job_id);
CREATE INDEX IF NOT EXISTS idx_forecast_audit_event_type ON forecast_audit(event_type);
