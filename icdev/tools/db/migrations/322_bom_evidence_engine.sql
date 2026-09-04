-- Migration 322: BOM Evidence Engine.
-- CUI // SP-CTI
--
-- Reconciles a pile of disparate documents (workbooks, decks, PDFs, diagrams)
-- into one bill of materials a person can defend in front of the people who
-- control the money.
--
-- GENERATED from tools/bom/db/init_db.py::SCHEMA_PG — do not hand-edit. The CHECK
-- constraints are derived from the tuples in tools/bom/constants.py, so Python
-- and the database cannot drift apart. Regenerate with:
--     python -m tools.bom.db.emit_migration
--
-- PostgreSQL is the runtime backend and this DDL is authored PG-first. It stays
-- inside the subset SQLite tolerates verbatim (TEXT ids, JSONB, TIMESTAMP,
-- BOOLEAN DEFAULT FALSE, NUMERIC), so the init-fallback applies without leaning
-- on translate_sql, which rewrites %s->? for DML and is never load-bearing here.
--
-- Every table carries tenant_id + classification, so all of these are
-- RLS-governed and use the normal get_connection().
--
-- APPEND-ONLY (registered in APPEND_ONLY_TABLES, .claude/hooks/pre_tool_use.py):
--   bom_match_decisions — a human's merge verdicts. Clusters are a recomputed
--                         projection OVER these; the decisions are the only
--                         durable record of intent. Updating one rewrites history
--                         the customer relied on.
--   bom_audit           — NIST AU trail.
--
-- Idempotent: CREATE TABLE / INDEX IF NOT EXISTS throughout.

CREATE TABLE IF NOT EXISTS bom_projects (
        project_id          TEXT PRIMARY KEY,
        tenant_id           TEXT NOT NULL DEFAULT 'default',
        classification      TEXT NOT NULL DEFAULT 'CUI',
        name                TEXT NOT NULL,
        -- The user's own words about what they are trying to achieve. This is
        -- what the extraction schema and the category taxonomy are derived
        -- FROM, which is why it is a first-class column and not a note.
        intent_text         TEXT NOT NULL DEFAULT '',
        -- Winner-selection is a stored policy, not code, so a customer can
        -- reorder the tiebreakers without a release.
        winner_policy_json  JSONB,
        -- The envelope the ask has to live inside. A BOM that ignores its own
        -- stated budget is a finding, not a surprise at the review.
        budget_floor        NUMERIC(18,4),
        budget_ceiling      NUMERIC(18,4),
        currency            TEXT NOT NULL DEFAULT 'USD',
        active_taxonomy_id  TEXT,
        -- Which architecture baseline is currently selected. NULL means no
        -- choice has been made, and every architecture-scoped option therefore
        -- contributes zero.
        selected_baseline   TEXT,
        status              TEXT NOT NULL DEFAULT 'active',
        created_by          TEXT,
        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

CREATE INDEX IF NOT EXISTS idx_bom_projects_tenant ON bom_projects(tenant_id);

CREATE TABLE IF NOT EXISTS bom_sources (
        source_id             TEXT PRIMARY KEY,
        project_id            TEXT NOT NULL,
        tenant_id             TEXT NOT NULL DEFAULT 'default',
        classification        TEXT NOT NULL DEFAULT 'CUI',
        filename              TEXT NOT NULL,
        content_sha256        TEXT NOT NULL,
        media_type            TEXT NOT NULL DEFAULT '',
        source_version        INTEGER NOT NULL DEFAULT 1,

        role                  TEXT NOT NULL DEFAULT 'bom_claim' CHECK (role IN ('bom_claim', 'inventory_truth', 'quote', 'baseline_architecture', 'narrative', 'diagram', 'derived')),
        role_confidence       REAL NOT NULL DEFAULT 0.0,
        role_set_by           TEXT NOT NULL DEFAULT 'default' CHECK (role_set_by IN ('human', 'ai_proposed', 'default')),

        -- How much this document's word is worth. AI proposes; only a human's
        -- setting is binding (role_set_by / credibility_set_by record which).
        credibility_tier      TEXT NOT NULL DEFAULT 'unknown'
                              CHECK (credibility_tier IN ('authoritative', 'corroborated', 'working', 'draft', 'derived', 'unknown')),
        authority_rank        INTEGER NOT NULL DEFAULT 0,
        credibility_set_by    TEXT NOT NULL DEFAULT 'default'
                              CHECK (credibility_set_by IN ('human', 'ai_proposed', 'default')),
        credibility_rationale TEXT NOT NULL DEFAULT '',
        credibility_signals   JSONB,

        -- Same document, different wrapper. The loser is kept (auditable) but
        -- contributes nothing, because it is the same money.
        derived_from          TEXT,
        content_fingerprint   TEXT,
        representation        TEXT NOT NULL DEFAULT '',
        fidelity              INTEGER NOT NULL DEFAULT 0,

        price_basis_default   TEXT NOT NULL DEFAULT 'unknown'
                              CHECK (price_basis_default IN ('quoted', 'street', 'msrp', 'budgetary', 'rom', 'unknown')),
        as_of_date            TIMESTAMP,
        doc_metadata          JSONB,
        warnings              JSONB,
        created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, content_sha256, source_version)
    );

