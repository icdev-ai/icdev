-- Migration: 20260902205902_asset_identity
-- CUI // SP-CTI
--
-- rmf-ident-01 -- ONE canonical asset identity across the three stacks that
-- each key an asset differently and none of which can be joined to another:
--
--   DoD 7-pillar ZTA   zta_maturity_scores / zta_posture_evidence  -> project_id
--   NSA ZIG            zig_device_registry                        -> sha256(hostname)[:16]
--   NDC / PVM          ni_devices.id, nc_attack_surface.device_name
--
-- There was no path from a DISCOVERED device to a ZT decision to an
-- attack-surface row to an enclave. This table is that path: one row per
-- physical asset, carrying a RESOLVER onto each stack's own key. It replaces
-- no key and rewrites no stack -- each keeps writing what it always wrote.
--
-- Deliberately NOT append-only. An asset is a THING with a current state, so
-- re-observing it upserts (last_seen, discovery_sources, corroboration_tier)
-- rather than appending a second identity for the same machine. The
-- append-only record of what was OBSERVED is each stack's own event table
-- (zig_nac_events, zig_device_attestations, nc_vuln_scans), untouched here.

CREATE TABLE IF NOT EXISTS asset_identity (
    -- 'ai-' || sha256(fabric_key)[:16]. Derived from the fabric key, so the
    -- same asset re-discovered by a different source lands on the same id.
    asset_id            TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL DEFAULT 'default',
    -- The RLS LABEL ('cui'), NEVER a banner -- a banner matches no clearance
    -- at any level, so the row would be written, retained and invisible.
    classification      TEXT NOT NULL DEFAULT 'cui',
    -- HOW that classification was arrived at. An OUI-derived label is weaker
    -- evidence than a human-confirmed one, and an RMF package that cannot
    -- tell them apart cannot say which of its assets have been adjudicated.
    --   rule            a declared rule assigned it (enclave membership, subnet)
    --   oui             inferred from the MAC OUI -> vendor
    --   model           inferred from a vendor/model match
    --   human_confirmed a person adjudicated it
    -- NULL means NOT CLASSIFIED BY ANYTHING -- never read that as 'rule'.
    classification_method TEXT
        CHECK (classification_method IS NULL OR classification_method IN
               ('rule', 'oui', 'model', 'human_confirmed')),

    -- The NATURAL key two sources must agree on to be the same asset:
    -- normalised MAC, else lowercased FQDN, else management IP. UNIQUE, so a
    -- second discovery of the same machine can only ever upsert.
    fabric_key          TEXT NOT NULL,

    hostname            TEXT,
    mgmt_ip             TEXT,
    mac_address         TEXT,
    os_platform         TEXT,
    device_type         TEXT,
    vendor              TEXT,
    model               TEXT,

    -- ---- resolvers onto each existing stack's own key --------------------
    -- Every one is NULLABLE and NULL means "this stack has never seen this
    -- asset", which is a finding in its own right and must not read as a
    -- resolution failure.
    zig_device_id       TEXT,   -- zig_device_registry.device_id
    ni_device_id        TEXT,   -- ni_devices.id
    ni_node_id          TEXT,   -- ni_devices.node_id / nc_vuln_hosts.node_id
    zta_project_id      TEXT,   -- zta_maturity_scores.project_id
    -- nc_attack_surface keys on device_name, not on an id. Recorded as its
    -- own resolver rather than assumed equal to hostname, because the PVM
    -- mapper writes whatever the NQE inventory called the device.
    surface_device_name TEXT,
    -- nc_boundaries.id -- the enclave. The last hop of the join the card
    -- names, and the reason classification_method matters.
    enclave_id          TEXT,

    -- JSON array of source names that have ever reported this asset
    -- ('ni_devices', 'zig_device_registry', 'nc_vuln_hosts', ...).
    discovery_sources   TEXT NOT NULL DEFAULT '[]',
    -- Derived from the DISTINCT source count, never from a row count:
    --   unconfirmed   0 sources (a hand-seeded placeholder)
    --   single_source 1
    --   corroborated  2+
    --   authoritative a human confirmed it
    -- Repetition by ONE source is not corroboration.
    corroboration_tier  TEXT NOT NULL DEFAULT 'unconfirmed',

    first_seen          TIMESTAMP,
    last_seen           TIMESTAMP,
    created_at          TIMESTAMP,
    updated_at          TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_identity_fabric_key
    ON asset_identity (tenant_id, fabric_key);
CREATE INDEX IF NOT EXISTS idx_asset_identity_hostname
    ON asset_identity (hostname);
CREATE INDEX IF NOT EXISTS idx_asset_identity_zig
    ON asset_identity (zig_device_id);
CREATE INDEX IF NOT EXISTS idx_asset_identity_ni
    ON asset_identity (ni_device_id);
CREATE INDEX IF NOT EXISTS idx_asset_identity_surface
    ON asset_identity (surface_device_name);
CREATE INDEX IF NOT EXISTS idx_asset_identity_enclave
    ON asset_identity (enclave_id);
