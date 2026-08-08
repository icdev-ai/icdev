-- Migration: 20260808030213_sbom_2026_minimum_elements
-- CUI // SP-CTI
--
-- sbx-fnd-02 — storage for the 2026 SBOM Minimum Elements.
--
-- Standard: "2026 Minimum Elements for a Software Bill of Materials (SBOM)",
-- CISA with NSA, FBI and 16 international partners, 2026-07-29, v2.1.
-- Gap analysis: docs/compliance/sbom-2026-minimum-elements-gap-analysis.md
--
-- This is a NEW migration rather than an edit to the CREATE TABLE bodies in
-- tools/db/init_icdev_db.py on purpose. CREATE TABLE IF NOT EXISTS never
-- alters an existing table, so editing that DDL adds nothing to a live
-- database while making the declaration disagree with reality — the exact
-- failure mode check_insert_schema_parity exists to catch.
--
-- WHAT LANDS HERE
--
-- 1. sbom_records gains the 11 document-level fields the standard requires
--    but ICDEV never persisted: SBOM Author and its signature/algorithm,
--    Data Format Name/Version, Generation Context, Tool Name/Version,
--    SBOM Version, Serial Number, and the supersedes link that
--    "Accommodation of Updates" needs — sbx-prc-02 writes that one.
--
-- 2. sbom_components gains Component Producer, Hash Value, Hash Algorithm,
--    the multi-identifier blob, and the two explicit-unknown blobs that
--    sbx-prc-01 defines a convention for. The pre-existing `license` and
--    `vendor` columns are REUSED, not duplicated: they are dead today only
--    because the generator has never written an sbom_components row, and
--    `license` is exactly the 2026 Component License element.
--
-- 3. sbom_dependencies is new — the Component Dependency Relationship
--    element, as a companion edge table rather than a parent-ref column on
--    sbom_components, because a component can have MANY parents inside one
--    SBOM document. The edge is scoped by sbom_record_id: the same component
--    row can sit at different points of the graph in two different documents,
--    so the document, not the component, is what makes a parent meaningful.
--
--    relationship_type carries NO CHECK constraint. The house rule is that a
--    CHECK vocabulary must be derived from a Python constant rather than
--    hardcoded in DDL, and the vocabulary itself is sbx-cov-02's to define —
--    it has to express both CycloneDX dependsOn and SPDX RELATIONSHIP kinds.
--    Inventing one here would fix the wrong list in a constraint that a later
--    migration then has to rewrite. 'depends_on' is the default.
--
-- RLS DECISION
--
-- sbom_records had NO classification and NO tenant_id in the declared schema.
-- get_connection attaches the global predicate to every table it touches
-- inside a request context — `classification IN <dominated set>` for any
-- authenticated caller, plus `tenant_id = ?` for a tenant-scoped one — so a
-- table missing either column raises UndefinedColumn on every query from the
-- browser while passing every pytest run outside a request context. That is
-- the trap migrations 305 / 309 / 311 / 326 each documented. Both columns are
-- therefore ensured on both tables and declared on the new one.
--
-- Defaults mirror 326 exactly: classification NOT NULL DEFAULT 'CUI' so
-- pre-existing rows stay visible under a read-down predicate, tenant_id
-- nullable so single-tenant installs are unaffected. Note the consequence,
-- which is the same one 326 accepted: a tenant-scoped caller sees no legacy
-- NULL-tenant row, because inject_row_predicate uses strict equality. Today
-- those callers get a hard error instead, so this is strictly an improvement.
--
-- IDEMPOTENCY
--
-- The PostgreSQL branch takes ADD COLUMN IF NOT EXISTS and is re-runnable.
-- SQLite has no such clause, so its branch is single-shot — which is correct
-- here because schema_migrations records the version and the runner never
-- replays it, and because no SQLite database anywhere declares these columns
-- yet. Do not "fix" that by adding them to init_icdev_db.py: a fresh SQLite
-- install would then create them AND run this ALTER, and the duplicate-column
-- error is not one the runner's "already exists" guard matches.

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only

-- --- sbom_records: the 9 SBOM Metadata elements + signature + supersedes ---
ALTER TABLE sbom_records ADD COLUMN sbom_author          TEXT;
ALTER TABLE sbom_records ADD COLUMN author_signature     TEXT;
ALTER TABLE sbom_records ADD COLUMN signature_algorithm  TEXT;
ALTER TABLE sbom_records ADD COLUMN data_format_name     TEXT;
ALTER TABLE sbom_records ADD COLUMN data_format_version  TEXT;
ALTER TABLE sbom_records ADD COLUMN generation_context   TEXT;
ALTER TABLE sbom_records ADD COLUMN tool_name            TEXT;
ALTER TABLE sbom_records ADD COLUMN tool_version         TEXT;
ALTER TABLE sbom_records ADD COLUMN sbom_version         TEXT;
ALTER TABLE sbom_records ADD COLUMN serial_number        TEXT;
ALTER TABLE sbom_records ADD COLUMN supersedes_sbom_id   INTEGER REFERENCES sbom_records(id);
ALTER TABLE sbom_records ADD COLUMN classification       TEXT NOT NULL DEFAULT 'CUI';
ALTER TABLE sbom_records ADD COLUMN tenant_id            TEXT;

