# Phase 30 — Dashboard Authentication & RBAC

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 30 |
| Title | Dashboard Authentication & RBAC |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 10 (Web Dashboard), Phase 29 (Proactive Monitoring) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

The ICDEV dashboard exposes project status, compliance posture, security findings, audit trails, and agent health information -- all of which may contain CUI or other controlled information at IL4/IL5/IL6 impact levels. Despite this sensitivity, the dashboard currently has no authentication mechanism. Any user with network access can view every project, every compliance gap, every security finding, and the complete audit trail. This is a direct violation of NIST 800-53 AC-2 (Account Management), AC-3 (Access Enforcement), and IA-2 (Identification and Authentication).

Furthermore, different operator roles have fundamentally different information needs. A program manager needs project status and schedule risk; an ISSO needs compliance posture and security findings; a developer needs build status and test results; a contracting officer needs deliverable tracking. Presenting all information to all users creates cognitive overload and increases the risk of inadvertent CUI exposure to unauthorized personnel.

This phase adds self-contained authentication against `icdev.db` (not dependent on the SaaS layer), role-based access control with 5 operator roles, per-user API key authentication with SHA-256 hashing, Flask signed sessions for browser access, admin user management, a CUI banner toggle, and a merged activity feed combining audit trail and hook events.

---

## 2. Goals

1. Implement per-user API key authentication with SHA-256 hashing stored in `dashboard_api_keys` table, independent of the SaaS layer (D169)
2. Establish 5 RBAC roles (admin, pm, developer, isso, co) with role-based page visibility mapped to existing `ROLE_VIEWS` configuration
3. Provide admin user management capabilities: create admin, list users, assign roles, generate and revoke API keys
4. Use Flask signed sessions (`app.secret_key` from `ICDEV_DASHBOARD_SECRET` env var or auto-generated) for browser-based access (D171)
5. Add a CUI banner toggle via `ICDEV_CUI_BANNER_ENABLED` env var (default `true`) while preserving existing `CUI_BANNER_TOP/BOTTOM` env vars (D173)
6. Create a merged activity feed at `/activity` combining `audit_trail` and `hook_events` via UNION ALL query, maintaining the append-only contract (D174)
7. Log all authentication events (login, logout, failed attempts, key generation, key revocation) in `dashboard_auth_log` table for NIST AU-2 compliance

---

## 3. Architecture

```
+---------------------------------------------------------------+
|                    Dashboard Auth Layer                         |
|                                                                |
|  +------------------+    +-------------------------------+     |
|  | Login Page       |    | API Key Middleware             |     |
|  | /login           |    | Authorization: Bearer icdev_.. |     |
|  | API key entry    |    | SHA-256 hash lookup            |     |
|  +--------+---------+    +---------------+---------------+     |
|           |                              |                     |
|           v                              v                     |
|  +---------------------------------------------------+        |
|  |              Session Management                    |        |
|  |  Flask signed sessions                             |        |
|  |  ICDEV_DASHBOARD_SECRET env var                    |        |
|  |  30-minute timeout                                 |        |
|  +---------------------------------------------------+        |
|           |                                                    |
|           v                                                    |
|  +---------------------------------------------------+        |
|  |              RBAC Engine (5 Roles)                 |        |
|  |  admin: Full access + user management              |        |
|  |  pm: Projects, status, schedule, deliverables      |        |
|  |  developer: Build, test, code, deployments         |        |
|  |  isso: Compliance, security, audit, agents         |        |
|  |  co: Projects, deliverables, audit (read-only)     |        |
|  +---------------------------------------------------+        |
|           |                                                    |
|           v                                                    |
|  +---------------------------------------------------+        |
|  |              Protected Dashboard Pages             |        |
|  |  Role-based tab visibility per page                |        |
|  |  CUI banner toggle (env var controlled)            |        |
|  |  Activity feed (audit + hook events merged)        |        |
|  +---------------------------------------------------+        |
+---------------------------------------------------------------+
```

### Authentication Flow

1. User navigates to any dashboard page
2. Middleware checks for valid Flask session cookie
3. If no session: redirect to `/login`
4. User enters API key on login page
5. Key is SHA-256 hashed, looked up in `dashboard_api_keys`
6. On match: Flask session created with user_id, role, expiry
7. Session validated on every subsequent request
8. Session expires after 30 minutes of inactivity

---

## 4. Requirements

### 4.1 Authentication

#### REQ-30-001: API Key Authentication
The system SHALL authenticate dashboard users via per-user API keys, hashed with SHA-256 and stored in the `dashboard_api_keys` table within `icdev.db`.

#### REQ-30-002: Self-Contained Auth (D169)
Dashboard authentication SHALL be self-contained against `icdev.db`, with no dependency on the SaaS platform layer, keeping the dashboard independently deployable.

#### REQ-30-003: Flask Signed Sessions (D171)
The system SHALL use Flask's built-in signed sessions for browser access, with the secret key sourced from `ICDEV_DASHBOARD_SECRET` environment variable or auto-generated on first run.

#### REQ-30-004: Session Expiry
Dashboard sessions SHALL expire after 30 minutes of inactivity, requiring re-authentication.

#### REQ-30-005: Authentication Logging
All authentication events (login success, login failure, logout, key generation, key revocation) SHALL be recorded in the `dashboard_auth_log` table for NIST AU-2 compliance.

