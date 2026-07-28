-- 305_studio_trigger_dispatch.sql
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
--                           that something did. "no_match", "matched",
--                           "run_started", "refused_classification", "error".
--
-- studio_trigger_events stays APPEND-ONLY (NIST AU-9): the run_id produced by a
-- claim is written as a second row referencing it, never as an UPDATE.

ALTER TABLE studio_event_sources ADD COLUMN max_il TEXT DEFAULT 'IL2';

ALTER TABLE studio_workflow_triggers ADD COLUMN workflow_il TEXT DEFAULT 'IL6';
ALTER TABLE studio_workflow_triggers ADD COLUMN project_id TEXT DEFAULT 'default';

ALTER TABLE studio_trigger_events ADD COLUMN workflow_id TEXT;
ALTER TABLE studio_trigger_events ADD COLUMN outcome TEXT;
ALTER TABLE studio_trigger_events ADD COLUMN classification TEXT;
ALTER TABLE studio_trigger_events ADD COLUMN idempotency_key TEXT;
ALTER TABLE studio_trigger_events ADD COLUMN envelope_id TEXT;
ALTER TABLE studio_trigger_events ADD COLUMN created_at TEXT;

-- The idempotency mechanism itself. NULL keys repeat freely on both backends,
-- which is what we want: an event carrying no stable delivery id is dispatched
-- but explicitly not de-duplicated, and the audit row says so.
CREATE UNIQUE INDEX IF NOT EXISTS idx_studio_trigger_events_idem
    ON studio_trigger_events(idempotency_key);

CREATE INDEX IF NOT EXISTS idx_studio_trigger_events_outcome
    ON studio_trigger_events(outcome);
