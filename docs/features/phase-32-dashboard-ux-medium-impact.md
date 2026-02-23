# Phase 32 — Dashboard UX Medium Impact

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 32 |
| Title | Dashboard UX -- Medium Impact Enhancements |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 10 (Web Dashboard), Phase 30 (Dashboard Authentication & RBAC), Phase 31 (Dashboard UX Low Impact) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Phase 31 addressed presentation-layer gaps with glossary tooltips, friendly timestamps, breadcrumbs, accessibility, and role-based views. These low-impact changes improved readability and navigation, but the dashboard still lacks interactive capabilities that operators need for daily workflow efficiency.

Compliance posture data exists in the database but is only visible as tabular text. There are no charts showing trends over time, no visual indicators of improvement or regression, and no at-a-glance gauges for readiness scores. Tables cannot be sorted, filtered, or exported, forcing operators to manually scan hundreds of rows. There is no onboarding experience for new users, no real-time updates without manual page refresh, no way to run multi-tool workflows from the UI, and no keyboard shortcuts for power users.

These are "medium impact" changes -- they introduce new client-side JavaScript modules (charts.js, tables.js, tour.js, live.js, batch.js, shortcuts.js) that enhance interactivity without modifying backend APIs or database schemas. Each module is zero-dependency, self-contained, and air-gap safe, following the principle that all modules inject styles via JavaScript (D102) to avoid additional CSS files.

The cumulative effect of Phases 30-32 transforms the dashboard from a static data viewer into an interactive operational console that operators can use as their primary workflow interface.

---

## 2. Goals

1. Implement an SVG chart library (`charts.js`) with zero external dependencies that renders sparkline, line, bar, donut, and gauge chart types from server data, with WCAG-accessible `role="img"` and `aria-label` attributes (D94)
2. Build table interactivity (`tables.js`) that auto-enhances all `.table-container` tables with search, column sorting, filtering, and CSV export -- no per-table configuration required (D95)
3. Create a first-visit onboarding tour (`tour.js`) with spotlight overlay, step-by-step guidance, and localStorage-based completion tracking (`icdev_tour_completed`) for air-gap safe detection (D98)
4. Add SSE-based live updates (`live.js`) with 3-second debounced batches, connection status indicator, and automatic reconnection on failure (D99)
5. Implement batch operations (`batch.js`) with 4 built-in workflows (ATO Package, Security Scan, Compliance Check, Full Build) runnable from the UI, executing as sequential subprocesses in background threads (D100)
6. Add keyboard shortcuts (`shortcuts.js`) using a chord pattern (`g` + key) with 1.5-second chord window for navigation, `?` for help modal, and `/` for search focus (D101)

---

## 3. Architecture

```
+---------------------------------------------------------------+
|                Dashboard UX Layer (Medium Impact)               |
|                                                                |
|  +-------------------+    +-----------------------------+      |
|  | charts.js         |    | tables.js                    |      |
|  | SVG chart library |    | Auto-enhance all tables      |      |
|  | 5 chart types     |    | Search, sort, filter, CSV    |      |
|  | Zero deps, WCAG   |    | No per-table config needed   |      |
|  +-------------------+    +-----------------------------+      |
|                                                                |
|  +-------------------+    +-----------------------------+      |
|  | tour.js           |    | live.js                      |      |
|  | Onboarding tour   |    | SSE live updates             |      |
|  | Spotlight overlay  |    | 3s debounce batching         |      |
|  | localStorage      |    | Connection status indicator  |      |
|  | first-visit detect |    | Auto-reconnect on failure    |      |
|  +-------------------+    +-----------------------------+      |
|                                                                |
|  +-------------------+    +-----------------------------+      |
|  | batch.js          |    | shortcuts.js                 |      |
|  | Batch operations   |    | Keyboard navigation          |      |
|  | 4 built-in flows  |    | g+key chord (1.5s window)    |      |
|  | Background threads |    | ? help, / search             |      |
|  | Status polling     |    | No browser conflicts         |      |
|  +-------------------+    +-----------------------------+      |
+---------------------------------------------------------------+
```

### Chart Types

| Type | Use Case | Example |
|------|----------|---------|
| Sparkline | Inline trend indicators | Compliance score trend in table cell |
| Line | Time-series metrics | Build success rate over 30 days |
| Bar | Comparative values | STIG findings by category (CAT1/CAT2/CAT3) |
| Donut | Proportional breakdown | Control satisfaction status |
| Gauge | Single-value readiness | ATO readiness score (0-100%) |

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `g` + `h` | Navigate to Home |
| `g` + `p` | Navigate to Projects |
| `g` + `a` | Navigate to Agents |
| `g` + `m` | Navigate to Monitoring |
| `g` + `c` | Navigate to Chat |
| `?` | Show keyboard shortcuts help modal |
| `/` | Focus search input |
| `Escape` | Close modal / cancel chord |

---

## 4. Requirements

### 4.1 SVG Chart Library

#### REQ-32-001: Zero-Dependency SVG Charts (D94)
The system SHALL implement an SVG chart library in `charts.js` with no external dependencies (no Chart.js, no D3.js), rendering charts from server-provided data as lightweight SVG elements.

#### REQ-32-002: Five Chart Types
The chart library SHALL support sparkline, line, bar, donut, and gauge chart types, each with configurable colors, labels, and dimensions.

#### REQ-32-003: WCAG Accessibility
All charts SHALL include `role="img"` and `aria-label` attributes with human-readable descriptions of the chart data for screen reader users.

### 4.2 Table Interactivity

