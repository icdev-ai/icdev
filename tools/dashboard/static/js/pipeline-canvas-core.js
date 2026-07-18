/* CUI // SP-CTI — Pipeline Design Canvas: Core
 * JointJS init, pan/zoom, selection, undo/redo, save/load, node/link creation,
 * drag-drop + click-to-add, boundary boxes, legend, and the single consolidated
 * keyboard listener. Split from the pipeline-canvas.js monolith (pdx-ux-01).
 *
 * Load order: after pipeline-node-styles.js, before the ndc-bridge / snippets /
 * iac / analysis modules. All declarations stay top-level (classic script) so
 * inline canvas.html handlers and cross-module calls resolve through the shared
 * global lexical environment. No ES modules / bundler / framework.
 */

'use strict';

// ── XSS-safe escaping (pdx-sec-02) ───────────────────────────────────────────
// Provided by pdc-escape.js when loaded; local fallbacks keep the canvas
// self-contained (canvas.html does not load pdc-escape.js). Shared by every
// pipeline-* module that renders HTML into the right panel.
const escapeHtml = (typeof window !== 'undefined' && window.escapeHtml) || function (value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
};
const escapeAttr = (typeof window !== 'undefined' && window.escapeAttr) || function (value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
};

// ── Shared JSON fetch + toast (pdx-fix-01) ───────────────────────────────────
// Provided by pdc-http.js when loaded; local fallbacks keep the canvas working
// self-contained (canvas.html does not load pdc-http.js). fetchJson rejects on
// !r.ok or a body-level `error`; callers .catch and surface via pdcToast.
const fetchJson = (typeof window !== 'undefined' && window.fetchJson) || async function (url, opts) {
  const resp = await fetch(url, opts || {});
  let data = null;
  try { const t = await resp.text(); data = t ? JSON.parse(t) : null; } catch (_) { data = null; }
  if (!resp.ok) { throw new Error((data && data.error) || ('Request failed (HTTP ' + resp.status + ')')); }
  if (data && data.error) { throw new Error(data.error); }
  return data;
};
const pdcToast = (typeof window !== 'undefined' && window.pdcToast) || function (message, type) {
  const accent = type === 'success' ? '#27ae60' : type === 'info' ? '#3498db' : '#e74c3c';
  const toast = document.createElement('div');
  toast.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:10000;background:#0f1e35;border:1px solid #1e3a6e;border-left:4px solid ' + accent + ';border-radius:8px;padding:12px 40px 12px 16px;max-width:360px;box-shadow:0 4px 16px rgba(0,0,0,0.5);font-family:sans-serif;font-size:13px;color:#eaeaea;';
  const msg = document.createElement('div');
  msg.textContent = String(message == null ? '' : message);
  toast.appendChild(msg);
  const close = document.createElement('button');
  close.textContent = '✕';
  close.setAttribute('aria-label', 'Dismiss notification');
  close.style.cssText = 'position:absolute;top:8px;right:8px;background:none;border:none;color:#7a8cb0;cursor:pointer;font-size:14px;';
  close.addEventListener('click', () => toast.remove());
  toast.appendChild(close);
  document.body.appendChild(toast);
  setTimeout(() => { if (toast.parentElement) toast.remove(); }, 6000);
  return toast;
};

// ── State ────────────────────────────────────────────────────────────────────

let graph, paper, selectedCell = null;
let undoStack = [], redoStack = [];
let isDirty = false, saveTimer = null;
let suppressDirty = false; // true while initial graph is loading; see initCanvas
let pipelineId = 'new';

// Undo/redo history cap — keep memory bounded on long editing sessions.
const _MAX_HISTORY = 50;

// ── JointJS Initialization ──────────────────────────────────────────────────

