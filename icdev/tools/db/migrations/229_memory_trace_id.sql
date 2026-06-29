-- CUI // SP-CTI
-- Migration 229: add trace_id correlation column to memory_entries
-- Links memory events to OTel spans / agent_loop sessions for end-to-end
-- LLM Ops traceability (Phase C-2 — correlation ID threading).

ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS trace_id TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_memory_trace_id
    ON memory_entries (trace_id) WHERE trace_id IS NOT NULL;
