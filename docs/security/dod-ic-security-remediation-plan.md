# ICDEV™ DoD/IC Security & Access Control Remediation Plan
<!-- CUI // SP-CTI -->
**Classification:** CUI // SP-CTI  
**Author Role:** CISO, DoD/IC  
**Date:** 2026-05-28  
**Scope:** RBAC · ABAC · ZTA · RLS · Column Security · LAC · COI · ECI · PKI · STE/STN  
**NIST Controls:** AC-1 through AC-25, AU-2, AU-9, AU-12, IA-2, IA-5, SC-7, SC-12, SC-28

---

## 1. Executive Summary

ICDEV™ is preparing for rollout across DoD/IC environments spanning IL4 through IL6. The platform hosts dozens of canvases and child applications (FORGE Academy, AI GameDay, Proposal Genesis, CPMP, GovCon Intelligence, and more) wired to a 15-agent A2A mesh. Before rollout, **every user, group, and role must hold an explicit access grant** to each tool, module, and child application — ambient access is not acceptable under DoD Zero Trust mandates.

This document records the output of a full CISO-level audit, maps every gap to its DoD/IC requirement, and defines the phased remediation roadmap that closes all P0 (IL4-blocking) gaps in Phase 1 and all P1 (IL5/IL6-blocking) gaps in Phase 2.

---

## 2. What Already Exists (Do Not Rework)

| Control | Primary File(s) | Status |
|---------|----------------|--------|
| Multi-auth: API Key / OAuth 2.0 / SAML 2.0 / CAC/PIV | `tools/saas/auth/` | ✅ Production |
| RBAC — 5 platform roles, endpoint-category matrix | `tools/saas/auth/rbac.py` | ✅ Production |
| RBAC — MCP tool-level role×tool fnmatch matrix | `tools/security/mcp_tool_authorizer.py` | ✅ Production |
| ABAC Engine — XACML-style PIP / PDP / PEP | `tools/security/abac_engine.py` | ✅ Beta |
| RLS — tenant + classification predicate injection | `tools/security/row_security.py` | ✅ Production |
| RLS — SQLite regex + PostgreSQL native policies | `tools/db/storage.py` | ✅ Production |
| Column masking — null / redact / hash / truncate | `tools/security/column_security.py` | ✅ Beta |
| SecurityContext with `compartments` frozenset | `tools/security/security_context.py` | ✅ Production |
| Classification Manager — CUI / SECRET / TS / TS//SCI | `tools/compliance/classification_manager.py` | ✅ Production |
| Append-only audit trail (NIST AU-12) | `audit_trail` + pre_tool_use.py hook | ✅ Production |
| ZTA maturity scorer — 7 DoD pillars, NIST SP 800-207 | `tools/devsecops/zta_maturity_scorer.py` | ✅ Production |
| Service mesh generation — Istio / Linkerd | `tools/devsecops/service_mesh_generator.py` | ✅ Production |
| K8s default-deny network policies | `k8s/networkpolicy.yaml` | ✅ Production |
| A2A mTLS dev cert provisioner — 16 agents | `tools/a2a/provision_dev_certs.py` | ✅ Dev |
| Remote Command Gateway — 8-gate security chain | `tools/gateway/security_chain.py` | ✅ Production |

---

## 3. Gap Registry

Gaps are rated **P0** (blocks IL4 ATO), **P1** (blocks IL5/IL6), **P2** (operational hardening).

### 3.1 P0 — Blocks IL4 ATO

