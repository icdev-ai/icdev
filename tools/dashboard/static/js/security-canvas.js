/**
 * Security Design Canvas — JointJS canvas logic
 * Manages security design graph: assets, threats, controls, boundaries, data flows.
 */

/* State */
let graph, paper;
let selectedCell = null;
let undoStack = [], redoStack = [];
let saveTimer = null;
let isDirty = false;

/* Node type → display config */
const SC_STYLES = {
  // Assets
  'asset-server':    {fill:'#0f2b3a', stroke:'#3498db', label:'Server', symbol:'SRV'},
  'asset-database':  {fill:'#0f2b3a', stroke:'#2980b9', label:'Database', symbol:'DB'},
  'asset-api':       {fill:'#0f2b3a', stroke:'#1abc9c', label:'API', symbol:'API'},
  'asset-webapp':    {fill:'#0f2b3a', stroke:'#3498db', label:'Web App', symbol:'WEB'},
  'asset-user':      {fill:'#1a1a2e', stroke:'#9b59b6', label:'User', symbol:'USR'},
  'asset-container': {fill:'#0f2b3a', stroke:'#16a085', label:'Container', symbol:'CTR'},
  'asset-lambda':    {fill:'#0f2b3a', stroke:'#e67e22', label:'Function', symbol:'FN'},
  'asset-queue':     {fill:'#0f2b3a', stroke:'#2c3e50', label:'Queue', symbol:'MQ'},
  'asset-storage':   {fill:'#0f2b3a', stroke:'#27ae60', label:'Storage', symbol:'S3'},
  'asset-iot':       {fill:'#0f2b3a', stroke:'#e74c3c', label:'IoT', symbol:'IOT'},
  // Controls
  'ctrl-firewall':   {fill:'#1a2e1a', stroke:'#27ae60', label:'Firewall', symbol:'FW'},
  'ctrl-idp':        {fill:'#1a2e1a', stroke:'#2ecc71', label:'IdP', symbol:'IDP'},
  'ctrl-mfa':        {fill:'#1a2e1a', stroke:'#27ae60', label:'MFA', symbol:'MFA'},
  'ctrl-encryption': {fill:'#1a2e1a', stroke:'#1abc9c', label:'Encryption', symbol:'ENC'},
  'ctrl-siem':       {fill:'#1a2e1a', stroke:'#16a085', label:'SIEM', symbol:'SIM'},
  'ctrl-scanner':    {fill:'#1a2e1a', stroke:'#2ecc71', label:'Scanner', symbol:'SCN'},
  'ctrl-pam':        {fill:'#1a2e1a', stroke:'#27ae60', label:'PAM', symbol:'PAM'},
  'ctrl-dlp':        {fill:'#1a2e1a', stroke:'#1abc9c', label:'DLP', symbol:'DLP'},
  'ctrl-edr':        {fill:'#1a2e1a', stroke:'#16a085', label:'EDR', symbol:'EDR'},
  'ctrl-cspm':       {fill:'#1a2e1a', stroke:'#2ecc71', label:'CSPM', symbol:'CSP'},
  // Threats
  'threat-spoofing':       {fill:'#2b0f0f', stroke:'#e74c3c', label:'Spoofing', symbol:'S'},
  'threat-tampering':      {fill:'#2b0f0f', stroke:'#e74c3c', label:'Tampering', symbol:'T'},
  'threat-repudiation':    {fill:'#2b0f0f', stroke:'#c0392b', label:'Repudiation', symbol:'R'},
  'threat-info-disclosure':{fill:'#2b0f0f', stroke:'#e74c3c', label:'Info Disc.', symbol:'I'},
  'threat-dos':            {fill:'#2b0f0f', stroke:'#c0392b', label:'DoS', symbol:'D'},
  'threat-elevation':      {fill:'#2b0f0f', stroke:'#e74c3c', label:'Elevation', symbol:'E'},
  'threat-lateral':        {fill:'#2b1a0f', stroke:'#f39c12', label:'Lateral', symbol:'LM'},
  'threat-exfil':          {fill:'#2b0f0f', stroke:'#e74c3c', label:'Exfil', symbol:'EX'},
  // Boundaries
  'boundary-dmz':       {fill:'rgba(233,69,96,0.06)', stroke:'#e94560', label:'DMZ', symbol:'DMZ'},
  'boundary-enclave':   {fill:'rgba(233,69,96,0.06)', stroke:'#e94560', label:'Enclave', symbol:'ENC'},
  'boundary-internet':  {fill:'rgba(231,76,60,0.06)', stroke:'#e74c3c', label:'Internet', symbol:'INT'},
  'boundary-cloud':     {fill:'rgba(52,152,219,0.06)', stroke:'#3498db', label:'Cloud', symbol:'VPC'},
  'boundary-onprem':    {fill:'rgba(155,89,182,0.06)', stroke:'#9b59b6', label:'On-Prem', symbol:'ONP'},
  'boundary-classify':  {fill:'rgba(241,196,15,0.06)', stroke:'#f1c40f', label:'Classify', symbol:'CLS'},
};

