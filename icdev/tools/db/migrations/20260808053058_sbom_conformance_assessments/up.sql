-- Migration: 20260808053058_sbom_conformance_assessments
-- CUI // SP-CTI
--
-- sbx-sig-02 — the conformance validator's assessment ledger.
--
-- Standard: "2026 Minimum Elements for a Software Bill of Materials (SBOM)",
-- CISA with NSA, FBI and 16 international partners, 2026-07-29, v2.1.
-- Gap analysis: docs/compliance/sbom-2026-minimum-elements-gap-analysis.md
-- Producer: tools/compliance/sbom_minimum_elements_validator.py
--
-- WHY A TABLE AT ALL
--
-- The validator itself is a pure function: read a document, score it, print
-- JSON. It writes nothing unless asked (`--record`). The table exists because
-- sbx-gov-01's requirement is not "block when the score is low" but "block
-- when the score REGRESSES", and a regression cannot be computed from a
-- single run. One row per assessment is the history that comparison needs.
--
-- APPEND-ONLY. This is compliance evidence: it is the record of what ICDEV
-- knew about an SBOM's conformance at a point in time, including SBOMs
-- RECEIVED FROM VENDORS. Rewriting a past score would rewrite the basis of a
-- past acceptance decision. Registered in APPEND_ONLY_TABLES in
-- .claude/hooks/pre_tool_use.py. Re-scoring a document inserts a new row; the
-- latest row for a document_sha256 wins.
--
-- WHY document_sha256 AND NOT sbom_record_id ALONE
--
-- Half the point of this validator is grading third-party SBOMs, which have
-- no sbom_records row — there is no ICDEV project and no generation event
-- behind a vendor's CycloneDX file. So sbom_record_id and project_id are both
-- nullable and the hash of the document bytes is the durable identity. For an
-- ICDEV-generated SBOM both are populated and the row joins back to the
-- generation event.
--
-- WHY elements_json AND NOT AN ELEMENT TABLE
--
-- The 23 elements are a CLOSED vocabulary fixed by a published standard, and
-- the per-element verdict is only ever read as a whole report. A child table
-- would buy join-ability for a set that never changes shape and would have to
-- be re-migrated the next time the standard revises (the 2021 document lasted
-- five years). The counts that gates actually query are denormalised into
-- their own columns; the blob carries the rationale text a human reads.
--
-- RLS
--
-- classification NOT NULL DEFAULT 'CUI' and nullable tenant_id, matching
-- migration 326 and sbx-fnd-02 exactly. get_connection attaches the global
-- row predicate to every table it touches inside a request context, so a
-- table missing either column raises UndefinedColumn in the browser while
-- passing every pytest run outside a request context.

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only

CREATE TABLE IF NOT EXISTS sbom_conformance_assessments (
    id                   TEXT    PRIMARY KEY,
    sbom_record_id       INTEGER REFERENCES sbom_records(id),
    project_id           TEXT,
    document_path        TEXT,
    document_sha256      TEXT    NOT NULL,
    format_name          TEXT,
    format_version       TEXT,
    component_count      INTEGER NOT NULL DEFAULT 0,
    data_fields_met      INTEGER NOT NULL DEFAULT 0,
    data_fields_partial  INTEGER NOT NULL DEFAULT 0,
    data_fields_gap      INTEGER NOT NULL DEFAULT 0,
    practices_met        INTEGER NOT NULL DEFAULT 0,
    practices_partial    INTEGER NOT NULL DEFAULT 0,
    practices_gap        INTEGER NOT NULL DEFAULT 0,
    weighted_score       REAL    NOT NULL DEFAULT 0.0,
    conformant           INTEGER NOT NULL DEFAULT 0,
    elements_json        TEXT    NOT NULL DEFAULT '[]',
    validator_version    TEXT,
    standard_version     TEXT,
    assessed_at          TEXT    DEFAULT (datetime('now')),
    classification       TEXT    NOT NULL DEFAULT 'CUI',
    tenant_id            TEXT
);

CREATE INDEX IF NOT EXISTS idx_sbom_conf_record  ON sbom_conformance_assessments(sbom_record_id);
CREATE INDEX IF NOT EXISTS idx_sbom_conf_project ON sbom_conformance_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_sbom_conf_sha     ON sbom_conformance_assessments(document_sha256);
CREATE INDEX IF NOT EXISTS idx_sbom_conf_tenant  ON sbom_conformance_assessments(tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only

CREATE TABLE IF NOT EXISTS sbom_conformance_assessments (
    id                   TEXT    PRIMARY KEY,
    sbom_record_id       INTEGER REFERENCES sbom_records(id),
    project_id           TEXT,
    document_path        TEXT,
    document_sha256      TEXT    NOT NULL,
    format_name          TEXT,
    format_version       TEXT,
    component_count      INTEGER NOT NULL DEFAULT 0,
    data_fields_met      INTEGER NOT NULL DEFAULT 0,
    data_fields_partial  INTEGER NOT NULL DEFAULT 0,
    data_fields_gap      INTEGER NOT NULL DEFAULT 0,
    practices_met        INTEGER NOT NULL DEFAULT 0,
    practices_partial    INTEGER NOT NULL DEFAULT 0,
    practices_gap        INTEGER NOT NULL DEFAULT 0,
    weighted_score       REAL    NOT NULL DEFAULT 0.0,
    conformant           INTEGER NOT NULL DEFAULT 0,
    elements_json        TEXT    NOT NULL DEFAULT '[]',
    validator_version    TEXT,
    standard_version     TEXT,
    assessed_at          TEXT    DEFAULT (now()::text),
    classification       TEXT    NOT NULL DEFAULT 'CUI',
    tenant_id            TEXT
);

CREATE INDEX IF NOT EXISTS idx_sbom_conf_record  ON sbom_conformance_assessments(sbom_record_id);
CREATE INDEX IF NOT EXISTS idx_sbom_conf_project ON sbom_conformance_assessments(project_id);
CREATE INDEX IF NOT EXISTS idx_sbom_conf_sha     ON sbom_conformance_assessments(document_sha256);
CREATE INDEX IF NOT EXISTS idx_sbom_conf_tenant  ON sbom_conformance_assessments(tenant_id);

-- @all
