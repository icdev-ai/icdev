-- Rollback: 20260809221051_agov_agent_wakes
-- CUI // SP-CTI
--
-- Safe to drop: agent_wakes is short-lived suspension state, not evidence. What
-- an agent actually DID after it woke is recorded in the append-only trails
-- (agent_approval_log, hook_events, audit_trail), none of which this rollback
-- touches.
--
-- What IS lost is every unspent wake: an agent suspended on a timer or waiting
-- on a job or event will never be resumed by anything after this runs, and
-- nothing will report that. Cancel or fire the pending rows before rolling back.

DROP INDEX IF EXISTS idx_agent_wakes_session;
DROP INDEX IF EXISTS idx_agent_wakes_event;
DROP INDEX IF EXISTS idx_agent_wakes_job;
DROP INDEX IF EXISTS idx_agent_wakes_timer;
DROP TABLE IF EXISTS agent_wakes;
