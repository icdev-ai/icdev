-- Migration: 254_win_theme_type_ghost_strategy
-- CUI // SP-CTI
-- Fix: pg_win_themes.theme_type could never hold a ghost strategy.
--
-- win_theme_manager.THEME_TYPES declares ('win_theme','discriminator','ghost_strategy')
-- and register_theme() rejects anything else at :240 — but the CHECK constraint
-- allowed ('win_theme','discriminator','ghost'). The two values never intersected,
-- so 'ghost_strategy' failed the CHECK and 'ghost' failed the Python guard: no ghost
-- row could be written by any path. Consumers that read theme_type = 'ghost'
-- (color_review_simulator.py, program_bridge.py) therefore always found zero.
--
-- Python is the source of truth per CLAUDE.md ("SQL CHECK constraints: derive from
-- Python constants"), so 'ghost_strategy' wins. Readers are updated in the same commit.
--
-- Companion changes (bootstrap_pg marks migrations applied, so a fresh DB never runs
-- this file and must be born correct):
--   * tools/db/init_icdev_db.py + icdev/tools/db/init_icdev_db.py CHECK updated
--   * tools/db/schema/pg_consolidated.sql constraint updated
--   * color_review_simulator.py / program_bridge.py read 'ghost_strategy'
--
-- Idempotent: the UPDATE is a no-op once migrated; DROP CONSTRAINT IF EXISTS is safe.

-- @pg-only
ALTER TABLE pg_win_themes DROP CONSTRAINT IF EXISTS pg_win_themes_theme_type_check;
UPDATE pg_win_themes SET theme_type = 'ghost_strategy' WHERE theme_type = 'ghost';
ALTER TABLE pg_win_themes ADD CONSTRAINT pg_win_themes_theme_type_check
    CHECK (theme_type IN ('win_theme', 'discriminator', 'ghost_strategy'));

-- @sqlite-only
-- SQLite cannot ALTER a CHECK constraint. SQLite is an init-only fallback here, so
-- normalise the data and let init_icdev_db.py create fresh tables with the right CHECK.
UPDATE pg_win_themes SET theme_type = 'ghost_strategy' WHERE theme_type = 'ghost';