CREATE INDEX IF NOT EXISTS idx_bom_sources_project ON bom_sources(project_id);

CREATE INDEX IF NOT EXISTS idx_bom_sources_role ON bom_sources(project_id, role);

CREATE INDEX IF NOT EXISTS idx_bom_sources_fingerprint ON bom_sources(content_fingerprint);

CREATE TABLE IF NOT EXISTS bom_cells (
        cell_id        TEXT PRIMARY KEY,
        source_id      TEXT NOT NULL,
        project_id     TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        sheet          TEXT NOT NULL DEFAULT '',
        locator        TEXT NOT NULL,
        row_idx        INTEGER,
        col_idx        INTEGER,
        value_text     TEXT,
        value_num      NUMERIC(18,4),
        formula        TEXT,
        number_format  TEXT,
        UNIQUE (source_id, sheet, locator)
    );

CREATE INDEX IF NOT EXISTS idx_bom_cells_source ON bom_cells(source_id, sheet);

CREATE INDEX IF NOT EXISTS idx_bom_cells_formula ON bom_cells(source_id);

CREATE TABLE IF NOT EXISTS bom_rollup_edges (
        edge_id          TEXT PRIMARY KEY,
        source_id        TEXT NOT NULL,
        project_id       TEXT NOT NULL,
        tenant_id        TEXT NOT NULL DEFAULT 'default',
        classification   TEXT NOT NULL DEFAULT 'CUI',
        target_cell_id   TEXT NOT NULL,
        consumed_cell_id TEXT NOT NULL,
        fn               TEXT NOT NULL DEFAULT 'SUM',
        is_hardcoded     BOOLEAN NOT NULL DEFAULT FALSE
    );

CREATE INDEX IF NOT EXISTS idx_bom_rollup_target ON bom_rollup_edges(target_cell_id);

CREATE INDEX IF NOT EXISTS idx_bom_rollup_consumed ON bom_rollup_edges(consumed_cell_id);

