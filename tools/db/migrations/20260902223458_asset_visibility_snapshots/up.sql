-- Migration: 20260902223458_asset_visibility_snapshots
-- CUI // SP-CTI
--
-- rmf-vis-01 -- asset visibility that cannot fabricate a percentage.
--
-- ONE row per (fabric, measurement). Append-only: a snapshot is EVIDENCE of
-- what could be seen at a moment, and an RMF package that can rewrite last
-- month's coverage number has no coverage history at all. Corrections are a
-- NEW snapshot, the same rule the SBOM frequency element states.
--
-- THE COLUMN THAT MATTERS IS NULLABLE. `visibility_pct` is NULL whenever no
-- authoritative denominator resolved for the fabric, and NULL is the whole
-- point: nothing here may write 0.0 (which reads as "we see nothing") or
-- 100.0 (which reads as "we see everything") over an estate whose size
-- nobody has declared. args/perfect_score_gate.yaml is ratcheted to 0 for
-- exactly this defect.
--
-- `denominator_source` and `denominator_confidence` travel WITH the number
-- and are NOT NULL whenever `denominator` is, because "43%" derived from a
-- switch's own port count and "43%" against an approved CMDB are different
-- claims and a reader who cannot tell them apart has been misled by a
-- correctly-computed number.

CREATE TABLE IF NOT EXISTS asset_visibility_snapshots (
    snapshot_id             TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL DEFAULT 'default',
    -- The RLS LABEL ('cui'), NEVER a banner -- a banner matches no clearance
    -- at any level, so the row would be written, retained and invisible.
    classification          TEXT NOT NULL DEFAULT 'cui',

    -- The fabric this measurement is ABOUT. Never blended across fabrics: the
    -- fabric whose only source has been unreachable for a month is invisible
    -- inside an average, and it is the one that matters.
    fabric_id               TEXT NOT NULL,
    -- The fabric's own classification LABEL, as declared. NULL when the
    -- fabric is not declared anywhere (an asset attributed to a fabric no
    -- config knows about is a finding, not a fabric with no label).
    fabric_classification   TEXT,
    measured_at             TIMESTAMP NOT NULL,

    -- unmeasurable | not_assessed | assessed
    --   unmeasurable  the identity table is absent/unreadable, or nothing has
    --                 ever been ingested. NEITHER number is a measurement.
    --   not_assessed  assets counted, NO denominator resolved -> pct is NULL.
    --   assessed      both sides present -> pct is a real measured number.
    state                   TEXT NOT NULL,

    -- ---- the numerator, and the depth beside it -------------------------
    -- Distinct assets attributed to this fabric that at least one OBSERVED
    -- source reported. NULL -- never 0 -- when the numerator could not be
    -- measured at all.
    observed_assets         INTEGER,
    -- Distinct (asset, source) PAIRS, never rows. A ZIG scanner that
    -- re-registered one device forty times contributes ONE pair.
    corroboration_pairs     INTEGER,
    -- pairs / observed_assets. The SECOND number the card requires beside
    -- coverage: it needs no denominator, so it is reportable on every
    -- deployment including this one. NULL when observed_assets is 0.
    corroboration_depth     REAL,
    -- {unconfirmed, single_source, corroborated, authoritative} -> count.
    tiers_json              TEXT NOT NULL DEFAULT '{}',

    -- ---- the denominator, and how much it is worth ----------------------
    denominator             INTEGER,
    -- approved_cmdb | ip_allocation_plan | dhcp_scope | derived_if_mib
    denominator_source      TEXT,
    -- A DECLARED PRIOR, not a measurement: high | medium | low | inferred.
    denominator_confidence  TEXT,
    -- What the number COUNTS: assets | addresses | leases | ports. A port
    -- count is not an asset count, and a percentage that hides the unit
    -- invites the reader to treat it as one.
    denominator_unit        TEXT,
    -- The denominator's OWN clock (when the CMDB was approved), kept apart
    -- from measured_at (ours), so stale evidence stays distinguishable.
    denominator_as_of       TIMESTAMP,
    denominator_rank        INTEGER,
    denominator_declared_by TEXT,
    -- Every denominator that resolved and LOST the ranking, verbatim. Kept
    -- rather than merged: two sources that disagree about the size of the
    -- estate is a finding for a human, and averaging them deletes it.
    alternates_json         TEXT NOT NULL DEFAULT '[]',

    -- NULL means NOT ASSESSED. See the header.
    visibility_pct          REAL,
    -- TRUE when observed_assets > denominator. NOT clamped to 100: a
    -- numerator over its denominator means the denominator is wrong or
    -- stale, and clamping hides the one fact worth acting on.
    numerator_exceeds       INTEGER NOT NULL DEFAULT 0,

    -- Assets deliberately kept OUT of the numerator, by reason
    -- ({synthetic: 24, unattributed_source: 3}). Never silently dropped:
    -- an excluded asset that vanishes from the report is indistinguishable
    -- from one that was never discovered.
    excluded_json           TEXT NOT NULL DEFAULT '{}',
    notes                   TEXT,
    created_at              TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_asset_visibility_fabric
    ON asset_visibility_snapshots (fabric_id, measured_at);
CREATE INDEX IF NOT EXISTS idx_asset_visibility_measured
    ON asset_visibility_snapshots (measured_at);
CREATE INDEX IF NOT EXISTS idx_asset_visibility_state
    ON asset_visibility_snapshots (state);
