-- CUI // SP-CTI
-- Migration 281: Pulse publish-gate override audit (nav-intel-09, TRUST invariant)
-- APPEND-ONLY — never UPDATE or DELETE. One row per HITL force-override of the
-- LLM-judge publish gate (RED verdict / judge-not-run blocks publish; admin may
-- force with a reason). Registered in APPEND_ONLY_TABLES
-- (.claude/hooks/pre_tool_use.py). Mirrors idr_publish_audit (migration 276).

CREATE TABLE IF NOT EXISTS pulse_publish_audit (
    id            TEXT PRIMARY KEY,
    post_id       TEXT NOT NULL,
    action        TEXT NOT NULL DEFAULT 'force_publish',
    actor         TEXT,
    reason        TEXT,
    judge_verdict TEXT,
    source        TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pulse_publish_audit_post ON pulse_publish_audit(post_id);