CREATE TABLE IF NOT EXISTS bom_lines (
        line_id            TEXT PRIMARY KEY,
        -- sha256(source_id, source_locator, raw_text). Hashes ONLY inputs that
        -- no parser change can alter: a human's merge approval is keyed on this
        -- pair, and if improving the parser rewrote the hash we would silently
        -- orphan every decision the customer ever made.
        line_hash          TEXT NOT NULL,
        project_id         TEXT NOT NULL,
        source_id          TEXT NOT NULL,
        tenant_id          TEXT NOT NULL DEFAULT 'default',
        classification     TEXT NOT NULL DEFAULT 'CUI',

        source_document    TEXT NOT NULL DEFAULT '',
        source_sheet       TEXT NOT NULL DEFAULT '',
        source_locator     TEXT NOT NULL DEFAULT '',
        raw_text           TEXT NOT NULL DEFAULT '',

        description_raw    TEXT NOT NULL DEFAULT '',
        description_norm   TEXT NOT NULL DEFAULT '',
        manufacturer_raw   TEXT NOT NULL DEFAULT '',
        manufacturer_norm  TEXT NOT NULL DEFAULT '',
        part_number_raw    TEXT,
        part_number_norm   TEXT,
        -- What the thing DOES. The only key that survives a source with no part
        -- numbers at all, and the one that lets us notice that a $10,500
        -- firewall and a $200,000 firewall are competing for the same job.
        function_slug      TEXT NOT NULL DEFAULT '',

        category_id        TEXT,
        category_confidence REAL NOT NULL DEFAULT 0.0,

        qty                NUMERIC(18,4),
        uom                TEXT NOT NULL DEFAULT '',
        unit_price         NUMERIC(18,4),
        extended_price     NUMERIC(18,4),
        computed_extended  NUMERIC(18,4),
        discount_pct       NUMERIC(9,6),

        price_basis        TEXT NOT NULL DEFAULT 'unknown'
                           CHECK (price_basis IN ('quoted', 'street', 'msrp', 'budgetary', 'rom', 'unknown')),
        -- A line that looks costed and costs nothing: qty 1, price never filled
        -- in, and the formula multiplies it politely to zero.
        price_missing      BOOLEAN NOT NULL DEFAULT FALSE,

        source_formula     TEXT,
        value_is_formula   BOOLEAN NOT NULL DEFAULT FALSE,

        is_recurring       BOOLEAN NOT NULL DEFAULT FALSE,
        recurrence_period  TEXT NOT NULL DEFAULT 'one_time'
                           CHECK (recurrence_period IN ('one_time', 'month', 'quarter', 'year')),
        term_months        INTEGER,
        cost_type          TEXT NOT NULL DEFAULT 'unknown' CHECK (cost_type IN ('capex', 'opex', 'unknown')),

        option_group_id    TEXT,
        option_label       TEXT,
        -- An option nobody has chosen contributes ZERO. Not the cheapest, not
        -- the mean, not the one that happens to be listed first.
        is_alternative     BOOLEAN NOT NULL DEFAULT FALSE,

        -- ── When, and what it frees up ───────────────────────────────────────
        -- A BOM sorted only by cost buries its own best news. The twelve servers
        -- already sitting in the lab cost nothing and are the reason the team can
        -- start building on Monday instead of waiting a year for a facility —
        -- and on a cost-sorted table they are the last row.
        wave_label         TEXT NOT NULL DEFAULT '',
        wave_order         INTEGER NOT NULL DEFAULT 100,
        -- The business outcome this spend unlocks, in the customer's words.
        -- Free text on purpose: "brings the platform in-house so the team can
        -- start coding" is not a value any enum of mine would have held.
        unblocks           TEXT NOT NULL DEFAULT '',
        -- Long-lead items gate everything behind them. A six-week server order
        -- placed in week one is the difference between a plan and a slip.
        lead_time_days     INTEGER,
        needed_by          TIMESTAMP,
        -- TRUE when this line removes a dependency from the critical path. These
        -- get surfaced as OPPORTUNITIES, not buried as cheap rows.
        is_enabler         BOOLEAN NOT NULL DEFAULT FALSE,

        -- Was this line read out of a document, or did we compute it? A refresh
        -- reserve is a legitimate — often the most important — ask, but it is
        -- OUR arithmetic, not a source's claim, and it says so.
        line_kind          TEXT NOT NULL DEFAULT 'extracted'
                           CHECK (line_kind IN ('extracted', 'reserve', 'contingency', 'placeholder')),
        -- For computed lines: how we got here, in a form a reviewer can check.
        -- Names the counts, the unit price and the source cell each came from,
        -- so a reserve can be argued with rather than merely believed.
        derivation         TEXT NOT NULL DEFAULT '',

        -- ── Kit we already own ───────────────────────────────────────────────
        -- Repurposing is avoided CapEx and belongs on the slide as a win. The
        -- catch is that a reserve is only as right as the count it was sized
        -- from, so claimed and verified are tracked separately and never
        -- conflated.
        existing_asset     BOOLEAN NOT NULL DEFAULT FALSE,
        asset_disposition  TEXT NOT NULL DEFAULT 'unknown'
                           CHECK (asset_disposition IN ('repurpose', 'refresh_reserve', 'replace_now', 'retire', 'unknown')),
        claimed_qty        NUMERIC(18,4),   -- what the BOM/design leans on
        verified_qty       NUMERIC(18,4),   -- what the serials actually prove
        warranty_end       TIMESTAMP,
        -- What it would cost to replace the VERIFIED units when they fail. This
        -- is the number the refresh reserve is built from — and it is sourced
        -- from a real priced line elsewhere in the corpus, or it stays NULL and
        -- raises no_replacement_price_basis. We do not invent it.
        replacement_unit_price NUMERIC(18,4),
        refresh_reserve_usd    NUMERIC(18,4),
        -- Avoided CapEx: verified_qty * replacement_unit_price. The value of the
        -- fleet you are contributing, which is worth saying out loud.
        avoided_capex_usd      NUMERIC(18,4),

        cluster_id         TEXT,
        role_in_cluster    TEXT CHECK (role_in_cluster IN ('winner', 'duplicate', 'variant', 'excluded', '')),

        confidence         REAL NOT NULL DEFAULT 1.0,
        status             TEXT NOT NULL DEFAULT 'parsed'
                           CHECK (status IN ('parsed', 'clustered', 'pending_review', 'accepted', 'overridden', 'rejected', 'suppressed')),
        suppressed_by      TEXT,
        superseded_by      TEXT,
        notes              TEXT NOT NULL DEFAULT '',
        created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, line_hash)
    );