/* Initialize */
document.addEventListener('DOMContentLoaded', () => {
  graph = new joint.dia.Graph();
  paper = new joint.dia.Paper({
    el: document.getElementById('canvas-container'),
    model: graph,
    width: '100%', height: '100%',
    gridSize: 10, drawGrid: {name:'mesh', args:{color:'#1e3a6e', thickness:0.5}},
    background: {color: '#1a1a2e'},
    interactive: true,
    defaultLink: () => new joint.shapes.standard.Link({
      attrs: {line: {stroke:'#5dade2', strokeWidth:2, targetMarker:{type:'path',d:'M 10 -5 0 0 10 5 z',fill:'#5dade2'}}},
    }),
    linkPinning: false,
    defaultConnectionPoint: {name: 'boundary'},
    validateConnection: function(cellViewS, magnetS, cellViewT, magnetT, end, linkView) {
      if (!cellViewS || !cellViewT) return false;
      const srcType = cellViewS.model.get('nodeType') || '';
      const tgtType = cellViewT.model.get('nodeType') || '';
      // Don't allow self-connections
      if (cellViewS === cellViewT) return false;
      // Don't allow connections from boundaries to boundaries
      if (srcType.startsWith('boundary-') && tgtType.startsWith('boundary-')) return false;
      // Threats can only connect to assets or controls (what they threaten/what mitigates them)
      if (srcType.startsWith('threat-')) {
        return tgtType.startsWith('asset-') || tgtType.startsWith('ctrl-');
      }
      if (tgtType.startsWith('threat-')) {
        return srcType.startsWith('asset-') || srcType.startsWith('ctrl-');
      }
      // Controls connect to assets they protect, other controls, or boundaries
      // Assets connect to other assets, controls, or boundaries
      // Generally permissive for assets and controls — block nonsensical combos
      return true;
    },
    snapLinks: {radius: 20},
  });

  // Load existing design
  if (DESIGN_ID && DESIGN_ID !== 'new') { loadDesign(DESIGN_ID); }

  // Enhanced tooltips (shared utility from canvas-tooltips.js)
  if (typeof initEnhancedTooltips === 'function') {
    initEnhancedTooltips(paper, graph, (type) => SC_STYLES[type] || {fill:'#0f2b3a', stroke:'#3498db', symbol:'?', label:type});
  }

  // Click handler
  paper.on('element:pointerclick', (view) => {
    selectedCell = view.model;
    showProperties(selectedCell);
  });
  paper.on('blank:pointerclick', () => {
    selectedCell = null;
    document.getElementById('config-empty').classList.remove('hidden');
    document.getElementById('config-form').classList.add('hidden');
  });

  // Undo capture — save state before interactive operations
  let _undoDebounce = null;
  graph.on('batch:start', () => {
    if (_undoDebounce) return;
    _undoDebounce = setTimeout(() => { pushUndo(); _undoDebounce = null; }, 200);
  });

  // Auto-save on change
  graph.on('change', () => { isDirty = true; scheduleSave(); });
  graph.on('add', () => { isDirty = true; scheduleSave(); });
  graph.on('remove', () => { isDirty = true; scheduleSave(); });

  // Auto-style links based on connected node types
  graph.on('add', (cell) => {
    if (cell.isLink && cell.isLink()) {
      const src = graph.getCell(cell.source().id);
      const tgt = graph.getCell(cell.target().id);
      if (src && tgt) {
        const style = getLinkStyle(src.get('nodeType'), tgt.get('nodeType'));
        cell.attr('line/stroke', style.stroke);
        cell.attr('line/strokeWidth', style.width);
        if (style.dash) cell.attr('line/strokeDasharray', style.dash);
        cell.attr('line/targetMarker/fill', style.stroke);
      }
    }
  });

  // Canvas panning (drag on blank area)
  let _panStart = null;
  paper.on('blank:pointerdown', (evt, x, y) => {
    _panStart = {x: evt.clientX, y: evt.clientY, tx: paper.translate().tx, ty: paper.translate().ty};
  });
  document.addEventListener('mousemove', (evt) => {
    if (!_panStart) return;
    paper.translate(_panStart.tx + evt.clientX - _panStart.x, _panStart.ty + evt.clientY - _panStart.y);
  });
  document.addEventListener('mouseup', () => { _panStart = null; });

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'z') { e.preventDefault(); undo(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 'y') { e.preventDefault(); redo(); }
    if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveDesign(); }
    if (e.key === 'Delete' && selectedCell) { pushUndo(); selectedCell.remove(); selectedCell = null; }
  });

  // Mouse wheel zoom
  document.getElementById('canvas-container').addEventListener('wheel', (evt) => {
    evt.preventDefault();
    const delta = evt.deltaY > 0 ? 0.9 : 1.1;
    const s = paper.scale();
    const newScale = Math.max(0.2, Math.min(4, s.sx * delta));
    paper.scale(newScale, newScale);
  }, {passive: false});

  // Palette section toggle (uses .open class on parent .palette-section — same as NDC CSS)
  document.querySelectorAll('.palette-cat').forEach(cat => {
    cat.style.cursor = 'pointer';
    cat.addEventListener('click', () => {
      const section = cat.closest('.palette-section');
      if (section) section.classList.toggle('open');
    });
  });
  // Open first section by default
  const firstSection = document.querySelector('.palette-section');
  if (firstSection) firstSection.classList.add('open');
});

/* Drag-and-drop handlers (same pattern as NDC) */
function onDragStart(event) {
  event.dataTransfer.setData('text/plain', event.currentTarget.dataset.type);
}

function onDrop(event) {
  event.preventDefault();
  const type = event.dataTransfer.getData('text/plain');
  if (!type) return;
  pushUndo();
  const rect = document.getElementById('canvas-container').getBoundingClientRect();
  let x = event.clientX - rect.left;
  let y = event.clientY - rect.top;
  // Adjust for current pan/zoom
  const t = paper.translate();
  const s = paper.scale();
  x = (x - t.tx) / s.sx;
  y = (y - t.ty) / s.sy;
  // Find non-overlapping position
  const pos = findClearPosition(x, y, type.startsWith('boundary-') ? 400 : 110, type.startsWith('boundary-') ? 300 : 60);
  addNodeToGraph(type, pos.x, pos.y);
}

