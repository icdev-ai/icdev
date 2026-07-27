-- Migration 242: Aggregation Guard audit trail (prop-sec-05)
-- Append-only log of every classification-by-compilation / mosaic-effect
-- rule evaluation. NEVER UPDATE/DELETE rows (NIST AU) — see
-- .claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES.
-- Schema matches tools/db/schema/pg_consolidated.sql's aggregation_events
-- definition (already present there for fresh-bootstrap PG, but not yet
-- applied to incrementally-migrated databases).

CREATE TABLE IF NOT EXISTS aggregation_events (
    id TEXT PRIMARY KEY,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id TEXT,
    tenant_id TEXT,
    surface TEXT,
    rule_name TEXT,
    derived_classification TEXT NOT NULL,
    surface_ceiling TEXT,
    action TEXT NOT NULL DEFAULT 'derive' CHECK (action IN ('derive', 'warn', 'block')),
    element_summary TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_aggregation_events_action ON aggregation_events(action);
CREATE INDEX IF NOT EXISTS idx_aggregation_events_occurred ON aggregation_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_aggregation_events_rule ON aggregation_events(rule_name);
CREATE INDEX IF NOT EXISTS idx_aggregation_events_tenant ON aggregation_events(tenant_id);
CREATE INDEX IF NOT EXISTS idx_aggregation_events_user ON aggregation_events(user_id);

-- Mutable document-level review state (prop-sec-07). One row per fired rule
-- per document. The resolution column transitions from NULL (open, blocking) to
-- 'override' once a human clears it. NOT append-only — mirrors the
-- redaction_registry (mutable) vs redaction_audit (append-only) split
-- already used by the redaction subsystem. Shared by /rfi and /proposals
-- (and future /govcon, /cpmp) via the `surface` + `document_id` pair.
CREATE TABLE IF NOT EXISTS document_aggregation_findings (
    id TEXT PRIMARY KEY,
    surface TEXT NOT NULL,
    document_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    derived_classification TEXT NOT NULL,
    matched_elements TEXT,
    content_signature TEXT NOT NULL,
    resolution TEXT CHECK (resolution IN ('override')),
    resolved_by TEXT,
    resolved_at TEXT,
    resolution_comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agg_findings_doc ON document_aggregation_findings(surface, document_id);
CREATE INDEX IF NOT EXISTS idx_agg_findings_signature ON document_aggregation_findings(content_signature);