### 4.2 Role-Based Access Control

#### REQ-30-006: Five Operator Roles (D172)
The system SHALL enforce 5 RBAC roles with distinct page visibility:
- **admin**: Full access to all pages plus user/key management at `/admin/users`
- **pm**: Projects, status, monitoring, wizard, quick-paths, batch, activity
- **developer**: Projects, build, test, agents, monitoring, chat, activity
- **isso**: Compliance, security, audit, agents, monitoring, gateway, activity
- **co**: Projects, deliverables, audit (read-only access)

#### REQ-30-007: Role-Based Tab Visibility
Project detail pages SHALL show or hide tabs (compliance, security, deployments, audit) based on the authenticated user's role.

#### REQ-30-008: Admin User Management
Admin users SHALL be able to create users, assign roles, generate API keys, revoke API keys, and list all users via CLI commands and the `/admin/users` dashboard page.

### 4.3 CUI Banner and Activity Feed

#### REQ-30-009: CUI Banner Toggle (D173)
The system SHALL support toggling CUI banners via the `ICDEV_CUI_BANNER_ENABLED` environment variable (default `true`), while preserving the existing `CUI_BANNER_TOP` and `CUI_BANNER_BOTTOM` env vars for content customization.

#### REQ-30-010: Merged Activity Feed (D174)
The system SHALL provide a merged activity feed at `/activity` that combines entries from `audit_trail` and `hook_events` tables via UNION ALL query, maintaining the append-only contract (D6) with no modification to either source table.

#### REQ-30-011: Activity Feed Delivery
The activity feed SHALL support both WebSocket (via Flask-SocketIO, additive) and HTTP polling for real-time updates, falling back gracefully when SocketIO is unavailable (D170).

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `dashboard_users` | User records: user_id, email, name, role, created_at, active |
| `dashboard_api_keys` | API keys: key_hash (SHA-256), user_id, created_at, last_used, revoked |
| `dashboard_auth_log` | Append-only authentication event log: event_type, user_id, ip_address, timestamp, success |
| `dashboard_user_llm_keys` | BYOK LLM keys: user_id, provider, encrypted_key (Fernet AES-256), created_at |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/dashboard/auth.py` | CLI for admin user management: create-admin, list-users, generate-key, revoke-key |
| `tools/dashboard/app.py` | Enhanced Flask app with auth middleware, session management, RBAC enforcement |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D169 | Dashboard auth is self-contained against `icdev.db` (not imported from SaaS layer) | Keeps dashboard independently deployable; no coupling to Phase 21 SaaS infrastructure |
| D170 | WebSocket via Flask-SocketIO is additive; HTTP polling remains for backward compat | Falls back automatically when SocketIO unavailable; no breaking changes |
| D171 | Session cookies use Flask's built-in signed sessions with `ICDEV_DASHBOARD_SECRET` | Stdlib-compatible, no additional dependencies, air-gap safe |
| D172 | Dashboard RBAC: 5 roles (admin, pm, developer, isso, co) mapped to existing ROLE_VIEWS | Matches organizational structure of Gov/DoD teams without over-engineering |
| D173 | CUI banner toggle via `ICDEV_CUI_BANNER_ENABLED` env var (default true) | Allows non-CUI deployments (ISV, healthcare) to disable banners without code changes |
| D174 | Activity feed merges `audit_trail` + `hook_events` via UNION ALL query | Read-only merge preserves append-only contract (D6); no data duplication |
| D175 | BYOK keys stored AES-256 encrypted in `dashboard_user_llm_keys` table (Fernet) | Per-user keys override department env vars; encrypted at rest for CUI compliance |

---

## 8. Security Gate

**Dashboard Auth Gate:**
- All dashboard pages require authentication (no anonymous access to any page)
- API keys stored as SHA-256 hashes only (plaintext never persisted)
- Failed login attempts rate-limited (5 failures per IP per 15 minutes)
- Session cookies signed with HMAC; tamper-evident
- Admin role required for user management operations
- All auth events recorded in append-only `dashboard_auth_log` (NIST AC-2, AC-3, IA-2, AU-2)
- CUI banners enforced on all pages when `ICDEV_CUI_BANNER_ENABLED=true`
- BYOK keys encrypted with Fernet AES-256 (PBKDF2, 600K iterations) before storage

---

## 9. Commands

```bash
# Dashboard auth management
python tools/dashboard/auth.py create-admin --email admin@icdev.local --name "Admin"   # Create first admin + API key
python tools/dashboard/auth.py list-users            # List all dashboard users

# Start dashboard (with auth enabled)
python tools/dashboard/app.py                        # Start web dashboard on port 5000

# Environment variables
# ICDEV_DASHBOARD_SECRET    — Flask session signing key
# ICDEV_CUI_BANNER_ENABLED  — Toggle CUI banners (default: true)
# ICDEV_BYOK_ENABLED        — Enable BYOK LLM key management (default: false)
# ICDEV_BYOK_ENCRYPTION_KEY — Fernet key for BYOK encryption

# Dashboard pages (auth required)
# /login           — API key login page
# /logout          — Clear session and redirect to login
# /activity        — Merged activity feed (audit + hook events)
# /admin/users     — Admin user/key management (admin role only)
# /profile         — User profile + BYOK LLM key management
# /usage           — Usage tracking + cost dashboard (per-user, per-provider)
```

**CUI // SP-CTI**
