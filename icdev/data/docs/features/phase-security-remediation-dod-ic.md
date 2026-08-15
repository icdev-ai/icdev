# ICDEV™ DoD/IC Security & Access Control Remediation
<!-- CUI // SP-CTI -->

**Classification:** CUI // SP-CTI  
**Phase:** Security Remediation — DoD/IC IL4–IL6  
**Date Completed:** 2026-05-29  
**Scope:** RBAC · ABAC · ZTA · RLS · Column Security · LAC · COI · ECI · PKI · STE/STN · MFA

---

## Summary

This phase implements 21 security gaps (G-01 through G-21) identified in a full CISO-level audit of ICDEV™ against DoD/IC requirements for IL4–IL6 deployment. All gaps are implemented. This document summarizes what was built and where to find it.

---

## Gaps Closed

### P0 — IL4 ATO Blockers (G-01 through G-08)

| Gap | What Was Built | Files |
|-----|----------------|-------|
| G-01 — User Groups | `groups`, `group_members`, `group_roles` tables; `GroupManager` CRUD | `tools/db/migrations/163_groups_canvas_access.sql`, `tools/security/group_manager.py` |
| G-02 — Canvas Explicit Access Control | `canvas_access_grants` table; `@require_canvas_access` decorator; default-deny | `tools/security/canvas_access.py`, `tools/db/migrations/163_groups_canvas_access.sql` |
| G-03 — LAC SQL Predicate | `lac_label` column predicate in `inject_row_predicate()` | `tools/security/row_security.py` |
| G-04 — API Key Scope Enforcement | Scope intersection check in middleware | `tools/saas/auth/middleware.py` |
| G-05 — ABAC Audit Trail | `abac_audit` table write on every ABAC decision | `tools/security/abac_engine.py` |
| G-06 — PostgreSQL Column GRANTs | `apply_column_grants()` called at DB init | `tools/db/init_icdev_db.py`, `tools/security/column_security.py` |
| G-07 — MFA | TOTP RFC 6238 enrollment + verify + backup codes + Flask decorator | `tools/saas/auth/mfa.py`, `tools/dashboard/templates/mfa/` |
| G-08 — Canvas RLS Re-enablement | `get_canvas_connection(rls_mode=...)` parameter | `tools/db/storage.py` |

### P1 — IL5/IL6 Blockers (G-09 through G-15)

| Gap | What Was Built | Files |
|-----|----------------|-------|
| G-09 — COI SQL Predicate | `coi_tag` column predicate in `inject_row_predicate()` | `tools/security/row_security.py` |
| G-10 — ECI Classification Level | ECI added to `VALID_CLASSIFICATIONS`, `CLEARANCE_ORDER` (ECI=2, SECRET=3) | `tools/compliance/classification_manager.py`, `tools/security/security_context.py` |
| G-11 — CAC/PIV CRL/OCSP | `verify_crl()` + `check_revocation()` using DISA PKI CDP | `tools/saas/auth/cac_auth.py` |
| G-12 — PDP Integration | `PDPClient` with DISA ICAM / Zscaler / CrowdStrike adapters; fail-closed | `tools/security/pdp_client.py`, `tools/security/abac_engine.py` |
| G-13 — STE/STN Deployment | `ste_validator.py` readiness checker; `k8s/ste/` manifests | `tools/airgap/ste_validator.py`, `k8s/ste/` |
| G-14 — Continuous Authentication | Session risk scorer; step-up trigger on anomaly | `tools/security/continuous_auth.py` |
| G-15 — Device Trust | CrowdStrike Falcon device posture adapter | `tools/security/device_trust.py` |

### P2 — Operational Hardening (G-16 through G-21)

| Gap | What Was Built | Files |
|-----|----------------|-------|
| G-16 — Persistent Rate Limiting | `gateway_rate_limits` DB table; sliding-window DB-backed counters | `tools/db/migrations/`, `tools/gateway/security_chain.py` |
| G-17 — HMAC Key Rotation | `_v2` key versioning; dual-key accept period | `tools/gateway/security_chain.py` |
| G-18 — ZTA Continuous Monitoring | 30-day scheduled assessment; drift detection (>10% drop) | `tools/devsecops/zta_maturity_scorer.py` |
| G-19 — A2A Cert Rotation | 90-day validity; `--rotate` flag; intermediate CA | `tools/a2a/provision_dev_certs.py` |
| G-20 — Cross-Tenant Parameter Check | Per-resource RBAC owner check in API endpoints | `tools/dashboard/app.py` (resource endpoints) |
| G-21 — NULL tenant_id Rows | `inject_row_predicate()` asserts non-NULL tenant_id | `tools/security/row_security.py` |

---

## Linter/Test Regressions Resolved

| ID | Fix |
|----|-----|
| R-01 | `tests/test_security_context.py` — ECI clearance order assertions already correct post-G-10 |
| R-02 | `tools/airgap/ste_validator.py` — `ICDEV_CANVAS_ACCESS_GATE` → `ICDEV_CANVAS_ACCESS_ENFORCE`; B310 scheme guard already present |
| R-03 | `tools/dashboard/templates/mfa/enroll.html` + `challenge.html` created |

---

## Key Config Variables

| Variable | Purpose | Required for |
|----------|---------|-------------|
| `ICDEV_MFA_REQUIRED` | Enforce MFA globally | IL4+ |
| `ICDEV_CANVAS_ACCESS_ENFORCE` | Enable canvas access gate (deny-by-default) | IL4+ |
| `ICDEV_PKI_CRL_URL` | DISA PKI CRL distribution point | IL5+ |
| `ICDEV_PKI_STRICT_REVOCATION` | Fail-closed on revocation check failure | IL5+ |
| `ICDEV_FIPS_MODE` | FIPS 140-2/3 mode | IL5+ |
| `ICDEV_CONTINUOUS_AUTH_ENABLED` | Enable session risk scoring | IL5+ |
| `ICDEV_DEVICE_TRUST_REQUIRED` | Enforce device posture at login | IL5+ |
| `ICDEV_DEPLOY_MODE=STE` | Activate air-gap/STE validation | IL6 |

---

## Access Control Matrix (Target State)

Every user, group, and role requires an explicit entry in `canvas_access_grants`. Default: **deny all**.

```sql
SELECT * FROM canvas_access_grants WHERE tenant_id = ? AND principal_id = ?;
```

Seed existing tenants: applied automatically by migration `20260815191145_seed_canvas_grants_for_existing_tenants` (`python tools/db/migrate.py --up`). It was previously a bare `168_seed_canvas_grants.py`, which the migration runner skips silently — so it never ran.

---

## ZTA Maturity Score

Run: `python tools/devsecops/zta_maturity_scorer.py --assess --json`

- IL4 ATO minimum: **0.4** (Basic)
- IL5 minimum: **0.6** (Advanced)  
- IL6 minimum: **0.85** (Optimal)

---

## Operational Runbooks

- STE deployment: `docs/ops/ste-runbook.md`
- A2A cert rotation: `docs/ops/a2a-cert-rotation.md`
- Air-gap LLM routing: `docs/ops/airgap-runbook.md`
<!-- CUI // SP-CTI -->
