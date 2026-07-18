-- CUI // SP-CTI
-- Migration 274: IDR publish-gate override audit (cnr-doc-01, TRUST invariant)
-- APPEND-ONLY — never UPDATE or DELETE. One row per HITL force-override of the
-- citation_guard / placeholder_guard export gate. Registered in
-- APPEND_ONLY_TABLES (.claude/hooks/pre_tool_use.py).

CREATE TABLE IF NOT EXISTS idr_publish_audit (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    gate        TEXT NOT NULL
                    CHECK (gate IN ('citation_guard','placeholder_guard')),
    reviewer    TEXT,
    findings    TEXT,                    -- JSON array of the overridden defects
    tenant_id   TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_idr_publish_audit_session ON idr_publish_audit(session_id);
