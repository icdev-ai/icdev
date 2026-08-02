-- Rollback: 20260802200931_agent_approval_log
-- CUI // SP-CTI
--
-- Dropping the table discards the approval trail. That is acceptable only as a
-- schema rollback in a development environment — in any environment holding real
-- decisions, export the rows before running this.

DROP INDEX IF EXISTS idx_agent_approval_tool;
DROP INDEX IF EXISTS idx_agent_approval_decision;
DROP INDEX IF EXISTS idx_agent_approval_session;
DROP INDEX IF EXISTS idx_agent_approval_created;
DROP TABLE IF EXISTS agent_approval_log;
