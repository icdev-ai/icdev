# Phase 31 — Dashboard UX Low Impact

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 31 |
| Title | Dashboard UX -- Low Impact Enhancements |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 10 (Web Dashboard), Phase 30 (Dashboard Authentication & RBAC) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

The ICDEV dashboard serves a diverse audience: program managers tracking schedule risk, ISSOs verifying compliance posture, developers monitoring build pipelines, and contracting officers reviewing deliverables. Many of these users operate in high-stress, time-constrained environments where cognitive overload directly impacts mission effectiveness. The existing dashboard presents raw technical data -- STIG CAT1 counts, POAM identifiers, NIST control families, CVE severity scores -- without any translation layer for non-technical operators.

A program manager seeing "0 CAT1 STIG findings" does not know whether that is good or bad. An ISSO encountering "FedRAMP Moderate baseline: 325 controls" needs to know which controls are satisfied without reading a 200-page SSP. A contracting officer viewing an audit trail full of JSON event types cannot quickly assess whether deliverables are on track.

Furthermore, the dashboard lacks basic usability affordances that users of modern web applications expect: no glossary for domain-specific acronyms, no breadcrumb navigation, no accessibility features (skip-to-content, ARIA labels), no friendly timestamps ("2 hours ago" vs "2026-02-23T14:32:00Z"), no notification system for important events, no error recovery guidance when gates fail, and no role-based filtering to show each persona only what they need.

These are "low impact" changes -- they require no new backend logic, no database schema changes, and no API modifications. They are purely presentation-layer enhancements that transform raw technical output into actionable, role-appropriate information.

---

## 2. Goals

1. Implement a glossary tooltip system using `data-glossary` HTML attributes and client-side JavaScript, providing plain-English definitions for every Gov/DoD and ICDEV-specific acronym on hover
2. Add friendly timestamps throughout the dashboard ("2 hours ago", "yesterday", "last Tuesday") alongside ISO-8601 precision timestamps for auditability
3. Provide breadcrumb navigation on all pages for spatial orientation within the dashboard hierarchy
4. Implement ARIA accessibility features: skip-to-content link, role attributes, aria-labels, focus management, and WCAG 2.1 AA compliance on all interactive elements
5. Add notification toasts for important events (gate failures, build completions, compliance alerts) with auto-dismiss and persistence options
6. Create an error recovery dictionary that maps gate failure codes to plain-English fix instructions with who/what/why/fix/estimated-time fields so non-technical users can self-serve
7. Implement role-based views via `?role=` query parameter with Flask context processor for progressive disclosure by persona (pm, developer, isso, co)
8. Add help icons next to complex metrics with expandable explanations

---

## 3. Architecture

```
+---------------------------------------------------------------+
|                    Dashboard UX Layer (Low Impact)              |
|                                                                |
|  +-------------------+    +-----------------------------+      |
|  | Jinja2 Filters    |    | JavaScript Modules          |      |
|  | friendly_time()   |    | glossary.js (tooltips)       |      |
|  | breadcrumb()      |    | notifications.js (toasts)    |      |
|  | help_icon()       |    | accessibility.js (skip-to,   |      |
|  | role_visible()    |    |   focus management)          |      |
|  +-------------------+    +-----------------------------+      |
|                                                                |
|  +-------------------+    +-----------------------------+      |
|  | UX Helpers        |    | Error Recovery Dictionary   |      |
|  | tools/dashboard/  |    | Gate failure code ->         |      |
|  |   ux_helpers.py   |    | plain-English instructions   |      |
|  | Role views, term  |    | who/what/why/fix/est-time   |      |
|  | definitions,      |    |                             |      |
|  | progress pipeline |    |                             |      |
|  +-------------------+    +-----------------------------+      |
+---------------------------------------------------------------+
```

### Role-Based View Filtering

| Role | Visible Sections | Hidden Sections |
|------|-----------------|-----------------|
| pm | Project status, schedule, risk, deliverables, activity | Security scan details, STIG internals, agent config |
| developer | Build status, test results, code metrics, agents, deployments | Compliance scores, POAM details, ATO status |
| isso | Compliance posture, security findings, audit trail, agents | Build internals, code metrics |
| co | Project status, deliverables, audit trail (read-only) | Security details, build details, agent config |

---

## 4. Requirements

### 4.1 Glossary and Tooltips

#### REQ-31-001: Glossary Tooltip System
The system SHALL implement a glossary tooltip system using `data-glossary` HTML attributes on domain-specific terms, with client-side JavaScript rendering plain-English definitions on hover.

