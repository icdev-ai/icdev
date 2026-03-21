/**
 * ICDEV Studio — Workflow Studio (DAG Editor)
 * Vanilla JS + SVG canvas for drag-and-drop workflow design.
 */

const StudioWF = (() => {
  // ── State ──
  let nodes = [];
  let edges = [];
  let selectedNode = null;
  let dragState = null; // {nodeId, offsetX, offsetY} or {type:'tool', data}
  let connectState = null; // {fromId, fromPort}
  let zoom = 1;
  let nextNodeY = 60;
  const GRID = 24;

  // ── DOM refs ──
  const $ = id => document.getElementById(id);
  const canvas = () => $('wf-canvas');
  const nodesEl = () => $('wf-nodes');
  const edgesSvg = () => $('wf-edges');
  const emptyState = () => $('wf-empty-state');

  // ── Toast helper ──
  function toast(msg, type = 'info') {
    const container = $('studio-toasts');
    const el = document.createElement('div');
    el.className = `studio-toast studio-toast--${type}`;
    el.innerHTML = `
      <div class="studio-toast__message">${msg}</div>
      <button class="studio-toast__dismiss" onclick="this.parentElement.remove()">&times;</button>
    `;
    container.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  // ── Snap to grid ──
  function snap(v) { return Math.round(v / GRID) * GRID; }

  // ── Load tool catalog ──
  async function loadCatalog() {
    try {
      const resp = await fetch('/api/studio/tools/catalog');
      const data = await resp.json();
      renderPalette(data);
    } catch (e) {
      console.error('Failed to load catalog:', e);
      renderPaletteFallback();
    }
  }

  function renderPalette(catalog) {
    const container = $('wf-palette-groups');
    let html = '';
    for (const [catId, cat] of Object.entries(catalog)) {
      html += `<div class="wf-studio__palette-group" data-category="${catId}">`;
      html += `<div class="wf-studio__palette-group-title">${cat.label}</div>`;
      for (const tool of cat.tools) {
        html += `
          <div class="wf-studio__tool-item"
               draggable="true"
               data-tool-id="${tool.id}"
               data-tool-name="${tool.name}"
               data-tool-path="${tool.tool}"
               data-tool-desc="${tool.description || ''}"
               data-tool-color="${cat.color}"
               ondragstart="StudioWF.handleToolDragStart(event, this)">
            <div class="wf-studio__tool-icon wf-studio__tool-icon--${cat.color}">
              ${iconFor(cat.color)}
            </div>
            <span>${tool.name}</span>
          </div>`;
      }
      html += '</div>';
    }
    container.innerHTML = html;
  }

  function renderPaletteFallback() {
    const container = $('wf-palette-groups');
    container.innerHTML = `
      <div class="studio-empty" style="padding:24px 16px;">
        <div class="studio-text-muted" style="font-size:0.75rem;">
          Could not load tool catalog.<br>API may not be running.
        </div>
      </div>`;
  }

  function iconFor(color) {
    const icons = {
      compliance: '&#128737;', ai_security: '&#129504;', security: '&#128274;',
      build: '&#128296;', testing: '&#9989;', code_intel: '&#128269;',
      deploy: '&#9729;', devsecops: '&#128737;&#128274;', requirements: '&#128203;',
      mbse: '&#128208;', modernization: '&#128260;', maintenance: '&#128295;',
      monitoring: '&#128200;', analysis: '&#128161;', knowledge: '&#128218;',
      govcon: '&#128188;', test: '&#9888;'
    };
    return icons[color] || '&#9632;';
  }

  // ── Tool search ──
  function filterTools(query) {
    const q = query.toLowerCase();
    document.querySelectorAll('.wf-studio__tool-item').forEach(el => {
      const name = el.dataset.toolName.toLowerCase();
      const desc = el.dataset.toolDesc.toLowerCase();
      el.style.display = (!q || name.includes(q) || desc.includes(q)) ? '' : 'none';
    });
    // Hide empty groups
    document.querySelectorAll('.wf-studio__palette-group').forEach(group => {
      const visible = group.querySelectorAll('.wf-studio__tool-item[style=""], .wf-studio__tool-item:not([style])');
      group.style.display = visible.length ? '' : 'none';
    });
  }

  // ── Drag & Drop ──
  function handleToolDragStart(e, el) {
    e.dataTransfer.setData('application/json', JSON.stringify({
      toolId: el.dataset.toolId,
      toolName: el.dataset.toolName,
      toolPath: el.dataset.toolPath,
      toolDesc: el.dataset.toolDesc,
      toolColor: el.dataset.toolColor,
    }));
    e.dataTransfer.effectAllowed = 'copy';
  }

  function handleDrop(e) {
    e.preventDefault();
    try {
      const data = JSON.parse(e.dataTransfer.getData('application/json'));
      const canvasRect = canvas().getBoundingClientRect();
      const x = snap((e.clientX - canvasRect.left) / zoom);
      const y = snap((e.clientY - canvasRect.top) / zoom);
      addNode(data, x, y);
    } catch (err) {
      console.warn('Drop parse error:', err);
    }
  }

  // ── Node Management ──
  function addNode(toolData, x, y) {
    const id = 'node-' + Date.now().toString(36);
    const node = {
      id,
      toolId: toolData.toolId,
      name: toolData.toolName,
      tool: toolData.toolPath,
      description: toolData.toolDesc || '',
      color: toolData.toolColor || 'compliance',
      x: x || 60,
      y: y || nextNodeY,
      args: {},
      dependsOn: [],
      timeout: 300,
      required: true,
      status: 'idle',
    };
    nodes.push(node);
    nextNodeY = Math.max(nextNodeY, y + 80);
    renderNode(node);
    updateCounts();
    hideEmptyState();
    return node;
  }

  function renderNode(node) {
    const container = nodesEl();
    const el = document.createElement('div');
    el.className = `wf-node ${node.status !== 'idle' ? 'wf-node--' + node.status : ''}`;
    el.id = node.id;
    el.style.left = node.x + 'px';
    el.style.top = node.y + 'px';
    el.innerHTML = `
      <div class="wf-node__port wf-node__port--input"
           onmousedown="StudioWF.startConnect(event, '${node.id}', 'input')"
           onmouseup="StudioWF.endConnect(event, '${node.id}', 'input')"></div>
      <div class="wf-node__header">
        <div class="wf-node__icon wf-studio__tool-icon--${node.color}">
          ${iconFor(node.color)}
        </div>
        <div class="wf-node__name">${node.name}</div>
        <div class="wf-node__status wf-node__status--${node.status}"></div>
      </div>
      <div class="wf-node__body">${node.description}</div>
      <div class="wf-node__port wf-node__port--output"
           onmousedown="StudioWF.startConnect(event, '${node.id}', 'output')"
           onmouseup="StudioWF.endConnect(event, '${node.id}', 'output')"></div>
    `;

    // Node drag
    el.addEventListener('mousedown', (e) => {
      if (e.target.classList.contains('wf-node__port')) return;
      e.preventDefault();
      selectNode(node.id);
      const rect = el.getBoundingClientRect();
      dragState = { nodeId: node.id, offsetX: e.clientX - rect.left, offsetY: e.clientY - rect.top };
    });

    // Double-click to configure
    el.addEventListener('dblclick', () => openNodeConfig(node.id));

    container.appendChild(el);
  }

  // ── Node dragging ──
  document.addEventListener('mousemove', (e) => {
    if (!dragState || !dragState.nodeId) return;
    const canvasRect = canvas().getBoundingClientRect();
    const x = snap((e.clientX - canvasRect.left - dragState.offsetX) / zoom);
    const y = snap((e.clientY - canvasRect.top - dragState.offsetY) / zoom);
    const node = nodes.find(n => n.id === dragState.nodeId);
    if (node) {
      node.x = Math.max(0, x);
      node.y = Math.max(0, y);
      const el = $(node.id);
      if (el) {
        el.style.left = node.x + 'px';
        el.style.top = node.y + 'px';
      }
      renderEdges();
    }
  });

  document.addEventListener('mouseup', () => {
    dragState = null;
    connectState = null;
  });

  // ── Selection ──
  function selectNode(id) {
    if (selectedNode) {
      const prev = $(selectedNode);
      if (prev) prev.classList.remove('wf-node--selected');
    }
    selectedNode = id;
    const el = $(id);
    if (el) el.classList.add('wf-node--selected');
  }

  // ── Connections ──
  function startConnect(e, nodeId, port) {
    e.stopPropagation();
    if (port === 'output') {
      connectState = { fromId: nodeId };
    }
  }

  function endConnect(e, nodeId, port) {
    e.stopPropagation();
    if (connectState && port === 'input' && connectState.fromId !== nodeId) {
      // Check for duplicate
      const exists = edges.some(
        edge => edge.from === connectState.fromId && edge.to === nodeId
      );
      if (!exists) {
        edges.push({ from: connectState.fromId, to: nodeId });
        // Update dependsOn
        const target = nodes.find(n => n.id === nodeId);
        if (target && !target.dependsOn.includes(connectState.fromId)) {
          target.dependsOn.push(connectState.fromId);
        }
        renderEdges();
        toast('Connection added', 'success');
      }
    }
    connectState = null;
  }

  // ── Edge rendering (SVG) ──
  function renderEdges() {
    const svg = edgesSvg();
    const canvasRect = canvas().getBoundingClientRect();
    svg.setAttribute('width', canvasRect.width);
    svg.setAttribute('height', canvasRect.height);
    let paths = '';
    for (const edge of edges) {
      const fromNode = nodes.find(n => n.id === edge.from);
      const toNode = nodes.find(n => n.id === edge.to);
      if (!fromNode || !toNode) continue;

      const fromEl = $(fromNode.id);
      const toEl = $(toNode.id);
      if (!fromEl || !toEl) continue;

      const x1 = fromNode.x + fromEl.offsetWidth;
      const y1 = fromNode.y + fromEl.offsetHeight / 2;
      const x2 = toNode.x;
      const y2 = toNode.y + toEl.offsetHeight / 2;

      const dx = Math.abs(x2 - x1) * 0.5;
      paths += `<path d="M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}"
                  data-from="${edge.from}" data-to="${edge.to}"/>`;
    }
    svg.innerHTML = paths;
  }

  // ── Node Config Modal ──
  function openNodeConfig(nodeId) {
    const node = nodes.find(n => n.id === nodeId);
    if (!node) return;

    $('wf-modal-title').textContent = `Configure: ${node.name}`;
    $('wf-modal-body').innerHTML = `
      <div class="studio-form-group">
        <label class="studio-label">Step Name</label>
        <input type="text" class="studio-input" id="cfg-name" value="${node.name}">
      </div>
      <div class="studio-form-group">
        <label class="studio-label">Description</label>
        <input type="text" class="studio-input" id="cfg-desc" value="${node.description}">
      </div>
      <div class="studio-form-group">
        <label class="studio-label">Tool Path</label>
        <input type="text" class="studio-input" id="cfg-tool" value="${node.tool}" readonly
               style="opacity:0.7;">
      </div>
      <div class="studio-grid studio-grid--2">
        <div class="studio-form-group">
          <label class="studio-label">Timeout (seconds)</label>
          <input type="number" class="studio-input" id="cfg-timeout" value="${node.timeout}">
        </div>
        <div class="studio-form-group">
          <label class="studio-label">Required</label>
          <select class="studio-input studio-select" id="cfg-required">
            <option value="true" ${node.required ? 'selected' : ''}>Yes</option>
            <option value="false" ${!node.required ? 'selected' : ''}>No (skip on failure)</option>
          </select>
        </div>
      </div>
      <div class="studio-form-group">
        <label class="studio-label">Custom Arguments (JSON)</label>
        <textarea class="studio-input studio-textarea" id="cfg-args" rows="4"
                  style="font-family:var(--studio-font-mono);font-size:0.8rem;"
                  placeholder='{"categorize": true}'>${JSON.stringify(node.args, null, 2)}</textarea>
      </div>
      <div style="margin-top:12px;">
        <button class="studio-btn studio-btn--danger studio-btn--sm"
                onclick="StudioWF.deleteNode('${nodeId}')">
          &#128465; Delete Step
        </button>
      </div>
    `;
    $('wf-node-modal').dataset.nodeId = nodeId;
    $('wf-node-modal').style.display = '';
  }

  function closeModal() {
    $('wf-node-modal').style.display = 'none';
  }

  function saveNodeConfig() {
    const nodeId = $('wf-node-modal').dataset.nodeId;
    const node = nodes.find(n => n.id === nodeId);
    if (!node) return;

    node.name = $('cfg-name').value;
    node.description = $('cfg-desc').value;
    node.timeout = parseInt($('cfg-timeout').value, 10) || 300;
    node.required = $('cfg-required').value === 'true';
    try {
      node.args = JSON.parse($('cfg-args').value || '{}');
    } catch (e) {
      toast('Invalid JSON in arguments', 'error');
      return;
    }

    // Re-render node
    const el = $(nodeId);
    if (el) {
      el.querySelector('.wf-node__name').textContent = node.name;
      el.querySelector('.wf-node__body').textContent = node.description;
    }

    closeModal();
    toast('Step configured', 'success');
  }

  function deleteNode(nodeId) {
    nodes = nodes.filter(n => n.id !== nodeId);
    edges = edges.filter(e => e.from !== nodeId && e.to !== nodeId);
    nodes.forEach(n => {
      n.dependsOn = n.dependsOn.filter(d => d !== nodeId);
    });
    const el = $(nodeId);
    if (el) el.remove();
    renderEdges();
    updateCounts();
    closeModal();
    if (nodes.length === 0) showEmptyState();
    toast('Step removed', 'info');
  }

  // ── Helpers ──
  function updateCounts() {
    $('wf-node-count').textContent = `${nodes.length} step${nodes.length !== 1 ? 's' : ''}`;
  }

  function hideEmptyState() {
    const es = emptyState();
    if (es) es.style.display = 'none';
  }

  function showEmptyState() {
    const es = emptyState();
    if (es) es.style.display = '';
  }

  // ── Tabs ──
  function switchTab(el) {
    document.querySelectorAll('.studio-tab').forEach(t => t.classList.remove('studio-tab--active'));
    el.classList.add('studio-tab--active');
    document.querySelectorAll('.studio-tab-panel').forEach(p => p.style.display = 'none');
    const panel = $('panel-' + el.dataset.tab);
    if (panel) panel.style.display = '';

    if (el.dataset.tab === 'saved') loadSavedWorkflows();
    if (el.dataset.tab === 'templates') loadTemplates();
  }

  // ── Save ──
  async function save() {
    const name = $('wf-name').value.trim() || 'Untitled Workflow';
    const yamlStr = exportToYAML();

    try {
      const resp = await fetch('/api/studio/workflows', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          template_yaml: yamlStr,
          description: `${nodes.length} steps`,
          category: 'custom',
        }),
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        toast(`Workflow "${name}" saved`, 'success');
      } else {
        toast(data.error || 'Save failed', 'error');
      }
    } catch (e) {
      toast('Network error saving workflow', 'error');
    }
  }

  // ── Export YAML ──
  function exportToYAML() {
    const steps = nodes.map(n => {
      const step = { id: n.toolId + '-' + n.id.slice(-6), name: n.name, tool: n.tool };
      const deps = edges.filter(e => e.to === n.id).map(e => {
        const fromNode = nodes.find(nn => nn.id === e.from);
        return fromNode ? fromNode.toolId + '-' + fromNode.id.slice(-6) : null;
      }).filter(Boolean);
      if (deps.length) step.depends_on = deps;
      if (Object.keys(n.args).length) step.args = n.args;
      if (n.timeout !== 300) step.timeout = n.timeout;
      if (!n.required) step.required = false;
      if (n.description) step.description = n.description;
      return step;
    });

    // Simple YAML serialization (no library dependency)
    let yaml = `description: "${$('wf-name').value.trim() || 'Untitled Workflow'}"\n`;
    yaml += `category: "custom"\n`;
    yaml += `steps:\n`;
    for (const s of steps) {
      yaml += `  - id: "${s.id}"\n`;
      yaml += `    name: "${s.name}"\n`;
      yaml += `    tool: "${s.tool}"\n`;
      if (s.depends_on) yaml += `    depends_on: [${s.depends_on.map(d => `"${d}"`).join(', ')}]\n`;
      if (s.args) yaml += `    args: ${JSON.stringify(s.args)}\n`;
      if (s.timeout) yaml += `    timeout: ${s.timeout}\n`;
      if (s.required === false) yaml += `    required: false\n`;
      if (s.description) yaml += `    description: "${s.description}"\n`;
    }
    return yaml;
  }

  function exportYAML() {
    const yaml = exportToYAML();
    const blob = new Blob([yaml], { type: 'text/yaml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = ($('wf-name').value.trim() || 'workflow').replace(/\s+/g, '_') + '.yaml';
    a.click();
    URL.revokeObjectURL(url);
    toast('YAML exported', 'success');
  }

  // ── Import YAML ──
  function importYAML() {
    $('wf-yaml-modal').style.display = '';
  }

  function doImportYAML() {
    const raw = $('wf-yaml-input').value.trim();
    if (!raw) { toast('Paste YAML first', 'warning'); return; }

    try {
      // Basic YAML parsing (steps extraction)
      const lines = raw.split('\n');
      let inSteps = false;
      let currentStep = null;
      const steps = [];

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('description:')) {
          const match = trimmed.match(/description:\s*"?(.+?)"?\s*$/);
          if (match) $('wf-name').value = match[1];
        }
        if (trimmed === 'steps:') { inSteps = true; continue; }
        if (!inSteps) continue;

        if (trimmed.startsWith('- id:')) {
          if (currentStep) steps.push(currentStep);
          currentStep = { id: trimmed.replace('- id:', '').trim().replace(/"/g, '') };
        } else if (currentStep) {
          const kv = trimmed.match(/^(\w+):\s*(.+)/);
          if (kv) currentStep[kv[1]] = kv[2].replace(/"/g, '').trim();
        }
      }
      if (currentStep) steps.push(currentStep);

      if (!steps.length) { toast('No steps found in YAML', 'error'); return; }

      // Clear canvas
      nodes = []; edges = [];
      nodesEl().innerHTML = '';
      edgesSvg().innerHTML = '';

      // Add nodes
      let y = 60;
      for (const s of steps) {
        addNode({
          toolId: s.id,
          toolName: s.name || s.id,
          toolPath: s.tool || '',
          toolDesc: s.description || '',
          toolColor: guessColor(s.tool || ''),
        }, 120, y);
        y += 100;
      }

      $('wf-yaml-modal').style.display = 'none';
      toast(`Imported ${steps.length} steps`, 'success');
    } catch (e) {
      toast('Parse error: ' + e.message, 'error');
    }
  }

  function guessColor(toolPath) {
    if (toolPath.includes('compliance')) return 'compliance';
    if (toolPath.includes('security')) return 'security';
    if (toolPath.includes('build') || toolPath.includes('test')) return 'build';
    if (toolPath.includes('infra') || toolPath.includes('deploy')) return 'deploy';
    if (toolPath.includes('analysis') || toolPath.includes('research')) return 'analysis';
    return 'compliance';
  }

  // ── Saved workflows ──
  async function loadSavedWorkflows() {
    try {
      const resp = await fetch('/api/studio/workflows');
      const data = await resp.json();
      const grid = $('wf-saved-grid');
      $('wf-saved-count').textContent = data.workflows.length;

      if (!data.workflows.length) {
        grid.innerHTML = `
          <div class="studio-empty" style="grid-column:1/-1;">
            <div class="studio-empty__icon">&#128451;</div>
            <div class="studio-empty__title">No saved workflows</div>
            <div class="studio-empty__description">Create your first workflow in the editor tab.</div>
          </div>`;
        return;
      }

      grid.innerHTML = data.workflows.map((wf, i) => `
        <div class="studio-card studio-card--interactive studio-animate-in" style="animation-delay:${i * 40}ms;">
          <div class="studio-card__header">
            <div class="studio-card__header-title">${esc(wf.name)}</div>
            <span class="studio-badge studio-badge--accent">v${wf.version}</span>
          </div>
          <div class="studio-card__body">
            <div class="studio-text-muted" style="font-size:0.8rem;margin-bottom:12px;">
              ${esc(wf.description || 'No description')}
            </div>
            <div class="studio-flex studio-items-center studio-gap-2">
              <span class="studio-tag">${esc(wf.category || 'custom')}</span>
              ${wf.shared ? '<span class="studio-badge studio-badge--info">Shared</span>' : ''}
            </div>
          </div>
          <div class="studio-card__footer">
            <span class="studio-text-muted" style="font-size:0.7rem;">
              ${new Date(wf.updated_at).toLocaleDateString()}
            </span>
            <button class="studio-btn studio-btn--secondary studio-btn--sm"
                    onclick="StudioWF.loadWorkflow('${wf.workflow_id}')">
              Open
            </button>
          </div>
        </div>
      `).join('');
    } catch (e) {
      console.error(e);
    }
  }

  // ── Templates ──
  async function loadTemplates() {
    try {
      const resp = await fetch('/api/studio/templates');
      const data = await resp.json();
      const grid = $('wf-template-grid');
      const templates = data.templates || [];

      $('tpl-builtin-count').textContent = templates.length;
      $('tpl-community-count').textContent = '0';
      const cats = new Set(templates.map(t => t.category));
      $('tpl-category-count').textContent = cats.size;

      if (!templates.length) {
        grid.innerHTML = `
          <div class="studio-empty" style="grid-column:1/-1;">
            <div class="studio-empty__icon">&#128218;</div>
            <div class="studio-empty__title">No templates found</div>
            <div class="studio-empty__description">
              Add YAML files to args/workflow_templates/ to see them here.
            </div>
          </div>`;
        return;
      }

      grid.innerHTML = templates.map((tpl, i) => `
        <div class="mkt-asset studio-animate-in" style="animation-delay:${i * 40}ms;">
          <div class="mkt-asset__banner" style="background: ${catGradient(tpl.category)};"></div>
          <div class="mkt-asset__body">
            <div class="mkt-asset__icon" style="background:${catBg(tpl.category)};color:${catFg(tpl.category)};">
              ${catIcon(tpl.category)}
            </div>
            <div class="mkt-asset__name">${esc(tpl.name)}</div>
            <div class="mkt-asset__description">${tpl.steps_count} steps &middot; ${esc(tpl.category)}</div>
            <div class="mkt-asset__tags">
              <span class="studio-tag">${esc(tpl.category)}</span>
              <span class="studio-tag studio-tag--accent">built-in</span>
            </div>
          </div>
          <div class="mkt-asset__footer">
            <span class="mkt-asset__stat">${tpl.steps_count} steps</span>
            <button class="studio-btn studio-btn--primary studio-btn--sm"
                    onclick="StudioWF.useTemplate('${esc(tpl.id)}')">
              Use Template
            </button>
          </div>
        </div>
      `).join('');
    } catch (e) {
      console.error(e);
    }
  }

  function catGradient(cat) {
    const m = {
      compliance: 'linear-gradient(135deg,#6366f1,#8b5cf6)',
      ai_security: 'linear-gradient(135deg,#ec4899,#f472b6)',
      security: 'linear-gradient(135deg,#ef4444,#f97316)',
      build: 'linear-gradient(135deg,#22c55e,#10b981)',
      testing: 'linear-gradient(135deg,#f59e0b,#eab308)',
      code_intel: 'linear-gradient(135deg,#0ea5e9,#38bdf8)',
      deploy: 'linear-gradient(135deg,#3b82f6,#2563eb)',
      devsecops: 'linear-gradient(135deg,#8b5cf6,#a78bfa)',
      requirements: 'linear-gradient(135deg,#14b8a6,#2dd4bf)',
      mbse: 'linear-gradient(135deg,#06b6d4,#22d3ee)',
      modernization: 'linear-gradient(135deg,#f97316,#fb923c)',
      maintenance: 'linear-gradient(135deg,#71717a,#a1a1aa)',
      monitoring: 'linear-gradient(135deg,#22c55e,#4ade80)',
      analysis: 'linear-gradient(135deg,#a855f7,#c084fc)',
      knowledge: 'linear-gradient(135deg,#f59e0b,#fbbf24)',
      govcon: 'linear-gradient(135deg,#64748b,#94a3b8)',
      general: 'linear-gradient(135deg,#3b82f6,#6366f1)',
    };
    return m[cat] || m.general;
  }

  function catBg(cat) {
    const m = {
      compliance: 'rgba(99,102,241,0.12)', ai_security: 'rgba(236,72,153,0.12)',
      security: 'rgba(239,68,68,0.12)', build: 'rgba(34,197,94,0.12)',
      testing: 'rgba(245,158,11,0.12)', code_intel: 'rgba(14,165,233,0.12)',
      deploy: 'rgba(59,130,246,0.12)', devsecops: 'rgba(139,92,246,0.12)',
      requirements: 'rgba(20,184,166,0.12)', mbse: 'rgba(6,182,212,0.12)',
      modernization: 'rgba(249,115,22,0.12)', maintenance: 'rgba(161,161,170,0.12)',
      monitoring: 'rgba(34,197,94,0.12)', analysis: 'rgba(168,85,247,0.12)',
      knowledge: 'rgba(251,191,36,0.12)', govcon: 'rgba(100,116,139,0.12)',
      general: 'rgba(59,130,246,0.12)',
    };
    return m[cat] || m.general;
  }

  function catFg(cat) {
    const m = {
      compliance: '#818cf8', ai_security: '#f472b6', security: '#ef4444',
      build: '#22c55e', testing: '#f59e0b', code_intel: '#38bdf8',
      deploy: '#3b82f6', devsecops: '#a78bfa', requirements: '#2dd4bf',
      mbse: '#22d3ee', modernization: '#fb923c', maintenance: '#a1a1aa',
      monitoring: '#4ade80', analysis: '#c084fc', knowledge: '#fbbf24',
      govcon: '#94a3b8', general: '#3b82f6',
    };
    return m[cat] || m.general;
  }

  function catIcon(cat) {
    const m = {
      compliance: '&#128737;', ai_security: '&#129504;', security: '&#128274;',
      build: '&#128296;', testing: '&#9989;', code_intel: '&#128269;',
      deploy: '&#9729;', devsecops: '&#128737;', requirements: '&#128203;',
      mbse: '&#128208;', modernization: '&#128260;', maintenance: '&#128295;',
      monitoring: '&#128200;', analysis: '&#128161;', knowledge: '&#128218;',
      govcon: '&#128188;', general: '&#9881;',
    };
    return m[cat] || m.general;
  }

  // ── Zoom ──
  function zoomIn() { zoom = Math.min(2, zoom + 0.1); applyZoom(); }
  function zoomOut() { zoom = Math.max(0.3, zoom - 0.1); applyZoom(); }
  function fitView() { zoom = 1; applyZoom(); }

  function applyZoom() {
    const c = nodesEl();
    if (c) c.style.transform = `scale(${zoom})`;
  }

  // ── Validate ──
  function validate() {
    if (!nodes.length) { toast('Add steps before validating', 'warning'); return; }

    const issues = [];
    // Check for cycles (simple)
    const visited = new Set();
    const visiting = new Set();
    function hasCycle(id) {
      if (visiting.has(id)) return true;
      if (visited.has(id)) return false;
      visiting.add(id);
      for (const e of edges.filter(e => e.from === id)) {
        if (hasCycle(e.to)) return true;
      }
      visiting.delete(id);
      visited.add(id);
      return false;
    }
    for (const n of nodes) {
      if (hasCycle(n.id)) { issues.push('Circular dependency detected'); break; }
    }

    // Check disconnected nodes
    const connected = new Set();
    edges.forEach(e => { connected.add(e.from); connected.add(e.to); });
    if (nodes.length > 1) {
      const disconnected = nodes.filter(n => !connected.has(n.id));
      if (disconnected.length) {
        issues.push(`${disconnected.length} disconnected step(s)`);
      }
    }

    if (issues.length) {
      toast('Validation: ' + issues.join('; '), 'warning');
    } else {
      toast('Workflow is valid', 'success');
    }
  }

  // ── Utility ──
  function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
  function createNew() {
    nodes = []; edges = []; selectedNode = null; nextNodeY = 60;
    nodesEl().innerHTML = '';
    edgesSvg().innerHTML = '';
    $('wf-name').value = 'Untitled Workflow';
    updateCounts();
    showEmptyState();
    // Switch to editor tab
    const editorTab = document.querySelector('[data-tab="editor"]');
    if (editorTab) switchTab(editorTab);
    toast('New workflow created', 'info');
  }

  function loadTemplate() {
    const tplTab = document.querySelector('[data-tab="templates"]');
    if (tplTab) switchTab(tplTab);
  }

  // Stubs for future features
  function undo() { toast('Undo not yet implemented', 'info'); }
  function redo() { toast('Redo not yet implemented', 'info'); }
  function loadWorkflow(id) { toast('Loading workflow...', 'info'); }
  function useTemplate(id) { toast('Loading template...', 'info'); }

  // ── Init ──
  function init() {
    loadCatalog();
    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Delete' && selectedNode) deleteNode(selectedNode);
      if (e.key === 'Escape') { closeModal(); connectState = null; }
    });
  }

  document.addEventListener('DOMContentLoaded', init);

  // ── Public API ──
  return {
    handleToolDragStart, handleDrop, filterTools,
    startConnect, endConnect,
    switchTab, save, exportYAML, importYAML, doImportYAML,
    validate, createNew, loadTemplate, loadWorkflow, useTemplate,
    openNodeConfig: openNodeConfig, closeModal, saveNodeConfig, deleteNode,
    zoomIn, zoomOut, fitView, undo, redo,
  };
})();