function initCanvas() {
  graph = new joint.dia.Graph();
  paper = new joint.dia.Paper({
    el: document.getElementById('pc-canvas-container'),
    model: graph,
    width: 5000, height: 5000,
    gridSize: 10,
    drawGrid: { name: 'dot', args: { color: 'rgba(0,0,0,0.12)' } },
    background: { color: 'transparent' },
    defaultLink: () => new joint.shapes.standard.Link({
      attrs: { line: { stroke: '#e94560', strokeWidth: 2, targetMarker: { type: 'classic', fill: '#e94560', size: 6 } } }
    }),
    validateConnection: () => true,
    snapLinks: { radius: 20 },
    linkPinning: false,
    interactive: { labelMove: false },
  });

  // Events
  paper.on('element:pointerclick', (view) => selectCell(view.model));
  paper.on('blank:pointerclick', () => deselectAll());
  paper.on('element:pointerdblclick', (view) => {
    const newLabel = prompt('Rename:', view.model.attr('label/text') || '');
    if (newLabel !== null) { pushUndo(); view.model.attr('label/text', newLabel); markDirty(); }
  });

  // Enhanced tooltips (shared utility from canvas-tooltips.js)
  // Adds config-aware rich tooltips. Pipeline-specific TOOL_INFO added as extra fields.
  if (typeof initEnhancedTooltips === 'function') {
    initEnhancedTooltips(paper, graph, (type) => {
      const s = getStyle(type);
      return { fill: s.fill || '#0f2b3a', stroke: s.stroke || '#3498db', label: s.label || type, symbol: s.symbol || '?' };
    });
  }

  let isPanning = false, panStart = {}, translateStart = {};
  const canvasArea = document.querySelector('.pc-canvas-area');

  // Drag-to-pan using paper.translate() — works at any scroll position
  paper.on('blank:pointerdown', (evt) => {
    isPanning = true;
    panStart = { x: evt.clientX, y: evt.clientY };
    translateStart = paper.translate();
    canvasArea.classList.add('is-panning');
  });
  document.addEventListener('mousemove', (evt) => {
    if (!isPanning) return;
    paper.translate(
      translateStart.tx + (evt.clientX - panStart.x),
      translateStart.ty + (evt.clientY - panStart.y)
    );
  });
  document.addEventListener('mouseup', () => {
    if (!isPanning) return;
    isPanning = false;
    canvasArea.classList.remove('is-panning');
  });

  // Mouse-wheel zoom centered on cursor — uses translate to keep point under cursor fixed
  canvasArea.addEventListener('wheel', (evt) => {
    evt.preventDefault();
    const s0 = paper.scale().sx;
    const factor = evt.deltaY < 0 ? 1.12 : 1 / 1.12;
    const s1 = Math.max(0.08, Math.min(4, s0 * factor));
    if (Math.abs(s1 - s0) < 0.001) return;
    const t0 = paper.translate();
    const areaRect = canvasArea.getBoundingClientRect();
    const mx = evt.clientX - areaRect.left;
    const my = evt.clientY - areaRect.top;
    // Keep the paper-space point under the cursor fixed: mx = px*s + tx → tx = mx - px*s
    const newTx = mx - (mx - t0.tx) * s1 / s0;
    const newTy = my - (my - t0.ty) * s1 / s0;
    paper.scale(s1, s1);
    paper.translate(newTx, newTy);
    _updateZoomLabel(s1);
  }, { passive: false });

  // Keyboard shortcuts are wired once, globally, at the bottom of this file
  // (single consolidated listener — pdx-ux-01).

  // Suppress auto-save during initial graph load. JointJS fires
  // `change add remove` for every cell as it materializes; we don't want
  // those to count as "user edits" and trigger savePipeline() 3s later.
  // (Fixes bug where opening a canvas wrote an UPDATE to the server that
  // blanked description/classification/target_csp — see PUT handler and
  // loadGraph() which sets/clears this flag.)
  graph.on('change add remove', () => { if (!suppressDirty) markDirty(); });
}

// ── Node Creation ───────────────────────────────────────────────────────────

function getStyle(type) {
  return NODE_STYLES[type] || { fill: '#1a1a2e', stroke: '#7a8cb0', label: type, symbol: '?' };
}

function createNode(type, x, y, label, nodeId) {
  const style = getStyle(type);
  const displayLabel = label || style.label;
  const w = 110, h = 60;

  const node = new joint.shapes.standard.Rectangle({
    id: nodeId || joint.util.uuid(),
    position: { x: x || 100, y: y || 100 },
    size: { width: w, height: h },
    attrs: {
      body: { fill: style.fill, stroke: style.stroke, strokeWidth: 2, rx: 6, ry: 6 },
      label: { text: displayLabel, fill: '#eaeaea', fontSize: 10, fontFamily: "'Segoe UI', sans-serif" },
    },
  });

  node.set('nodeType', type);
  node.set('nodeDesc', style.label + ' (' + type + ')');
  graph.addCell(node);
  return node;
}

// Get bounding box of all existing elements to compute snippet placement offset
function _getCanvasBounds() {
  const elements = graph.getElements();
  if (!elements.length) return { maxX: 0, maxY: 0, minX: 0, minY: 0 };
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  elements.forEach(el => {
    const pos = el.position();
    const size = el.size();
    minX = Math.min(minX, pos.x);
    minY = Math.min(minY, pos.y);
    maxX = Math.max(maxX, pos.x + size.width);
    maxY = Math.max(maxY, pos.y + size.height);
  });
  return { minX, minY, maxX, maxY };
}