function findClearPosition(x, y, w, h) {
  const existing = graph.getElements().map(el => {
    const p = el.position();
    const s = el.size();
    return {x: p.x, y: p.y, w: s.width, h: s.height};
  });
  const PAD = 20;
  function overlaps(bx, by) {
    return existing.some(e =>
      bx < e.x + e.w + PAD && bx + w + PAD > e.x &&
      by < e.y + e.h + PAD && by + h + PAD > e.y
    );
  }
  if (!overlaps(x, y)) return {x, y};
  // Spiral search
  const step = Math.max(w, h) + PAD + 10;
  for (let ring = 1; ring <= 8; ring++) {
    const candidates = [
      {dx: ring * step, dy: 0}, {dx: 0, dy: ring * step},
      {dx: -ring * step, dy: 0}, {dx: 0, dy: -ring * step},
      {dx: ring * step, dy: ring * step}, {dx: -ring * step, dy: ring * step},
    ];
    for (const c of candidates) {
      if (!overlaps(x + c.dx, y + c.dy)) return {x: x + c.dx, y: y + c.dy};
    }
  }
  // Fallback: place to the right
  const maxX = existing.reduce((m, e) => Math.max(m, e.x + e.w), x);
  return {x: maxX + PAD + 10, y: y};
}

function addNodeToGraph(type, x, y) {
  const style = SC_STYLES[type] || {fill:'#0f2b3a', stroke:'#3498db', symbol:'?', label:type};
  const isBoundary = type.startsWith('boundary-');
  const el = new joint.shapes.standard.Rectangle({
    position: {x, y},
    size: isBoundary ? {width:400, height:300} : {width:110, height:60},
    attrs: {
      body: {fill: style.fill, stroke: style.stroke, strokeWidth: 2,
        rx: isBoundary ? 8 : 6, ry: isBoundary ? 8 : 6,
        ...(isBoundary ? {strokeDasharray: '8,4'} : {})},
      label: {text: style.label, fill: isBoundary ? style.stroke : '#eaeaea',
        fontSize: isBoundary ? 13 : 11, fontWeight: isBoundary ? 'bold' : 'normal',
        fontFamily: 'Segoe UI, sans-serif', ...(isBoundary ? {refY: 15} : {})},
    },
  });
  el.set('nodeType', type);
  el.set('nodeId', 'sc-' + Date.now().toString(36));
  el.set('config', {});
  el.addTo(graph);
}

function getLinkStyle(srcType, tgtType) {
  // Threat links: red dashed
  if ((srcType || '').startsWith('threat-') || (tgtType || '').startsWith('threat-')) {
    return {stroke: '#e74c3c', dash: '8,4', width: 2};
  }
  // Control-to-asset links: green
  if ((srcType || '').startsWith('ctrl-') && (tgtType || '').startsWith('asset-')) {
    return {stroke: '#27ae60', dash: '', width: 2};
  }
  if ((srcType || '').startsWith('asset-') && (tgtType || '').startsWith('ctrl-')) {
    return {stroke: '#27ae60', dash: '', width: 2};
  }
  // Asset-to-asset: blue (data flow)
  if ((srcType || '').startsWith('asset-') && (tgtType || '').startsWith('asset-')) {
    return {stroke: '#3498db', dash: '', width: 2};
  }
  // Default: light blue
  return {stroke: '#5dade2', dash: '', width: 2};
}

function loadDesign(id) {
  fetch(SC_BASE + '/api/designs/' + id).then(r => r.json()).then(data => {
    if (data.graph_json) {
      const g = typeof data.graph_json === 'string' ? JSON.parse(data.graph_json) : data.graph_json;
      renderGraph(g);
      if ((g.nodes || []).length > 0) {
        requestAnimationFrame(() => setTimeout(zoomFit, 100));
      }
    }
    const nameEl = document.getElementById('design-name-display');
    if (nameEl) nameEl.textContent = data.name || 'Untitled';
  });
}

/* Push overlapping nodes apart so saved designs don't render with conflicts.
   Boundaries (large containers) are excluded via skipFn. */
function resolveNodeOverlaps(g, padding, skipFn) {
  padding = padding == null ? 20 : padding;
  let els = g.getElements();
  if (skipFn) els = els.filter(e => !skipFn(e));
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

function renderGraph(g) {
  graph.clear();
  const nodeMap = {};
  (g.nodes || []).forEach(n => {
    const style = SC_STYLES[n.type] || {fill:'#0f2b3a', stroke:'#3498db', symbol:'?'};
    const el = new joint.shapes.standard.Rectangle({
      position: {x: n.x || 0, y: n.y || 0},
      size: {width: 110, height: 60},
      attrs: {
        body: {fill: style.fill, stroke: style.stroke, strokeWidth: 2, rx: 6, ry: 6},
        label: {text: n.label || style.label, fill: '#eaeaea', fontSize: 11, fontFamily: 'Segoe UI, sans-serif'},
      },
    });
    el.set('nodeType', n.type);
    el.set('nodeId', n.id);
    el.set('config', n.config || {});
    el.addTo(graph);
    nodeMap[n.id] = el.id;
  });
  (g.boundaries || []).forEach(b => {
    const style = SC_STYLES[b.type] || {fill:'rgba(233,69,96,0.06)', stroke:'#e94560'};
    const el = new joint.shapes.standard.Rectangle({
      position: {x: b.x || 0, y: b.y || 0},
      size: {width: b.width || 400, height: b.height || 300},
      attrs: {
        body: {fill: style.fill, stroke: style.stroke, strokeWidth: 2, strokeDasharray: '8,4', rx: 8, ry: 8},
        label: {text: b.label || 'Boundary', fill: style.stroke, fontSize: 13, fontWeight: 'bold', refY: 15},
      },
    });
    el.set('nodeType', b.type);
    el.set('nodeId', b.id);
    el.addTo(graph);
  });
  // Resolve any overlap in saved node positions (skip boundary containers)
  resolveNodeOverlaps(graph, 20, e => (e.get('nodeType') || '').startsWith('boundary-'));
  (g.edges || []).forEach(e => {
    const src = nodeMap[e.source], tgt = nodeMap[e.target];
    if (src && tgt) {
      const link = new joint.shapes.standard.Link({
        source: {id: src}, target: {id: tgt},
        attrs: {line: {stroke: e.encrypted ? '#27ae60' : '#5dade2', strokeWidth: 2,
          targetMarker: {type:'path', d:'M 10 -5 0 0 10 5 z', fill: e.encrypted ? '#27ae60' : '#5dade2'}}},
        labels: [{attrs: {text: {text: e.label || '', fill: '#7a8cb0', fontSize: 10}}}],
      });
      link.addTo(graph);
    }
  });
}

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { if (isDirty) saveDesign(); }, 3000);
}

