-- CUI // SP-CTI
-- Migration 196: Ensure fedramp_ato_packages has a PRIMARY KEY (UNIQUE) constraint
-- before fedramp_controls references it via FK.
--
-- Context: pg_consolidated.sql adds the PRIMARY KEY via ALTER TABLE after the
-- CREATE TABLE statement.  On fresh installs this ordering is correct, but
-- partial loads or schema-snapshot gaps can leave fedramp_ato_packages without
-- a unique index, causing the fedramp_controls FK to fail validation.
--
-- This migration is idempotent: it is a no-op when fedramp_ato_packages_pkey
-- already exists (created by migration 192 or pg_consolidated.sql).
--
-- Idempotent (IF NOT EXISTS guard).

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
-- SQLite PRIMARY KEY already enforces uniqueness; nothing to add here.
SELECT 1;

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fedramp_ato_packages_pkey'
      AND conrelid = 'fedramp_ato_packages'::regclass
  ) THEN
    ALTER TABLE fedramp_ato_packages
      ADD CONSTRAINT fedramp_ato_packages_pkey PRIMARY KEY (id);
    RAISE NOTICE 'fedramp_ato_packages PRIMARY KEY (unique) constraint created';
  ELSE
    RAISE NOTICE 'fedramp_ato_packages PRIMARY KEY (unique) constraint already present';
  END IF;
END $$;

-- @all