function createLink(srcId, tgtId, label, linkId) {
  // Reject orphan endpoints — JointJS otherwise renders an invisible link
  // at origin, which looks like "links sometimes don't appear" in the UI.
  if (!srcId || !tgtId || !graph.getCell(srcId) || !graph.getCell(tgtId)) {
    console.warn('createLink: skipping orphan edge', { srcId: srcId, tgtId: tgtId, linkId: linkId });
    return null;
  }

  const link = new joint.shapes.standard.Link({
    id: linkId || joint.util.uuid(),
    source: { id: srcId },
    target: { id: tgtId },
    attrs: { line: { stroke: '#e94560', strokeWidth: 2, targetMarker: { type: 'classic', fill: '#e94560', size: 6 } } },
    labels: label ? [{ attrs: { text: { text: label, fill: '#374151', fontSize: 9 } }, position: 0.5 }] : [],
  });
  graph.addCell(link);
  return link;
}

// ── Drag & Drop / Click-to-Add ──────────────────────────────────────────────

function onDragStart(event) {
  const el = event.target.closest('[data-type]');
  if (el) {
    event.dataTransfer.setData('text/plain', el.dataset.type);
    event.dataTransfer.effectAllowed = 'copy';
  }
}

// Ring-based free-slot search around (x,y) for a box of size w×h. Shared by
// drop placement, click-to-add, and snippet spiral placement (pdx-ux-01: the
// snippet engine's bespoke overlap loop was removed in favour of this helper).
function _findFreePosition(x, y, w, h) {
  const PAD = 15;
  const occupied = graph.getElements().map(el => {
    const p = el.position(), s = el.size();
    return { x: p.x - PAD, y: p.y - PAD, x2: p.x + s.width + PAD, y2: p.y + s.height + PAD };
  });
  const overlaps = (cx, cy) => occupied.some(b => cx < b.x2 && cx + w > b.x && cy < b.y2 && cy + h > b.y);
  if (!overlaps(x, y)) return { x, y };
  const STEP = Math.max(w, h) + PAD;
  for (let ring = 1; ring <= 12; ring++) {
    const candidates = [];
    for (let i = -ring; i <= ring; i++) {
      candidates.push({ x: x + i * STEP, y: y - ring * STEP });
      candidates.push({ x: x + i * STEP, y: y + ring * STEP });
    }
    for (let j = -ring + 1; j < ring; j++) {
      candidates.push({ x: x - ring * STEP, y: y + j * STEP });
      candidates.push({ x: x + ring * STEP, y: y + j * STEP });
    }
    for (const c of candidates) {
      if (c.x >= 0 && c.y >= 0 && !overlaps(c.x, c.y)) return c;
    }
  }
  return { x: x + Math.random() * 60, y: y + Math.random() * 60 };
}

// Convert a client (screen) coordinate to a snapped, collision-free paper
// position and create a node there. Shared by onDrop and addNodeFromPalette.
function _placeNodeAtClient(clientX, clientY, type) {
  const rect = document.getElementById('pc-canvas-container').getBoundingClientRect();
  const t = paper.translate(), s = paper.scale();
  const rawX = Math.round(((clientX - rect.left - t.tx) / s.sx) / 10) * 10;
  const rawY = Math.round(((clientY - rect.top  - t.ty) / s.sy) / 10) * 10;
  const pos = _findFreePosition(rawX, rawY, 110, 60);
  pushUndo();
  const node = createNode(type, pos.x, pos.y);
  selectCell(node);
  markDirty();
  updateStatus('Added: ' + getStyle(type).label);
  return node;
}

function onDrop(event) {
  event.preventDefault();
  const type = event.dataTransfer.getData('text/plain');
  if (!type) return;
  _placeNodeAtClient(event.clientX, event.clientY, type);
}

// A11y: click-to-add path for palette nodes alongside drag. Places the node at
// the centre of the visible canvas viewport, snapped to a free slot — so the
// palette is usable by keyboard/click, not drag-and-drop alone.
function addNodeFromPalette(type) {
  if (!type || !paper) return;
  const area = document.querySelector('.pc-canvas-area');
  const areaRect = area ? area.getBoundingClientRect() : { left: 0, top: 0, width: 800, height: 600 };
  const clientX = areaRect.left + areaRect.width / 2;
  const clientY = areaRect.top + areaRect.height / 2;
  _placeNodeAtClient(clientX, clientY, type);
}

