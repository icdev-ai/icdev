/**
 * Shared Design Canvas — JointJS canvas logic for IDC, DDC, BDC, ODC.
 *
 * Renders graph_json (nodes + edges) from templates onto a JointJS paper.
 * Each canvas page sets window.CANVAS_CONFIG before loading this script:
 *   {
 *     containerId: 'canvas-container',
 *     designId: 'new' | 'idc-xxxx',
 *     apiBase: '/infra/api' | '/data/api' | ...,
 *     graphJson: '{"nodes":[...],"edges":[...]}',
 *     nodeStyles: { 'type-prefix': { fill, stroke, symbol }, ... },
 *     accentColor: '#00bcd4',
 *   }
 */

/* ── State ──────────────────────────────────────────────────────────────── */
let graph, paper;
let selectedCell = null;
let isDirty = false;

/* ── Category → color mapping (fallback if no explicit style) ──────────── */
const CATEGORY_COLORS = {
  // IDC
  'aws':    { fill: '#1a1a2e', stroke: '#ff9900' },
  'az':     { fill: '#1a1a2e', stroke: '#0078d4' },
  'gcp':    { fill: '#1a1a2e', stroke: '#4285f4' },
  'oci':    { fill: '#1a1a2e', stroke: '#f80000' },
  'ibm':    { fill: '#1a1a2e', stroke: '#054ada' },
  'op':     { fill: '#1a1a2e', stroke: '#9b59b6' },
  'iac':    { fill: '#1a2e1a', stroke: '#27ae60' },
  // DDC
  'ent':    { fill: '#0f2b3a', stroke: '#3498db' },
  'col':    { fill: '#1a2e2e', stroke: '#1abc9c' },
  'flow':   { fill: '#1a1a2e', stroke: '#e67e22' },
  'ctrl':   { fill: '#1a2e1a', stroke: '#27ae60' },
  'bnd':    { fill: '#2b1a1a', stroke: '#e94560' },
  // BDC
  'sys':    { fill: '#0f2b3a', stroke: '#3498db' },
  'isa':    { fill: '#1a1a2e', stroke: '#e67e22' },
  'doc':    { fill: '#1a2e1a', stroke: '#7a8cb0' },
  // ODC
  'src':    { fill: '#0f2b3a', stroke: '#3498db' },
  'plt':    { fill: '#1a2e1a', stroke: '#27ae60' },
  'auto':   { fill: '#2b1a1a', stroke: '#e94560' },
  'cmp':    { fill: '#1a2e2e', stroke: '#1abc9c' },
  // Generic
  'default': { fill: '#16213e', stroke: '#5a6e8c' },
};

function getNodeColor(nodeType) {
  const cfg = window.CANVAS_CONFIG || {};
  if (cfg.nodeStyles && cfg.nodeStyles[nodeType]) {
    return cfg.nodeStyles[nodeType];
  }
  // Match by prefix
  const prefix = (nodeType || '').split('-')[0];
  return CATEGORY_COLORS[prefix] || CATEGORY_COLORS['default'];
}

/* ── Node rendering ────────────────────────────────────────────────────── */

function createNode(nodeData) {
  const x = nodeData.x || 100;
  const y = nodeData.y || 100;
  const label = nodeData.label || nodeData.type || '?';
  const colors = getNodeColor(nodeData.type);
  const isBoundary = (nodeData.type || '').startsWith('bnd-') ||
                     (nodeData.type || '').startsWith('boundary-');

  if (isBoundary) {
    // Render boundaries as rectangles with dashed border
    // Use width/height from node data if provided, otherwise default
    const w = nodeData.width || 200;
    const h = nodeData.height || 150;
    const rect = new joint.shapes.standard.Rectangle({
      id: nodeData.id,
      position: { x, y },
      size: { width: w, height: h },
      attrs: {
        body: {
          fill: colors.fill + '22',
          stroke: colors.stroke,
          strokeWidth: 2,
          strokeDasharray: '8,4',
          rx: 8, ry: 8,
        },
        label: {
          text: label,
          fill: colors.stroke,
          fontSize: 12,
          fontWeight: 700,
          refY: 10,
          textAnchor: 'middle',
          textVerticalAnchor: 'top',
        },
      },
    });
    rect.set('nodeType', nodeData.type);
    rect.set('nodeLabel', label);
    return rect;
  }

  // Regular node — rounded rectangle
  const rect = new joint.shapes.standard.Rectangle({
    id: nodeData.id,
    position: { x, y },
    size: { width: 130, height: 50 },
    attrs: {
      body: {
        fill: colors.fill,
        stroke: colors.stroke,
        strokeWidth: 1.5,
        rx: 6, ry: 6,
      },
      label: {
        text: label,
        fill: '#eaeaea',
        fontSize: 11,
        fontWeight: 500,
      },
    },
  });
  rect.set('nodeType', nodeData.type);
  rect.set('nodeLabel', label);
  return rect;
}

function createEdge(edgeData) {
  const link = new joint.shapes.standard.Link({
    id: edgeData.id,
    source: { id: edgeData.source },
    target: { id: edgeData.target },
    attrs: {
      line: {
        stroke: '#5a6e8c',
        strokeWidth: 1.5,
        targetMarker: {
          type: 'path',
          d: 'M 8 -4 0 0 8 4 z',
          fill: '#5a6e8c',
        },
      },
    },
    labels: edgeData.label ? [{
      position: 0.5,
      attrs: {
        text: {
          text: edgeData.label,
          fill: '#7a8cb0',
          fontSize: 9,
        },
        rect: {
          fill: '#0d1b2a',
          stroke: 'none',
        },
      },
    }] : [],
  });
  return link;
}

