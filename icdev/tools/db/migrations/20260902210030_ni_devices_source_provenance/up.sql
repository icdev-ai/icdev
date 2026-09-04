-- Migration: 20260902210030_ni_devices_source_provenance
-- CUI // SP-CTI
--
-- rmf-disc-02: every ni_devices row records WHERE IT CAME FROM.
--
-- WHY THIS COLUMN EXISTS
--
-- ni_devices is the STRONGEST evidence class the de-facto standard learner
-- reads: args/docmod/inventory_feeds.yaml declares it `evidence_kind:
-- inventory`, precedence 10 -- an observed deployed estate, which outranks
-- every drawing of one. It held 0 rows, and that emptiness was HONEST: no
-- NetBox and no CSV export is reachable from this deployment, so there was
-- nothing observed to record.
--
-- The moment anything writes rows into it, "how many rows" stops being the
-- question and "what KIND of rows" starts. A fabricated demo device and a
-- device an SNMP sweep actually answered from are indistinguishable once both
-- are a row in this table, and the learner would then rank the fabrication as a
-- deployed estate -- the exact laundering inventory_feeds.yaml exists to
-- refuse. Without this column the only way to keep that learner honest is to
-- keep the table empty, which is what has kept every inventory surface on this
-- platform (MDC inventory, NDC EOL scanner, PVM attack surface) reading empty.
--
-- Values written by this repo. Deliberately NOT a CHECK constraint: a new
-- writer must be able to name itself without a migration, and an unrecognised
-- label is strictly better than a writer forced to pick a wrong one.
--   discovery         a scan reached the host and the host answered
--   synthetic         fabricated demo data. NOT evidence of anything, and
--                     excluded by name from the de-facto learner's inventory
--                     feed (args/docmod/inventory_feeds.yaml -> exclude_when)
--   netbox            NetBox sync
--   csv               operator CSV/JSON import
--   topology_ingest   re-ingested from a design diagram -- a DRAWING of an
--                     estate, never an observation of one
--   NULL              written before this migration. UNKNOWN, never assumed.

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only

ALTER TABLE ni_devices ADD COLUMN IF NOT EXISTS source TEXT;

CREATE INDEX IF NOT EXISTS idx_ni_devices_source ON ni_devices(source);
CREATE INDEX IF NOT EXISTS idx_nc_discovery_scans_created ON nc_discovery_scans(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_discovery_diffs_scan ON nc_discovery_diffs(scan_id);

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
--
-- SQLite has no ADD COLUMN IF NOT EXISTS, so this branch is single-shot. That
-- is correct here: schema_migrations records the version and the runner never
-- replays it. `DESC` in an index column list is accepted by SQLite 3.8.3+.

ALTER TABLE ni_devices ADD COLUMN source TEXT;

CREATE INDEX IF NOT EXISTS idx_ni_devices_source ON ni_devices(source);
CREATE INDEX IF NOT EXISTS idx_nc_discovery_scans_created ON nc_discovery_scans(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_discovery_diffs_scan ON nc_discovery_diffs(scan_id);
