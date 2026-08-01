-- 303_studio_run_memory.sql
-- DWO / dwo-mem-01 — run-scoped memory store for Studio workflow steps.
--
-- Steps used to communicate only through stdout JSON scraped by the next
-- executor.  This table is the single shared state for a run: one row per
-- (run_id, key), value stored as JSON text and parsed in Python (never with
-- SQLite JSON functions — see the CLAUDE.md PG portability rule).
--
-- Reserved keys:
--   _inputs  — run inputs supplied at trigger time (dwo-evt-04)

CREATE TABLE IF NOT EXISTS studio_run_memory (
    run_id     TEXT NOT NULL,
    key        TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, key)
);

CREATE INDEX IF NOT EXISTS idx_studio_run_memory_run ON studio_run_memory(run_id);
