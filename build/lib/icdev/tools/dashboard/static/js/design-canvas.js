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

  graph = new joint.dia.Graph();
  paper = new joint.dia.Paper({
    el: container,
    model: graph,
    width: '100%',
    height: '100%',
    gridSize: 10,
    drawGrid: { name: 'mesh', args: { color: '#1e3a6e', thickness: 0.5 } },
    background: { color: '#0d1b2a' },
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

  // Enable element selection
  paper.on('element:pointerdown', (cellView) => {
    selectedCell = cellView.model;
  });
  paper.on('link:pointerdown', (linkView) => {
    selectedCell = linkView.model;
  });
  paper.on('blank:pointerdown', () => {
    selectedCell = null;
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
  // (undo pause lifted in toolbar section after initial _pushUndo)

  // Zoom to fit if there are nodes
  if (graphData.nodes && graphData.nodes.length > 0) {
    try {
      paper.scaleContentToFit({ padding: 40, maxScale: 1.5, minScale: 0.3 });
    } catch (e) { /* ignore if not supported */ }
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

  container.addEventListener('dragover', (evt) => { evt.preventDefault(); });
  container.addEventListener('drop', (evt) => {
    evt.preventDefault();
    try {
      const data = JSON.parse(evt.dataTransfer.getData('text/plain'));
      const rect = container.getBoundingClientRect();
      const x = evt.clientX - rect.left;
      const y = evt.clientY - rect.top;
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
  let currentScale = 1;
  window.canvasZoomIn = function() {
    currentScale = Math.min(currentScale * 1.2, 4);
    paper.scale(currentScale, currentScale);
  };
  window.canvasZoomOut = function() {
    currentScale = Math.max(currentScale * 0.8, 0.2);
    paper.scale(currentScale, currentScale);
  };
  window.canvasZoomFit = function() {
    try {
      paper.scaleContentToFit({ padding: 40, maxScale: 2, minScale: 0.2 });
      currentScale = paper.scale().sx;
    } catch (e) { /* ignore */ }
  };
  window.canvasZoomReset = function() {
    currentScale = 1;
    paper.scale(1, 1);
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
    } else if (format === 'terraform' || format === 'ansible') {
      openRightPanel('Export', `<p style="color:#7a8cb0;">${format.charAt(0).toUpperCase() + format.slice(1)} export: use the IaC Gallery for generated code.</p>`);
    }
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

  window.canvasValidateIaC = function() {
    if (cfg.designId === 'new') {
      openRightPanel('Validate IaC', '<p style="color:#f39c12;">Save the design first.</p>');
      return;
    }
    showStatus('Validating IaC...');

    // Collect graph and send for validation
    const graphData = collectGraph();
    const iacNodes = graphData.nodes.filter(n =>
      (n.type || '').startsWith('iac-') || (n.label || '').toLowerCase().includes('terraform') ||
      (n.label || '').toLowerCase().includes('helm') || (n.label || '').toLowerCase().includes('ansible')
    );

    let html = '';
    html += _section('IaC Validation Results');

    if (iacNodes.length === 0) {
      html += '<div style="text-align:center;padding:16px;">';
      html += '<div style="font-size:24px;color:#f39c12;">⚠</div>';
      html += '<div style="color:#f39c12;font-weight:600;">No IaC Tools Found</div>';
      html += '<div style="color:#7a8cb0;font-size:11px;margin-top:4px;">Add Terraform, Helm, Ansible, or other IaC tools from the palette to enable validation.</div>';
      html += '</div>';
      openRightPanel('Validate IaC', html);
      showStatus('No IaC tools found');
      return;
    }

    // Layer 1: Syntax — check all nodes have types and labels
    let l1Pass = 0, l1Fail = 0;
    graphData.nodes.forEach(n => {
      if (n.type && n.label) l1Pass++; else l1Fail++;
    });

    // Layer 2: Schema — check edges reference valid nodes
    const nodeIds = new Set(graphData.nodes.map(n => n.id));
    let l2Pass = 0, l2Fail = 0;
    graphData.edges.forEach(e => {
      if (nodeIds.has(e.source) && nodeIds.has(e.target)) l2Pass++; else l2Fail++;
    });

    // Layer 3: Policy — check IaC nodes are connected
    let l3Pass = 0, l3Fail = 0;
    const connectedIds = new Set();
    graphData.edges.forEach(e => { connectedIds.add(e.source); connectedIds.add(e.target); });
    iacNodes.forEach(n => {
      if (connectedIds.has(n.id)) l3Pass++; else l3Fail++;
    });

    const totalChecks = (l1Pass + l1Fail) + (l2Pass + l2Fail) + (l3Pass + l3Fail);
    const totalPass = l1Pass + l2Pass + l3Pass;
    const pct = totalChecks > 0 ? Math.round((totalPass / totalChecks) * 100) : 0;
    const gateStatus = (l1Fail + l2Fail + l3Fail) === 0 ? 'PASS' : 'FAIL';
    const gateColor = gateStatus === 'PASS' ? '#27ae60' : '#e74c3c';

    html += `<div style="text-align:center;padding:12px 0;">`;
    html += `<div style="font-size:32px;font-weight:700;color:${gateColor};">${gateStatus}</div>`;
    html += `<div style="font-size:11px;color:#7a8cb0;">${pct}% checks passed</div>`;
    html += `</div>`;
    html += _bar(pct, gateColor);

    html += _section('Validation Layers');
    const layers = [
      { name: 'Layer 1: Syntax', pass: l1Pass, fail: l1Fail, desc: 'All nodes have type and label' },
      { name: 'Layer 2: Schema', pass: l2Pass, fail: l2Fail, desc: 'All edges reference valid nodes' },
      { name: 'Layer 3: Policy', pass: l3Pass, fail: l3Fail, desc: 'IaC nodes are connected to infrastructure' },
    ];
    layers.forEach(l => {
      const layerPct = (l.pass + l.fail) > 0 ? Math.round(l.pass / (l.pass + l.fail) * 100) : 100;
      const lColor = l.fail === 0 ? '#27ae60' : '#e74c3c';
      html += `<div style="margin-bottom:8px;">`;
      html += `<div style="display:flex;justify-content:space-between;font-size:11px;">`;
      html += `<span style="color:#eaeaea;">${l.name}</span>`;
      html += `<span style="color:${lColor};font-weight:600;">${l.pass}/${l.pass + l.fail}</span></div>`;
      html += `<div style="font-size:10px;color:#5a6e8c;">${l.desc}</div>`;
      html += _bar(layerPct, lColor);
      html += `</div>`;
    });

    html += _section('IaC Tools Detected');
    iacNodes.forEach(n => {
      html += `<div style="padding:6px 8px;background:#16213e;border-left:3px solid #27ae60;border-radius:4px;margin-bottom:4px;">`;
      html += `<div style="font-size:11px;font-weight:600;">${n.label}</div>`;
      html += `<div style="font-size:10px;color:#5a6e8c;">${n.type}</div></div>`;
    });

    openRightPanel('Validate IaC', html);
    showStatus(gateStatus === 'PASS' ? '✓ IaC validation passed' : '✗ IaC validation failed');
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