/* ── Initialize ────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  const cfg = window.CANVAS_CONFIG || {};
  const containerId = cfg.containerId || 'canvas-container';
  const container = document.getElementById(containerId);
  if (!container) {
    console.warn('Canvas container not found:', containerId);
    return;
  }

  const accent = cfg.accentColor || '#00bcd4';
  let currentScale = 1;

  graph = new joint.dia.Graph();
  paper = new joint.dia.Paper({
    el: container,
    model: graph,
    width: '100%',
    height: '100%',
    gridSize: 10,
    drawGrid: { name: 'mesh', args: { color: '#dde3ec', thickness: 0.5 } },
    background: { color: '#ffffff' },
    interactive: true,
    defaultLink: () => new joint.shapes.standard.Link({
      attrs: {
        line: {
          stroke: accent,
          strokeWidth: 2,
          targetMarker: {
            type: 'path',
            d: 'M 10 -5 0 0 10 5 z',
            fill: accent,
          },
        },
      },
    }),
    linkPinning: false,
    defaultConnectionPoint: { name: 'boundary' },
    validateConnection: (s, ms, t, mt) => s !== t,
  });

  // Enhanced tooltips (shared utility from canvas-tooltips.js)
  if (typeof initEnhancedTooltips === 'function') {
    initEnhancedTooltips(paper, graph, (type) => {
      const color = getNodeColor(type);
      return { fill: color.fill || '#0f2b3a', stroke: color.stroke || '#3498db', label: type, symbol: '?' };
    });
  }

  // Default cursor
  container.style.cursor = 'grab';

  // Zoom + pan state
  let panState = null;

  function updateZoomLabel() {
    const lbl = document.getElementById('dc-zoom-label');
    if (lbl) lbl.textContent = Math.round(currentScale * 100) + '%';
  }

  // Wheel zoom-to-cursor
  container.addEventListener('wheel', (e) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const newScale = Math.min(4, Math.max(0.2, currentScale * factor));
    if (newScale === currentScale) return;
    const rect = container.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const { tx, ty } = paper.translate();
    const mx = (cx - tx) / currentScale;
    const my = (cy - ty) / currentScale;
    currentScale = newScale;
    paper.scale(newScale, newScale);
    paper.translate(cx - mx * newScale, cy - my * newScale);
    updateZoomLabel();
  }, { passive: false });

  // Click-drag panning on blank canvas
  paper.on('blank:pointerdown', (evt) => {
    const e = evt.originalEvent || evt;
    panState = { startX: e.clientX, startY: e.clientY, tx: paper.translate().tx, ty: paper.translate().ty };
    container.style.cursor = 'grabbing';
    selectedCell = null;
    closeRightPanel();
  });

  document.addEventListener('mousemove', (e) => {
    if (!panState) return;
    paper.translate(panState.tx + e.clientX - panState.startX, panState.ty + e.clientY - panState.startY);
  });

  document.addEventListener('mouseup', () => {
    if (panState) { panState = null; container.style.cursor = 'grab'; }
  });

  // Enable element selection — open properties panel
  paper.on('element:pointerdown', (cellView) => {
    showNodeProperties(cellView);
  });
  paper.on('link:pointerdown', (linkView) => {
    showLinkProperties(linkView);
  });
  paper.on('blank:pointerdown', () => {
    // handled above (pan + close panel)
  });

  // Load graph data (pause undo tracking during initial load)
  window._mcUndoPaused = true;
  let graphData = { nodes: [], edges: [] };
  try {
    graphData = JSON.parse(cfg.graphJson || '{"nodes":[],"edges":[]}');
  } catch (e) {
    console.warn('Failed to parse graph JSON:', e);
  }

  // Render nodes first, then edges
  const nodeMap = {};
  (graphData.nodes || []).forEach(n => {
    const cell = createNode(n);
    nodeMap[n.id] = cell;
    graph.addCell(cell);
  });

  (graphData.edges || []).forEach(e => {
    // Only add edge if both endpoints exist
    if (nodeMap[e.source] && nodeMap[e.target]) {
      const link = createEdge(e);
      graph.addCell(link);
    }
  });
  // Resolve any overlapping node positions from saved data
  _resolveNodeOverlaps(graph, 20);
  // (undo pause lifted in toolbar section after initial _pushUndo)

  // Zoom to fit if there are nodes — defer so JointJS finishes rendering and
  // flex layout is resolved before measuring container dimensions.
  if (graphData.nodes && graphData.nodes.length > 0) {
    requestAnimationFrame(() => setTimeout(() => {
      try {
        paper.scaleContentToFit({
          fittingBBox: { x: 0, y: 0, width: container.clientWidth, height: container.clientHeight },
          padding: 40, maxScale: 1, minScale: 0.1,
        });
        currentScale = paper.scale().sx;
        updateZoomLabel();
      } catch (e) { /* ignore */ }
    }, 100));
  }

  console.log(`Canvas loaded: ${graphData.nodes?.length || 0} nodes, ${graphData.edges?.length || 0} edges`);

  // ── Load snippets ─────────────────────────────────────────────────────
  const snippetPanel = document.getElementById('snippet-panel');
  if (snippetPanel && cfg.apiBase) {
    fetch(cfg.apiBase + '/snippets')
      .then(r => r.json())
      .then(data => {
        const snippets = data.snippets || [];
        if (!snippets.length) return;
        let html = '';
        let lastCat = '';
        snippets.forEach(s => {
          if (s.category !== lastCat) {
            lastCat = s.category;
            html += `<h4>${(s.category || 'general').replace(/_/g, ' ')}</h4>`;
          }
          html += `<div class="dc-snippet-item" data-snippet='${JSON.stringify(s.graph_json).slice(1,-1)}' title="${s.description || ''}">${s.name}</div>`;
        });
        snippetPanel.innerHTML = html;

        // Click to add snippet to canvas
        snippetPanel.querySelectorAll('.dc-snippet-item').forEach(el => {
          el.addEventListener('click', () => {
            try {
              let snippetGraph = el.dataset.snippet;
              // Handle double-encoded JSON
              if (typeof snippetGraph === 'string') {
                snippetGraph = JSON.parse(snippetGraph);
              }
              if (typeof snippetGraph === 'string') {
                snippetGraph = JSON.parse(snippetGraph);
              }
              // Offset positions to avoid overlap with existing nodes
              const offsetX = 50 + Math.random() * 200;
              const offsetY = 50 + Math.random() * 200;
              const idMap = {};

              (snippetGraph.nodes || []).forEach(n => {
                const newId = 'n-' + Math.random().toString(36).substr(2, 8);
                idMap[n.id] = newId;
                const cell = createNode({
                  id: newId,
                  type: n.type,
                  label: n.label,
                  x: (n.x || 0) + offsetX,
                  y: (n.y || 0) + offsetY,
                });
                graph.addCell(cell);
              });

              (snippetGraph.edges || []).forEach(e => {
                const src = idMap[e.source];
                const tgt = idMap[e.target];
                if (src && tgt) {
                  const link = createEdge({
                    id: 'e-' + Math.random().toString(36).substr(2, 8),
                    source: src,
                    target: tgt,
                    label: e.label || '',
                  });
                  graph.addCell(link);
                }
              });
              isDirty = true;
              console.log(`Snippet "${el.textContent}" added (${Object.keys(idMap).length} nodes)`);
            } catch (err) {
              console.warn('Failed to add snippet:', err);
            }
          });
        });

        console.log(`Loaded ${snippets.length} snippets`);
      })
      .catch(e => console.warn('Failed to load snippets:', e));
  }

  // ── Palette drag-and-drop ──────────────────────────────────────────────
  document.querySelectorAll('[data-type][draggable]').forEach(el => {
    el.addEventListener('dragstart', (evt) => {
      evt.dataTransfer.setData('text/plain', JSON.stringify({
        type: el.dataset.type,
        label: el.textContent.trim(),
      }));
    });
  });

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

  function _findFreePosition(x, y, w, h) {
    const PAD = 12;
    const occupied = graph.getElements().map(el => {
      const p = el.position(), s = el.size();
      return { x: p.x - PAD, y: p.y - PAD, x2: p.x + s.width + PAD, y2: p.y + s.height + PAD };
    });
    const overlaps = (cx, cy) => occupied.some(b => cx < b.x2 && cx + w > b.x && cy < b.y2 && cy + h > b.y);
    if (!overlaps(x, y)) return { x, y };
    const STEP = Math.max(w, h) + PAD;
    for (let ring = 1; ring <= 10; ring++) {
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
        if (!overlaps(c.x, c.y)) return c;
      }
    }
    return { x: x + Math.random() * 30, y: y + Math.random() * 30 };
  }

  container.addEventListener('dragover', (evt) => { evt.preventDefault(); });
  container.addEventListener('drop', (evt) => {
    evt.preventDefault();
    try {
      const data = JSON.parse(evt.dataTransfer.getData('text/plain'));
      const rect = container.getBoundingClientRect();
      const { tx, ty } = paper.translate();
      let x = (evt.clientX - rect.left - tx) / currentScale;
      let y = (evt.clientY - rect.top  - ty) / currentScale;
      const pos = _findFreePosition(x, y, 130, 50);
      x = pos.x; y = pos.y;
      const nodeId = 'n-' + Math.random().toString(36).substr(2, 8);
      const cell = createNode({ id: nodeId, type: data.type, label: data.label, x, y });
      graph.addCell(cell);
      isDirty = true;
    } catch (e) { console.warn('Drop failed:', e); }
  });

  // ── Collect graph data from canvas ─────────────────────────────────────
  function collectGraph() {
    const nodes = [];
    const edges = [];
    graph.getCells().forEach(cell => {
      if (cell.isElement()) {
        const pos = cell.position();
        const size = cell.size();
        const nodeData = {
          id: cell.id,
          type: cell.get('nodeType') || '',
          label: cell.get('nodeLabel') || '',
          x: Math.round(pos.x),
          y: Math.round(pos.y),
        };
        // Preserve width/height for boundary nodes
        if (size.width !== 130 || size.height !== 50) {
          nodeData.width = size.width;
          nodeData.height = size.height;
        }
        nodes.push(nodeData);
      } else if (cell.isLink()) {
        edges.push({
          id: cell.id,
          source: cell.get('source')?.id || '',
          target: cell.get('target')?.id || '',
          label: (cell.labels()?.[0]?.attrs?.text?.text) || '',
        });
      }
    });
    return { nodes, edges };
  }

  // ── Right Panel helpers (matches NDC/PDC pattern) ────────────────────────
  function _pill(text, color) {
    return `<span style="display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600;background:${color};color:#fff;margin:1px 2px;">${text}</span>`;
  }
  function _bar(pct, color) {
    return `<div style="background:#0f2040;border-radius:4px;height:8px;margin:4px 0 8px;overflow:hidden;"><div style="width:${Math.min(pct,100)}%;height:100%;background:${color};border-radius:4px;"></div></div>`;
  }
  function _metric(label, value, sub) {
    return `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><span style="color:#7a8cb0;">${label}</span><span style="font-weight:600;">${value}</span></div>` + (sub ? `<div style="font-size:10px;color:#5a6e8c;padding:0 0 4px;">${sub}</div>` : '');
  }
  function _section(title) {
    return `<div style="font-weight:700;font-size:12px;margin:14px 0 6px;padding-bottom:4px;border-bottom:1px solid #1e3a6e;color:#eaeaea;">${title}</div>`;
  }
  function _sevColor(sev) {
    if (sev === 'CAT1') return '#e74c3c';
    if (sev === 'CAT2') return '#f39c12';
    return '#3498db';
  }

  window.openRightPanel = function(title, html) {
    const panel = document.getElementById('dc-right-panel');
    if (!panel) { alert(title + '\n\n' + html.replace(/<[^>]*>/g, '')); return; }
    panel.classList.add('open');
    document.getElementById('dc-panel-title').textContent = title;
    document.getElementById('dc-panel-body').innerHTML = html;
  };

  window.closeRightPanel = function() {
    const panel = document.getElementById('dc-right-panel');
    if (panel) panel.classList.remove('open');
  };

  function showStatus(msg, duration) {
    const info = document.querySelector('.dc-info');
    if (info) {
      const orig = info.textContent;
      info.textContent = msg;
      info.style.color = '#27ae60';
      setTimeout(() => { info.textContent = orig; info.style.color = '#5a6e8c'; }, duration || 3000);
    }
  }

  // ── Save function ──────────────────────────────────────────────────────
  window.canvasSave = function() {
    const nameEl = document.querySelector('[id$="-name"]');
    const name = nameEl ? nameEl.value : 'Untitled';
    const graphData = collectGraph();

    if (cfg.designId === 'new') {
      // CREATE new design via POST
      fetch(cfg.apiBase + '/designs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          graph: graphData,
          graph_json: JSON.stringify(graphData),
          classification: 'CUI',
        }),
      })
      .then(r => r.json())
      .then(d => {
        if (d.id) {
          cfg.designId = d.id;
          isDirty = false;
          // Update URL without reload so future saves use PUT
          const basePath = cfg.apiBase.replace('/api', '');
          window.history.replaceState({}, '', basePath + '/canvas/' + d.id);
          // Update info bar
          const infoEl = document.querySelector('.dc-info');
          if (infoEl) {
            infoEl.textContent = infoEl.textContent.replace('new', d.id);
          }
          showStatus('\u2713 Created: ' + d.id);
        } else {
          openRightPanel('Save Error', '<p style="color:#e74c3c;">Unexpected response: ' + JSON.stringify(d) + '</p>');
        }
      })
      .catch(e => openRightPanel('Save Error', `<p style="color:#e74c3c;">Create failed: ${e}</p>`));
    } else {
      // UPDATE existing design via PUT
      fetch(cfg.apiBase + '/designs/' + cfg.designId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          graph: graphData,
          graph_json: JSON.stringify(graphData),
          classification: 'CUI',
        }),
      })
      .then(r => r.json())
      .then(d => {
        isDirty = false;
        showStatus('\u2713 Saved');
      })
      .catch(e => openRightPanel('Save Error', `<p style="color:#e74c3c;">Save failed: ${e}</p>`));
    }
  };

  // ── Assess function ────────────────────────────────────────────────────
  window.canvasAssess = function() {
    if (cfg.designId === 'new') {
      // Auto-save first, then assess
      const nameEl = document.querySelector('[id$="-name"]');
      const name = nameEl ? nameEl.value : 'Untitled';
      const graphData = collectGraph();

      fetch(cfg.apiBase + '/designs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          graph: graphData,
          graph_json: JSON.stringify(graphData),
          classification: 'CUI',
        }),
      })
      .then(r => r.json())
      .then(d => {
        if (d.id) {
          cfg.designId = d.id;
          const basePath = cfg.apiBase.replace('/api', '');
          window.history.replaceState({}, '', basePath + '/canvas/' + d.id);
          const infoEl = document.querySelector('.dc-info');
          if (infoEl) infoEl.textContent = infoEl.textContent.replace('new', d.id);
          // Now run assessment
          runAssessment(d.id);
        }
      })
      .catch(e => openRightPanel('Save Error', `<p style="color:#e74c3c;">Auto-save failed: ${e}</p>`));
    } else {
      runAssessment(cfg.designId);
    }
  };

  function runAssessment(designId) {
    fetch(cfg.apiBase + '/designs/' + designId + '/assess', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        const a = d.assessment || d;
        const score = Math.round(a.score ?? a.risk_score ?? 0);
        const grade = a.grade || a.posture_grade || '';
        const total = a.total_findings ?? a.finding_count ?? a.summary?.total ?? 0;
        const findings = a.findings || d.findings || [];

        const scoreColor = score >= 80 ? '#27ae60' : score >= 50 ? '#f39c12' : '#e74c3c';
        let html = '';
        html += `<div style="text-align:center;padding:12px 0;">`;
        html += `<div style="font-size:36px;font-weight:700;color:${scoreColor};">${score}</div>`;
        html += `<div style="font-size:11px;color:#7a8cb0;">out of 100</div>`;
        if (grade) html += `<div style="font-size:14px;font-weight:600;margin-top:4px;">${grade}</div>`;
        html += `</div>`;
        html += _bar(score, scoreColor);
        html += _metric('Total Findings', total);

        // Findings by severity
        const bySev = {};
        findings.forEach(f => { const s = f.severity || '?'; bySev[s] = (bySev[s]||0)+1; });
        Object.entries(bySev).forEach(([sev, cnt]) => {
          html += _metric(sev, _pill(cnt, _sevColor(sev)));
        });

        if (findings.length > 0) {
          html += _section('Findings');
          findings.slice(0, 20).forEach(f => {
            const sev = f.severity || '?';
            html += `<div style="padding:8px;background:#16213e;border-left:3px solid ${_sevColor(sev)};border-radius:4px;margin-bottom:6px;">`;
            html += `<div style="font-weight:600;font-size:11px;">${_pill(sev, _sevColor(sev))} ${f.title || ''}</div>`;
            const detail = f.detail || f.description || f.message || '';
            if (detail) html += `<div style="font-size:10px;color:#7a8cb0;margin-top:4px;">${detail.substring(0, 200)}</div>`;
            if (f.affected_entity) html += `<div style="font-size:10px;color:#5a6e8c;margin-top:2px;">Affected: ${f.affected_entity}</div>`;
            html += `</div>`;
          });
          if (findings.length > 20) html += `<div style="color:#5a6e8c;font-size:10px;">...and ${findings.length-20} more</div>`;
        }

        openRightPanel('Compliance Assessment', html);
      })
      .catch(e => openRightPanel('Assessment Error', `<p style="color:#e74c3c;">${e}</p>`));
  }

  // ══════════════════════════════════════════════════════════════════════
  // SHARED TOOLBAR FUNCTIONS
  // ══════════════════════════════════════════════════════════════════════

  // ── Undo / Redo ────────────────────────────────────────────────────────
  const undoStack = [];
  const redoStack = [];

  function _pushUndo() {
    if (window._mcUndoPaused) return;
    undoStack.push(JSON.stringify(graph.toJSON()));
    if (undoStack.length > 50) undoStack.shift();
    redoStack.length = 0;
  }

  // Track mutations for undo: add, remove
  graph.on('add', () => { _pushUndo(); isDirty = true; });
  graph.on('remove', () => { _pushUndo(); isDirty = true; });
  graph.on('change:position', () => { isDirty = true; });
  graph.on('change', () => { isDirty = true; });

  // Capture state after initial load completed (initial state is the baseline)
  window._mcUndoPaused = false;
  _pushUndo();

  window.canvasUndo = function() {
    if (undoStack.length <= 1) return;
    window._mcUndoPaused = true;
    redoStack.push(undoStack.pop());
    const state = undoStack[undoStack.length - 1];
    graph.fromJSON(JSON.parse(state));
    window._mcUndoPaused = false;
    selectedCell = null;
  };
  window.canvasRedo = function() {
    if (!redoStack.length) return;
    window._mcUndoPaused = true;
    const state = redoStack.pop();
    undoStack.push(state);
    graph.fromJSON(JSON.parse(state));
    window._mcUndoPaused = false;
    selectedCell = null;
  };

  // ── Delete selected node/edge ─────────────────────────────────────────
  window.canvasDeleteSelected = function() {
    if (selectedCell) {
      selectedCell.remove();
      selectedCell = null;
      isDirty = true;
    }
  };

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Delete' || e.key === 'Backspace') {
      // Don't intercept if typing in an input/textarea
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      e.preventDefault();
      canvasDeleteSelected();
    }
  });

  // ── Zoom ───────────────────────────────────────────────────────────────
  window.canvasZoomIn = function() {
    const rect = container.getBoundingClientRect();
    const cx = rect.width / 2, cy = rect.height / 2;
    const { tx, ty } = paper.translate();
    const mx = (cx - tx) / currentScale, my = (cy - ty) / currentScale;
    currentScale = Math.min(currentScale * 1.2, 4);
    paper.scale(currentScale, currentScale);
    paper.translate(cx - mx * currentScale, cy - my * currentScale);
    updateZoomLabel();
  };
  window.canvasZoomOut = function() {
    const rect = container.getBoundingClientRect();
    const cx = rect.width / 2, cy = rect.height / 2;
    const { tx, ty } = paper.translate();
    const mx = (cx - tx) / currentScale, my = (cy - ty) / currentScale;
    currentScale = Math.max(currentScale * 0.8, 0.2);
    paper.scale(currentScale, currentScale);
    paper.translate(cx - mx * currentScale, cy - my * currentScale);
    updateZoomLabel();
  };
  window.canvasZoomFit = function() {
    try {
      paper.scaleContentToFit({
        fittingBBox: { x: 0, y: 0, width: container.clientWidth, height: container.clientHeight },
        padding: 40, maxScale: 1, minScale: 0.1,
      });
      currentScale = paper.scale().sx;
      updateZoomLabel();
    } catch (e) { /* ignore */ }
  };
  window.canvasZoomReset = function() {
    currentScale = 1;
    paper.scale(1, 1);
    paper.translate(0, 0);
    updateZoomLabel();
  };

  // ── Export (SVG / DrawIO / JSON) ───────────────────────────────────────
  window.canvasExport = function(format) {
    if (format === 'svg') {
      const svgEl = container.querySelector('svg');
      if (!svgEl) { openRightPanel('Export Error', '<p style="color:#e74c3c;">No SVG found</p>'); return; }
      const svgData = new XMLSerializer().serializeToString(svgEl);
      const blob = new Blob([svgData], { type: 'image/svg+xml' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (cfg.designId || 'design') + '.svg';
      a.click();
    } else if (format === 'json') {
      const graphData = collectGraph();
      const blob = new Blob([JSON.stringify(graphData, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (cfg.designId || 'design') + '.json';
      a.click();
    } else if (format === 'drawio') {
      const graphData = collectGraph();
      // Build minimal draw.io XML
      let xml = '<?xml version="1.0"?><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>';
      let cellId = 2;
      const idMap = {};
      graphData.nodes.forEach(n => {
        idMap[n.id] = cellId;
        const w = n.width || 130;
        const h = n.height || 50;
        xml += `<mxCell id="${cellId}" value="${n.label}" style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1"><mxGeometry x="${n.x}" y="${n.y}" width="${w}" height="${h}" as="geometry"/></mxCell>`;
        cellId++;
      });
      graphData.edges.forEach(e => {
        const src = idMap[e.source];
        const tgt = idMap[e.target];
        if (src && tgt) {
          xml += `<mxCell id="${cellId}" value="${e.label || ''}" edge="1" source="${src}" target="${tgt}" parent="1"><mxGeometry relative="1" as="geometry"/></mxCell>`;
          cellId++;
        }
      });
      xml += '</root></mxGraphModel>';
      const blob = new Blob([xml], { type: 'application/xml' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (cfg.designId || 'design') + '.drawio';
      a.click();
    } else if (format === 'visio' || format === 'vsdx') {
      // Visio export via server-side API
      if (cfg.designId === 'new') { openRightPanel('Export', '<p style="color:#f39c12;">Save design first to export Visio.</p>'); return; }
      fetch(cfg.apiBase + '/export/' + cfg.designId + '/vsdx', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
          if (d.data) {
            // Base64-encoded vsdx — decode and download
            const bytes = atob(d.data);
            const arr = new Uint8Array(bytes.length);
            for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
            const blob = new Blob([arr], { type: 'application/vnd.ms-visio.drawing' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = (d.filename || cfg.designId) + '.vsdx';
            a.click();
          } else {
            openRightPanel('Export Error', `<p style="color:#e74c3c;">Visio export failed: ${d.error || 'unknown error'}</p>`);
          }
        })
        .catch(e => openRightPanel('Export Error', `<p style="color:#e74c3c;">Visio export failed: ${e}</p>`));
    } else if (format === 'pptx') {
      // PowerPoint export via server-side API (GET -> raw .pptx bytes)
      if (cfg.designId === 'new') { openRightPanel('Export', '<p style="color:#f39c12;">Save design first to export PowerPoint.</p>'); return; }
      fetch(cfg.apiBase + '/export/' + cfg.designId + '/pptx', { method: 'GET' })
        .then(r => {
          if (!r.ok) { throw new Error('HTTP ' + r.status); }
          return r.blob();
        })
        .then(blob => {
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = (cfg.designId || 'design') + '.pptx';
          a.click();
        })
        .catch(e => openRightPanel('Export Error', `<p style="color:#e74c3c;">PowerPoint export failed: ${e}</p>`));
    } else if (format === 'terraform' || format === 'ansible') {
      openRightPanel('Export', `<p style="color:#7a8cb0;">${format.charAt(0).toUpperCase() + format.slice(1)} export: use the IaC Gallery for generated code.</p>`);
    }
  };

  // ── Data Governance Framework Check ───────────────────────────────────
  window.canvasGovernance = function() {
    if (cfg.designId === 'new') {
      openRightPanel('Governance', '<p style="color:#f39c12;">Save the design first, then run Governance check.</p>');
      return;
    }
    openRightPanel('Governance', '<p style="color:#7a8cb0;font-size:11px;">Running governance framework check…</p>');
    fetch(cfg.apiBase + '/designs/' + cfg.designId + '/governance', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        if (d.error) { openRightPanel('Governance', `<p style="color:#e74c3c;">${d.error}</p>`); return; }

        const score    = d.score || 0;
        const grade    = d.grade || '?';
        const maturity = d.maturity || {};
        const cats     = d.categories || {};
        const checks   = d.checks || [];
        const recs     = d.recommendations || [];

        const scoreColor = score >= 80 ? '#27ae60' : score >= 60 ? '#f39c12' : '#e74c3c';
        const matColors  = ['#e74c3c','#e74c3c','#f39c12','#f39c12','#27ae60'];
        const matColor   = matColors[Math.min((maturity.level || 1) - 1, 4)];

        let html = '';

        // Header score card
        html += `<div style="background:#0f1e36;border-radius:8px;padding:12px;margin-bottom:12px;text-align:center;">`;
        html += `<div style="font-size:38px;font-weight:700;color:${scoreColor};">${score}</div>`;
        html += `<div style="font-size:11px;color:#7a8cb0;">Governance Score / 100 &nbsp;·&nbsp; Grade <strong style="color:${scoreColor};">${grade}</strong></div>`;
        html += `</div>`;
        html += _bar(score, scoreColor);

        // Maturity badge
        const matIcons = ['','🔴','🟠','🟡','🟢','🟢'];
        html += `<div style="background:#16213e;border:1px solid #9b59b644;border-radius:6px;padding:8px 12px;margin-bottom:12px;display:flex;align-items:center;gap:10px;">`;
        html += `<div style="font-size:22px;">${matIcons[maturity.level||1]}</div>`;
        html += `<div><div style="font-size:12px;font-weight:700;color:${matColor};">Level ${maturity.level||1} — ${maturity.label||''}</div>`;
        html += `<div style="font-size:10px;color:#5a6e8c;">${maturity.description||''}</div></div>`;
        html += `</div>`;

        // Per-pillar breakdown
        html += _section(`Pillar Breakdown (${d.passed_checks}/${d.total_checks} checks passed)`);
        const PILLAR_ICONS = {
          'Stewardship': '👤', 'Catalog & Metadata': '📚', 'Lineage & Provenance': '🔗',
          'Quality & Observability': '📊', 'Privacy & Consent': '🔒',
          'Compliance & Policy': '📋', 'Data Mesh Governance': '🕸',
        };
        Object.entries(cats).forEach(([pillar, ps]) => {
          const pct = ps.pct || 0;
          const pColor = pct >= 80 ? '#27ae60' : pct >= 50 ? '#f39c12' : '#e74c3c';
          const icon = PILLAR_ICONS[pillar] || '•';
          html += `<div style="margin-bottom:8px;">`;
          html += `<div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:2px;">`;
          html += `<span style="color:#eaeaea;">${icon} ${pillar}</span>`;
          html += `<span style="color:${pColor};font-weight:600;">${pct}% &nbsp;(${ps.passed}/${ps.total})</span>`;
          html += `</div>`;
          html += _bar(pct, pColor);
          html += `</div>`;
        });

        // All checks detail
        html += _section('Framework Checks');
        const byPillar = {};
        checks.forEach(c => { (byPillar[c.pillar] = byPillar[c.pillar] || []).push(c); });
        Object.entries(byPillar).forEach(([pillar, cs]) => {
          html += `<div style="margin-bottom:2px;padding:4px 0;border-bottom:1px solid #1e3a6e;font-size:10px;font-weight:700;color:#7a8cb0;text-transform:uppercase;letter-spacing:.5px;">${PILLAR_ICONS[pillar]||''} ${pillar}</div>`;
          cs.forEach(c => {
            const stColor = c.status === 'PASS' ? '#27ae60' : c.status === 'WARN' ? '#f39c12' : '#e74c3c';
            const stIcon  = c.status === 'PASS' ? '✓' : c.status === 'WARN' ? '⚠' : '✗';
            html += `<div style="padding:6px 8px;margin:2px 0;background:#16213e;border-radius:4px;border-left:3px solid ${stColor};">`;
            html += `<div style="display:flex;justify-content:space-between;font-size:11px;">`;
            html += `<span style="color:#eaeaea;font-weight:600;">${stIcon} ${c.title}</span>`;
            html += `<span style="font-size:9px;color:${stColor};font-weight:700;">${c.status}</span>`;
            html += `</div>`;
            html += `<div style="font-size:10px;color:#5a6e8c;margin-top:2px;">${c.framework}</div>`;
            if (!c.passed && c.detail) {
              html += `<div style="font-size:10px;color:#7a8cb0;margin-top:3px;">${c.detail}</div>`;
            }
            html += `</div>`;
          });
        });

        // Recommendations
        if (recs.length > 0) {
          html += _section(`Recommendations (${recs.length})`);
          const sevOrder = { 'HIGH': 0, 'MEDIUM': 1, 'LOW': 2 };
          recs.sort((a, b) => (sevOrder[a.severity]||9) - (sevOrder[b.severity]||9));
          recs.forEach(r => {
            const rColor = r.severity === 'HIGH' ? '#e74c3c' : r.severity === 'MEDIUM' ? '#f39c12' : '#3498db';
            html += `<div style="padding:8px;background:#16213e;border-left:3px solid ${rColor};border-radius:0 4px 4px 0;margin-bottom:6px;">`;
            html += `<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">`;
            html += `${_pill(r.severity, rColor)}`;
            html += `<span style="font-size:11px;font-weight:600;color:#eaeaea;">${r.title}</span>`;
            html += `</div>`;
            html += `<div style="font-size:10px;color:#7a8cb0;">${r.recommendation}</div>`;
            html += `<div style="font-size:9px;color:#5a6e8c;margin-top:2px;">${r.pillar} · ${r.id}</div>`;
            html += `</div>`;
          });
        } else {
          html += `<div style="text-align:center;padding:12px;"><div style="font-size:20px;">🏆</div><div style="color:#27ae60;font-weight:600;font-size:12px;">All governance checks passed</div></div>`;
        }

        openRightPanel('Data Governance', html);
      })
      .catch(e => openRightPanel('Governance Error', `<p style="color:#e74c3c;">${e}</p>`));
  };

  // ── Scorecard ──────────────────────────────────────────────────────────
  window.canvasScorecard = function() {
    if (cfg.designId === 'new') { openRightPanel('Scorecard', '<p style="color:#f39c12;">Save first.</p>'); return; }
    runAssessment(cfg.designId);
  };

  // ── Gaps Detection ─────────────────────────────────────────────────────
  window.canvasGaps = function() {
    if (cfg.designId === 'new') { openRightPanel('Gap Analysis', '<p style="color:#f39c12;">Save the design first.</p>'); return; }
    fetch(cfg.apiBase + '/designs/' + cfg.designId + '/assess', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        const gaps = d.gaps || d.gap_analysis || (d.assessment||{}).gaps || [];
        const gapList = gaps.gaps || gaps.recommendations || (Array.isArray(gaps) ? gaps : []);
        let html = '';
        if (gapList.length === 0) {
          html += '<div style="text-align:center;padding:20px;"><div style="font-size:24px;color:#27ae60;">\u2713</div><div style="color:#27ae60;font-weight:600;">No gaps detected</div><div style="color:#7a8cb0;font-size:11px;margin-top:4px;">Design is compliant</div></div>';
        } else {
          html += _metric('Total Gaps', gapList.length);
          html += _section('Recommendations');
          gapList.slice(0, 20).forEach(g => {
            const sev = g.severity || g.priority || 'info';
            const sevColor = sev.includes('1') || sev === 'critical' ? '#e74c3c' : sev.includes('2') || sev === 'recommended' ? '#f39c12' : '#3498db';
            html += `<div style="padding:8px;background:#16213e;border-left:3px solid ${sevColor};border-radius:4px;margin-bottom:6px;">`;
            html += `<div style="font-weight:600;font-size:11px;">${g.action || g.recommendation || g.title || g.description || ''}</div>`;
            if (g.affected) html += `<div style="font-size:10px;color:#5a6e8c;margin-top:2px;">Affected: ${g.affected}</div>`;
            if (g.recommended_control) html += `<div style="font-size:10px;color:#5a6e8c;margin-top:2px;">Add: ${g.recommended_control}</div>`;
            html += `</div>`;
          });
        }
        openRightPanel('Gap Analysis', html);
      })
      .catch(e => openRightPanel('Gap Analysis', `<p style="color:#e74c3c;">Failed: ${e}</p>`));
  };

  // ── Legend Toggle ──────────────────────────────────────────────────────
  // Human-readable labels for each prefix, per canvas type
  const LEGEND_LABELS = {
    // IDC prefixes
    'aws': 'Amazon Web Services', 'az': 'Microsoft Azure', 'gcp': 'Google Cloud',
    'oci': 'Oracle Cloud', 'ibm': 'IBM Cloud', 'op': 'On-Premises',
    'iac': 'IaC / DevOps Tools',
    // DDC prefixes
    'ent': 'Entities (Tables / Stores)', 'col': 'Columns (Data Fields)',
    'flow': 'Data Flows (ETL / API / CDC)', 'ctrl': 'Controls (Security / Policy)',
    'bnd': 'Boundaries (Zones / Regions)',
    // BDC prefixes
    'sys': 'Systems', 'isa': 'Interconnections (ISA)',
    'doc': 'Documentation', 'boundary': 'Authorization Boundaries',
    // ODC prefixes
    'src': 'Log / Telemetry Sources', 'plt': 'Platforms (SIEM / Analytics)',
    'auto': 'Automation (SOAR / Alerts)', 'cmp': 'Compliance',
  };

  window.canvasLegend = function() {
    // Build legend from actual nodes on the canvas — only show categories present
    const typeCounts = {};
    graph.getCells().forEach(cell => {
      if (!cell.isElement()) return;
      const t = cell.get('nodeType') || '';
      const prefix = t.split('-')[0];
      if (!prefix) return;
      if (!typeCounts[prefix]) typeCounts[prefix] = { count: 0, color: null };
      typeCounts[prefix].count++;
      if (!typeCounts[prefix].color) {
        typeCounts[prefix].color = getNodeColor(t);
      }
    });

    let html = '';
    if (Object.keys(typeCounts).length === 0) {
      html = '<p style="color:#7a8cb0;font-size:11px;">No objects on canvas yet. Drag items from the palette to get started.</p>';
    } else {
      // Sort by count descending
      const sorted = Object.entries(typeCounts).sort((a, b) => b[1].count - a[1].count);
      sorted.forEach(([prefix, info]) => {
        const label = LEGEND_LABELS[prefix] || prefix.toUpperCase();
        const color = info.color ? info.color.stroke : '#5a6e8c';
        html += `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.04);">`;
        html += `<div style="width:14px;height:14px;border-radius:3px;background:${color};flex-shrink:0;"></div>`;
        html += `<div style="flex:1;"><div style="color:#eaeaea;font-size:11px;font-weight:500;">${label}</div>`;
        html += `<div style="color:#5a6e8c;font-size:10px;">${info.count} object${info.count > 1 ? 's' : ''}</div></div>`;
        html += `</div>`;
      });
      html += `<div style="margin-top:10px;padding-top:8px;border-top:1px solid #1e3a6e;font-size:10px;color:#5a6e8c;">`;
      html += `Total: ${Object.values(typeCounts).reduce((s, v) => s + v.count, 0)} objects</div>`;
    }
    openRightPanel('Legend', html);
  };

  // ── NIST Overlay ───────────────────────────────────────────────────────
  window.canvasNistOverlay = function() {
    if (cfg.designId === 'new') { openRightPanel('NIST Overlay', '<p style="color:#f39c12;">Save first.</p>'); return; }
    fetch(cfg.apiBase + '/designs/' + cfg.designId + '/assess', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        const nist = d.nist_coverage || (d.assessment||{}).nist_coverage || {};
        const families = nist.families || [];
        const overall = Math.round(nist.overall_coverage_pct || 0);
        const overallColor = overall >= 70 ? '#27ae60' : overall >= 40 ? '#f39c12' : '#e74c3c';
        let html = `<div style="text-align:center;padding:8px 0;"><div style="font-size:30px;font-weight:700;color:${overallColor};">${overall}%</div><div style="font-size:11px;color:#7a8cb0;">Overall NIST Coverage</div></div>`;
        html += _bar(overall, overallColor);
        html += _section('Control Families');
        families.forEach(f => {
          const pct = Math.round(f.coverage_pct || 0);
          const color = pct >= 70 ? '#27ae60' : pct >= 40 ? '#f39c12' : '#e74c3c';
          html += `<div style="margin-bottom:6px;"><div style="display:flex;justify-content:space-between;font-size:11px;"><span style="color:#7a8cb0;">${f.code} ${f.name || ''}</span><span style="font-weight:600;color:${color};">${pct}%</span></div>`;
          html += _bar(pct, color) + '</div>';
        });
        openRightPanel('NIST 800-53 Coverage', html);
      })
      .catch(e => openRightPanel('NIST Overlay', `<p style="color:#e74c3c;">Failed: ${e}</p>`));
  };

  // ── Compliance Crosswalk ───────────────────────────────────────────────
  window.canvasCrosswalk = function() {
    if (cfg.designId === 'new') { openRightPanel('Crosswalk', '<p style="color:#f39c12;">Save first.</p>'); return; }
    fetch(cfg.apiBase + '/designs/' + cfg.designId + '/assess', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
        const cw = d.crosswalk || d.compliance_crosswalk || {};
        let html = '';
        if (cw.mappings) {
          html += _section('NIST \u2192 FedRAMP \u2192 CMMC');
          html += '<table style="width:100%;border-collapse:collapse;font-size:10px;">';
          html += '<tr style="color:#7a8cb0;border-bottom:1px solid #1e3a6e;"><th style="text-align:left;padding:4px;">NIST</th><th style="text-align:left;padding:4px;">FedRAMP</th><th style="text-align:left;padding:4px;">CMMC</th></tr>';
          cw.mappings.forEach(m => {
            html += `<tr style="border-bottom:1px solid #0f1e36;"><td style="padding:4px;">${m.nist||'\u2014'}</td><td style="padding:4px;">${m.fedramp||'\u2014'}</td><td style="padding:4px;">${m.cmmc||'\u2014'}</td></tr>`;
          });
          html += '</table>';
        } else {
          html += '<p style="color:#7a8cb0;">Crosswalk not available for this canvas type. See Security Design Canvas for full NIST/FedRAMP/CMMC crosswalk.</p>';
        }
        openRightPanel('Compliance Crosswalk', html);
      })
      .catch(e => openRightPanel('Crosswalk', `<p style="color:#e74c3c;">Failed: ${e}</p>`));
  };

  // ── FIPS Overlay ───────────────────────────────────────────────────────
  window.canvasFips = function() {
    const encNodes = [];
    graph.getCells().forEach(cell => {
      if (!cell.isElement()) return;
      const t = cell.get('nodeType') || '';
      const l = cell.get('nodeLabel') || '';
      if (t.includes('kms') || t.includes('vault') || t.includes('encrypt') || t.includes('hsm') || t.includes('keyvault')) {
        encNodes.push({ label: l, type: t });
      }
    });
    let html = '';
    if (encNodes.length) {
      html += `<div style="text-align:center;padding:8px 0;"><div style="font-size:24px;color:#27ae60;">\ud83d\udd12</div><div style="color:#27ae60;font-weight:600;">${encNodes.length} Encryption Service(s)</div></div>`;
      html += _section('FIPS-Validated Services');
      encNodes.forEach(n => {
        html += `<div style="padding:6px 8px;background:#16213e;border-left:3px solid #27ae60;border-radius:4px;margin-bottom:4px;">`;
        html += `<div style="font-size:11px;font-weight:600;">\u2713 ${n.label}</div>`;
        html += `<div style="font-size:10px;color:#5a6e8c;">${n.type}</div></div>`;
      });
    } else {
      html += `<div style="text-align:center;padding:20px;"><div style="font-size:24px;color:#e74c3c;">\u26a0</div><div style="color:#e74c3c;font-weight:600;">No Encryption Services</div><div style="color:#7a8cb0;font-size:11px;margin-top:4px;">Add KMS, Key Vault, or HSM for FIPS compliance</div></div>`;
    }
    openRightPanel('FIPS 140 Coverage', html);
  };

  // ── Export dropdown toggle ─────────────────────────────────────────────
  window.toggleExportMenu = function() {
    const menu = document.getElementById('export-dropdown');
    if (menu) menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
  };

  // Close export menu on outside click
  document.addEventListener('click', (e) => {
    const menu = document.getElementById('export-dropdown');
    if (menu && !e.target.closest('.dc-export-group')) {
      menu.style.display = 'none';
    }
  });

  // ══════════════════════════════════════════════════════════════════════
  // IaC VALIDATION & DEPLOYMENT (mirrors PDC pattern)
  // ══════════════════════════════════════════════════════════════════════

  // ── Context-aware IaC checks per canvas type ────────────────────────────
  function _getCanvasType() {
    const base = cfg.apiBase || '';
    if (base.includes('/infra')) return 'infra';
    if (base.includes('/data')) return 'data';
    if (base.includes('/observability')) return 'observability';
    if (base.includes('/migration')) return 'migration';
    return 'generic';
  }

  function _contextChecks(graphData, canvasType) {
    // Returns [{id, layer, label, status, message, detail, fix_hint, fix_snippet}]
    const checks = [];
    const nodes = graphData.nodes || [];
    const edges = graphData.edges || [];
    const nodeIds = new Set(nodes.map(n => n.id));
    const connectedIds = new Set();
    edges.forEach(e => { connectedIds.add(e.source); connectedIds.add(e.target); });
    const types = nodes.map(n => (n.type || '').toLowerCase());
    const labels = nodes.map(n => (n.label || '').toLowerCase());

    // ── Layer 1: Syntax — all nodes have type + label ──
    nodes.forEach((n, i) => {
      if (!n.type || !n.label) {
        checks.push({ id: `l1-${i}`, layer: 1, label: n.label || n.id || '(unnamed)',
          status: 'warn', message: 'Node missing type or label',
          fix_hint: 'Double-click the node to set its label. A blank label cannot be mapped to an IaC resource.',
          fix_snippet: null });
      }
    });
    if (!checks.some(c => c.layer === 1)) {
      checks.push({ id: 'l1-ok', layer: 1, label: 'All nodes', status: 'pass', message: `${nodes.length} node(s) have valid type and label` });
    }

    // ── Layer 2: Schema — edges reference existing nodes ──
    let badEdges = 0;
    edges.forEach((e, i) => {
      if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) badEdges++;
    });
    checks.push({ id: 'l2-edges', layer: 2, label: 'Edge references',
      status: badEdges > 0 ? 'fail' : 'pass',
      message: badEdges > 0 ? `${badEdges} edge(s) reference missing nodes — delete and redraw` : `${edges.length} edge(s) valid`,
      fix_hint: badEdges > 0 ? 'Delete the broken connections (shown in red) and redraw them between valid nodes.' : null });

    // ── Layer 3: Canvas-type-specific policy checks ──
    if (canvasType === 'infra') {
      const hasIaC = types.some(t => t.startsWith('iac-')) || labels.some(l => l.includes('terraform') || l.includes('helm'));
      checks.push({ id: 'l3-iac', layer: 3, label: 'IaC tool present',
        status: hasIaC ? 'pass' : 'warn', message: hasIaC ? 'IaC tool found in design' : 'No Terraform/Helm node found',
        fix_hint: hasIaC ? null : 'Add a Terraform or Helm node from the IaC palette to wire deployment tooling to the infrastructure.',
        fix_snippet: hasIaC ? null : '# Add to palette → IaC → Terraform\n# Then connect it to your compute/storage nodes' });

      const hasNetwork = types.some(t => t.startsWith('aws-vpc') || t.startsWith('az-vnet') || t.startsWith('gcp-vpc') || t.includes('subnet') || t.includes('vnet') || t.includes('vpc'));
      const hasCompute = types.some(t => t.includes('compute') || t.includes('vm') || t.includes('ec2') || t.includes('eks') || t.includes('aks') || t.includes('gke'));
      if (hasCompute && !hasNetwork) {
        checks.push({ id: 'l3-net', layer: 3, label: 'Compute networking',
          status: 'warn', message: 'Compute nodes found but no VPC/subnet',
          fix_hint: 'Add a VPC or Virtual Network node and connect compute resources to it. All cloud compute must live inside a network boundary.',
          fix_snippet: '# AWS example:\nresource "aws_vpc" "main" {\n  cidr_block = "10.0.0.0/16"\n}\nresource "aws_subnet" "app" {\n  vpc_id     = aws_vpc.main.id\n  cidr_block = "10.0.1.0/24"\n}' });
      }

      const hasEncryption = labels.some(l => l.includes('encrypt') || l.includes('kms') || l.includes('cmk'));
      const hasStorage = types.some(t => t.includes('s3') || t.includes('storage') || t.includes('rds') || t.includes('db') || t.includes('disk'));
      if (hasStorage && !hasEncryption) {
        checks.push({ id: 'l3-enc', layer: 3, label: 'Storage encryption',
          status: 'warn', message: 'Storage resources found without encryption node',
          fix_hint: 'Add a KMS key node and connect storage nodes to it, or label storage nodes with "encrypted=true" to document encryption intent.',
          fix_snippet: 'resource "aws_kms_key" "data" {\n  description             = "Data encryption key"\n  deletion_window_in_days = 30\n  enable_key_rotation     = true\n}' });
      }

      const isolatedNodes = nodes.filter(n => !connectedIds.has(n.id));
      if (isolatedNodes.length > 0) {
        checks.push({ id: 'l3-iso', layer: 3, label: 'Isolated nodes',
          status: 'warn', message: `${isolatedNodes.length} node(s) have no connections`,
          fix_hint: `Connect isolated nodes: ${isolatedNodes.slice(0,3).map(n=>n.label||n.type).join(', ')}. Every IaC resource should relate to at least one other resource.`,
          fix_snippet: null });
      }

    } else if (canvasType === 'data') {
      const hasIaC = types.some(t => t.startsWith('iac-')) || labels.some(l => l.includes('terraform') || l.includes('helm'));
      checks.push({ id: 'l3-iac', layer: 3, label: 'IaC tool present',
        status: hasIaC ? 'pass' : 'warn', message: hasIaC ? 'IaC tool found in design' : 'No IaC tool node found',
        fix_hint: hasIaC ? null : 'Add a Terraform or Helm node from the IaC palette to define managed data service provisioning.',
        fix_snippet: hasIaC ? null : 'resource "aws_rds_instance" "main" {\n  engine         = "postgres"\n  engine_version = "15"\n  instance_class = "db.t3.medium"\n  storage_encrypted = true\n}' });

      const hasPII = labels.some(l => l.includes('pii') || l.includes('phi') || l.includes('cui') || l.includes('sensitive'));
      const hasMask = types.some(t => t.includes('mask') || t.includes('encrypt') || t.includes('classify')) || labels.some(l => l.includes('mask') || l.includes('dlp') || l.includes('encrypt'));
      if (hasPII && !hasMask) {
        checks.push({ id: 'l3-pii', layer: 3, label: 'Sensitive data protection',
          status: 'fail', message: 'PII/PHI/CUI data nodes have no masking or encryption control',
          fix_hint: 'Add a Data Masking or Encryption node and connect it to all PII/PHI/CUI data stores. Unprotected sensitive data is a NIST 800-53 SC-28 violation.',
          fix_snippet: '# AWS Macie for S3 PII detection\nresource "aws_macie2_account" "main" {}\nresource "aws_macie2_classification_job" "pii_scan" {\n  job_type = "SCHEDULED"\n  s3_job_definition { bucket_definitions { buckets = [aws_s3_bucket.data.id] } }\n  schedule_frequency { daily_schedule {} }\n}' });
      }

      const hasBackup = labels.some(l => l.includes('backup') || l.includes('snapshot') || l.includes('rpo') || l.includes('rto'));
      const hasDB = types.some(t => t.includes('rds') || t.includes('db') || t.includes('database') || t.includes('warehouse'));
      if (hasDB && !hasBackup) {
        checks.push({ id: 'l3-bak', layer: 3, label: 'Backup policy',
          status: 'warn', message: 'Database nodes found but no backup/snapshot node',
          fix_hint: 'Add a Backup Policy node or label database nodes with RPO/RTO values. Cloud databases should have automated backup configured.',
          fix_snippet: '# RDS automated backup\nresource "aws_db_instance" "main" {\n  backup_retention_period = 7\n  backup_window          = "03:00-04:00"\n  skip_final_snapshot    = false\n  final_snapshot_identifier = "final-backup"\n}' });
      }

    } else if (canvasType === 'observability') {
      const hasPlatform = types.some(t => t.startsWith('plt-') || t.includes('splunk') || t.includes('elastic') || t.includes('prometheus') || t.includes('grafana') || t.includes('siem'));
      const hasSource = types.some(t => t.startsWith('src-') || t.includes('log') || t.includes('metric') || t.includes('trace'));
      if (hasSource && !hasPlatform) {
        checks.push({ id: 'l3-plt', layer: 3, label: 'Collection platform',
          status: 'warn', message: 'Log/metric sources found but no collection platform',
          fix_hint: 'Add a SIEM, Prometheus, or ELK platform node and connect sources to it.',
          fix_snippet: '# Prometheus Helm chart\nresource "helm_release" "prometheus" {\n  name       = "prometheus"\n  repository = "https://prometheus-community.github.io/helm-charts"\n  chart      = "kube-prometheus-stack"\n  namespace  = "monitoring"\n}' });
      }

      const hasAlert = types.some(t => t.includes('alert') || t.includes('pagerduty') || t.includes('opsgenie') || t.includes('victorops'));
      if (hasPlatform && !hasAlert) {
        checks.push({ id: 'l3-alert', layer: 3, label: 'Alerting route',
          status: 'warn', message: 'Observability platform has no alerting destination',
          fix_hint: 'Connect the observability platform to an alerting node (PagerDuty, Alertmanager, OpsGenie). Silent monitoring is not monitoring.',
          fix_snippet: '# Alertmanager config for PagerDuty\nroute:\n  receiver: pagerduty\nreceivers:\n- name: pagerduty\n  pagerduty_configs:\n  - service_key: YOUR_PAGERDUTY_KEY' });
      }

      const hasRetention = labels.some(l => l.includes('retention') || l.includes('ttl') || l.match(/\d+d\b/) || l.match(/\d+ day/));
      if (hasPlatform && !hasRetention) {
        checks.push({ id: 'l3-ret', layer: 3, label: 'Retention policy',
          status: 'warn', message: 'No retention policy labelled on storage nodes',
          fix_hint: 'Label your log storage or SIEM nodes with a retention period (e.g., "90d"). DoD systems typically require ≥90 days hot, 1 year cold.',
          fix_snippet: '# Elasticsearch ILM policy\nresource "elasticsearch_index_lifecycle_policy" "logs" {\n  name = "logs-policy"\n  body = jsonencode({\n    policy = {\n      phases = {\n        hot   = { min_age = "0ms", actions = { rollover = { max_size = "50gb" } } }\n        delete = { min_age = "90d", actions = { delete = {} } }\n      }\n    }\n  })\n}' });
      }
    } else if (canvasType === 'migration') {
      const hasSource = types.some(t => t.includes('source') || t.includes('on-prem') || t.includes('legacy') || t.includes('current'));
      const hasTarget = types.some(t => t.includes('target') || t.includes('cloud') || t.includes('aws') || t.includes('azure') || t.includes('gcp'));
      if (!hasSource) {
        checks.push({ id: 'l3-src', layer: 3, label: 'Source environment',
          status: 'warn', message: 'No source/on-prem node found in migration design',
          fix_hint: 'Add a "Source" or "On-Premises" node representing the current environment. Migration IaC requires a source baseline.',
          fix_snippet: '# Terraform data source to import existing infra\ndata "aws_instance" "existing" {\n  instance_id = "i-0123456789abcdef0"\n}' });
      }
      if (!hasTarget) {
        checks.push({ id: 'l3-tgt', layer: 3, label: 'Target cloud environment',
          status: 'warn', message: 'No target cloud node found in migration design',
          fix_hint: 'Add a target cloud provider node (AWS, Azure, GCP) to define where workloads land. The target VPC/subscription/project drives Terraform provider config.',
          fix_snippet: 'provider "aws" {\n  region = "us-gov-west-1"  # GovCloud for IL4/IL5\n}\nresource "aws_vpc" "target" {\n  cidr_block = "10.0.0.0/16"\n}' });
      }

      const hasMigTool = types.some(t => t.includes('rehost') || t.includes('replatform') || t.includes('refactor') || t.includes('migrate')) ||
                         labels.some(l => l.includes('lift') || l.includes('shift') || l.includes('cloudendure') || l.includes('dms') || l.includes('aws-mig'));
      if (!hasMigTool) {
        checks.push({ id: 'l3-tool', layer: 3, label: 'Migration strategy',
          status: 'warn', message: 'No migration strategy (rehost/replatform/refactor) labelled',
          fix_hint: 'Label each workload node with its migration strategy: Rehost (lift-and-shift), Replatform, or Refactor. This drives IaC template selection.',
          fix_snippet: '# AWS MGN (lift-and-shift)\nresource "aws_mgn_source_server" "app" {\n  source_server_id = "s-0123456789abcdef0"\n}' });
      }

      const hasRollback = labels.some(l => l.includes('rollback') || l.includes('cutover') || l.includes('fallback') || l.includes('rto'));
      if (!hasRollback) {
        checks.push({ id: 'l3-rb', layer: 3, label: 'Rollback / cutover plan',
          status: 'warn', message: 'No rollback or cutover node found',
          fix_hint: 'Add a Rollback/Cutover node documenting the RTO and procedure. NIST SP 800-34 requires documented contingency for all migration events.',
          fix_snippet: '# Terraform cutover: re-route DNS after validation\nresource "aws_route53_record" "cutover" {\n  zone_id = data.aws_route53_zone.main.zone_id\n  name    = "app.example.gov"\n  type    = "A"\n  ttl     = 60\n  records = [aws_instance.target.public_ip]\n}' });
      }

      const hasData = types.some(t => t.includes('db') || t.includes('database') || t.includes('storage') || t.includes('s3') || t.includes('rds'));
      const hasDataMig = labels.some(l => l.includes('dms') || l.includes('snowball') || l.includes('datasync') || l.includes('data migration'));
      if (hasData && !hasDataMig) {
        checks.push({ id: 'l3-dms', layer: 3, label: 'Data migration tool',
          status: 'warn', message: 'Data stores present but no data migration tool (DMS/DataSync) found',
          fix_hint: 'Add an AWS DMS, DataSync, or Snowball node for each data store being migrated. Data migration is separate from compute migration.',
          fix_snippet: 'resource "aws_dms_replication_instance" "main" {\n  allocated_storage            = 20\n  apply_immediately            = true\n  auto_minor_version_upgrade   = true\n  replication_instance_class   = "dms.t3.micro"\n  replication_subnet_group_id  = aws_dms_replication_subnet_group.main.id\n}' });
      }
    }

    // Passes for anything not already checked
    if (!checks.some(c => c.layer === 3 && c.status !== 'pass')) {
      checks.push({ id: 'l3-ok', layer: 3, label: 'Policy checks', status: 'pass', message: 'All context-specific policy checks passed' });
    }

    return checks;
  }

  window._dcDismissed = new Set();

  window.canvasValidateIaC = function() {
    if (cfg.designId === 'new') {
      openRightPanel('Validate IaC', '<p style="color:#b7770d;">Save the design first.</p>');
      return;
    }
    showStatus('Validating IaC...');
    const graphData = collectGraph();
    const canvasType = _getCanvasType();
    const checks = _contextChecks(graphData, canvasType);

    const passed = checks.filter(c => c.status === 'pass').length;
    const failed = checks.filter(c => c.status === 'fail').length;
    const warned = checks.filter(c => c.status === 'warn').length;
    const total  = checks.length;
    const gate   = failed === 0 ? 'PASS' : 'FAIL';
    const gateColor = gate === 'PASS' ? '#1e8449' : '#c0392b';
    const pct = total > 0 ? Math.round(passed / total * 100) : 100;

    const canvasLabels = { infra: 'Infrastructure', data: 'Data', observability: 'Observability', migration: 'Migration', generic: '' };

    let html = _section(`IaC Validation — ${canvasLabels[canvasType] || ''}`);
    html += `<div style="text-align:center;margin:8px 0;">
      <span style="font-size:28px;font-weight:800;color:${gateColor};">${gate}</span>
      <div style="font-size:11px;color:#4a5568;">${passed} pass, ${failed} fail, ${warned} warn</div>
    </div>`;
    html += _bar(pct, gateColor);

    const fixable = checks.filter(c => (c.status === 'warn' || c.status === 'fail') && c.fix_hint);
    if (fixable.length > 0) {
      html += `<div style="margin:8px 0;padding:7px 10px;background:#fef9e7;border:1px solid #f39c12;border-radius:6px;display:flex;align-items:center;justify-content:space-between;">
        <span style="font-size:11px;font-weight:600;color:#7d6608;">⚠ ${fixable.length} issue(s) have suggested fixes</span>
        <button onclick="_dcAutoFixAll()" style="background:#d5f0e0;color:#1e7e34;border:1px solid #1e7e34;border-radius:4px;font-size:11px;padding:2px 8px;cursor:pointer;font-weight:600;">✦ Auto-fix All</button>
      </div>`;
    }

    const layerNames = {1:'Layer 1: Syntax', 2:'Layer 2: Schema', 3:'Layer 3: Policy'};
    [1,2,3].forEach(layer => {
      const layerChecks = checks.filter(c => c.layer === layer);
      if (!layerChecks.length) return;
      html += _section(layerNames[layer]);
      layerChecks.forEach((c, idx) => {
        if (window._dcDismissed.has(c.id)) return;
        const icons  = {pass:'✓', fail:'✗', warn:'⚠', skip:'→'};
        const colors = {pass:'#1e8449', fail:'#c0392b', warn:'#b7770d', skip:'#4a5568'};
        const bgs    = {pass:'#f0fff4', fail:'#fff5f5', warn:'#fffbf0', skip:'#f8f8f8'};
        const needsAction = c.status === 'warn' || c.status === 'fail';
        const rid = `dc-vr-${layer}-${idx}`;

        html += `<div id="${rid}" style="margin:3px 0;padding:6px 8px;border-left:3px solid ${colors[c.status]};background:${bgs[c.status]};border-radius:0 4px 4px 0;">`;
        html += `<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:4px;">`;
        html += `<div style="flex:1;min-width:0;">
          <span style="color:${colors[c.status]};font-size:13px;margin-right:4px;">${icons[c.status]}</span>
          <b style="font-size:11px;color:#1a1a2e;">${c.label}</b>
          <div style="font-size:10px;color:#4a5568;margin-top:2px;">${c.message}</div>
          ${c.detail ? `<div style="font-size:9px;color:#4a5568;margin-left:12px;">— ${c.detail}</div>` : ''}
        </div>`;
        if (needsAction) {
          html += `<div style="display:flex;flex-direction:column;gap:3px;flex-shrink:0;">`;
          if (c.fix_hint) html += `<button style="background:#dce8fb;color:#1a5276;border:1px solid #aed6f1;border-radius:3px;font-size:10px;padding:2px 7px;cursor:pointer;" onclick="_dcToggleFix('${rid}')">Fix →</button>`;
          html += `<button style="background:transparent;color:#4a5568;border:1px solid #c9d3e0;border-radius:3px;font-size:10px;padding:2px 7px;cursor:pointer;" onclick="_dcDismiss('${rid}')">Dismiss</button>`;
          html += `</div>`;
        }
        html += `</div>`;

        if (c.fix_hint) {
          const safeSnip = (c.fix_snippet||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
          html += `<div id="${rid}-fix" style="display:none;margin-top:6px;padding:8px;background:#eaf3fb;border-radius:4px;border:1px solid #aed6f1;">
            <div style="font-size:10px;font-weight:700;color:#1a5276;margin-bottom:4px;">💡 Suggested Fix</div>
            <div style="font-size:10px;color:#2c3e50;margin-bottom:6px;">${c.fix_hint}</div>`;
          if (c.fix_snippet) {
            html += `<pre style="font-size:9px;background:#d6eaf8;padding:6px;border-radius:3px;white-space:pre-wrap;color:#1a1a2e;margin:0 0 6px;font-family:monospace;">${safeSnip}</pre>`;
            html += `<button style="background:#1a5276;color:#fff;border:none;border-radius:3px;font-size:10px;padding:2px 8px;cursor:pointer;" onclick="_dcCopyFix(this,'${rid}')">Copy Snippet</button>`;
          }
          html += `</div>`;
        }
        html += `</div>`;
      });
    });

    html += `<p style="font-size:10px;color:#4a5568;margin-top:8px;">Validation runs client-side (air-gap safe). Checks are context-aware for ${canvasLabels[canvasType]||'this'} designs.</p>`;
    window._dcLastChecks = checks;
    openRightPanel('IaC Validation', html);
    showStatus(gate === 'PASS' ? '✓ IaC validation passed' : '✗ IaC validation failed — see panel');
  };

  window._dcToggleFix = function(rid) {
    const el = document.getElementById(rid + '-fix');
    if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
  };
  window._dcDismiss = function(rid) {
    const el = document.getElementById(rid);
    if (el) { el.style.opacity = '0.4'; el.style.pointerEvents = 'none'; }
  };
  window._dcCopyFix = function(btn, rid) {
    const pre = document.getElementById(rid + '-fix')?.querySelector('pre');
    if (!pre) return;
    navigator.clipboard.writeText(pre.textContent).then(() => {
      const orig = btn.textContent;
      btn.textContent = '✓ Copied!';
      setTimeout(() => { btn.textContent = orig; }, 1500);
    }).catch(() => {
      const ta = document.createElement('textarea');
      ta.value = pre.textContent;
      document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
      btn.textContent = '✓ Copied!'; setTimeout(() => { btn.textContent = 'Copy Snippet'; }, 1500);
    });
  };
  window._dcAutoFixAll = function() {
    const checks = window._dcLastChecks || [];
    const fixable = checks.filter(c => (c.status === 'warn' || c.status === 'fail') && c.fix_hint);
    fixable.forEach((c, i) => {
      const rid = `dc-vr-${c.layer}-${checks.filter(x=>x.layer===c.layer).indexOf(c)}`;
      window._dcDismissed.add(c.id);
    });
    showStatus(`Acknowledged ${fixable.length} fix(es) — review snippets above and apply manually, then re-validate.`);
    // Re-render with dismissed items hidden
    window.canvasValidateIaC();
  };

  window.canvasDeployIaC = function() {
    if (cfg.designId === 'new') {
      openRightPanel('Deploy IaC', '<p style="color:#f39c12;">Save the design first.</p>');
      return;
    }

    const graphData = collectGraph();
    const iacNodes = graphData.nodes.filter(n =>
      (n.type || '').startsWith('iac-') || (n.label || '').toLowerCase().includes('terraform') ||
      (n.label || '').toLowerCase().includes('helm') || (n.label || '').toLowerCase().includes('ansible')
    );

    if (iacNodes.length === 0) {
      openRightPanel('Deploy IaC', '<div style="text-align:center;padding:16px;"><div style="font-size:24px;color:#f39c12;">⚠</div><div style="color:#f39c12;font-weight:600;">No IaC Tools Found</div><div style="color:#7a8cb0;font-size:11px;margin-top:4px;">Add IaC tools to the design first.</div></div>');
      return;
    }

    // Generate deployment manifest
    const manifest = {
      design_id: cfg.designId,
      generated_at: new Date().toISOString(),
      iac_tools: iacNodes.map(n => ({ type: n.type, label: n.label })),
      resources: graphData.nodes.filter(n => !(n.type || '').startsWith('iac-')).map(n => ({
        id: n.id, type: n.type, label: n.label,
      })),
      connections: graphData.edges.length,
      total_resources: graphData.nodes.length - iacNodes.length,
    };

    let html = '';
    html += `<div style="text-align:center;padding:12px 0;">`;
    html += `<div style="font-size:24px;color:#27ae60;">📦</div>`;
    html += `<div style="color:#27ae60;font-weight:600;font-size:14px;">Deployment Bundle Ready</div>`;
    html += `</div>`;

    html += _metric('IaC Tools', manifest.iac_tools.length);
    html += _metric('Resources', manifest.total_resources);
    html += _metric('Connections', manifest.connections);

    html += _section('Generated Files');
    const files = [];
    manifest.iac_tools.forEach(t => {
      const tl = t.label.toLowerCase();
      if (tl.includes('terraform') || t.type === 'iac-terraform' || t.type === 'iac-opentofu') {
        files.push({ name: 'main.tf', desc: 'Terraform root module' });
        files.push({ name: 'variables.tf', desc: 'Input variables' });
        files.push({ name: 'outputs.tf', desc: 'Output values' });
        files.push({ name: 'providers.tf', desc: 'Provider configuration' });
        files.push({ name: 'backend.tf', desc: 'State backend (S3/GCS/Azure)' });
      }
      if (tl.includes('helm') || t.type === 'iac-helm') {
        files.push({ name: 'values.yaml', desc: 'Helm chart values' });
        files.push({ name: 'Chart.yaml', desc: 'Chart metadata' });
      }
      if (tl.includes('ansible') || t.type === 'iac-ansible') {
        files.push({ name: 'playbook.yml', desc: 'Ansible playbook' });
        files.push({ name: 'inventory.ini', desc: 'Host inventory' });
      }
      if (tl.includes('argocd') || t.type === 'iac-argocd') {
        files.push({ name: 'application.yaml', desc: 'ArgoCD Application manifest' });
      }
      if (tl.includes('crossplane') || t.type === 'iac-crossplane') {
        files.push({ name: 'composition.yaml', desc: 'Crossplane Composition' });
        files.push({ name: 'claim.yaml', desc: 'Crossplane Claim' });
      }
    });
    if (files.length === 0) {
      files.push({ name: 'deploy.sh', desc: 'Deployment script' });
    }
    files.push({ name: 'manifest.json', desc: 'Resource manifest' });
    files.push({ name: 'README.md', desc: 'Deployment instructions' });

    files.forEach(f => {
      html += `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);">`;
      html += `<span style="color:#eaeaea;font-size:11px;font-family:monospace;">${f.name}</span>`;
      html += `<span style="color:#5a6e8c;font-size:10px;">${f.desc}</span></div>`;
    });

    html += `<div style="margin-top:12px;">`;
    html += `<button onclick="canvasExport('json')" style="background:#27ae60;border:none;color:#fff;padding:6px 16px;border-radius:4px;cursor:pointer;font-size:12px;width:100%;">Download Bundle (JSON)</button>`;
    html += `</div>`;

    openRightPanel('Deploy IaC Bundle', html);
    showStatus('✓ IaC bundle generated');
  };

  // ── Node / Edge Properties Panel ──────────────────────────────────────
  const CATEGORY_LABELS = {
    'ent': 'Entity / Store', 'col': 'Column / Field', 'flow': 'Data Flow',
    'ctrl': 'Control / Policy', 'bnd': 'Boundary / Zone', 'twin': 'Digital Twin',
    'aws': 'AWS Service', 'az': 'Azure Service', 'gcp': 'GCP Service',
    'oci': 'OCI Service', 'ibm': 'IBM Cloud', 'op': 'On-Premises', 'iac': 'IaC Tool',
    'sys': 'System', 'isa': 'Interconnection (ISA)', 'doc': 'Documentation',
    'src': 'Log / Telemetry Source', 'plt': 'Platform', 'auto': 'Automation', 'cmp': 'Compliance',
  };

  const ENTITY_INFO = {
    'ent-table':        { icon: '📋', name: 'Relational Table',   notes: 'SQL table with primary key' },
    'ent-view':         { icon: '👁',  name: 'Database View',      notes: 'Virtual table from query' },
    'ent-schema':       { icon: '🗂',  name: 'Schema',             notes: 'Namespace grouping tables' },
    'ent-collection':   { icon: '📦', name: 'NoSQL Collection',   notes: 'Document store collection' },
    'ent-stream':       { icon: '🌊', name: 'Event Stream',       notes: 'Kafka topic / message stream' },
    'ent-bucket':       { icon: '🪣', name: 'Object Store',       notes: 'S3-compatible blob storage' },
    'ent-queue':        { icon: '📨', name: 'Message Queue',      notes: 'FIFO message queue' },
    'ent-cache':        { icon: '⚡', name: 'Cache Store',        notes: 'Redis / Memcached' },
    'ent-graph':        { icon: '🕸',  name: 'Graph Database',     notes: 'Property graph (node/edge)' },
    'ent-warehouse':    { icon: '🏭', name: 'Data Warehouse',     notes: 'Analytical OLAP store' },
    'ent-lakehouse':    { icon: '🏞',  name: 'Lakehouse',          notes: 'Delta / Iceberg open table format' },
    'ent-feature-store':{ icon: '🧪', name: 'Feature Store',      notes: 'ML feature online/offline store' },
    'ent-model-registry':{ icon: '🤖', name: 'Model Registry',    notes: 'ML model artifact versioning' },
    'ent-dataset':      { icon: '📊', name: 'Training Dataset',   notes: 'Labeled ML training data' },
    'ent-experiment':   { icon: '🔬', name: 'Experiment Run',     notes: 'ML experiment tracking' },
    'ent-ml-pipeline':  { icon: '🔁', name: 'ML Pipeline',        notes: 'Orchestrated ML workflow' },
    'ent-data-product': { icon: '📦', name: 'Data Product',       notes: 'Data mesh product with ports' },
    'ent-domain':       { icon: '🏛',  name: 'Data Domain',        notes: 'Organizational data domain' },
    'ent-contract':     { icon: '📜', name: 'Data Contract',      notes: 'ODCS / bitol-io schema contract' },
    'ent-input-port':   { icon: '↙',  name: 'Input Port',         notes: 'Data product input interface' },
    'ent-output-port':  { icon: '↗',  name: 'Output Port',        notes: 'Data product output interface' },
  };

  const FLOW_INFO = {
    'flow-etl':         { icon: '🔄', name: 'ETL Pipeline',        notes: 'Extract, Transform, Load batch job' },
    'flow-api':         { icon: '🔗', name: 'REST / GraphQL API',  notes: 'Synchronous API call' },
    'flow-cdc':         { icon: '📡', name: 'Change Data Capture', notes: 'Real-time event capture (Debezium)' },
    'flow-stream':      { icon: '🌊', name: 'Stream Processing',   notes: 'Kafka Streams / Flink pipeline' },
    'flow-replication': { icon: '🪞', name: 'DB Replication',      notes: 'Logical / physical replication' },
    'flow-sync':        { icon: '🔁', name: 'Data Sync',           notes: 'Bidirectional data synchronization' },
    'flow-webhook':     { icon: '🪝', name: 'Webhook',             notes: 'Push-based event notification' },
    'flow-export':      { icon: '📤', name: 'Export / Bulk Load',  notes: 'Bulk data export job' },
  };

  function showNodeProperties(cellView) {
    const cell = cellView.model;
    if (!cell.isElement()) return;
    selectedCell = cell;

    const nodeType  = cell.get('nodeType') || '';
    const nodeLabel = cell.get('nodeLabel') || cell.id;
    const pos       = cell.position();
    const size      = cell.size();
    const colors    = getNodeColor(nodeType);
    const desc      = (window.NODE_DESCS || {})[nodeType] || '';
    const prefix    = nodeType.split('-')[0] || 'unknown';
    const catLabel  = CATEGORY_LABELS[prefix] || prefix.toUpperCase();

    const allLinks  = graph.getConnectedLinks(cell);
    const incoming  = allLinks.filter(l => l.get('target') && l.get('target').id === cell.id);
    const outgoing  = allLinks.filter(l => l.get('source') && l.get('source').id === cell.id);

    let html = '';

    // Color-coded header card
    html += `<div style="background:${colors.fill};border:2px solid ${colors.stroke};border-radius:8px;padding:10px 12px;margin-bottom:10px;">`;
    html += `<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">`;
    html += `<div style="width:10px;height:10px;border-radius:50%;background:${colors.stroke};flex-shrink:0;"></div>`;
    html += `<div style="font-size:13px;font-weight:700;color:#eaeaea;">${nodeLabel}</div>`;
    html += `</div>`;
    html += `<div style="font-size:10px;color:${colors.stroke};font-family:monospace;">${nodeType}</div>`;
    html += `<div style="font-size:10px;color:#5a6e8c;margin-top:2px;">${catLabel}</div>`;
    html += `</div>`;

    // Description
    if (desc) {
      html += `<div style="background:#16213e;border-left:3px solid ${colors.stroke};border-radius:0 6px 6px 0;padding:8px 10px;margin-bottom:10px;font-size:11px;color:#8ea8c3;line-height:1.5;">${desc}</div>`;
    }

    // Identity
    html += _section('Identity');
    html += _metric('Label', nodeLabel);
    html += _metric('Type', `<span style="font-family:monospace;font-size:10px;color:#ff9800;">${nodeType}</span>`);
    html += _metric('Category', catLabel);
    html += _metric('ID', `<span style="font-family:monospace;font-size:9px;color:#5a6e8c;">${cell.id}</span>`);

    // Geometry
    html += _section('Geometry');
    html += _metric('Position', `(${Math.round(pos.x)}, ${Math.round(pos.y)})`);
    if (size.width !== 130 || size.height !== 50) {
      html += _metric('Size', `${Math.round(size.width)} × ${Math.round(size.height)}`);
    }

    // Connections
    html += _section(`Connections (${allLinks.length})`);
    if (allLinks.length === 0) {
      html += `<div style="color:#5a6e8c;font-size:11px;font-style:italic;">No connections yet.</div>`;
    } else {
      html += _metric('Incoming', incoming.length);
      html += _metric('Outgoing', outgoing.length);
      if (incoming.length) {
        html += `<div style="margin-top:5px;"><div style="font-size:10px;color:#3498db;font-weight:600;margin-bottom:3px;">▲ From</div>`;
        incoming.forEach(l => {
          const src = graph.getCell(l.get('source')?.id);
          const lbl = src ? (src.get('nodeLabel') || src.id) : '?';
          const el  = (l.labels()?.[0]?.attrs?.text?.text) || '';
          html += `<div style="padding:2px 8px;background:#0f1e36;border-radius:3px;font-size:10px;color:#3498db;margin-bottom:2px;display:flex;justify-content:space-between;"><span>← ${lbl}</span>${el ? `<span style="color:#5a6e8c;font-style:italic;">${el}</span>` : ''}</div>`;
        });
        html += `</div>`;
      }
      if (outgoing.length) {
        html += `<div style="margin-top:5px;"><div style="font-size:10px;color:#27ae60;font-weight:600;margin-bottom:3px;">▼ To</div>`;
        outgoing.forEach(l => {
          const tgt = graph.getCell(l.get('target')?.id);
          const lbl = tgt ? (tgt.get('nodeLabel') || tgt.id) : '?';
          const el  = (l.labels()?.[0]?.attrs?.text?.text) || '';
          html += `<div style="padding:2px 8px;background:#0f1e36;border-radius:3px;font-size:10px;color:#27ae60;margin-bottom:2px;display:flex;justify-content:space-between;"><span>→ ${lbl}</span>${el ? `<span style="color:#5a6e8c;font-style:italic;">${el}</span>` : ''}</div>`;
        });
        html += `</div>`;
      }
    }

    // Type-specific detail card
    const einfo = ENTITY_INFO[nodeType];
    const finfo = FLOW_INFO[nodeType];
    if (einfo || finfo) {
      const info = einfo || finfo;
      html += _section('Type Detail');
      html += `<div style="display:flex;align-items:center;gap:10px;padding:8px;background:#16213e;border-radius:6px;">`;
      html += `<div style="font-size:22px;">${info.icon}</div>`;
      html += `<div><div style="font-size:12px;font-weight:600;color:#eaeaea;">${info.name}</div><div style="font-size:10px;color:#5a6e8c;margin-top:2px;">${info.notes}</div></div>`;
      html += `</div>`;
    }

    // Compliance hints by prefix
    if (prefix === 'ctrl') {
      html += _section('Control Guidance');
      html += `<div style="font-size:10px;color:#7a8cb0;line-height:1.5;">Controls enforce access, encryption, and audit policies. Connect to entity nodes they protect.</div>`;
    }
    if (prefix === 'bnd') {
      html += _section('Boundary Guidance');
      html += `<div style="font-size:10px;color:#7a8cb0;line-height:1.5;">Boundaries define trust zones. Drag to resize. All nodes inside share this classification scope.</div>`;
    }

    // Actions
    html += _section('Actions');
    html += `<div style="display:flex;flex-direction:column;gap:5px;margin-top:4px;">`;
    html += `<button onclick="canvasRenameSelected()" style="background:#1e3a6e;border:1px solid #2a4070;color:#eaeaea;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:11px;text-align:left;">✏️ Rename</button>`;
    html += `<button onclick="canvasDuplicateSelected()" style="background:#1e3a6e;border:1px solid #2a4070;color:#eaeaea;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:11px;text-align:left;">📋 Duplicate</button>`;
    html += `<button onclick="canvasDeleteSelected();closeRightPanel();" style="background:#2b1a1a;border:1px solid #c0392b44;color:#e74c3c;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:11px;text-align:left;">🗑 Delete</button>`;
    html += `</div>`;

    openRightPanel('Properties', html);
  }

  function showLinkProperties(linkView) {
    const cell = linkView.model;
    selectedCell = cell;

    const src  = graph.getCell(cell.get('source')?.id);
    const tgt  = graph.getCell(cell.get('target')?.id);
    const srcLabel = src ? (src.get('nodeLabel') || src.id) : '(unconnected)';
    const tgtLabel = tgt ? (tgt.get('nodeLabel') || tgt.id) : '(unconnected)';
    const edgeLabel = (cell.labels()?.[0]?.attrs?.text?.text) || '';

    let html = '';
    html += `<div style="background:#16213e;border:2px solid #5a6e8c;border-radius:8px;padding:10px 12px;margin-bottom:10px;">`;
    html += `<div style="font-size:13px;font-weight:700;color:#eaeaea;">Edge</div>`;
    html += `<div style="font-size:10px;color:#5a6e8c;margin-top:2px;font-family:monospace;">${cell.id}</div>`;
    html += `</div>`;

    html += _section('Connection');
    html += _metric('From', `<span style="color:#3498db;">${srcLabel}</span>`);
    html += _metric('To',   `<span style="color:#27ae60;">${tgtLabel}</span>`);
    if (edgeLabel) html += _metric('Label', edgeLabel);

    html += _section('Actions');
    html += `<div style="display:flex;flex-direction:column;gap:5px;margin-top:4px;">`;
    html += `<button onclick="canvasRelabelEdge()" style="background:#1e3a6e;border:1px solid #2a4070;color:#eaeaea;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:11px;text-align:left;">✏️ Add / Edit Label</button>`;
    html += `<button onclick="canvasDeleteSelected();closeRightPanel();" style="background:#2b1a1a;border:1px solid #c0392b44;color:#e74c3c;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:11px;text-align:left;">🗑 Delete Edge</button>`;
    html += `</div>`;

    openRightPanel('Edge Properties', html);
  }

  window.canvasRenameSelected = function() {
    if (!selectedCell || !selectedCell.isElement()) return;
    const current = selectedCell.get('nodeLabel') || '';
    const newLabel = prompt('Rename node:', current);
    if (newLabel !== null && newLabel.trim()) {
      selectedCell.attr('label/text', newLabel.trim());
      selectedCell.set('nodeLabel', newLabel.trim());
      isDirty = true;
      const cv = paper.findViewByModel(selectedCell);
      if (cv) showNodeProperties(cv);
    }
  };

  window.canvasDuplicateSelected = function() {
    if (!selectedCell || !selectedCell.isElement()) return;
    const pos = selectedCell.position();
    const newId = 'n-' + Math.random().toString(36).substr(2, 8);
    const clone = createNode({
      id: newId,
      type: selectedCell.get('nodeType'),
      label: (selectedCell.get('nodeLabel') || '') + ' (copy)',
      x: pos.x + 20,
      y: pos.y + 20,
    });
    graph.addCell(clone);
    isDirty = true;
  };

  window.canvasRelabelEdge = function() {
    if (!selectedCell || !selectedCell.isLink()) return;
    const current = (selectedCell.labels()?.[0]?.attrs?.text?.text) || '';
    const newLabel = prompt('Edge label (blank to remove):', current);
    if (newLabel === null) return;
    if (newLabel.trim()) {
      selectedCell.labels([{ position: 0.5, attrs: { text: { text: newLabel.trim(), fill: '#7a8cb0', fontSize: 9 }, rect: { fill: '#0d1b2a', stroke: 'none' } } }]);
    } else {
      selectedCell.labels([]);
    }
    isDirty = true;
    const lv = paper.findViewByModel(selectedCell);
    if (lv) showLinkProperties(lv);
  };

  // ── PII Lineage Panel ──────────────────────────────────────────────────
  window.canvasPiiLineage = function() {
    if (cfg.designId === 'new') {
      openRightPanel('PII Lineage', '<p style="color:#f39c12;">Save the design first.</p>');
      return;
    }
    openRightPanel('PII Lineage', '<p style="color:#7a8cb0;font-size:11px;">Loading lineage graph…</p>');
    fetch(cfg.apiBase + '/lineage/' + cfg.designId)
      .then(r => r.json())
      .then(data => {
        const nodes = data.nodes || [];
        const edges = data.edges || [];

        // Summary counts
        const piiCount = nodes.filter(n => n.pii_marker === 'pii').length;
        const sensCount = nodes.filter(n => n.pii_marker === 'sensitive').length;
        const cleanCount = nodes.filter(n => n.pii_marker === 'clean').length;

        let html = '';

        // Legend + counts
        html += '<div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;">';
        html += `<span style="background:#e74c3c22;border:1px solid #e74c3c;border-radius:4px;padding:3px 8px;font-size:10px;color:#e74c3c;">&#9679; PII &nbsp;${piiCount}</span>`;
        html += `<span style="background:#f39c1222;border:1px solid #f39c12;border-radius:4px;padding:3px 8px;font-size:10px;color:#f39c12;">&#9679; Sensitive &nbsp;${sensCount}</span>`;
        html += `<span style="background:#27ae6022;border:1px solid #27ae60;border-radius:4px;padding:3px 8px;font-size:10px;color:#27ae60;">&#9679; Clean &nbsp;${cleanCount}</span>`;
        html += '</div>';

        if (nodes.length === 0) {
          html += '<div style="text-align:center;padding:20px;"><div style="font-size:24px;color:#27ae60;">&#10003;</div>';
          html += '<div style="color:#27ae60;font-weight:600;">No lineage edges recorded</div>';
          html += '<div style="color:#7a8cb0;font-size:11px;margin-top:4px;">Add lineage edges from the Lineage page.</div></div>';
          openRightPanel('PII Lineage', html);
          return;
        }

        // Build SVG lineage graph (horizontal left→right layout)
        const NODE_W = 110, NODE_H = 32, PAD_X = 140, PAD_Y = 48;
        const nodeIndex = {};
        nodes.forEach((n, i) => { nodeIndex[n.id] = i; });

        // Simple column layout: bucket nodes by how many incoming edges (level = 0 if no incoming)
        const inDeg = {};
        nodes.forEach(n => { inDeg[n.id] = 0; });
        edges.forEach(e => { if (e.target in inDeg) inDeg[e.target]++; });
        const levels = {};
        // BFS-style level assignment
        const visited = new Set();
        const queue = nodes.filter(n => inDeg[n.id] === 0).map(n => ({ id: n.id, lvl: 0 }));
        if (queue.length === 0) { nodes.forEach(n => queue.push({ id: n.id, lvl: 0 })); }
        while (queue.length > 0) {
          const { id, lvl } = queue.shift();
          if (visited.has(id)) continue;
          visited.add(id);
          if (levels[id] === undefined || levels[id] < lvl) levels[id] = lvl;
          edges.filter(e => e.source === id).forEach(e => {
            if (!visited.has(e.target)) queue.push({ id: e.target, lvl: lvl + 1 });
          });
        }
        nodes.forEach(n => { if (levels[n.id] === undefined) levels[n.id] = 0; });

        // Group nodes by level
        const byLevel = {};
        nodes.forEach(n => {
          const l = levels[n.id] || 0;
          (byLevel[l] = byLevel[l] || []).push(n);
        });
        const maxLevel = Math.max(...Object.keys(byLevel).map(Number));
        const svgW = (maxLevel + 1) * PAD_X + NODE_W + 20;
        const maxPerLevel = Math.max(...Object.values(byLevel).map(a => a.length));
        const svgH = maxPerLevel * PAD_Y + 20;

        // Compute node screen positions
        const pos = {};
        Object.entries(byLevel).forEach(([lvl, nds]) => {
          const x = Number(lvl) * PAD_X + 10;
          nds.forEach((n, i) => {
            const y = i * PAD_Y + 10;
            pos[n.id] = { x, y };
          });
        });

        let svgEdges = '';
        edges.forEach(e => {
          const sp = pos[e.source], tp = pos[e.target];
          if (!sp || !tp) return;
          const x1 = sp.x + NODE_W, y1 = sp.y + NODE_H / 2;
          const x2 = tp.x, y2 = tp.y + NODE_H / 2;
          const mx = (x1 + x2) / 2;
          svgEdges += `<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" fill="none" stroke="#2a4070" stroke-width="1.5" marker-end="url(#arrow)"/>`;
          if (e.column_name) {
            const lx = (x1 + x2) / 2, ly = (y1 + y2) / 2 - 4;
            svgEdges += `<text x="${lx}" y="${ly}" text-anchor="middle" fill="#5a6e8c" font-size="8">${e.column_name.substring(0,16)}</text>`;
          }
        });

        let svgNodes = '';
        nodes.forEach(n => {
          const p = pos[n.id];
          if (!p) return;
          const label = (n.label || n.id).substring(0, 14);
          svgNodes += `<rect x="${p.x}" y="${p.y}" width="${NODE_W}" height="${NODE_H}" rx="5" fill="#16213e" stroke="${n.pii_color}" stroke-width="2"/>`;
          svgNodes += `<circle cx="${p.x + 10}" cy="${p.y + NODE_H/2}" r="4" fill="${n.pii_color}"/>`;
          svgNodes += `<text x="${p.x + 20}" y="${p.y + NODE_H/2 + 4}" fill="#eaeaea" font-size="9">${label}</text>`;
        });

        html += `<div style="overflow:auto;max-height:320px;background:#0a1628;border-radius:6px;padding:4px;">
<svg width="${svgW}" height="${svgH}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arrow" markerWidth="6" markerHeight="6" refX="6" refY="3" orient="auto">
      <path d="M0,0 L6,3 L0,6 Z" fill="#2a4070"/>
    </marker>
  </defs>
  ${svgEdges}${svgNodes}
</svg></div>`;

        // Node list with PII markers
        html += _section('Nodes');
        nodes.forEach(n => {
          const markerLabel = n.pii_marker === 'pii' ? 'PII' : n.pii_marker === 'sensitive' ? 'Sensitive' : 'Clean';
          html += `<div style="display:flex;align-items:center;gap:6px;padding:5px 0;border-bottom:1px solid #0f1e36;">`;
          html += `<div style="width:8px;height:8px;border-radius:50%;background:${n.pii_color};flex-shrink:0;"></div>`;
          html += `<div style="flex:1;font-size:11px;color:#eaeaea;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${n.label || n.id}</div>`;
          html += `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:${n.pii_color}22;color:${n.pii_color};border:1px solid ${n.pii_color};">${markerLabel}</span>`;
          html += `</div>`;
        });

        openRightPanel('PII Lineage', html);
      })
      .catch(e => openRightPanel('PII Lineage', `<p style="color:#e74c3c;">Failed: ${e}</p>`));
  };
});

/* ── Shared Collaboration Widget (Task 18) ─────────────────────────────────
 *
 * Polling-based real-time collaboration for all design canvases.
 * No WebSocket dependency — air-gapped/CUI compatible.
 *
 * Usage (in canvas page):
 *   canvas.collab.init({ designId, apiBase, userId, userName });
 *   canvas.collab.destroy();
 *
 * Emits DOM events on document:
 *   canvas:collab:op     — { detail: { op_type, data, user_id } }
 *   canvas:collab:join   — { detail: { user_id, name, color } }
 *   canvas:collab:leave  — { detail: { user_id } }
 */
(function () {
  'use strict';

  const POLL_INTERVAL_MS = 2000;   // poll every 2 s
  const CURSOR_DEBOUNCE_MS = 300;  // throttle cursor updates
  const COLORS = [
    '#e74c3c','#3498db','#27ae60','#f39c12',
    '#9b59b6','#1abc9c','#e67e22','#2c3e50'
  ];

  let _state = null; // active collab state

  /* ── Public API ──────────────────────────────────────────────────────── */
  window.canvas = window.canvas || {};
  window.canvas.collab = {
    /**
     * Start collaborative editing for a design.
     * @param {object} opts - { designId, apiBase, userId, userName }
     */
    init(opts) {
      if (_state) this.destroy();
      const userId = opts.userId || _randomId();
      _state = {
        designId: opts.designId,
        apiBase: opts.apiBase,
        userId,
        userName: opts.userName || `User-${userId.slice(0, 6)}`,
        myColor: COLORS[Math.floor(Math.random() * COLORS.length)],
        latestSeq: 0,
        participants: {},
        pollTimer: null,
        cursorTimer: null,
        pendingCursor: null,
      };
      _join(_state);
    },

    /** Stop collaborative editing and clean up. */
    destroy() {
      if (!_state) return;
      _leave(_state);
      clearInterval(_state.pollTimer);
      clearTimeout(_state.cursorTimer);
      _removeBar();
      _state = null;
    },

    /**
     * Push an operation (call after local graph changes).
     * @param {string} opType - node_add | node_move | node_delete | edge_add | edge_delete
     * @param {object} data   - operation payload
     */
    push(opType, data) {
      if (!_state) return;
      _push(_state, opType, data);
    },

    /** Update cursor position (call on paper:mousemove). */
    moveCursor(x, y) {
      if (!_state) return;
      _state.pendingCursor = { x, y };
      clearTimeout(_state.cursorTimer);
      _state.cursorTimer = setTimeout(() => {
        if (_state && _state.pendingCursor) {
          const { x: cx, y: cy } = _state.pendingCursor;
          const base = _state.apiBase;
          const did = _state.designId;
          fetch(`${base}/collab/${did}/poll?since=${_state.latestSeq}` +
            `&user_id=${encodeURIComponent(_state.userId)}&cx=${cx}&cy=${cy}`)
            .catch(() => {});
          _state.pendingCursor = null;
        }
      }, CURSOR_DEBOUNCE_MS);
    },

    /** Return current participants map (user_id → info). */
    participants() {
      return _state ? { ..._state.participants } : {};
    },
  };

  /* ── Internal ────────────────────────────────────────────────────────── */
  function _randomId() {
    return Math.random().toString(36).slice(2, 10);
  }

  function _join(s) {
    fetch(`${s.apiBase}/collab/${s.designId}/join`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: s.userId, user_name: s.userName }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.color) s.myColor = data.color;
        if (data.participants) _syncParticipants(s, data.participants);
        _renderBar(s);
        s.pollTimer = setInterval(() => _poll(s), POLL_INTERVAL_MS);
      })
      .catch(err => console.warn('[collab] join failed:', err));
  }

  function _leave(s) {
    fetch(`${s.apiBase}/collab/${s.designId}/leave`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: s.userId }),
    }).catch(() => {});
  }

  function _push(s, opType, data) {
    fetch(`${s.apiBase}/collab/${s.designId}/push`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: s.userId, op_type: opType, data }),
    })
      .then(r => r.json())
      .then(res => { if (res.seq) s.latestSeq = Math.max(s.latestSeq, res.seq); })
      .catch(() => {});
  }

  function _poll(s) {
    fetch(`${s.apiBase}/collab/${s.designId}/poll?since=${s.latestSeq}` +
      `&user_id=${encodeURIComponent(s.userId)}`)
      .then(r => r.json())
      .then(data => {
        if (!_state) return;
        if (data.latest_seq !== undefined) s.latestSeq = data.latest_seq;
        if (data.participants) _syncParticipants(s, data.participants);
        (data.operations || []).forEach(op => {
          if (op.user_id === s.userId) return; // skip own ops
          document.dispatchEvent(new CustomEvent('canvas:collab:op', { detail: op }));
        });
        _updateBar(s);
      })
      .catch(() => {});
  }

  function _syncParticipants(s, list) {
    const prev = new Set(Object.keys(s.participants));
    const next = new Set();
    list.forEach(p => {
      next.add(p.user_id);
      if (!prev.has(p.user_id) && p.user_id !== s.userId) {
        document.dispatchEvent(new CustomEvent('canvas:collab:join', { detail: p }));
      }
      s.participants[p.user_id] = p;
    });
    prev.forEach(id => {
      if (!next.has(id)) {
        document.dispatchEvent(new CustomEvent('canvas:collab:leave', { detail: { user_id: id } }));
        delete s.participants[id];
      }
    });
  }

  /* ── Collaboration Bar ───────────────────────────────────────────────── */
  const BAR_ID = 'canvas-collab-bar';

  function _renderBar(s) {
    _removeBar();
    const bar = document.createElement('div');
    bar.id = BAR_ID;
    bar.style.cssText = [
      'position:fixed;top:56px;right:12px;z-index:9000',
      'background:rgba(18,26,38,0.96)',
      'border:1px solid rgba(255,255,255,0.08)',
      'border-radius:8px;padding:6px 10px',
      'display:flex;align-items:center;gap:8px',
      'font-size:11px;color:#8ea8c3',
      'box-shadow:0 2px 12px rgba(0,0,0,0.4)',
      'pointer-events:none',
    ].join(';');
    bar.innerHTML = `
      <span style="font-weight:600;color:#1abc9c;">● LIVE</span>
      <span id="${BAR_ID}-avatars" style="display:flex;gap:4px;"></span>
      <span id="${BAR_ID}-count"></span>
    `;
    document.body.appendChild(bar);
    _updateBar(s);
  }

  function _updateBar(s) {
    const bar = document.getElementById(BAR_ID);
    if (!bar) return;
    const avatarEl = bar.querySelector(`#${BAR_ID}-avatars`);
    const countEl  = bar.querySelector(`#${BAR_ID}-count`);
    const others = Object.values(s.participants).filter(p => p.user_id !== s.userId);
    const total = others.length + 1; // include self
    if (avatarEl) {
      avatarEl.innerHTML = others.slice(0, 5).map(p => {
        const initials = (p.name || '?').slice(0, 2).toUpperCase();
        const color = p.color || '#3498db';
        return `<span title="${p.name}" style="
          display:inline-flex;align-items:center;justify-content:center;
          width:22px;height:22px;border-radius:50%;
          background:${color};color:#fff;font-size:9px;font-weight:700;
          border:1.5px solid rgba(255,255,255,0.12);
        ">${initials}</span>`;
      }).join('');
    }
    if (countEl) {
      countEl.textContent = total === 1 ? 'Only you' : `${total} collaborators`;
    }
  }

  function _removeBar() {
    const el = document.getElementById(BAR_ID);
    if (el) el.remove();
  }
})();
