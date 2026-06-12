-- CUI // SP-CTI
-- Migration 112: AADC Phase 10 — Design Review, Lifecycle & Monitoring
-- Tables: aadc_lifecycle_states, aadc_review_comments

CREATE TABLE IF NOT EXISTS aadc_lifecycle_states (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    from_state      TEXT NOT NULL DEFAULT 'DRAFT',
    to_state        TEXT NOT NULL,
    actor           TEXT DEFAULT '',
    reason          TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_lifecycle_design ON aadc_lifecycle_states(design_id);

CREATE TABLE IF NOT EXISTS aadc_review_comments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    reviewer        TEXT NOT NULL DEFAULT '',
    comment_type    TEXT NOT NULL DEFAULT 'COMMENT',
    body            TEXT DEFAULT '',
    node_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_review_design ON aadc_review_comments(design_id);
