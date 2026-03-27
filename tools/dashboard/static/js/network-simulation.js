/**
 * Network Design Canvas — Simulation visualization layer
 * Renders simulation results on the JointJS canvas.
 */

/* ── Simulation Panel Controls ───────────────────────────────────────────────── */
function openSimPanel() {
  document.getElementById('sim-overlay').classList.remove('hidden');
  document.getElementById('sim-output').textContent = '';
}

function closeSimPanel() {
  document.getElementById('sim-overlay').classList.add('hidden');
  clearHighlights();
}

/* ── Run Simulation ───────────────────────────────────────────────────────────── */
async function runSimulation() {
  const simType = document.getElementById('sp-sim-type').value;
  const output = document.getElementById('sim-output');

  if (!currentTopoId || currentTopoId === 'new') {
    await saveTopology();
  }
  if (!currentTopoId || currentTopoId === 'new') {
    output.textContent = 'Error: Save the topology first.';
    return;
  }

  output.textContent = `Running ${simType}...`;
  clearHighlights();
  stopAnimations();
  clearOutages();

  // Collect extra params
  const extraParams = { sim_type: simType };
  const prefixEl = document.getElementById('sp-prefix');
  if (prefixEl && prefixEl.value) extraParams.prefix = prefixEl.value;
  const mtuEl = document.getElementById('sp-mtu-size');
  if (mtuEl && mtuEl.value) extraParams.mtu_size = parseInt(mtuEl.value);
  if (simType === 'blast_radius') {
    const hopsEl = document.getElementById('sp-max-hops');
    if (hopsEl && hopsEl.value) extraParams.max_hops = parseInt(hopsEl.value);
    // Use selected node as source; fall back to first node
    if (typeof selectedCell !== 'undefined' && selectedCell && selectedCell.isElement()) {
      extraParams.source = selectedCell.id;
    }
  }

  try {
    const r = await fetch(NC_BASE + `/api/topologies/${currentTopoId}/simulate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(extraParams)
    });
    const data = await r.json();
    const result = data.result || {};

    output.textContent = formatResult(simType, result);
    visualizeResult(simType, result);

  } catch (err) {
    output.textContent = 'Simulation error: ' + err.message;
  }
}

/* ── Format result text ───────────────────────────────────────────────────────── */
function formatResult(simType, result) {
  const lines = [];
  lines.push(`Simulation: ${simType.toUpperCase()}`);
  lines.push(`Summary: ${result.summary || '—'}`);
  lines.push('');

  switch (simType) {
    case 'ping':
      lines.push(`Source:      ${result.source || '?'}`);
      lines.push(`Destination: ${result.destination || '?'}`);
      lines.push(`Reachable:   ${result.reachable ? 'YES' : 'NO'}`);
      if (result.latency_ms != null) lines.push(`Latency:     ${result.latency_ms} ms`);
      if (result.hops?.length) lines.push(`Path: ${result.hops.join(' → ')}`);
      break;

    case 'traceroute':
      lines.push(`Source: ${result.source || '?'} → Dest: ${result.destination || '?'}`);
      lines.push('');
      (result.trace || []).forEach(h => {
        lines.push(`  Hop ${h.hop}: ${h.node} (${h.latency_ms} ms)`);
      });
      break;

    case 'spof':
      lines.push(`SPOFs found: ${(result.spof_nodes || []).length}`);
      (result.spof_nodes || []).forEach(n => lines.push(`  ✕ ${n}`));
      break;

    case 'failover':
      lines.push(`Resilience Score: ${result.resilience_score}%`);
      lines.push('');
      (result.risks || []).forEach(r => {
        lines.push(`  Node: ${r.node}`);
        lines.push(`  Impact: ${r.impact}`);
        lines.push(`  Fix: ${r.recommendation}`);
        lines.push('');
      });
      break;

    case 'load':
      lines.push(`Avg Utilization: ${result.avg_utilization_pct}%`);
      lines.push('');
      (result.utilization || []).forEach(u => {
        const bar = utilBar(u.utilization_pct);
        lines.push(`  ${(u.label || u.edge).padEnd(20)} ${bar} ${u.utilization_pct}% [${u.status.toUpperCase()}]`);
      });
      break;

    case 'bgp_bestpath':
      lines.push(`Source:      ${result.source || '?'}`);
      lines.push(`Destination: ${result.destination || '?'}`);
      lines.push(`Decision:    ${result.decision_reason || '—'}`);
      lines.push('');
      (result.paths || []).forEach((p, i) => {
        const marker = i === 0 ? '★ BEST' : `  #${i+1}`;
        lines.push(`${marker}: ${p.path.join(' → ')}`);
        lines.push(`    Weight=${p.weight} LP=${p.local_pref} AS_PATH=${p.as_path_length} MED=${p.med} Score=${p.score}`);
        if (p.hop_details) {
          p.hop_details.forEach(h => {
            lines.push(`      ${h.node}: ASN=${h.asn} LP=${h.local_pref} MED=${h.med} W=${h.weight}`);
          });
        }
        lines.push('');
      });
      break;

    case 'bgp_propagation':
      lines.push(`Originator: ${result.originator || '?'}`);
      lines.push(`Reached: ${result.nodes_reached}/${result.total_bgp_nodes} BGP nodes`);
      lines.push('');
      (result.waves || []).forEach(w => {
        lines.push(`Wave ${w.wave}: ${w.action}`);
        lines.push(`  Nodes: ${w.nodes.join(', ')}`);
        if (w.local_prefs) {
          Object.entries(w.local_prefs).forEach(([n, lp]) => {
            lines.push(`    ${n}: LOCAL_PREF=${lp}`);
          });
        }
        lines.push('');
      });
      break;

    case 'ospf_spf':
      lines.push(`SPF Root: ${result.root || '?'}`);
      lines.push(`ECMP nodes: ${(result.ecmp_nodes || []).join(', ') || 'none'}`);
      lines.push('');
      (result.spf_tree || []).forEach(n => {
        lines.push(`  ${n.node.padEnd(20)} Cost=${String(n.cost).padStart(5)} Hops=${n.hops} Path: ${n.path.join(' → ')}`);
      });
      break;

    case 'ospf_cost':
      lines.push(`Links: ${(result.link_costs || []).length}`);
      lines.push(`Load-balance groups: ${Object.keys(result.load_balance_groups || {}).length}`);
      lines.push(`ABR boundaries: ${(result.abr_boundaries || []).length}`);
      lines.push('');
      (result.link_costs || []).forEach(lc => {
        const abr = lc.is_abr_boundary ? ' [ABR]' : '';
        lines.push(`  Cost=${String(lc.cost).padStart(5)}  ${lc.link}  (${lc.label || '—'}) Area=${lc.area}${abr}`);
      });
      if (Object.keys(result.load_balance_groups || {}).length) {
        lines.push('');
        lines.push('Equal-Cost Load Balancing:');
        Object.entries(result.load_balance_groups).forEach(([cost, links]) => {
          lines.push(`  Cost ${cost}: ${links.length} parallel paths`);
          links.forEach(l => lines.push(`    ${l}`));
        });
      }
      break;

    case 'jumbo_mtu':
      const ready = result.jumbo_ready ? 'YES' : 'NO';
      lines.push(`Desired MTU:  ${result.desired_mtu}`);
      lines.push(`Path MTU:     ${result.path_mtu}`);
      lines.push(`Jumbo Ready:  ${ready}`);
      if (result.bottleneck) lines.push(`Bottleneck:   ${result.bottleneck}`);
      lines.push('');
      lines.push('Path MTU Detail:');
      (result.path_detail || []).forEach(p => {
        const icon = p.status === 'ok' ? '  ' : '✕ ';
        lines.push(`  ${icon}${p.node.padEnd(20)} MTU=${p.mtu} ${p.status}`);
      });
      lines.push('');
      const fragPts = result.fragmentation_points || [];
      if (fragPts.length) {
        lines.push(`Fragmentation points (${fragPts.length}):`);
        fragPts.forEach(n => lines.push(`  ✕ ${n}`));
      } else {
        lines.push('No fragmentation points — all nodes support jumbo frames.');
      }
      break;

    case 'dwdm_optical':
      const sys = result.system || {};
      lines.push(`DWDM Optical Quality Analysis`);
      lines.push(`═══════════════════════════════`);
      lines.push(`Spans:     ${sys.total_spans}`);
      lines.push(`Distance:  ${sys.total_distance_km} km`);
      lines.push(`OSNR:      ${sys.osnr_db} dB  [${(sys.status || '').toUpperCase()}]`);
      lines.push(`EDFAs:     ${sys.edfa_count}`);
      lines.push(`Net Loss:  ${sys.total_net_loss_db} dB`);
      lines.push(`CD Total:  ${sys.total_cd_ps_nm} ps/nm`);
      lines.push(`PMD Total: ${sys.total_pmd_ps} ps`);
      lines.push('');
      lines.push('Per-Span Detail:');
      (result.spans || []).forEach(s => {
        const q = s.quality === 'good' ? '  ' : s.quality === 'warning' ? '! ' : '✕ ';
        lines.push(`${q}${s.span}`);
        lines.push(`    ${s.distance_km}km | Loss=${s.total_loss_db}dB | EDFA=${s.edfa_gain_db}dB | Net=${s.net_loss_db}dB`);
        lines.push(`    CD=${s.chromatic_dispersion_ps_nm} ps/nm | PMD=${s.pmd_ps} ps | ${s.protocol}`);
      });
      break;

    case 'fiber_budget':
      lines.push(`Fiber Power Budget`);
      lines.push(`═══════════════════`);
      lines.push(`TX:           ${result.tx_node} @ ${result.tx_power_dbm} dBm`);
      lines.push(`RX:           ${result.rx_node}`);
      lines.push(`RX Sens:      ${result.rx_sensitivity_dbm} dBm`);
      lines.push(`RX Power:     ${result.rx_power_dbm} dBm`);
      lines.push(`Total Loss:   ${result.total_loss_db} dB`);
      lines.push(`Margin:       ${result.margin_db} dB  [${(result.status || '').toUpperCase()}]`);
      lines.push('');
      lines.push('Power Level Walk:');
      (result.budget_detail || []).forEach(b => {
        const bar = b.power_dbm > -10 ? '█' : b.power_dbm > -20 ? '▓' : '░';
        lines.push(`  ${bar} ${b.node.padEnd(22)} ${b.action.padEnd(10)} ${String(b.loss_db > 0 ? '-' + b.loss_db : b.loss_db < 0 ? '+' + Math.abs(b.loss_db) : '—').padStart(8)} dB  →  ${b.power_dbm} dBm`);
      });
      break;

    case 'blast_radius': {
      const riskColors = { CRITICAL: '!!!', HIGH: '!! ', MEDIUM: '!  ', LOW: '   ' };
      lines.push(`Source:       ${result.source || '?'} (compromised)`);
      lines.push(`Max Hops:     ${result.max_hops || 3}`);
      lines.push(`Risk:         ${riskColors[result.risk] || ''} ${result.risk || '?'}`);
      lines.push(`Blast:        ${result.compromised_count}/${result.total_nodes} devices (${result.blast_pct}%)`);
      lines.push(`Blocked by:   ${result.blocked_count} firewall(s)`);
      lines.push(`Unreachable:  ${result.unreachable_count} device(s)`);
      lines.push(`ZT Score:     ${result.zero_trust_score}/100`);
      lines.push('');
      const blastBar = utilBar(result.blast_pct);
      lines.push(`Blast Radius: ${blastBar} ${result.blast_pct}%`);
      lines.push('');
      (result.hop_layers || []).forEach(layer => {
        lines.push(`─── Hop ${layer.hop} ───`);
        (layer.devices || []).forEach(d => {
          let icon = '  ';
          if (d.status === 'blocked')       icon = '🛡';
          else if (d.status === 'zone_boundary') icon = '⚠';
          else                              icon = '✕ ';
          lines.push(`  ${icon} ${d.label} (${d.type}) [${d.status.toUpperCase()}]`);
        });
      });
      if (result.narrative) {
        lines.push('');
        lines.push('Analysis:');
        lines.push(result.narrative);
      }
      break;
    }

    default:
      lines.push(JSON.stringify(result, null, 2));
  }

  return lines.join('\n');
}

