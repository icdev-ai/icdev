-- Migration 234: objective progress auto-tracking
-- Adds auto-derived progress fields to user_objectives

ALTER TABLE user_objectives ADD COLUMN IF NOT EXISTS progress_notes TEXT DEFAULT '[]';
ALTER TABLE user_objectives ADD COLUMN IF NOT EXISTS last_auto_update TEXT;
ALTER TABLE user_objectives ADD COLUMN IF NOT EXISTS auto_progress_pct INTEGER DEFAULT 0;
