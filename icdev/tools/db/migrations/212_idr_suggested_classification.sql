-- Migration 212: Add AI classification suggestion columns to idr_sessions
-- Supports IDR item 9: LLM-suggested classification level + confidence tracking.
-- conflicts_resolved backfills the gate flag used by Stage 3 check.

ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS conflicts_resolved BOOLEAN DEFAULT FALSE;
ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS suggested_classification TEXT;
ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS suggested_classification_confidence REAL;
