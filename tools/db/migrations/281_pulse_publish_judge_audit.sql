-- CUI // SP-CTI
-- Migration 281: Pulse publish-gate override audit (nav-intel-09, product decision "block red")
--
-- The Pulse blog's LLM judge maps each post to a FAR 15.305 color rating stored
-- on pulse_posts.judge_color. A RED verdict now HARD-BLOCKS publishing (the
-- verdict was previously advisory only). An admin may force-publish over the
-- block with a documented reason; every such override writes one immutable row
-- here (NIST AU).
--
-- APPEND-ONLY — never UPDATE or DELETE. Registered in APPEND_ONLY_TABLES
-- (.claude/hooks/pre_tool_use.py). Additive only; PG-authored, CREATE ... IF NOT
-- EXISTS follows the migration-276 idr_publish_audit precedent.

CREATE TABLE IF NOT EXISTS pulse_publish_audit (
    id          TEXT PRIMARY KEY,
    post_id     TEXT NOT NULL,
    action      TEXT NOT NULL,       -- 'force_publish'
    verdict     TEXT,                -- judge_color at override time ('red' or 'absent')
    reviewer    TEXT,                -- admin who authorized the override
    reason      TEXT NOT NULL,       -- documented force_reason
    tenant_id   TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pulse_publish_audit_post
    ON pulse_publish_audit(post_id);
