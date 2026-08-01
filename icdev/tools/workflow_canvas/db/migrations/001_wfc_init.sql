-- CUI // SP-CTI
-- Workflow Forms Canvas (WFC) — initial migration
-- Tables: wfc_branding
-- (wfc_workflow_form_nodes was removed in cnr-wfc-03 — the form-intake HITL
--  gate it backed was never registered or enforced; per-step form linkage is
--  persisted in studio_workflows.template_yaml instead.)

CREATE TABLE IF NOT EXISTS wfc_branding (
    id               TEXT PRIMARY KEY,
    entity_type      TEXT NOT NULL CHECK(entity_type IN ('form','workflow')),
    entity_id        TEXT NOT NULL,
    org_name         TEXT,
    logo_data        TEXT,
    primary_color    TEXT DEFAULT '#1a365d',
    secondary_color  TEXT DEFAULT '#c8a951',
    header_html      TEXT,
    footer_html      TEXT,
    show_classification INTEGER DEFAULT 1,
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_wfc_branding_entity ON wfc_branding(entity_type, entity_id);
