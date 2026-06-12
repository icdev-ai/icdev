# Network Canvas: 3D Rack Elevation View — Research

**Task ID:** task-b82b424237
**Tier:** 4
**Type:** Research
**Date:** 2026-03-27

---

## Feature Summary

When a user clicks a rack-type device on the 2D JointJS topology canvas, a side-panel slides out showing a 3D rack elevation view with:

- **U-position** of each mounted device (rack units, 1U–48U)
- **Power draw** per device (watts) and rack total
- **Weight** per device (lbs/kg) and rack total
- **Airflow** direction (front-to-back, top-exhaust, etc.) color-coded by heat density
- **Bidirectional link**: clicking a device in the rack elevation highlights the corresponding node on the JointJS topology, and vice versa

---

## Current State

The Network Canvas has **no 3D or rack elevation capabilities** today:

- Visualization: 2D JointJS SVG graph only
- No rack unit (RU/U) fields in `nc_objects` or any config JSON schema
- No Three.js, Babylon.js, WebGL, or rack-specific JS libraries loaded
- NetBox integration pulls rack data (`/api/netbox/pull/racks`) but it is not surfaced in the canvas UI
- No DB table tracks physical rack properties (U count, power capacity, weight capacity, airflow type)

---

## Recommended Implementation Approach

### Rendering Library: CSS 3D Transform (No New JS Dependencies)

Given air-gap constraints and the existing vendor-only pattern, **avoid adding Three.js or Babylon.js** (large bundles, CDN-only). Instead use:

**Option A — Pure CSS 3D Isometric Rack (Recommended)**
- Render rack elevation as an isometric perspective using CSS `transform: rotateX() rotateY()` on `<div>` elements
- Each U slot is a `<div>` row with device label, colored by heat/power
- Side-panel SVG overlay handles the isometric "face" of the rack cabinet
- ~200 lines of CSS + ~400 lines of JS — no new dependencies
- Performant, air-gap safe, mobile-friendly

**Option B — SVG-Based 2D Elevation with Pseudo-3D Shadow**
- Render as a flat front-elevation SVG with isometric shadow/depth illusion
- Simpler but less visually impressive
- Reuses JointJS SVG rendering pipeline

**Option C — Three.js WebGL 3D** *(not recommended)*
- Full 3D rotation and zoom
- Requires vendoring Three.js (~600KB min) — conflicts with air-gap policy
- Overkill for a side-panel rack view

**Decision: Option A** — CSS 3D isometric rendering, vendored JS only if needed (likely zero new deps).

---

## Data Model Changes

### New Table: `nc_racks`

```sql
CREATE TABLE nc_racks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topology_id  TEXT    NOT NULL REFERENCES topologies(id) ON DELETE CASCADE,
    node_id      TEXT    NOT NULL,          -- JointJS cell id of the rack object
    label        TEXT    NOT NULL DEFAULT 'Rack',
    rack_units   INTEGER NOT NULL DEFAULT 42,
    power_capacity_w   INTEGER DEFAULT 10000,
    weight_capacity_kg REAL    DEFAULT 1000,
    airflow_type TEXT    DEFAULT 'front-to-back'  -- front-to-back | top-exhaust | side-exhaust
        CHECK(airflow_type IN ('front-to-back','top-exhaust','side-exhaust')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','utc')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now','utc'))
);
```

### New Table: `nc_rack_slots`

```sql
CREATE TABLE nc_rack_slots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rack_id      INTEGER NOT NULL REFERENCES nc_racks(id) ON DELETE CASCADE,
    node_id      TEXT    NOT NULL,          -- JointJS cell id of the mounted device
    u_start      INTEGER NOT NULL,          -- bottom-most U occupied (1-indexed from bottom)
    u_height     INTEGER NOT NULL DEFAULT 1,
    label        TEXT,
    device_type  TEXT,
    power_draw_w REAL    DEFAULT 0,
    weight_kg    REAL    DEFAULT 0,
    airflow_dir  TEXT    DEFAULT 'front-to-back'
        CHECK(airflow_dir IN ('front-to-back','top-exhaust','side-exhaust','none')),
    color        TEXT    DEFAULT '#4a90d9',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now','utc'))
);
```

### `nc_objects` Extension

Add a JSON field to existing device config to store `rack_id` and `u_start` for bidirectional lookups (no schema migration needed — stored inside existing `config_json` JSONB blob).

---

## Backend API Endpoints (new, on `blueprint.py`)