function utilBar(pct) {
  const filled = Math.round(pct / 10);
  return '[' + '█'.repeat(filled) + '░'.repeat(10 - filled) + ']';
}

/* ── Visual highlighting on canvas ───────────────────────────────────────────── */
const HIGHLIGHTED_CELLS = [];

function clearHighlights() {
  HIGHLIGHTED_CELLS.forEach(id => {
    const cell = graph.getCell(id);
    if (!cell) return;
    if (cell.isElement()) {
      const type = cell.get('nodeType') || 'unknown';
      const style = (typeof getStyle !== 'undefined') ? getStyle(type) : { fill: '#1a1a2e', stroke: '#7a8cb0' };
      cell.attr('body/fill', style.fill);
      cell.attr('body/stroke', style.stroke);
    } else {
      cell.attr('line/stroke', '#e94560');
      cell.attr('line/strokeWidth', 2);
    }
  });
  HIGHLIGHTED_CELLS.length = 0;
}

function highlightSpof(spofLabels) {
  graph.getElements().forEach(el => {
    const label = el.attr('label/text') || '';
    if (spofLabels.includes(label)) {
      el.attr('body/fill', '#3a0000');
      el.attr('body/stroke', '#e74c3c');
      HIGHLIGHTED_CELLS.push(el.id);
    }
  });
}

function highlightPath(hopLabels) {
  // Collect node ids by label
  const labelToId = {};
  graph.getElements().forEach(el => {
    const lbl = el.attr('label/text') || '';
    labelToId[lbl] = el.id;
  });

  hopLabels.forEach(lbl => {
    const id = labelToId[lbl];
    if (!id) return;
    const el = graph.getCell(id);
    if (el) {
      el.attr('body/fill', '#0f2b0f');
      el.attr('body/stroke', '#27ae60');
      HIGHLIGHTED_CELLS.push(id);
    }
  });

  // Highlight links in path
  for (let i = 0; i < hopLabels.length - 1; i++) {
    const srcId = labelToId[hopLabels[i]];
    const tgtId = labelToId[hopLabels[i + 1]];
    if (!srcId || !tgtId) continue;
    graph.getLinks().forEach(lk => {
      const src = lk.get('source')?.id;
      const tgt = lk.get('target')?.id;
      if ((src === srcId && tgt === tgtId) || (src === tgtId && tgt === srcId)) {
        lk.attr('line/stroke', '#27ae60');
        lk.attr('line/strokeWidth', 3);
        HIGHLIGHTED_CELLS.push(lk.id);
      }
    });
  }
}

function applyLoadHeatmap(utilization) {
  // Map edge ids/labels to link colors
  const edgeMap = {};
  (utilization || []).forEach(u => {
    edgeMap[u.edge] = u;
  });

  graph.getLinks().forEach(lk => {
    const uid = edgeMap[lk.id];
    if (!uid) return;
    const pct = uid.utilization_pct;
    let color;
    if (pct > 75) color = '#e74c3c';      // critical — red
    else if (pct > 50) color = '#f39c12'; // warning — orange
    else color = '#27ae60';               // ok — green
    lk.attr('line/stroke', color);
    lk.attr('line/strokeWidth', 2 + Math.floor(pct / 30));
    HIGHLIGHTED_CELLS.push(lk.id);
  });
}

function visualizeResult(simType, result) {
  switch (simType) {
    case 'ping':
      if (result.hops?.length) highlightPath(result.hops);
      break;
    case 'traceroute':
      if (result.trace?.length) {
        const hopLabels = result.trace.map(h => h.node);
        highlightPath(hopLabels);
      }
      break;
    case 'spof':
      if (result.spof_nodes?.length) highlightSpof(result.spof_nodes);
      break;
    case 'failover':
      if (result.risks?.length) highlightSpof(result.risks.map(r => r.node));
      break;
    case 'load':
      applyLoadHeatmap(result.utilization);
      break;
    case 'blast_radius':
      highlightBlastRadius(result);
      break;
  }
}

