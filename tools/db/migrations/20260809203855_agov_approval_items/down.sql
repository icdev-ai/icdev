-- Rollback: 20260809203855_agov_approval_items
-- CUI // SP-CTI
--
-- Safe to drop: approval_items is mutable short-lived state, not evidence. The
-- permanent record of every decision it produced is already in the append-only
-- agent_approval_log and is untouched by this rollback.

DROP INDEX IF EXISTS idx_approval_items_expires;
DROP INDEX IF EXISTS idx_approval_items_session;
DROP INDEX IF EXISTS idx_approval_items_state;
DROP INDEX IF EXISTS idx_approval_items_inbox_state;
DROP TABLE IF EXISTS approval_items;
