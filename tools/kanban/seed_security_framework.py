#!/usr/bin/env python3
"""Seed Kanban tasks for Comprehensive Multi-Layer Security Framework (sec-*).

Decomposed from plan: bright-launching-crescent.md
TS//SCI column/row/field security with RBAC, ABAC, PKI across ICDEV core,
12 canvases, Academy, GameDay, all modules, and child applications.

Run: python tools/kanban/seed_security_framework.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.task_factory import create_tasks  # noqa: E402

_NOW = datetime.now(timezone.utc).isoformat()


def _conn():
    return get_connection()


# Project prefix: sec
TASKS = [
    # ═══════════════════════════════════════════════════════════════════════
    # EP1: FOUNDATION — Security Context + ABAC + MAC + RLS + Column + Field + Crypto
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "sec-fnd-01",
        "title": "SEC: Create tools/security/security_context.py (thread-local context)",
        "description": (
            "Create the foundational SecurityContext dataclass at tools/security/security_context.py.\n\n"
            "Requirements:\n"
            "  - Fields: user_id, role, clearance_level, compartments (set[str]), impact_level, tenant_id, classification, mtls_cn, auth_method.\n"
            "  - _CLEARANCE_ORDER = ['PUBLIC','CUI','SECRET','TOP SECRET','TS//SCI'].\n"
            "  - Thread-local storage via threading.local() + contextvars for async.\n"
            "  - Flask integration: g.security_context set in before_request.\n"
            "  - Propagation to subprocesses via env vars when needed.\n"
            "  - get() / set(ctx) / clear() API.\n"
            "  - Air-gap safe; no external dependencies beyond stdlib + flask.\n\n"
            "Do NOT write middleware yet — that is sec-db-06."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "sec-fnd-02",
        "title": "SEC: Create tools/security/abac_engine.py (PIP/PDP/PEP)",
        "description": (
            "Create XACML-style ABAC engine at tools/security/abac_engine.py.\n\n"
            "Components:\n"
            "  - PIP (Policy Information Point): get_user_attributes(), get_resource_attributes(), get_environment_attributes().\n"
            "  - PDP (Policy Decision Point): evaluate(subject, resource, action, env) → Decision (PERMIT|DENY|INDETERMINATE|NOT_APPLICABLE).\n"
            "  - PEP (Policy Enforcement Point): enforce(subject, resource, action) → bool; on DENY raises AccessDeniedError + audits.\n\n"
            "Policy language: JSON policies loaded from args/security_config.yaml.\n"
            "  - Support operators: >=, <, contains_any, in_range, in.\n"
            "  - Example: ts_read_policy (clearance >= TS + compartment match + time_of_day in_range).\n"
            "  - Example: no_write_down (Bell-LaPadula *-Property).\n\n"
            "Cache PDP decisions for 60s to reduce overhead."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-01",
    },
    {
        "id": "sec-fnd-03",
        "title": "SEC: Create tools/security/classification_enforcer.py (Bell-LaPadula MAC)",
        "description": (
            "Create Mandatory Access Control enforcer at tools/security/classification_enforcer.py.\n\n"
            "Rules:\n"
            "  - Simple Security Property (no read-up): can_read() requires subject_clearance >= object_classification.\n"
            "  - *-Property (no write-down): can_write() requires subject_clearance <= object_classification.\n"
            "  - Compartment checks (need-to-know): object_compartments.issubset(subject_compartments).\n"
            "  - Classification downgrade protection: audit every change; require dual auth for SECRET→CUI.\n"
            "  - Log to audit_trail with event_type='classification_change' and 'classification_violation'.\n\n"
            "Reuse _CLEARANCE_ORDER from security_context.py.\n"
            "Provide decorator @require_clearance(min_level) and @require_compartment(compartment)."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-01",
    },
    {
        "id": "sec-fnd-04",
        "title": "SEC: Create tools/security/row_security.py (PG RLS + SQLite fallback)",
        "description": (
            "Create Row-Level Security manager at tools/security/row_security.py.\n\n"
            "PostgreSQL path:\n"
            "  - generate_rls_policy_sql(table) → ALTER TABLE … ENABLE ROW LEVEL SECURITY; CREATE POLICY … USING (classification_level <= current_setting('app.user_clearance_level')).\n"
            "  - Also inject tenant_id = current_setting('app.current_tenant').\n"
            "  - Connection checkout in storage.py sets these vars.\n\n"
            "SQLite fallback:\n"
            "  - inject_classification_predicate(sql, ctx) → parse SQL (sqlparse or regex) and inject WHERE classification <= ? before ORDER BY / LIMIT.\n"
            "  - Also inject tenant_id = ?.\n\n"
            "Storage integration hook:\n"
            "  - In tools/db/storage.py StorageCursor.execute(), detect SELECT on tables with classification column and auto-inject predicates.\n\n"
            "Test both PG and SQLite paths."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-03",
    },
    {
        "id": "sec-fnd-05",
        "title": "SEC: Create tools/security/column_security.py (masking + privileges)",
        "description": (
            "Create Column-Level Security manager at tools/security/column_security.py.\n\n"
            "PostgreSQL path:\n"
            "  - generate_grant_sql(table, role, allowed_columns) → GRANT SELECT(cols) ON table TO role.\n"
            "  - generate_masking_view_sql(table) → CREATE VIEW table_masked AS … CASE WHEN clearance >= 'TS' THEN col ELSE 'REDACTED' END.\n\n"
            "SQLite fallback (application-level):\n"
            "  - mask_columns(row, ctx, table) → dict with [REDACTED] for columns user lacks clearance to see.\n"
            "  - _COLUMN_POLICES loaded from args/security_config.yaml.\n\n"
            "Integrate into storage.py fetchone/fetchall hooks so all returned rows are masked automatically."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-04",
    },
    {
        "id": "sec-fnd-06",
        "title": "SEC: Create tools/security/field_security.py (API response filtering)",
        "description": (
            "Create Field-Level Security manager at tools/security/field_security.py.\n\n"
            "Requirements:\n"
            "  - filter_response_fields(data, ctx, schema) → removes or redacts fields user lacks clearance to see.\n"
            "  - _FIELD_POLICIES from args/security_config.yaml keyed by schema/endpoint.\n"
            "  - Supports nested dict/list traversal.\n"
            "  - Flask after_request hook: if response.is_json, apply filtering before returning.\n\n"
            "LLM integration:\n"
            "  - Extend LLMRequest output_schema to include field_clearance annotations.\n"
            "  - Filter LLM responses before returning to user.\n\n"
            "Provide helper: redact_field(value, required_clearance, user_clearance) → value or '[REDACTED]'."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-05",
    },
    {
        "id": "sec-fnd-07",
        "title": "SEC: Create tools/security/encryption_at_rest.py (per-classification keys)",
        "description": (
            "Create per-classification encryption manager at tools/security/encryption_at_rest.py.\n\n"
            "Key management:\n"
            "  - _CLASSIFICATION_KEYS = {CUI: ICDEV_CUI_MASTER_KEY, SECRET: ICDEV_SECRET_MASTER_KEY, TS: ICDEV_TS_MASTER_KEY, TS//SCI: ICDEV_TS_SCI_MASTER_KEY}.\n"
            "  - HKDF-SHA256 from master key + classification + column name for column-specific keys.\n"
            "  - Optional HSM integration via PKCS#11 if available.\n\n"
            "PostgreSQL path:\n"
            "  - pgp_sym_encrypt / pgp_sym_decrypt wrappers using current_setting('app.encryption_key').\n"
            "SQLite path:\n"
            "  - Extend tools/security/db_encryption.py (AES-256-GCM) with per-column overlay for mixed-classification tables.\n\n"
            "Key rotation: scheduled re-encryption with audit log entry.\n"
            "Provide: encrypt_column(value, classification) and decrypt_column(ciphertext, classification)."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-06",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP2: DATABASE + AUTH + mTLS
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "sec-db-01",
        "title": "SEC: Migration 145 — add clearance_level, compartment, tenant_id to user tables",
        "description": (
            "Create migration tools/db/migrations/145_security_framework/up.py.\n\n"
            "Schema changes:\n"
            "  - dashboard_users: ADD COLUMN clearance_level TEXT DEFAULT 'CUI', ADD COLUMN compartment TEXT, ADD COLUMN tenant_id TEXT.\n"
            "  - Add CHECK(clearance_level IN ('PUBLIC','CUI','SECRET','TOP SECRET','TS//SCI')).\n"
            "  - Create table user_compartments (user_id, compartment_name) for many-to-many.\n"
            "  - Create table security_policies (id, name, policy_json, active, created_at).\n"
            "  - Update all canvas user tables with same columns (backfill with 'CUI' + 'default').\n\n"
            "Update tests/conftest.py MINIMAL_ICDEV_SCHEMA.\n"
            "Add to .claude/hooks/pre_tool_use.py append-only tables if new audit tables are created."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-01",
    },
    {
        "id": "sec-db-02",
        "title": "SEC: Migration 146 — PG RLS policies for all classification tables",
        "description": (
            "Create migration tools/db/migrations/146_rls_policies/up.py.\n\n"
            "Actions:\n"
            "  - For every table with classification column: ALTER TABLE … ENABLE ROW LEVEL SECURITY.\n"
            "  - CREATE POLICY classification_rls USING (classification_level <= current_setting('app.user_clearance_level', true)).\n"
            "  - CREATE POLICY tenant_rls USING (tenant_id = current_setting('app.current_tenant', true)).\n"
            "  - Grant bypassrls to admin roles if needed for maintenance.\n"
            "  - Log all policy creations to audit_trail.\n\n"
            "Include reverse migration (DROP POLICY) in down.py.\n"
            "Test on both PG and SQLite (SQLite uses predicate injection, no native RLS)."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-db-01",
    },
    {
        "id": "sec-db-03",
        "title": "SEC: Enhance tools/dashboard/auth.py with clearance + compartment",
        "description": (
            "Update tools/dashboard/auth.py.\n\n"
            "Changes:\n"
            "  - Add clearance_level and compartment to session/JWT creation and validation.\n"
            "  - Enhance require_role() to also check require_clearance(min_level).\n"
            "  - Add @require_clearance(min_level) decorator.\n"
            "  - Add @require_compartment(compartment) decorator.\n"
            "  - Add @require_mtls_and_clearance() composite decorator.\n"
            "  - Update RBAC_MATRIX to include clearance requirements per role.\n"
            "  - API key auth: lookup user clearance from DB and inject into security context.\n"
            "  - TOTP/MFA: preserve clearance claims through challenge.\n\n"
            "Backwards compatible: default clearance_level='CUI' for existing users."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-db-01",
    },
    {
        "id": "sec-db-04",
        "title": "SEC: Enhance tools/dashboard/api/auth.py JWT claims",
        "description": (
            "Update tools/dashboard/api/auth.py.\n\n"
            "Changes:\n"
            "  - Add 'clearance', 'compartment', 'impact_level' to JWT access token claims.\n"
            "  - Validate these claims on every /api/v1/* request in JWT middleware.\n"
            "  - Refresh token rotation must preserve clearance claims.\n"
            "  - If claim missing (legacy token), default to 'CUI' and log a warning.\n"
            "  - On token validation failure due to clearance mismatch, return 403 with policy reference.\n\n"
            "Update token issuance endpoint to accept clearance_level param (admin-only override)."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-db-03",
    },
    {
        "id": "sec-db-05",
        "title": "SEC: Create tools/security/mtls_integration.py (CN → user identity)",
        "description": (
            "Create mTLS identity resolver at tools/security/mtls_integration.py.\n\n"
            "Requirements:\n"
            "  - resolve_mtls_identity(cert_cn: str) → SecurityContext.\n"
            "  - Lookup user by cert CN in dashboard_users (new column cert_cn or mapping table).\n"
            "  - Build SecurityContext with clearance, compartments, impact_level, mtls_cn, auth_method='mtls'.\n"
            "  - If no user found, raise AccessDeniedError with audit log.\n"
            "  - If cert expired or revoked (check against CRL/OCSP if available), deny.\n\n"
            "Update tools/pki/enforce.py:\n"
            "  - Link ICDEV_MTLS_CLIENT_CN to resolve_mtls_identity().\n"
            "  - Reject connections without valid client cert in enforce mode.\n"
            "  - Update sslmode to verify-full when impact_level >= IL5."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-01",
    },
    {
        "id": "sec-db-06",
        "title": "SEC: Create tools/security/security_middleware.py (Flask before_request)",
        "description": (
            "Create Flask security middleware at tools/security/security_middleware.py.\n\n"
            "Requirements:\n"
            "  - init_app(app) → registers before_request and after_request handlers.\n"
            "  - before_request:\n"
            "      1. Extract auth method (session | jwt | api_key | mtls).\n"
            "      2. Build SecurityContext from auth credentials.\n"
            "      3. Set g.security_context and thread-local context.\n"
            "      4. For PG connections, SET app.user_clearance_level / app.user_compartments / app.current_tenant.\n"
            "  - after_request:\n"
            "      1. If response.is_json, apply field_security.filter_response_fields().\n"
            "      2. Add X-Classification-Ceiling header.\n"
            "      3. Clear thread-local context.\n\n"
            "Skip security context for public/static routes (whitelist).\n"
            "Register in tools/dashboard/app.py factory."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-db-03",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP3: CANVAS INTEGRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "sec-cvs-01",
        "title": "SEC: Apply security framework to NDC + SDC canvases",
        "description": (
            "Update Network Design Canvas (NDC) and Security Design Canvas (SDC).\n\n"
            "Changes:\n"
            "  - Add clearance_level and compartment columns to canvas user tables (migration or ALTER).\n"
            "  - Add tenant_id to design tables.\n"
            "  - Apply @require_clearance() to all blueprint routes returning classified data.\n"
            "  - Filter queries: SELECT * FROM designs WHERE classification <= ? AND tenant_id = ?.\n"
            "  - Apply field_security to JSON API responses.\n"
            "  - Update templates to show per-design classification banner.\n"
            "  - Add classification to canvas_kg_nodes/edges where applicable.\n\n"
            "Verify: CUI user cannot see SECRET designs; TS user sees all."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-db-02",
    },
    {
        "id": "sec-cvs-02",
        "title": "SEC: Apply security framework to PDC + BDC canvases",
        "description": (
            "Update Policy Design Canvas (PDC) and Boundary Design Canvas (BDC).\n\n"
            "Changes:\n"
            "  - Same pattern as sec-cvs-01: user columns, tenant_id, route decorators, query filters, field filtering, template banners.\n"
            "  - PDC policies may have higher classification (SECRET) — verify no-read-up blocks.\n"
            "  - BDC boundary containers often span classifications — handle mixed-tenant boundary views carefully.\n\n"
            "Test with users at CUI, SECRET, and TS clearances."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-cvs-01",
    },
    {
        "id": "sec-cvs-03",
        "title": "SEC: Apply security framework to DDC + ODC canvases",
        "description": (
            "Update Data Design Canvas (DDC) and Orchestration Design Canvas (ODC).\n\n"
            "Changes:\n"
            "  - DDC: data models and flows often contain CUI/SECRET data — apply column masking to payload fields.\n"
            "  - ODC: orchestration graphs may reference classified systems — filter nodes by classification.\n"
            "  - Both: tenant isolation for multi-tenant deployments.\n"
            "  - Both: apply security decorators to blueprint routes.\n\n"
            "Test DDC with mixed-classification data flows."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-cvs-02",
    },
    {
        "id": "sec-cvs-04",
        "title": "SEC: Apply security framework to IDC + remaining canvases",
        "description": (
            "Update Integration Design Canvas (IDC) and all remaining canvases (AADC, MCE, NMCE, OHC, AISG, etc.).\n\n"
            "Changes:\n"
            "  - IDC: integration points may cross classification boundaries — enforce *-Property (no write-down).\n"
            "  - AADC: add auth wrapper (currently missing) with @require_clearance('CUI') minimum.\n"
            "  - MCE/NMCE/OHC: apply same pattern — user columns, tenant_id, decorators, filters, banners.\n"
            "  - AISG: AI-generated designs may auto-classify — set classification based on data sources.\n\n"
            "Backfill existing designs with classification='CUI' and tenant_id='default'."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-cvs-03",
    },
    {
        "id": "sec-cvs-05",
        "title": "SEC: Canvas classification filtering (query layer)",
        "description": (
            "Create reusable canvas query filter layer in tools/canvas/classification_filter.py.\n\n"
            "Requirements:\n"
            "  - filter_query(sql, ctx, table) → returns SQL with classification and tenant predicates injected.\n"
            "  - Works for both raw SQL and ORM-style queries.\n"
            "  - Auto-detects tables with classification column from schema introspection.\n"
            "  - Caches table-has-classification flag to avoid repeated introspection.\n"
            "  - Raises ClassificationViolationError if user attempts to bypass filters.\n\n"
            "Integrate into all 12 canvas blueprint.py files via a shared decorator @canvas_secure_query()."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-cvs-01",
    },
    {
        "id": "sec-cvs-06",
        "title": "SEC: Canvas tenant isolation",
        "description": (
            "Implement tenant isolation across all 12 canvases.\n\n"
            "Changes:\n"
            "  - Add tenant_id column to all canvas design tables (migration or ALTER).\n"
            "  - Backfill existing rows with tenant_id='default'.\n"
            "  - All SELECT queries must include tenant_id = current tenant.\n"
            "  - Multi-tenant mode: derive tenant_id from subdomain, path prefix, or JWT claim.\n"
            "  - Admin users (role='admin') can see all tenants with explicit tenant_id=None override.\n"
            "  - Cross-tenant queries blocked by RLS or predicate injection.\n\n"
            "Update tools/db/init_icdev_db.py with tenant_id columns."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-db-01",
    },
    {
        "id": "sec-cvs-07",
        "title": "SEC: Canvas field-level security in API responses",
        "description": (
            "Apply field_security.py to all canvas API endpoints.\n\n"
            "Changes:\n"
            "  - In each canvas blueprint, add after_request handler or wrap jsonify() calls with filter_response_fields().\n"
            "  - Define _FIELD_POLICIES for each canvas in args/security_config.yaml.\n"
            "  - Example: NDC node 'payload' field requires SECRET; 'name' is PUBLIC.\n"
            "  - Example: SDC 'secret_notes' requires SECRET; 'status' is CUI.\n"
            "  - Test: CUI user sees [REDACTED] for SECRET fields in API JSON.\n\n"
            "Use shared helper in tools/canvas/ to avoid duplicating logic per canvas."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-06",
    },
    {
        "id": "sec-cvs-08",
        "title": "SEC: Canvas template classification banners",
        "description": (
            "Update all 12 canvas templates to show per-design classification banners.\n\n"
            "Changes:\n"
            "  - Replace global classification banner with per-design banner in detail/edit views.\n"
            "  - List views show classification badge per row (color-coded: PUBLIC=green, CUI=blue, SECRET=orange, TS=red, TS//SCI=purple).\n"
            "  - Add compartment badge if design has compartment tags.\n"
            "  - Banner changes dynamically when user switches designs.\n"
            "  - Read-only indicator when user lacks write clearance (*-Property).\n\n"
            "Reuse classification_manager.py for color and marking strings."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-cvs-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP4: ACADEMY + GAMEDAY
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "sec-agd-01",
        "title": "SEC: Academy security context propagation through LLM pipeline",
        "description": (
            "Update FORGE Academy to propagate security context through all LLM interactions.\n\n"
            "Changes:\n"
            "  - Add security_context parameter to all Academy LLM request helpers.\n"
            "  - Academy content defaults to CUI; TS content modules require elevated clearance.\n"
            "  - Filter training examples by user's clearance — don't show SECRET scenarios to CUI trainees.\n"
            "  - Audit every LLM interaction with classification context (user, prompt classification, response classification).\n"
            "  - In quiz/scoring, don't penalize users for content they can't see.\n\n"
            "Update tools/llm/router.py to accept security_context in LLMRequest."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-db-06",
    },
    {
        "id": "sec-agd-02",
        "title": "SEC: GameDay agent bridge security context",
        "description": (
            "Update AI GameDay League to bridge agents through security context.\n\n"
            "Changes:\n"
            "  - Game scenarios can have classification levels (CUI default, SECRET for cyber scenarios, TS for space).\n"
            "  - Agents respect classification: red-team agent cannot see blue-team SECRET assets unless cleared.\n"
            "  - Agent-to-agent (A2A) communication uses mutual TLS + security context exchange.\n"
            "  - Scoreboard filters by viewer clearance — spectators see only CUI-level game events.\n"
            "  - Game replay archives tagged with classification and compartment.\n\n"
            "Integrate with tools/ai_game_engine/ module."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-db-06",
    },
    {
        "id": "sec-agd-03",
        "title": "SEC: Academy content tier classification levels",
        "description": (
            "Classify all Academy tiers and labs by impact level.\n\n"
            "Changes:\n"
            "  - Tier 1 (Fundamentals): PUBLIC / CUI.\n"
            "  - Tier 2 (Intermediate): CUI / SECRET — requires SECRET clearance for some labs.\n"
            "  - Tier 3 (Advanced): SECRET / TS — requires TS clearance for capstone projects.\n"
            "  - Tier 4 (Expert/SCI): TS//SCI with compartment requirements.\n"
            "  - Update args/forge_academy_config.yaml with classification map per tier.\n"
            "  - Enrollment gate: verify user clearance before allowing tier access.\n"
            "  - Lab certificates stamped with classification level.\n\n"
            "Update dashboard enrollment UI with clearance badges."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-agd-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP5: ECOSYSTEM INTEGRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "sec-eco-01",
        "title": "SEC: Audit logger enhancement (access denied events)",
        "description": (
            "Update tools/audit/audit_logger.py.\n\n"
            "Changes:\n"
            "  - Add event types: access_denied, unauthorized_access, classification_violation, compartment_breach, security_context_created, policy_violation.\n"
            "  - Log every access denied with full context: user_id, clearance, compartment, resource, action, policy_name, timestamp, SHA-256 row hash.\n"
            "  - Add helper: log_access_denied(ctx, resource, action, policy_name).\n"
            "  - Add helper: log_classification_violation(ctx, resource, attempted_classification).\n"
            "  - Ensure append-only: INSERT only, never UPDATE/DELETE.\n"
            "  - Add to APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py if new tables are created.\n\n"
            "Call from abac_engine.PEP, classification_enforcer, and row_security modules."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-02",
    },
    {
        "id": "sec-eco-02",
        "title": "SEC: AI trace mixin extension for security decisions",
        "description": (
            "Update tools/canvas/ai_trace_mixin.py.\n\n"
            "Changes:\n"
            "  - Extend decision_type enum to include: security_violation, access_granted, access_denied.\n"
            "  - Record security decisions with confidence = policy match score (0.0–1.0).\n"
            "  - Add security_context snapshot to canvas_ai_decisions row (JSONB).\n"
            "  - Add classification and compartment columns to canvas_ai_decisions if not present.\n"
            "  - When AI recommends a classified action, record the classification in the trace.\n\n"
            "Update tests/conftest.py schema.\n"
            "Update canvas blueprint record_canvas_decision() calls to pass classification."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-eco-01",
    },
    {
        "id": "sec-eco-03",
        "title": "SEC: MCP tool registry PEP authorization gate",
        "description": (
            "Update tools/mcp/tool_registry.py and gap_handlers.py.\n\n"
            "Changes:\n"
            "  - Add automatic PEP middleware: before executing any handler, call abac_engine.PEP.enforce().\n"
            "  - Unauthorized tool calls return {'error': 'access_denied', 'policy': policy_name}.\n"
            "  - Tool metadata includes required_clearance and required_compartment.\n"
            "  - Dynamic policy: some tools require SECRET, others CUI, based on tool function.\n"
            "  - MCP server protocol handler catches AccessDeniedError and returns proper JSON-RPC error.\n"
            "  - Log all denied tool calls via audit_logger.\n\n"
            "Ensure backward compatibility: tools without metadata default to CUI."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-02",
    },
    {
        "id": "sec-eco-04",
        "title": "SEC: Compliance crosswalk NIST AC control mapping",
        "description": (
            "Update tools/compliance/crosswalk_engine.py.\n\n"
            "Changes:\n"
            "  - Map NIST 800-53 Rev 5 controls to security framework components:\n"
            "      AC-2 (Account Management) → auth.py + migration 145\n"
            "      AC-3 (Access Enforcement) → abac_engine PEP\n"
            "      AC-4 (Information Flow Enforcement) → classification_enforcer + row_security\n"
            "      AC-16 (Security Attributes) → security_context + field_security\n"
            "      AU-6 (Audit Review) → audit_logger enhancement\n"
            "      AU-12 (Audit Generation) → audit_logger (already append-only)\n"
            "      SC-13 (Cryptographic Protection) → encryption_at_rest\n"
            "      SC-28 (Protection of Information at Rest) → encryption_at_rest\n"
            "  - Auto-populate control implementation evidence from security audit logs.\n"
            "  - Generate markdown evidence report per control.\n\n"
            "Add to docs/reference/compliance-security.md."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-eco-01",
    },
    {
        "id": "sec-eco-05",
        "title": "SEC: Event bus security context propagation",
        "description": (
            "Update tools/canvas/event_bus.py.\n\n"
            "Changes:\n"
            "  - Include security_context (clearance, compartment, tenant_id) in every event payload.\n"
            "  - Subscribers verify they have clearance to consume events before processing.\n"
            "  - Downgrade events automatically if subscriber clearance is lower: strip TS fields, keep CUI.\n"
            "  - Cross-tenant events blocked unless subscriber is admin or same tenant.\n"
            "  - Audit log for every event publish and subscribe with classification context.\n\n"
            "Add event types: security_context_propagated, event_downgraded, event_blocked."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-01",
    },
    {
        "id": "sec-eco-06",
        "title": "SEC: Memory system classification tagging and retrieval filtering",
        "description": (
            "Update tools/memory/memory_write.py and memory_read.py.\n\n"
            "Changes:\n"
            "  - Tag every memory entry with classification and compartment on write.\n"
            "  - Filter retrieval by current security context: only return entries where entry.classification <= user.clearance.\n"
            "  - Filter by compartment: entry.compartments subset of user.compartments.\n"
            "  - Hybrid search scoring: downgrade relevance of higher-classification entries to avoid information leakage via ranking.\n"
            "  - Migration: add classification and compartment columns to memory_entries.\n"
            "  - Backfill existing entries with classification='CUI' and compartment=''.\n\n"
            "Update tools/memory/hybrid_search.py to apply filters."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-01",
    },
    {
        "id": "sec-eco-07",
        "title": "SEC: LLM router security context propagation",
        "description": (
            "Update tools/llm/router.py.\n\n"
            "Changes:\n"
            "  - Add security_context to LLMRequest dataclass.\n"
            "  - Router propagates context through provider pipeline: redaction, RAG, cache, gateway, telemetry.\n"
            "  - Redaction layer: strip classified content from prompts if provider is not cleared (e.g., cloud provider for TS data).\n"
            "  - Cache key includes security context hash to prevent cross-clearance cache poisoning.\n"
            "  - Telemetry records clearance_level with every request.\n"
            "  - Gateway: block TS//SCI requests from going to non-airgap providers.\n\n"
            "Update args/llm_config.yaml with provider_clearance_map (ollama=TS, bedrock=SECRET, azure=SECRET)."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-01",
    },
    {
        "id": "sec-eco-08",
        "title": "SEC: Extend cui_crypto.py to per-classification keys",
        "description": (
            "Update tools/security/cui_crypto.py.\n\n"
            "Changes:\n"
            "  - Replace single ICDEV_CUI_MASTER_KEY with per-classification key derivation.\n"
            "  - encrypt_cui(value, classification='CUI') → uses key for that classification.\n"
            "  - decrypt_cui(ciphertext, classification='CUI') → uses matching key.\n"
            "  - Backward compatible: default classification='CUI' maintains existing behavior.\n"
            "  - Add key rotation helper: re_encrypt_table(table, column, old_key, new_key).\n"
            "  - Audit every rotation event.\n\n"
            "Integrate with tools/security/encryption_at_rest.py for master key management."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-07",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP6: CHILD APP PACKAGING
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "sec-chl-01",
        "title": "SEC: Child app generator security framework inheritance",
        "description": (
            "Update tools/builder/child_app_generator.py.\n\n"
            "Changes:\n"
            "  - tools/security/ is already in DIRECTORY_TREE (line 165) — child apps inherit modules automatically.\n"
            "  - Add security_policy_update adaptation: adjust default policies for child app classification.\n"
            "  - Add clearance_ceiling adaptation: cap child app users at parent-defined max clearance.\n"
            "  - Child app args/security_config.yaml generated from parent template with lowered classification levels.\n"
            "  - Child app DB migrations include security framework tables.\n"
            "  - Verify child_app_generator.py scaffolds security middleware init in app factory.\n\n"
            "Run forge_validator.py --gate on a test child app after changes."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-db-06",
    },
    {
        "id": "sec-chl-02",
        "title": "SEC: Child app security policy validation",
        "description": (
            "Create validation gate for child app security policies.\n\n"
            "Requirements:\n"
            "  - Verify child app's max classification <= parent app's max classification.\n"
            "  - Verify child app's security_config.yaml is syntactically valid and all policies are satisfiable.\n"
            "  - Verify child app's migrations include security framework columns.\n"
            "  - Verify child app's blueprint routes have @require_clearance() decorators.\n"
            "  - Run as part of tools/builder/forge_validator.py --gate.\n\n"
            "Add to child app generator post-scaffold checklist."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-chl-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP7: FORGE + ANVIL ARTIFACTS
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "sec-fan-01",
        "title": "SEC: Create FORGE goal goals/enable_ts_security.md",
        "description": (
            "Create FORGE goal artifact at goals/enable_ts_security.md.\n\n"
            "Content:\n"
            "  - Title: Enable Top Secret//SCI Multi-Layer Security Framework\n"
            "  - Objective: Deploy column/row/field security, ABAC, MAC, mTLS, encryption across ICDEV ecosystem.\n"
            "  - Workflow: assess current posture → deploy security framework → verify all layers → audit.\n"
            "  - Tools: security_context.py, abac_engine.py, classification_enforcer.py, row_security.py, column_security.py, field_security.py, encryption_at_rest.py, mtls_integration.py, security_middleware.py.\n"
            "  - Expected outputs: all 9 epics complete, coherence checker passes, health check passes.\n"
            "  - Success criteria: CUI user cannot read TS data; TS user can read all; write-down blocked; audit trail complete.\n\n"
            "Add entry to goals/manifest.md."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "sec-fan-02",
        "title": "SEC: Create context doc context/ts_security_policy.md",
        "description": (
            "Create security policy reference at context/ts_security_policy.md.\n\n"
            "Content:\n"
            "  - Bell-LaPadula rules: simple security property (no read-up), *-property (no write-down).\n"
            "  - Compartment handling: need-to-know, strict subset check, dual authorization for cross-compartment.\n"
            "  - Key management: per-classification master keys, HKDF derivation, rotation schedule, HSM integration.\n"
            "  - mTLS requirements: verify-full for IL5+, CN-to-identity mapping, cert revocation checks.\n"
            "  - Audit expectations: every access denied, every classification change, every policy violation logged.\n"
            "  - NIST 800-53 control mapping summary.\n"
            "  - Classification markings: CUI banners, SECRET/TS headers, handling instructions.\n\n"
            "Reference: tools/compliance/classification_manager.py, tools/pki/enforce.py."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fan-01",
    },
    {
        "id": "sec-fan-03",
        "title": "SEC: Create hard prompt hardprompts/ts_rbac_abac.md",
        "description": (
            "Create prompt engineering template at hardprompts/ts_rbac_abac.md.\n\n"
            "Content:\n"
            "  - Guidelines for writing security-aware code that respects classification boundaries.\n"
            "  - Rules: always check clearance before data access, always audit denials, never trust client-side classification.\n"
            "  - Patterns: decorator-based enforcement, middleware-based context, query predicate injection, column masking.\n"
            "  - Anti-patterns: hardcoding secrets, skipping audit for 'internal' calls, trusting JWT without validation.\n"
            "  - Example: 'Given a user with clearance CUI, write a query that returns only CUI-classified rows.'\n"
            "  - Example: 'Write a decorator that enforces both RBAC role and ABAC clearance.'\n\n"
            "Reference: hardprompts/karpathy_principles.md for pre-design gate."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fan-01",
    },
    {
        "id": "sec-fan-04",
        "title": "SEC: Create ANVIL workflow .claude/commands/security.md",
        "description": (
            "Create ANVIL command at .claude/commands/security.md.\n\n"
            "Sections:\n"
            "  ## Audit security posture: python tools/security/audit_posture.py --json\n"
            "  ## Rotate keys: python tools/security/encryption_at_rest.py --rotate --classification TS --json\n"
            "  ## Review policies: python tools/security/abac_engine.py --review --json\n"
            "  ## Test RLS: python tools/security/row_security.py --test --table <name> --json\n"
            "  ## Check clearance: python tools/security/security_context.py --whoami --json\n"
            "  ## Run security scan: python tools/testing/health_check.py --json + bandit\n"
            "  ## Verify mTLS: python tools/pki/validate.py --mtls --json\n\n"
            "Ensure all commands use allowlisted python tools/... prefix.\n"
            "Add to .claude/commands/ manifest."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fan-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP8: CONFIGURATION + REGISTRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "sec-cfg-01",
        "title": "SEC: Create args/security_config.yaml",
        "description": (
            "Create security framework configuration at args/security_config.yaml.\n\n"
            "Sections:\n"
            "  - classification_levels: [PUBLIC, CUI, SECRET, TOP SECRET, TS//SCI] with encryption_required flags.\n"
            "  - compartments: [HCS-P, KLONDIKE, ORION, etc.] with descriptions.\n"
            "  - default_policies: ts_read_policy, no_write_down, cui_minimum_policy, etc.\n"
            "  - column_policies: per-table column-to-clearance mapping.\n"
            "  - field_policies: per-endpoint/schema field-to-clearance mapping.\n"
            "  - mTLS: enforce_mode, verify_full_impact_level, air_gap_sslmode.\n"
            "  - key_management: rotation_days, derivation_algorithm, hsm_enabled.\n"
            "  - cache: pdp_decision_ttl_seconds=60.\n\n"
            "Validate YAML schema on load. Provide --validate CLI."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-01",
    },
    {
        "id": "sec-cfg-02",
        "title": "SEC: Register in tools/manifest + update CLAUDE.md",
        "description": (
            "Registration checklist:\n\n"
            "  1. tools/manifest/security.md — create shard with all 10 new security tools.\n"
            "  2. tools/manifest.md — add security section link.\n"
            "  3. CLAUDE.md docs/reference/commands.md — add security CLI commands:\n"
            "       python tools/security/security_context.py --whoami --json\n"
            "       python tools/security/abac_engine.py --review --json\n"
            "       python tools/security/row_security.py --test --table <name> --json\n"
            "  4. args/security_gates.yaml — add security framework gates if any blocking conditions.\n"
            "  5. .claude/hooks/pre_tool_use.py — add append-only tables (audit_trail, security_policies).\n"
            "  6. tests/conftest.py — add new table schemas to MINIMAL_ICDEV_SCHEMA.\n"
            "  7. Register in tools/mcp/tool_registry.py if security tools are MCP-exposed.\n\n"
            "Run companion sync after registration."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "sec-cfg-03",
        "title": "SEC: Update tests/conftest.py and pre_tool_use.py hooks",
        "description": (
            "Update test and hook infrastructure.\n\n"
            "Changes:\n"
            "  - tests/conftest.py MINIMAL_ICDEV_SCHEMA:\n"
            "      Add security_context, user_compartments, security_policies, classification_enforcer tables.\n"
            "      Add clearance_level, compartment, tenant_id to existing user/design table schemas.\n"
            "  - .claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES:\n"
            "      Add audit_trail (if not present), security_policies, user_compartments.\n"
            "  - .claude/hooks/post_tool_use.py:\n"
            "      Add security framework to auto-commit message patterns if relevant.\n"
            "  - tests/ fixtures:\n"
            "      Add fixture 'security_context_cui()', 'security_context_ts()', 'security_context_public()'.\n\n"
            "Ensure no test regressions."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-db-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP9: TESTING + V&V
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "sec-tst-01",
        "title": "SEC: Write unit tests for security framework core",
        "description": (
            "Create tests/tools/security/ directory with unit tests.\n\n"
            "Test coverage:\n"
            "  - security_context: get/set/clear, clearance ordering, compartment subset.\n"
            "  - abac_engine: PERMIT/DENY/INDETERMINATE for sample policies, cache hit/miss.\n"
            "  - classification_enforcer: can_read (no read-up), can_write (no write-down), compartment strict check.\n"
            "  - row_security: PG RLS SQL generation, SQLite predicate injection on sample queries.\n"
            "  - column_security: masking logic, grant SQL generation.\n"
            "  - field_security: nested dict/list filtering, [REDACTED] replacement.\n"
            "  - encryption_at_rest: round-trip encrypt/decrypt per classification, key derivation determinism.\n"
            "  - mtls_integration: CN lookup, expired cert rejection.\n"
            "  - security_middleware: context propagation, after_request filtering.\n\n"
            "Use pytest. Mock DB where possible."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-fnd-07",
    },
    {
        "id": "sec-tst-02",
        "title": "SEC: Write integration tests (full request pipeline)",
        "description": (
            "Create integration tests for end-to-end security pipeline.\n\n"
            "Test scenarios:\n"
            "  1. Create user with clearance 'CUI', attempt to query TS data → verify 403 + audit log entry.\n"
            "  2. Create user with clearance 'TS//SCI' + compartment 'HCS-P', query TS data with compartment → verify 200.\n"
            "  3. Attempt write-down (TS user writing CUI data) → verify blocked by *-Property (403 or exception).\n"
            "  4. Verify column masking: CUI user sees [REDACTED] for TS columns in API response.\n"
            "  5. Verify field filtering: API response excludes TS fields for CUI users.\n"
            "  6. Verify mTLS: connection without valid client cert rejected in enforce mode.\n"
            "  7. Verify PG RLS: direct SQL query as CUI user returns only CUI rows.\n"
            "  8. Verify SQLite predicate injection: same behavior on SQLite backend.\n"
            "  9. Verify tenant isolation: user A tenant 1 cannot see user B tenant 2 data.\n"
            "  10. Verify key rotation: re-encrypt and decrypt correctly.\n\n"
            "Use Flask test client for HTTP tests. Use temp DBs for isolation."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-tst-01",
    },
    {
        "id": "sec-tst-03",
        "title": "SEC: Final validation — health_check + coherence + companion",
        "description": (
            "Post-implementation validation sequence:\n\n"
            "  1. python tools/testing/health_check.py --json\n"
            "       - Verify no import errors after all security module changes.\n"
            "  2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "       - Verify manifest registration, CLAUDE.md sync, schema consistency, ruff lint gate.\n"
            "  3. python tools/dx/companion.py --sync --write --json\n"
            "       - Sync to icdev/ package mirror and all AI platforms.\n"
            "  4. python tools/security/audit_posture.py --json (if built)\n"
            "       - Verify all security layers report healthy.\n"
            "  5. Run bandit security scan:\n"
            "       python -m bandit -r tools/security/ --severity-level medium\n"
            "  6. Penetration-style tests:\n"
            "       - SQL injection via classification predicate injection (should be parameterized).\n"
            "       - JWT tampering (clearance claim modification → should fail validation).\n"
            "       - Cache poisoning (cross-clearance cache key separation).\n\n"
            "Report pass/fail for each gate."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sec-tst-02",
    },
]


def seed():
    conn = _conn()

    report = create_tasks(
        "sec", TASKS, conn=conn, strict=False, register_project=False
    )

    conn.close()
    inserted = len(report.seeded)
    skipped = len(report.skipped)
    print(report.summary())
    print(f"Inserted {inserted} tasks, skipped {skipped} existing.")
    return {"inserted": inserted, "skipped": skipped}


if __name__ == "__main__":
    seed()
