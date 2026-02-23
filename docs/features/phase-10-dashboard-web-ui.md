# Phase 10 — Dashboard Web UI

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 10 |
| Title | Dashboard Web UI |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 8 (Self-Healing System), Phase 9 (Monitoring & Observability) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV operates as a CLI-driven agentic system, which is effective for developers and engineers but inaccessible to program managers, ISSOs, contracting officers, and other stakeholders who need visibility into project status, compliance posture, and security findings. These users should not need to run Python scripts or parse JSON to understand whether a system is ready for ATO.

Gov/DoD programs require audit-ready dashboards that display CUI markings on every page, provide role-based views for different stakeholders, and render entirely server-side to minimize the STIG attack surface. Client-side JavaScript frameworks (React, Angular, Vue) introduce thousands of transitive dependencies, each a potential vulnerability. A server-side rendered Flask application with Jinja2 templates produces auditable HTML with zero NPM dependencies.

The dashboard must be "GI proof" -- meaning it must be usable by personnel with minimal technical background. Glossary tooltips explain compliance jargon, friendly timestamps replace ISO formats, breadcrumbs provide navigation context, and ARIA accessibility attributes ensure Section 508 compliance. The UX translation layer converts raw technical tool output into business-friendly displays without modifying the underlying deterministic tools.

---

## 2. Goals

1. Provide a Flask-based server-side rendered web dashboard on port 5000 with CUI banners on every page, CSRF protection, and Content-Security-Policy headers
2. Display project status, compliance posture (SSP, POAM, STIG, SBOM), security scan results, deployment history, and agent health in a unified interface
3. Implement role-based views (admin, pm, developer, isso, co) via query parameter, providing progressive disclosure of information appropriate to each persona
4. Deliver "GI-proof" UX with glossary tooltips, friendly timestamps, breadcrumbs, ARIA accessibility, skip-to-content links, notification toasts, and keyboard shortcuts
5. Provide a REST API for programmatic access to all dashboard data
6. Support SSE (Server-Sent Events) for near-real-time updates with connection status indicators and 3-second debounced batches
7. Include zero-dependency SVG chart library (sparklines, line, bar, donut, gauge) and auto-enhancing table interactivity (search, sort, filter, CSV export)
8. Implement API key authentication with SHA-256 hashed keys, Flask signed sessions, and 5-role RBAC (admin, pm, developer, isso, co)

---

## 3. Architecture

```
+-----------------------------------------------------------+
|                  Flask Dashboard (Port 5000)               |
|                                                           |
|  +-----------+  +------------------+  +---------------+   |
|  | Jinja2    |  | REST API         |  | SSE Endpoint  |   |
|  | Templates |  | /api/*           |  | /events       |   |
|  | (SSR)     |  | (JSON)           |  | (live)        |   |
|  +-----------+  +------------------+  +---------------+   |
|       |                |                     |            |
|  +-----------+  +------------------+  +---------------+   |
|  | Static    |  | Auth Middleware   |  | UX Helpers    |   |
|  | CSS/JS    |  | API Key + RBAC   |  | Glossary,     |   |
|  | (minimal) |  | (SHA-256 hash)   |  | Timestamps,   |   |
|  +-----------+  +------------------+  | Breadcrumbs   |   |
|                                       +---------------+   |
+----------------------------+------------------------------+
                             |
                             v
+-----------------------------------------------------------+
|              Data Layer (SQLite)                           |
|                                                           |
|  data/icdev.db    — Projects, compliance, agents, audit   |
|  data/platform.db — Dashboard users, API keys, auth log   |
+-----------------------------------------------------------+
```

### Dashboard Pages

| Route | Page | Description |
|-------|------|-------------|
| `/` | Home | Auto-notifications, project overview, compliance summary |
| `/projects` | Projects | Project listing with friendly timestamps |
| `/projects/<id>` | Project Detail | Role-based tab visibility (overview, compliance, security, deploy, audit) |
| `/agents` | Agents | Agent registry with heartbeat age |
| `/monitoring` | Monitoring | Status icons, accessibility, health checks |
| `/wizard` | Getting Started | 3-question wizard recommending workflows |
| `/quick-paths` | Quick Paths | Workflow templates + error recovery reference |
| `/events` | Events | Real-time SSE event timeline |
| `/query` | NLQ Query | Natural language compliance queries |
| `/chat` | Agent Chat | SQLite-based agent chat interface |
| `/activity` | Activity Feed | Merged audit + hook events feed |
| `/login` | Login | API key authentication |
| `/admin/users` | Admin | User/key management (admin role only) |

---

## 4. Requirements

### 4.1 Core Dashboard

#### REQ-10-001: Flask Server-Side Rendering
The system SHALL use Flask with Jinja2 templates for server-side rendering, producing auditable HTML with no client-side JavaScript frameworks.

#### REQ-10-002: CUI Markings
Every dashboard page SHALL include top and bottom CUI banners ("CUI // SP-CTI"), configurable via `ICDEV_CUI_BANNER_ENABLED` environment variable.

#### REQ-10-003: Project Status Views
The system SHALL display project status with compliance posture (SSP, POAM, STIG, SBOM status), security scan results, deployment history, and audit trail scoped to each project.

#### REQ-10-004: Agent Health Display
The system SHALL display agent health in a grid view showing health status, heartbeat age, task queue depth, recent completions, and error rates per agent.

### 4.2 Authentication and RBAC