function saveDesign() {
  const graphData = exportGraph();
  const nameEl = document.getElementById('design-name-display') || document.getElementById('design-name');
  const name = (nameEl ? (nameEl.value || nameEl.textContent) : 'Untitled').trim() || 'Untitled';
  const statusEl = document.getElementById('tb-status');
  if (statusEl) statusEl.textContent = 'Saving...';
  const currentId = window._designId || DESIGN_ID;
  if (!currentId || currentId === 'new') {
    fetch(SC_BASE + '/api/designs', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, graph_json: graphData}),
    }).then(r => r.json()).then(d => {
      window._designId = d.id;
      window.history.replaceState({}, '', SC_BASE + '/canvas/' + d.id);
      isDirty = false;
      if (statusEl) statusEl.textContent = 'Saved';
    }).catch(() => { if (statusEl) statusEl.textContent = 'Save failed'; });
  } else {
    fetch(SC_BASE + '/api/designs/' + currentId, {
      method: 'PUT', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, graph_json: graphData}),
    }).then(() => {
      isDirty = false;
      if (statusEl) statusEl.textContent = 'Saved';
    }).catch(() => { if (statusEl) statusEl.textContent = 'Save failed'; });
  }
}

function saveName() { if (DESIGN_ID !== 'new') saveDesign(); }

function exportGraph() {
  const nodes = [], edges = [], boundaries = [];
  graph.getElements().forEach(el => {
    const type = el.get('nodeType') || '';
    const pos = el.position();
    const size = el.size();
    const obj = {id: el.get('nodeId') || el.id, type, label: el.attr('label/text') || '', x: pos.x, y: pos.y, config: el.get('config') || {}};
    if (type.startsWith('boundary-')) { obj.width = size.width; obj.height = size.height; boundaries.push(obj); }
    else { nodes.push(obj); }
  });
  graph.getLinks().forEach(lnk => {
    const src = graph.getCell(lnk.source().id), tgt = graph.getCell(lnk.target().id);
    if (src && tgt) {
      edges.push({source: src.get('nodeId') || src.id, target: tgt.get('nodeId') || tgt.id,
        label: (lnk.labels()[0]||{}).attrs?.text?.text || '', protocol: '', encrypted: 0, authenticated: 0});
    }
  });
  return {nodes, edges, boundaries};
}

function showProperties(cell) {
  document.getElementById('config-empty').classList.add('hidden');
  document.getElementById('config-form').classList.remove('hidden');
  const type = cell.get('nodeType') || 'unknown';
  const label = cell.attr('label/text') || '';
  const config = cell.get('config') || {};
  document.getElementById('cfg-type').value = type;
  document.getElementById('cfg-label').value = label;
  if (document.getElementById('cfg-classification')) {
    document.getElementById('cfg-classification').value = config.classification || 'CUI';
  }
  if (document.getElementById('cfg-description')) {
    document.getElementById('cfg-description').value = config.description || '';
  }
}

/* Actions */
function runStrideAnalysis() {
  if (DESIGN_ID === 'new') { alert('Save the design first.'); return; }
  fetch(SC_BASE + '/api/designs/' + DESIGN_ID + '/stride', {method:'POST'}).then(r=>r.json()).then(d => {
    const panel = document.getElementById('assessment-panel');
    panel.classList.remove('hidden');
    document.getElementById('assess-title').textContent = 'STRIDE Analysis';
    let html = `<div class="stats-bar" style="margin-bottom:12px;">
      <div class="stat-card"><div class="stat-value">${d.total||0}</div><div class="stat-label">Threats Found</div></div>`;
    for (const [cat, cnt] of Object.entries(d.by_category || {})) {
      html += `<div class="stat-card"><div class="stat-value" style="color:#e74c3c;">${cnt}</div><div class="stat-label">${cat}</div></div>`;
    }
    html += '</div>';
    if (d.threats && d.threats.length) {
      html += '<table class="data-table"><thead><tr><th>Category</th><th>Title</th><th>Affected</th></tr></thead><tbody>';
      d.threats.forEach(t => html += `<tr><td style="color:#e74c3c;font-weight:bold;">${t.category}</td><td>${t.title}</td><td>${t.affected || (t.affected_assets||[]).join(', ') || '—'}</td></tr>`);
      html += '</tbody></table>';
    }
    document.getElementById('assess-content').innerHTML = html;
  });
}

