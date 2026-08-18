-- Migration: 20260817010533_entity_currency
-- CUI // SP-CTI
--
-- cef-fnd-04 — one domain-agnostic store for "is this thing still current?".
--
-- WHAT WAS SCATTERED
--
-- The platform already answers currency questions, three times, each answer
-- shaped by the provider that produced it and readable only by the subsystem
-- that owns it:
--
--   docmod_eol_products    110 rows — endoflife.date product/cycle pairs. A
--                          genuinely working external path (105 of the 110 are
--                          synced, 5 seeded). endoflife.date knows SOFTWARE
--                          RELEASE CYCLES and nothing else: it has no opinion on
--                          a hardware chassis or on a protocol version.
--   mc_net_eol_data        101 rows — vendor + model_pattern EOL/EOS/EOSM for
--                          network hardware, carried on a completely different
--                          shape (a match PATTERN, not a product/cycle pair).
--   docmod_catalog_entries  19 rows — the curated, AUTHORITATIVE catalog, whose
--                          currency signal is a `status` word (approved /
--                          deprecated / retired) and not a date at all.
--
-- Nothing can ask all three at once, so "what does the platform know about X"
-- has no answer, and a fourth provider (an OS vendor feed, an internal
-- deprecation register, a standards body) has nowhere to write.
--
-- WHAT THIS TABLE IS
--
-- One row per (source, entity, version) ASSERTION. It is deliberately NOT a
-- resolved per-entity answer: two sources may disagree, and squashing them at
-- write time destroys the disagreement, which is the one thing a caller most
-- needs to see. Resolution is a read-time policy in
-- tools/currency/entity_currency.py::resolve(), where it can be changed without
-- rewriting history.
--
-- DOMAIN-AGNOSTIC MEANS THE COLUMNS NAME NO DOMAIN
--
-- `entity_type` and `namespace` are OPEN vocabularies supplied by the source:
-- entity_type is what kind of thing this is ("software_release",
-- "hardware_model", "protocol_version", or whatever a future provider brings),
-- namespace is the naming authority the key is unique within (a vendor, a
-- publisher, a standards body, a registry) or '' when the source has none. No
-- vendor, product, protocol or industry appears anywhere in this DDL, in
-- tools/currency/, or in args/entity_currency.yaml's mapping rules — the source
-- tables carry those names as DATA and this table stores them as DATA.
--
-- `verdict` is the one CLOSED vocabulary, because a caller has to branch on it.
-- It is validated by VERDICTS in tools/currency/entity_currency.py and NOT by a
-- CHECK constraint here — same call migrations 20260803002224, 20260809203855,
-- 20260815063941 and 20260816122036 made: a CHECK is a second copy of a
-- vocabulary and it drifts the first time a verdict is added.
--
-- `as_of` IS THE SOURCE'S CLOCK, `observed_at` IS OURS
--
-- Kept apart on purpose. A feed synced today can be asserting a fact it last
-- reviewed a year ago; collapsing the two into one timestamp makes stale
-- evidence indistinguishable from fresh evidence, which is exactly the failure
-- this store exists to make visible. Recency comparison uses `as_of`.
--
-- `confidence` IS A DECLARED PRIOR, NOT A MEASUREMENT
--
-- Stated plainly so no reader mistakes it for one. It is the per-source constant
-- from args/entity_currency.yaml, lowered by a declared amount when the source
-- asserts no currency signal at all. It ranks sources against each other; it is
-- not a calibrated probability and nothing in this repo has measured it.
--
-- PROVENANCE IS A POINTER, NOT A COPY
--
-- provenance_table + provenance_id address the exact row this assertion was
-- derived from, so any record here is re-derivable and refutable at its origin.
-- provenance_json carries the raw source fields for the case where the origin
-- row has since been overwritten (docmod_eol_products and mc_net_eol_data are
-- both MUTABLE upsert caches, so that case is routine, not hypothetical).
--
-- MUTABLE, NOT APPEND-ONLY
--
-- A re-run of the same source over the same entity UPDATEs its row rather than
-- growing a second one; the UNIQUE index below is what makes the backfill
-- idempotent. Deliberately not registered in APPEND_ONLY_TABLES: this is a
-- refreshable cache of external assertions, the same class of thing as
-- docmod_eol_products, not an audit record.
--
-- NO JSON SQL
--
-- provenance_json is TEXT and is parsed in Python. Nothing filters, groups or
-- exists-checks inside it, so there is no json_extract / jsonb branch to keep
-- portable (CLAUDE.md: compute in Python).

