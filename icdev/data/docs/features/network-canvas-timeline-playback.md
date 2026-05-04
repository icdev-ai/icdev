# Network Canvas: Timeline Playback (Phase Animation)
<!-- CUI // SP-CTI -->
**Phase:** Tier 4 Research | **Task:** task-b7f654110a | **Date:** 2026-03-27

---

## Summary

Timeline Playback enables animated rendering of network topology evolution across version history phases. Users can play, pause, scrub, and step through snapshots — showing devices appearing, links changing, and enclaves expanding. Primary use cases: migration briefings, ATO narratives, and phase-gate reviews.

---

## Current State Assessment

### What Exists

| Component | Status |
|-----------|--------|
| `nc_versions` DB table | **Ready** — stores full `graph_json` snapshots per version |
| Version list API (`GET /api/versions/<topo_id>`) | **Ready** |
| Version diff API (`POST /api/versions/<topo_id>/diff`) | **Ready** — returns added/removed node/edge counts |
| JointJS paper (`graph`, `paper` globals) | **Ready** — supports programmatic node/edge mutation |
| `requestAnimationFrame` animation loop | **Ready** — used by packet simulation and BGP wave animations |
| Heatmap visual state overlay | **Ready** — per-node color coding extensible to temporal state |
| Static version comparison UI (`versions.html`) | **Partial** — radio-select A/B diff only, no playback |

### What's Missing

- Timeline scrubber UI (range input + phase labels)
- Play / Pause / Step Forward / Step Back controls
- Playback speed selector
- Frame-by-frame diff engine (between consecutive version snapshots)
- Visual transition animations (node fade-in/out, edge draw/erase)
- Phase label overlay on canvas during playback

---

## Data Model

The `nc_versions` table (in `tools/network/db/init_db.py`) provides the temporal backbone:

```sql
nc_versions (
    id          INTEGER PRIMARY KEY,
    topology_id INTEGER REFERENCES topologies(id),
    version_num INTEGER,          -- auto-incremented, used as frame index
    label       TEXT,             -- e.g. "As-Is", "Year 1", "TO-BE"
    phase       TEXT,             -- e.g. "current", "future", "Q2", "Year-1"
    graph_json  TEXT,             -- full JSON snapshot: {nodes:[], edges:[]}
    created_by  TEXT,
    notes       TEXT,
    created_at  DATETIME
)
```

Each `graph_json` is a complete topology snapshot — no delta storage. Diffs are computed on-the-fly by comparing consecutive frame pairs.

---

## Implementation Design

### 1. API: Ordered Version Frames

Add endpoint to `tools/network/blueprint.py`:

```python
GET /api/versions/<topo_id>/frames
# Returns all versions ordered by version_num with graph_json payloads
# Used by the timeline player to pre-fetch all frames on load
```

Response shape:
```json
{
  "frames": [
    {"version_num": 1, "label": "As-Is", "phase": "current",
     "graph_json": {...}, "created_at": "2026-01-01T00:00:00"},
    {"version_num": 2, "label": "Year 1", "phase": "future", ...}
  ]
}
```

### 2. Frontend: Timeline Player Component

Inject into `tools/dashboard/templates/network/canvas.html` as a collapsible panel below the toolbar:

```html
<!-- Timeline Player Panel -->
<div id="timeline-player" class="timeline-panel collapsed">
  <!-- Phase label + timestamp -->
  <div class="timeline-phase-label">
    <span id="tl-phase-name">—</span>
    <span id="tl-phase-date" class="text-muted"></span>
  </div>

  <!-- Scrubber -->
  <input type="range" id="tl-scrubber" min="0" max="0" value="0" step="1">

  <!-- Frame counter -->
  <span id="tl-frame-counter">0 / 0</span>

  <!-- Controls -->
  <div class="timeline-controls">
    <button id="tl-prev">⏮</button>
    <button id="tl-play">▶</button>
    <button id="tl-pause" disabled>⏸</button>
    <button id="tl-next">⏭</button>
    <select id="tl-speed">
      <option value="2000">0.5×</option>
      <option value="1000" selected>1×</option>
      <option value="500">2×</option>
      <option value="250">4×</option>
    </select>
  </div>
</div>
```

### 3. JavaScript: Playback Engine

New module: `tools/dashboard/static/js/network-timeline.js`