function runAssessment() {
  if (DESIGN_ID === 'new') { alert('Save the design first.'); return; }
  fetch(SC_BASE + '/api/designs/' + DESIGN_ID + '/assess', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'}).then(r=>r.json()).then(d => {
    const panel = document.getElementById('assessment-panel');
    panel.classList.remove('hidden');
    document.getElementById('assess-title').textContent = 'Security Assessment — Grade: ' + d.posture_grade;
    const gc = d.posture_grade <= 'B' ? '#27ae60' : d.posture_grade === 'C' ? '#f39c12' : '#e74c3c';
    let html = `<div class="stats-bar" style="margin-bottom:12px;">
      <div class="stat-card"><div class="stat-value" style="color:${gc};font-size:2rem;">${d.posture_grade}</div><div class="stat-label">Grade</div></div>
      <div class="stat-card"><div class="stat-value">${(d.risk_score||0).toFixed(1)}</div><div class="stat-label">Risk Score</div></div>
      <div class="stat-card"><div class="stat-value">${d.total_threats||0}</div><div class="stat-label">Threats</div></div>
      <div class="stat-card"><div class="stat-value">${(d.findings||[]).length}</div><div class="stat-label">Findings</div></div></div>`;
    if (d.findings && d.findings.length) {
      html += '<table class="data-table"><thead><tr><th>Rule</th><th>Sev</th><th>Title</th></tr></thead><tbody>';
      d.findings.forEach(f => {
        const c = f.severity==='CAT1'?'#e74c3c':f.severity==='CAT2'?'#f39c12':'#5dade2';
        html += `<tr><td><strong>${f.rule_id}</strong></td><td style="color:${c};font-weight:bold;">${f.severity}</td><td>${f.title}</td></tr>`;
      });
      html += '</tbody></table>';
    }
    html += `<div style="margin-top:12px;text-align:center;">
      <button class="btn btn-primary" style="background:#27ae60;border-color:#1e8449;" onclick="generateRemediation()">Generate Remediation Plan</button>
    </div>`;
    document.getElementById('assess-content').innerHTML = html;
  });
}

function showGaps() {
  if (DESIGN_ID === 'new') { alert('Save the design first.'); return; }
  fetch(SC_BASE + '/api/designs/' + DESIGN_ID + '/export/stride').then(r=>r.json()).then(d => {
    const panel = document.getElementById('assessment-panel');
    panel.classList.remove('hidden');
    document.getElementById('assess-title').textContent = 'Security Gaps';
    let html = '<h3 style="color:#e94560;margin-bottom:8px;">Detected Gaps</h3><ul style="padding-left:1.5rem;">';
    (d.gaps || []).forEach(g => html += `<li style="margin-bottom:4px;"><strong>${g.title||g.gap_type||''}</strong>: ${g.description||''}</li>`);
    html += '</ul>';
    if (d.suggested_controls && d.suggested_controls.length) {
      html += '<h3 style="color:#27ae60;margin:12px 0 8px;">Suggested Controls</h3>';
      html += '<table class="data-table"><thead><tr><th>Threat</th><th>NIST Controls</th><th>Rationale</th></tr></thead><tbody>';
      d.suggested_controls.forEach(c => {
        const ctrls = (c.recommended_controls||[]).map(r => r.control_id || r).join(', ');
        html += `<tr><td style="font-size:0.8rem;">${c.threat_title||''}</td><td style="color:#27ae60;font-weight:bold;">${ctrls}</td><td style="font-size:0.8rem;">${c.rationale||''}</td></tr>`;
      });
      html += '</tbody></table>';
    }
    document.getElementById('assess-content').innerHTML = html;
  });
}

function generateRemediation() {
  if (DESIGN_ID === 'new') { alert('Save the design first.'); return; }
  fetch(SC_BASE + '/api/designs/' + DESIGN_ID + '/remediate', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'}).then(r=>r.json()).then(d => {
    alert('Remediation plan generated: ' + (d.total_actions||0) + ' actions, risk: ' + (d.overall_risk||'?'));
    window.location.href = SC_BASE + '/remediation/' + DESIGN_ID;
  });
}

function importFromNDC() {
  const topoId = prompt('Enter NDC Topology ID (or leave blank to pick from list):');
  if (topoId) {
    fetch(SC_BASE + '/api/import-from-ndc/' + topoId, {method:'POST'}).then(r=>r.json()).then(d => {
      if (d.design_id) { window.location.href = SC_BASE + '/canvas/' + d.design_id; }
      else { alert('Error: ' + (d.error || 'Unknown')); }
    });
  }
}

/* Snippets panel */
function openSnippetsPanel() {
  fetch(SC_BASE + '/api/snippets').then(r => r.json()).then(snippets => {
    const panel = document.getElementById('assessment-panel');
    panel.classList.remove('hidden');
    document.getElementById('assess-title').textContent = 'Security Snippets — Drag-and-Drop Building Blocks';
    let html = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;">';
    snippets.forEach(s => {
      const graph = typeof s.graph_json === 'string' ? JSON.parse(s.graph_json) : s.graph_json;
      const nodeCount = (graph.nodes || []).length;
      html += `<div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:12px;">
        <div style="font-weight:700;font-size:13px;color:#a29bfe;margin-bottom:4px;">${s.name}</div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px;">${s.category} &middot; ${nodeCount} nodes</div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px;line-height:1.3;">${(s.description || '').substring(0, 120)}</div>
        <button class="btn btn-primary" style="font-size:11px;padding:2px 10px;background:#a29bfe;border-color:#8b7fe0;" onclick='dropSnippet(${JSON.stringify(s.graph_json)})'>Drop on Canvas</button>
      </div>`;
    });
    html += '</div>';
    if (!snippets.length) html = '<p style="color:var(--text-dim);padding:1rem;">No snippets available.</p>';
    document.getElementById('assess-content').innerHTML = html;
  });
}

