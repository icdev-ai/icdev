# CUI // SP-CTI
# Tasks: Dashboard Kanban Board

## Status
- [ ] Not started

## Steps
### Step 1: Write unit test for updated index route (TDD — RED)
- [ ] Add a test in `tests/test_dashboard_kanban.py` that verifies the index route:
  - [ ] Returns 200
  - [ ] Template context includes `projects` list
  - [ ] Projects have `id`, `name`, `status`, `type`, `classification` fields
  - [ ] Still includes `firing_alerts`, `open_poam`, `active_agents` context vars

<!-- Parallel group: steps 2, 3, 4, 5, 6, 7, 8 -->
### [P] Step 2: Add Kanban CSS to style.css
- [ ] Add `.kanban-board` container: horizontal flex layout with gap, overflow-x auto
- [ ] Add `.kanban-column`: vertical flex, min-width 280px, flex 1
- [ ] Add `.kanban-column-header`: column title with count badge
- [ ] Add `.kanban-card`: project card within column (name, type badge, classification badge, link)
- [ ] Add `.kanban-empty`: empty column placeholder
- [ ] Add `.stat-bar`: compact horizontal stat row for agents/alerts/POAM (replaces 3 of the old cards)

### [P] Step 3: Update `app.py` index route
- [ ] Add query to fetch all projects: `SELECT id, name, type, status, classification FROM projects ORDER BY updated_at DESC`
- [ ] Pass `projects` list to the template alongside existing variables
- [ ] Keep existing `firing_alerts`, `open_poam`, `active_agents`, `inactive_agents`, `total_agents` context vars (used by stat bar and notifications)

### [P] Step 4: Rewrite index.html card-grid section
- [ ] Replace the `.card-grid` div (lines 22-53) with:
  - [ ] A compact `.stat-bar` showing: Active Agents, Firing Alerts, Open POAM (inline, horizontal)
  - [ ] A `.kanban-board` container with 4 `.kanban-column` divs: Planning, Active, Completed, Inactive
  - [ ] Each column rendered server-side with Jinja2 loop filtering projects by status
  - [ ] Each project card shows: name (linked to detail), type badge, classification badge
  - [ ] Empty columns show a muted "No projects" message

### [P] Step 5: Create kanban.js for client-side auto-refresh
- [ ] Create `tools/dashboard/static/js/kanban.js`
- [ ] On DOMContentLoaded, if `.kanban-board` exists, set up a 30-second auto-refresh
- [ ] Refresh fetches `/api/projects`, re-renders cards into correct columns
- [ ] Uses ICDEV.escapeHTML for safe HTML rendering
- [ ] Register in `base.html` script includes

### [P] Step 6: Update tour steps in app.py
- [ ] Change the `.card-grid` tour step selector to `.kanban-board`
- [ ] Update title from "Summary Cards" to "Project Kanban Board"
- [ ] Update description to reference the Kanban workflow view

### [P] Step 7: Add CUI markings to all new/modified Python files
- [ ] Verify `# CUI // SP-CTI` header in any new or modified `.py` files

### [P] Step 8: Run tests (GREEN)
- [ ] Run `python -m pytest tests/test_dashboard_kanban.py -v` to verify the new test passes
- [ ] Run `python -m pytest tests/ -v` to verify no regressions

# CUI // SP-CTI
