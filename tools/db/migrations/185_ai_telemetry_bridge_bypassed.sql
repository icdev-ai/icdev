-- Migration 185: Add bridge_bypassed column to ai_telemetry
-- Tracks when the CLI bridge was explicitly bypassed via per-page toggle.

-- SQLite
ALTER TABLE ai_telemetry ADD COLUMN bridge_bypassed INTEGER DEFAULT 0;

-- PostgreSQL (idempotent when run via migration runner that skips already-present columns)
-- ALTER TABLE ai_telemetry ADD COLUMN IF NOT EXISTS bridge_bypassed INTEGER DEFAULT 0;
