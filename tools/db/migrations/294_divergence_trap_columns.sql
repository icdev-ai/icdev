-- CUI // SP-CTI
-- Migration 294: trap-detection columns on divergence_idea_scores (dvg-critic-02).
--
-- The divergence critic now also flags SEDUCTIVE-BUT-BROKEN ideas (attractive
-- but failing for a non-obvious reason) with a MANDATORY written rationale. An
-- unexplained trap flag is demoted to non-actionable in Python (compose_trap),
-- so is_trap is only ever true when trap_rationale is present. Advisory input to
-- existing gates, not an autonomous blocker (until dvg-bench-01 measures it).
--
-- Additive, nullable/defaulted columns (mirrors migration 293 conventions);
-- ADD COLUMN IF NOT EXISTS is PG-native. On the SQLite init/test fallback the
-- runtime tolerates the columns' absence (persistence is best-effort) and the
-- conftest schema carries them directly.

-- @pg-only
ALTER TABLE IF EXISTS divergence_idea_scores ADD COLUMN IF NOT EXISTS trap_flag TEXT DEFAULT 'clear';
-- @pg-only
ALTER TABLE IF EXISTS divergence_idea_scores ADD COLUMN IF NOT EXISTS trap_level REAL DEFAULT 0.0;
-- @pg-only
ALTER TABLE IF EXISTS divergence_idea_scores ADD COLUMN IF NOT EXISTS is_trap INTEGER DEFAULT 0;
-- @pg-only
ALTER TABLE IF EXISTS divergence_idea_scores ADD COLUMN IF NOT EXISTS trap_rationale TEXT DEFAULT '';
-- @all