| ID | Gap | DoD Requirement | Root Cause |
|----|-----|----------------|-----------|
| G-01 | User Groups not implemented | NIST AC-3, AC-6 | No `groups` table; `compartments` JSONB is a flat tag bag |
| G-02 | No explicit child-app access control | NIST AC-3, ZTA Pillar 5 | Every authenticated user reaches every canvas/child-app |
| G-03 | LAC not enforced at SQL level | NIST AC-16, IC LAC policy | `inject_row_predicate()` skips compartment/label predicates |
| G-04 | API Key scopes ignored | NIST AC-6, IA-5 | `api_keys.scopes` JSONB field never evaluated by middleware |
| G-05 | ABAC decisions not audited | NIST AU-2, AU-12 | `log_abac_decision()` exists but never auto-called from PDP |
| G-06 | Column security Python-only | NIST AC-3(7), AC-6 | `grant_column_select()` DDL never executed; DB-level grants absent |
| G-07 | MFA absent | NIST IA-2(1), IA-2(2), DoD IL4+ | No MFA enforced on dashboard or SaaS portal |
| G-08 | Canvas RLS disabled | NIST AC-3, AU-9 | `get_canvas_connection()` sets `security_context=None` |

### 3.2 P1 — Blocks IL5/IL6

| ID | Gap | DoD Requirement | Root Cause |
|----|-----|----------------|-----------|
| G-09 | COI not enforced at SQL level | IC COI policy, NIST AC-16 | No `coi_tag` column; no SQL predicate |
| G-10 | ECI not implemented | IL5/IC ECI policy | Not in `VALID_CLASSIFICATIONS`, schema, or compartments |
| G-11 | No CRL/OCSP for CAC/PIV | DoD PKI Policy, NIST IA-5(2) | `cac_auth.py` validates CN but never checks revocation |
| G-12 | PDP is config-gen only | NIST AC-24, ZTA Pillar 1 | `pdp_config_generator.py` emits YAML; no policy engine |
| G-13 | No STE/STN deployment model | DISA STE/STN STIG, IL6 | Zero air-gap K8s docs or SIPR-only network isolation |
| G-14 | No continuous authentication | NIST IA-11, ZTA Pillar 1 | Sessions are static after initial auth |
| G-15 | Device trust absent | NIST IA-3, ZTA Pillar 2 | No MDM/EDR integration at login |

### 3.3 P2 — Operational Hardening

| ID | Gap | Root Cause |
|----|-----|-----------|
| G-16 | Rate limiting in-memory | Gateway counters reset on pod restart |
| G-17 | HMAC secret not rotatable | Static `ICDEV_GATEWAY_HMAC_SECRET`; no versioning |
| G-18 | ZTA posture not continuously monitored | Evidence scored but no 30-day drift loop |
| G-19 | A2A cert rotation absent | 365-day static dev certs; no intermediate CA |
| G-20 | Cross-tenant parameter tampering | No per-resource owner check in API layer |
| G-21 | NULL tenant_id rows world-readable | No DB NOT NULL constraint on `tenant_id` |

---

## 4. Remediation Roadmap

### Phase 1 — Foundation (~3–4 weeks, closes all P0 gaps)

#### G-01: User Groups + Role Hierarchy

**New files:**
- `tools/db/migrations/163_groups_canvas_access.sql` — `groups`, `group_members`, `group_roles`
- `tools/security/group_manager.py` — CRUD, membership resolution, group-role assignment

**Modified files:**
- `tools/saas/auth/rbac.py` — add `check_permission_for_group()` resolving group → roles → matrix
- `tools/dashboard/auth.py` `_attach_security_context()` — inject `group_ids` into SecurityContext

```sql
CREATE TABLE groups (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL
);
CREATE TABLE group_members (
    group_id TEXT REFERENCES groups(id),
    user_id TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE group_roles (
    group_id TEXT REFERENCES groups(id),
    role TEXT NOT NULL,
    canvas_scope TEXT,   -- NULL = all canvases
    PRIMARY KEY (group_id, role, canvas_scope)
);
```

---

#### G-02: Explicit Canvas / Child-App Access Control

**New files:**
- `tools/db/migrations/163_groups_canvas_access.sql` (same migration) — `canvas_access_grants`
- `tools/security/canvas_access.py` — `@require_canvas_access(canvas_name, min_level)` decorator + grant API
- `args/canvas_registry.yaml` — canonical list of all canvases and child apps