// ── Selection ───────────────────────────────────────────────────────────────

function selectCell(cell) {
  deselectAll();
  selectedCell = cell;
  if (cell && cell.isElement()) {
    cell.attr('body/strokeWidth', 3);
    openConfigPanel(cell);
  }
}

function deselectAll() {
  if (selectedCell && selectedCell.isElement()) {
    selectedCell.attr('body/strokeWidth', 2);
  }
  selectedCell = null;
  closeConfigPanel();
}

function deleteSelected() {
  if (selectedCell) { pushUndo(); selectedCell.remove(); selectedCell = null; markDirty(); }
}

// ── Undo / Redo ─────────────────────────────────────────────────────────────

function graphToJSON() { return graph.toJSON(); }
function loadGraphJSON(data) { graph.fromJSON(data); }

function pushUndo() {
  undoStack.push(graphToJSON());
  if (undoStack.length > _MAX_HISTORY) undoStack.shift();
  redoStack = [];
}
function undoAction() {
  if (!undoStack.length) return;
  redoStack.push(graphToJSON());
  if (redoStack.length > _MAX_HISTORY) redoStack.shift();
  loadGraphJSON(undoStack.pop());
  deselectAll();
}
function redoAction() {
  if (!redoStack.length) return;
  undoStack.push(graphToJSON());
  if (undoStack.length > _MAX_HISTORY) undoStack.shift();
  loadGraphJSON(redoStack.pop());
  deselectAll();
}

// ── Save / Load ─────────────────────────────────────────────────────────────

function markDirty() {
  isDirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(savePipeline, 3000);
  updateStatus('Unsaved changes...');
}