| Method | Route | Purpose |
|--------|-------|---------|
| GET    | `/api/racks/<topo_id>` | List all racks in topology |
| POST   | `/api/racks/<topo_id>` | Create rack record for a node |
| GET    | `/api/racks/<topo_id>/<rack_id>` | Get rack + all slots |
| PUT    | `/api/racks/<topo_id>/<rack_id>` | Update rack metadata |
| DELETE | `/api/racks/<topo_id>/<rack_id>` | Remove rack |
| GET    | `/api/racks/<topo_id>/<rack_id>/slots` | List all slots |
| POST   | `/api/racks/<topo_id>/<rack_id>/slots` | Add device to slot |
| PUT    | `/api/racks/<topo_id>/<rack_id>/slots/<slot_id>` | Update slot (re-position, change power) |
| DELETE | `/api/racks/<topo_id>/<rack_id>/slots/<slot_id>` | Remove slot |
| GET    | `/api/racks/<topo_id>/<rack_id>/summary` | Aggregated totals (power, weight, heat map data) |

---

## Frontend Architecture

### Trigger: JointJS Cell Click

In `network-canvas.js`, the existing `cell:pointerclick` event already fires for all node clicks. Add a check:

```javascript
paper.on('cell:pointerclick', function(cellView) {
    const cell = cellView.model;
    if (cell.get('type') !== 'link' && isRackDevice(cell)) {
        openRackElevationPanel(cell.id, cell.get('attrs')['.label']['text']);
    }
});

function isRackDevice(cell) {
    const rackTypes = ['colocation.Cabinet', 'server', 'patch_panel', 'Server'];
    return rackTypes.some(t => (cell.get('deviceType') || '').includes(t));
}
```

### Side Panel HTML Structure

```html
<div id="rack-elevation-panel" class="side-panel collapsed">
  <div class="panel-header">
    <span id="rack-panel-title">Rack: Cabinet-01</span>
    <button onclick="closeRackPanel()">✕</button>
  </div>
  <div class="rack-summary-bar">
    <!-- Power: 3,240W / 10,000W | Weight: 120kg / 500kg -->
  </div>
  <div class="rack-3d-wrapper">
    <div class="rack-cabinet">
      <!-- U slots rendered here by renderRackElevation() -->
    </div>
  </div>
  <div class="rack-legend">
    <!-- Airflow color gradient legend -->
  </div>
  <div class="rack-device-table">
    <!-- Tabular view: U, Device, Power, Weight, Airflow -->
  </div>
</div>
```

### CSS Isometric 3D Effect

```css
.rack-3d-wrapper {
    perspective: 800px;
}
.rack-cabinet {
    transform: rotateX(15deg) rotateY(-10deg);
    transform-style: preserve-3d;
    border: 2px solid #333;
    background: #1a1a2e;
    width: 200px;
    margin: auto;
}
.rack-slot {
    height: 18px;       /* 1U = 18px */
    border-bottom: 1px solid #333;
    transition: background 0.2s;
    cursor: pointer;
}
.rack-slot:hover {
    filter: brightness(1.3);
}
.rack-slot.selected {
    outline: 2px solid #fff;
}
/* Airflow heat gradient: cool=blue → warm=orange → hot=red */
.heat-0   { background: #4a90d9; }
.heat-low { background: #27ae60; }
.heat-med { background: #f39c12; }
.heat-high{ background: #e74c3c; }
```

### Bidirectional Highlighting

**Rack → Topology:** When user clicks a slot in the rack panel:

```javascript
function onRackSlotClick(slot) {
    // Clear previous selection
    paper.findViewByModel(currentHighlightedCell)?.unhighlight();
    // Find JointJS cell by node_id
    const cell = graph.getCell(slot.node_id);
    if (cell) {
        const view = paper.findViewByModel(cell);
        view.highlight();
        // Pan topology to center on cell
        const bbox = view.getBBox();
        centerCanvasOn(bbox.x + bbox.width/2, bbox.y + bbox.height/2);
        currentHighlightedCell = cell;
    }
    // Mark slot as selected in panel
    document.querySelectorAll('.rack-slot').forEach(el => el.classList.remove('selected'));
    document.querySelector(`[data-slot-id="${slot.id}"]`).classList.add('selected');
}
```

**Topology → Rack:** When topology canvas node is selected and it belongs to a rack, auto-scroll rack panel to that slot and pulse-highlight it.

```javascript
// In the cell:pointerclick handler, after opening panel:
function highlightSlotForNode(nodeId) {
    const slotEl = document.querySelector(`[data-node-id="${nodeId}"]`);
    if (slotEl) {
        slotEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        slotEl.classList.add('pulse-highlight');
        setTimeout(() => slotEl.classList.remove('pulse-highlight'), 2000);
    }
}
```