function dropSnippet(graphJson) {
  pushUndo();
  const g = typeof graphJson === 'string' ? JSON.parse(graphJson) : graphJson;
  const offsetX = 100 + Math.random() * 200;
  const offsetY = 100 + Math.random() * 200;
  const nodeMap = {};
  (g.nodes || []).forEach(n => {
    const newId = 'sc-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
    nodeMap[n.id] = newId;
    addNodeToGraph(n.type, (n.x || 0) + offsetX, (n.y || 0) + offsetY);
    const lastEl = graph.getElements()[graph.getElements().length - 1];
    if (lastEl) {
      lastEl.set('nodeId', newId);
      lastEl.attr('label/text', n.label || '');
    }
  });
  (g.boundaries || []).forEach(b => {
    addNodeToGraph(b.type, (b.x || 0) + offsetX, (b.y || 0) + offsetY);
  });
  document.getElementById('assessment-panel').classList.add('hidden');
}

/* Config panel helpers (match NDC pattern) */
function updateSelectedLabel(val) {
  if (selectedCell) selectedCell.attr('label/text', val);
}
function updateConfig(key, val) {
  if (selectedCell) {
    const cfg = selectedCell.get('config') || {};
    cfg[key] = val;
    selectedCell.set('config', cfg);
  }
}

/* Zoom */
function zoomIn() { paper.scale(paper.scale().sx * 1.2, paper.scale().sy * 1.2); }
function zoomOut() { paper.scale(paper.scale().sx / 1.2, paper.scale().sy / 1.2); }
function zoomFit() {
  const area = document.getElementById('canvas-container');
  const w = area ? area.clientWidth : 1200;
  const h = area ? area.clientHeight : 800;
  paper.scaleContentToFit({ fittingBBox: { x: 0, y: 0, width: w, height: h }, padding: 40, maxScale: 1 });
}

/* ── Undo / Redo (snapshot-based, same pattern as NDC) ──────────────────────── */
function pushUndo() {
  undoStack.push(JSON.stringify(exportGraph()));
  redoStack = [];
  // Cap stack at 50 entries
  if (undoStack.length > 50) undoStack.shift();
}

function undo() {
  if (!undoStack.length) return;
  redoStack.push(JSON.stringify(exportGraph()));
  const prev = JSON.parse(undoStack.pop());
  graph.clear();
  renderGraph(prev);
  isDirty = true;
  scheduleSave();
}

function redo() {
  if (!redoStack.length) return;
  undoStack.push(JSON.stringify(exportGraph()));
  const next = JSON.parse(redoStack.pop());
  graph.clear();
  renderGraph(next);
  isDirty = true;
  scheduleSave();
}