CREATE TABLE IF NOT EXISTS entity_currency (
    record_id       TEXT PRIMARY KEY,
    -- Open vocabulary, supplied by the source. Never enumerated in Python.
    entity_type     TEXT NOT NULL,
    -- Naming authority the key is unique within (vendor / publisher / registry).
    -- '' when the source has none — NOT NULL so the UNIQUE index below cannot be
    -- defeated by a NULL, which never equals itself.
    namespace       TEXT NOT NULL DEFAULT '',
    -- Normalized identity within (entity_type, namespace). Casefolded, collapsed
    -- whitespace — the join key.
    entity_key      TEXT NOT NULL,
    -- The entity as the SOURCE spells it, verbatim. Display and re-derivation.
    entity_label    TEXT,
    -- Release cycle / model revision / protocol version. '' when the assertion
    -- is version-independent (same NOT NULL reason as namespace).
    entity_version  TEXT NOT NULL DEFAULT '',
    -- Closed vocabulary — VERDICTS in tools/currency/entity_currency.py.
    verdict         TEXT NOT NULL,
    -- What replaces it, as the source names it. NULL = the source names nothing.
    superseded_by   TEXT,
    -- Provider id (args/entity_currency.yaml sources.*.id).
    source          TEXT NOT NULL,
    -- How the provider knows: external_feed / curated / inventory / derived.
    source_kind     TEXT NOT NULL DEFAULT 'derived',
    -- The SOURCE's clock: when it asserts this was true. ISO8601 UTC.
    as_of           TEXT NOT NULL,
    -- OUR clock: when this row was written. ISO8601 UTC.
    observed_at     TEXT NOT NULL,
    -- Declared prior in [0,1]. See the note above — not a measurement.
    confidence      REAL NOT NULL DEFAULT 0.0,
    -- Carried through when the source has them, so a caller need not re-open the
    -- origin row to sort by imminence. NULL = the source announced no date.
    eol_date        TEXT,
    eos_date        TEXT,
    provenance_table TEXT,
    provenance_id    TEXT,
    provenance_json  TEXT NOT NULL DEFAULT '{}',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI'
);

-- Idempotency, enforced by the database rather than by the backfill. Re-running
-- a source over the same entity is an UPDATE; a lost race is a constraint
-- violation the writer resolves, never a duplicate assertion.
CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_currency_identity
    ON entity_currency (source, entity_type, namespace, entity_key, entity_version);

-- The dominant read: "everything every source says about this entity", which is
-- resolve() and the disagreement report.
CREATE INDEX IF NOT EXISTS idx_entity_currency_entity
    ON entity_currency (entity_type, entity_key);

-- "What is dying, soonest first" — the sweep read.
CREATE INDEX IF NOT EXISTS idx_entity_currency_verdict
    ON entity_currency (verdict, eol_date);

-- RLS injects `tenant_id = ?` into every SELECT this table serves, so the
-- planner never sees the module's SQL shape. Index the tenant alongside the
-- range column the query actually orders on.
CREATE INDEX IF NOT EXISTS idx_entity_currency_tenant
    ON entity_currency (tenant_id, as_of);

-- "Did this provider ever write, and how recently" — the substrate probe and the
-- per-source freshness report.
CREATE INDEX IF NOT EXISTS idx_entity_currency_source
    ON entity_currency (source, observed_at);
