-- CUI // SP-CTI
-- Migration 289: twin_compat_reports — high-side compatibility report snapshots (twx-fed-03)
--
-- Backing store for tools/twin_core/compat_report.py. One row per persisted
-- compatibility-report run for an IDC twin/deployment against a target-environment
-- preset (fed-02). MUTABLE snapshot table following the PDC dedup/retention
-- pattern (NOT append-only): persist_report() no-ops when the newest row for a
-- target already carries the same content_hash, and prunes to the newest N rows
-- per target. The immutable ATO evidence trail lives in the existing append-only
-- audit_trail via the compliance engines, so no new append-only table is needed.
--
-- Carries tenant_id + classification for row-level security. DDL is idempotent
-- (CREATE ... IF NOT EXISTS) and mirrors the runtime _ensure_schema() in the
-- engine, so running either first is safe.

CREATE TABLE IF NOT EXISTS twin_compat_reports (
    id             TEXT PRIMARY KEY,
    target_id      TEXT NOT NULL,
    source_canvas  TEXT,
    target_preset  TEXT,
    verdict        TEXT,
    blocker_count  INTEGER NOT NULL DEFAULT 0,
    content_hash   TEXT NOT NULL,
    report_json    TEXT NOT NULL,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_twin_compat_target ON twin_compat_reports(target_id, created_at);
