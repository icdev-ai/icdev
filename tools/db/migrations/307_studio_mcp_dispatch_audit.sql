-- 307_studio_mcp_dispatch_audit.sql
-- DWO / dwo-mcp-02-d5 — append-only audit of every MCP dispatch attempt.
--
-- d1-d4 built the gates (allowlist, caller IL/RBAC, human approval) but each
-- refusal only reached the operator as the step's stderr, which is discarded
-- once the run row is pruned.  This table is the durable record: one row per
-- attempt, written on *every* path — dispatched, refused, or parked awaiting a
-- human decision — so "which tools did this workflow try to run, as whom, and
-- what did the gate decide" is answerable after the fact.
--
-- Parameters are stored as a SHA-256 digest, never verbatim: tool arguments
-- routinely carry CUI and credentials, and the audit question is "were these
-- the same arguments the approver saw", which a digest answers without
-- widening the blast radius of the audit store itself.
--
-- The `decision` CHECK list mirrors the DECISIONS constant in
-- tools/studio/executors/mcp_executor.py.  test_mcp_executor_audit.py asserts
-- the two stay in step (CLAUDE.md: CHECK constraints derive from Python
-- constants).
--
-- classification is resolved per row through
-- tools/compliance/classification_manager.py::get_classification_for_il from
-- the caller's impact level — no hardcoded CUI banner.
--
-- APPEND-ONLY (NIST 800-53 AU).  Never UPDATE or DELETE.  Registered in
-- APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py.

CREATE TABLE IF NOT EXISTS studio_mcp_dispatch_audit (
    audit_id       TEXT PRIMARY KEY,
    run_id         TEXT,
    step_id        TEXT,
    tool           TEXT NOT NULL,
    params_sha256  TEXT NOT NULL,
    principal_id   TEXT,
    tenant_id      TEXT,
    caller_il      TEXT,
    caller_roles   TEXT,
    caller_source  TEXT,
    decision       TEXT NOT NULL
                   CHECK(decision IN ('allowed','refused','pending_approval')),
    reason         TEXT NOT NULL,
    detail         TEXT,
    classification TEXT NOT NULL,
    recorded_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_studio_mcp_dispatch_audit_run
    ON studio_mcp_dispatch_audit(run_id);
CREATE INDEX IF NOT EXISTS idx_studio_mcp_dispatch_audit_tool
    ON studio_mcp_dispatch_audit(tool);
CREATE INDEX IF NOT EXISTS idx_studio_mcp_dispatch_audit_decision
    ON studio_mcp_dispatch_audit(decision);