async function exportSDCAs(fmt) {
  if (!DESIGN_ID || DESIGN_ID === 'new') {
    await saveDesign();
  }
  const id = window._designId || DESIGN_ID;
  if (!id || id === 'new') { alert('Save the design first.'); return; }
  try {
    const r = await fetch(SC_BASE + '/api/designs/' + id + '/export/' + fmt, {method: 'POST'});
    const data = await r.json();
    if (data.error) { alert('Export error: ' + data.error); return; }
    if (data.content_b64) {
      const bin = atob(data.content_b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const blob = new Blob([bytes], {type: 'application/octet-stream'});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = data.filename;
      a.click();
    } else if (data.content) {
      const blob = new Blob([data.content], {type: 'text/plain'});
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = data.filename;
      a.click();
    }
  } catch (e) { alert('Export failed: ' + e.message); }
}

function showNistOverlay() {
  const id = window._designId || DESIGN_ID;
  if (!id || id === 'new') { alert('Save the design first.'); return; }
  fetch(SC_BASE + '/api/designs/' + id + '/nist-coverage').then(r=>r.json()).then(d => {
    const panel = document.getElementById('assessment-panel');
    panel.classList.remove('hidden');
    document.getElementById('assess-title').textContent = 'NIST 800-53 Control Coverage \u2014 ' + (d.overall_coverage_pct||0) + '%';
    let html = `<div class="stats-bar" style="margin-bottom:12px;">
      <div class="stat-card"><div class="stat-value" style="color:${d.overall_coverage_pct>=70?'#27ae60':d.overall_coverage_pct>=40?'#f39c12':'#e74c3c'}">${d.overall_coverage_pct||0}%</div><div class="stat-label">Overall Coverage</div></div>
      <div class="stat-card"><div class="stat-value">${d.covered_families||0}/${d.total_families||20}</div><div class="stat-label">Families Covered</div></div></div>`;
    html += '<table class="data-table"><thead><tr><th>Family</th><th>Name</th><th>Coverage</th><th>Status</th><th>Controls</th></tr></thead><tbody>';
    for (const [code, fam] of Object.entries(d.families || {}).sort((a,b)=>a[0].localeCompare(b[0]))) {
      const sc = fam.coverage_pct>=70?'#27ae60':fam.coverage_pct>=30?'#f39c12':'#e74c3c';
      const bar = `<div style="background:var(--bg);border-radius:3px;height:8px;width:80px;display:inline-block;vertical-align:middle;"><div style="background:${sc};height:100%;border-radius:3px;width:${fam.coverage_pct}%;"></div></div> <span style="font-size:10px;">${fam.coverage_pct}%</span>`;
      html += `<tr><td style="font-weight:bold;color:#5dade2;">${code}</td><td>${fam.name}</td><td>${bar}</td><td style="color:${sc};font-weight:bold;">${fam.status}</td><td style="font-size:11px;">${(fam.controls_present||[]).join(', ')||'\u2014'}</td></tr>`;
    }
    html += '</tbody></table>';
    document.getElementById('assess-content').innerHTML = html;
  });
}

function showAttackPaths() {
  const id = window._designId || DESIGN_ID;
  if (!id || id === 'new') { alert('Save the design first.'); return; }
  fetch(SC_BASE + '/api/designs/' + id + '/attack-paths', {method:'POST'}).then(r=>r.json()).then(d => {
    const panel = document.getElementById('assessment-panel');
    panel.classList.remove('hidden');
    document.getElementById('assess-title').textContent = 'Attack Path Analysis \u2014 ' + (d.total_paths||0) + ' paths (' + (d.critical_paths||0) + ' critical)';
    let html = '';
    if (d.attack_paths && d.attack_paths.length) {
      html += '<table class="data-table"><thead><tr><th>Risk</th><th>Entry Point</th><th>Path</th><th>Target</th><th>Score</th><th>Mitigations</th></tr></thead><tbody>';
      d.attack_paths.sort((a,b) => b.risk_score - a.risk_score).forEach(p => {
        const rc = p.risk_level==='critical'?'#e74c3c':p.risk_level==='high'?'#f39c12':p.risk_level==='medium'?'#5dade2':'#27ae60';
        const hops = p.hops.map(h => h.node_label || h.node_id).join(' \u2192 ');
        html += `<tr>
          <td style="color:${rc};font-weight:bold;text-transform:uppercase;">${p.risk_level}</td>
          <td style="color:#e74c3c;">${p.entry.label}</td>
          <td style="font-size:11px;color:var(--text-dim);">${hops}</td>
          <td style="color:#f39c12;font-weight:bold;">${p.target.label}</td>
          <td style="font-weight:bold;">${p.risk_score}</td>
          <td style="font-size:11px;">${(p.mitigations||[]).join(', ')||'None'}</td>
        </tr>`;
      });
      html += '</tbody></table>';
    } else {
      html = '<p style="padding:1rem;color:var(--text-dim);">No attack paths found. Either no internet entry points or no high-value targets in this design.</p>';
    }
    document.getElementById('assess-content').innerHTML = html;
  });
}

function applyAutoFix() {
  const id = window._designId || DESIGN_ID;
  if (!id || id === 'new') { alert('Save the design first.'); return; }
  if (!confirm('Auto-fix will add missing security controls to your canvas. Continue?')) return;
  const statusEl = document.getElementById('tb-status');
  if (statusEl) statusEl.textContent = 'Auto-fixing...';
  fetch(SC_BASE + '/api/designs/' + id + '/auto-fix', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'}).then(r=>r.json()).then(d => {
    if (d.error) { alert('Auto-fix error: ' + d.error); return; }
    if (statusEl) statusEl.textContent = 'Fixed: +' + (d.total_fixes||0) + ' controls';
    // Reload the design to show new nodes
    loadDesign(id);
    // Show results in panel
    const panel = document.getElementById('assessment-panel');
    panel.classList.remove('hidden');
    document.getElementById('assess-title').textContent = 'Auto-Fix Applied \u2014 ' + (d.total_fixes||0) + ' controls added';
    let html = '<div style="padding:8px;">';
    if (d.nodes_added && d.nodes_added.length) {
      html += '<h4 style="color:#27ae60;margin-bottom:8px;">Controls Added:</h4><ul style="padding-left:1.5rem;">';
      d.nodes_added.forEach(n => html += `<li style="margin-bottom:2px;font-size:12px;">${n.label} (${n.type})</li>`);
      html += '</ul>';
    }
    if (d.edges_modified && d.edges_modified.length) {
      html += `<p style="margin-top:8px;font-size:12px;color:#5dade2;">${d.edges_modified.length} existing flow(s) updated with encryption/authentication.</p>`;
    }
    html += '</div>';
    document.getElementById('assess-content').innerHTML = html;
  }).catch(e => { if (statusEl) statusEl.textContent = 'Auto-fix failed'; });
}

function showMitreCoverage() {
  const id = window._designId || DESIGN_ID;
  if (!id || id === 'new') { alert('Save the design first.'); return; }
  fetch(SC_BASE + '/api/designs/' + id + '/mitre-coverage').then(r=>r.json()).then(d => {
    const panel = document.getElementById('assessment-panel');
    panel.classList.remove('hidden');
    document.getElementById('assess-title').textContent = 'MITRE ATT&CK Coverage \u2014 ' + (d.overall_coverage_pct||0) + '% (' + (d.detected_techniques||0) + '/' + (d.total_techniques||0) + ')';
    let html = '';
    // Heatmap grid
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;margin-bottom:16px;">';
    for (const [key, tactic] of Object.entries(d.tactics || {})) {
      const pct = tactic.coverage_pct || 0;
      const color = pct >= 70 ? '#27ae60' : pct >= 40 ? '#f39c12' : '#e74c3c';
      const bg = pct >= 70 ? 'rgba(39,174,96,0.1)' : pct >= 40 ? 'rgba(243,156,18,0.1)' : 'rgba(231,76,60,0.1)';
      html += '<div style="background:' + bg + ';border:1px solid ' + color + ';border-radius:6px;padding:10px;">';
      html += '<div style="font-size:10px;color:var(--text-dim);">' + tactic.tactic_id + '</div>';
      html += '<div style="font-weight:700;font-size:12px;color:' + color + ';">' + tactic.name + '</div>';
      html += '<div style="margin:6px 0;background:var(--bg);border-radius:3px;height:6px;"><div style="background:' + color + ';height:100%;border-radius:3px;width:' + pct + '%;"></div></div>';
      html += '<div style="font-size:11px;">' + tactic.detected_techniques + '/' + tactic.total_techniques + ' detected (' + pct + '%)</div>';
      html += '</div>';
    }
    html += '</div>';
    // Critical undetected
    if (d.critical_undetected && d.critical_undetected.length) {
      html += '<div style="background:rgba(231,76,60,0.1);border:1px solid #e74c3c;border-radius:6px;padding:12px;margin-bottom:12px;">';
      html += '<h4 style="color:#e74c3c;margin:0 0 8px;">Critical Undetected Techniques</h4>';
      html += '<ul style="margin:0;padding-left:1.5rem;font-size:12px;">';
      d.critical_undetected.forEach(t => html += '<li style="color:#e74c3c;">' + t + '</li>');
      html += '</ul></div>';
    }
    document.getElementById('assess-content').innerHTML = html;
  });
}