/* ── Blast Radius Visualization ────────────────────────────────────────────── */
function highlightBlastRadius(result) {
  // Gradient colors: closer hops = darker red, further = lighter
  const HOP_COLORS = [
    { fill: '#4a0000', stroke: '#ff1744' },  // hop 1 — bright red
    { fill: '#3a0a00', stroke: '#ff6d00' },  // hop 2 — orange
    { fill: '#3a2a00', stroke: '#ffc107' },  // hop 3 — amber
    { fill: '#2a2a00', stroke: '#ffeb3b' },  // hop 4 — yellow
    { fill: '#1a2a00', stroke: '#cddc39' },  // hop 5+
  ];
  const BLOCKED_STYLE = { fill: '#002a1a', stroke: '#00e676' };  // green = firewall held
  const SOURCE_STYLE  = { fill: '#4a0022', stroke: '#ff1744' };  // pulsing red for source
  const ZONE_STYLE    = { fill: '#1a1a00', stroke: '#ffc107' };  // amber outline

  // Build label-to-id map
  const labelToId = {};
  const idToEl = {};
  graph.getElements().forEach(el => {
    const lbl = el.attr('label/text') || '';
    labelToId[lbl] = el.id;
    idToEl[el.id] = el;
  });

  // Highlight source node
  const srcLabel = result.source;
  const srcElId = labelToId[srcLabel];
  if (srcElId) {
    const srcEl = idToEl[srcElId];
    if (srcEl) {
      srcEl.attr('body/fill', SOURCE_STYLE.fill);
      srcEl.attr('body/stroke', SOURCE_STYLE.stroke);
      srcEl.attr('body/strokeWidth', 3);
      HIGHLIGHTED_CELLS.push(srcElId);
    }
  }

  // Highlight each hop layer
  (result.hop_layers || []).forEach(layer => {
    const hopIdx = Math.min(layer.hop - 1, HOP_COLORS.length - 1);
    const hopStyle = HOP_COLORS[hopIdx];

    (layer.devices || []).forEach(d => {
      const elId = labelToId[d.label];
      if (!elId) return;
      const el = idToEl[elId];
      if (!el) return;

      let style;
      if (d.status === 'blocked') {
        style = BLOCKED_STYLE;
      } else if (d.status === 'zone_boundary') {
        style = ZONE_STYLE;
      } else {
        style = hopStyle;
      }

      el.attr('body/fill', style.fill);
      el.attr('body/stroke', style.stroke);
      el.attr('body/strokeWidth', d.status === 'blocked' ? 3 : 2);
      HIGHLIGHTED_CELLS.push(elId);
    });
  });

  // Highlight edges between compromised nodes
  const compromisedIds = new Set();
  if (srcElId) compromisedIds.add(srcElId);
  (result.hop_layers || []).forEach(layer => {
    (layer.devices || []).forEach(d => {
      if (d.status === 'compromised' || d.status === 'zone_boundary') {
        const eid = labelToId[d.label];
        if (eid) compromisedIds.add(eid);
      }
    });
  });

  graph.getLinks().forEach(lk => {
    const src = lk.get('source')?.id;
    const tgt = lk.get('target')?.id;
    if (compromisedIds.has(src) && compromisedIds.has(tgt)) {
      lk.attr('line/stroke', '#e74c3c');
      lk.attr('line/strokeWidth', 3);
      HIGHLIGHTED_CELLS.push(lk.id);
    }
    // Show blocked edges (to firewall) as dashed green
    const blockedIds = new Set();
    (result.hop_layers || []).forEach(layer => {
      (layer.devices || []).forEach(d => {
        if (d.status === 'blocked') {
          const eid = labelToId[d.label];
          if (eid) blockedIds.add(eid);
        }
      });
    });
    if ((compromisedIds.has(src) && blockedIds.has(tgt)) ||
        (compromisedIds.has(tgt) && blockedIds.has(src))) {
      lk.attr('line/stroke', '#00e676');
      lk.attr('line/strokeWidth', 3);
      lk.attr('line/strokeDasharray', '6,3');
      HIGHLIGHTED_CELLS.push(lk.id);
    }
  });
}

/* ── Packet Animation ─────────────────────────────────────────────────────────── */
let _animFrames = [];

function animatePacketPath(hopLabels, opts = {}) {
  const speed = opts.speed || 600; // ms per hop
  const color = opts.color || '#27ae60';
  const size  = opts.size || 8;

  // Map labels → element ids
  const labelToId = {};
  graph.getElements().forEach(el => { labelToId[el.attr('label/text') || ''] = el.id; });

  // Build ordered list of (sourceView, targetView, link) for each hop
  const hops = [];
  for (let i = 0; i < hopLabels.length - 1; i++) {
    const srcId = labelToId[hopLabels[i]];
    const tgtId = labelToId[hopLabels[i + 1]];
    if (!srcId || !tgtId) continue;
    const link = graph.getLinks().find(lk => {
      const s = lk.get('source')?.id, t = lk.get('target')?.id;
      return (s === srcId && t === tgtId) || (s === tgtId && t === srcId);
    });
    if (link) hops.push({ srcId, tgtId, link });
  }
  if (!hops.length) return;

  // Create SVG circle for the packet
  const svgRoot = paper.svg;
  const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  circle.setAttribute('r', size);
  circle.setAttribute('fill', color);
  circle.setAttribute('opacity', '0.9');
  circle.classList.add('sim-packet');
  svgRoot.appendChild(circle);

  let hopIdx = 0;
  function animateHop() {
    if (hopIdx >= hops.length) {
      // Trail end: flash destination, remove circle
      setTimeout(() => { circle.remove(); }, 300);
      return;
    }
    const hop = hops[hopIdx];
    const linkView = paper.findViewByModel(hop.link);
    if (!linkView) { hopIdx++; animateHop(); return; }

    // Get path points from SVG
    const pathEl = linkView.el.querySelector('path');
    if (!pathEl) { hopIdx++; animateHop(); return; }
    const pathLen = pathEl.getTotalLength();
    const startTime = performance.now();

    function step(ts) {
      const elapsed = ts - startTime;
      const t = Math.min(elapsed / speed, 1);
      const pt = pathEl.getPointAtLength(t * pathLen);
      circle.setAttribute('cx', pt.x);
      circle.setAttribute('cy', pt.y);
      if (t < 1) {
        const frameId = requestAnimationFrame(step);
        _animFrames.push(frameId);
      } else {
        hopIdx++;
        animateHop();
      }
    }
    const frameId = requestAnimationFrame(step);
    _animFrames.push(frameId);
  }
  animateHop();
}

function stopAnimations() {
  _animFrames.forEach(id => cancelAnimationFrame(id));
  _animFrames.length = 0;
  document.querySelectorAll('.sim-packet').forEach(el => el.remove());
  document.querySelectorAll('.outage-marker').forEach(el => el.remove());
}

/* ── Link Outage / Circuit Cut ────────────────────────────────────────────────── */
let _outageLinks = [];

function simulateLinkOutage(linkId) {
  const link = graph.getCell(linkId);
  if (!link || !link.isLink()) return;

  // Visual: make link dashed red with X marker
  link.attr('line/stroke', '#e74c3c');
  link.attr('line/strokeWidth', 3);
  link.attr('line/strokeDasharray', '8,4');
  HIGHLIGHTED_CELLS.push(linkId);
  _outageLinks.push(linkId);

  // Add X marker at midpoint
  const linkView = paper.findViewByModel(link);
  if (linkView) {
    const pathEl = linkView.el.querySelector('path');
    if (pathEl) {
      const midPt = pathEl.getPointAtLength(pathEl.getTotalLength() / 2);
      const svgRoot = paper.svg;
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.classList.add('outage-marker');
      g.setAttribute('transform', `translate(${midPt.x}, ${midPt.y})`);

      // Red circle background
      const bg = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      bg.setAttribute('r', '12');
      bg.setAttribute('fill', '#e74c3c');
      bg.setAttribute('opacity', '0.9');
      g.appendChild(bg);

      // X text
      const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      txt.setAttribute('text-anchor', 'middle');
      txt.setAttribute('dy', '5');
      txt.setAttribute('fill', '#fff');
      txt.setAttribute('font-size', '14');
      txt.setAttribute('font-weight', 'bold');
      txt.textContent = '✕';
      g.appendChild(txt);
      svgRoot.appendChild(g);
    }
  }
}

