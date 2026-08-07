-- CUI // SP-CTI
-- Widen proposal_reviews.review_type to accept 'white_team' (mvs-invisible-04).
--
-- This finishes a job migration 173 started and never did.
-- 173_white_team_review_type.py is a bare .py file, a shape MigrationRunner has
-- never discovered, so it has never run anywhere. The consequence is a live
-- schema that disagrees with the code above it:
--
--   * tools/dashboard/api/proposals.py::REVIEW_TYPES offers 'white_team'
--   * tools/db/init_icdev_db.py grants it in the CREATE TABLE, so every FRESH
--     database has the wide constraint and looks correct
--   * every database that predates that DDL edit still carries the narrow
--     constraint, because CREATE TABLE IF NOT EXISTS never alters an existing
--     table (the exact failure mode CLAUDE.md calls out)
--
-- Verified on the live PostgreSQL database 2026-08-03: the constraint allowed
-- only pink_team, red_team, gold_team, white_glove, internal — so scheduling a
-- white-team review raised a CHECK violation on precisely the deployments that
-- have been running longest.
--
-- 173 is NOT renumbered into this shape, for three independent reasons:
--
--   1. schema_migrations on the live database ALREADY holds version 173, as
--      "squashed-173" from the bootstrap marking. A migration promoted to
--      version 173 would therefore be treated as applied and skipped without
--      running — the fix would look landed and change nothing.
--   2. Version 173 is also claimed by 173_cpmp_obligation_periods.py, so
--      promoting either file to a directory creates a duplicate version — the
--      collision that shadows migrations and that
--      tests/test_migration_version_uniqueness.py exists to prevent.
--   3. The legacy 3-digit range is closed (mvs-alloc-01).
--
-- So the fix lands on a timestamp id and 173 stays documented as
-- invisible-but-superseded.

-- @pg-only
-- Drop-then-add is what makes this re-runnable: a second run drops the wide
-- constraint it just created and puts it back identically.
ALTER TABLE proposal_reviews DROP CONSTRAINT IF EXISTS proposal_reviews_review_type_check;

ALTER TABLE proposal_reviews ADD CONSTRAINT proposal_reviews_review_type_check
    CHECK (review_type IN (
        'pink_team', 'red_team', 'gold_team', 'white_team', 'white_glove', 'internal'));

-- @sqlite-only
-- Deliberately a no-op. SQLite cannot ALTER a CHECK constraint; changing one
-- means rebuilding the table, which is what 173_white_team_review_type.py does
-- and what it remains available for. It is not replayed here because SQLite is
-- the init-fallback backend, its fresh schema already comes from
-- init_icdev_db.py with 'white_team' present, and a table rebuild is too blunt
-- to run unconditionally against a database this migration cannot inspect.
SELECT 1;
