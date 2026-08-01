-- CUI // SP-CTI
-- Migration 295: 'web' citation type + web_fetch_provenance (oss-cite-01).
--
-- source_citation_registry.citation_type had no web/url value, so a page fetched
-- from the internet could not be registered as a first-class citation. That
-- blocked the TRUST invariant end-to-end: inline [source: ...] citations must
-- validate against a PERSISTED provenance record, so anything the two-pass
-- content filter (oss-filter-01/02) extracted from a fetched page was
-- un-citeable no matter how good the extraction was.
--
-- The CHECK clause below is RENDERED FROM the Python constant in
-- tools/provenance/citation_types.py::check_constraint_sql(). Do not hand-edit
-- it — tests/provenance/test_citation_types.py asserts the shipped SQL still
-- matches the constant, so an edit in one place without the other fails CI
-- rather than failing at INSERT time in production.
--
-- Companion updates required by the same guardrail (mirrors migration 254):
--   * tools/db/schema/pg_consolidated.sql constraint updated
--   * tools/provenance/registry.py validates against CITATION_TYPES
--   * tests/conftest.py MINIMAL_ICDEV_SCHEMA carries web_fetch_provenance
--   * .claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES carries it too
--
-- Idempotent: DROP CONSTRAINT IF EXISTS + CREATE TABLE IF NOT EXISTS.

-- @pg-only
ALTER TABLE source_citation_registry
    DROP CONSTRAINT IF EXISTS source_citation_registry_citation_type_check;
-- @pg-only
ALTER TABLE source_citation_registry
    ADD CONSTRAINT source_citation_registry_citation_type_check
    CHECK (citation_type IN ('hitl', 'rag', 'prov_entity', 'prov_activity', 'canvas_ai', 'slsa', 'sbom', 'compliance_evidence', 'agent_decision', 'manual', 'web'));

-- Fetch provenance for a 'web' citation.
--
-- Why a companion table rather than columns on the registry: these fields are
-- meaningless for the other ten citation types, and the registry is queried
-- across all of them. Keeping them separate also means a fetch can be recorded
-- once and cited many times.
--
-- APPEND-ONLY. A fetch is an observation of what a URL served at a moment in
-- time; rewriting it would destroy the evidence the citation rests on. Re-fetch
-- writes a NEW row — that is how evidence drift becomes visible rather than
-- silently overwritten.
CREATE TABLE IF NOT EXISTS web_fetch_provenance (
    id TEXT PRIMARY KEY,
    citation_id TEXT,                       -- source_citation_registry.id
    requested_url TEXT NOT NULL,            -- what the caller asked for
    final_url TEXT,                         -- after redirects; differs => the
                                            -- cited content is not at the asked-for URL
    http_status INTEGER,
    content_hash TEXT NOT NULL,             -- sha256 of the fetched body
    content_type TEXT,
    content_length INTEGER,
    etag TEXT,                              -- server validators, when offered:
    last_modified TEXT,                     -- let a re-fetch prove un/changed
                                            -- without re-downloading
    fetched_at TEXT NOT NULL,
    fetcher TEXT,                           -- which module performed the fetch
    classification TEXT DEFAULT 'CUI',
    project_id TEXT,
    tenant_id TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_wfp_citation ON web_fetch_provenance (citation_id);
CREATE INDEX IF NOT EXISTS idx_wfp_requested_url ON web_fetch_provenance (requested_url);
CREATE INDEX IF NOT EXISTS idx_wfp_content_hash ON web_fetch_provenance (content_hash);
CREATE INDEX IF NOT EXISTS idx_wfp_fetched_at ON web_fetch_provenance (fetched_at);

-- @sqlite-only
-- SQLite cannot ALTER a CHECK constraint. It is an init-only fallback here, so
-- init_icdev_db.py creates the table fresh with the constraint rendered from the
-- same Python constant. Nothing to normalise: 'web' is a NEW value, so no
-- existing row can violate the old constraint.