#### REQ-31-002: Comprehensive Term Coverage
The glossary SHALL include definitions for all Gov/DoD acronyms (ATO, SSP, POAM, STIG, CUI, SBOM, FedRAMP, CMMC, cATO, IL2-IL6), ICDEV-specific terms (GOTCHA, ATLAS, RICOAS), and compliance concepts (CAT1/CAT2/CAT3, control families).

#### REQ-31-003: No Backend Changes
The glossary system SHALL be implemented entirely in client-side JavaScript with no backend API calls, database queries, or server-side rendering changes required.

### 4.2 Timestamps and Navigation

#### REQ-31-004: Friendly Timestamps
The system SHALL display friendly timestamps ("2 hours ago", "yesterday", "3 days ago") alongside ISO-8601 precision timestamps (shown on hover or in title attribute) throughout the dashboard.

#### REQ-31-005: Breadcrumb Navigation
Every dashboard page SHALL include breadcrumb navigation showing the current page's position in the hierarchy (e.g., Home > Projects > proj-123 > Compliance).

### 4.3 Accessibility

#### REQ-31-006: Skip-to-Content Link
Every dashboard page SHALL include a skip-to-content link as the first focusable element, visible on keyboard focus, for WCAG 2.1 AA compliance.

#### REQ-31-007: ARIA Attributes
All interactive elements (buttons, links, form controls, status indicators) SHALL include appropriate ARIA roles, labels, and state attributes.

#### REQ-31-008: Focus Management
The system SHALL manage focus appropriately during page transitions, modal openings, and notification appearances, ensuring keyboard-only users can navigate the full dashboard.

### 4.4 Notifications and Error Recovery

#### REQ-31-009: Notification Toasts
The system SHALL display notification toasts for important events (gate failures, build completions, compliance alerts) with configurable auto-dismiss timing and a manual dismiss option.

#### REQ-31-010: Error Recovery Dictionary (D92)
The system SHALL maintain an error recovery dictionary mapping gate failure codes to plain-English fix instructions containing: who should fix it, what failed, why it matters, how to fix it, and estimated time to resolution.

### 4.5 Role-Based Views

#### REQ-31-011: Role Query Parameter (D90)
The system SHALL support role-based views via `?role=` query parameter, with a Flask context processor providing role information to all templates for progressive disclosure by persona.

#### REQ-31-012: Help Icons
Complex metrics and compliance scores SHALL include help icons that expand to show plain-English explanations of what the metric means and what constitutes a good or bad value.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| (No new tables) | Phase 31 is presentation-layer only; all data comes from existing tables |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/dashboard/ux_helpers.py` | UX translation functions: glossary terms, role views, error recovery dictionary, progress pipeline rendering, Quick Path templates |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D88 | UX Translation Layer wraps existing tools without rewriting them | Jinja2 filters + JS modules convert technical output to business-friendly display; zero backend changes |
| D89 | Glossary tooltip system uses `data-glossary` HTML attributes + client-side JS | No backend changes needed to add new terms; air-gap safe, self-contained |
| D90 | Role-based views via `?role=` query parameter + Flask context processor | No authentication required for role filtering; progressive disclosure by persona |
| D91 | Getting Started wizard uses declarative path mapping (goal x role x classification) | Add new paths without code changes; guides new users to recommended workflows |
| D92 | Error recovery dictionary maps gate failure codes to plain-English fix instructions | Non-technical users (PMs, COs) can self-serve without requiring developer assistance |
| D93 | Quick Path templates are declarative data (list of dicts in ux_helpers.py) | Add new workflow shortcuts without touching templates |

---

## 8. Security Gate

**No dedicated security gate for Phase 31.**

Phase 31 is a presentation-layer enhancement with no new data exposure, no new APIs, and no new database tables. Security is enforced by:
- Phase 30 authentication and RBAC (all pages require login)
- Role-based views restrict information visibility per persona
- CUI banners remain enforced on all pages
- No client-side JavaScript makes API calls or modifies data

---

## 9. Commands

```bash
# Start dashboard with UX enhancements (all low-impact features auto-enabled)
python tools/dashboard/app.py

# Role-based view filtering (append to any dashboard URL)
# /projects?role=pm           — PM-focused project view
# /projects?role=developer    — Developer-focused project view
# /projects?role=isso         — ISSO-focused project view
# /projects?role=co           — CO-focused project view

# Dashboard pages with UX enhancements
# /wizard            — Getting Started wizard (3 questions -> workflow recommendation)
# /quick-paths       — Quick Path workflow templates + error recovery reference
```

**CUI // SP-CTI**
