-- Migration 212: Add AI classification suggestion + Stage 0 extraction columns to idr_sessions
-- Supports IDR item 9: LLM-suggested classification level + confidence tracking.
-- Supports IDR item 7: prior_docs_context for Stage 0 LLM extraction results.
-- conflicts_resolved backfills the gate flag used by Stage 3 check.

ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS conflicts_resolved BOOLEAN DEFAULT FALSE;
ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS suggested_classification TEXT;
ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS suggested_classification_confidence REAL;
ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS prior_docs_context TEXT;
ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS last_source_hash TEXT;
ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS source_hash_checked_at TIMESTAMP;
