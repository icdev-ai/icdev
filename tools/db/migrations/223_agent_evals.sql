-- CUI // SP-CTI
-- Migration 223: Agent evaluation results.
-- Stores rule-based and LLM-graded quality metrics for agent_loop_sessions.

CREATE TABLE IF NOT EXISTS agent_evals (
    id                          TEXT PRIMARY KEY,
    session_id                  TEXT NOT NULL,
    outcome                     TEXT NOT NULL DEFAULT '',
    done                        INTEGER NOT NULL DEFAULT 0,
    turns_used                  INTEGER NOT NULL DEFAULT 0,
    efficiency_score            REAL NOT NULL DEFAULT 0.0,
    total_tool_calls            INTEGER NOT NULL DEFAULT 0,
    error_tool_calls            INTEGER NOT NULL DEFAULT 0,
    tool_error_rate             REAL NOT NULL DEFAULT 0.0,
    unique_tools_json           TEXT NOT NULL DEFAULT '[]',
    tool_precision              REAL NOT NULL DEFAULT 0.0,
    total_cost_usd              REAL NOT NULL DEFAULT 0.0,
    total_input_tokens          INTEGER NOT NULL DEFAULT 0,
    total_output_tokens         INTEGER NOT NULL DEFAULT 0,
    reasoning_coverage          REAL NOT NULL DEFAULT 0.0,
    avg_reasoning_chars         REAL NOT NULL DEFAULT 0.0,
    has_error_recovery_reasoning INTEGER NOT NULL DEFAULT 0,
    plan_stated                 INTEGER NOT NULL DEFAULT 0,
    scope_violations            INTEGER NOT NULL DEFAULT 0,
    trust_denials               INTEGER NOT NULL DEFAULT 0,
    llm_grade_json              TEXT,
    reasoning_style             TEXT NOT NULL DEFAULT '',
    graded_at                   TEXT NOT NULL DEFAULT (datetime('now')),
    grading_version             TEXT NOT NULL DEFAULT '1.0'
);

CREATE INDEX IF NOT EXISTS idx_agent_evals_session
    ON agent_evals (session_id);

CREATE INDEX IF NOT EXISTS idx_agent_evals_outcome
    ON agent_evals (outcome, graded_at);
