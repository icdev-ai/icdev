-- 304_studio_event_tables.sql
-- DWO / dwo-evt-01 — event source and workflow trigger registry.
--
-- ICDEV already receives external events (tools/gateway/gateway_agent.py) and
-- runs DAGs (tools/studio/workflow_runner.py), but nothing bound the two.
-- These three tables are that binding:
--
--   studio_event_sources     — where events come from (one row per source)
--   studio_workflow_triggers — "when event X matching filter Y arrives on
--                              source S, start workflow Z"
--   studio_trigger_events    — APPEND-ONLY audit of every event evaluated,
--                              matched or not, with the run it started.
--                              This is the answer to "why did this run start".
--
-- JSON columns are TEXT and are parsed in Python — never with SQLite JSON
-- functions (see the CLAUDE.md PG portability rule).
--
-- Filter language: filter_json holds automation_builder.CONDITION_OPERATORS
-- conditions (equals, not_equals, contains, greater_than, less_than, in_list,
-- is_empty, is_not_empty).  There is no second condition DSL.

CREATE TABLE IF NOT EXISTS studio_event_sources (
    source_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL
                CHECK(kind IN ('gateway_channel','canvas_bus','schedule','manual')),
    config_json TEXT,
    enabled     INTEGER DEFAULT 1,
    created_by  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_studio_event_sources_kind
    ON studio_event_sources(kind);

CREATE TABLE IF NOT EXISTS studio_workflow_triggers (
    trigger_id        TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL REFERENCES studio_event_sources(source_id),
    workflow_id       TEXT NOT NULL REFERENCES studio_workflows(workflow_id),
    event_type        TEXT,
    filter_json       TEXT,
    input_mapping_json TEXT,
    enabled           INTEGER DEFAULT 1,
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_studio_workflow_triggers_source
    ON studio_workflow_triggers(source_id);
CREATE INDEX IF NOT EXISTS idx_studio_workflow_triggers_workflow
    ON studio_workflow_triggers(workflow_id);

-- APPEND-ONLY — audit trail (NIST AU).  Never UPDATE or DELETE.
-- One row per evaluated event, including events that matched nothing
-- (matched = 0, run_id NULL) so a non-firing trigger is diagnosable.
CREATE TABLE IF NOT EXISTS studio_trigger_events (
    event_id     TEXT PRIMARY KEY,
    source_id    TEXT,
    trigger_id   TEXT,
    event_type   TEXT,
    payload_json TEXT,
    matched      INTEGER DEFAULT 0,
    run_id       TEXT,
    reason       TEXT,
    received_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_studio_trigger_events_source
    ON studio_trigger_events(source_id);
CREATE INDEX IF NOT EXISTS idx_studio_trigger_events_trigger
    ON studio_trigger_events(trigger_id);
CREATE INDEX IF NOT EXISTS idx_studio_trigger_events_run
    ON studio_trigger_events(run_id);
