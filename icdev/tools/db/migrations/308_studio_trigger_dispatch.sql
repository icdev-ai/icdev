-- 308_studio_trigger_dispatch.sql
-- CUI // SP-CTI
-- DWO / dwo-evt-02 — columns the gateway→workflow dispatch hop needs.
--
-- Migration 304 (dwo-evt-01) created the three registry tables. Dispatch adds
-- three properties on top of them, and each needs storage:
--
--   max_il / workflow_il  — classification. A run inherits the event's IL, and
--                           a trigger pointing at a workflow rated *below* that
--                           IL is refused. Both ceilings have to be persisted
--                           or the refusal cannot be evaluated.
--   idempotency_key       — replay protection. UNIQUE, so a webhook retry loses
--                           the INSERT and therefore never starts a second run.
--                           Mirrors kanban_tasks.idempotency_key.
--   outcome / classification / workflow_id / envelope_id
--                         — the audit row has to say what happened, not just
--                           that something did: 'no_match', 'matched',
--                           'run_started', 'refused_classification', 'error'.
--
-- studio_trigger_events stays APPEND-ONLY (NIST AU-9): the run_id produced by a
-- claim is written as a SECOND row referencing it, never as an UPDATE.
--
-- Numbering: 305/306/307 are taken (studio_workflows_rls_columns,
-- studio_workflow_run_inputs, studio_mcp_dispatch_audit). The original
-- dwo-evt-02 branch authored this as 305 against an older ceiling.

ALTER TABLE studio_event_sources ADD COLUMN max_il TEXT DEFAULT 'IL2';

ALTER TABLE studio_workflow_triggers ADD COLUMN workflow_il TEXT DEFAULT 'IL6';
ALTER TABLE studio_workflow_triggers ADD COLUMN project_id TEXT DEFAULT 'default';

ALTER TABLE studio_trigger_events ADD COLUMN workflow_id TEXT;
ALTER TABLE studio_trigger_events ADD COLUMN outcome TEXT;
ALTER TABLE studio_trigger_events ADD COLUMN classification TEXT;
ALTER TABLE studio_trigger_events ADD COLUMN idempotency_key TEXT;
ALTER TABLE studio_trigger_events ADD COLUMN envelope_id TEXT;

-- The replay guard. UNIQUE (not just indexed): the INSERT is the claim, so a
-- duplicate delivery loses the race at the database rather than in a
-- SELECT-then-INSERT check that two concurrent webhooks would both pass.
-- Partial so the many rows with no delivery id (no_match, refusals, events on
-- channels that send no stable id) do not collide with each other on NULL.
CREATE UNIQUE INDEX IF NOT EXISTS ux_studio_trigger_events_idem
    ON studio_trigger_events (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_studio_trigger_events_outcome
    ON studio_trigger_events (outcome);