**Modified files:**
- All canvas blueprint route handlers — add `@require_canvas_access(canvas_name)` decorator
- `tools/saas/tenant_manager.py` — seed `tenant_admin` grants to all canvases on tenant creation

```sql
CREATE TABLE canvas_access_grants (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    principal_type TEXT NOT NULL CHECK (principal_type IN ('user','group','role')),
    principal_id TEXT NOT NULL,
    canvas_name TEXT NOT NULL,
    access_level TEXT NOT NULL CHECK (access_level IN ('read','write','admin')),
    granted_by TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE (tenant_id, principal_type, principal_id, canvas_name)
);
```

**Access Control Matrix (target state — deny-all default):**

| Canvas / Child App | Minimum Grant | Default Principals |
|-------------------|--------------|-------------------|
| Dashboard Home | `dashboard:read` | All active users (auto) |
| Proposals | `proposals:read` | GovCon capture team |
| CPMP | `cpmp:read` | Post-award PMs |
| GovCon Intelligence | `govcon:read` | Capture team |
| AI GameDay | `ai_gameday:read` | Training staff |
| FORGE Academy | `forge_academy:read` | Dev / training users |
| AAC Canvas | `aac:read` | ISSO / compliance officers |
| DSOC Canvas | `dsoc:read` | SOC analysts |
| Network Canvas | `network:read` | Infrastructure admins |
| Observability Canvas | `observability:read` | Ops / SRE |
| Admin Panel | `admin:write` | `tenant_admin` role only |
| Agent Control | `agents:admin` | Orchestrator role only |

---

#### G-03: LAC SQL Predicate Enforcement

**Modified files:**
- `tools/security/row_security.py` — `inject_row_predicate()` — add `lac_labels` + `lac_column` parameters; inject `AND (lac_label IS NULL OR lac_label IN (...))` predicate
- `tools/db/storage.py` — `StorageCursor._inject_rls()` — derive `lac_labels` from `ctx.compartments` (filter `LAC_*` prefix tags); pass to `inject_row_predicate()`
- Migration: add `lac_label TEXT DEFAULT NULL` to `projects`, `documents`, `tasks`

Logic: `LAC_*` tags in `SecurityContext.compartments` → SQL `IN (...)` predicate.  
Rows with `lac_label IS NULL` remain world-readable within tenant+classification scope.

---

#### G-04: API Key Scope Enforcement

**Modified files:**
- `tools/saas/auth/middleware.py` — extract `scopes` from `api_keys` record; store as `g.api_key_scopes`
- `tools/saas/auth/rbac.py` `check_permission()` — if `g.api_key_scopes` set, intersect requested `canvas:level` against allowed scopes; deny if not present

Scope format: `["projects:read", "proposals:write", "compliance:read"]`

---

#### G-05: ABAC Decision Audit (auto-log all evaluations)

**Modified files:**
- `tools/security/abac_engine.py` — `PDP.evaluate()` — fire-and-forget write to `abac_decisions` table after every evaluation (best-effort; never raises)

The `abac_decisions` table and `log_abac_decision()` helper already exist. This closes the gap by wiring them together automatically.

---

#### G-06: PostgreSQL Column GRANTs Execution

**Modified files:**
- `tools/db/init_icdev_db.py` — after schema creation on PostgreSQL backend, call `apply_column_grants(conn)` which executes `grant_column_select()` / `revoke_column_select()` DDL for all policies in `args/security_config.yaml`

---

#### G-07: TOTP MFA

**New files:**
- `tools/saas/auth/mfa.py` — TOTP enrollment, challenge, verify, backup codes (`pyotp`)
- `tools/dashboard/templates/mfa/enroll.html` + `challenge.html`

**Modified files:**
- `tools/dashboard/auth.py` — redirect to MFA challenge after credential auth when user has MFA enrolled
- `tools/saas/auth/middleware.py` — enforce MFA step-up for IL4+ users

