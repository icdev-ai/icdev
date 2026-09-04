-- Migration: 20260903194350_dic_artifacts
-- CUI // SP-CTI
--
-- rmf-wp-02 -- one row per DIC version export, mirroring idr_artifacts.
--
-- DIC had no export route, so nothing recorded what left the canvas. The
-- row is the record: the file's sha256 and size, the WriteGuard score the
-- export was gated on, the full gate report, and whether a human forced it
-- past a refusing gate (`forced` + `force_reason`, beside the append-only
-- idr_publish_audit / audit_trail rows the route writes first).
--
-- `version_status` is the version's status AT EXPORT TIME. Export does not
-- require `approved` (a draft exported for offline review is legitimate), so
-- the artifact must say what it was rather than let a reader assume.
--
-- The `format` CHECK is rendered from tools/document_intelligence/exporter.py
-- EXPORT_FORMATS; tests/document_intelligence/test_export.py asserts the two
-- agree. Runtime also CREATEs this table IF NOT EXISTS (exporter._ensure_schema)
-- so a SQLite deployment that never ran migrations still has it; that CREATE
-- never ALTERs, so a later column needs a migration of its own.
--
-- tenant_id/classification: read through the RLS-aware get_connection(), like
-- every dic_* table. classification holds a LABEL ('CUI'), never a banner.

CREATE TABLE IF NOT EXISTS dic_artifacts (
    artifact_id      TEXT PRIMARY KEY,
    version_id       TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    format           TEXT NOT NULL CHECK (format IN ('md','html','docx','pdf')),
    file_path        TEXT,
    sha256           TEXT,
    byte_size        INTEGER,
    title            TEXT,
    version_status   TEXT,
    wg_score         REAL,
    wg_passed        INTEGER,
    gate_report_json TEXT,
    forced           INTEGER NOT NULL DEFAULT 0,
    force_reason     TEXT,
    exported_by      TEXT,
    tenant_id        TEXT,
    classification   TEXT,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dic_artifacts_version ON dic_artifacts(version_id);
CREATE INDEX IF NOT EXISTS idx_dic_artifacts_doc ON dic_artifacts(doc_id);