```javascript
// network-timeline.js — Timeline Playback Engine for Network Canvas
// Depends on: JointJS (graph, paper globals from network-canvas.js)

const TimelinePlayer = (() => {
  let frames = [];       // [{version_num, label, phase, graph_json, created_at}]
  let currentIdx = 0;
  let playTimer = null;
  let speed = 1000;      // ms per frame

  // ── Load ──────────────────────────────────────────────────────────────
  async function load(topoId) {
    const res = await fetch(`/api/versions/${topoId}/frames`);
    const data = await res.json();
    frames = data.frames || [];
    document.getElementById('tl-scrubber').max = frames.length - 1;
    document.getElementById('tl-frame-counter').textContent = `0 / ${frames.length}`;
    if (frames.length > 0) renderFrame(0);
  }

  // ── Render ────────────────────────────────────────────────────────────
  function renderFrame(idx) {
    if (idx < 0 || idx >= frames.length) return;
    currentIdx = idx;

    const frame = frames[idx];
    const g = typeof frame.graph_json === 'string'
      ? JSON.parse(frame.graph_json)
      : frame.graph_json;

    // Diff against previous frame
    const prev = idx > 0
      ? (typeof frames[idx-1].graph_json === 'string'
          ? JSON.parse(frames[idx-1].graph_json)
          : frames[idx-1].graph_json)
      : { nodes: [], edges: [] };

    applyGraphDiff(prev, g);

    // Update UI
    document.getElementById('tl-phase-name').textContent = frame.label || frame.phase || `Frame ${idx+1}`;
    document.getElementById('tl-phase-date').textContent = frame.created_at ? new Date(frame.created_at).toLocaleDateString() : '';
    document.getElementById('tl-scrubber').value = idx;
    document.getElementById('tl-frame-counter').textContent = `${idx+1} / ${frames.length}`;
  }

  // ── Diff Engine ───────────────────────────────────────────────────────
  function applyGraphDiff(prev, next) {
    const prevNodeIds = new Set((prev.nodes || []).map(n => n.id));
    const nextNodeIds = new Set((next.nodes || []).map(n => n.id));
    const prevEdgeIds = new Set((prev.edges || []).map(e => e.id));
    const nextEdgeIds = new Set((next.edges || []).map(e => e.id));

    // Nodes to add (fade in)
    for (const node of (next.nodes || [])) {
      if (!prevNodeIds.has(node.id)) {
        addNodeAnimated(node);
      } else {
        updateNode(node); // position/label changes
      }
    }

    // Nodes to remove (fade out)
    for (const node of (prev.nodes || [])) {
      if (!nextNodeIds.has(node.id)) {
        removeNodeAnimated(node.id);
      }
    }

    // Edges to add
    for (const edge of (next.edges || [])) {
      if (!prevEdgeIds.has(edge.id)) {
        addEdgeAnimated(edge);
      }
    }

    // Edges to remove
    for (const edge of (prev.edges || [])) {
      if (!nextEdgeIds.has(edge.id)) {
        removeEdge(edge.id);
      }
    }
  }

  // ── JointJS Mutations ─────────────────────────────────────────────────
  function addNodeAnimated(nodeData) {
    // Create JointJS element, start transparent, fade to full opacity
    const el = graph.getCell(nodeData.id);
    if (el) return; // already exists
    const cell = buildJointCell(nodeData);
    cell.attr('body/opacity', 0);
    graph.addCell(cell);
    animateOpacity(cell, 0, 1, 400);
  }

  function removeNodeAnimated(nodeId) {
    const cell = graph.getCell(nodeId);
    if (!cell) return;
    animateOpacity(cell, 1, 0, 400, () => cell.remove());
  }

  function addEdgeAnimated(edgeData) {
    const link = buildJointLink(edgeData);
    link.attr('line/opacity', 0);
    graph.addCell(link);
    animateOpacity(link, 0, 1, 300);
  }

  function removeEdge(edgeId) {
    const cell = graph.getCell(edgeId);
    if (cell) cell.remove();
  }

  function updateNode(nodeData) {
    const cell = graph.getCell(nodeData.id);
    if (!cell) { addNodeAnimated(nodeData); return; }
    if (nodeData.x !== undefined && nodeData.y !== undefined) {
      cell.position(nodeData.x, nodeData.y, { silent: false });
    }
    if (nodeData.label) {
      cell.attr('label/text', nodeData.label);
    }
  }

  // ── Opacity Animation (no external deps) ─────────────────────────────
  function animateOpacity(cell, from, to, duration, onComplete) {
    const start = performance.now();
    function step(now) {
      const t = Math.min((now - start) / duration, 1);
      const opacity = from + (to - from) * t;
      const isLink = cell.isLink ? cell.isLink() : false;
      cell.attr(isLink ? 'line/opacity' : 'body/opacity', opacity);
      if (t < 1) {
        requestAnimationFrame(step);
      } else if (onComplete) {
        onComplete();
      }
    }
    requestAnimationFrame(step);
  }

  // ── Playback Controls ─────────────────────────────────────────────────
  function play() {
    if (playTimer) return;
    document.getElementById('tl-play').disabled = true;
    document.getElementById('tl-pause').disabled = false;
    function tick() {
      if (currentIdx >= frames.length - 1) {
        pause();
        return;
      }
      renderFrame(currentIdx + 1);
      playTimer = setTimeout(tick, speed);
    }
    playTimer = setTimeout(tick, speed);
  }

  function pause() {
    clearTimeout(playTimer);
    playTimer = null;
    document.getElementById('tl-play').disabled = false;
    document.getElementById('tl-pause').disabled = true;
  }

  // ── Public API ────────────────────────────────────────────────────────
  return { load, renderFrame, play, pause, frames: () => frames };
})();

// Wire UI after DOM ready
document.addEventListener('DOMContentLoaded', () => {
  const topoId = window.TOPOLOGY_ID;
  if (!topoId) return;

  TimelinePlayer.load(topoId);

  document.getElementById('tl-play').addEventListener('click', () => TimelinePlayer.play());
  document.getElementById('tl-pause').addEventListener('click', () => TimelinePlayer.pause());
  document.getElementById('tl-prev').addEventListener('click', () => {
    TimelinePlayer.pause();
    TimelinePlayer.renderFrame(+document.getElementById('tl-scrubber').value - 1);
  });
  document.getElementById('tl-next').addEventListener('click', () => {
    TimelinePlayer.pause();
    TimelinePlayer.renderFrame(+document.getElementById('tl-scrubber').value + 1);
  });
  document.getElementById('tl-scrubber').addEventListener('input', e => {
    TimelinePlayer.pause();
    TimelinePlayer.renderFrame(+e.target.value);
  });
  document.getElementById('tl-speed').addEventListener('change', e => {
    speed = +e.target.value;
  });
});
```

