/* CUI // SP-CTI
 * Minimal JointJS renderer for the Network Migration wizard topology sidecar.
 * Uses the same graph JSON shape as tools/dashboard/static/js/network-canvas.js
 * but runs read-only in a small panel.
 */
(function (window) {
  'use strict';

  const NODE_STYLES = {
    router: { fill: '#0f2b3a', stroke: '#3498db', w: 120, h: 60, r: 4 },
    'switch-l3': { fill: '#0f2b0f', stroke: '#2ecc71', w: 120, h: 60, r: 4 },
    'switch-l2': { fill: '#0f2b0f', stroke: '#27ae60', w: 120, h: 60, r: 4 },
    server: { fill: '#0f2b2b', stroke: '#1abc9c', w: 100, h: 50, r: 4 },
    firewall: { fill: '#2b0f0f', stroke: '#e94560', w: 110, h: 55, r: 4 },
    'media-100ge': { fill: '#0f2b2b', stroke: '#0097a7', w: 74, h: 30, r: 3 },
    'media-40ge': { fill: '#0f2b2b', stroke: '#00b8d4', w: 74, h: 30, r: 3 },
    'media-25ge': { fill: '#0f2b1a', stroke: '#00d2a0', w: 74, h: 30, r: 3 },
    'media-10ge': { fill: '#0f2b1a', stroke: '#2ecc71', w: 74, h: 30, r: 3 },
    'media-ge': { fill: '#0f2b1a', stroke: '#48c774', w: 74, h: 30, r: 3 },
    'sfp-plus': { fill: '#1a1a0f', stroke: '#9e9d24', w: 64, h: 28, r: 3 },
    sfp: { fill: '#1a1a0f', stroke: '#c0ca33', w: 64, h: 28, r: 3 },
    qsfp: { fill: '#1a1a0f', stroke: '#827717', w: 64, h: 28, r: 3 },
    'qsfp-dd': { fill: '#1a1a0f', stroke: '#6d4c00', w: 64, h: 28, r: 3 },
    vlan: { fill: '#0f2b0f', stroke: '#58d68d', w: 86, h: 40, r: 8 },
  };
  const DEFAULT_STYLE = { fill: '#16213e', stroke: '#4a9eff', w: 90, h: 40, r: 4 };

  function _style(type) {
    return NODE_STYLES[type] || DEFAULT_STYLE;
  }

  function _makeNode(graph, node) {
    const s = _style(node.type);
    const labelText = (node.label || '').replace(/\\n/g, '\n');
    const shape = new joint.shapes.standard.Rectangle({
      id: node.id,
      position: { x: node.x || 100, y: node.y || 100 },
      size: { width: s.w, height: s.h },
      attrs: {
        body: {
          fill: s.fill,
          stroke: s.stroke,
          strokeWidth: 2,
          rx: s.r,
          ry: s.r,
        },
        label: {
          text: labelText,
          fill: '#eaeaea',
          fontSize: 10,
          fontFamily: 'Segoe UI, system-ui, sans-serif',
          textWrap: { width: s.w - 8, height: s.h - 6 },
        },
      },
    });
    graph.addCell(shape);
    return shape;
  }

  function _makeLink(graph, edge, nodeIds) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return;
    const link = new joint.shapes.standard.Link({
      id: edge.id,
      source: { id: edge.source },
      target: { id: edge.target },
      attrs: {
        line: {
          stroke: '#7a8cb0',
          strokeWidth: edge.config && edge.config.dashed ? 1.5 : 2,
          strokeDasharray: edge.config && edge.config.dashed ? '5 3' : undefined,
          targetMarker: { type: 'classic', fill: '#7a8cb0', size: 5 },
        },
      },
      labels: edge.label
        ? [{ attrs: { text: { text: edge.label, fill: '#7a8cb0', fontSize: 9 } } }]
        : [],
    });
    graph.addCell(link);
  }

  function renderMigrationTopology(containerId, graphJson) {
    const container = document.getElementById(containerId);
    if (!container || typeof joint === 'undefined') return;
    container.innerHTML = '';

    const width = Math.max(container.clientWidth || 1000, 320);
    const height = Math.max(container.clientHeight || 420, 200);

    const graph = new joint.dia.Graph();
    const paper = new joint.dia.Paper({
      el: container,
      model: graph,
      width: width,
      height: height,
      gridSize: 10,
      drawGrid: { name: 'dot', args: { color: 'rgba(255,255,255,0.05)' } },
      background: { color: '#0a1624' },
      defaultLink: () => new joint.shapes.standard.Link(),
      interactive: false,
    });

    const nodeIds = new Set();
    (graphJson.nodes || []).forEach(function (n) {
      _makeNode(graph, n);
      nodeIds.add(n.id);
    });
    (graphJson.edges || []).forEach(function (e) {
      _makeLink(graph, e, nodeIds);
    });

    // Fit to content with padding
    const cells = graph.getElements();
    if (cells.length) {
      const bbox = graph.getBBox();
      const pad = 20;
      const sx = Math.min(1, (width - pad * 2) / (bbox.width || width));
      const sy = Math.min(1, (height - pad * 2) / (bbox.height || height));
      const scale = Math.max(0.4, Math.min(sx, sy));
      paper.scale(scale, scale);
      // Center the graph
      const dx = (width - bbox.width * scale) / 2 - bbox.x * scale;
      const dy = (height - bbox.height * scale) / 2 - bbox.y * scale;
      paper.translate(dx, dy);
    }
  }

  async function loadMigrationTopology(sessionId, containerId) {
    const r = await fetch('/migration-canvas/api/network-migration/' + sessionId + '/topology');
    if (!r.ok) throw new Error('Failed to load topology');
    const data = await r.json();
    renderMigrationTopology(containerId, data.graph_json || {});
    return data;
  }

  async function refreshMigrationTopology(sessionId, containerId) {
    const r = await fetch('/migration-canvas/api/network-migration/' + sessionId + '/topology', {
      method: 'POST',
    });
    if (!r.ok) throw new Error('Failed to refresh topology');
    const data = await r.json();
    renderMigrationTopology(containerId, data.graph_json || {});
    return data;
  }

  window.renderMigrationTopology = renderMigrationTopology;
  window.loadMigrationTopology = loadMigrationTopology;
  window.refreshMigrationTopology = refreshMigrationTopology;
})(window);
