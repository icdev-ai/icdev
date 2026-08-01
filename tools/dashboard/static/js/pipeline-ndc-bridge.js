/* CUI // SP-CTI — Pipeline Design Canvas: NDC Bridge + Config Panel
 * Right-panel node properties, NDC topology linking, and live infrastructure
 * health. Split from the pipeline-canvas.js monolith (pdx-ux-01).
 *
 * Depends (at runtime) on pipeline-canvas-core.js (getStyle, openRightPanel,
 * escapeHtml/escapeAttr, fetchJson, pdcToast, pushUndo, markDirty, updateStatus,
 * selectedCell, graph) and pipeline-analysis.js (_section, _metric, _bar). All
 * cross-module names resolve at call time, so file load order does not matter
 * beyond core loading first. Classic script — declarations stay top-level.
 */

'use strict';

function openConfigPanel(cell) {
  const type = cell.get('nodeType') || 'unknown';
  const style = getStyle(type);
  const label = cell.attr('label/text') || '';
  let html = `
    <label>Type</label><input value="${escapeAttr(type)}" readonly>
    <label>Label</label><input id="cfg-label" value="${escapeAttr(label)}" onchange="updateNodeLabel(this.value)">
    <label>Icon</label><input value="${escapeAttr(style.symbol)}" readonly>
    <label>Stroke Color</label><div style="width:20px;height:20px;border-radius:4px;background:${escapeAttr(style.stroke)};border:1px solid #1e3a6e;display:inline-block;vertical-align:middle;margin-top:4px;"></div>
    <span style="color:#7a8cb0;margin-left:6px;font-size:11px;">${escapeHtml(style.stroke)}</span>
  `;

  // NDC Bridge: show topology picker
  if (type === 'ndc-topology') {
    html += _section('Link to NDC Topology');
    const linkedId = cell.get('configData')?.ndc_topology_id || '';
    if (linkedId) {
      html += `<div style="padding:4px;background:#0f3460;border-radius:4px;margin:4px 0;font-size:11px;">Linked: <b>${escapeHtml(cell.get('configData')?.ndc_topology_name || linkedId)}</b></div>`;
      html += `<button class="tb-btn" style="margin:4px 0;" onclick="fetchNdcHealth('${encodeURIComponent(linkedId)}')">Refresh Health</button>`;
      html += `<a href="/network/canvas/${encodeURIComponent(linkedId)}" target="_blank" class="tb-btn" style="display:inline-block;text-decoration:none;margin:4px 0;">Open in NDC →</a>`;
      html += '<div id="ndc-health-panel"></div>';
    }
    html += '<div id="ndc-topology-list" style="margin-top:8px;"><i style="color:#7a8cb0;font-size:11px;">Loading topologies...</i></div>';
    openRightPanel('NDC Bridge', html);
    _loadNdcTopologyList(cell);
    if (linkedId) fetchNdcHealth(linkedId);
    return;
  }

  // Hybrid connectivity: show NDC reference note
  if (type.startsWith('hybrid-') || type.startsWith('onprem-') || type === 'ndc-vpc') {
    html += _section('NDC Reference');
    html += '<p style="font-size:11px;color:#7a8cb0;">This object represents infrastructure designed in the Network Design Canvas. For full configuration, open the linked NDC topology.</p>';
    const linkedTopo = _findLinkedNdcTopology();
    if (linkedTopo) {
      html += `<a href="/network/canvas/${linkedTopo}" target="_blank" class="tb-btn" style="display:inline-block;text-decoration:none;margin:4px 0;">Open NDC Topology →</a>`;
    } else {
      html += '<p style="font-size:10px;color:#f39c12;">No NDC topology linked. Add an "NDC Topology" node and link it first.</p>';
    }
  }

  openRightPanel('Properties', html);
}

function _findLinkedNdcTopology() {
  const ndcNodes = graph.getElements().filter(el => el.get('nodeType') === 'ndc-topology');
  for (const node of ndcNodes) {
    const config = node.get('configData') || {};
    if (config.ndc_topology_id) return config.ndc_topology_id;
  }
  return null;
}

