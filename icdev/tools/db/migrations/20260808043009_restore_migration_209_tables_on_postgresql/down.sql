-- Rollback: 20260808043009_restore_migration_209_tables_on_postgresql
-- CUI // SP-CTI
--
-- Deliberately empty.
--
-- This migration does not introduce these tables; it repairs a PostgreSQL that
-- was missing them because the consolidated snapshot certified migration 209 as
-- applied without containing it. Dropping them on rollback would destroy the
-- 209 schema on every database that legitimately has it, plus whatever the SBOM
-- generator and the supply-chain risk handler have written since — a rollback
-- that is strictly more destructive than the change it reverses.
--
-- Every statement in up.sql is IF NOT EXISTS, so re-applying is free and there is
-- nothing to undo.

SELECT 1;
