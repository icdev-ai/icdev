-- Rollback: 20260816122036_agent_session_events
-- CUI // SP-CTI
--
-- Dropping the table destroys the evidence it holds. That is what a rollback of
-- a CREATE TABLE means and there is no non-destructive version of it, so this is
-- for un-applying the migration on a database that never carried real events —
-- a fresh worktree, an ephemeral CI database, a failed first apply.

DROP INDEX IF EXISTS idx_agent_session_events_correlation;
DROP INDEX IF EXISTS idx_agent_session_events_tenant;
DROP INDEX IF EXISTS idx_agent_session_events_session;
DROP INDEX IF EXISTS idx_agent_session_events_seq;
DROP TABLE IF EXISTS agent_session_events;