CREATE INDEX IF NOT EXISTS idx_bom_lines_project ON bom_lines(project_id);

CREATE INDEX IF NOT EXISTS idx_bom_lines_function ON bom_lines(project_id, function_slug);

CREATE INDEX IF NOT EXISTS idx_bom_lines_part ON bom_lines(part_number_norm);

CREATE INDEX IF NOT EXISTS idx_bom_lines_cluster ON bom_lines(cluster_id);

CREATE INDEX IF NOT EXISTS idx_bom_lines_source ON bom_lines(source_id);

CREATE TABLE IF NOT EXISTS bom_line_blocks (
        block_id       TEXT PRIMARY KEY,
        project_id     TEXT NOT NULL,
        line_id        TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        block_key      TEXT NOT NULL,
        block_type     TEXT NOT NULL
    );

CREATE INDEX IF NOT EXISTS idx_bom_blocks_key ON bom_line_blocks(project_id, block_key);

CREATE TABLE IF NOT EXISTS bom_architecture_components (
        component_id     TEXT PRIMARY KEY,
        project_id       TEXT NOT NULL,
        source_id        TEXT NOT NULL,
        tenant_id        TEXT NOT NULL DEFAULT 'default',
        classification   TEXT NOT NULL DEFAULT 'CUI',
        baseline_label   TEXT NOT NULL DEFAULT '',
        node_id          TEXT NOT NULL DEFAULT '',
        label            TEXT NOT NULL DEFAULT '',
        label_norm       TEXT NOT NULL DEFAULT '',
        function_slug    TEXT NOT NULL DEFAULT '',
        zone             TEXT NOT NULL DEFAULT '',
        shape            TEXT NOT NULL DEFAULT '',
        -- A rack elevation that draws twelve of something is CLAIMING twelve of
        -- something. It is not evidence that twelve exist.
        claimed_qty      NUMERIC(18,4) NOT NULL DEFAULT 1,
        locator          TEXT NOT NULL DEFAULT '',
        raw_text         TEXT NOT NULL DEFAULT ''
    );

CREATE INDEX IF NOT EXISTS idx_bom_arch_project ON bom_architecture_components(project_id, baseline_label);

CREATE INDEX IF NOT EXISTS idx_bom_arch_function ON bom_architecture_components(project_id, function_slug);