function savePipeline() {
  const graphData = graphToJSON();
  const nodes = graphData.cells.filter(c => c.type !== 'standard.Link').map(c => ({
    id: c.id, label: c.attrs?.label?.text || '', type: c.nodeType || '',
    x: c.position?.x || 0, y: c.position?.y || 0, config: c.configData || {},
  }));
  const edges = graphData.cells.filter(c => c.type === 'standard.Link').map(c => ({
    id: c.id, source: c.source?.id || '', target: c.target?.id || '',
    label: c.labels?.[0]?.attrs?.text?.text || '',
  }));
  const payload = { name: document.getElementById('pc-pipeline-name')?.value || 'Pipeline', graph_json: JSON.stringify({ nodes, edges }) };

  const method = pipelineId === 'new' ? 'POST' : 'PUT';
  const url = pipelineId === 'new' ? '/devops/api/pipelines' : `/devops/api/pipelines/${pipelineId}`;

  fetchJson(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    .then(data => {
      if (data && data.id && pipelineId === 'new') { pipelineId = data.id; history.replaceState(null, '', `/devops/canvas/${data.id}`); }
      isDirty = false;
      updateStatus('Saved');
      // Show SDC cross-canvas compliance result if Security Canvas re-assessed
      if (data && data.sdc_assessment) {
        _showSdcToast(data.sdc_assessment, 'PDC');
      }
    })
    .catch(err => {
      // Keep isDirty true so autosave retries and the user is not misled: a
      // 413 / RLS-denial / server error must NOT clear the dirty flag or claim "Saved".
      updateStatus('Save failed');
      pdcToast('Save failed: ' + (err && err.message ? err.message : 'unknown error'), 'error');
    });
}

// Explicit grade→color map so an unknown/'?' grade renders neutral grey, never
// green. (Prior lexicographic `grade <= 'B'` scored '?' — char code 63, below
// 'B' — as pass/green; pdx-ux-01 fix.)
const _SDC_GRADE_COLORS = { A: '#27ae60', B: '#27ae60', C: '#f39c12', D: '#e74c3c', F: '#e74c3c' };

function _showSdcToast(sdc, source) {
  const cat1 = sdc.cat1_count || 0;
  const grade = sdc.posture_grade || '?';
  const score = sdc.risk_score != null ? sdc.risk_score.toFixed(1) : '—';
  const gradeColor = _SDC_GRADE_COLORS[grade] || '#7a8cb0';
  const cat1Color = cat1 > 0 ? '#e74c3c' : '#27ae60';
  const cat1Text = cat1 > 0
    ? `<span style="color:#e74c3c;font-weight:bold;">&#9888; ${cat1} CAT1</span>`
    : `<span style="color:#27ae60;">&#10003; 0 CAT1</span>`;

  // Remove any existing SDC toast
  const existing = document.getElementById('sdc-cross-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'sdc-cross-toast';
  toast.style.cssText = [
    'position:fixed', 'bottom:24px', 'right:24px', 'z-index:9999',
    'background:#0f1e35', 'border:1px solid ' + (cat1 > 0 ? '#c0392b' : '#1e3a6e'),
    'border-radius:8px', 'padding:12px 16px', 'min-width:260px',
    'box-shadow:0 4px 16px rgba(0,0,0,0.5)', 'font-family:sans-serif',
    'animation:fadeIn 0.2s ease',
  ].join(';');
  toast.innerHTML = `
    <div style="font-size:11px;color:#7a8cb0;margin-bottom:4px;">SDC Re-assessed (triggered by ${source} save)</div>
    <div style="display:flex;align-items:center;gap:12px;">
      <div style="font-size:2rem;font-weight:bold;color:${gradeColor};">${grade}</div>
      <div>
        <div style="font-size:13px;color:#eaeaea;">Score: ${score}</div>
        <div style="font-size:13px;">${cat1Text}</div>
      </div>
    </div>
    ${cat1 > 0 ? '<div style="margin-top:8px;font-size:11px;color:#e74c3c;">View findings on <a href="/security/posture" style="color:#e74c3c;text-decoration:underline;">Security Posture</a></div>' : ''}
    <button onclick="this.parentElement.remove()" aria-label="Dismiss" style="position:absolute;top:8px;right:8px;background:none;border:none;color:#7a8cb0;cursor:pointer;font-size:14px;">&#10005;</button>
  `;
  toast.style.position = 'fixed';
  document.body.appendChild(toast);
  setTimeout(() => { if (toast.parentElement) toast.remove(); }, 8000);
}

function _resolveNodeOverlaps(g, padding) {
  padding = padding == null ? 20 : padding;
  const els = g.getElements();
  if (els.length < 2) return;
  for (let iter = 0; iter < 80; iter++) {
    let moved = false;
    for (let i = 0; i < els.length; i++) {
      for (let j = i + 1; j < els.length; j++) {
        const a = els[i], b = els[j];
        const ap = a.position(), as_ = a.size();
        const bp = b.position(), bs = b.size();
        const p2 = padding / 2;
        const ax1 = ap.x - p2, ay1 = ap.y - p2, ax2 = ap.x + as_.width + p2, ay2 = ap.y + as_.height + p2;
        const bx1 = bp.x - p2, by1 = bp.y - p2, bx2 = bp.x + bs.width + p2, by2 = bp.y + bs.height + p2;
        if (ax2 <= bx1 || bx2 <= ax1 || ay2 <= by1 || by2 <= ay1) continue;
        let dx = (bx1 + bx2) / 2 - (ax1 + ax2) / 2;
        let dy = (by1 + by2) / 2 - (ay1 + ay2) / 2;
        if (dx === 0 && dy === 0) { dx = 1; dy = 0; }
        const ovX = (ax2 - ax1) / 2 + (bx2 - bx1) / 2 - Math.abs(dx);
        const ovY = (ay2 - ay1) / 2 + (by2 - by1) / 2 - Math.abs(dy);
        if (ovX <= 0 || ovY <= 0) continue;
        let px = 0, py = 0;
        if (ovX <= ovY) { px = (ovX / 2 + 0.5) * (dx >= 0 ? 1 : -1); }
        else            { py = (ovY / 2 + 0.5) * (dy >= 0 ? 1 : -1); }
        a.position(ap.x - px, ap.y - py);
        b.position(bp.x + px, bp.y + py);
        moved = true;
      }
    }
    if (!moved) break;
  }
}

function loadGraph(graphJson) {
  const data = typeof graphJson === 'string' ? JSON.parse(graphJson) : graphJson;
  // Suppress dirty-flag during programmatic cell creation. Without this,
  // JointJS fires `change add remove` for every loaded cell, which the
  // auto-save handler would interpret as user edits and trigger savePipeline
  // 3s later — blanking description/classification/target_csp on the server
  // (the PUT handler reset them to defaults). See PUT handler for the
  // server-side belt-and-suspenders fix.
  suppressDirty = true;
  try {
    graph.clear();
    // Remap IDs to UUIDs to avoid collisions if the same template is loaded again
    const prefix = 'ld' + Date.now().toString(36) + '-';
    const idMap = {};
    (data.nodes || []).forEach(n => {
      const newId = prefix + (n.id || joint.util.uuid());
      idMap[n.id] = newId;
      createNode(n.type, n.x, n.y, n.label, newId);
    });

    let dropped = 0;
    (data.edges || []).forEach(e => {
      if (!e.source || !e.target || !idMap[e.source] || !idMap[e.target]) {
        dropped++;
        console.warn('loadGraph: dropping edge with missing endpoint', e);
        return;
      }
      createLink(idMap[e.source], idMap[e.target], e.label);
    });
    if (dropped > 0) {
      console.warn('loadGraph: dropped ' + dropped + ' edge(s) referencing missing nodes');
    }
    _resolveNodeOverlaps(graph, 20);
  } finally {
    suppressDirty = false;
  }
}

// ── Zoom ────────────────────────────────────────────────────────────────────

function _updateZoomLabel(s) {
  const el = document.getElementById('pc-zoom-label');
  if (el) el.textContent = Math.round((s !== undefined ? s : paper.scale().sx) * 100) + '%';
}

function _zoomAroundCenter(newScale) {
  const canvasArea = document.querySelector('.pc-canvas-area');
  const s0 = paper.scale().sx;
  const cx = canvasArea.clientWidth  / 2;
  const cy = canvasArea.clientHeight / 2;
  const newScrollX = (canvasArea.scrollLeft + cx) * newScale / s0 - cx;
  const newScrollY = (canvasArea.scrollTop  + cy) * newScale / s0 - cy;
  paper.scale(newScale, newScale);
  canvasArea.scrollLeft = newScrollX;
  canvasArea.scrollTop  = newScrollY;
  _updateZoomLabel(newScale);
}

function zoomIn()   { _zoomAroundCenter(Math.min(4,   paper.scale().sx * 1.2)); }
function zoomOut()  { _zoomAroundCenter(Math.max(0.08, paper.scale().sx / 1.2)); }
function zoomFit() {
  const area = document.querySelector('.pc-canvas-area');
  const w = area ? area.clientWidth  : 1200;
  const h = area ? area.clientHeight : 800;
  paper.scaleContentToFit({
    fittingBBox: { x: 0, y: 0, width: w, height: h },
    padding: 60,
    maxScale: 1,   // never zoom in past 100% — only zoom out to fit
  });
  _updateZoomLabel();
}
function zoomReset(){ paper.scale(1, 1); paper.translate(0, 0); _updateZoomLabel(1); }

// ── Right Panel (Properties / Analysis) ─────────────────────────────────────

function openRightPanel(title, html) {
  const panel = document.querySelector('.pc-config-panel');
  if (!panel) return;
  panel.classList.add('open');
  const header = panel.querySelector('.pc-config-header span');
  if (header) header.textContent = title;
  panel.querySelector('.pc-config-body').innerHTML = html;
}

// ── Utility ─────────────────────────────────────────────────────────────────

function updateStatus(msg) {
  const el = document.getElementById('pc-status');
  if (el) el.textContent = msg;
}

// Warn before navigating away / closing the tab with unsaved edits. Since save
// failures now keep isDirty true, this prevents silent loss of unsaved work.
window.addEventListener('beforeunload', (e) => {
  if (isDirty) {
    e.preventDefault();
    e.returnValue = '';
    return '';
  }
});

function newCanvas() {
  if (isDirty && !confirm('Unsaved changes. Continue?')) return;
  window.location.href = '/devops/canvas/new';
}

// ── Palette Search ──────────────────────────────────────────────────────────

function filterPalette(query) {
  const q = query.toLowerCase();
  document.querySelectorAll('.palette-item').forEach(item => {
    const text = (item.textContent || '').toLowerCase();
    const type = (item.dataset.type || '').toLowerCase();
    item.style.display = (text.includes(q) || type.includes(q)) ? '' : 'none';
  });
}

// ── Boundary Boxes (Stage Lanes) ────────────────────────────────────────────
// STAGE_COLORS / STAGE_LABELS live in pipeline-node-styles.js (loaded first).

function createBoundaryBox(label, x, y, w, h, color) {
  pushUndo();
  const box = new joint.shapes.standard.Rectangle({
    position: { x, y },
    size: { width: w, height: h },
    attrs: {
      body: {
        fill: 'rgba(255,255,255,0.02)',
        stroke: color || '#e94560',
        strokeWidth: 2,
        strokeDasharray: '8,4',
        rx: 10, ry: 10,
      },
      label: {
        text: label || 'Boundary',
        fill: color || '#e94560',
        fontSize: 12,
        fontWeight: 'bold',
        refX: 12,
        refY: 14,
        textAnchor: 'start',
        textVerticalAnchor: 'top',
      },
    },
    z: -1, // Behind other elements
  });
  box.set('nodeType', 'boundary-box');
  box.set('isBoundary', true);
  graph.addCell(box);
  markDirty();
  return box;
}

function autoGroupByStage() {
  pushUndo();
  // Remove existing boundary boxes
  graph.getElements().filter(el => el.get('isBoundary')).forEach(el => el.remove());

  // Group nodes by inferred stage
  const stageGroups = {};
  graph.getElements().forEach(el => {
    const type = el.get('nodeType') || '';
    if (type === 'boundary-box') return;
    const pos = el.position();
    const size = el.size();
    // Infer stage from type
    let stage = 'build';
    const prefixMap = {
      'scm-': 'source', 'branch-': 'source', 'commit-': 'source',
      'build-': 'build', 'cicd-': 'build',
      'scan-': 'test', 'sbom-': 'build',
      'registry-': 'package', 'sign-': 'package', 'attest-': 'package',
      'policy-': 'policy_gate', 'gate-': 'policy_gate',
      'deploy-': 'deploy_prod', 'k8s-': 'deploy_prod',
      'aws-eks': 'deploy_prod', 'az-aks': 'deploy_prod', 'gcp-gke': 'deploy_prod',
      'openshift': 'deploy_prod', 'rke2': 'deploy_prod', 'mesh-': 'deploy_prod',
      'mon-': 'monitor', 'aws-cloudwatch': 'monitor', 'az-monitor': 'monitor',
      'comp-': 'compliance',
      'sre-': 'sre', 'aws-fis': 'sre', 'az-chaos': 'sre', 'aws-cw-slo': 'sre',
      'aws-incident': 'sre', 'aws-resilience': 'sre', 'gcp-service-mon': 'sre',
      'az-advisor': 'sre', 'ibm-instana': 'sre',
      'cds-': 'cross_domain', 'boundary-': 'cross_domain', 'pipeline-': 'cross_domain',
      'sneakernet': 'cross_domain', 'vuln-db-': 'cross_domain', 'package-mirror': 'cross_domain',
      'ndc-': 'infrastructure', 'hybrid-': 'infrastructure', 'onprem-': 'infrastructure',
    };
    for (const [prefix, s] of Object.entries(prefixMap)) {
      if (type.startsWith(prefix) || type === prefix) { stage = s; break; }
    }
    if (!stageGroups[stage]) stageGroups[stage] = [];
    stageGroups[stage].push({ x: pos.x, y: pos.y, w: size.width, h: size.height });
  });

  // Create boundary boxes around each stage group
  for (const [stage, rects] of Object.entries(stageGroups)) {
    if (!rects.length) continue;
    const pad = 30;
    const minX = Math.min(...rects.map(r => r.x)) - pad;
    const minY = Math.min(...rects.map(r => r.y)) - pad - 18; // Extra space for label
    const maxX = Math.max(...rects.map(r => r.x + r.w)) + pad;
    const maxY = Math.max(...rects.map(r => r.y + r.h)) + pad;
    const color = STAGE_COLORS[stage] || '#7a8cb0';
    const label = STAGE_LABELS[stage] || stage;
    createBoundaryBox(label, minX, minY, maxX - minX, maxY - minY, color);
  }
  markDirty();
  updateStatus('Stage boundaries added');
}

function clearBoundaries() {
  pushUndo();
  graph.getElements().filter(el => el.get('isBoundary')).forEach(el => el.remove());
  markDirty();
  updateStatus('Boundaries cleared');
}

// ── Legend ───────────────────────────────────────────────────────────────────

function toggleLegend() {
  let legend = document.getElementById('pc-legend');
  if (legend) { legend.style.display = legend.style.display === 'none' ? 'block' : 'none'; return; }

  legend = document.createElement('div');
  legend.id = 'pc-legend';
  legend.style.cssText = 'position:fixed;bottom:12px;left:260px;background:#16213e;border:1px solid #1e3a6e;border-radius:8px;padding:12px 16px;z-index:50;font-size:11px;max-height:400px;overflow-y:auto;min-width:200px;box-shadow:0 4px 16px rgba(0,0,0,0.4);';

  let html = '<div style="font-weight:700;margin-bottom:8px;font-size:13px;color:#eaeaea;">Legend</div>';

  // Stage colors
  html += '<div style="font-weight:600;color:#7a8cb0;margin-bottom:4px;">Pipeline Stages</div>';
  for (const [key, color] of Object.entries(STAGE_COLORS)) {
    const label = STAGE_LABELS[key] || key;
    html += '<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">' +
      '<span style="width:12px;height:12px;border-radius:2px;background:' + color + ';flex-shrink:0;display:inline-block;"></span>' +
      '<span style="color:#eaeaea;">' + label + '</span></div>';
  }

  // Category colors
  html += '<div style="font-weight:600;color:#7a8cb0;margin:8px 0 4px;">Tool Categories</div>';
  const categories = [
    { label: 'Orchestration', color: '#3498db' },
    { label: 'Source Control', color: '#27ae60' },
    { label: 'Build', color: '#1abc9c' },
    { label: 'Security Scanning', color: '#9b59b6' },
    { label: 'Artifact / Registry', color: '#8e44ad' },
    { label: 'Supply Chain / Signing', color: '#f1c40f' },
    { label: 'Policy / Gates', color: '#e94560' },
    { label: 'Secrets / Keys', color: '#5b6abf' },
    { label: 'Deploy Targets', color: '#e67e22' },
    { label: 'Monitoring', color: '#1abc9c' },
    { label: 'Compliance', color: '#16a085' },
    { label: 'Cross-Domain / CDS', color: '#c0392b' },
    { label: 'SRE / Reliability', color: '#00bcd4' },
    { label: 'Service Mesh', color: '#e91e63' },
    { label: 'Infrastructure (NDC Bridge)', color: '#d4a017' },
  ];
  categories.forEach(c => {
    html += '<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">' +
      '<span style="width:12px;height:12px;border-radius:2px;border:2px solid ' + c.color + ';flex-shrink:0;display:inline-block;"></span>' +
      '<span style="color:#eaeaea;">' + c.label + '</span></div>';
  });

  // CSP colors
  html += '<div style="font-weight:600;color:#7a8cb0;margin:8px 0 4px;">Cloud Providers</div>';
  const csps = [
    { label: 'AWS', color: '#ff9900' },
    { label: 'Azure', color: '#0078d4' },
    { label: 'GCP', color: '#4285f4' },
    { label: 'OCI', color: '#f80000' },
    { label: 'IBM', color: '#1261fe' },
  ];
  csps.forEach(c => {
    html += '<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">' +
      '<span style="width:12px;height:12px;border-radius:2px;border:2px solid ' + c.color + ';flex-shrink:0;display:inline-block;"></span>' +
      '<span style="color:#eaeaea;">' + c.label + '</span></div>';
  });

  // Close button
  html += '<div style="margin-top:8px;text-align:right;"><button class="tb-btn" onclick="toggleLegend()">Close</button></div>';

  legend.innerHTML = html;
  document.body.appendChild(legend);
}

// ── Keyboard Shortcuts (consolidated — pdx-ux-01) ───────────────────────────
// Single global listener replaces the former two (zoom keys registered inside
// initCanvas + edit keys at the monolith's end). Delete/Backspace and bare zoom
// keys are guarded so they never fire while a form field or contenteditable
// element is focused. Ctrl/Cmd+Shift+Z is redo.

function _isEditableTarget(t) {
  if (!t) return false;
  const tag = t.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return !!t.isContentEditable;
}

document.addEventListener('keydown', (evt) => {
  const editable = _isEditableTarget(evt.target);

  // Ctrl/Cmd combos: undo / redo / save. Suppressed while editing a field so the
  // browser's native text editing (undo, save-page) is not hijacked.
  if (evt.ctrlKey || evt.metaKey) {
    if (editable) return;
    const k = (evt.key || '').toLowerCase();
    if (k === 'z' && evt.shiftKey) { evt.preventDefault(); redoAction(); }
    else if (k === 'z') { evt.preventDefault(); undoAction(); }
    else if (k === 'y') { evt.preventDefault(); redoAction(); }
    else if (k === 's') { evt.preventDefault(); savePipeline(); }
    return;
  }

  if (editable) return; // bare keys never mutate the graph while typing

  if (evt.key === '+' || evt.key === '=') { zoomIn(); evt.preventDefault(); }
  else if (evt.key === '-') { zoomOut(); evt.preventDefault(); }
  else if (evt.key === '0') { zoomReset(); evt.preventDefault(); }
  else if (evt.key === 'f' || evt.key === 'F') { zoomFit(); evt.preventDefault(); }
  else if (evt.key === 'Delete' || evt.key === 'Backspace') { evt.preventDefault(); deleteSelected(); }
});
