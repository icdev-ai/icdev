-- Migration 193: entitlements table
-- Resolves orphan_db_table gap: tools/rag/entitlement_rag.py queries
-- "SELECT * FROM entitlements" as the fallback SQL when LLM router is
-- unavailable. This table stores named entitlements granted to principals
-- (users / service accounts) and drives the Entitlement-Driven RAG pipeline
-- (irad-cls-01/02/03) and ABAC cross-domain-pull checks.
--
-- Schema derived from:
--   tools/security/abac_engine.py        (user_id, entitlements list)
--   tools/security_canvas/identity_governance.py  (risk_level, used_entitlements)
--   CERT_CADENCE_DAYS / SOD_CONFLICT_PAIRS constants in identity_governance.py

CREATE TABLE IF NOT EXISTS entitlements (
    id              BIGSERIAL PRIMARY KEY,
    principal_id    TEXT NOT NULL,
    entitlement     TEXT NOT NULL,
    risk_level      TEXT NOT NULL DEFAULT 'standard'
                    CHECK (risk_level IN ('privileged', 'standard', 'external')),
    granted_by      TEXT,
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    last_used_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    revoked_by      TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'revoked', 'expired')),
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tenant_id       TEXT,
    UNIQUE (principal_id, entitlement, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_entitlements_principal
    ON entitlements (principal_id, status);
CREATE INDEX IF NOT EXISTS idx_entitlements_tenant
    ON entitlements (tenant_id, status)
    WHERE tenant_id IS NOT NULL;