function clearOutages() {
  _outageLinks.forEach(id => {
    const link = graph.getCell(id);
    if (link) {
      link.attr('line/strokeDasharray', '');
    }
  });
  _outageLinks.length = 0;
  document.querySelectorAll('.outage-marker').forEach(el => el.remove());
}

/* ── Enhanced Run Simulation — with outage support ────────────────────────────── */
async function runOutageSimulation() {
  // Let user click a link to cut it, then re-run simulations
  setStatus('Click a link to simulate circuit cut...');
  document.getElementById('sim-output').textContent = 'Click on a link in the canvas to simulate an outage.\nThen click "Run" to see impact.';

  paper.on('link:pointerclick', function _outagePick(view) {
    paper.off('link:pointerclick', _outagePick);
    const link = view.model;
    simulateLinkOutage(link.id);

    const srcLabel = (graph.getCell(link.get('source')?.id) || {}).attr?.('label/text') || '?';
    const tgtLabel = (graph.getCell(link.get('target')?.id) || {}).attr?.('label/text') || '?';
    const msg = `Circuit cut: ${srcLabel} — ${tgtLabel}\nLink ${link.id} is now DOWN.`;
    document.getElementById('sim-output').textContent = msg;
    setStatus('Link outage simulated — run SPOF or Failover to see impact');
  });
}

/* ── Zoom / Pan Controls ──────────────────────────────────────────────────────── */
let _zoomLevel = 1;
const ZOOM_STEP = 0.15;
const ZOOM_MIN = 0.3;
const ZOOM_MAX = 3;

function zoomIn() {
  _zoomLevel = Math.min(_zoomLevel + ZOOM_STEP, ZOOM_MAX);
  paper.scale(_zoomLevel, _zoomLevel);
  updateZoomDisplay();
}

function zoomOut() {
  _zoomLevel = Math.max(_zoomLevel - ZOOM_STEP, ZOOM_MIN);
  paper.scale(_zoomLevel, _zoomLevel);
  updateZoomDisplay();
}

function zoomFit() {
  paper.scaleContentToFit({ padding: 30, maxScale: 2 });
  _zoomLevel = paper.scale().sx;
  updateZoomDisplay();
}

function zoomReset() {
  _zoomLevel = 1;
  paper.scale(1, 1);
  paper.translate(0, 0);
  updateZoomDisplay();
}

function updateZoomDisplay() {
  const el = document.getElementById('sb-zoom');
  if (el) el.textContent = `Zoom: ${Math.round(_zoomLevel * 100)}%`;
  // Reposition boundary zones after zoom/pan
  if (typeof repositionBoundaries === 'function') repositionBoundaries();
}

/* ── Mouse wheel zoom ─────────────────────────────────────────────────────────── */
function initZoomWheel() {
  const canvasEl = document.getElementById('canvas-container');
  if (!canvasEl) return;
  canvasEl.addEventListener('wheel', (e) => {
    e.preventDefault();
    const delta = e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP;
    _zoomLevel = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, _zoomLevel + delta));
    paper.scale(_zoomLevel, _zoomLevel);
    updateZoomDisplay();
  }, { passive: false });
}

/* ── Enhanced visualize with animation ────────────────────────────────────────── */

function visualizeResult(simType, result) {
  stopAnimations();
  switch (simType) {
    case 'ping':
      if (result.hops?.length) {
        highlightPath(result.hops);
        animatePacketPath(result.hops, { color: '#27ae60', speed: 500 });
      }
      break;
    case 'traceroute':
      if (result.trace?.length) {
        const hopLabels = result.trace.map(h => h.node);
        highlightPath(hopLabels);
        animatePacketPath(hopLabels, { color: '#3498db', speed: 700 });
      }
      break;
    case 'spof':
      if (result.spof_nodes?.length) highlightSpof(result.spof_nodes);
      break;
    case 'failover':
      if (result.risks?.length) highlightSpof(result.risks.map(r => r.node));
      break;
    case 'load':
      applyLoadHeatmap(result.utilization);
      break;
    case 'bgp_bestpath':
      if (result.best_path?.length) {
        highlightPath(result.best_path);
        animatePacketPath(result.best_path, { color: '#f39c12', speed: 600, size: 10 });
      }
      // Dim non-best paths
      (result.paths || []).slice(1).forEach(p => {
        highlightPathDim(p.path);
      });
      break;
    case 'bgp_propagation':
      // Animate wave-by-wave propagation
      animateBgpWaves(result.waves || []);
      break;
    case 'ospf_spf':
      if (result.best_path?.length) {
        highlightPath(result.best_path);
        animatePacketPath(result.best_path, { color: '#2ecc71', speed: 400 });
      }
      // Highlight ECMP nodes in blue
      (result.ecmp_nodes || []).forEach(label => {
        graph.getElements().forEach(el => {
          if (el.attr('label/text') === label) {
            el.attr('body/stroke', '#3498db');
            el.attr('body/fill', '#0f1a3a');
            HIGHLIGHTED_CELLS.push(el.id);
          }
        });
      });
      break;
    case 'ospf_cost':
      // Color links by cost (green=low, yellow=mid, red=high)
      (result.link_costs || []).forEach(lc => {
        const cost = lc.cost;
        let color;
        if (cost <= 4) color = '#27ae60';
        else if (cost <= 10) color = '#f39c12';
        else color = '#e74c3c';
        graph.getLinks().forEach(lk => {
          const src = graph.getCell(lk.get('source')?.id);
          const tgt = graph.getCell(lk.get('target')?.id);
          const srcLabel = src?.attr?.('label/text') || '';
          const tgtLabel = tgt?.attr?.('label/text') || '';
          if (lc.link.includes(srcLabel) && lc.link.includes(tgtLabel)) {
            lk.attr('line/stroke', color);
            lk.attr('line/strokeWidth', 3);
            HIGHLIGHTED_CELLS.push(lk.id);
          }
        });
      });
      break;
    case 'dwdm_optical':
      // Color optical spans by quality
      (result.spans || []).forEach(s => {
        let color = '#27ae60';
        if (s.quality === 'warning') color = '#f39c12';
        if (s.quality === 'critical') color = '#e74c3c';
        graph.getLinks().forEach(lk => {
          const src = graph.getCell(lk.get('source')?.id);
          const tgt = graph.getCell(lk.get('target')?.id);
          const srcL = src?.attr?.('label/text') || '';
          const tgtL = tgt?.attr?.('label/text') || '';
          if (s.span.includes(srcL) && s.span.includes(tgtL)) {
            lk.attr('line/stroke', color);
            lk.attr('line/strokeWidth', 3);
            HIGHLIGHTED_CELLS.push(lk.id);
          }
        });
      });
      // Highlight EDFAs in blue
      graph.getElements().forEach(el => {
        if (el.get('nodeType') === 'edfa') {
          el.attr('body/stroke', '#00bfff');
          el.attr('body/fill', '#0f2b3a');
          HIGHLIGHTED_CELLS.push(el.id);
        }
      });
      break;
    case 'fiber_budget':
      // Color nodes by power level
      (result.budget_detail || []).forEach(b => {
        graph.getElements().forEach(el => {
          if (el.attr('label/text') === b.node) {
            let color, fill;
            if (b.power_dbm > -10) { color = '#27ae60'; fill = '#0f2b0f'; }
            else if (b.power_dbm > -20) { color = '#f39c12'; fill = '#2b1a0f'; }
            else { color = '#e74c3c'; fill = '#2b0f0f'; }
            el.attr('body/stroke', color);
            el.attr('body/fill', fill);
            HIGHLIGHTED_CELLS.push(el.id);
          }
        });
      });
      break;
    case 'jumbo_mtu':
      // Highlight fragmentation points in red, ok in green
      (result.node_audit || []).forEach(nm => {
        graph.getElements().forEach(el => {
          if (el.attr('label/text') === nm.node) {
            if (nm.supports_jumbo) {
              el.attr('body/stroke', '#27ae60');
              el.attr('body/fill', '#0f2b0f');
            } else {
              el.attr('body/stroke', '#e74c3c');
              el.attr('body/fill', '#3a0000');
            }
            HIGHLIGHTED_CELLS.push(el.id);
          }
        });
      });
      // Animate path showing where fragmentation happens
      if (result.path_detail?.length) {
        const pathLabels = result.path_detail.map(p => p.node);
        animatePacketPath(pathLabels, {
          color: result.jumbo_ready ? '#27ae60' : '#e74c3c',
          speed: 600, size: 12
        });
      }
      break;
  }
}

