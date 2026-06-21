-- CUI // SP-CTI
-- Migration 163: User Groups + Canvas Access Grants
-- DoD/IC access control remediation (G-01, G-02, G-21)
-- NIST 800-53: AC-3, AC-6, AC-16, AU-2

-- ============================================================
-- USER GROUPS
-- ============================================================
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI'
        CHECK (classification IN ('PUBLIC', 'CUI', 'ECI', 'SECRET', 'TOP SECRET', 'TOP SECRET//SCI')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled'))
);

CREATE INDEX IF NOT EXISTS idx_groups_tenant ON groups (tenant_id);

-- ============================================================
-- GROUP MEMBERSHIP
-- ============================================================
CREATE TABLE IF NOT EXISTS group_members (
    group_id TEXT NOT NULL REFERENCES groups(id),
    user_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    added_by TEXT,
    PRIMARY KEY (group_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members (user_id);

-- ============================================================
-- GROUP ROLES  (scoped to a canvas_name or NULL = all canvases)
-- ============================================================
CREATE TABLE IF NOT EXISTS group_roles (
    group_id TEXT NOT NULL REFERENCES groups(id),
    role TEXT NOT NULL,
    canvas_scope TEXT,   -- NULL = applies to all canvases
    granted_at TEXT NOT NULL,
    granted_by TEXT,
    PRIMARY KEY (group_id, role, canvas_scope)
);

-- ============================================================
-- CANVAS ACCESS GRANTS
-- Explicit deny-all default: a principal must have a row here
-- to access any canvas or child app.
-- NIST AC-3: least privilege; ZTA Pillar 5: data access control
-- ============================================================
CREATE TABLE IF NOT EXISTS canvas_access_grants (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    principal_type TEXT NOT NULL
        CHECK (principal_type IN ('user', 'group', 'role')),
    principal_id TEXT NOT NULL,  -- user_id, group_id, or role name
    canvas_name TEXT NOT NULL,   -- see args/canvas_registry.yaml
    access_level TEXT NOT NULL
        CHECK (access_level IN ('read', 'write', 'admin')),
    granted_by TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT,             -- NULL = never expires
    revoked_at TEXT,             -- NULL = still active
    UNIQUE (tenant_id, principal_type, principal_id, canvas_name)
);

CREATE INDEX IF NOT EXISTS idx_cag_tenant_canvas ON canvas_access_grants (tenant_id, canvas_name);
CREATE INDEX IF NOT EXISTS idx_cag_principal ON canvas_access_grants (principal_type, principal_id);

-- ============================================================
-- LAC LABEL COLUMNS  (G-03: Label-Based Access Control)
-- lac_label = NULL means world-readable within tenant+classification
-- Prefixed with LAC_ (e.g. LAC_DC_EAST, LAC_NOFORN)
-- ============================================================
ALTER TABLE projects    ADD COLUMN IF NOT EXISTS lac_label TEXT DEFAULT NULL;
ALTER TABLE a2a_tasks   ADD COLUMN IF NOT EXISTS lac_label TEXT DEFAULT NULL;

-- ============================================================
-- COI TAG COLUMNS  (G-09: Community of Interest, Phase 2)
-- coi_tag = NULL means accessible to all within tenant+classification
-- Prefixed with COI_ (e.g. COI_FINANCE, COI_INTEL)
-- ============================================================
ALTER TABLE projects    ADD COLUMN IF NOT EXISTS coi_tag TEXT DEFAULT NULL;
ALTER TABLE a2a_tasks   ADD COLUMN IF NOT EXISTS coi_tag TEXT DEFAULT NULL;