function importThreatModel() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json,.tm7,.xml';
  input.onchange = function(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(ev) {
      const content = ev.target.result;
      const fmt = file.name.endsWith('.tm7') ? 'tmt' : file.name.endsWith('.json') ? 'threat-dragon' : 'auto';
      fetch(SC_BASE + '/api/import/threat-model', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({content: content, format: fmt})
      }).then(r => r.json()).then(d => {
        if (d.error) { alert('Import error: ' + d.error); return; }
        if (d.design_id) {
          alert('Imported ' + (d.nodes_imported||0) + ' nodes and ' + (d.threats_imported||0) + ' threats from ' + (d.source_format||'unknown'));
          window.location.href = SC_BASE + '/canvas/' + d.design_id;
        }
      });
    };
    reader.readAsText(file);
  };
  input.click();
}

function showComplianceCrosswalk() {
  const id = window._designId || DESIGN_ID;
  if (!id || id === 'new') { alert('Save the design first.'); return; }
  fetch(SC_BASE + '/api/designs/' + id + '/compliance-crosswalk').then(r=>r.json()).then(d => {
    const panel = document.getElementById('assessment-panel');
    panel.classList.remove('hidden');
    document.getElementById('assess-title').textContent = 'Compliance Crosswalk — NIST / FedRAMP / CMMC';
    let html = '<div class="stats-bar" style="margin-bottom:12px;">';
    for (const [fw, data] of Object.entries(d.frameworks || {})) {
      const pct = data.coverage_pct || 0;
      const color = pct >= 70 ? '#27ae60' : pct >= 40 ? '#f39c12' : '#e74c3c';
      const label = fw === 'nist_800_53' ? 'NIST 800-53' : fw === 'fedramp' ? 'FedRAMP' : 'CMMC L2';
      html += '<div class="stat-card"><div class="stat-value" style="color:' + color + ';">' + pct + '%</div><div class="stat-label">' + label + ' (' + data.covered + '/' + data.total_controls + ')</div></div>';
    }
    html += '</div>';
    html += '<table class="data-table"><thead><tr><th>NIST 800-53</th><th>FedRAMP</th><th>CMMC L2</th><th>Description</th><th>Status</th></tr></thead><tbody>';
    (d.crosswalk_matrix || []).forEach(row => {
      const sc = row.covered ? '#27ae60' : '#e74c3c';
      html += '<tr><td style="font-weight:bold;">' + row.nist + '</td><td>' + (row.fedramp||'N/A') + '</td><td>' + (row.cmmc||'N/A') + '</td><td style="font-size:11px;">' + row.description + '</td><td style="color:' + sc + ';font-weight:bold;">' + (row.covered ? 'Covered' : 'Gap') + '</td></tr>';
    });
    html += '</tbody></table>';
    document.getElementById('assess-content').innerHTML = html;
  });
}

function llmThreats() {
  const id = window._designId || DESIGN_ID;
  if (!id || id === 'new') { alert('Save the design first.'); return; }
  const statusEl = document.getElementById('tb-status');
  if (statusEl) statusEl.textContent = 'Analyzing threats (LLM)...';
  fetch(SC_BASE + '/api/designs/' + id + '/llm-threats', {method:'POST'}).then(r=>r.json()).then(d => {
    if (statusEl) statusEl.textContent = 'Ready';
    const panel = document.getElementById('assessment-panel');
    panel.classList.remove('hidden');
    const src = d.source === 'ollama' ? 'LLM (Ollama)' : 'Deterministic fallback';
    document.getElementById('assess-title').textContent = 'AI Threat Analysis \u2014 ' + (d.total_threats||0) + ' threats (' + src + ')';
    let html = '';
    if (d.error) html += '<div style="background:rgba(243,156,18,0.1);border:1px solid #f39c12;border-radius:4px;padding:8px;margin-bottom:8px;font-size:12px;color:#f39c12;">Note: ' + d.error + '</div>';
    if (d.threats && d.threats.length) {
      html += '<table class="data-table"><thead><tr><th>Cat</th><th>Title</th><th>Description</th><th>Affected</th><th>Control</th></tr></thead><tbody>';
      d.threats.forEach(t => {
        html += '<tr><td style="color:#e74c3c;font-weight:bold;">' + (t.category||'?') + '</td>';
        html += '<td style="font-weight:bold;">' + (t.title||'') + '</td>';
        html += '<td style="font-size:11px;">' + (t.description||'') + '</td>';
        html += '<td style="font-size:11px;">' + (t.affected||t.affected_assets||'') + '</td>';
        html += '<td style="color:#27ae60;font-size:11px;">' + (t.nist_control||(t.nist_controls||[]).join(',')||'') + '</td></tr>';
      });
      html += '</tbody></table>';
    }
    document.getElementById('assess-content').innerHTML = html;
  }).catch(() => { if (statusEl) statusEl.textContent = 'Ready'; });
}
