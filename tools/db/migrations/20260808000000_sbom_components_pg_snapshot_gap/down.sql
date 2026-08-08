-- Rollback: 20260808000000_sbom_components_pg_snapshot_gap
-- CUI // SP-CTI
--
-- Deliberately a no-op.
--
-- This migration does not OWN the three tables — migration 209 does. It exists
-- only because a fresh PostgreSQL bootstrap skips 209 (its DDL is absent from
-- pg_consolidated.sql while the snapshot marker claims coverage through 301).
-- Dropping them here would delete tables that 209 created on every database
-- that ran it for real, and would take `sbom_dependencies`, every supply-chain
-- vulnerability row and every risk score with them.
--
-- Roll 209 back if the tables genuinely need to go.

-- @all
SELECT 1;
