-- CUI // SP-CTI
-- Migration 169: Add trace_id to kanban_tasks and reflex observer tables
-- Phase ai-trace ep1 (D-TRACE)
-- Enables cross-referencing kanban work items with OTel distributed traces.

ALTER TABLE kanban_tasks ADD COLUMN trace_id TEXT DEFAULT NULL;
ALTER TABLE kanban_tasks ADD COLUMN span_id  TEXT DEFAULT NULL;

-- Genesis reflex observer (if table exists — non-fatal if absent)
-- SQLite does not support conditional ALTER TABLE; wrap in application layer.
-- PostgreSQL: uncomment below lines when running on PG backend.
-- ALTER TABLE reflex_observations ADD COLUMN trace_id TEXT DEFAULT NULL;
-- ALTER TABLE reflex_observations ADD COLUMN span_id  TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_kanban_trace_id
    ON kanban_tasks (trace_id)
    WHERE trace_id IS NOT NULL;