function _loadNdcTopologyList(cell) {
  fetch('/network/api/topologies')
    .then(r => r.ok ? r.json() : [])
    .then(topologies => {
      const container = document.getElementById('ndc-topology-list');
      if (!container) return;
      if (!topologies.length) {
        container.innerHTML = '<p style="font-size:11px;color:#7a8cb0;">No NDC topologies found. <a href="/network/canvas/new" target="_blank" style="color:#e94560;">Create one →</a></p>';
        return;
      }
      let html = '<div style="font-size:11px;font-weight:600;margin-bottom:4px;">Available Topologies:</div>';
      topologies.forEach(t => {
        const isLinked = cell.get('configData')?.ndc_topology_id === t.id;
        const updated = t.updated_at ? String(t.updated_at).substring(0, 10) : '';
        // data-* + addEventListener: id/name (incl. cross-canvas NDC data) never
        // reach a JS/HTML-injection sink. escapeAttr guards the attribute values.
        html += `<div class="ndc-topo-row" data-topo-id="${escapeAttr(t.id)}" data-topo-name="${escapeAttr(t.name || '')}" style="background:${isLinked ? '#0f3460' : '#0f2040'};border:1px solid ${isLinked ? '#3498db' : '#1e3a6e'};border-radius:4px;padding:6px 8px;margin:3px 0;cursor:pointer;">`;
        html += `<div style="font-weight:600;font-size:11px;">${escapeHtml(t.name || 'Untitled')}${isLinked ? ' ✓' : ''}</div>`;
        html += `<div style="font-size:10px;color:#7a8cb0;">${escapeHtml(t.classification || 'public')} · ${escapeHtml(updated)}</div>`;
        html += '</div>';
      });
      container.innerHTML = html;
      container.querySelectorAll('.ndc-topo-row').forEach(row => {
        row.addEventListener('click', () => linkNdcTopology(row.dataset.topoId, row.dataset.topoName));
      });
    })
    .catch(() => {
      const container = document.getElementById('ndc-topology-list');
      if (container) container.innerHTML = '<p style="font-size:11px;color:#e74c3c;">NDC not available. Is the Network Canvas enabled?</p>';
    });
}

function linkNdcTopology(topoId, topoName) {
  if (!selectedCell || selectedCell.get('nodeType') !== 'ndc-topology') return;
  pushUndo();
  selectedCell.set('configData', {
    ...(selectedCell.get('configData') || {}),
    ndc_topology_id: topoId,
    ndc_topology_name: topoName,
  });
  selectedCell.attr('label/text', topoName || 'NDC Topology');
  markDirty();
  openConfigPanel(selectedCell); // Refresh panel
  updateStatus('Linked to NDC: ' + topoName);
}

function fetchNdcHealth(topoId) {
  const panel = document.getElementById('ndc-health-panel');
  if (panel) panel.innerHTML = '<i style="color:#7a8cb0;font-size:10px;">Analyzing...</i>';

  // The former POST /cloud-analysis endpoint never existed — the panel hung on
  // "Analyzing..." forever. Use the real NDC analysis health endpoint
  // (GET /network/api/topologies/<id>/analysis/topology-health), which returns
  // an overall health score plus five scored dimensions. Adapt those to the panel.
  const _rate = (v) => (v >= 80 ? '#27ae60' : v >= 50 ? '#f39c12' : '#e74c3c');
  fetchJson('/network/api/topologies/' + topoId + '/analysis/topology-health', { method: 'GET' })
    .then(data => {
      if (!panel) return;
      const overall = Number(data && data.overall_health) || 0;
      const dims = (data && data.dimensions) || {};
      const _DIM_LABELS = {
        compliance: 'Compliance', security: 'Security', eol: 'End-of-Life',
        redundancy: 'Redundancy', capacity: 'Capacity',
      };

      let html = _section('Infrastructure Health');
      html += _metric('Overall Health', overall + '/100');
      html += _bar(overall, _rate(overall));
      Object.keys(_DIM_LABELS).forEach(key => {
        if (dims[key] === undefined || dims[key] === null) return;
        const v = Number(dims[key]) || 0;
        html += _metric(_DIM_LABELS[key], v + '/100');
        html += _bar(v, _rate(v));
      });
      html += `<div style="font-size:10px;color:#5a6e8c;margin-top:6px;">Live from linked NDC topology. <a href="/network/canvas/${escapeAttr(topoId)}" target="_blank" style="color:#3498db;">Open in NDC →</a></div>`;
      panel.innerHTML = html;
    })
    .catch(err => {
      const emsg = err && err.message ? err.message : 'topology unreachable';
      if (panel) {
        panel.innerHTML = '<p style="font-size:10px;color:#e74c3c;">Failed to fetch NDC health: '
          + escapeHtml(emsg) + '. Is the topology accessible and the Network Canvas enabled?</p>';
      }
      pdcToast('NDC health check failed: ' + emsg, 'error');
    });
}

function closeConfigPanel() {
  const panel = document.querySelector('.pc-config-panel');
  if (panel) panel.classList.remove('open');
}

function updateNodeLabel(val) {
  if (selectedCell) { pushUndo(); selectedCell.attr('label/text', val); markDirty(); }
}