Gate: `ICDEV_MFA_REQUIRED=true` enforces globally; `false` in dev/test.

---

#### G-08: Canvas RLS `rls_mode` Parameter

**Modified files:**
- `tools/db/storage.py` `get_canvas_connection()` — add `rls_mode: str = 'disabled'` parameter  
  - `'disabled'` → current behavior (security_context=None)  
  - `'classification_only'` → skip tenant filter, apply classification filter only  
  - `'full'` → normal RLS (requires tenant_id column on canvas table)
- Canvas `db/init_db.py` files that have `classification` column — switch to `rls_mode='classification_only'`

---

#### G-21: NOT NULL Tenant ID

**Modified files:**
- Migration `163_groups_canvas_access.sql` — add `CHECK (tenant_id IS NOT NULL)` notes  
- `tools/security/row_security.py` `inject_row_predicate()` — assert `tenant_id is not None` when called in multi-tenant mode (log warning if None instead of silently skipping)

---

### Phase 2 — IL5/IL6 Hardening (~4–6 weeks)

#### G-09: COI SQL Predicate

- Add `coi_tag TEXT DEFAULT NULL` column to core data tables (migration)
- Extend `inject_row_predicate()` with `coi_tags` + `coi_column` parameters
- Derive user's COI tags from `SecurityContext.compartments` (filter `COI_*` prefix)

#### G-10: ECI Classification Level

- Add `"ECI"` to `VALID_CLASSIFICATIONS` and `_CLASSIFICATION_MAP["IL5"] = "ECI"` in `classification_manager.py`
- Add ECI to `CLEARANCE_ORDER` between CUI (1) and SECRET (3): `"ECI": 2`
- Update `security_context.py` clearance calculation
- Add ECI portion marking to `args/classification_markings.yaml`

#### G-11: CAC/PIV CRL/OCSP

- Add `verify_revocation(cert_pem)` to `tools/saas/auth/cac_auth.py`
- Config: `ICDEV_PKI_CRL_URL`, `ICDEV_PKI_OCSP_URL`, `ICDEV_PKI_STRICT_REVOCATION`
- Fail-closed when strict mode is on and CRL/OCSP is unreachable

#### G-12: PDP Integration

- New `tools/security/pdp_client.py` with adapters for DISA ICAM, Zscaler ZPA, CrowdStrike Falcon
- `evaluate_access(user_ctx, resource, action) -> PDPDecision`
- Fail-closed gate + 60-second cache; config in `args/pdp_config.yaml`

#### G-13: STE/STN Deployment Model

- `docs/ops/ste-runbook.md` — SIPR initialization playbook
- `k8s/ste/` — air-gap K8s manifests (networkpolicy-sipr, configmap-ste, secret-pki template)
- `tools/airgap/ste_validator.py` — validates STE readiness before deploy

#### G-14: Continuous Authentication

- `tools/security/continuous_auth.py` — session risk scorer (auth age, IP change, anomalous rate)
- Modify `tools/dashboard/auth.py` `_auth_before_request()` — step-up redirect above risk threshold
- Config: `ICDEV_SESSION_MAX_AGE_MINUTES=480`, `ICDEV_STEP_UP_RISK_THRESHOLD=0.7`

#### G-15: Device Trust

- `tools/security/device_trust.py` — CrowdStrike Falcon device posture adapter
- Enforce on login when `ICDEV_DEVICE_TRUST_REQUIRED=true`

---

### Phase 3 — Operational Hardening (~2–3 weeks, parallel with Phase 2)

| Gap | Remediation |
|-----|------------|
| G-16 Rate limiting | DB-backed `gateway_rate_limits` table; sliding window in `security_chain.py` |
| G-17 HMAC rotation | Dual-key support (`_v1`/`_v2`) + quarterly rotation procedure |
| G-18 ZTA drift | 30-day scheduled assessment in `zta_maturity_scorer.py`; drift alert >10% drop |
| G-19 Cert rotation | 90-day validity; `--rotate` flag; `docs/ops/a2a-cert-rotation.md` |
| G-20 Cross-tenant | Resource-owner check in RBAC layer for all writable endpoints |