#### REQ-32-004: Auto-Enhanced Tables (D95)
The system SHALL implement table interactivity in `tables.js` that auto-enhances all tables within `.table-container` elements on page load, requiring no per-table configuration.

#### REQ-32-005: Search, Sort, Filter, Export
Enhanced tables SHALL support full-text search across all columns, click-to-sort on any column header, column value filtering, and one-click CSV export of visible data.

### 4.3 Onboarding Tour

#### REQ-32-006: First-Visit Tour (D98)
The system SHALL implement an onboarding tour in `tour.js` that activates on first visit, using `localStorage` key `icdev_tour_completed` for detection (no server-side user tracking, air-gap safe).

#### REQ-32-007: Spotlight Overlay
The tour SHALL use a spotlight overlay that highlights the current step's target element with a semi-transparent backdrop, step counter, and next/previous/skip controls.

### 4.4 Live Updates

#### REQ-32-008: SSE Live Updates (D99)
The system SHALL implement Server-Sent Events (SSE) based live updates in `live.js`, debouncing to 3-second batches to prevent API hammering while maintaining near-real-time dashboard state.

#### REQ-32-009: Connection Status Indicator
The live update module SHALL display a visible connection status indicator showing connected (green), reconnecting (yellow), or disconnected (red) state.

### 4.5 Batch Operations

#### REQ-32-010: Four Built-In Workflows (D100)
The system SHALL implement batch operations in `batch.js` with 4 pre-configured workflows: ATO Package (SSP + POAM + STIG + SBOM), Security Scan (SAST + deps + secrets + container), Compliance Check (multi-framework assessment), and Full Build (scaffold + generate + test + lint).

#### REQ-32-011: Background Execution
Batch operations SHALL execute as sequential subprocesses in background threads, with the Flask request returning immediately and the frontend polling for status updates.

### 4.6 Keyboard Shortcuts

#### REQ-32-012: Chord Pattern Navigation (D101)
The system SHALL implement keyboard shortcuts in `shortcuts.js` using a chord pattern (`g` + key) with a 1.5-second chord window, cancelled on invalid key, to avoid conflicts with browser shortcuts.

#### REQ-32-013: Help Modal
Pressing `?` SHALL display a modal listing all available keyboard shortcuts with their descriptions.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| (No new tables) | Phase 32 is client-side JavaScript only; all data comes from existing APIs |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/dashboard/static/js/charts.js` | Zero-dependency SVG chart library (sparkline, line, bar, donut, gauge) |
| `tools/dashboard/static/js/tables.js` | Auto-enhancing table interactivity (search, sort, filter, CSV export) |
| `tools/dashboard/static/js/tour.js` | First-visit onboarding tour with spotlight overlay |
| `tools/dashboard/static/js/live.js` | SSE live updates with 3-second debounce and connection status |
| `tools/dashboard/static/js/batch.js` | Batch operations UI with 4 built-in workflows |
| `tools/dashboard/static/js/shortcuts.js` | Keyboard shortcuts with g+key chord pattern |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D94 | SVG chart library is zero-dependency, renders server data into lightweight SVG | No Chart.js/D3 needed; air-gap safe; WCAG accessible with role="img" and aria-label |
| D95 | Table interactivity auto-enhances all `.table-container` tables on page load | No per-table configuration; search, sort, filter, CSV export with zero setup |
| D96 | CLI output formatter uses only Python stdlib (ANSI codes, os.get_terminal_size) | `--human` flag on any tool for colored tables/banners/scores instead of JSON |
| D98 | Onboarding tour uses localStorage (`icdev_tour_completed`) for first-visit detection | No server-side user tracking; air-gap safe; no additional API endpoints |
| D99 | SSE live updates debounce to 3-second batches | Prevents API hammering while keeping dashboard near-real-time |
| D100 | Batch operations run as sequential subprocesses in background threads | Flask request returns immediately; frontend polls status; no blocking |
| D101 | Keyboard shortcuts use chord pattern (g + key) to avoid conflicts with browser shortcuts | 1.5-second chord window; cancelled on invalid key; familiar pattern from GitHub/GitLab |
| D102 | All Medium Impact UX modules inject styles via JS (no additional CSS files) | Consistent with ux.js pattern; self-contained modules with no external style dependencies |

---

## 8. Security Gate

**No dedicated security gate for Phase 32.**

Phase 32 is a client-side JavaScript enhancement with no new APIs, no new data exposure, and no database changes. Security is enforced by:
- Phase 30 authentication and RBAC (all pages require login)
- Batch operations execute existing tools with existing permissions
- No client-side JavaScript bypasses server-side access controls
- CSV export respects role-based visibility (exports only visible data)
- All SSE connections authenticated via existing session cookies

---

## 9. Commands

```bash
# Start dashboard (all UX enhancements auto-enabled)
python tools/dashboard/app.py

# CLI output formatting (any tool that supports --json also supports --human)
python tools/compliance/stig_checker.py --project-id "proj-123" --human
python tools/maintenance/maintenance_auditor.py --project-id "proj-123" --human

# Programmatic chart/table usage
# from tools.cli.output_formatter import format_table, format_banner, format_score
# print(format_table(["Name", "Status"], [["App1", "healthy"], ["App2", "degraded"]]))

# Dashboard batch operations (via UI at /batch)
# ATO Package:     SSP + POAM + STIG + SBOM generation
# Security Scan:   SAST + dependency audit + secret detection + container scan
# Compliance Check: Multi-framework compliance assessment
# Full Build:      Scaffold + generate + test + lint
```

**CUI // SP-CTI**