---

## Airflow Visualization

Airflow is shown as a color gradient overlay on the rack elevation:

| Airflow Type | Visual |
|---|---|
| `front-to-back` | Blue → Red gradient from bottom to top (cold aisle → hot aisle) |
| `top-exhaust` | Radial gradient, dark at top slots |
| `side-exhaust` | Left-to-right gradient |

Each device slot colored by normalized heat density = `power_draw_w / (u_height * max_power_per_u)`.

---

## NetBox Integration Hook

The existing `/api/netbox/pull/racks` endpoint already fetches NetBox rack data. Extend the NetBox import flow to auto-populate `nc_racks` and `nc_rack_slots` when topology is imported:

1. Pull racks from NetBox → create `nc_racks` rows
2. Pull rack device placements from NetBox → create `nc_rack_slots` with `u_start`, `u_height`, power info
3. Map NetBox device → matching JointJS `node_id` via existing `nc_netbox_objects` table

---

## Device Types That Activate Rack View

Any `nc_objects.object_type` matching these should show the rack panel:

```python
RACK_DEVICE_TYPES = [
    'colocation.Cabinet',
    'colocation.Cage',
    'server',
    'server_rack',
    'patch_panel',
    'Patch Panel',
    'Server',
]
```

Right-click context menu on any device → "View in Rack Elevation" should also be available even for non-rack-type nodes (to allow placing any device into a rack).

---

## Implementation Plan (Phased)

### Phase 1 — Data Foundation (2 days)
- Add `nc_racks` and `nc_rack_slots` tables to `tools/network/db/init_db.py`
- Add tables to `APPEND_ONLY_TABLES` if audit trail required, or standard tables
- Add 9 CRUD API routes to `blueprint.py`
- Run `init_icdev_db.py` to apply schema

### Phase 2 — Side Panel UI (2 days)
- Add `#rack-elevation-panel` HTML to `canvas.html`
- Implement CSS isometric 3D rack rendering
- Implement `renderRackElevation(rackId)` JS function
- Wire `cell:pointerclick` in `network-canvas.js`
- Add airflow heat gradient

### Phase 3 — Bidirectional Linking (1 day)
- Topology → Rack: auto-open panel and highlight slot on node click
- Rack → Topology: click slot → highlight + pan JointJS canvas to node
- Keyboard: `Escape` closes panel, `Tab` cycles through rack devices

### Phase 4 — NetBox Sync + Polish (1 day)
- Extend NetBox import to populate rack/slot data
- Add rack summary bar (power %, weight %, heat status)
- Add right-click "Add to Rack" context menu for any device
- Selenium E2E test

---

## E2E Test Plan

```python
# tests/e2e_rack_elevation.py
# 1. Navigate to /network/canvas/<topo_id>
# 2. Drop a "Server" node on the canvas
# 3. Save topology
# 4. Click the Server node → verify side panel opens
# 5. Verify rack elevation renders (check .rack-cabinet exists and has .rack-slot children)
# 6. Click a slot → verify JointJS node highlights (check cell has 'highlighted' class)
# 7. Click node on topology → verify panel slot pulses
# 8. Screenshot: playwright/screenshots/rack-elevation-desktop-1920x1080.png
```

---

## Files to Create/Modify

| File | Change |
|------|--------|
| `tools/network/db/init_db.py` | Add `nc_racks` + `nc_rack_slots` CREATE TABLE statements |
| `tools/network/blueprint.py` | Add 9 rack/slot API routes + helper functions |
| `tools/dashboard/templates/network/canvas.html` | Add side panel HTML + CSS |
| `tools/dashboard/static/js/network-canvas.js` | Wire `cell:pointerclick` + render functions |
| `tools/network/netbox_client.py` | Extend rack pull to populate nc_racks |
| `tests/e2e_rack_elevation.py` | New Selenium E2E test |
| `tools/manifest.md` | No new tools needed |
| `docs/features/network-canvas-3d-rack-elevation.md` | This document |

---

## Risk & Constraints

| Risk | Mitigation |
|------|-----------|
| Air-gap: no CDN for Three.js | Use CSS 3D transforms — zero new dependencies |
| NetBox not always present | Rack data entry is manual-first; NetBox sync is additive |
| Large racks (48U+) overflow panel | Scrollable panel container with fixed height + sticky summary bar |
| Performance: many racks in one topology | Lazy-load slot data on panel open, not on topology load |
| JointJS `node_id` drift after copy/paste | Stable UUIDs assigned at node creation, stored in `nc_rack_slots.node_id` |
