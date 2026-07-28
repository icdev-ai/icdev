-- CUI // SP-CTI
-- Migration 302: harness_eval table + indexes (ahx-eval-03).
--
-- harness_eval has been live and read/written since the Genesis continuous
-- evaluation harness shipped, but it never had a migration. Its schema existed
-- in exactly two places: tools/db/schema/pg_consolidated.sql (the squashed
-- bootstrap) and tests/conftest.py::MINIMAL_ICDEV_SCHEMA. A deployment that
-- applied migrations against a database not built from the consolidated
-- bootstrap would therefore never get the table, and every record_decision /
-- record_outcome call would fail into its own except-and-warn handler — which
-- is precisely the failure mode that keeps a measurement table silently empty.
--
-- This is a consistency fix, NOT a schema change. Columns, types, defaults,
-- nullability, the primary key and both indexes are reproduced exactly as they
-- appear in pg_consolidated.sql (table at ~16411, PK at ~41364, indexes at
-- ~53187 and ~53194). Idempotent, so it is a no-op on any database already
-- bootstrapped from the consolidated schema.

CREATE TABLE IF NOT EXISTS harness_eval (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL DEFAULT '',
    reflex         TEXT NOT NULL,
    decision       TEXT NOT NULL,
    confidence     REAL,
    metadata_json  TEXT DEFAULT '{}',
    actual_outcome TEXT,
    resolved_at    TEXT,
    created_at     TEXT NOT NULL
);

-- Reflex-scoped metric windows: compute_metrics() filters by reflex and a
-- created_at cutoff, so the composite ordering matters.
CREATE INDEX IF NOT EXISTS idx_harness_eval_reflex ON harness_eval (reflex, created_at);

-- record_outcome() looks rows up by task_id alone.
CREATE INDEX IF NOT EXISTS idx_harness_eval_task ON harness_eval (task_id);
