-- CUI // SP-CTI
-- Migration 173: Create documents table
-- Addresses the missing CREATE TABLE for 'documents' referenced by migration 166
-- (tenant_id NOT NULL enforcement, triggers) and expected by the ICDEV™ schema.

-- @sqlite-only
CREATE TABLE IF NOT EXISTS documents (
    id             TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI'
                       CHECK (classification IN ('PUBLIC', 'CUI', 'ECI', 'SECRET', 'TOP SECRET', 'TOP SECRET//SCI')),
    status         TEXT DEFAULT 'active'
                       CHECK (status IN ('active', 'archived', 'deleted')),
    file_path      TEXT,
    mime_type      TEXT,
    file_hash      TEXT,
    created_by     TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents (tenant_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents (status);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents (created_at DESC);

-- @pg-only
CREATE TABLE IF NOT EXISTS documents (
    id             TEXT PRIMARY KEY,
    tenant_id      TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI'
                       CHECK (classification IN ('PUBLIC', 'CUI', 'ECI', 'SECRET', 'TOP SECRET', 'TOP SECRET//SCI')),
    status         TEXT DEFAULT 'active'
                       CHECK (status IN ('active', 'archived', 'deleted')),
    file_path      TEXT,
    mime_type      TEXT,
    file_hash      TEXT,
    created_by     TEXT,
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    updated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant ON documents (tenant_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents (status);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents (created_at DESC);
