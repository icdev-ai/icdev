-- CUI // SP-CTI
-- Migration 224: Eval suite run history.
-- Each row is one named eval case result within a suite run.

CREATE TABLE IF NOT EXISTS agent_eval_runs (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    eval_name       TEXT NOT NULL,
    session_id      TEXT,
    passed          INTEGER,
    expected_outcome TEXT,
    actual_outcome  TEXT,
    expected_tools_json TEXT,
    actual_tools_json   TEXT,
    eval_id         TEXT,
    run_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_aer_run_id
    ON agent_eval_runs (run_id, run_at);
