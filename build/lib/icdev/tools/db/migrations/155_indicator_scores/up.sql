-- Migration: 155_indicator_scores
-- CUI // SP-CTI

-- Observed indicator scores with snapshotted evaluation results.
-- Each row records a single score observation, the baseline it was evaluated
-- against, and whether it triggered a threshold breach at that moment.

CREATE TABLE IF NOT EXISTS indicator_scores (
    id TEXT PRIMARY KEY,
    indicator_name TEXT NOT NULL,
    indicator_category TEXT DEFAULT 'general',
    scope TEXT NOT NULL DEFAULT 'project'
        CHECK(scope IN ('global', 'platform', 'tenant', 'project', 'user')),
    scope_id TEXT,
    score REAL NOT NULL
        CHECK(score >= 0),
    score_type TEXT DEFAULT 'raw'
        CHECK(score_type IN ('raw', 'normalized', 'aggregated')),
    source TEXT,
    operator_id TEXT,
    baseline_id TEXT,
    exceeded INTEGER,
    delta REAL,
    severity_at_time TEXT
        CHECK(severity_at_time IN ('low', 'medium', 'high', 'critical')),
    evaluated_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_indicator_scores_name
    ON indicator_scores(indicator_name, created_at);
CREATE INDEX IF NOT EXISTS idx_indicator_scores_scope
    ON indicator_scores(scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_indicator_scores_baseline
    ON indicator_scores(baseline_id);
CREATE INDEX IF NOT EXISTS idx_indicator_scores_exceeded
    ON indicator_scores(exceeded, created_at)
    WHERE exceeded = 1;