/* ── BGP Wave Animation ──────────────────────────────────────────────────────── */
function animateBgpWaves(waves) {
  const labelToEl = {};
  graph.getElements().forEach(el => { labelToEl[el.attr('label/text') || ''] = el; });

  let delay = 0;
  waves.forEach((wave, i) => {
    setTimeout(() => {
      wave.nodes.forEach(label => {
        const el = labelToEl[label];
        if (!el) return;
        // Flash the node
        el.attr('body/stroke', i === 0 ? '#f39c12' : '#27ae60');
        el.attr('body/fill', i === 0 ? '#2b1a0f' : '#0f2b0f');
        HIGHLIGHTED_CELLS.push(el.id);
      });
      setStatus(`BGP Wave ${wave.wave}: ${wave.nodes.join(', ')}`);
    }, delay);
    delay += 1200;
  });
}

/* ── Dim highlight for non-best paths ────────────────────────────────────────── */
function highlightPathDim(hopLabels) {
  const labelToId = {};
  graph.getElements().forEach(el => { labelToId[el.attr('label/text') || ''] = el.id; });

  for (let i = 0; i < hopLabels.length - 1; i++) {
    const srcId = labelToId[hopLabels[i]];
    const tgtId = labelToId[hopLabels[i + 1]];
    if (!srcId || !tgtId) continue;
    graph.getLinks().forEach(lk => {
      const src = lk.get('source')?.id, tgt = lk.get('target')?.id;
      if ((src === srcId && tgt === tgtId) || (src === tgtId && tgt === srcId)) {
        lk.attr('line/stroke', '#555');
        lk.attr('line/strokeWidth', 1);
        lk.attr('line/strokeDasharray', '4,4');
        HIGHLIGHTED_CELLS.push(lk.id);
      }
    });
  }
}

/* ── Sim type change — show/hide extra params ────────────────────────────────── */
function onSimTypeChange(val) {
  const extra = document.getElementById('sim-extra-params');
  const prefixGrp = document.getElementById('sim-prefix-group');
  const mtuGrp = document.getElementById('sim-mtu-group');
  const blastGrp = document.getElementById('sim-blast-group');
  if (!extra) return;

  const showPrefix = ['bgp_bestpath', 'bgp_propagation'].includes(val);
  const showMtu = val === 'jumbo_mtu';
  const showBlast = val === 'blast_radius';

  extra.classList.toggle('hidden', !showPrefix && !showMtu && !showBlast);
  if (prefixGrp) prefixGrp.style.display = showPrefix ? '' : 'none';
  if (mtuGrp) mtuGrp.style.display = showMtu ? '' : 'none';
  if (blastGrp) blastGrp.style.display = showBlast ? '' : 'none';
}

/* ══════════════════════════════════════════════════════════════════════════════
 * FIPS 140 Encryption Coverage Visualizer
 * Highlights unprotected links in red, protected links in green.
 * ══════════════════════════════════════════════════════════════════════════════ */

const ENCRYPTION_TYPES = new Set([
  'fips-140-l1','fips-140-l2','fips-140-l3','fips-140-l4',
  'hsm','type1-encryptor',
  'kg-175d','kg-175g','kg-250','kg-340','kg-245x','kg-255',
  'macsec','qkd-device','tls-terminator',
]);

const ENCRYPTED_PROTOCOLS = new Set([
  'IPSec','IPSec ESP','GRE/IPSec','mTLS','TLS','DTLS','MACsec','BB84',
]);

let _fipsOverlayActive = false;
let _fipsOriginalStyles = new Map(); // linkId -> {stroke, strokeWidth, strokeDasharray}

function toggleFipsOverlay() {
  if (_fipsOverlayActive) {
    clearFipsOverlay();
  } else {
    showFipsOverlay();
  }
}

function showFipsOverlay() {
  if (!graph) return;
  _fipsOverlayActive = true;
  _fipsOriginalStyles.clear();

  const elements = graph.getElements();
  const links = graph.getLinks();

  // Build set of encryption device IDs
  const encryptionDeviceIds = new Set();
  elements.forEach(el => {
    const ntype = el.get('nodeType') || '';
    if (ENCRYPTION_TYPES.has(ntype)) encryptionDeviceIds.add(el.id);
  });

  // Build adjacency: which nodes are directly connected to encryption devices
  const protectedNodes = new Set(encryptionDeviceIds);
  links.forEach(link => {
    const srcId = link.get('source')?.id;
    const tgtId = link.get('target')?.id;
    if (encryptionDeviceIds.has(srcId)) protectedNodes.add(tgtId);
    if (encryptionDeviceIds.has(tgtId)) protectedNodes.add(srcId);
  });

  let protectedCount = 0;
  let unprotectedCount = 0;

  links.forEach(link => {
    const srcId = link.get('source')?.id;
    const tgtId = link.get('target')?.id;
    const proto = link.get('protocol') || '';
    const lineAttrs = link.attr('line') || {};

    // Save original style
    _fipsOriginalStyles.set(link.id, {
      stroke: lineAttrs.stroke,
      strokeWidth: lineAttrs.strokeWidth,
      strokeDasharray: lineAttrs.strokeDasharray,
    });

    // Check if link is protected
    const hasEncryptedProto = ENCRYPTED_PROTOCOLS.has(proto);
    const srcIsEncDev = encryptionDeviceIds.has(srcId);
    const tgtIsEncDev = encryptionDeviceIds.has(tgtId);
    const srcProtected = protectedNodes.has(srcId);
    const tgtProtected = protectedNodes.has(tgtId);

    const isProtected = hasEncryptedProto || srcIsEncDev || tgtIsEncDev || (srcProtected && tgtProtected);

    if (isProtected) {
      link.attr('line/stroke', '#27ae60');
      link.attr('line/strokeWidth', 3);
      link.removeAttr('line/strokeDasharray');
      protectedCount++;
    } else {
      link.attr('line/stroke', '#e74c3c');
      link.attr('line/strokeWidth', 4);
      link.attr('line/strokeDasharray', '8,4');
      unprotectedCount++;
    }
  });

  // Highlight encryption devices with glow
  elements.forEach(el => {
    const ntype = el.get('nodeType') || '';
    if (ENCRYPTION_TYPES.has(ntype)) {
      const view = paper.findViewByModel(el);
      if (view) view.highlight();
    }
  });

  const total = protectedCount + unprotectedCount;
  const pct = total ? Math.round(protectedCount / total * 100) : 100;
  const btn = document.getElementById('tb-fips-btn');
  if (btn) { btn.classList.add('active'); btn.title = `${pct}% encrypted`; }
  setStatus(`FIPS Coverage: ${protectedCount}/${total} links protected (${pct}%) — ${unprotectedCount} unprotected`);
}

