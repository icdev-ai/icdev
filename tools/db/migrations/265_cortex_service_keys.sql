-- Migration 265: Cortex service keys — external-caller auth for the Cortex
-- REST/MCP service surface (ctx-expose-02).
-- CUI // SP-CTI
--
-- One row per issued icdev_ctx_ API key. The key row is authoritative for the
-- caller's tenant binding, classification ceiling, and scopes — request bodies
-- can never override them (see tools/cortex/service_keys.py::resolve_context).
--
-- Mutable by design (revocation, last_used_at) — NOT append-only. Key
-- lifecycle events surface through the icdev logger; the governed calls the
-- key authorizes each write their own append-only cortex_audit row.
--
-- No classification column on purpose: key verification runs BEFORE a
-- security context exists (chicken-and-egg), so this table must never be
-- RLS-filtered. Same posture as dashboard_api_keys.
--
-- PG-first DDL; types chosen so the SQLite init-fallback applies verbatim
-- (TEXT ids, JSONB/TIMESTAMP tolerated by SQLite). Idempotent.

CREATE TABLE IF NOT EXISTS cortex_service_keys (
    id                       TEXT PRIMARY KEY,
    label                    TEXT NOT NULL,
    key_hash                 TEXT NOT NULL UNIQUE,
    key_prefix               TEXT,
    tenant_id                TEXT NOT NULL DEFAULT 'default',
    classification_ceiling   TEXT NOT NULL DEFAULT 'CUI',
    scopes                   JSONB,
    status                   TEXT NOT NULL DEFAULT 'active',
    created_by               TEXT,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at             TIMESTAMP,
    revoked_at               TIMESTAMP,
    revoked_by               TEXT
);
CREATE INDEX IF NOT EXISTS idx_cortex_service_keys_tenant ON cortex_service_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cortex_service_keys_status ON cortex_service_keys(status);
