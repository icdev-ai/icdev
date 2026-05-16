-- Migration: 154_indicator_baselines
-- CUI // SP-CTI

-- Operator-configured threshold scores for indicators.
-- Supports per-project, per-tenant, platform, global, and user-level baselines.

CREATE TABLE IF NOT EXISTS indicator_baselines (
    id TEXT PRIMARY KEY,
    indicator_name TEXT NOT NULL,
    indicator_category TEXT DEFAULT 'general',
    scope TEXT NOT NULL DEFAULT 'project'
        CHECK(scope IN ('global', 'platform', 'tenant', 'project', 'user')),
    scope_id TEXT,
    threshold_score REAL NOT NULL,
    severity_band TEXT DEFAULT 'medium'
        CHECK(severity_band IN ('low', 'medium', 'high', 'critical')),
    operator_id TEXT NOT NULL,
    rationale TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_indicator_baselines_scope
    ON indicator_baselines(scope, scope_id, is_active);
CREATE INDEX IF NOT EXISTS idx_indicator_baselines_name
    ON indicator_baselines(indicator_name, is_active);
CREATE INDEX IF NOT EXISTS idx_indicator_baselines_operator
    ON indicator_baselines(operator_id, created_at);
