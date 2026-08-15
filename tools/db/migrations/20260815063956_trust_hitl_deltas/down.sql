-- Rollback: 20260815063956_trust_hitl_deltas
-- CUI // SP-CTI
--
-- Dropping this table destroys HITL review evidence. It is reversible only in
-- the schema sense — the rows are not recoverable. Take an evidence copy first.

DROP INDEX IF EXISTS idx_trust_deltas_approval_item;
DROP INDEX IF EXISTS idx_trust_deltas_created;
DROP INDEX IF EXISTS idx_trust_deltas_supersedes;
DROP INDEX IF EXISTS idx_trust_deltas_artifact;
DROP INDEX IF EXISTS idx_trust_deltas_disposition;
DROP TABLE IF EXISTS trust_deltas;