function clearFipsOverlay() {
  if (!graph) return;
  _fipsOverlayActive = false;

  graph.getLinks().forEach(link => {
    const orig = _fipsOriginalStyles.get(link.id);
    if (orig) {
      link.attr('line/stroke', orig.stroke || '#e94560');
      link.attr('line/strokeWidth', orig.strokeWidth || 2);
      if (orig.strokeDasharray) {
        link.attr('line/strokeDasharray', orig.strokeDasharray);
      } else {
        link.removeAttr('line/strokeDasharray');
      }
    }
  });

  // Remove highlights from encryption devices
  graph.getElements().forEach(el => {
    const ntype = el.get('nodeType') || '';
    if (ENCRYPTION_TYPES.has(ntype)) {
      const view = paper.findViewByModel(el);
      if (view) view.unhighlight();
    }
  });

  _fipsOriginalStyles.clear();
  const btn = document.getElementById('tb-fips-btn');
  if (btn) btn.classList.remove('active');
  setStatus('FIPS overlay cleared');
}

/* ══════════════════════════════════════════════════════════════════════════════
 * Classification Banner Overlay
 * Blank by default. User can set text via toolbar input.
 * Persists per-topology in configData._classificationBanner.
 * ══════════════════════════════════════════════════════════════════════════════ */

let _bannerEl = null;

function initClassificationBanner() {
  // Create banner element if not exists
  if (!_bannerEl) {
    _bannerEl = document.createElement('div');
    _bannerEl.id = 'canvas-classification-banner';
    _bannerEl.className = 'canvas-classification-banner';
    const layout = document.querySelector('.canvas-layout');
    if (layout) layout.prepend(_bannerEl);
  }
  updateBannerDisplay();
}

function updateBannerDisplay() {
  if (!_bannerEl) return;
  const input = document.getElementById('banner-text-input');
  const text = input ? input.value.trim() : '';
  if (text) {
    _bannerEl.textContent = text;
    _bannerEl.style.display = 'block';
  } else {
    _bannerEl.textContent = '';
    _bannerEl.style.display = 'none';
  }
}

function onBannerTextChange() {
  updateBannerDisplay();
  // Save to topology metadata
  if (typeof markDirty === 'function') markDirty();
}

/* ══════════════════════════════════════════════════════════════════════════════
 * Smart Auto-Layout (dagre)
 * Uses JointJS built-in DirectedGraph layout with dagre.
 * ══════════════════════════════════════════════════════════════════════════════ */

function autoLayout(direction) {
  if (!graph || !joint.layout || !joint.layout.DirectedGraph) {
    // dagre not available — try basic grid layout fallback
    autoLayoutGrid();
    return;
  }

  if (typeof pushUndo === 'function') pushUndo();

  const dir = direction || 'TB'; // TB=top-bottom, LR=left-right

  joint.layout.DirectedGraph.layout(graph, {
    dagre: dagre,
    graphlib: dagre.graphlib,
    setVertices: false,
    setLabels: false,
    rankDir: dir,
    rankSep: 80,
    nodeSep: 60,
    edgeSep: 40,
    marginX: 40,
    marginY: 40,
  });

  if (typeof markDirty === 'function') markDirty();
  if (typeof updateStatusBar === 'function') updateStatusBar();
  setStatus('Auto-layout applied (' + dir + ')');
}

function autoLayoutGrid() {
  if (!graph) return;
  if (typeof pushUndo === 'function') pushUndo();

  const elements = graph.getElements();
  // Separate zone/text nodes from devices
  const devices = elements.filter(el => {
    const t = el.get('nodeType') || '';
    return !t.startsWith('draw-') && !t.startsWith('text-');
  });

  const cols = Math.ceil(Math.sqrt(devices.length));
  const spacingX = 150;
  const spacingY = 100;

  devices.forEach((el, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    el.position(40 + col * spacingX, 40 + row * spacingY);
  });

  if (typeof markDirty === 'function') markDirty();
  if (typeof updateStatusBar === 'function') updateStatusBar();
  setStatus('Grid layout applied');
}

/* ══════════════════════════════════════════════════════════════════════════════
 * AI Topology Generator — natural language to canvas
 * Uses local Ollama LLM (air-gap compatible, scanner tier)
 * ══════════════════════════════════════════════════════════════════════════════ */

function openAiGenerateDialog() {
  document.getElementById('ai-generate-overlay').classList.remove('hidden');
  document.getElementById('ai-gen-description').focus();
  document.getElementById('ai-gen-status').style.display = 'none';
}

function closeAiGenerateDialog() {
  document.getElementById('ai-generate-overlay').classList.add('hidden');
}

async function runAiGenerate() {
  const desc = document.getElementById('ai-gen-description').value.trim();
  if (!desc) { alert('Please describe the network you want to generate.'); return; }

  const btn = document.getElementById('ai-gen-btn');
  const statusEl = document.getElementById('ai-gen-status');
  btn.disabled = true;
  btn.textContent = 'Generating...';
  statusEl.style.display = 'block';
  statusEl.innerHTML = '<span style="color:#f39c12;">Generating topology... this may take a few seconds.</span>';

  try {
    const r = await fetch(NC_BASE + '/api/ai-generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({description: desc}),
    });
    const data = await r.json();

    if (!r.ok || data.error) {
      statusEl.innerHTML = '<span style="color:#e74c3c;">Error: ' + (data.error || 'Unknown error') + '</span>';
      if (data.raw) statusEl.innerHTML += '<br><small style="color:#7a8cb0;">' + data.raw.substring(0, 300) + '</small>';
      btn.disabled = false;
      btn.textContent = 'Generate Topology';
      return;
    }

    // Success — load onto canvas
    if (typeof pushUndo === 'function') pushUndo();
    if (typeof loadGraphJSON === 'function') loadGraphJSON(data.graph_json);
    if (typeof updateStatusBar === 'function') updateStatusBar();
    if (typeof markDirty === 'function') markDirty();

    const prov = data.provider ? ' via ' + data.provider : '';
    statusEl.innerHTML = '<span style="color:#27ae60;">Generated ' + data.node_count + ' nodes, ' + data.edge_count + ' edges' + prov + '.</span>';
    setStatus('AI generated: ' + data.node_count + ' nodes, ' + data.edge_count + ' edges' + prov);

    // Auto-close after a short delay
    setTimeout(() => closeAiGenerateDialog(), 1500);

  } catch (err) {
    statusEl.innerHTML = '<span style="color:#e74c3c;">Request failed: ' + err.message + '</span>';
  }

  btn.disabled = false;
  btn.textContent = 'Generate Topology';
}

/* ══════════════════════════════════════════════════════════════════════════════
 * Embedded Chat Panel — multi-stream chat for topology generation
 * Uses /api/chat endpoints (same as main dashboard chat)
 * ══════════════════════════════════════════════════════════════════════════════ */

let _ncChatContextId = null;
let _ncChatPollTimer = null;
let _ncChatLastTurn = 0;

function ncChatToggle() {
  const panel = document.getElementById('nc-chat-panel');
  if (!panel) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    document.getElementById('nc-chat-input').focus();
  }
}

function ncChatClose() {
  const panel = document.getElementById('nc-chat-panel');
  if (panel) panel.classList.add('hidden');
  if (_ncChatPollTimer) { clearInterval(_ncChatPollTimer); _ncChatPollTimer = null; }
}

function ncChatClear() {
  _ncChatContextId = null;
  _ncChatLastTurn = 0;
  if (_ncChatPollTimer) { clearInterval(_ncChatPollTimer); _ncChatPollTimer = null; }
  const msgs = document.getElementById('nc-chat-messages');
  if (msgs) msgs.innerHTML = `<div class="nc-chat-welcome">
    <div style="font-size:20px;margin-bottom:6px;">✦</div>
    <p>Describe a network topology and I'll generate it on the canvas.</p>
    <p style="color:var(--text-dim);font-size:11px;">Try: "3-tier campus with DMZ" or "MPLS L3VPN with dual PE routers"</p>
  </div>`;
}

function ncChatKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ncChatSend(); }
}

