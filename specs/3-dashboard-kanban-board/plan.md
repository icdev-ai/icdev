# CUI // SP-CTI
# Plan: Dashboard Kanban Board

## Phases
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

# CUI // SP-CTI
