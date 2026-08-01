/**
 * ICDEV™ Studio — Workflow Studio (DAG Editor)
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
  let panX = 0, panY = 0;
  let isPanning = false, panStart = null;
  let nextNodeY = 60;
  let _nodeSeq = 0;
  const GRID = 24;
  let _rolesCache = null;
  let _docTemplatesCache = null;
  let _navStack = [];  // [{nodes, edges, name, parentNodeId}]
  let _contextMenu = null;

  // ── DOM refs ──
  const $ = id => document.getElementById(id);
  const canvas = () => $('wf-canvas');
  const world  = () => $('wf-world');
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
      html += `<div class="wf-studio__palette-group wf-palette-group--collapsed" data-category="${catId}">`;
      html += `<div class="wf-studio__palette-group-title" onclick="StudioWF.toggleGroup(this)">
        <span>${cat.label}</span>
        <span class="wf-group-chevron">&#9656;</span>
      </div>`;
      html += `<div class="wf-palette-group__items">`;
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
      html += `</div>`;
      html += '</div>';
    }
    container.innerHTML = html;
  }

  function toggleGroup(titleEl) {
    const group = titleEl.closest('.wf-studio__palette-group');
    group.classList.toggle('wf-palette-group--collapsed');
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
      govcon: '&#128188;', telecom: '&#128225;', test: '&#9888;'
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
    document.querySelectorAll('.wf-studio__palette-group').forEach(group => {
      const visible = group.querySelectorAll('.wf-studio__tool-item:not([style="display: none;"])');
      const hasMatch = visible.length > 0;
      group.style.display = hasMatch ? '' : 'none';
      // Auto-expand when searching, collapse back when cleared
      if (q && hasMatch) {
        group.classList.remove('wf-palette-group--collapsed');
      } else if (!q) {
        group.classList.add('wf-palette-group--collapsed');
      }
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
      const x = snap((e.clientX - canvasRect.left - panX) / zoom);
      const y = snap((e.clientY - canvasRect.top  - panY) / zoom);
      addNode(data, x, y);
    } catch (err) {
      console.warn('Drop parse error:', err);
    }
  }

  // ── Node Management ──
  function addNode(toolData, x, y) {
    const id = 'node-' + Date.now().toString(36) + '-' + (++_nodeSeq).toString(36);
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
      node_type: toolData.node_type || 'tool',
      role: toolData.role || null,
      human_required: toolData.human_required || false,
      approval_policy: toolData.approval_policy || null,
      doc_template: toolData.doc_template || null,
      mcp_tool: toolData.mcp_tool || null,
      mcp_params: toolData.mcp_params || {},
      sub_steps: toolData.sub_steps || [],
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
    el.dataset.nodeType = node.node_type || 'tool';
    if (node.role) el.dataset.role = node.role;
    if (node.approval_policy) el.dataset.approvalPolicy = node.approval_policy;
    const hasSubSteps = true;  // always show decompose button on every node
    el.dataset.hasSubSteps = node.sub_steps && node.sub_steps.length > 0 ? 'true' : 'false';
    let badgeHtml = '';
    if (node.node_type === 'human' && node.role) {
      badgeHtml = `<div class="wf-node__badge">Human | ${node.role}</div>`;
    } else if (node.node_type === 'approval' && node.approval_policy) {
      badgeHtml = `<div class="wf-node__badge">Approval | ${node.approval_policy}</div>`;
    } else if (node.node_type === 'mcp' && node.mcp_tool) {
      badgeHtml = `<div class="wf-node__badge">MCP | ${node.mcp_tool}</div>`;
    }

    el.innerHTML = `
      <div class="wf-node__port wf-node__port--input"
           onmousedown="StudioWF.startConnect(event, '${node.id}', 'input')"
           onmouseup="StudioWF.endConnect(event, '${node.id}', 'input')"></div>
      ${hasSubSteps ? `<button class="wf-node__expand" onclick="event.stopPropagation();StudioWF.drillInto('${node.id}')" title="Expand sub-steps" style="position:absolute;top:4px;right:4px;background:transparent;border:1px solid var(--studio-border,#2d3047);border-radius:3px;width:20px;height:20px;font-size:13px;cursor:pointer;color:var(--studio-accent,#6366f1);padding:0;display:flex;align-items:center;justify-content:center;z-index:1;">⊞</button>` : ''}
      <div class="wf-node__header">
        <div class="wf-node__icon wf-studio__tool-icon--${node.color}">
          ${iconFor(node.color)}
        </div>
        <div class="wf-node__name">${node.name}</div>
        ${badgeHtml}
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

    // Right-click context menu
    el.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      showContextMenu(e.clientX, e.clientY, node.id);
    });

    container.appendChild(el);
  }

  // ── Context Menu ──
  function showContextMenu(x, y, nodeId) {
    hideContextMenu();
    const menu = document.createElement('div');
    menu.id = 'wf-context-menu';
    menu.style.cssText = `position:fixed;left:${x}px;top:${y}px;z-index:9999;` +
      `background:var(--studio-bg-elevated,#1e2030);border:1px solid var(--studio-border,#2d3047);` +
      `border-radius:6px;padding:4px 0;box-shadow:0 8px 32px rgba(0,0,0,0.4);min-width:160px;`;
    menu.innerHTML = `<div style="padding:8px 16px;cursor:pointer;font-size:0.85rem;` +
      `color:var(--studio-text,#e2e8f0);"` +
      ` onmouseenter="this.style.background='rgba(99,102,241,0.15)'"` +
      ` onmouseleave="this.style.background=''"` +
      ` onclick="StudioWF.drillInto('${nodeId}');StudioWF.hideContextMenu();">` +
      `⊞ Decompose node</div>`;
    document.body.appendChild(menu);
    _contextMenu = menu;
    setTimeout(() => document.addEventListener('click', hideContextMenu, { once: true }), 0);
  }

  function hideContextMenu() {
    if (_contextMenu) { _contextMenu.remove(); _contextMenu = null; }
  }

  // ── Sub-canvas navigation ──
  function drillInto(nodeId) {
    const node = nodes.find(n => n.id === nodeId);
    if (!node) return;
    hideContextMenu();

    _navStack.push({
      nodes: nodes.map(n => Object.assign({}, n, { sub_steps: (n.sub_steps || []).slice() })),
      edges: edges.map(e => Object.assign({}, e)),
      name: $('wf-name').value,
      parentNodeId: nodeId,
    });

    nodes = []; edges = []; selectedNode = null; nextNodeY = 60;
    nodesEl().innerHTML = '';
    edgesSvg().innerHTML = '';

    if (node.sub_steps && node.sub_steps.length > 0) {
      const layoutSteps = node.sub_steps.map(s => ({ id: s.id, depends_on: s.dependsOn || [] }));
      const positions = computeDagLayout(layoutSteps);
      const idMap = {};
      for (const sub of node.sub_steps) {
        const pos = positions[sub.id] || { x: 60, y: nextNodeY };
        const n = addNode({
          toolId: sub.id,
          toolName: sub.name,
          toolPath: sub.tool || '',
          toolDesc: sub.description || '',
          toolColor: sub.color || guessColor(sub.tool || ''),
          node_type: sub.node_type || 'tool',
          role: sub.role || null,
          human_required: sub.human_required || false,
          approval_policy: sub.approval_policy || null,
          doc_template: sub.doc_template || null,
          mcp_tool: sub.mcp_tool || null,
          mcp_params: sub.mcp_params || {},
          sub_steps: sub.sub_steps || [],
        }, pos.x, pos.y);
        idMap[sub.id] = n.id;
      }
      for (const sub of node.sub_steps) {
        for (const dep of (sub.dependsOn || [])) {
          const fromId = idMap[dep], toId = idMap[sub.id];
          if (fromId && toId && !edges.some(e => e.from === fromId && e.to === toId)) {
            edges.push({ from: fromId, to: toId });
            const target = nodes.find(n => n.id === toId);
            if (target && !target.dependsOn.includes(fromId)) target.dependsOn.push(fromId);
          }
        }
      }
      requestAnimationFrame(() => requestAnimationFrame(() => renderEdges()));
      toast(`Drilling into “${node.name}” — ${node.sub_steps.length} sub-steps`, 'info');
    } else {
      showEmptyState();
      toast(`Sub-canvas for “${node.name}” — add steps to build sub-workflow`, 'info');
    }

    $('wf-name').value = node.name;
    updateCounts();
    renderBreadcrumb();
  }

  function drillBack() {
    if (!_navStack.length) return;
    const frame = _navStack.pop();

    if (frame.parentNodeId) {
      const parentNode = frame.nodes.find(n => n.id === frame.parentNodeId);
      if (parentNode) {
        const idReverseMap = {};
        nodes.forEach(n => { idReverseMap[n.id] = n.toolId || n.id; });
        parentNode.sub_steps = nodes.map(n => ({
          id: n.toolId || n.id,
          name: n.name,
          tool: n.tool,
          description: n.description,
          color: n.color,
          node_type: n.node_type,
          role: n.role,
          human_required: n.human_required,
          approval_policy: n.approval_policy,
          doc_template: n.doc_template,
          mcp_tool: n.mcp_tool,
          mcp_params: n.mcp_params,
          args: n.args,
          timeout: n.timeout,
          required: n.required,
          dependsOn: n.dependsOn.map(cid => idReverseMap[cid] || cid),
          sub_steps: n.sub_steps || [],
        }));
      }
    }

    nodes = frame.nodes; edges = frame.edges; selectedNode = null;
    nodesEl().innerHTML = '';
    edgesSvg().innerHTML = '';
    nodes.forEach(n => renderNode(n));
    $('wf-name').value = frame.name;
    updateCounts();
    if (nodes.length === 0) showEmptyState(); else hideEmptyState();
    renderBreadcrumb();
    requestAnimationFrame(() => requestAnimationFrame(() => renderEdges()));
  }

  function drillBackTo(stackIndex) {
    const popCount = _navStack.length - stackIndex;
    for (let k = 0; k < popCount; k++) drillBack();
  }

  function renderBreadcrumb() {
    let crumbEl = $('wf-breadcrumb');
    if (!crumbEl) {
      crumbEl = document.createElement('div');
      crumbEl.id = 'wf-breadcrumb';
      crumbEl.style.cssText = `display:flex;align-items:center;gap:6px;padding:4px 12px;` +
        `background:var(--studio-bg-elevated,#1e2030);border-bottom:1px solid var(--studio-border,#2d3047);` +
        `font-size:0.8rem;color:var(--studio-text-muted,#94a3b8);flex-wrap:wrap;`;
      const canvasEl = canvas();
      if (canvasEl && canvasEl.parentNode) canvasEl.parentNode.insertBefore(crumbEl, canvasEl);
    }
    if (!_navStack.length) { crumbEl.style.display = 'none'; return; }
    crumbEl.style.display = 'flex';
    let html = _navStack.map((f, i) =>
      `<span style="cursor:pointer;color:var(--studio-accent,#6366f1);" onclick="StudioWF.drillBackTo(${i})">${esc(f.name || 'Root')}</span>` +
      `<span style="opacity:0.5;">›</span>`
    ).join('');
    const nameEl = $('wf-name');
    html += `<span>${esc(nameEl ? nameEl.value : '')}</span>`;
    html += `<button onclick="StudioWF.drillBack()" style="margin-left:auto;padding:2px 8px;` +
      `font-size:0.75rem;background:transparent;border:1px solid var(--studio-border,#2d3047);` +
      `border-radius:4px;color:var(--studio-text,#e2e8f0);cursor:pointer;">↩ Back</button>`;
    crumbEl.innerHTML = html;
  }

  // ── Node dragging / panning ──
  document.addEventListener('mousemove', (e) => {
    // Canvas pan
    if (isPanning) {
      panX = e.clientX - panStart.x;
      panY = e.clientY - panStart.y;
      applyZoom();
      return;
    }
    // Node drag
    if (dragState && dragState.nodeId) {
      const canvasRect = canvas().getBoundingClientRect();
      const x = snap((e.clientX - canvasRect.left - panX - dragState.offsetX) / zoom);
      const y = snap((e.clientY - canvasRect.top  - panY - dragState.offsetY) / zoom);
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
    }
    // Rubber-band connection line
    if (connectState) {
      const fromEl = $(connectState.fromId);
      if (!fromEl) return;
      const fromNode = nodes.find(n => n.id === connectState.fromId);
      if (!fromNode) return;
      const canvasEl = canvas();
      const canvasRect = canvasEl.getBoundingClientRect();
      const fromW = fromEl.offsetWidth || 200;
      const fromH = fromEl.offsetHeight || 72;
      const x1 = fromNode.x + fromW - 8;
      const y1 = fromNode.y + fromH / 2;
      const x2 = (e.clientX - canvasRect.left - panX) / zoom;
      const y2 = (e.clientY - canvasRect.top  - panY) / zoom;
      const dx = Math.max(Math.abs(x2 - x1) * 0.5, 60);
      const svg = edgesSvg();
      // Ensure SVG covers cursor position
      const svgW = Math.max(svg.getAttribute('width') || 0, x2 + 40);
      const svgH = Math.max(svg.getAttribute('height') || 0, y2 + 40);
      svg.setAttribute('width', svgW);
      svg.setAttribute('height', svgH);
      // Remove old rubber-band, keep committed edges
      const old = svg.querySelector('.wf-edge--rubber');
      if (old) old.remove();
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('class', 'wf-edge wf-edge--rubber');
      path.setAttribute('d', `M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}`);
      svg.appendChild(path);
    }
  });

  document.addEventListener('mouseup', () => {
    // Remove rubber-band line on any mouseup (endConnect may have already removed it)
    const old = edgesSvg().querySelector('.wf-edge--rubber');
    if (old) old.remove();
    dragState = null;
    connectState = null;
    if (isPanning) {
      isPanning = false;
      panStart = null;
      canvas().classList.remove('is-panning');
    }
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
    const c   = canvas();

    // Cover the full scrollable area so no paths are clipped
    const svgW = Math.max(c.scrollWidth, c.clientWidth, 800);
    const svgH = Math.max(c.scrollHeight, c.clientHeight, 600);
    svg.setAttribute('width', svgW);
    svg.setAttribute('height', svgH);

    // Arrowhead marker + paths
    let html = `
      <defs>
        <marker id="wf-arrowhead" viewBox="0 0 10 10" refX="9" refY="5"
                markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10"
                orient="auto-start-reverse">
          <path d="M 0 1.5 L 9 5 L 0 8.5 Z" class="wf-arrow-head"/>
        </marker>
        <marker id="wf-arrowhead-active" viewBox="0 0 10 10" refX="9" refY="5"
                markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10"
                orient="auto-start-reverse">
          <path d="M 0 1.5 L 9 5 L 0 8.5 Z" class="wf-arrow-head wf-arrow-head--active"/>
        </marker>
      </defs>`;

    for (const edge of edges) {
      const fromNode = nodes.find(n => n.id === edge.from);
      const toNode   = nodes.find(n => n.id === edge.to);
      if (!fromNode || !toNode) continue;

      const fromEl = $(fromNode.id);
      const toEl   = $(toNode.id);
      if (!fromEl || !toEl) continue;

      // Fallback to CSS min-width/height constants if not yet painted
      const fromW = fromEl.offsetWidth  || 200;
      const fromH = fromEl.offsetHeight || 72;
      const toH   = toEl.offsetHeight   || 72;

      // Output port = right-center of source node
      // Input port  = left-center of target node
      const x1 = fromNode.x + fromW - 8;   // -8 pulls back from the port dot edge
      const y1 = fromNode.y + fromH / 2;
      const x2 = toNode.x + 8;              // +8 clears the port dot
      const y2 = toNode.y + toH / 2;
      const dx = Math.max(Math.abs(x2 - x1) * 0.5, 60);

      html += `<path class="wf-edge"
                     d="M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}"
                     marker-end="url(#wf-arrowhead)"
                     data-from="${edge.from}" data-to="${edge.to}"/>`;
    }
    svg.innerHTML = html;
  }

  // ── Role / doc-template lazy fetchers ──
  async function fetchRoles() {
    if (_rolesCache) return _rolesCache;
    try {
      const data = await fetch('/api/studio/roles').then(r => r.json());
      _rolesCache = Array.isArray(data) ? data : (data.roles || []);
    } catch (e) { _rolesCache = []; }
    return _rolesCache;
  }

  async function fetchDocTemplates() {
    if (_docTemplatesCache) return _docTemplatesCache;
    try {
      const data = await fetch('/api/studio/doc-templates').then(r => r.json());
      _docTemplatesCache = Array.isArray(data) ? data : (data.templates || data.doc_templates || []);
    } catch (e) { _docTemplatesCache = []; }
    return _docTemplatesCache;
  }

  function buildRoleOptions(roles, selected) {
    return roles.map(r => {
      const val = typeof r === 'string' ? r : (r.id || r.name || '');
      const lbl = typeof r === 'string' ? r : (r.name || r.id || '');
      return `<option value="${esc(val)}"${selected === val ? ' selected' : ''}>${esc(lbl)}</option>`;
    }).join('');
  }

  function buildDocTplOptions(templates, selected) {
    return templates.map(t => {
      const val = typeof t === 'string' ? t : (t.id || t.name || '');
      const lbl = typeof t === 'string' ? t : `${t.name || t.id}${t.doc_type ? ' (' + t.doc_type + ')' : ''}`;
      return `<option value="${esc(val)}"${selected === val ? ' selected' : ''}>${esc(lbl)}</option>`;
    }).join('');
  }

  function onNodeTypeChange(value) {
    const toolSec = $('cfg-tool-section');
    const humanSec = $('cfg-human-section');
    const approvalSec = $('cfg-approval-section');
    const mcpSec = $('cfg-mcp-section');
    if (!toolSec || !humanSec || !approvalSec || !mcpSec) return;
    toolSec.style.display     = value === 'tool'     ? '' : 'none';
    humanSec.style.display    = value === 'human'    ? '' : 'none';
    approvalSec.style.display = value === 'approval' ? '' : 'none';
    mcpSec.style.display      = value === 'mcp'      ? '' : 'none';
  }

  // ── Node Config Modal ──
  async function openNodeConfig(nodeId) {
    const node = nodes.find(n => n.id === nodeId);
    if (!node) return;
    const nodeType = node.node_type || 'tool';

    let roles = [], docTemplates = [];
    try {
      [roles, docTemplates] = await Promise.all([fetchRoles(), fetchDocTemplates()]);
    } catch (e) { console.warn('Config fetch error:', e); }

    const rolesOpts   = buildRoleOptions(roles, node.role);
    const docTplOpts  = buildDocTplOptions(docTemplates, node.doc_template);

    $('wf-modal-title').textContent = `Configure: ${node.name}`;
    $('wf-modal-body').innerHTML = `
      <div class="studio-form-group">
        <label class="studio-label">Node Type</label>
        <select class="studio-input studio-select" id="cfg-node-type"
                onchange="StudioWF.onNodeTypeChange(this.value)">
          <option value="tool"${nodeType === 'tool' ? ' selected' : ''}>Tool</option>
          <option value="human"${nodeType === 'human' ? ' selected' : ''}>Human Gate</option>
          <option value="approval"${nodeType === 'approval' ? ' selected' : ''}>Approval Gate</option>
          <option value="mcp"${nodeType === 'mcp' ? ' selected' : ''}>MCP Tool</option>
        </select>
      </div>
      <div class="studio-form-group">
        <label class="studio-label">Step Name</label>
        <input type="text" class="studio-input" id="cfg-name" value="${esc(node.name)}">
      </div>
      <div class="studio-form-group">
        <label class="studio-label">Description</label>
        <input type="text" class="studio-input" id="cfg-desc" value="${esc(node.description)}">
      </div>
      <div id="cfg-tool-section">
        <div class="studio-form-group">
          <label class="studio-label">Tool Path</label>
          <input type="text" class="studio-input" id="cfg-tool" value="${esc(node.tool)}" readonly
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
              <option value="true"${node.required ? ' selected' : ''}>Yes</option>
              <option value="false"${!node.required ? ' selected' : ''}>No (skip on failure)</option>
            </select>
          </div>
        </div>
        <div class="studio-form-group">
          <label class="studio-label">Custom Arguments (JSON)</label>
          <textarea class="studio-input studio-textarea" id="cfg-args" rows="4"
                    style="font-family:var(--studio-font-mono);font-size:0.8rem;"
                    placeholder='{"categorize": true}'>${esc(JSON.stringify(node.args, null, 2))}</textarea>
        </div>
      </div>
      <div id="cfg-human-section">
        <div class="studio-form-group">
          <label class="studio-label">Role</label>
          <select class="studio-input studio-select" id="cfg-role">
            <option value="">— select role —</option>
            ${rolesOpts}
          </select>
        </div>
        <div class="studio-form-group">
          <label class="studio-label" style="display:flex;align-items:center;gap:8px;">
            <input type="checkbox" id="cfg-human-required"${node.human_required !== false ? ' checked' : ''}>
            Human Required
          </label>
        </div>
        <div class="studio-form-group">
          <label class="studio-label">Document Template</label>
          <select class="studio-input studio-select" id="cfg-doc-template">
            <option value="">— none —</option>
            ${docTplOpts}
          </select>
        </div>
      </div>
      <div id="cfg-approval-section">
        <div class="studio-form-group">
          <label class="studio-label">Approval Policy</label>
          <select class="studio-input studio-select" id="cfg-approval-policy">
            <option value="any"${node.approval_policy === 'any' ? ' selected' : ''}>Any One</option>
            <option value="all"${node.approval_policy === 'all' ? ' selected' : ''}>All</option>
            <option value="majority"${node.approval_policy === 'majority' ? ' selected' : ''}>Majority</option>
          </select>
        </div>
        <div class="studio-form-group">
          <label class="studio-label">Approver Role</label>
          <select class="studio-input studio-select" id="cfg-approver-role">
            <option value="">— select role —</option>
            ${buildRoleOptions(roles, node.role)}
          </select>
        </div>
      </div>
      <div id="cfg-mcp-section">
        <div class="studio-form-group">
          <label class="studio-label">MCP Tool Name</label>
          <input type="text" class="studio-input" id="cfg-mcp-tool"
                 value="${esc(node.mcp_tool || '')}"
                 placeholder="scan_dependencies">
        </div>
        <div class="studio-form-group">
          <label class="studio-label">MCP Parameters (JSON)</label>
          <textarea class="studio-input studio-textarea" id="cfg-mcp-params" rows="4"
                    style="font-family:var(--studio-font-mono);font-size:0.8rem;"
                    placeholder='{"path": "tools/"}'>${esc(JSON.stringify(node.mcp_params || {}, null, 2))}</textarea>
        </div>
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
    onNodeTypeChange(nodeType);
  }

  function closeModal() {
    $('wf-node-modal').style.display = 'none';
  }

  function saveNodeConfig() {
    const nodeId = $('wf-node-modal').dataset.nodeId;
    const node = nodes.find(n => n.id === nodeId);
    if (!node) return;

    node.name        = $('cfg-name').value;
    node.description = $('cfg-desc').value;
    const nodeType   = $('cfg-node-type').value;
    node.node_type   = nodeType;

    if (nodeType === 'tool') {
      node.timeout  = parseInt($('cfg-timeout').value, 10) || 300;
      node.required = $('cfg-required').value === 'true';
      try {
        node.args = JSON.parse($('cfg-args').value || '{}');
      } catch (e) {
        toast('Invalid JSON in arguments', 'error');
        return;
      }
      node.role            = null;
      node.human_required  = false;
      node.doc_template    = null;
      node.approval_policy = null;
      node.mcp_tool        = null;
      node.mcp_params      = {};
    } else if (nodeType === 'human') {
      node.role            = $('cfg-role').value || null;
      node.human_required  = $('cfg-human-required').checked;
      node.doc_template    = $('cfg-doc-template').value || null;
      node.approval_policy = null;
      node.mcp_tool        = null;
      node.mcp_params      = {};
    } else if (nodeType === 'approval') {
      node.approval_policy = $('cfg-approval-policy').value || null;
      node.role            = $('cfg-approver-role').value || null;
      node.human_required  = false;
      node.doc_template    = null;
      node.mcp_tool        = null;
      node.mcp_params      = {};
    } else if (nodeType === 'mcp') {
      const mcpTool = $('cfg-mcp-tool').value.trim();
      if (!mcpTool) {
        toast('MCP nodes require a tool name', 'error');
        return;
      }
      try {
        node.mcp_params = JSON.parse($('cfg-mcp-params').value || '{}');
      } catch (e) {
        toast('Invalid JSON in MCP parameters', 'error');
        return;
      }
      node.mcp_tool        = mcpTool;
      node.role            = null;
      node.human_required  = false;
      node.doc_template    = null;
      node.approval_policy = null;
    }

    const el = $(nodeId);
    if (el) {
      el.dataset.nodeType = nodeType;
      el.querySelector('.wf-node__name').textContent = node.name;
      el.querySelector('.wf-node__body').textContent = node.description;
      const existing = el.querySelector('.wf-node__badge');
      if (existing) existing.remove();
      let badge = '';
      if (nodeType === 'human' && node.role) {
        badge = `<div class="wf-node__badge">Human | ${esc(node.role)}</div>`;
      } else if (nodeType === 'approval' && node.approval_policy) {
        badge = `<div class="wf-node__badge">Approval | ${esc(node.approval_policy)}</div>`;
      } else if (nodeType === 'mcp' && node.mcp_tool) {
        badge = `<div class="wf-node__badge">MCP | ${esc(node.mcp_tool)}</div>`;
      }
      if (badge) {
        const statusEl = el.querySelector('.wf-node__status');
        if (statusEl) statusEl.insertAdjacentHTML('beforebegin', badge);
      }
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
    if (!es) return;
    if (_navStack.length > 0) {
      es.innerHTML = `
        <div class="studio-empty__icon">&#10753;</div>
        <div class="studio-empty__title">Empty Sub-Canvas</div>
        <div class="studio-empty__description">
          Drag tools from the palette to build the sub-workflow for this step.
        </div>`;
    } else {
      es.innerHTML = `
        <div class="studio-empty__icon">&#128640;</div>
        <div class="studio-empty__title">Start Building</div>
        <div class="studio-empty__description">
          Drag tools from the palette to create workflow steps,
          or start from a template.
        </div>
        <button class="studio-btn studio-btn--primary" onclick="StudioWF.loadTemplate()">
          Browse Templates
        </button>`;
    }
    es.style.display = '';
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

  // ── YAML step serializer (recursive, handles sub_steps) ──
  function serializeStepYAML(s, stepIndent) {
    const sp = ' '.repeat(stepIndent);
    const pp = ' '.repeat(stepIndent + 2);
    let out = `${sp}- id: "${s.id}"\n${pp}name: "${s.name}"\n${pp}tool: "${s.tool || ''}"\n`;
    const deps = s.depends_on || s.dependsOn || [];
    if (deps.length) out += `${pp}depends_on: [${deps.map(d => `"${d}"`).join(', ')}]\n`;
    if (s.args && Object.keys(s.args).length) out += `${pp}args: ${JSON.stringify(s.args)}\n`;
    if (s.timeout && s.timeout !== 300) out += `${pp}timeout: ${s.timeout}\n`;
    if (s.required === false) out += `${pp}required: false\n`;
    if (s.description) out += `${pp}description: "${s.description}"\n`;
    if (s.node_type) out += `${pp}node_type: "${s.node_type}"\n`;
    if (s.role) out += `${pp}role: "${s.role}"\n`;
    if (s.human_required) out += `${pp}human_required: true\n`;
    if (s.approval_policy) out += `${pp}approval_policy: "${s.approval_policy}"\n`;
    if (s.doc_template) out += `${pp}doc_template: "${s.doc_template}"\n`;
    if (s.mcp_tool) out += `${pp}mcp_tool: "${s.mcp_tool}"\n`;
    if (s.mcp_params && Object.keys(s.mcp_params).length) {
      out += `${pp}mcp_params: ${JSON.stringify(s.mcp_params)}\n`;
    }
    if (s.sub_steps && s.sub_steps.length) {
      out += `${pp}sub_steps:\n`;
      for (const sub of s.sub_steps) out += serializeStepYAML(sub, stepIndent + 4);
    }
    return out;
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
      if (n.node_type) step.node_type = n.node_type;
      if (n.role) step.role = n.role;
      if (n.human_required) step.human_required = n.human_required;
      if (n.approval_policy) step.approval_policy = n.approval_policy;
      if (n.doc_template) step.doc_template = n.doc_template;
      if (n.mcp_tool) step.mcp_tool = n.mcp_tool;
      if (n.mcp_params && Object.keys(n.mcp_params).length) step.mcp_params = n.mcp_params;
      if (n.sub_steps && n.sub_steps.length) step.sub_steps = n.sub_steps;
      return step;
    });

    let yaml = `description: "${$('wf-name').value.trim() || 'Untitled Workflow'}"\n`;
    yaml += `category: "custom"\n`;
    yaml += `steps:\n`;
    for (const s of steps) yaml += serializeStepYAML(s, 2);
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

  // ── YAML import helpers ──
  function _yamlIndent(line) { return line.search(/\S/); }
  function _yamlUnquote(s) { return (s || '').replace(/^["']|["']$/g, '').trim(); }

  function _parseYAMLSteps(lines, stepIndent) {
    const steps = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const indent = _yamlIndent(line);
      if (indent < stepIndent) break;
      const trimmed = line.trim();
      if (trimmed.startsWith('- id:') && indent === stepIndent) {
        const step = { id: _yamlUnquote(trimmed.replace('- id:', '').trim()), sub_steps: [] };
        i++;
        while (i < lines.length) {
          const pl = lines[i];
          if (!pl.trim()) { i++; continue; }
          const pi = _yamlIndent(pl);
          if (pi < stepIndent + 2) break;
          const pt = pl.trim();
          if (pt === 'sub_steps:' && pi === stepIndent + 2) {
            i++;
            const subLines = [];
            while (i < lines.length) {
              const sl = lines[i];
              if (!sl.trim()) { i++; continue; }
              if (_yamlIndent(sl) <= stepIndent + 2) break;
              subLines.push(sl);
              i++;
            }
            step.sub_steps = _parseYAMLSteps(subLines, stepIndent + 4);
          } else if (pi === stepIndent + 2) {
            const kv = pt.match(/^(\w+):\s*(.+)/);
            if (kv) step[kv[1]] = _yamlUnquote(kv[2].trim());
            i++;
          } else {
            i++;
          }
        }
        steps.push(step);
      } else {
        i++;
      }
    }
    return steps;
  }

  function doImportYAML() {
    const raw = $('wf-yaml-input').value.trim();
    if (!raw) { toast('Paste YAML first', 'warning'); return; }

    try {
      const lines = raw.split('\n');
      let stepsStart = -1;

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmed = line.trim();
        // Only read top-level description (no leading whitespace) to avoid
        // step-level description: fields overwriting the workflow name
        if (line.startsWith('description:')) {
          const match = trimmed.match(/description:\s*"?(.+?)"?\s*$/);
          if (match) $('wf-name').value = match[1];
        }
        if (trimmed === 'steps:') stepsStart = i + 1;
      }

      if (stepsStart < 0) { toast('No steps: found in YAML', 'error'); return; }

      const steps = _parseYAMLSteps(lines.slice(stepsStart), 2);
      if (!steps.length) { toast('No steps found in YAML', 'error'); return; }

      nodes = []; edges = [];
      nodesEl().innerHTML = '';
      edgesSvg().innerHTML = '';

      let y = 60;
      for (const s of steps) {
        addNode({
          toolId: s.id,
          toolName: s.name || s.id,
          toolPath: s.tool || '',
          toolDesc: s.description || '',
          toolColor: guessColor(s.tool || ''),
          node_type: s.node_type || 'tool',
          role: s.role || null,
          human_required: s.human_required === 'true' || s.human_required === true,
          approval_policy: s.approval_policy || null,
          doc_template: s.doc_template || null,
          mcp_tool: s.mcp_tool || null,
          mcp_params: s.mcp_params || {},
          sub_steps: s.sub_steps || [],
        }, 120, y);
        y += 100;
      }

      $('wf-yaml-modal').style.display = 'none';
      toast(`Imported ${steps.length} step${steps.length !== 1 ? 's' : ''}`, 'success');
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
            <div style="display:flex;gap:6px;align-items:center;">
              <button class="studio-btn studio-btn--secondary studio-btn--sm"
                      title="Edit workflow in canvas"
                      onclick="StudioWF.loadWorkflow('${wf.workflow_id}')">
                &#9998; Edit
              </button>
              <button class="studio-btn studio-btn--primary studio-btn--sm"
                      title="Run workflow now"
                      onclick="StudioWF.loadWorkflow('${wf.workflow_id}').then(()=>StudioWF.run())">
                &#9654; Run
              </button>
              <button class="studio-btn studio-btn--ghost studio-btn--sm"
                      title="Delete workflow"
                      style="color:#f87171;border-color:#7f1d1d;"
                      onclick="StudioWF.deleteSavedWorkflow('${wf.workflow_id}')">
                &#128465;
              </button>
            </div>
          </div>
        </div>
      `).join('');
    } catch (e) {
      console.error(e);
    }
  }

  async function deleteSavedWorkflow(workflowId) {
    if (!confirm('Delete this workflow? This cannot be undone.')) return;
    try {
      const resp = await fetch('/api/studio/workflows/' + encodeURIComponent(workflowId), {method: 'DELETE'});
      if (resp.ok) loadSavedWorkflows();
    } catch (e) { console.error(e); }
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

  // ── Zoom & Pan ──
  function applyZoom() {
    const w = world();
    if (w) w.style.transform = `translate(${panX}px,${panY}px) scale(${zoom})`;
    const lbl = $('wf-zoom-label');
    if (lbl) lbl.textContent = Math.round(zoom * 100) + '%';
  }

  function zoomAtPoint(screenX, screenY, newZoom) {
    const r = canvas().getBoundingClientRect();
    const wx = (screenX - r.left - panX) / zoom;
    const wy = (screenY - r.top  - panY) / zoom;
    zoom = Math.min(2, Math.max(0.25, newZoom));
    panX = screenX - r.left - wx * zoom;
    panY = screenY - r.top  - wy * zoom;
    applyZoom();
  }

  function zoomIn() {
    const r = canvas().getBoundingClientRect();
    zoomAtPoint(r.left + r.width / 2, r.top + r.height / 2, zoom + 0.1);
  }
  function zoomOut() {
    const r = canvas().getBoundingClientRect();
    zoomAtPoint(r.left + r.width / 2, r.top + r.height / 2, zoom - 0.1);
  }
  function fitView() {
    if (!nodes.length) { zoom = 1; panX = 0; panY = 0; applyZoom(); return; }
    const c = canvas();
    if (!c) return;
    const vw = c.clientWidth  || 800;
    const vh = c.clientHeight || 600;
    const PAD = 60;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    nodes.forEach(n => {
      const el = document.getElementById(n.id);
      const w = (el && el.offsetWidth)  || 220;
      const h = (el && el.offsetHeight) || 120;
      minX = Math.min(minX, n.x);
      minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x + w);
      maxY = Math.max(maxY, n.y + h);
    });
    const cw = maxX - minX || 1;
    const ch = maxY - minY || 1;
    zoom = Math.min((vw - PAD * 2) / cw, (vh - PAD * 2) / ch, 1);
    zoom = Math.max(zoom, 0.1);
    panX = (vw - cw * zoom) / 2 - minX * zoom;
    panY = (vh - ch * zoom) / 2 - minY * zoom;
    applyZoom();
  }

  // Mouse-wheel zoom (zoom toward cursor)
  document.addEventListener('DOMContentLoaded', () => {
    const c = canvas();
    if (!c) return;

    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      const delta = e.deltaY < 0 ? 0.1 : -0.1;
      zoomAtPoint(e.clientX, e.clientY, zoom + delta);
    }, { passive: false });

    // Click-drag on empty canvas → pan
    c.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      if (e.target.closest('.wf-node')) return;
      if (e.target.closest('#wf-empty-state')) return;
      e.preventDefault();
      isPanning = true;
      panStart = { x: e.clientX - panX, y: e.clientY - panY };
      c.classList.add('is-panning');
    });
  });

  // ── Validate ──
  function validate() {
    if (!nodes.length) { toast('Add steps before validating', 'warning'); return; }
    const issues = [];

    // 1. Cycle check (DFS)
    const visited = new Set(), visiting = new Set();
    function hasCycle(id) {
      if (visiting.has(id)) return true;
      if (visited.has(id)) return false;
      visiting.add(id);
      for (const e of edges.filter(e => e.from === id)) { if (hasCycle(e.to)) return true; }
      visiting.delete(id); visited.add(id); return false;
    }
    for (const n of nodes) { if (hasCycle(n.id)) { issues.push('Circular dependency detected'); break; } }

    // 2. Union-Find — detect isolated nodes and disconnected subgraphs
    if (nodes.length > 1) {
      const parent = {};
      nodes.forEach(n => { parent[n.id] = n.id; });
      function find(x) {
        while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; }
        return x;
      }
      function union(a, b) { const ra = find(a), rb = find(b); if (ra !== rb) parent[ra] = rb; }
      edges.forEach(e => { if (e.from in parent && e.to in parent) union(e.from, e.to); });

      const compMap = {};
      nodes.forEach(n => {
        const r = find(n.id);
        (compMap[r] = compMap[r] || []).push(n.label || n.id);
      });
      const comps = Object.values(compMap);
      if (comps.length > 1) {
        const iso = comps.filter(c => c.length === 1).map(c => c[0]);
        const sub = comps.filter(c => c.length > 1);
        if (iso.length) issues.push(`${iso.length} isolated node(s): ${iso.join(', ')}`);
        if (sub.length > 1) issues.push(`${sub.length} disconnected subgraph(s) — connect all chains into one graph`);
        if (!iso.length && !sub.length && comps.length > 1) issues.push(`${comps.length} disconnected component(s)`);
      }
    }

    const validLabel = _navStack.length > 0 ? 'Sub-workflow valid' : 'Workflow valid';
    if (issues.length) toast('Validation failed: ' + issues.join(' | '), 'warning');
    else toast(validLabel + ' — ' + nodes.length + ' steps, ' + edges.length + ' connections', 'success');
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

  // ── DAG layout — assign (x, y) positions via rank + column ──
  function computeDagLayout(steps) {
    const NODE_W = 190;
    const NODE_H = 72;
    const GAP_X  = 80;
    const GAP_Y  = 28;
    const PAD_X  = 60;
    const PAD_Y  = 60;

    // Build dep/successor maps
    const deps = {};   // id → [dep-id, ...]
    const succs = {};  // id → [successor-id, ...]
    steps.forEach(s => {
      deps[s.id]  = s.depends_on
        ? (Array.isArray(s.depends_on) ? s.depends_on : [s.depends_on])
        : [];
      succs[s.id] = [];
    });
    steps.forEach(s => deps[s.id].forEach(d => { if (succs[d]) succs[d].push(s.id); }));

    // Assign rank = longest path from any root
    const rank = {};
    steps.forEach(s => { rank[s.id] = 0; });
    const inDeg = {};
    steps.forEach(s => { inDeg[s.id] = deps[s.id].length; });
    const queue = steps.filter(s => inDeg[s.id] === 0).map(s => s.id);
    while (queue.length) {
      const id = queue.shift();
      succs[id].forEach(sid => {
        rank[sid] = Math.max(rank[sid], rank[id] + 1);
        inDeg[sid]--;
        if (inDeg[sid] === 0) queue.push(sid);
      });
    }

    // Group by rank
    const byRank = {};
    steps.forEach(s => {
      const r = rank[s.id];
      (byRank[r] = byRank[r] || []).push(s.id);
    });

    // Assign positions: rank → x column, position-in-rank → y row
    const maxPerCol = Math.max(...Object.values(byRank).map(a => a.length));
    const positions = {};
    Object.entries(byRank).forEach(([r, ids]) => {
      const x = PAD_X + parseInt(r) * (NODE_W + GAP_X);
      const colH = ids.length * (NODE_H + GAP_Y) - GAP_Y;
      const gridH = maxPerCol * (NODE_H + GAP_Y) - GAP_Y;
      const startY = PAD_Y + (gridH - colH) / 2;
      ids.forEach((id, i) => {
        positions[id] = { x, y: startY + i * (NODE_H + GAP_Y) };
      });
    });

    return positions;
  }

  // Stubs for future features
  function undo() { toast('Undo not yet implemented', 'info'); }
  function redo() { toast('Redo not yet implemented', 'info'); }

  async function loadWorkflow(id) {
    if (!id) return;
    try {
      toast('Loading workflow...', 'info');
      // Fetch metadata (name) and parsed steps (composer format) in parallel
      const [metaResp, stepsResp] = await Promise.all([
        fetch('/api/studio/workflows/' + encodeURIComponent(id)),
        fetch('/api/studio/workflows/' + encodeURIComponent(id) + '/composer'),
      ]);
      if (!metaResp.ok) { toast('Workflow not found', 'error'); return; }
      const wf   = await metaResp.json();
      const data = stepsResp.ok ? await stepsResp.json() : {};
      const steps = data.steps || [];

      // Clear canvas
      nodes = []; edges = []; selectedNode = null; nextNodeY = 60;
      nodesEl().innerHTML = '';
      edgesSvg().innerHTML = '';
      $('wf-name').value = wf.name || 'Untitled Workflow';
      updateCounts();
      showEmptyState();

      if (!steps.length) {
        toast(`Workflow "${wf.name}" loaded (no steps)`, 'warning');
        return;
      }

      const positions = computeDagLayout(steps);
      const idMap = {};
      for (const s of steps) {
        const pos = positions[s.id] || { x: 60, y: 60 };
        const node = addNode({
          toolId: s.id,
          toolName: s.name || s.id,
          toolPath: s.tool || '',
          toolDesc: s.description || '',
          toolColor: guessColor(s.tool || ''),
          node_type: s.node_type || 'tool',
          role: s.role || null,
          human_required: s.human_required || false,
          approval_policy: s.approval_policy || null,
          doc_template: s.doc_template || null,
          mcp_tool: s.mcp_tool || null,
          mcp_params: s.mcp_params || {},
          sub_steps: s.sub_steps || [],
        }, pos.x, pos.y);
        idMap[s.id] = node.id;
      }

      for (const s of steps) {
        if (!s.depends_on) continue;
        const depList = Array.isArray(s.depends_on) ? s.depends_on : [s.depends_on];
        for (const dep of depList) {
          const fromId = idMap[dep];
          const toId   = idMap[s.id];
          if (fromId && toId) {
            edges.push({ from: fromId, to: toId });
            const target = nodes.find(n => n.id === toId);
            if (target && !target.dependsOn.includes(fromId)) target.dependsOn.push(fromId);
          }
        }
      }

      const editorTab = document.querySelector('[data-tab="editor"]');
      if (editorTab) switchTab(editorTab);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        renderEdges();
        requestAnimationFrame(() => setTimeout(fitView, 80));
      }));
      toast(`Workflow "${wf.name}" loaded — ${steps.length} steps`, 'success');
    } catch (e) {
      toast('Failed to load workflow: ' + e.message, 'error');
    }
  }

  async function useTemplate(id) {
    try {
      toast('Loading template...', 'info');
      const resp = await fetch('/api/studio/templates/' + encodeURIComponent(id));
      if (!resp.ok) { toast('Template not found', 'error'); return; }
      const data = await resp.json();
      const steps = data.steps || [];
      if (!steps.length) { toast('Template has no steps', 'warning'); return; }

      // Clear canvas
      nodes = []; edges = []; selectedNode = null; nextNodeY = 60;
      nodesEl().innerHTML = '';
      edgesSvg().innerHTML = '';
      $('wf-name').value = data.name || 'Untitled Workflow';
      updateCounts();
      showEmptyState();

      // Compute DAG layout positions
      const positions = computeDagLayout(steps);

      // Create nodes, track YAML step-id → canvas node-id mapping
      const idMap = {};
      for (const s of steps) {
        const pos = positions[s.id] || { x: 60, y: 60 };
        const node = addNode({
          toolId: s.id,
          toolName: s.name || s.id,
          toolPath: s.tool || '',
          toolDesc: s.description || '',
          toolColor: guessColor(s.tool || ''),
          node_type: s.node_type || 'tool',
          role: s.role || null,
          human_required: s.human_required || false,
          approval_policy: s.approval_policy || null,
          doc_template: s.doc_template || null,
          mcp_tool: s.mcp_tool || null,
          mcp_params: s.mcp_params || {},
          sub_steps: s.sub_steps || [],
        }, pos.x, pos.y);
        idMap[s.id] = node.id;
      }

      // Build edges from depends_on
      for (const s of steps) {
        if (!s.depends_on) continue;
        const depList = Array.isArray(s.depends_on) ? s.depends_on : [s.depends_on];
        for (const dep of depList) {
          const fromId = idMap[dep];
          const toId   = idMap[s.id];
          if (fromId && toId) {
            edges.push({ from: fromId, to: toId });
            const target = nodes.find(n => n.id === toId);
            if (target && !target.dependsOn.includes(fromId)) target.dependsOn.push(fromId);
          }
        }
      }

      // Switch to editor first so nodes are visible, then render edges after paint
      const editorTab = document.querySelector('[data-tab="editor"]');
      if (editorTab) switchTab(editorTab);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        renderEdges();
        requestAnimationFrame(() => setTimeout(fitView, 80));
      }));

      toast(`Template "${data.name}" loaded — ${steps.length} steps`, 'success');
    } catch (e) {
      toast('Failed to load template: ' + e.message, 'error');
    }
  }

  // ── Palette collapse ──
  let paletteCollapsed = false;

  function togglePalette() {
    paletteCollapsed = !paletteCollapsed;
    const studio = document.querySelector('.wf-studio');
    const btn = $('wf-palette-toggle');
    if (paletteCollapsed) {
      studio.classList.add('wf-studio--palette-collapsed');
      if (btn) btn.textContent = '»';
    } else {
      studio.classList.remove('wf-studio--palette-collapsed');
      if (btn) btn.textContent = '«';
    }
  }

  // ── Init ──
  function init() {
    loadCatalog();
    // Auto-collapse palette on load
    togglePalette();
    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Delete' && selectedNode) deleteNode(selectedNode);
      if (e.key === 'Escape') { closeModal(); connectState = null; }
    });
    // Auto-load workflow if ?load= is in the URL (e.g. from canvas bridge redirect)
    const loadId = new URLSearchParams(window.location.search).get('load');
    if (loadId) loadWorkflow(loadId);
  }

  document.addEventListener('DOMContentLoaded', init);

  // ── More menu (toolbar overflow) ──
  function toggleMoreMenu(e) {
    if (e) e.stopPropagation();
    const menu = $('wf-more-menu');
    if (!menu) return;
    if (menu.hidden) {
      menu.hidden = false;
      setTimeout(() => document.addEventListener('click', function _close() {
        menu.hidden = true;
        document.removeEventListener('click', _close);
      }), 0);
    } else {
      menu.hidden = true;
    }
  }
  function closeMoreMenu() {
    const menu = $('wf-more-menu');
    if (menu) menu.hidden = true;
  }

  // ── Public API ──
  return {
    handleToolDragStart, handleDrop, filterTools,
    startConnect, endConnect,
    switchTab, save, exportYAML, importYAML, doImportYAML,
    validate, createNew, loadTemplate, loadWorkflow, useTemplate, deleteSavedWorkflow,
    openNodeConfig: openNodeConfig, closeModal, saveNodeConfig, deleteNode, onNodeTypeChange,
    zoomIn, zoomOut, fitView, undo, redo, togglePalette, toggleGroup,
    drillInto, drillBack, drillBackTo, hideContextMenu,
    toggleMoreMenu, closeMoreMenu,
    _exportToYAMLInternal: exportToYAML,
  };
})();