CREATE INDEX IF NOT EXISTS idx_sbom_rec_project    ON sbom_records(project_id);
CREATE INDEX IF NOT EXISTS idx_sbom_rec_serial     ON sbom_records(serial_number);
CREATE INDEX IF NOT EXISTS idx_sbom_rec_supersedes ON sbom_records(supersedes_sbom_id);
CREATE INDEX IF NOT EXISTS idx_sbom_rec_tenant     ON sbom_records(tenant_id);

-- --- sbom_components: Producer, Hash, Identifiers, explicit unknowns ---
ALTER TABLE sbom_components ADD COLUMN producer             TEXT;
ALTER TABLE sbom_components ADD COLUMN hash_value           TEXT;
ALTER TABLE sbom_components ADD COLUMN hash_algorithm       TEXT;
ALTER TABLE sbom_components ADD COLUMN identifiers_json     TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sbom_components ADD COLUMN unknown_fields_json  TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sbom_components ADD COLUMN withheld_fields_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sbom_components ADD COLUMN tenant_id            TEXT;

CREATE INDEX IF NOT EXISTS idx_sbom_comp_producer ON sbom_components(producer);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_hash     ON sbom_components(hash_value);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_tenant   ON sbom_components(tenant_id);

-- --- sbom_dependencies: the Component Dependency Relationship element ---
CREATE TABLE IF NOT EXISTS sbom_dependencies (
    id                  TEXT    PRIMARY KEY,
    sbom_record_id      INTEGER NOT NULL REFERENCES sbom_records(id),
    parent_component_id TEXT    NOT NULL REFERENCES sbom_components(id),
    child_component_id  TEXT    NOT NULL REFERENCES sbom_components(id),
    relationship_type   TEXT    NOT NULL DEFAULT 'depends_on',
    scope               TEXT,
    classification      TEXT    NOT NULL DEFAULT 'CUI',
    tenant_id           TEXT,
    created_at          TEXT    DEFAULT (datetime('now')),
    UNIQUE (sbom_record_id, parent_component_id, child_component_id, relationship_type)
);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_record ON sbom_dependencies(sbom_record_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_parent ON sbom_dependencies(parent_component_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_child  ON sbom_dependencies(child_component_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_tenant ON sbom_dependencies(tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only

-- --- sbom_records: the 9 SBOM Metadata elements + signature + supersedes ---
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS sbom_author          TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS author_signature     TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS signature_algorithm  TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS data_format_name     TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS data_format_version  TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS generation_context   TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS tool_name            TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS tool_version         TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS sbom_version         TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS serial_number        TEXT;
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS supersedes_sbom_id   INTEGER REFERENCES sbom_records(id);
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS classification       TEXT NOT NULL DEFAULT 'CUI';
ALTER TABLE sbom_records ADD COLUMN IF NOT EXISTS tenant_id            TEXT;

CREATE INDEX IF NOT EXISTS idx_sbom_rec_project    ON sbom_records(project_id);
CREATE INDEX IF NOT EXISTS idx_sbom_rec_serial     ON sbom_records(serial_number);
CREATE INDEX IF NOT EXISTS idx_sbom_rec_supersedes ON sbom_records(supersedes_sbom_id);
CREATE INDEX IF NOT EXISTS idx_sbom_rec_tenant     ON sbom_records(tenant_id);

-- --- sbom_components: Producer, Hash, Identifiers, explicit unknowns ---
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS producer             TEXT;
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS hash_value           TEXT;
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS hash_algorithm       TEXT;
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS identifiers_json     TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS unknown_fields_json  TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS withheld_fields_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS tenant_id            TEXT;

CREATE INDEX IF NOT EXISTS idx_sbom_comp_producer ON sbom_components(producer);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_hash     ON sbom_components(hash_value);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_tenant   ON sbom_components(tenant_id);

-- --- sbom_dependencies: the Component Dependency Relationship element ---
CREATE TABLE IF NOT EXISTS sbom_dependencies (
    id                  TEXT    PRIMARY KEY,
    sbom_record_id      INTEGER NOT NULL REFERENCES sbom_records(id),
    parent_component_id TEXT    NOT NULL REFERENCES sbom_components(id),
    child_component_id  TEXT    NOT NULL REFERENCES sbom_components(id),
    relationship_type   TEXT    NOT NULL DEFAULT 'depends_on',
    scope               TEXT,
    classification      TEXT    NOT NULL DEFAULT 'CUI',
    tenant_id           TEXT,
    created_at          TEXT    DEFAULT (now()::text),
    UNIQUE (sbom_record_id, parent_component_id, child_component_id, relationship_type)
);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_record ON sbom_dependencies(sbom_record_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_parent ON sbom_dependencies(parent_component_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_child  ON sbom_dependencies(child_component_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_tenant ON sbom_dependencies(tenant_id);

-- @all