#### REQ-10-005: API Key Authentication
The system SHALL authenticate users via API keys (SHA-256 hashed, stored in `dashboard_api_keys` table), with Flask signed sessions using `ICDEV_DASHBOARD_SECRET` environment variable.

#### REQ-10-006: Role-Based Access Control
The system SHALL enforce 5-role RBAC (admin, pm, developer, isso, co) with role-based page visibility and tab-level access control.

### 4.3 UX and Accessibility

#### REQ-10-007: GI-Proof UX
The system SHALL provide glossary tooltips (via `data-glossary` HTML attributes), friendly timestamps, breadcrumb navigation, ARIA accessibility attributes, skip-to-content links, and Section 508 compliance.

#### REQ-10-008: Keyboard Navigation
The system SHALL support keyboard shortcuts using chord pattern (`g` + key) for page navigation, `?` for help modal, and `/` for search, with a 1.5-second chord window.

### 4.4 Live Updates and Charts

#### REQ-10-009: SSE Live Updates
The system SHALL use Server-Sent Events for near-real-time dashboard updates, debounced to 3-second batches, with a connection status indicator.

#### REQ-10-010: Zero-Dependency Charts
The system SHALL provide SVG-based chart rendering (sparkline, line, bar, donut, gauge) with zero external JavaScript dependencies, WCAG accessible with `role="img"` and `aria-label` attributes.

#### REQ-10-011: Table Interactivity
The system SHALL auto-enhance all `.table-container` tables with search, sort, filter, and CSV export functionality via a self-contained JavaScript module.

### 4.5 Security Hardening

#### REQ-10-012: Security Headers
The system SHALL set Content-Security-Policy headers, CSRF tokens on all forms, session timeout at 30 minutes, HTTPS-only in production, and rate limiting on API endpoints.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `dashboard_users` | Dashboard user accounts with name, email, role, and status |
| `dashboard_api_keys` | SHA-256 hashed API keys linked to users with expiration and scope |
| `dashboard_auth_log` | Append-only authentication event log (login, logout, failed attempts) |
| `dashboard_user_llm_keys` | BYOK LLM keys, Fernet AES-256 encrypted (per-user, per-provider) |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/dashboard/app.py` | Flask application factory, route registration, SSE endpoints |
| `tools/dashboard/config.py` | Configuration loading from args/*.yaml |
| `tools/dashboard/auth.py` | User creation, API key management, authentication middleware |
| `tools/dashboard/static/js/charts.js` | Zero-dependency SVG chart library (sparkline, line, bar, donut, gauge) |
| `tools/dashboard/static/js/tables.js` | Auto-enhancing table interactivity (search, sort, filter, CSV export) |
| `tools/dashboard/static/js/tour.js` | First-visit onboarding tour with spotlight overlay |
| `tools/dashboard/static/js/live.js` | SSE auto-refresh with connection status indicator |
| `tools/dashboard/static/js/shortcuts.js` | Keyboard chord navigation (`g` + key pattern) |
| `tools/dashboard/static/js/batch.js` | Batch operation workflows (ATO, Security, Compliance, Build) |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D3 | Flask over FastAPI | Simpler, fewer dependencies, auditable SSR, smaller STIG surface |
| D29 | SSE over WebSocket for live updates | Flask-native, simpler, no additional dependencies, unidirectional sufficient |
| D88 | UX Translation Layer wraps existing tools | Jinja2 filters + JS modules convert technical output to business-friendly display without modifying tools |
| D89 | Glossary tooltip system uses `data-glossary` HTML attributes | No backend changes needed to add new terms |
| D90 | Role-based views via `?role=` query parameter | No authentication required for progressive disclosure, RBAC layered separately |
| D94 | SVG chart library is zero-dependency | No Chart.js/D3 needed, air-gap safe, WCAG accessible |
| D95 | Table interactivity auto-enhances on page load | No per-table configuration needed, self-contained module |
| D98 | Onboarding tour uses localStorage | No server-side user tracking, air-gap safe |
| D99 | SSE live updates debounce to 3-second batches | Prevents API hammering while keeping dashboard near-real-time |
| D169 | Dashboard auth is self-contained against icdev.db | Keeps dashboard independently deployable, not coupled to SaaS layer |
| D171 | Session cookies use Flask built-in signed sessions | Secret from ICDEV_DASHBOARD_SECRET env var or auto-generated |
| D172 | 5-role RBAC (admin, pm, developer, isso, co) | Maps to existing ROLE_VIEWS for page visibility |

---

## 8. Security Gate

**Dashboard Security Gate:**
- CUI banners present on every rendered page (configurable via `ICDEV_CUI_BANNER_ENABLED`)
- CSRF token required on all form submissions
- Content-Security-Policy headers set on all responses
- No inline JavaScript permitted
- Session timeout enforced at 30 minutes
- API key authentication required for all non-login routes
- BYOK keys encrypted with AES-256 (Fernet) before storage
- Admin-only access to user management routes (`/admin/users`)

---

## 9. Commands

```bash
# Start web dashboard
python tools/dashboard/app.py

# Dashboard auth management
python tools/dashboard/auth.py create-admin --email admin@icdev.local --name "Admin"
python tools/dashboard/auth.py list-users

# Environment variables
# ICDEV_DASHBOARD_SECRET     — Flask session signing key
# ICDEV_CUI_BANNER_ENABLED   — Toggle CUI banners (default: true)
# ICDEV_BYOK_ENABLED          — Enable bring-your-own LLM keys (default: false)
# ICDEV_BYOK_ENCRYPTION_KEY   — Fernet key for BYOK encryption
```

**CUI // SP-CTI**
