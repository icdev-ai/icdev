-- CUI // SP-CTI
-- Migration 292: constitutional_audit_log — per-rule Constitutional AI trail (agx-verify-02).
--
-- Constitutional AI (tools/quality/constitutional_ai.py) critiques LLM-drafted
-- artifacts rule-by-rule against the constitution encoded in
-- args/security_gates.yaml. This table is the append-only (NIST AU) record of
-- every per-rule judgment: which rule, the enum verdict, the offending span, and
-- whether a targeted revision was applied. A reviewer reads the full rule-by-rule
-- trace here instead of a single blended verdict.
--
-- Conventions (mirror migrations 287-291): TEXT-only, dialect-neutral,
-- CREATE TABLE IF NOT EXISTS is idempotent. APPEND-ONLY — never UPDATE/DELETE
-- (registered in APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py). Carries
-- tenant_id + classification for RLS. The module persists best-effort and
-- tolerates the table being absent, so an un-migrated checkout still works.

CREATE TABLE IF NOT EXISTS constitutional_audit_log (
    id                 TEXT PRIMARY KEY,
    artifact_type      TEXT DEFAULT '',
    rule_id            TEXT NOT NULL,
    severity           TEXT NOT NULL DEFAULT 'warn',   -- block | warn
    verdict            TEXT NOT NULL,                  -- pass | fail | not_applicable
    offending_span     TEXT DEFAULT '',
    rationale          TEXT DEFAULT '',
    revised            INTEGER DEFAULT 0,
    vocabulary_version TEXT DEFAULT 'const-1.0',
    tenant_id          TEXT,
    classification     TEXT DEFAULT 'CUI',
    recorded_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_constitutional_audit_rule
    ON constitutional_audit_log (rule_id, verdict);
CREATE INDEX IF NOT EXISTS idx_constitutional_audit_recorded_at
    ON constitutional_audit_log (recorded_at);
