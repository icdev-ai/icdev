-- Migration: 20260802200931_agent_approval_log
-- CUI // SP-CTI
--
-- ars-appr-01 — append-only record of every approval-gate decision made for an
-- agent-loop tool call. One row per DECISION, not per tool call: reversible calls
-- execute without a decision and are already covered by the loop's tool_call_log.
--
-- Append-only, NIST 800-53 AU. Registered in APPEND_ONLY_TABLES in
-- .claude/hooks/pre_tool_use.py — never UPDATE or DELETE a row here.
--
-- actor and reason are NOT NULL by design: the whole point of the trail is WHO
-- decided and WHY. The recorder substitutes system:unattributed rather than
-- writing a null.

CREATE TABLE IF NOT EXISTS agent_approval_log (
    id                  TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    session_id          TEXT,
    trace_id            TEXT,
    tool_name           TEXT NOT NULL,
    tool_input_preview  TEXT,
    reversibility       TEXT NOT NULL,
    rule_id             TEXT,
    decision            TEXT NOT NULL,
    actor               TEXT NOT NULL,
    reason              TEXT NOT NULL,
    approval_mode       TEXT,
    classification      TEXT DEFAULT 'CUI',
    metadata            TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_approval_created
    ON agent_approval_log (created_at);

CREATE INDEX IF NOT EXISTS idx_agent_approval_session
    ON agent_approval_log (session_id);

CREATE INDEX IF NOT EXISTS idx_agent_approval_decision
    ON agent_approval_log (decision, reversibility);

CREATE INDEX IF NOT EXISTS idx_agent_approval_tool
    ON agent_approval_log (tool_name);