function _ncChatAppendMsg(role, html) {
  const msgs = document.getElementById('nc-chat-messages');
  if (!msgs) return;
  // Remove welcome on first message
  const welcome = msgs.querySelector('.nc-chat-welcome');
  if (welcome) welcome.remove();
  const div = document.createElement('div');
  div.className = 'nc-chat-msg nc-chat-msg-' + role;
  div.innerHTML = html;
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

async function _ncChatEnsureContext() {
  if (_ncChatContextId) return _ncChatContextId;
  try {
    const r = await fetch('/api/chat/contexts', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        user_id: 'nc-designer',
        title: 'Network Canvas Assistant',
        system_prompt: `You are a network topology design assistant integrated into a canvas editor.
When the user describes a network, generate a topology as a JSON object with {"nodes":[...],"edges":[...]}.
Use this EXACT format — each node: {"id":"unique","label":"Name","type":"device-type","x":N,"y":N,"config":{}}
Each edge: {"id":"unique","source":"node-id","target":"node-id","label":"","protocol":""}
Valid types: router, switch-l2, switch-l3, firewall, load-balancer, server, cloud, wap, endpoint-pc, endpoint-iot, siem, sdwan-edge, mpls-pe, pop, draw-rect, text-heading, text-badge.
For draw-rect zones use config: {"_fill":"#0a1628","_stroke":"#74b9ff","_width":N,"_height":N}
For text-heading use config: {"_textColor":"#74b9ff"}
Wrap JSON in a code fence tagged \`\`\`topology so the UI can extract it.
For non-topology questions, answer normally without JSON.`
      }),
    });
    const data = await r.json();
    if (data.context_id) { _ncChatContextId = data.context_id; return _ncChatContextId; }
    if (data.error) throw new Error(data.error);
  } catch (err) {
    // Chat system unavailable — fall back to direct AI generate
    return null;
  }
  return null;
}

async function ncChatSend() {
  const input = document.getElementById('nc-chat-input');
  const sendBtn = document.getElementById('nc-chat-send-btn');
  const text = input.value.trim();
  if (!text) return;

  // Show user message
  _ncChatAppendMsg('user', _escHtml(text));
  input.value = '';
  sendBtn.disabled = true;
  sendBtn.textContent = '...';

  // Use direct AI generate (Claude API — fast and reliable)
  _ncChatAppendMsg('assistant', '<span class="nc-chat-thinking">Generating topology...</span>');
  await _ncChatDirectGenerate(text);

  sendBtn.disabled = false;
  sendBtn.textContent = 'Send';
}

async function _ncChatDirectGenerate(description) {
  try {
    const r = await fetch(NC_BASE + '/api/ai-generate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({description}),
    });
    const data = await r.json();

    // Remove thinking indicator
    const msgs = document.getElementById('nc-chat-messages');
    const thinking = msgs.querySelector('.nc-chat-thinking');
    if (thinking) thinking.closest('.nc-chat-msg').remove();

    if (!r.ok || data.error) {
      _ncChatAppendMsg('system', 'Error: ' + (data.error || 'Unknown'));
      return;
    }

    // Load topology onto canvas
    if (typeof pushUndo === 'function') pushUndo();
    if (typeof loadGraphJSON === 'function') loadGraphJSON(data.graph_json);
    if (typeof updateStatusBar === 'function') updateStatusBar();
    if (typeof markDirty === 'function') markDirty();

    const prov = data.provider ? ' via ' + data.provider : '';
    _ncChatAppendMsg('assistant',
      `Generated <strong>${data.node_count} nodes</strong> and <strong>${data.edge_count} edges</strong>${prov}. Loaded onto canvas.` +
      `<button class="nc-chat-action-btn" onclick="if(typeof undoAction==='function')undoAction()">Undo</button>`
    );
    setStatus('AI generated: ' + data.node_count + ' nodes, ' + data.edge_count + ' edges');
  } catch (err) {
    _ncChatAppendMsg('system', 'Request failed: ' + _escHtml(err.message));
  }
}

function _ncChatPollForResponse() {
  if (_ncChatPollTimer) clearInterval(_ncChatPollTimer);
  let attempts = 0;
  _ncChatPollTimer = setInterval(async () => {
    attempts++;
    if (attempts > 60) { // 30s max
      clearInterval(_ncChatPollTimer); _ncChatPollTimer = null;
      return;
    }
    try {
      const r = await fetch(`/api/chat/${_ncChatContextId}/messages?since=${_ncChatLastTurn}&limit=10`);
      const data = await r.json();
      if (data.messages && data.messages.length > 0) {
        // Remove thinking indicator
        const msgs = document.getElementById('nc-chat-messages');
        const thinking = msgs.querySelector('.nc-chat-thinking');
        if (thinking) thinking.closest('.nc-chat-msg').remove();

        for (const msg of data.messages) {
          if (msg.turn > _ncChatLastTurn) {
            _ncChatLastTurn = msg.turn;
            if (msg.role === 'assistant' || msg.role === 'agent') {
              let content = msg.content || '';
              // Check for topology JSON in ```topology fences
              const topoMatch = content.match(/```topology\s*\n?([\s\S]*?)```/);
              if (topoMatch) {
                try {
                  const gj = JSON.parse(topoMatch[1].trim());
                  if (gj.nodes && gj.edges) {
                    if (typeof pushUndo === 'function') pushUndo();
                    if (typeof loadGraphJSON === 'function') loadGraphJSON(gj);
                    if (typeof updateStatusBar === 'function') updateStatusBar();
                    if (typeof markDirty === 'function') markDirty();
                    content = content.replace(/```topology[\s\S]*?```/, '');
                    _ncChatAppendMsg('assistant',
                      (content.trim() ? _escHtml(content.trim()) + '<br>' : '') +
                      `Loaded <strong>${gj.nodes.length} nodes</strong>, <strong>${gj.edges.length} edges</strong> onto canvas.` +
                      `<button class="nc-chat-action-btn" onclick="if(typeof undoAction==='function')undoAction()">Undo</button>`
                    );
                    setStatus('Chat generated: ' + gj.nodes.length + ' nodes');
                    continue;
                  }
                } catch (_) { /* not valid JSON, show as text */ }
              }
              _ncChatAppendMsg('assistant', _escHtml(content));
            }
          }
        }
        // Check if still processing
        const stateR = await fetch(`/api/chat/${_ncChatContextId}/state`);
        const state = await stateR.json();
        if (state.status !== 'processing') {
          clearInterval(_ncChatPollTimer); _ncChatPollTimer = null;
        }
      }
    } catch (_) { /* ignore poll errors */ }
  }, 500);
}

function _escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

/* ══════════════════════════════════════════════════════════════════════════════
 * Heatmap Overlay — color nodes/links by selectable metric
 * Metrics: bandwidth, vuln, stig, age
 * ══════════════════════════════════════════════════════════════════════════════ */

let _heatmapActive = false;
let _heatmapMetric = 'bandwidth';
let _heatmapOrigNodeStyles = new Map(); // nodeId -> {fill, stroke}
let _heatmapOrigLinkStyles = new Map(); // linkId -> {stroke, strokeWidth, strokeDasharray}

const HEATMAP_GRADIENT = [
  { stop: 0.0, r: 39,  g: 174, b: 96  }, // green
  { stop: 0.25, r: 241, g: 196, b: 15  }, // yellow
  { stop: 0.5,  r: 243, g: 156, b: 18  }, // orange
  { stop: 0.75, r: 231, g: 76,  b: 60  }, // red
  { stop: 1.0,  r: 192, g: 57,  b: 43  }, // dark red
];