### 4. CSS: Timeline Panel

Add to existing network canvas stylesheet or inline in template:

```css
.timeline-panel {
  position: absolute;
  bottom: 0; left: 0; right: 0;
  background: rgba(15,15,30,0.92);
  border-top: 1px solid #2a2a4a;
  padding: 8px 16px;
  display: flex; align-items: center; gap: 12px;
  z-index: 100;
  transition: transform 0.3s;
}
.timeline-panel.collapsed { transform: translateY(100%); }
.timeline-phase-label { min-width: 200px; }
#tl-scrubber { flex: 1; accent-color: #4f8ef7; }
.timeline-controls button {
  background: #1e2040; border: 1px solid #4f8ef7;
  color: #4f8ef7; border-radius: 4px;
  padding: 4px 10px; cursor: pointer;
}
.timeline-controls button:hover { background: #4f8ef7; color: #fff; }
.timeline-controls button:disabled { opacity: 0.4; cursor: default; }
```

---

## Integration Points

### Blueprint Changes (blueprint.py)

```python
@network_bp.route('/api/versions/<int:topo_id>/frames')
@login_required
def get_version_frames(topo_id):
    """Return all version snapshots ordered for timeline playback."""
    db = get_db()
    rows = db.execute(
        """SELECT version_num, label, phase, graph_json, created_at
           FROM nc_versions WHERE topology_id=? ORDER BY version_num ASC""",
        (topo_id,)
    ).fetchall()
    frames = [dict(r) for r in rows]
    return jsonify({"frames": frames, "count": len(frames)})
```

### Canvas Template Addition

In `canvas.html`, after the existing toolbar `<div>`:
1. Add `<script src="/static/js/network-timeline.js"></script>`
2. Add the timeline player HTML panel
3. Add a toggle button in the main toolbar: `<button onclick="document.getElementById('timeline-player').classList.toggle('collapsed')">⏱ Timeline</button>`

---

## ATO Narrative Integration

For mission use (migration briefings, ATO narratives):

- **Export:** Add `Export as GIF/MP4` button — use canvas `paper.toSVG()` per frame, stitch server-side with Pillow or ffmpeg
- **Phase Labels:** Map `phase` field values to ATO milestones: `current` → "As-Is (FY25)", `future` → "To-Be (FY26)", `q2` → "Phase Gate Q2"
- **Annotation overlay:** Show diff counts (e.g., "+3 nodes, -1 link") as floating badge during transitions

---

## Dependencies

All existing — no new packages required:
- **JointJS** — already vendored at `tools/dashboard/static/vendor/`
- **requestAnimationFrame** — browser native
- **SQLite** — already used for `nc_versions`
- **Flask** — existing blueprint

Optional for video export:
- **Pillow** (`pip install Pillow`) — for GIF assembly
- **ffmpeg** — for MP4 assembly (system package)

---

## Implementation Checklist

- [ ] Add `GET /api/versions/<topo_id>/frames` endpoint to `blueprint.py`
- [ ] Create `tools/dashboard/static/js/network-timeline.js`
- [ ] Add timeline panel HTML + CSS to `canvas.html`
- [ ] Add toolbar toggle button
- [ ] Wire `TOPOLOGY_ID` JS global (already set by canvas template)
- [ ] Test with ≥3 version snapshots (add, remove, update nodes)
- [ ] Validate playback at 0.5×, 1×, 2×, 4× speeds
- [ ] Test scrubber seek during playback
- [ ] E2E: Selenium test in `tests/e2e_network_canvas.py`
- [ ] (Optional) Server-side GIF export endpoint

---

## Risk / Complexity

| Risk | Mitigation |
|------|-----------|
| Large graph_json payloads slow initial load | Pre-fetch all frames async; show loading indicator |
| JointJS cell ID collisions between versions | Always check `graph.getCell(id)` before adding |
| `buildJointCell`/`buildJointLink` not exposed | Refactor canvas.js to export factory functions |
| Opacity animation conflicts with paper selection highlights | Guard with `if (!cell.isSelected())` |

**Estimated implementation effort:** 2–3 days (1 dev)
**Tier:** 4 (low priority, no blockers, purely additive)
