# [TEMPLATE: CUI // SP-CTI]
# Feature: Dashboard Kanban Board

## Metadata
issue_number: `3`
run_id: `a8898ca4`

## Feature Description
Replace the static Project Summary Cards (Total Projects, Active Projects, Completed Projects, Active Agents, Firing Alerts, Open POAM) on the dashboard home page (`/`) with an interactive Kanban board. The Kanban board displays project cards organized into columns by status (Planning, Active, Completed, Inactive). Each project card shows the project name, type, classification, and key compliance metrics. This provides a more actionable, visual workflow view compared to the current aggregate number cards.

## User Story
As a program manager or developer
I want to see projects organized in a Kanban board on the dashboard home page
So that I can quickly understand project workflow status and take action on projects in each stage

## Solution Statement
Replace the `.card-grid` section (lines 22-53 of `index.html`) with a horizontal Kanban board that:
1. Fetches all projects from `/api/projects` via JavaScript on page load
2. Organizes projects into 4 status columns: Planning, Active, Completed, Inactive
3. Renders each project as a card with name, type, classification badge, and link to detail page
4. Shows a column count header (e.g., "Active (3)")
5. Falls back to a friendly empty-state message when no projects exist
6. Preserves the existing charts, alerts table, and activity table below the board
7. Retains the summary stats (agents, alerts, POAM) as a compact stat bar above the Kanban board

## ATO Impact Assessment
- **Boundary Impact**: GREEN
- **New NIST Controls**: None — this is a UI-only change to an internal dashboard; no new data flows, components, or external connections
- **SSP Impact**: None
- **Data Classification Change**: No — project data is already served via `/api/projects`

## Relevant Files
- `tools/dashboard/templates/index.html` — Home page template; the card-grid (lines 22-53) will be replaced with Kanban board HTML
- `tools/dashboard/app.py` — Flask route handler for `/`; needs to pass full project list to template (currently only passes counts)
- `tools/dashboard/static/js/kanban.js` — **New file** — Client-side JS to fetch projects from `/api/projects` and render into Kanban columns
- `tools/dashboard/static/css/style.css` — Add Kanban board CSS styles
- `tools/dashboard/api/projects.py` — Existing API; already returns project list with status field — no changes needed

### New Files
- `tools/dashboard/static/js/kanban.js` — Kanban board JavaScript module

## Implementation Plan
### Phase 1: Foundation
- Add Kanban CSS styles to `style.css` (columns layout, project cards, empty states)
- Update the `index()` route in `app.py` to pass full project list (with status, type, classification, id, name) to the template

### Phase 2: Core Implementation
- Rewrite the card-grid section in `index.html` to render a compact stat bar (agents, alerts, POAM) plus a Kanban board container
- Create `kanban.js` to fetch `/api/projects` and render project cards into the correct status columns
- Each Kanban card links to `/projects/<id>` for drill-down

### Phase 3: Integration & Testing
- Update tour steps in `app.py` to reference the new Kanban board instead of `.card-grid`
- Verify the dashboard loads correctly with 0 projects (empty state) and with multiple projects across statuses
- Add unit test for the updated index route

## Step by Step Tasks
IMPORTANT: Execute every step in order, top to bottom.

### Step 1: Write unit test for updated index route (TDD — RED)
- Add a test in `tests/test_dashboard_kanban.py` that verifies the index route:
  - Returns 200
  - Template context includes `projects` list
  - Projects have `id`, `name`, `status`, `type`, `classification` fields
  - Still includes `firing_alerts`, `open_poam`, `active_agents` context vars

### Step 2: Add Kanban CSS to style.css
- Add `.kanban-board` container: horizontal flex layout with gap, overflow-x auto
- Add `.kanban-column`: vertical flex, min-width 280px, flex 1
- Add `.kanban-column-header`: column title with count badge
- Add `.kanban-card`: project card within column (name, type badge, classification badge, link)
- Add `.kanban-empty`: empty column placeholder
- Add `.stat-bar`: compact horizontal stat row for agents/alerts/POAM (replaces 3 of the old cards)

### Step 3: Update `app.py` index route
- Add query to fetch all projects: `SELECT id, name, type, status, classification FROM projects ORDER BY updated_at DESC`
- Pass `projects` list to the template alongside existing variables
- Keep existing `firing_alerts`, `open_poam`, `active_agents`, `inactive_agents`, `total_agents` context vars (used by stat bar and notifications)