function _heatmapColor(value) {
  const v = Math.max(0, Math.min(1, value));
  let lower = HEATMAP_GRADIENT[0], upper = HEATMAP_GRADIENT[HEATMAP_GRADIENT.length - 1];
  for (let i = 0; i < HEATMAP_GRADIENT.length - 1; i++) {
    if (v >= HEATMAP_GRADIENT[i].stop && v <= HEATMAP_GRADIENT[i + 1].stop) {
      lower = HEATMAP_GRADIENT[i];
      upper = HEATMAP_GRADIENT[i + 1];
      break;
    }
  }
  const range = upper.stop - lower.stop || 1;
  const t = (v - lower.stop) / range;
  const r = Math.round(lower.r + t * (upper.r - lower.r));
  const g = Math.round(lower.g + t * (upper.g - lower.g));
  const b = Math.round(lower.b + t * (upper.b - lower.b));
  return `rgb(${r},${g},${b})`;
}

function toggleHeatmap(metric) {
  if (_heatmapActive && _heatmapMetric === metric) {
    clearHeatmap();
    return;
  }
  if (_heatmapActive) clearHeatmap();
  _heatmapMetric = metric;
  showHeatmap(metric);
}

async function showHeatmap(metric) {
  if (!graph || !currentTopoId || currentTopoId === 'new') {
    setStatus('Save topology before using heatmap'); return;
  }
  setStatus('Loading heatmap data...');
  try {
    const resp = await fetch(NC_BASE + '/api/heatmap/' + currentTopoId + '?metric=' + encodeURIComponent(metric));
    if (!resp.ok) throw new Error('Failed to load heatmap data');
    const data = await resp.json();
    applyHeatmap(data);
  } catch (err) {
    setStatus('Heatmap error: ' + err.message);
  }
}

function applyHeatmap(data) {
  _heatmapActive = true;
  _heatmapOrigNodeStyles.clear();
  _heatmapOrigLinkStyles.clear();

  const nodeVals = data.node_values || {};
  const linkVals = data.link_values || {};
  const metric = data.metric || _heatmapMetric;

  const elements = graph.getElements();
  const links = graph.getLinks();

  // Apply node colours
  elements.forEach(el => {
    const ntype = el.get('nodeType') || '';
    if (!ntype || ntype.startsWith('draw-') || ntype.startsWith('text-') || ntype === 'group-site' || ntype === 'annotation') return;

    const bodyAttrs = el.attr('body') || {};
    _heatmapOrigNodeStyles.set(el.id, {
      fill: bodyAttrs.fill,
      stroke: bodyAttrs.stroke,
    });

    const val = nodeVals[el.id];
    if (val !== undefined && val !== null) {
      const color = _heatmapColor(val);
      el.attr('body/fill', color);
      el.attr('body/stroke', color);
      el.attr('body/fillOpacity', 0.7);
    } else {
      // Dim nodes with no data
      el.attr('body/fillOpacity', 0.15);
    }
  });

  // Apply link colours
  links.forEach(link => {
    const lineAttrs = link.attr('line') || {};
    _heatmapOrigLinkStyles.set(link.id, {
      stroke: lineAttrs.stroke,
      strokeWidth: lineAttrs.strokeWidth,
      strokeDasharray: lineAttrs.strokeDasharray,
    });

    const val = linkVals[link.id];
    if (val !== undefined && val !== null) {
      const color = _heatmapColor(val);
      link.attr('line/stroke', color);
      link.attr('line/strokeWidth', 3);
      link.removeAttr('line/strokeDasharray');
    } else {
      // Dim links with no data
      link.attr('line/stroke', '#333');
      link.attr('line/strokeWidth', 1);
    }
  });

  // Show legend
  showHeatmapLegend(metric);

  // Update toolbar button
  const btn = document.getElementById('tb-heatmap-btn');
  if (btn) btn.classList.add('active');

  const METRIC_LABELS = {bandwidth: 'Bandwidth Utilization', vuln: 'Vulnerability Severity', stig: 'STIG Compliance', age: 'Equipment Age'};
  setStatus('Heatmap: ' + (METRIC_LABELS[metric] || metric));
}

function clearHeatmap() {
  if (!graph) return;
  _heatmapActive = false;

  graph.getElements().forEach(el => {
    const orig = _heatmapOrigNodeStyles.get(el.id);
    if (orig) {
      el.attr('body/fill', orig.fill || '#1a1a2e');
      el.attr('body/stroke', orig.stroke || '#7a8cb0');
      el.removeAttr('body/fillOpacity');
    }
  });

  graph.getLinks().forEach(link => {
    const orig = _heatmapOrigLinkStyles.get(link.id);
    if (orig) {
      link.attr('line/stroke', orig.stroke || '#e94560');
      link.attr('line/strokeWidth', orig.strokeWidth || 2);
      if (orig.strokeDasharray) {
        link.attr('line/strokeDasharray', orig.strokeDasharray);
      } else {
        link.removeAttr('line/strokeDasharray');
      }
    }
  });

  _heatmapOrigNodeStyles.clear();
  _heatmapOrigLinkStyles.clear();
  hideHeatmapLegend();

  const btn = document.getElementById('tb-heatmap-btn');
  if (btn) btn.classList.remove('active');
  setStatus('Heatmap cleared');
}

function showHeatmapLegend(metric) {
  let legend = document.getElementById('heatmap-legend');
  if (!legend) {
    legend = document.createElement('div');
    legend.id = 'heatmap-legend';
    legend.className = 'heatmap-legend';
    const canvasArea = document.querySelector('.canvas-area') || document.querySelector('.canvas-body');
    if (canvasArea) canvasArea.appendChild(legend);
  }
  const METRIC_INFO = {
    bandwidth: { label: 'Bandwidth Utilization', low: '0%', high: '100%' },
    vuln:      { label: 'Vulnerability Severity', low: 'None', high: 'CAT1' },
    stig:      { label: 'STIG Compliance', low: '100%', high: '0%' },
    age:       { label: 'Equipment Age', low: 'New', high: '10+ yr' },
  };
  const info = METRIC_INFO[metric] || { label: metric, low: 'Low', high: 'High' };
  legend.innerHTML = `
    <div class="heatmap-legend-title">${info.label}</div>
    <div class="heatmap-legend-bar">
      <span class="heatmap-legend-label">${info.low}</span>
      <div class="heatmap-legend-gradient"></div>
      <span class="heatmap-legend-label">${info.high}</span>
    </div>
    <button class="heatmap-legend-close" onclick="clearHeatmap()" title="Clear heatmap">&times;</button>
  `;
  legend.style.display = 'flex';
}

function hideHeatmapLegend() {
  const legend = document.getElementById('heatmap-legend');
  if (legend) legend.style.display = 'none';
}

/* ══════════════════════════════════════════════════════════════════════════════
 * Palette Stencil Icons — replace text icons with mini Cisco SVG stencils
 * ══════════════════════════════════════════════════════════════════════════════ */

function initPaletteStencils() {
  if (typeof CISCO_STENCILS === 'undefined' || typeof NODE_STYLES === 'undefined') return;

  document.querySelectorAll('.palette-item[data-type]').forEach(item => {
    const type = item.dataset.type;
    if (!type) return;
    // Skip drawing shapes and text labels — they keep their CSS icons
    if (type.startsWith('draw-') || type.startsWith('text-')) return;

    const stencil = (typeof getCiscoStencil === 'function') ? getCiscoStencil(type) : null;
    if (!stencil) return;

    const style = (typeof getStyle === 'function') ? getStyle(type) : null;
    const fillColor = style ? style.stroke : '#3498db';

    const piIcon = item.querySelector('.pi-icon');
    if (!piIcon) return;

    // Replace the text content with a mini SVG
    piIcon.innerHTML = `<svg viewBox="0 0 48 48" width="28" height="22" style="display:block;">
      <path d="${stencil.body}" fill="${fillColor}" stroke="none"/>
      ${stencil.detail ? `<path d="${stencil.detail}" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>` : ''}
    </svg>`;
    piIcon.style.background = 'transparent';
    piIcon.style.border = 'none';
    piIcon.style.width = '32px';
    piIcon.style.height = '24px';
  });
}

/* ── Init zoom on DOMContentLoaded ────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  // Wait a tick for canvas.js to init paper
  setTimeout(initZoomWheel, 500);
  setTimeout(initClassificationBanner, 600);
  setTimeout(initPaletteStencils, 300);
});