CREATE TABLE IF NOT EXISTS bom_scope_items (
        scope_id       TEXT PRIMARY KEY,
        project_id     TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        label          TEXT NOT NULL,
        description    TEXT NOT NULL DEFAULT '',
        -- Free text, in the customer's words. "Simulate the customer network in
        -- GNS3" is a capability; no enum I could write would have held it, and
        -- inventing one would be the hardcoding this engine exists to avoid.
        capabilities   JSONB,
        status         TEXT NOT NULL DEFAULT 'declared'
                       CHECK (status IN ('declared', 'designed', 'priced', 'funded', 'deferred')),
        wave_label     TEXT NOT NULL DEFAULT '',
        wave_order     INTEGER NOT NULL DEFAULT 100,
        -- Where the declaration came from: the user typed it, or it was read out
        -- of their stated intent. Never inferred from a price.
        declared_by    TEXT NOT NULL DEFAULT '',
        -- Rolled up from covering lines. NULL means genuinely unpriced, and we
        -- print NULL rather than a guess.
        priced_total   NUMERIC(18,4),
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

CREATE INDEX IF NOT EXISTS idx_bom_scope_project ON bom_scope_items(project_id, status);

CREATE TABLE IF NOT EXISTS bom_scope_coverage (
        id             TEXT PRIMARY KEY,
        scope_id       TEXT NOT NULL,
        project_id     TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        line_id        TEXT,
        component_id   TEXT,
        matched_by     TEXT NOT NULL DEFAULT '',
        confidence     REAL NOT NULL DEFAULT 0.0
    );

CREATE INDEX IF NOT EXISTS idx_bom_scopecov_scope ON bom_scope_coverage(scope_id);

CREATE TABLE IF NOT EXISTS bom_coverage (
        coverage_id    TEXT PRIMARY KEY,
        project_id     TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        baseline_label TEXT NOT NULL DEFAULT '',
        component_id   TEXT,
        line_id        TEXT,
        direction      TEXT NOT NULL,
        matched_by     TEXT NOT NULL DEFAULT '',
        confidence     REAL NOT NULL DEFAULT 0.0
    );

CREATE INDEX IF NOT EXISTS idx_bom_coverage_project ON bom_coverage(project_id, baseline_label);

CREATE TABLE IF NOT EXISTS bom_option_groups (
        group_id             TEXT PRIMARY KEY,
        project_id           TEXT NOT NULL,
        tenant_id            TEXT NOT NULL DEFAULT 'default',
        classification       TEXT NOT NULL DEFAULT 'CUI',
        label                TEXT NOT NULL DEFAULT '',
        scope                TEXT NOT NULL DEFAULT 'line' CHECK (scope IN ('line', 'table', 'architecture')),
        detected_by          TEXT NOT NULL DEFAULT 'human' CHECK (detected_by IN ('heading', 'versus', 'structural', 'baseline', 'human')),
        -- NULL means nobody has decided. Which means this group is worth $0,
        -- and the deck says so out loud, with the range.
        selected_option_label TEXT,
        selected_by          TEXT,
        selected_at          TIMESTAMP,
        min_total            NUMERIC(18,4),
        max_total            NUMERIC(18,4),
        rationale            TEXT NOT NULL DEFAULT '',
        created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

CREATE INDEX IF NOT EXISTS idx_bom_optgroups_project ON bom_option_groups(project_id);

CREATE TABLE IF NOT EXISTS bom_option_members (
        member_id      TEXT PRIMARY KEY,
        group_id       TEXT NOT NULL,
        project_id     TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        option_label   TEXT NOT NULL,
        line_id        TEXT,
        source_id      TEXT
    );

CREATE INDEX IF NOT EXISTS idx_bom_optmembers_group ON bom_option_members(group_id);

CREATE TABLE IF NOT EXISTS bom_taxonomy_versions (
        taxonomy_id    TEXT PRIMARY KEY,
        project_id     TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        version        INTEGER NOT NULL DEFAULT 1,
        status         TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'superseded')),
        derived_from_intent TEXT NOT NULL DEFAULT '',
        approved_by    TEXT,
        approved_at    TIMESTAMP,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

CREATE TABLE IF NOT EXISTS bom_taxonomy_nodes (
        node_id        TEXT PRIMARY KEY,
        taxonomy_id    TEXT NOT NULL,
        project_id     TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        parent_node_id TEXT,
        label          TEXT NOT NULL,
        definition     TEXT NOT NULL DEFAULT '',
        sort_order     INTEGER NOT NULL DEFAULT 0
    );

CREATE INDEX IF NOT EXISTS idx_bom_taxnodes_tax ON bom_taxonomy_nodes(taxonomy_id);

CREATE TABLE IF NOT EXISTS bom_inventory_assets (
        asset_id          TEXT PRIMARY KEY,
        project_id        TEXT NOT NULL,
        source_id         TEXT NOT NULL,
        tenant_id         TEXT NOT NULL DEFAULT 'default',
        classification    TEXT NOT NULL DEFAULT 'CUI',
        serial            TEXT NOT NULL,
        manufacturer_norm TEXT NOT NULL DEFAULT '',
        model_raw         TEXT NOT NULL DEFAULT '',
        model_key         TEXT NOT NULL DEFAULT '',
        warranty_end      TIMESTAMP,
        locator           TEXT NOT NULL DEFAULT '',
        raw_text          TEXT NOT NULL DEFAULT '',
        UNIQUE (source_id, serial)
    );

CREATE INDEX IF NOT EXISTS idx_bom_inv_model ON bom_inventory_assets(project_id, model_key);

CREATE TABLE IF NOT EXISTS bom_clusters (
        cluster_id        TEXT PRIMARY KEY,
        project_id        TEXT NOT NULL,
        tenant_id         TEXT NOT NULL DEFAULT 'default',
        classification    TEXT NOT NULL DEFAULT 'CUI',
        function_slug     TEXT NOT NULL DEFAULT '',
        winner_line_id    TEXT,
        qty_policy        TEXT NOT NULL DEFAULT 'max',
        resolved_qty      NUMERIC(18,4),
        resolved_unit_price NUMERIC(18,4),
        resolved_price_basis TEXT NOT NULL DEFAULT 'unknown'
                          CHECK (resolved_price_basis IN ('quoted', 'street', 'msrp', 'budgetary', 'rom', 'unknown')),
        -- When the same job is contested by genuinely different products, we do
        -- not average them into a fiction. We carry the range and we say a human
        -- has to choose.
        price_min         NUMERIC(18,4),
        price_max         NUMERIC(18,4),
        status            TEXT NOT NULL DEFAULT 'pending_review'
                          CHECK (status IN ('parsed', 'clustered', 'pending_review', 'accepted', 'overridden', 'rejected', 'suppressed')),
        match_confidence  REAL NOT NULL DEFAULT 0.0,
        rationale         TEXT NOT NULL DEFAULT '',
        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

CREATE INDEX IF NOT EXISTS idx_bom_clusters_project ON bom_clusters(project_id);

CREATE TABLE IF NOT EXISTS bom_cluster_members (
        cluster_id       TEXT NOT NULL,
        line_id          TEXT NOT NULL,
        project_id       TEXT NOT NULL,
        tenant_id        TEXT NOT NULL DEFAULT 'default',
        classification   TEXT NOT NULL DEFAULT 'CUI',
        role             TEXT NOT NULL DEFAULT 'duplicate' CHECK (role IN ('winner', 'duplicate', 'variant', 'excluded')),
        match_confidence REAL NOT NULL DEFAULT 0.0,
        matched_by       TEXT NOT NULL DEFAULT 'exact_part' CHECK (matched_by IN ('exact_part', 'trigram', 'function', 'llm', 'human')),
        PRIMARY KEY (cluster_id, line_id)
    );

CREATE TABLE IF NOT EXISTS bom_match_decisions (
        decision_id       TEXT PRIMARY KEY,
        project_id        TEXT NOT NULL,
        tenant_id         TEXT NOT NULL DEFAULT 'default',
        classification    TEXT NOT NULL DEFAULT 'CUI',
        pair_key          TEXT NOT NULL,
        a_line_hash       TEXT NOT NULL,
        b_line_hash       TEXT NOT NULL,
        verdict           TEXT NOT NULL CHECK (verdict IN ('same_item', 'same_function_different_item', 'alternative_of', 'component_of', 'different', 'insufficient_evidence')),
        canonical_line_hash TEXT,
        confidence        REAL NOT NULL DEFAULT 0.0,
        decided_by        TEXT NOT NULL DEFAULT 'llm' CHECK (decided_by IN ('human', 'llm', 'deterministic')),
        decided_by_user   TEXT,
        reason            TEXT NOT NULL DEFAULT '',
        llm_proposal_json JSONB,
        decided_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

CREATE INDEX IF NOT EXISTS idx_bom_decisions_pair ON bom_match_decisions(project_id, pair_key);

CREATE TABLE IF NOT EXISTS bom_findings (
        finding_id     TEXT PRIMARY KEY,
        project_id     TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        finding_type   TEXT NOT NULL CHECK (finding_type IN ('intra_doc_double_count', 'hardcoded_rollup', 'stale_rollup', 'unpriced_line_zeroed', 'arithmetic_mismatch', 'price_basis_undeclared', 'price_basis_mismatch', 'capex_opex_conflation', 'duplicate_representation', 'asset_count_disputed', 'inventory_incomplete', 'unbooked_asset', 'unverified_existing_asset', 'unfunded_refresh_reserve', 'no_replacement_price_basis', 'schedule_blocker', 'unblocks_now', 'sequencing_absent', 'unfunded_component', 'unjustified_line', 'scope_declared_unpriced', 'scope_declared_undesigned', 'scope_priced_only_by_weak_source', 'baseline_asset_gap', 'open_decision', 'price_spread', 'authoritative_conflict', 'reopened_decision', 'hidden_content', 'deleted_content', 'unresolved_placeholder', 'budget_variance', 'taxonomy_divergence')),
        -- Not every finding is a defect. A register that only reports problems
        -- gets dreaded and then ignored — and it hides the best news in the
        -- package. "You already own the machines that unblock the team on day
        -- one" belongs in this table, and it is not a bug.
        kind           TEXT NOT NULL DEFAULT 'defect'
                       CHECK (kind IN ('defect', 'risk', 'decision', 'opportunity')),
        severity       TEXT NOT NULL DEFAULT 'medium' CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
        title          TEXT NOT NULL,
        detail         TEXT NOT NULL DEFAULT '',
        -- When a finding is a DECISION rather than a defect, these are the ways
        -- out. The engine offers them; it does not take one. Two sources
        -- disagreeing about how many servers exist is not resolvable by
        -- arithmetic, and pretending otherwise is how a tool tells a room full
        -- of executives something false with total confidence.
        options_json   JSONB,
        -- The load-bearing column. It is what lets the leadership slide sort
        -- discrepancies by money, which is the only ordering an executive has
        -- ever cared about. NULL is allowed and means "real, but we refuse to
        -- invent a number for it".
        impact_usd     NUMERIC(18,4),
        -- Says whether this finding is arithmetic or opinion. The reader is
        -- entitled to know which claims lean on a model.
        detector       TEXT NOT NULL DEFAULT 'deterministic' CHECK (detector IN ('deterministic', 'llm_assisted')),
        -- [{source_document, sheet, locator, raw_text, line_id}] — every finding
        -- points at the exact cell it came from, or it does not ship.
        evidence_json  JSONB,
        disposition    TEXT NOT NULL DEFAULT 'open'
                       CHECK (disposition IN ('open', 'accepted', 'waived', 'fixed', 'not_an_issue')),
        disposed_by    TEXT,
        disposed_at    TIMESTAMP,
        disposition_note TEXT NOT NULL DEFAULT '',
        fingerprint    TEXT NOT NULL DEFAULT '',
        first_seen_snapshot TEXT,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, fingerprint)
    );

CREATE INDEX IF NOT EXISTS idx_bom_findings_project ON bom_findings(project_id, severity, disposition);

CREATE INDEX IF NOT EXISTS idx_bom_findings_type ON bom_findings(project_id, finding_type);

CREATE TABLE IF NOT EXISTS bom_snapshots (
        snapshot_id    TEXT PRIMARY KEY,
        project_id     TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        label          TEXT NOT NULL DEFAULT '',
        content_json   JSONB,
        content_sha256 TEXT NOT NULL,
        totals_json    JSONB,
        exported_at    TIMESTAMP,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

CREATE INDEX IF NOT EXISTS idx_bom_snapshots_project ON bom_snapshots(project_id);

CREATE TABLE IF NOT EXISTS bom_audit (
        audit_id       TEXT PRIMARY KEY,
        project_id     TEXT NOT NULL,
        tenant_id      TEXT NOT NULL DEFAULT 'default',
        classification TEXT NOT NULL DEFAULT 'CUI',
        actor          TEXT NOT NULL DEFAULT '',
        action         TEXT NOT NULL,
        entity_type    TEXT NOT NULL DEFAULT '',
        entity_id      TEXT NOT NULL DEFAULT '',
        before_json    JSONB,
        after_json     JSONB,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

CREATE INDEX IF NOT EXISTS idx_bom_audit_project ON bom_audit(project_id);
