-- Migration: 20260808063350_sbom_revision_frequency
-- CUI // SP-CTI
--
-- sbx-prc-02 — Frequency and Accommodation of Updates to SBOM Data.
--
-- Standard: "2026 Minimum Elements for a Software Bill of Materials (SBOM)",
-- CISA with NSA, FBI and 16 international partners, 2026-07-29, v2.1.
-- Gap analysis: docs/compliance/sbom-2026-minimum-elements-gap-analysis.md §3.3
--
-- WHY THREE MORE COLUMNS RATHER THAN REUSING WHAT sbx-fnd-02 LANDED
--
-- fnd-02 gave sbom_records the supersedes link, which is the *pointer* half of
-- Accommodation of Updates. It deliberately left out the two facts a reader
-- needs to make the pointer mean anything, because they belong to this task:
--
-- 1. content_digest — the SBOM's identity apart from its packaging. Every
--    regeneration mints a fresh serialNumber, a fresh timestamp and a fresh
--    SBOM Version, so no existing column can answer "did the component set
--    actually change, or is this the same bill of materials re-emitted?".
--    Frequency requires a new SBOM per build either way; the digest is what
--    lets a recipient tell a substantive revision from a re-issue, and it is
--    what the correction flow compares to prove a correction changed something.
--    Computed over the document with serialNumber / metadata.timestamp / the
--    CycloneDX integer version / the SBOM Version property removed — see
--    tools/compliance/sbom_revision.py::content_digest for the exact recipe.
--
-- 2. source_revision — the build the SBOM describes. CLAUDE.md has asserted
--    "SBOM regenerated on every build" while the only enforced rule was a
--    30-day file mtime threshold, and the gap between those two is not
--    closeable without recording which build each row came from. Git commit
--    SHA where the project directory is a repository, else a caller-supplied
--    build identifier, else NULL (unknown, and reported as such rather than
--    assumed current).
--
-- 3. revision_reason — why this row exists: initial, new_build,
--    dependency_change, correction, detail_discovered. The standard splits
--    "new build or release → new SBOM" from "error corrected → revised SBOM",
--    and a chain that cannot distinguish the two cannot show that corrections
--    were in fact accommodated promptly.
--
-- NO CHECK CONSTRAINT ON revision_reason
--
-- Same reasoning fnd-02 applied to sbom_dependencies.relationship_type: the
-- house rule is that a CHECK vocabulary is derived from a Python constant, not
-- hand-written in DDL. The vocabulary lives in
-- tools/compliance/sbom_revision.py::REVISION_REASONS and is enforced in
-- Python at the single writer. Duplicating it here would create a second copy
-- to keep in sync, and a migration is the most expensive place to fix a
-- vocabulary. NULL is legal and means the row predates this migration.
--
-- APPEND-ONLY
--
-- sbom_records is not an audit table, but the revision chain is treated as
-- append-only by convention and by test: a correction inserts a successor
-- carrying supersedes_sbom_id, and the predecessor's own columns are never
-- rewritten. Supersession is therefore a property a reader derives from the
-- forward links (sbom_revision.revision_chain marks it at read time), not a
-- flag anybody flips on the old row. See tests/test_sbom_revision_2026.py.
--
-- IDEMPOTENCY
--
-- PostgreSQL takes ADD COLUMN IF NOT EXISTS and is re-runnable. SQLite has no
-- such clause so its branch is single-shot, which is correct because
-- schema_migrations records the version and the runner never replays it. Do
-- not add these columns to init_icdev_db.py's CREATE TABLE: a fresh SQLite
-- install would then both create them and run this ALTER, and the resulting
-- duplicate-column error is not one the runner's "already exists" guard
-- matches. Same trap fnd-02 documented.

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only

ALTER TABLE sbom_records ADD COLUMN content_digest  TEXT;
ALTER TABLE sbom_records ADD COLUMN source_revision TEXT;
ALTER TABLE sbom_records ADD COLUMN revision_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_sbom_rec_digest   ON sbom_records(content_digest);
CREATE INDEX IF NOT EXISTS idx_sbom_rec_srcrev   ON sbom_records(source_revision);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only

ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS content_digest  TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS source_revision TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS revision_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_sbom_rec_digest   ON sbom_records(content_digest);
CREATE INDEX IF NOT EXISTS idx_sbom_rec_srcrev   ON sbom_records(source_revision);
