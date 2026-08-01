-- Migration 274: zig_maturity_scores.target_id — per-target ZIG assessment history
-- CUI // SP-CTI
--
-- The ZIG multi-target surface (tools/security_canvas/zig_portfolio.py) reads
-- persisted pillar scores back by target — _latest_scores_for_target() filters
-- "WHERE target_id=%s" — and get_target_assessment() runs a target-scoped
-- assessment. But zig_maturity_scores (migration 272 / consolidated baseline)
-- had no target_id column, so that read failed and every persisted run collapsed
-- onto a single untagged history. This adds the column so run_zig_assessment(
-- target_id=...) and the portfolio comparison operate on independent per-target
-- score histories (cnr-zig-02).
--
-- Additive only. PG-authored; ADD COLUMN IF NOT EXISTS follows migration 267.
-- Existing rows predate multi-target support and are the platform self-scan, so
-- the 'icdev-self' default correctly tags them.

ALTER TABLE zig_maturity_scores ADD COLUMN IF NOT EXISTS target_id TEXT NOT NULL DEFAULT 'icdev-self';

CREATE INDEX IF NOT EXISTS idx_zig_score_target ON zig_maturity_scores(target_id);