---

## 5. PKI / STE / STN CISO Gate Checklist

Sign-off required before any IL5+ production deployment:

- [ ] CAC/PIV enforced at ingress (`ssl_verify_client on` or ALB mutual TLS)
- [ ] CRL/OCSP configured and `ICDEV_PKI_STRICT_REVOCATION=true`
- [ ] SAML SP metadata loaded with DISA EIS or AFIDM IdP
- [ ] MFA enforced (`ICDEV_MFA_REQUIRED=true`)
- [ ] All A2A certs replaced with DISA PKI-issued certs (not dev self-signed)
- [ ] PostgreSQL mTLS enabled (`ICDEV_PG_SSLMODE=verify-full`)
- [ ] `canvas_access_grants` populated — zero default-allow entries exist
- [ ] `lac_label` and `coi_tag` columns populated on all sensitive tables
- [ ] ZTA maturity score ≥ 0.6 (Advanced) for IL5; ≥ 0.85 (Optimal) for IL6
- [ ] `tools/airgap/ste_validator.py --validate` passes (IL6/STE only)
- [ ] Audit trail forwarding to SIEM active (Splunk / Elasticsearch)

---

## 6. Verification Plan

```bash
# Phase 1 — unit tests
pytest tests/test_rls_integration.py -v        # LAC predicate scenarios
pytest tests/test_canvas_access.py -v          # @require_canvas_access decorator
pytest tests/test_group_manager.py -v          # Group CRUD + membership
pytest tests/test_abac_engine.py -v            # ABAC auto audit trail
pytest tests/test_mfa.py -v                    # TOTP enrollment + challenge

# Phase 1 — integration
pytest tests/test_rls_integration.py::TestLAC -v
pytest tests/test_canvas_access_integration.py -v
pytest tests/test_api_key_scopes.py -v

# E2E
python tools/testing/e2e_runner.py --run-all
python tools/devsecops/zta_maturity_scorer.py --assess --json
python -m bandit -r tools/ --severity-level medium

# Manual CISO gate
# 1. Create user alice (no grants) → Proposals returns 403
# 2. Grant alice proposals:read → Proposals 200, CPMP 403
# 3. Create group govcon-team → add alice → confirm both canvases reachable
# 4. Enable MFA for alice → confirm TOTP challenge fires
# 5. Query lac_label='SECRET' row as CUI user → row absent from results
# 6. Query COI_FINANCE row as user without COI_FINANCE → row absent
# 7. Check abac_decisions table → every decision logged with policy + reason
# 8. ZTA scorer → overall score ≥ 0.6
```

---

## 7. Reference

| File | Purpose |
|------|---------|
| `tools/security/row_security.py` | RLS predicate injection engine |
| `tools/security/abac_engine.py` | ABAC PDP/PEP |
| `tools/security/security_context.py` | SecurityContext dataclass |
| `tools/security/column_security.py` | Column masking |
| `tools/security/canvas_access.py` | Canvas access grants + decorator *(new)* |
| `tools/security/group_manager.py` | User group management *(new)* |
| `tools/saas/auth/rbac.py` | Platform RBAC matrix |
| `tools/saas/auth/mcp_tool_authorizer.py` | MCP tool RBAC |
| `tools/saas/auth/cac_auth.py` | CAC/PIV authentication |
| `tools/saas/auth/mfa.py` | TOTP MFA *(new)* |
| `tools/compliance/classification_manager.py` | CUI/ECI/SECRET classification |
| `tools/devsecops/zta_maturity_scorer.py` | ZTA 7-pillar scorer |
| `docs/ops/ste-runbook.md` | STE deployment guide *(new)* |
| `args/canvas_registry.yaml` | Canonical canvas list *(new)* |
| `args/pdp_config.yaml` | PDP adapter config *(new, Phase 2)* |
