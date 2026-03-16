// INTaaS Visualizations — SVG knowledge graph, heatmap, topic explorer
// Zero dependencies — pure SVG generation from API data

const VIZ = {
  // Color palette
  colors: {
    person: '#58a6ff',
    organization: '#bc8cff',
    location: '#3fb950',
    event: '#d29922',
    policy: '#f0883e',
    statistic: '#8b949e',
    claim: '#f85149',
    document: '#6e7681',
    unknown: '#484f58',
    edge: 'rgba(139,148,158,0.3)',
    edgeHighlight: 'rgba(88,166,255,0.6)',
    bg: '#0d1117',
    text: '#c9d1d9',
    muted: '#6e7681',
  },

  /**
   * Render an interactive force-directed knowledge graph as SVG.
   * Supports: drag nodes, pan viewport, zoom, hover highlight, click select.
   */
  renderGraph(container, graphData, width, height) {
    width = width || container.clientWidth || 800;
    height = height || 500;
    const self = this;

    const nodes = (graphData.nodes || []).map(n => ({
      ...n,
      x: width / 2 + (Math.random() - 0.5) * width * 0.6,
      y: height / 2 + (Math.random() - 0.5) * height * 0.6,
      vx: 0, vy: 0,
      r: n.depth === 0 ? 20 : n.depth === 1 ? 14 : 9,
      pinned: false,
    }));

    const edges = (graphData.edges || []).slice(0, 80);
    const nodeMap = {};
    nodes.forEach(n => { nodeMap[n.name] = n; });

    // Initial layout (spring physics)
    for (let iter = 0; iter < 100; iter++) { self._simStep(nodes, edges, nodeMap, width, height); }

    // Create SVG element
    const svgNs = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNs, 'svg');
    svg.setAttribute('width', width);
    svg.setAttribute('height', height);
    svg.style.cssText = 'background:#0d1117;border-radius:8px;border:1px solid #30363d;cursor:grab;user-select:none';

    // Zoom/pan group
    const g = document.createElementNS(svgNs, 'g');
    svg.appendChild(g);
    let scale = 1, panX = 0, panY = 0;

    function updateTransform() {
      g.setAttribute('transform', 'translate(' + panX + ',' + panY + ') scale(' + scale + ')');
    }

    // Draw edges
    const edgeEls = [];
    for (const e of edges) {
      const a = nodeMap[e.from], b = nodeMap[e.to];
      if (!a || !b) continue;
      const line = document.createElementNS(svgNs, 'line');
      line.setAttribute('stroke', self.colors.edge);
      line.setAttribute('stroke-width', Math.min(e.weight || 1, 3));
      line._from = e.from;
      line._to = e.to;
      g.appendChild(line);
      edgeEls.push(line);
    }

    // Draw nodes
    const nodeEls = [];
    for (const n of nodes) {
      const col = self.colors[n.type] || self.colors.unknown;

      const group = document.createElementNS(svgNs, 'g');
      group.style.cursor = 'pointer';
      group._node = n;

      const circle = document.createElementNS(svgNs, 'circle');
      circle.setAttribute('r', n.r);
      circle.setAttribute('fill', col);
      circle.setAttribute('opacity', '0.85');
      circle.setAttribute('stroke', '#0d1117');
      circle.setAttribute('stroke-width', '2');
      group.appendChild(circle);

      if (n.r >= 10) {
        const label = n.name.length > 18 ? n.name.substring(0, 16) + '..' : n.name;
        const text = document.createElementNS(svgNs, 'text');
        text.setAttribute('y', n.r + 14);
        text.setAttribute('fill', self.colors.text);
        text.setAttribute('font-size', '10');
        text.setAttribute('text-anchor', 'middle');
        text.setAttribute('font-family', 'sans-serif');
        text.setAttribute('pointer-events', 'none');
        text.textContent = label;
        group.appendChild(text);
      }

      g.appendChild(group);
      nodeEls.push(group);
    }

    // Info panel
    const info = document.createElementNS(svgNs, 'text');
    info.setAttribute('x', '10');
    info.setAttribute('y', '20');
    info.setAttribute('fill', self.colors.muted);
    info.setAttribute('font-size', '11');
    info.setAttribute('font-family', 'sans-serif');
    info.textContent = 'Drag nodes | Scroll to zoom | Drag background to pan';
    svg.appendChild(info);

    // Legend
    const types = [...new Set(nodes.map(n => n.type))];
    let lx = 10;
    for (const t of types) {
      const col = self.colors[t] || self.colors.unknown;
      const lc = document.createElementNS(svgNs, 'circle');
      lc.setAttribute('cx', lx); lc.setAttribute('cy', height - 15);
      lc.setAttribute('r', 5); lc.setAttribute('fill', col);
      svg.appendChild(lc);
      const lt = document.createElementNS(svgNs, 'text');
      lt.setAttribute('x', lx + 10); lt.setAttribute('y', height - 11);
      lt.setAttribute('fill', self.colors.muted);
      lt.setAttribute('font-size', '10'); lt.setAttribute('font-family', 'sans-serif');
      lt.textContent = t;
      svg.appendChild(lt);
      lx += t.length * 7 + 25;
    }

    function updatePositions() {
      for (const el of nodeEls) {
        const n = el._node;
        el.setAttribute('transform', 'translate(' + n.x.toFixed(1) + ',' + n.y.toFixed(1) + ')');
      }
      for (const line of edgeEls) {
        const a = nodeMap[line._from], b = nodeMap[line._to];
        if (a && b) {
          line.setAttribute('x1', a.x.toFixed(1)); line.setAttribute('y1', a.y.toFixed(1));
          line.setAttribute('x2', b.x.toFixed(1)); line.setAttribute('y2', b.y.toFixed(1));
        }
      }
    }

    updatePositions();
    updateTransform();

    // ── Drag nodes ──
    let dragNode = null, dragOffset = {x: 0, y: 0};

    svg.addEventListener('mousedown', function(e) {
      const el = e.target.closest('g[style*="pointer"]');
      if (el && el._node) {
        dragNode = el._node;
        dragNode.pinned = true;
        const pt = svgPoint(e);
        dragOffset.x = pt.x - dragNode.x;
        dragOffset.y = pt.y - dragNode.y;
        svg.style.cursor = 'grabbing';
        e.preventDefault();
      } else if (e.target === svg || e.target.tagName === 'svg') {
        // Pan start
        dragNode = 'PAN';
        dragOffset.x = e.clientX - panX;
        dragOffset.y = e.clientY - panY;
        svg.style.cursor = 'grabbing';
      }
    });

    svg.addEventListener('mousemove', function(e) {
      if (dragNode === 'PAN') {
        panX = e.clientX - dragOffset.x;
        panY = e.clientY - dragOffset.y;
        updateTransform();
      } else if (dragNode) {
        const pt = svgPoint(e);
        dragNode.x = pt.x - dragOffset.x;
        dragNode.y = pt.y - dragOffset.y;
        // Mini simulation step for connected nodes
        self._simStep(nodes, edges, nodeMap, width, height, dragNode);
        updatePositions();
      }

      // Hover highlight
      const el = e.target.closest('g[style*="pointer"]');
      for (const ne of nodeEls) {
        ne.querySelector('circle').setAttribute('opacity', '0.85');
        ne.querySelector('circle').removeAttribute('stroke-dasharray');
      }
      for (const le of edgeEls) {
        le.setAttribute('stroke', self.colors.edge);
        le.setAttribute('stroke-width', Math.min(1, 3));
      }
      if (el && el._node) {
        const n = el._node;
        el.querySelector('circle').setAttribute('opacity', '1');
        el.querySelector('circle').setAttribute('stroke', self.colors[n.type] || '#fff');
        info.textContent = n.name + ' (' + n.type + ')';
        // Highlight connected edges
        for (const le of edgeEls) {
          if (le._from === n.name || le._to === n.name) {
            le.setAttribute('stroke', self.colors.edgeHighlight);
            le.setAttribute('stroke-width', '3');
          }
        }
      } else {
        info.textContent = 'Drag nodes | Scroll to zoom | Drag background to pan';
      }
    });

    svg.addEventListener('mouseup', function() {
      if (dragNode && dragNode !== 'PAN') { dragNode.pinned = false; }
      dragNode = null;
      svg.style.cursor = 'grab';
    });

    svg.addEventListener('mouseleave', function() {
      if (dragNode && dragNode !== 'PAN') { dragNode.pinned = false; }
      dragNode = null;
      svg.style.cursor = 'grab';
    });

    // ── Zoom ──
    svg.addEventListener('wheel', function(e) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      scale = Math.max(0.3, Math.min(3, scale * delta));
      updateTransform();
    });

    function svgPoint(e) {
      // Convert screen coords to SVG coords (accounting for pan/zoom)
      const rect = svg.getBoundingClientRect();
      return {
        x: (e.clientX - rect.left - panX) / scale,
        y: (e.clientY - rect.top - panY) / scale,
      };
    }

    container.innerHTML = '';
    container.appendChild(svg);
  },

  /** Single simulation step (used for initial layout + drag updates) */
  _simStep(nodes, edges, nodeMap, width, height, pinnedNode) {
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].pinned) continue;
      for (let j = i + 1; j < nodes.length; j++) {
        if (nodes[j].pinned) continue;
        const dx = nodes[j].x - nodes[i].x;
        const dy = nodes[j].y - nodes[i].y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = 2500 / (dist * dist);
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        nodes[i].vx -= fx; nodes[i].vy -= fy;
        nodes[j].vx += fx; nodes[j].vy += fy;
      }
    }
    for (const e of edges) {
      const a = nodeMap[e.from], b = nodeMap[e.to];
      if (!a || !b) continue;
      if (a.pinned && b.pinned) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = (dist - 120) * 0.008;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      if (!a.pinned) { a.vx += fx; a.vy += fy; }
      if (!b.pinned) { b.vx -= fx; b.vy -= fy; }
    }
    for (const n of nodes) {
      if (n.pinned) continue;
      n.vx += (width / 2 - n.x) * 0.003;
      n.vy += (height / 2 - n.y) * 0.003;
      n.x += n.vx * 0.3;
      n.y += n.vy * 0.3;
      n.vx *= 0.75; n.vy *= 0.75;
      n.x = Math.max(30, Math.min(width - 30, n.x));
      n.y = Math.max(30, Math.min(height - 30, n.y));
    }
  },

  /**
   * Render source × entity heatmap
   */
  renderHeatmap(container, matrixData, width) {
    const cols = matrixData.entity_columns || [];
    const rows = matrixData.rows || [];
    if (!cols.length || !rows.length) {
      container.innerHTML = '<p style="color:#6e7681">No matrix data available</p>';
      return;
    }

    width = width || container.clientWidth || 800;
    const cellSize = 28;
    const labelWidth = 160;
    const headerHeight = 100;
    const svgWidth = labelWidth + cols.length * cellSize + 20;
    const svgHeight = headerHeight + rows.length * cellSize + 20;

    let svg = '<svg width="' + svgWidth + '" height="' + svgHeight + '" xmlns="http://www.w3.org/2000/svg" '
      + 'style="background:' + this.colors.bg + ';border-radius:8px;border:1px solid #30363d;overflow:auto">';

    // Column headers (rotated entity names)
    for (let c = 0; c < cols.length; c++) {
      const x = labelWidth + c * cellSize + cellSize / 2;
      const label = cols[c].display.length > 15 ? cols[c].display.substring(0, 13) + '..' : cols[c].display;
      const col = this.colors[cols[c].type] || this.colors.unknown;
      svg += '<text x="' + x + '" y="' + (headerHeight - 8)
        + '" fill="' + col + '" font-size="9" text-anchor="end" font-family="sans-serif" '
        + 'transform="rotate(-45,' + x + ',' + (headerHeight - 8) + ')">' + this._esc(label) + '</text>';
    }

    // Rows
    for (let r = 0; r < rows.length; r++) {
      const y = headerHeight + r * cellSize;
      const src = rows[r].source.length > 22 ? rows[r].source.substring(0, 20) + '..' : rows[r].source;

      // Source label
      svg += '<text x="' + (labelWidth - 5) + '" y="' + (y + cellSize / 2 + 4)
        + '" fill="' + this.colors.text + '" font-size="9" text-anchor="end" font-family="sans-serif">'
        + this._esc(src) + '</text>';

      // Cells
      for (let c = 0; c < cols.length; c++) {
        const x = labelWidth + c * cellSize;
        const val = rows[r].entities[cols[c].name] || 0;
        const fill = val ? '#3fb950' : '#161b22';
        const opacity = val ? '0.7' : '0.3';
        svg += '<rect x="' + x + '" y="' + y + '" width="' + (cellSize - 2)
          + '" height="' + (cellSize - 2) + '" rx="3" fill="' + fill + '" opacity="' + opacity + '">'
          + '<title>' + this._esc(rows[r].source) + ' / ' + this._esc(cols[c].display) + ': ' + (val ? 'YES' : 'NO') + '</title>'
          + '</rect>';
      }
    }

    svg += '</svg>';
    container.innerHTML = '<div style="overflow-x:auto">' + svg + '</div>';
  },

  /**
   * Render topic explorer (related topics via shared entities)
   */
  renderTopicExplorer(container, topics) {
    if (!topics || topics.length === 0) {
      container.innerHTML = '<p style="color:#6e7681">No related topics found</p>';
      return;
    }

    let html = '<div style="display:flex;flex-direction:column;gap:0.5rem">';
    for (const t of topics) {
      const barWidth = Math.min(t.shared_pct || 0, 100);
      html += '<div style="display:flex;align-items:center;gap:0.75rem;padding:0.5rem;'
        + 'background:#161b22;border-radius:6px;border:1px solid #30363d;cursor:pointer" '
        + 'onclick="location.href=\'topic.html?id=' + t.topic_id + '\'">'
        + '<div style="flex:1"><div style="font-weight:500;font-size:0.9rem">' + this._esc(t.title) + '</div>'
        + '<div style="font-size:0.75rem;color:#6e7681">' + (t.shared_entities || 0) + ' shared entities</div></div>'
        + '<div style="width:80px;height:12px;background:#0d1117;border-radius:3px;overflow:hidden">'
        + '<div style="width:' + barWidth + '%;height:100%;background:#58a6ff;border-radius:3px"></div></div>'
        + '<span style="font-size:0.8rem;color:#58a6ff;width:35px;text-align:right">' + barWidth + '%</span>'
        + '</div>';
    }
    html += '</div>';
    container.innerHTML = html;
  },

  _esc(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
};
