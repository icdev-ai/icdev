-- 305_studio_run_inputs.sql
-- DWO / dwo-evt-04 — a run must be able to see what started it.
--
-- start_run() previously took no payload, so a run started by a trigger
-- (dwo-evt-01's studio_workflow_triggers) had no way to show its steps the
-- event that fired it.  Two additive, nullable columns close that:
--
--   inputs_json      — the run's resolved inputs, as supplied by the caller or
--                      produced by the trigger's input_mapping_json.  This is a
--                      denormalised copy for run-detail display; the channel
--                      steps actually read is run memory (dwo-mem-01) under
--                      run_memory.INPUTS_KEY.  We do NOT invent a third
--                      mechanism — this column is for the UI and audit.
--   trigger_event_id — FK-by-convention to studio_trigger_events(event_id).
--                      NULL for a manually started run.  This is what the
--                      run-detail badge links to when answering "why did this
--                      run start".
--
-- Both are nullable with no default, so every existing run row and every
-- existing start_run() caller keeps working unchanged.

ALTER TABLE studio_workflow_runs ADD COLUMN inputs_json TEXT;
ALTER TABLE studio_workflow_runs ADD COLUMN trigger_event_id TEXT;

CREATE INDEX IF NOT EXISTS idx_studio_workflow_runs_trigger_event
    ON studio_workflow_runs(trigger_event_id);