### Step 4: Rewrite index.html card-grid section
- Replace the `.card-grid` div (lines 22-53) with:
  1. A compact `.stat-bar` showing: Active Agents, Firing Alerts, Open POAM (inline, horizontal)
  2. A `.kanban-board` container with 4 `.kanban-column` divs: Planning, Active, Completed, Inactive
  3. Each column rendered server-side with Jinja2 loop filtering projects by status
  4. Each project card shows: name (linked to detail), type badge, classification badge
  5. Empty columns show a muted "No projects" message

### Step 5: Create kanban.js for client-side auto-refresh
- Create `tools/dashboard/static/js/kanban.js`
- On DOMContentLoaded, if `.kanban-board` exists, set up a 30-second auto-refresh
- Refresh fetches `/api/projects`, re-renders cards into correct columns
- Uses ICDEV.escapeHTML for safe HTML rendering
- Register in `base.html` script includes

### Step 6: Update tour steps in app.py
- Change the `.card-grid` tour step selector to `.kanban-board`
- Update title from "Summary Cards" to "Project Kanban Board"
- Update description to reference the Kanban workflow view

### Step 7: Add CUI markings to all new/modified Python files
- Verify `# CUI // SP-CTI` header in any new or modified `.py` files

### Step 8: Run tests (GREEN)
- Run `python -m pytest tests/test_dashboard_kanban.py -v` to verify the new test passes
- Run `python -m pytest tests/ -v` to verify no regressions

## Testing Strategy
### Unit Tests
- `tests/test_dashboard_kanban.py`:
  - Test index route returns 200 and includes `projects` in template context
  - Test that projects are dicts with required keys
  - Test that stat bar context vars (firing_alerts, open_poam, etc.) are still present

### BDD Tests
- N/A — no `features/` directory for this project

### Edge Cases
- 0 projects: Kanban columns should show empty-state message
- Projects with unknown status: Should fall into a default column or be excluded
- Long project names: Should truncate with CSS ellipsis
- Many projects (>20): Columns should scroll vertically

## Acceptance Criteria
- Dashboard home page shows a Kanban board with 4 status columns instead of the old 6-card grid
- Each project appears as a card in the correct status column
- Project cards link to `/projects/<id>` detail page
- Agents, Alerts, and POAM counts are still visible in a compact stat bar
- Charts, Recent Alerts table, and Recent Activity table remain unchanged below the Kanban board
- Page loads without JS errors
- Auto-refresh updates the Kanban board every 30 seconds
- Empty state is handled gracefully

## Validation Commands
- `python -m py_compile tools/dashboard/app.py` - Syntax check
- `ruff check .` - Lint check
- `python -m pytest tests/ -v --tb=short` - Unit tests
- `python tools/security/sast_runner.py --project-dir . --json` - SAST scan
- `python tools/security/secret_detector.py --project-dir . --json` - Secret detection
- `python tools/security/dependency_auditor.py --project-dir . --json` - Dependency audit
- `python tools/compliance/sbom_generator.py --project-dir .` - SBOM
- `python tools/compliance/control_mapper.py --activity "code.commit" --project-id "proj-icdev"` - NIST mapping
- `python tools/compliance/crosswalk_engine.py --project-id "proj-icdev" --coverage` - Crosswalk
- `python tools/compliance/stig_checker.py --project-id "proj-icdev"` - STIG check
- `python tools/compliance/fips199_categorizer.py --project-id "proj-icdev" --gate` - FIPS 199
- `python tools/compliance/fips200_validator.py --project-id "proj-icdev" --gate` - FIPS 200
- `python tools/compliance/compliance_detector.py --project-id "proj-icdev" --json` - Framework detection
- `python tools/compliance/multi_regime_assessor.py --project-id "proj-icdev" --gate` - Multi-regime
- `python tools/devsecops/zta_maturity_scorer.py --project-id "proj-icdev" --all --json` - ZTA
- `python tools/compliance/mosa_assessor.py --project-id "proj-icdev" --gate` - MOSA

## NIST 800-53 Controls
- AC-3 (Access Enforcement) — Dashboard enforces role-based views
- AU-2 (Event Logging) — Dashboard changes logged via audit trail
- SI-2 (Flaw Remediation) — UI improvement addresses usability gap

## Notes
- The `/api/projects` endpoint already exists and returns all required fields (id, name, type, status, classification)
- Kanban is rendered server-side via Jinja2 for initial load, then refreshed client-side via JS for auto-refresh
- Project status values from the DB: `planning`, `active`, `completed`, `inactive`
- The chart-grid, Recent Alerts table, and Recent Activity table are preserved unchanged below the Kanban board

# [TEMPLATE: CUI // SP-CTI]
