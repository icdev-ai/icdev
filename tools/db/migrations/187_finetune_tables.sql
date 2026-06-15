-- CUI // SP-CTI
-- Migration 187: Create finetune_jobs / finetune_metrics / finetune_eval_results tables.
--
-- These three tables back render_handler_service.py::render_and_notify_finetune_job_result
-- (aiify-opp-5917 render-notify chain for fine-tuning job completions).
-- They are distinct from the DIC-canvas dic_finetune_jobs table which tracks
-- in-canvas fine-tuning runs; these are platform-level notification tables.
--
-- Idempotent (IF NOT EXISTS). Carry tenant_id + classification for RLS.

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS finetune_jobs (
    id             TEXT    PRIMARY KEY,
    model_base     TEXT,
    status         TEXT    NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    epochs         INTEGER DEFAULT 0,
    started_at     TEXT,
    completed_at   TEXT,
    tenant_id      TEXT    NOT NULL DEFAULT 'default',
    classification TEXT    NOT NULL DEFAULT 'CUI',
    created_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_finetune_jobs_status ON finetune_jobs(status);
CREATE INDEX IF NOT EXISTS idx_finetune_jobs_tenant ON finetune_jobs(tenant_id);

CREATE TABLE IF NOT EXISTS finetune_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT    NOT NULL REFERENCES finetune_jobs(id),
    metric_name TEXT    NOT NULL,
    value       REAL,
    epoch       INTEGER DEFAULT 0,
    tenant_id   TEXT    NOT NULL DEFAULT 'default',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_finetune_metrics_job   ON finetune_metrics(job_id);
CREATE INDEX IF NOT EXISTS idx_finetune_metrics_epoch ON finetune_metrics(epoch);

CREATE TABLE IF NOT EXISTS finetune_eval_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT    NOT NULL REFERENCES finetune_jobs(id),
    benchmark_name  TEXT    NOT NULL,
    score           REAL,
    delta           REAL,
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_finetune_eval_job   ON finetune_eval_results(job_id);
CREATE INDEX IF NOT EXISTS idx_finetune_eval_score ON finetune_eval_results(score);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS finetune_jobs (
    id             TEXT    PRIMARY KEY,
    model_base     TEXT,
    status         TEXT    NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    epochs         INTEGER DEFAULT 0,
    started_at     TEXT,
    completed_at   TEXT,
    tenant_id      TEXT    NOT NULL DEFAULT 'default',
    classification TEXT    NOT NULL DEFAULT 'CUI',
    created_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_finetune_jobs_status ON finetune_jobs(status);
CREATE INDEX IF NOT EXISTS idx_finetune_jobs_tenant ON finetune_jobs(tenant_id);

CREATE TABLE IF NOT EXISTS finetune_metrics (
    id             BIGSERIAL PRIMARY KEY,
    job_id         TEXT    NOT NULL REFERENCES finetune_jobs(id),
    metric_name    TEXT    NOT NULL,
    value          REAL,
    epoch          INTEGER DEFAULT 0,
    tenant_id      TEXT    NOT NULL DEFAULT 'default',
    classification TEXT    NOT NULL DEFAULT 'CUI',
    created_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_finetune_metrics_job   ON finetune_metrics(job_id);
CREATE INDEX IF NOT EXISTS idx_finetune_metrics_epoch ON finetune_metrics(epoch);

CREATE TABLE IF NOT EXISTS finetune_eval_results (
    id             BIGSERIAL PRIMARY KEY,
    job_id         TEXT    NOT NULL REFERENCES finetune_jobs(id),
    benchmark_name TEXT    NOT NULL,
    score          REAL,
    delta          REAL,
    tenant_id      TEXT    NOT NULL DEFAULT 'default',
    classification TEXT    NOT NULL DEFAULT 'CUI',
    created_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_finetune_eval_job   ON finetune_eval_results(job_id);
CREATE INDEX IF NOT EXISTS idx_finetune_eval_score ON finetune_eval_results(score);

-- @all
