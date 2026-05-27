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

    case 'failover': {
      const score = result.resilience_score || 0;
      const scoreBar = score >= 80 ? '🟢' : score >= 50 ? '🟡' : '🔴';
      lines.push(`${scoreBar} Resilience Score: ${score}%`);
      lines.push(`   ${(result.risks||[]).length} risk(s) found`);
      lines.push('');
      lines.push('── NARRATIVE (watch the canvas) ──');
      lines.push('');
      (result.risks || []).forEach((r, i) => {
        lines.push(`${i+1}. SCENARIO: What if "${r.node}" fails?`);
        lines.push(`   Impact: ${r.impact}`);
        lines.push(`   Neighbors with redundancy → GREEN (safe, alternate path exists)`);
        lines.push(`   Neighbors without redundancy → AMBER (at risk, no failover)`);
        lines.push(`   Recommendation: ${r.recommendation}`);
        lines.push('');
      });
      lines.push('── LEGEND ──');
      lines.push('  🔴 Pulsing red = failed device (simulated)');
      lines.push('  --- Red dashed links = broken connections');
      lines.push('  🟢 Green glow = redundant path available (safe)');
      lines.push('  🟠 Amber glow = no redundancy (at risk)');
      break;
    }

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
  // Stop any blast radius / failover animations
  if (typeof _blastAnimTimers !== 'undefined') {
    _blastAnimTimers.forEach(t => clearTimeout(t));
    _blastAnimTimers.length = 0;
  }
  if (typeof _failoverAnimTimers !== 'undefined') {
    _failoverAnimTimers.forEach(t => clearTimeout(t));
    _failoverAnimTimers.length = 0;
  }
  if (typeof _simAnimTimers !== 'undefined') {
    _simAnimTimers.forEach(t => clearTimeout(t));
    _simAnimTimers.length = 0;
  }
  HIGHLIGHTED_CELLS.forEach(id => {
    const cell = graph.getCell(id);
    if (!cell) return;
    if (cell.isElement()) {
      const type = cell.get('nodeType') || 'unknown';
      const style = (typeof getStyle !== 'undefined') ? getStyle(type) : { fill: '#1a1a2e', stroke: '#7a8cb0' };
      cell.attr('body/fill', style.fill);
      cell.attr('body/stroke', style.stroke);
      cell.attr('body/strokeWidth', 1);
      // Remove blast animation CSS classes
      const view = paper.findViewByModel(cell);
      if (view && view.el) {
        view.el.classList.remove('blast-source', 'blast-compromised', 'blast-blocked', 'blast-zone');
        view.el.style.filter = '';
      }
    } else {
      cell.attr('line/stroke', '#e94560');
      cell.attr('line/strokeWidth', 2);
      cell.removeAttr('line/strokeDasharray');
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
      el.attr('body/strokeWidth', 3);
      HIGHLIGHTED_CELLS.push(el.id);
      const view = paper.findViewByModel(el);
      if (view && view.el) view.el.classList.add('blast-source');
    }
  });
}

/* ── Animated Failover / Resilience Visualization ─────────────────────────── */
let _failoverAnimTimers = [];

function highlightFailover(result) {
  _failoverAnimTimers.forEach(t => clearTimeout(t));
  _failoverAnimTimers = [];

  const RISK_COLORS = [
    { fill: '#4a0000', stroke: '#ff1744', label: 'CRITICAL' },  // critical risk
    { fill: '#3a0a00', stroke: '#ff6d00', label: 'HIGH' },      // high risk
    { fill: '#3a2a00', stroke: '#ffc107', label: 'MEDIUM' },    // medium
    { fill: '#1a2a00', stroke: '#cddc39', label: 'LOW' },       // low
  ];
  const REDUNDANT_STYLE = { fill: '#002a1a', stroke: '#00e676' };  // green = redundant/safe
  const FAILED_STYLE = { fill: '#4a0000', stroke: '#ff1744' };     // red = simulated failure

  const labelToId = {};
  const idToEl = {};
  graph.getElements().forEach(el => {
    const lbl = el.attr('label/text') || '';
    labelToId[lbl] = el.id;
    idToEl[el.id] = el;
  });

  const risks = result.risks || [];
  const score = result.resilience_score || 0;
  const simOutput = document.getElementById('sim-output');

  function _narrate(msg) {
    setStatus(msg);
    if (simOutput) {
      simOutput.textContent += '\n▶ ' + msg;
      simOutput.scrollTop = simOutput.scrollHeight;
    }
  }

  _narrate(`Failover analysis: resilience score ${score}% — simulating ${risks.length} failure scenarios...`);

  // Phase 1: Show overall resilience score color
  const scoreColor = score >= 80 ? '#00e676' : score >= 50 ? '#ffc107' : '#ff1744';

  // Phase 2: Animate each risk scenario
  risks.forEach((risk, idx) => {
    const delay = (idx + 1) * 1200;
    const riskLevel = Math.min(idx, RISK_COLORS.length - 1);
    const riskStyle = RISK_COLORS[riskLevel];

    const timer = setTimeout(() => {
      const nodeLabel = risk.node;
      const elId = labelToId[nodeLabel];
      if (!elId) return;
      const el = idToEl[elId];
      if (!el) return;

      // Flash the failed node
      el.attr('body/fill', FAILED_STYLE.fill);
      el.attr('body/stroke', FAILED_STYLE.stroke);
      el.attr('body/strokeWidth', 4);
      HIGHLIGHTED_CELLS.push(elId);

      const view = paper.findViewByModel(el);
      if (view && view.el) view.el.classList.add('blast-source');

      // Highlight affected links (links connected to this node go red/dashed)
      graph.getLinks().forEach(lk => {
        const src = lk.get('source')?.id;
        const tgt = lk.get('target')?.id;
        if (src === elId || tgt === elId) {
          lk.attr('line/stroke', '#ff1744');
          lk.attr('line/strokeWidth', 3);
          lk.attr('line/strokeDasharray', '8,4');
          HIGHLIGHTED_CELLS.push(lk.id);
        }
      });

      // Show redundant paths (neighbors that have alternate routes)
      const neighborIds = new Set();
      graph.getLinks().forEach(lk => {
        const src = lk.get('source')?.id;
        const tgt = lk.get('target')?.id;
        if (src === elId && tgt) neighborIds.add(tgt);
        if (tgt === elId && src) neighborIds.add(src);
      });

      // Check if neighbors have other connections (redundancy)
      const redundantTimer = setTimeout(() => {
        neighborIds.forEach(nid => {
          const nEl = idToEl[nid];
          if (!nEl) return;
          const otherLinks = graph.getLinks().filter(lk => {
            const s = lk.get('source')?.id;
            const t = lk.get('target')?.id;
            return (s === nid || t === nid) && s !== elId && t !== elId;
          });
          if (otherLinks.length > 0) {
            // Has redundant path — mark green
            nEl.attr('body/fill', REDUNDANT_STYLE.fill);
            nEl.attr('body/stroke', REDUNDANT_STYLE.stroke);
            nEl.attr('body/strokeWidth', 2);
            HIGHLIGHTED_CELLS.push(nid);
            const nView = paper.findViewByModel(nEl);
            if (nView && nView.el) nView.el.classList.add('blast-blocked');
            // Highlight redundant links green
            otherLinks.forEach(lk => {
              lk.attr('line/stroke', '#00e676');
              lk.attr('line/strokeWidth', 3);
              lk.removeAttr('line/strokeDasharray');
              HIGHLIGHTED_CELLS.push(lk.id);
            });
          } else {
            // No redundancy — mark amber (at risk)
            nEl.attr('body/fill', riskStyle.fill);
            nEl.attr('body/stroke', riskStyle.stroke);
            nEl.attr('body/strokeWidth', 2);
            HIGHLIGHTED_CELLS.push(nid);
            const nView = paper.findViewByModel(nEl);
            if (nView && nView.el) nView.el.classList.add('blast-compromised');
          }
        });
      }, 400);
      _failoverAnimTimers.push(redundantTimer);

      // Narrate this scenario
      _narrate(`Scenario ${idx+1}: if "${nodeLabel}" fails — impact: ${risk.impact || 'unknown'}`);

      // Count redundant vs at-risk after animation
      const countTimer = setTimeout(() => {
        let safeCount = 0, riskCount = 0;
        neighborIds.forEach(nid => {
          const otherLinks = graph.getLinks().filter(lk => {
            const s = lk.get('source')?.id;
            const t = lk.get('target')?.id;
            return (s === nid || t === nid) && s !== elId && t !== elId;
          });
          if (otherLinks.length > 0) safeCount++; else riskCount++;
        });
        _narrate(`  → ${safeCount} neighbors have redundant paths (GREEN), ${riskCount} at risk (AMBER)`);
        if (risk.recommendation) _narrate(`  → Fix: ${risk.recommendation}`);
      }, 600);
      _failoverAnimTimers.push(countTimer);
    }, delay);
    _failoverAnimTimers.push(timer);
  });

  // Final summary
  const finalTimer = setTimeout(() => {
    _narrate(`═══ COMPLETE ═══`);
    _narrate(`Resilience: ${score}% — ${risks.length} failure scenarios analyzed`);
    _narrate(`GREEN nodes = safe (redundant path) | RED nodes = single point of failure`);
  }, (risks.length + 1) * 1200 + 800);
  _failoverAnimTimers.push(finalTimer);
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
  const simOutput = document.getElementById('sim-output');
  function _simNarrate(msg) {
    setStatus(msg);
    if (simOutput) { simOutput.textContent += '\n\u25B6 ' + msg; simOutput.scrollTop = simOutput.scrollHeight; }
  }

  switch (simType) {
    case 'ping':
      _animatePing(result, _simNarrate);
      break;
    case 'traceroute':
      _animateTraceroute(result, _simNarrate);
      break;
    case 'spof':
      _animateSpof(result, _simNarrate);
      break;
    case 'failover':
      highlightFailover(result);
      break;
    case 'load':
      _animateLoadHeatmap(result, _simNarrate);
      break;
    case 'blast_radius':
      highlightBlastRadius(result);
      break;
    case 'bgp_bestpath':
      _animateBgpBestpath(result, _simNarrate);
      break;
    case 'bgp_propagation':
      _animateBgpPropagation(result, _simNarrate);
      break;
    case 'ospf_spf':
      _animateOspfSpf(result, _simNarrate);
      break;
    case 'ospf_cost':
      _animateOspfCost(result, _simNarrate);
      break;
    case 'jumbo_mtu':
      _animateJumboMtu(result, _simNarrate);
      break;
  }
}

/* ── Animated Simulation Helpers ──────────────────────────────────────────── */
let _simAnimTimers = [];
function _clearSimTimers() { _simAnimTimers.forEach(t => clearTimeout(t)); _simAnimTimers = []; }

function _labelToIdMap() {
  const m = {}; const e = {};
  graph.getElements().forEach(el => { const l = el.attr('label/text') || ''; m[l] = el.id; e[el.id] = el; });
  return { labelToId: m, idToEl: e };
}

/* ── Ping: animated packet walk ──────────────────────────────────────────── */
function _animatePing(result, narrate) {
  _clearSimTimers();
  const { labelToId } = _labelToIdMap();
  const hops = result.hops || [];
  if (!hops.length) return;
  narrate('Ping: ' + result.source + ' \u2192 ' + result.destination + ' (' + (result.reachable ? 'REACHABLE' : 'UNREACHABLE') + ')');

  hops.forEach((hop, i) => {
    _simAnimTimers.push(setTimeout(() => {
      const elId = labelToId[hop];
      if (!elId) return;
      const el = graph.getCell(elId);
      if (el) {
        el.attr('body/fill', i === 0 ? '#0f2b0f' : i === hops.length - 1 ? '#0f2b3a' : '#0f2b0f');
        el.attr('body/stroke', i === hops.length - 1 ? '#3498db' : '#27ae60');
        el.attr('body/strokeWidth', 3);
        HIGHLIGHTED_CELLS.push(elId);
        const v = paper.findViewByModel(el); if (v?.el) v.el.classList.add('blast-blocked');
      }
      if (i < hops.length - 1) {
        const nid = labelToId[hops[i + 1]];
        if (elId && nid) graph.getLinks().forEach(lk => {
          const s = lk.get('source')?.id, t = lk.get('target')?.id;
          if ((s === elId && t === nid) || (s === nid && t === elId)) { lk.attr('line/stroke', '#27ae60'); lk.attr('line/strokeWidth', 3); HIGHLIGHTED_CELLS.push(lk.id); }
        });
      }
      narrate('Hop ' + (i + 1) + ': ' + hop + (i === 0 ? ' (source)' : i === hops.length - 1 ? ' (destination)' : ''));
    }, i * 400));
  });
  _simAnimTimers.push(setTimeout(() => narrate('Ping complete: ' + hops.length + ' hops, ' + (result.latency_ms || '?') + ' ms'), hops.length * 400 + 200));
}

/* ── Traceroute: animated hop-by-hop with latency ────────────────────────── */
function _animateTraceroute(result, narrate) {
  _clearSimTimers();
  const { labelToId } = _labelToIdMap();
  const trace = result.trace || [];
  if (!trace.length) return;
  narrate('Traceroute: ' + result.source + ' \u2192 ' + result.destination);

  trace.forEach((hop, i) => {
    _simAnimTimers.push(setTimeout(() => {
      const elId = labelToId[hop.node];
      if (elId) {
        const el = graph.getCell(elId);
        if (el) { el.attr('body/fill', '#0f2b0f'); el.attr('body/stroke', '#27ae60'); el.attr('body/strokeWidth', 3); HIGHLIGHTED_CELLS.push(elId); const v = paper.findViewByModel(el); if (v?.el) v.el.classList.add('blast-blocked'); }
      }
      if (i > 0) {
        const prevId = labelToId[trace[i - 1].node];
        if (prevId && elId) graph.getLinks().forEach(lk => {
          const s = lk.get('source')?.id, t = lk.get('target')?.id;
          if ((s === prevId && t === elId) || (s === elId && t === prevId)) { lk.attr('line/stroke', '#27ae60'); lk.attr('line/strokeWidth', 3); HIGHLIGHTED_CELLS.push(lk.id); }
        });
      }
      narrate('Hop ' + hop.hop + ': ' + hop.node + ' \u2014 ' + hop.latency_ms + ' ms');
    }, i * 500));
  });
  _simAnimTimers.push(setTimeout(() => narrate('Traceroute complete: ' + trace.length + ' hops'), trace.length * 500 + 200));
}

/* ── SPOF: pulsing red reveal ────────────────────────────────────────────── */
function _animateSpof(result, narrate) {
  _clearSimTimers();
  const spofNodes = result.spof_nodes || [];
  narrate('SPOF Analysis: ' + spofNodes.length + ' single points of failure found');

  spofNodes.forEach((nodeLabel, i) => {
    _simAnimTimers.push(setTimeout(() => {
      graph.getElements().forEach(el => {
        if (el.attr('label/text') === nodeLabel) {
          el.attr('body/fill', '#4a0000'); el.attr('body/stroke', '#ff1744'); el.attr('body/strokeWidth', 4);
          HIGHLIGHTED_CELLS.push(el.id);
          const v = paper.findViewByModel(el); if (v?.el) v.el.classList.add('blast-source');
        }
      });
      narrate('SPOF ' + (i + 1) + ': "' + nodeLabel + '" \u2014 single point of failure');
    }, i * 800));
  });
  _simAnimTimers.push(setTimeout(() => narrate('\u2550\u2550\u2550 SPOF complete: ' + spofNodes.length + ' critical vulnerabilities \u2550\u2550\u2550'), spofNodes.length * 800 + 400));
}

/* ── Load Heatmap: with narrative ────────────────────────────────────────── */
function _animateLoadHeatmap(result, narrate) {
  applyLoadHeatmap(result.utilization);
  const crit = (result.utilization || []).filter(u => u.status === 'critical');
  const warn = (result.utilization || []).filter(u => u.status === 'warning');
  narrate('Load Heatmap: avg ' + result.avg_utilization_pct + '%');
  narrate(crit.length + ' critical (>75%), ' + warn.length + ' warning (>50%)');
  if (crit.length) narrate('Critical: ' + crit.map(u => u.label || u.edge).join(', '));
}

/* ── BGP Best Path: highlight winning path green, alternatives gray ──────── */
function _animateBgpBestpath(result, narrate) {
  _clearSimTimers();
  const { labelToId } = _labelToIdMap();
  const paths = result.paths || [];
  narrate('BGP Best Path: ' + result.source + ' \u2192 ' + result.destination);
  narrate('Decision: ' + (result.decision_reason || 'standard BGP'));

  paths.forEach((p, pi) => {
    const best = pi === 0;
    _simAnimTimers.push(setTimeout(() => {
      const color = best ? '#27ae60' : '#636e72';
      narrate((best ? '\u2605 BEST' : '  #' + (pi + 1)) + ': ' + p.path.join(' \u2192 ') + ' (LP=' + p.local_pref + ' MED=' + p.med + ')');
      p.path.forEach((hop, hi) => {
        const eid = labelToId[hop];
        if (eid) { const el = graph.getCell(eid); if (el) { el.attr('body/stroke', color); el.attr('body/strokeWidth', best ? 3 : 1); if (best) el.attr('body/fill', '#0a180a'); HIGHLIGHTED_CELLS.push(eid); if (best) { const v = paper.findViewByModel(el); if (v?.el) v.el.classList.add('blast-blocked'); } } }
        if (hi < p.path.length - 1) { const nid = labelToId[p.path[hi + 1]]; if (eid && nid) graph.getLinks().forEach(lk => { const s = lk.get('source')?.id, t = lk.get('target')?.id; if ((s === eid && t === nid) || (s === nid && t === eid)) { lk.attr('line/stroke', color); lk.attr('line/strokeWidth', best ? 3 : 1); HIGHLIGHTED_CELLS.push(lk.id); } }); }
      });
    }, pi * 1000));
  });
}

/* ── BGP Propagation: wave-by-wave ───────────────────────────────────────── */
function _animateBgpPropagation(result, narrate) {
  _clearSimTimers();
  const { labelToId } = _labelToIdMap();
  narrate('BGP Propagation from ' + result.originator + ': ' + result.nodes_reached + '/' + result.total_bgp_nodes + ' nodes');

  (result.waves || []).forEach((wave, wi) => {
    _simAnimTimers.push(setTimeout(() => {
      narrate('Wave ' + wave.wave + ': ' + wave.action);
      (wave.nodes || []).forEach(n => {
        const eid = labelToId[n];
        if (eid) { const el = graph.getCell(eid); if (el) { el.attr('body/stroke', '#5dade2'); el.attr('body/strokeWidth', 3); el.attr('body/fill', '#0a1628'); HIGHLIGHTED_CELLS.push(eid); const v = paper.findViewByModel(el); if (v?.el) v.el.classList.add('blast-blocked'); } }
      });
    }, wi * 800));
  });
}

/* ── OSPF SPF: animate shortest path tree ────────────────────────────────── */
function _animateOspfSpf(result, narrate) {
  _clearSimTimers();
  const { labelToId } = _labelToIdMap();
  narrate('OSPF SPF Tree from root: ' + result.root);

  (result.spf_tree || []).forEach((node, i) => {
    _simAnimTimers.push(setTimeout(() => {
      const eid = labelToId[node.node];
      if (eid) { const el = graph.getCell(eid); if (el) { el.attr('body/fill', '#0a180a'); el.attr('body/stroke', '#27ae60'); el.attr('body/strokeWidth', 3); HIGHLIGHTED_CELLS.push(eid); const v = paper.findViewByModel(el); if (v?.el) v.el.classList.add('blast-blocked'); } }
      narrate('Cost ' + node.cost + ': ' + node.node + ' via ' + node.path.join(' \u2192 '));
    }, i * 400));
  });
  if (result.ecmp_nodes?.length) _simAnimTimers.push(setTimeout(() => narrate('ECMP: ' + result.ecmp_nodes.join(', ')), (result.spf_tree || []).length * 400 + 200));
}

/* ── OSPF Cost: color links by cost ──────────────────────────────────────── */
function _animateOspfCost(result, narrate) {
  narrate('OSPF Cost Analysis: ' + (result.link_costs || []).length + ' links');
  (result.link_costs || []).forEach(lc => {
    graph.getLinks().forEach(lk => {
      const lkLabel = lk.labels()?.[0]?.attrs?.text?.text || '';
      if (lk.id === lc.link || lkLabel === lc.label) {
        const c = lc.cost <= 10 ? '#27ae60' : lc.cost <= 50 ? '#f39c12' : '#e74c3c';
        lk.attr('line/stroke', c); lk.attr('line/strokeWidth', Math.max(2, 5 - Math.floor(lc.cost / 20))); HIGHLIGHTED_CELLS.push(lk.id);
      }
    });
  });
  if (result.abr_boundaries?.length) narrate('ABR boundaries: ' + result.abr_boundaries.join(', '));
}

/* ── Jumbo MTU: green=ok, red=bottleneck ─────────────────────────────────── */
function _animateJumboMtu(result, narrate) {
  _clearSimTimers();
  const { labelToId } = _labelToIdMap();
  narrate('MTU Check: desired ' + result.desired_mtu + ' bytes \u2014 ' + (result.jumbo_ready ? 'READY' : 'NOT READY'));
  if (!result.jumbo_ready) narrate('Bottleneck: ' + (result.bottleneck || '?'));

  (result.path_detail || []).forEach((p, i) => {
    _simAnimTimers.push(setTimeout(() => {
      const eid = labelToId[p.node];
      if (eid) { const el = graph.getCell(eid); if (el) { const ok = p.status === 'ok'; el.attr('body/fill', ok ? '#0a180a' : '#4a0000'); el.attr('body/stroke', ok ? '#27ae60' : '#ff1744'); el.attr('body/strokeWidth', 3); HIGHLIGHTED_CELLS.push(eid); const v = paper.findViewByModel(el); if (v?.el) v.el.classList.add(ok ? 'blast-blocked' : 'blast-source'); } }
      narrate((p.status === 'ok' ? '\u2713' : '\u2717') + ' ' + p.node + ': MTU=' + p.mtu);
    }, i * 400));
  });
}

/* ── Blast Radius Visualization (animated) ────────────────────────────────── */
let _blastAnimTimers = [];

function highlightBlastRadius(result) {
  // Clear any previous blast animation
  _blastAnimTimers.forEach(t => clearTimeout(t));
  _blastAnimTimers = [];

  const HOP_COLORS = [
    { fill: '#4a0000', stroke: '#ff1744', glow: 'rgba(255,23,68,0.6)' },   // hop 1 — bright red
    { fill: '#3a0a00', stroke: '#ff6d00', glow: 'rgba(255,109,0,0.5)' },   // hop 2 — orange
    { fill: '#3a2a00', stroke: '#ffc107', glow: 'rgba(255,193,7,0.4)' },   // hop 3 — amber
    { fill: '#2a2a00', stroke: '#ffeb3b', glow: 'rgba(255,235,59,0.3)' },  // hop 4 — yellow
    { fill: '#1a2a00', stroke: '#cddc39', glow: 'rgba(205,220,57,0.3)' },  // hop 5+
  ];
  const BLOCKED_STYLE = { fill: '#002a1a', stroke: '#00e676', glow: 'rgba(0,230,118,0.4)' };
  const SOURCE_STYLE  = { fill: '#4a0022', stroke: '#ff1744', glow: 'rgba(255,23,68,0.8)' };
  const ZONE_STYLE    = { fill: '#1a1a00', stroke: '#ffc107', glow: 'rgba(255,193,7,0.3)' };

  // Build label-to-id map
  const labelToId = {};
  const idToEl = {};
  graph.getElements().forEach(el => {
    const lbl = el.attr('label/text') || '';
    labelToId[lbl] = el.id;
    idToEl[el.id] = el;
  });

  // Collect all compromised/blocked IDs for edge highlighting
  const compromisedIds = new Set();
  const blockedIds = new Set();

  // Helper: apply style to a node with CSS animation class
  function _styleNode(elId, style, borderWidth, animClass) {
    const el = idToEl[elId];
    if (!el) return;
    el.attr('body/fill', style.fill);
    el.attr('body/stroke', style.stroke);
    el.attr('body/strokeWidth', borderWidth);
    HIGHLIGHTED_CELLS.push(elId);
    // Apply CSS animation via the JointJS view
    const view = paper.findViewByModel(el);
    if (view && view.el) {
      view.el.classList.add(animClass);
    }
  }

  // ── Phase 0: Source node (immediate — pulsing red) ──
  const srcLabel = result.source;
  const srcElId = labelToId[srcLabel];
  if (srcElId) {
    _styleNode(srcElId, SOURCE_STYLE, 4, 'blast-source');
    compromisedIds.add(srcElId);
  }

  const simOutput = document.getElementById('sim-output');
  function _blastNarrate(msg) {
    setStatus(msg);
    if (simOutput) {
      simOutput.textContent += '\n▶ ' + msg;
      simOutput.scrollTop = simOutput.scrollHeight;
    }
  }

  _blastNarrate(`Blast radius: "${srcLabel}" compromised — propagating...`);

  // ── Phase 1+: Each hop layer reveals with delay ──
  const HOP_DELAY = 800; // ms between each hop wave

  (result.hop_layers || []).forEach(layer => {
    const hopIdx = Math.min(layer.hop - 1, HOP_COLORS.length - 1);
    const hopStyle = HOP_COLORS[hopIdx];
    const delay = layer.hop * HOP_DELAY;

    const timer = setTimeout(() => {
      // Narrate this hop
      const devNames = (layer.devices||[]).map(d => d.label).join(', ');
      const blocked = (layer.devices||[]).filter(d => d.status === 'blocked').length;
      _blastNarrate(`Hop ${layer.hop}: ${(layer.devices||[]).length} devices reached — ${devNames}`);
      if (blocked) _blastNarrate(`  → ${blocked} blocked by firewall/segmentation (GREEN)`);

      (layer.devices || []).forEach((d, di) => {
        const elId = labelToId[d.label];
        if (!elId) return;

        // Stagger individual devices within a hop (50ms apart)
        const deviceTimer = setTimeout(() => {
          let style, bw, animClass;
          if (d.status === 'blocked') {
            style = BLOCKED_STYLE;
            bw = 3;
            animClass = 'blast-blocked';
            blockedIds.add(elId);
          } else if (d.status === 'zone_boundary') {
            style = ZONE_STYLE;
            bw = 3;
            animClass = 'blast-zone';
            compromisedIds.add(elId);
          } else {
            style = hopStyle;
            bw = 2;
            animClass = 'blast-compromised';
            compromisedIds.add(elId);
          }
          _styleNode(elId, style, bw, animClass);

          // Highlight edges to this device
          graph.getLinks().forEach(lk => {
            const lkSrc = lk.get('source')?.id;
            const lkTgt = lk.get('target')?.id;
            if ((compromisedIds.has(lkSrc) && elId === lkTgt) ||
                (compromisedIds.has(lkTgt) && elId === lkSrc)) {
              if (d.status === 'blocked') {
                lk.attr('line/stroke', '#00e676');
                lk.attr('line/strokeWidth', 3);
                lk.attr('line/strokeDasharray', '6,3');
              } else {
                lk.attr('line/stroke', hopStyle.stroke);
                lk.attr('line/strokeWidth', 3);
                lk.removeAttr('line/strokeDasharray');
              }
              HIGHLIGHTED_CELLS.push(lk.id);
            }
          });
        }, di * 50);
        _blastAnimTimers.push(deviceTimer);
      });
    }, delay);
    _blastAnimTimers.push(timer);
  });

  // Final narrative after all hops
  const totalHops = (result.hop_layers || []).length;
  const finalTimer = setTimeout(() => {
    const total = result.total_compromised || 0;
    const blocked = result.total_blocked || 0;
    _blastNarrate(`═══ BLAST RADIUS COMPLETE ═══`);
    _blastNarrate(`${total} devices compromised, ${blocked} blocked by firewalls/segmentation`);
    _blastNarrate(`RED = compromised | ORANGE/AMBER = reachable | GREEN = firewall held`);
  }, (totalHops + 1) * HOP_DELAY);
  _blastAnimTimers.push(finalTimer);
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
  const area = document.querySelector('.canvas-area') || document.querySelector('.canvas-body');
  const w = area ? area.clientWidth : 1200;
  const h = area ? area.clientHeight : 800;
  paper.scaleContentToFit({ fittingBBox: { x: 0, y: 0, width: w, height: h }, padding: 30, maxScale: 1 });
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
      highlightFailover(result);
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

/* ── Blast Radius — Direct Device Trigger ─────────────────────────────────────
 * Called from the right-click context menu on any canvas device.
 * Opens the sim panel pre-configured for blast_radius and runs immediately.
 * ─────────────────────────────────────────────────────────────────────────── */

/**
 * Entry point from the right-click context menu.
 * @param {number} [maxHops=3] - How many hops to expand the blast radius.
 */
async function triggerBlastRadiusFromContext(maxHops) {
  if (typeof _blastCtxNodeId === 'undefined' || !_blastCtxNodeId) return;
  hideBlastContextMenu();
  await triggerBlastRadiusForNode(_blastCtxNodeId, maxHops || 3);
}

async function triggerFailoverFromContext() {
  if (typeof _blastCtxNodeId === 'undefined' || !_blastCtxNodeId) return;
  hideBlastContextMenu();
  await _runSimFromContext('failover', _blastCtxNodeId);
}

async function triggerSpofFromContext() {
  hideBlastContextMenu();
  await _runSimFromContext('spof');
}

async function _runSimFromContext(simType, nodeId) {
  openSimPanel();
  const typeSelect = document.getElementById('sp-sim-type');
  if (typeSelect) {
    typeSelect.value = simType;
    onSimTypeChange(simType);
  }

  const output = document.getElementById('sim-output');

  if (!currentTopoId || currentTopoId === 'new') {
    await saveTopology();
  }
  if (!currentTopoId || currentTopoId === 'new') {
    if (output) output.textContent = 'Error: Save the topology first.';
    return;
  }

  const cell = nodeId ? graph.getCell(nodeId) : null;
  const label = cell ? (cell.attr('label/text') || nodeId) : 'topology';
  if (output) output.textContent = `Running ${simType} analysis${nodeId ? ' for "' + label + '"' : ''}...`;

  clearHighlights();
  stopAnimations();
  clearOutages();

  try {
    const body = { sim_type: simType };
    if (nodeId) body.source = nodeId;
    const r = await fetch(NC_BASE + `/api/topologies/${currentTopoId}/simulate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    const result = data.result || {};
    if (output) output.textContent = formatResult(simType, result);
    visualizeResult(simType, result);
  } catch (err) {
    if (output) output.textContent = 'Error: ' + err.message;
  }
}

/**
 * Run a blast radius simulation for a specific node ID without user needing
 * to open the sim panel manually.
 * @param {string} nodeId  - JointJS element id of the compromised device.
 * @param {number} maxHops - BFS depth (default 3).
 */
async function triggerBlastRadiusForNode(nodeId, maxHops) {
  maxHops = maxHops || 3;

  // Open sim panel and switch type to blast_radius
  openSimPanel();
  const typeSelect = document.getElementById('sp-sim-type');
  if (typeSelect) {
    typeSelect.value = 'blast_radius';
    onSimTypeChange('blast_radius');
  }
  const hopsEl = document.getElementById('sp-max-hops');
  if (hopsEl) hopsEl.value = maxHops;

  const output = document.getElementById('sim-output');

  if (!currentTopoId || currentTopoId === 'new') {
    await saveTopology();
  }
  if (!currentTopoId || currentTopoId === 'new') {
    if (output) output.textContent = 'Error: Save the topology first.';
    return;
  }

  // Find node label for display
  const cell = graph.getCell(nodeId);
  const label = cell ? (cell.attr('label/text') || nodeId) : nodeId;
  if (output) output.textContent = `Running blast radius from "${label}"...`;

  clearHighlights();
  stopAnimations();
  clearOutages();

  try {
    const r = await fetch(NC_BASE + `/api/topologies/${currentTopoId}/simulate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sim_type: 'blast_radius', source: nodeId, max_hops: maxHops })
    });
    const data = await r.json();
    const result = data.result || {};

    if (output) output.textContent = formatResult('blast_radius', result);
    visualizeResult('blast_radius', result);

    // Pulse the source node for visual emphasis
    _pulseBlastSource(nodeId);

  } catch (err) {
    if (output) output.textContent = 'Blast radius error: ' + err.message;
  }
}

/** Brief CSS pulse animation on the compromised source node. */
function _pulseBlastSource(nodeId) {
  const el = graph.getCell(nodeId);
  if (!el || !el.isElement()) return;
  let toggle = true;
  const originalStroke = el.attr('body/stroke') || '#ff1744';
  let count = 0;
  const iv = setInterval(() => {
    el.attr('body/strokeWidth', toggle ? 5 : 3);
    toggle = !toggle;
    if (++count >= 8) {
      clearInterval(iv);
      el.attr('body/strokeWidth', 3);
    }
  }, 220);
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
  const fipsInd = document.getElementById('fips-indicator');
  if (fipsInd) { fipsInd.textContent = `${pct}%`; fipsInd.style.opacity = '1'; }
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
  const fipsInd = document.getElementById('fips-indicator');
  if (fipsInd) { fipsInd.textContent = 'OFF'; fipsInd.style.opacity = '0.6'; }
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

let _ncChatContextId = sessionStorage.getItem('nc_chat_ctx_id') || null;
let _ncChatPollTimer = null;
let _ncChatLastTurn = 0;

async function ncChatInitContext() {
  if (_ncChatContextId) {
    try {
      const r = await fetch(`/network/api/ai-context/${_ncChatContextId}/messages`);
      if (r.ok) {
        const data = await r.json();
        if (data.messages) {
          data.messages.forEach(m => _ncChatAppendMsg(m.role, m.content));
        }
      }
    } catch (_) { /* ignore — stale context, will create new on next send */ }
  } else {
    try {
      const r = await fetch('/network/api/ai-context', { method: 'POST' });
      if (r.ok) {
        const data = await r.json();
        if (data.context_id) {
          _ncChatContextId = data.context_id;
          sessionStorage.setItem('nc_chat_ctx_id', _ncChatContextId);
        }
      }
    } catch (_) { /* non-fatal — chat will use fallback path */ }
  }
}

function ncChatToggle() {
  // Close rack panel if open
  if (typeof closeRackView === 'function') closeRackView();
  const panel = document.getElementById('nc-chat-panel');
  if (!panel) return;
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    document.getElementById('nc-chat-input').focus();
    if (typeof ncChatInitContext === 'function') ncChatInitContext();
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

// File attachment state
let _ncChatAttachedFile = null;  // {name, content}

function ncChatHandleFile(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    _ncChatAttachedFile = {name: file.name, content: e.target.result};
    const bar = document.getElementById('nc-chat-attach-bar');
    const nameEl = document.getElementById('nc-chat-attach-name');
    if (bar) { bar.style.display = 'flex'; }
    if (nameEl) nameEl.textContent = file.name;
  };
  reader.readAsText(file);
  input.value = '';
}

function ncChatClearAttach() {
  _ncChatAttachedFile = null;
  const bar = document.getElementById('nc-chat-attach-bar');
  if (bar) bar.style.display = 'none';
}

async function ncChatSend(opts) {
  opts = opts || {};
  const input = document.getElementById('nc-chat-input');
  const sendBtn = document.getElementById('nc-chat-send-btn');
  let text = input.value.trim();
  if (!text && !opts.description) return;
  if (opts.description) text = opts.description;

  // Append file context if attached
  let fullDescription = text;
  if (_ncChatAttachedFile) {
    const snippet = _ncChatAttachedFile.content.slice(0, 3000);
    fullDescription = text + '\n\n--- Attached: ' + _ncChatAttachedFile.name + ' ---\n' + snippet;
    ncChatClearAttach();
  }

  // Show user message
  _ncChatAppendMsg('user', _escHtml(text) + (_ncChatAttachedFile ? ' <span style="color:var(--accent);font-size:10px;">[+file]</span>' : ''));
  input.value = '';
  sendBtn.disabled = true;
  sendBtn.textContent = '...';

  // "that's all" phrases → architect mode auto-trigger
  const _thatAllPhrases = ["that's all", "thats all", "that is all", "no more info", "just go ahead",
    "just generate", "best practices", "your call", "use your judgment", "proceed", "just do it"];
  const _lowerText = text.toLowerCase();
  const _saidThatAll = _thatAllPhrases.some(p => _lowerText.includes(p));
  if (_saidThatAll) {
    opts = Object.assign({}, opts, {architect_mode: true, skip_grill: true});
  }

  // Skip grilling if: architect mode, explicit bypass, short phrase, or "that's all"
  const skipGrill = opts.architect_mode || opts.skip_grill || text.length < 15;

  if (!skipGrill) {
    _ncChatAppendMsg('assistant', '<span class="nc-chat-thinking">Analyzing your request...</span>');
    try {
      const prepR = await fetch(NC_BASE + '/api/ai-chat-prep', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({description: fullDescription}),
      });
      const prep = await prepR.json();
      const msgs = document.getElementById('nc-chat-messages');
      const thinking = msgs ? msgs.querySelector('.nc-chat-thinking') : null;
      if (thinking) thinking.closest('.nc-chat-msg').remove();

      if (prep.needs_more_info && prep.questions && prep.questions.length) {
        // Show clarifying questions with options to answer or proceed
        const qHtml = prep.questions.map(q => `<li style="margin-bottom:4px;">${_escHtml(q)}</li>`).join('');
        const assumptionNote = prep.assumption_summary
          ? `<div style="margin-top:8px;padding:6px 8px;background:rgba(52,152,219,0.08);border-radius:4px;font-size:11px;color:var(--text-dim);">
               <strong>If you proceed without answering</strong>, I'll assume: ${_escHtml(prep.assumption_summary)}
             </div>` : '';
        _ncChatAppendMsg('assistant',
          `To design the optimal network, I need a few more details:<br><ul style="margin:6px 0 0 0;padding-left:16px;">${qHtml}</ul>` +
          assumptionNote +
          `<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap;">
             <button class="nc-chat-action-btn" onclick="ncChatProceedAsArchitect(${JSON.stringify(_escHtml(fullDescription))})">
               Generate with Best Practices
             </button>
           </div>`
        );
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send';
        return;
      }
    } catch (_pe) {
      // Grill step failed — just proceed to generate
      const msgs = document.getElementById('nc-chat-messages');
      const thinking = msgs ? msgs.querySelector('.nc-chat-thinking') : null;
      if (thinking) thinking.closest('.nc-chat-msg').remove();
    }
  }

  _ncChatAppendMsg('assistant', '<span class="nc-chat-thinking">Generating topology...</span>');
  try {
    const r = await fetch(NC_BASE + '/api/ai-chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        description: fullDescription,
        context_id: _ncChatContextId,
        architect_mode: !!opts.architect_mode,
      }),
    });
    const data = await r.json();

    // Remove thinking indicator
    const msgs = document.getElementById('nc-chat-messages');
    const thinking = msgs ? msgs.querySelector('.nc-chat-thinking') : null;
    if (thinking) thinking.closest('.nc-chat-msg').remove();

    if (!r.ok || data.error) {
      _ncChatAppendMsg('system', 'Error: ' + (data.error || 'Unknown'));
    } else if (data.mode === 'qa') {
      _ncChatAppendMsg('assistant', data.answer);
    } else {
      // topology mode
      if (typeof pushUndo === 'function') pushUndo();
      if (typeof loadGraphJSON === 'function') loadGraphJSON(data.graph_json);
      if (typeof updateStatusBar === 'function') updateStatusBar();
      if (typeof markDirty === 'function') markDirty();

      const prov = data.provider ? ' via ' + data.provider : '';
      let resultHtml = `Generated <strong>${data.node_count} nodes</strong> and <strong>${data.edge_count} edges</strong>${prov}. Loaded onto canvas.` +
        `<div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">` +
        `<button class="nc-chat-action-btn" onclick="if(typeof undoAction==='function')undoAction()" style="background:rgba(231,76,60,0.15);border-color:rgba(231,76,60,0.4);">↩ Undo</button>`;

      if (data.migration_session_url) {
        resultHtml += `<a class="nc-chat-action-btn" href="${_escHtml(data.migration_session_url)}" target="_blank" style="background:rgba(39,174,96,0.15);border-color:rgba(39,174,96,0.4);text-decoration:none;">🔄 Open Migration Workflow</a>`;
      }
      resultHtml += `</div>`;

      if (data.is_migration) {
        resultHtml += `<div style="margin-top:6px;padding:5px 8px;background:rgba(243,156,18,0.1);border-radius:4px;font-size:11px;color:#f39c12;">
        Migration diagram: phases are laid out left-to-right (Silver=AS-IS → Orange=Phase 1 → Green=TO-BE).
      </div>`;
      }

      _ncChatAppendMsg('assistant', resultHtml);
      setStatus('AI generated: ' + data.node_count + ' nodes, ' + data.edge_count + ' edges');
    }
  } catch (err) {
    const msgs = document.getElementById('nc-chat-messages');
    const thinking = msgs ? msgs.querySelector('.nc-chat-thinking') : null;
    if (thinking) thinking.closest('.nc-chat-msg').remove();
    _ncChatAppendMsg('system', 'Request failed: ' + _escHtml(err.message));
  }

  sendBtn.disabled = false;
  sendBtn.textContent = 'Send';
}

function ncChatProceedAsArchitect(description) {
  // Decode HTML entities that were escaped for display
  const txt = document.createElement('textarea');
  txt.innerHTML = description;
  ncChatSend({description: txt.value, architect_mode: true, skip_grill: true, context_id: _ncChatContextId});
}

async function _ncChatDirectGenerate(description, opts) {
  opts = opts || {};
  try {
    const r = await fetch(NC_BASE + '/api/ai-chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({description, context_id: _ncChatContextId, architect_mode: !!opts.architect_mode}),
    });
    const data = await r.json();

    // Remove thinking indicator
    const msgs = document.getElementById('nc-chat-messages');
    const thinking = msgs ? msgs.querySelector('.nc-chat-thinking') : null;
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
    let resultHtml = `Generated <strong>${data.node_count} nodes</strong> and <strong>${data.edge_count} edges</strong>${prov}. Loaded onto canvas.` +
      `<div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">` +
      `<button class="nc-chat-action-btn" onclick="if(typeof undoAction==='function')undoAction()" style="background:rgba(231,76,60,0.15);border-color:rgba(231,76,60,0.4);">↩ Undo</button>`;

    // Show migration workflow button when a session was created
    if (data.migration_session_url) {
      resultHtml += `<a class="nc-chat-action-btn" href="${_escHtml(data.migration_session_url)}" target="_blank" style="background:rgba(39,174,96,0.15);border-color:rgba(39,174,96,0.4);text-decoration:none;">🔄 Open Migration Workflow</a>`;
    }
    resultHtml += `</div>`;

    if (data.is_migration) {
      resultHtml += `<div style="margin-top:6px;padding:5px 8px;background:rgba(243,156,18,0.1);border-radius:4px;font-size:11px;color:#f39c12;">
        Migration diagram: phases are laid out left-to-right (Silver=AS-IS → Orange=Phase 1 → Green=TO-BE).
      </div>`;
    }

    _ncChatAppendMsg('assistant', resultHtml);
    setStatus('AI generated: ' + data.node_count + ' nodes, ' + data.edge_count + ' edges');
  } catch (err) {
    const msgs = document.getElementById('nc-chat-messages');
    const thinking = msgs ? msgs.querySelector('.nc-chat-thinking') : null;
    if (thinking) thinking.closest('.nc-chat-msg').remove();
    _ncChatAppendMsg('system', 'Request failed: ' + _escHtml(err.message));
  }
}

// ── AI Chat History ────────────────────────────────────────────────────────

let _ncHistoryVisible = false;

function ncChatToggleHistory() {
  const panel = document.getElementById('nc-chat-history-panel');
  const msgs  = document.getElementById('nc-chat-messages');
  const btn   = document.getElementById('nc-chat-history-btn');
  _ncHistoryVisible = !_ncHistoryVisible;
  if (_ncHistoryVisible) {
    panel.style.display = 'flex';
    msgs.style.display  = 'none';
    btn.style.background = 'rgba(116,185,255,.15)';
    btn.style.borderColor = 'rgba(116,185,255,.4)';
    _ncChatLoadHistory();
  } else {
    panel.style.display = 'none';
    msgs.style.display  = '';
    btn.style.background = '';
    btn.style.borderColor = '';
  }
}

async function _ncChatLoadHistory() {
  const list = document.getElementById('nc-chat-history-list');
  if (!list) return;
  list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim);font-size:12px;">Loading…</div>';
  try {
    const r = await fetch(NC_BASE + '/api/ai-history?limit=30');
    const data = await r.json();
    const entries = data.entries || [];
    if (!entries.length) {
      list.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-dim);font-size:12px;">No history yet.<br>Generate a topology to start building history.</div>';
      return;
    }
    list.innerHTML = entries.map(e => {
      const badge = e.is_migration
        ? '<span class="nc-history-badge nc-history-badge-mig">Migration</span>'
        : '<span class="nc-history-badge nc-history-badge-gen">Topology</span>';
      const ts = e.created_at ? new Date(e.created_at + 'Z').toLocaleString(undefined, {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
      const prov = e.provider ? ` · ${e.provider}` : '';
      return `<div class="nc-history-item" data-id="${_escHtml(e.id)}">
        <div class="nc-history-desc" title="${_escHtml(e.short_desc)}">${_escHtml(e.short_desc)}</div>
        <div class="nc-history-meta">
          ${badge}
          <span>${e.node_count}N · ${e.edge_count}E${prov}</span>
          <span>${ts}</span>
          <div class="nc-history-actions">
            <button onclick="ncHistoryReuse('${_escHtml(e.id)}',event)" title="Paste description into input">↩ Reuse</button>
            <button onclick="ncHistoryRestore('${_escHtml(e.id)}',event)" title="Load topology onto canvas">⬆ Load</button>
            <button onclick="ncHistoryDelete('${_escHtml(e.id)}',event)" title="Delete this entry" style="color:var(--danger,#e74c3c);">✕</button>
          </div>
        </div>
      </div>`;
    }).join('');
  } catch (err) {
    list.innerHTML = `<div style="padding:16px;color:var(--danger,#e74c3c);font-size:12px;">Failed to load history: ${_escHtml(err.message)}</div>`;
  }
}

async function ncHistoryReuse(id, evt) {
  if (evt) evt.stopPropagation();
  try {
    const r = await fetch(`${NC_BASE}/api/ai-history/${encodeURIComponent(id)}`);
    const data = await r.json();
    if (data.description) {
      const input = document.getElementById('nc-chat-input');
      if (input) { input.value = data.description; input.focus(); }
      ncChatToggleHistory();   // flip back to chat view
    }
  } catch (err) {
    alert('Could not load description: ' + err.message);
  }
}

async function ncHistoryRestore(id, evt) {
  if (evt) evt.stopPropagation();
  try {
    const r = await fetch(`${NC_BASE}/api/ai-history/${encodeURIComponent(id)}`);
    const data = await r.json();
    if (data.graph_json) {
      if (typeof pushUndo === 'function') pushUndo();
      if (typeof loadGraphJSON === 'function') loadGraphJSON(data.graph_json);
      if (typeof updateStatusBar === 'function') updateStatusBar();
      if (typeof markDirty === 'function') markDirty();
      ncChatToggleHistory();
      _ncChatAppendMsg('assistant',
        `Restored: <strong>${data.node_count} nodes</strong>, <strong>${data.edge_count} edges</strong> from history.` +
        `<button class="nc-chat-action-btn" onclick="if(typeof undoAction==='function')undoAction()">↩ Undo</button>`
      );
      if (typeof setStatus === 'function') setStatus('Restored from history: ' + data.node_count + ' nodes');
    }
  } catch (err) {
    alert('Could not restore topology: ' + err.message);
  }
}

async function ncHistoryDelete(id, evt) {
  if (evt) evt.stopPropagation();
  if (!confirm('Delete this history entry?')) return;
  try {
    await fetch(`${NC_BASE}/api/ai-history/${encodeURIComponent(id)}`, {method: 'DELETE'});
    _ncChatLoadHistory();   // refresh list
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
}

async function ncChatClearAllHistory() {
  if (!confirm('Clear all AI generation history?')) return;
  const list = document.getElementById('nc-chat-history-list');
  try {
    const r = await fetch(NC_BASE + '/api/ai-history?limit=100');
    const data = await r.json();
    await Promise.all((data.entries || []).map(e =>
      fetch(`${NC_BASE}/api/ai-history/${encodeURIComponent(e.id)}`, {method: 'DELETE'})
    ));
    if (list) list.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-dim);font-size:12px;">History cleared.</div>';
  } catch (err) {
    alert('Clear failed: ' + err.message);
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

/* ── Rack Elevation View ──────────────────────────────────────────────────────── */

function renderRackView() {
  if (!graph) return;

  const panel = document.getElementById('nc-rack-panel');
  if (panel.classList.contains('hidden')) return;

  const uSize = parseInt(document.getElementById('rack-u-size').value) || 42;
  const svg = document.getElementById('rack-svg');

  // Find the rack context — what rack are we viewing?
  let rackFilter = '';
  let siteLabel = 'All Devices with Rack Info';

  if (selectedCell && selectedCell.isElement && selectedCell.isElement()) {
    const config = selectedCell.get('configData') || {};
    if (config.rack) {
      rackFilter = config.rack.split('/')[0].trim();
      siteLabel = (config.site || 'Site') + ' \u2014 ' + rackFilter;
      if (config.location) siteLabel += ' (' + config.location + ')';
    }
  }

  document.getElementById('rack-site-label').textContent = siteLabel;

  // Collect all devices that belong to this rack
  const elements = graph.getElements();
  const rackDevices = [];

  elements.forEach(function(el) {
    const nodeType = el.get('nodeType') || '';
    if (nodeType.startsWith('draw-') || nodeType.startsWith('text-')) return;
    const config = el.get('configData') || {};
    if (!config.rack) return;

    const rackName = config.rack.split('/')[0].trim();
    if (rackFilter && rackName !== rackFilter) return;

    // Parse U-position: "Rack-12 / U22-U24" or "Rack-12 / U22"
    var uStart = 1, uEnd = 1;
    var uMatch = config.rack.match(/U(\d+)(?:\s*-\s*U?(\d+))?/i);
    if (uMatch) {
      uStart = parseInt(uMatch[1]);
      uEnd = uMatch[2] ? parseInt(uMatch[2]) : uStart;
    }

    var style = (typeof getStyle === 'function') ? getStyle(nodeType) : {stroke: '#3498db'};
    rackDevices.push({
      id: el.id,
      label: config.hostname || el.attr('label/text') || nodeType,
      type: nodeType,
      uStart: Math.min(uStart, uEnd),
      uEnd: Math.max(uStart, uEnd),
      color: config._fill || style.stroke || '#3498db',
      model: config.model || '',
      isSelected: selectedCell && selectedCell.id === el.id,
    });
  });

  // SVG dimensions
  var padTop = 30;
  var padLeft = 35;
  var rackWidth = 220;
  var uHeight = 18;
  var totalHeight = padTop + (uSize * uHeight) + 20;

  svg.setAttribute('height', totalHeight);
  svg.setAttribute('width', 300);

  var svgContent = '';

  // Rack frame
  svgContent += '<rect x="' + (padLeft-4) + '" y="' + (padTop-4) + '" width="' + (rackWidth+8) + '" height="' + (uSize*uHeight+8) + '" rx="3" fill="#1a1a1a" stroke="#636e72" stroke-width="2"/>';

  // Rails
  svgContent += '<rect x="' + padLeft + '" y="' + padTop + '" width="8" height="' + (uSize*uHeight) + '" fill="#2d3436"/>';
  svgContent += '<rect x="' + (padLeft+rackWidth-8) + '" y="' + padTop + '" width="8" height="' + (uSize*uHeight) + '" fill="#2d3436"/>';

  // U-position lines and numbers (bottom-up: U1 at bottom)
  for (var u = 1; u <= uSize; u++) {
    var y = padTop + (uSize - u) * uHeight;
    svgContent += '<line x1="' + (padLeft+8) + '" y1="' + y + '" x2="' + (padLeft+rackWidth-8) + '" y2="' + y + '" stroke="#2d3436" stroke-width="0.5"/>';
    if (u % 3 === 1 || u === 1) {
      svgContent += '<text x="' + (padLeft-6) + '" y="' + (y+uHeight-4) + '" text-anchor="end" fill="#636e72" font-size="9" font-family="Consolas,monospace">' + u + '</text>';
    }
  }

  // ── Fetch rack infrastructure from DB (PDU, UPS, Patch Panels) ──
  // These items are NOT on the canvas — they live in nc_racks.notes JSON
  if (rackFilter && typeof NC_BASE !== 'undefined') {
    fetch(NC_BASE + '/api/racks').then(function(r) { return r.json(); }).then(function(racks) {
      var rack = racks.find(function(r) { return r.rack_name === rackFilter; });
      if (!rack || !rack.notes) return;
      try {
        var notes = JSON.parse(rack.notes);
        var infra = notes.infra || [];
        var infraColors = {'PDU-A':'#e65100','PDU-B':'#e65100','UPS':'#f57f17','Fiber PP':'#37474f','Copper PP':'#455a64'};
        infra.forEach(function(item) {
          var uMatch = (item.ru || '').match(/U(\d+)(?:\s*-\s*U?(\d+))?/i);
          if (!uMatch) return;
          var uStart = parseInt(uMatch[1]);
          var uEnd = uMatch[2] ? parseInt(uMatch[2]) : uStart;
          rackDevices.push({
            id: 'infra-' + item.type,
            label: item.type,
            type: 'infra',
            uStart: Math.min(uStart, uEnd),
            uEnd: Math.max(uStart, uEnd),
            color: infraColors[item.type] || '#666',
            model: item.model || '',
            isSelected: false,
            isInfra: true,
          });
        });
        // Re-render with infra included
        _drawRackDevices(svg, rackDevices, uSize, padTop, padLeft, rackWidth, uHeight);
        // Update stats
        var usedU = 0;
        rackDevices.forEach(function(d) { usedU += (d.uEnd - d.uStart + 1); });
        document.getElementById('rack-stat-used').textContent = 'Used: ' + usedU + 'U';
        document.getElementById('rack-stat-free').textContent = 'Free: ' + (uSize - usedU) + 'U';
        document.getElementById('rack-stat-power').textContent = 'Devices: ' + rackDevices.length;
      } catch(e) { /* ignore parse errors */ }
    }).catch(function() { /* no rack API */ });
  }

  // Draw devices
  _drawRackDevices(svg, rackDevices, uSize, padTop, padLeft, rackWidth, uHeight);

  // Update stats
  var usedU = 0;
  rackDevices.forEach(function(d) { usedU += (d.uEnd - d.uStart + 1); });
  document.getElementById('rack-stat-used').textContent = 'Used: ' + usedU + 'U';
  document.getElementById('rack-stat-free').textContent = 'Free: ' + (uSize - usedU) + 'U';
  document.getElementById('rack-stat-power').textContent = 'Devices: ' + rackDevices.length;
}

function _drawRackDevices(svg, rackDevices, uSize, padTop, padLeft, rackWidth, uHeight) {
  // Infra style: dashed border for infrastructure items
  var infraPattern = {'PDU-A':true,'PDU-B':true,'UPS':true,'Fiber PP':true,'Copper PP':true};

  var svgContent = '';
  rackDevices.forEach(function(dev) {
    var yTop = padTop + (uSize - dev.uEnd) * uHeight;
    var height = (dev.uEnd - dev.uStart + 1) * uHeight;
    var x = padLeft + 10;
    var w = rackWidth - 20;

    var isInfra = dev.isInfra || infraPattern[dev.label];
    var borderColor = dev.isSelected ? '#e94560' : (isInfra ? '#666' : '#444');
    var borderWidth = dev.isSelected ? 2 : 1;
    var dash = isInfra ? ' stroke-dasharray="4 2"' : '';
    var opacity = isInfra ? '0.5' : '0.7';

    svgContent += '<rect x="' + x + '" y="' + (yTop+1) + '" width="' + w + '" height="' + (height-2) + '" rx="2" fill="' + dev.color + '" fill-opacity="' + opacity + '" stroke="' + borderColor + '" stroke-width="' + borderWidth + '"' + dash + ' cursor="pointer"/>';
    svgContent += '<text x="' + (x+6) + '" y="' + (yTop+height/2+4) + '" fill="#fff" font-size="10" font-family="Segoe UI,sans-serif" font-weight="' + (isInfra ? 'normal' : '600') + '">' + dev.label + '</text>';
    if (dev.model) {
      svgContent += '<text x="' + (x+w-4) + '" y="' + (yTop+height/2+4) + '" text-anchor="end" fill="rgba(255,255,255,0.5)" font-size="8" font-family="Consolas,monospace">' + dev.model + '</text>';
    }
    var uLabel = dev.uStart === dev.uEnd ? 'U' + dev.uStart : 'U' + dev.uStart + '-' + dev.uEnd;
    svgContent += '<text x="' + (padLeft+rackWidth+4) + '" y="' + (yTop+height/2+4) + '" fill="#636e72" font-size="8" font-family="Consolas,monospace">' + uLabel + '</text>';
  });

  svg.innerHTML += svgContent;
}

function toggleRackView() {
  var panel = document.getElementById('nc-rack-panel');
  if (!panel) return;
  // Close chat panel if open
  var chatPanel = document.getElementById('nc-chat-panel');
  if (chatPanel && !chatPanel.classList.contains('hidden')) chatPanel.classList.add('hidden');
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    renderRackView();
  }
}

function closeRackView() {
  document.getElementById('nc-rack-panel').classList.add('hidden');
}

/* ══════════════════════════════════════════════════════════════════════════════
 * PPS Matrix Generator — In-Canvas Panel
 * Select two enclaves or a device pair, auto-generate PPS matrix from
 * firewall rules and link annotations. Exportable as SSP table (CSV/MD).
 * ══════════════════════════════════════════════════════════════════════════════ */

var _ppsLastResult = null;
var _ppsSelMode = 'zone';

function openPpsPanel() {
  if (!currentTopoId || currentTopoId === 'new') {
    alert('Save the topology first before generating a PPS matrix.');
    return;
  }
  document.getElementById('pps-overlay').classList.remove('hidden');
  _ppsLoadEnclaves();
}

function closePpsPanel() {
  document.getElementById('pps-overlay').classList.add('hidden');
}

function ppsOnModeChange(mode) {
  _ppsSelMode = mode;
  document.getElementById('pps-zone-selectors').style.display = mode === 'zone' ? 'flex' : 'none';
  document.getElementById('pps-node-selectors').style.display = mode === 'node' ? 'flex' : 'none';
}

function _ppsLoadEnclaves() {
  fetch(NC_BASE + '/api/pps/' + currentTopoId + '/enclaves')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      // Populate zone dropdowns
      var srcZone = document.getElementById('pps-src-zone');
      var dstZone = document.getElementById('pps-dst-zone');
      srcZone.innerHTML = '<option value="">-- select --</option>';
      dstZone.innerHTML = '<option value="">-- select --</option>';
      (data.enclaves || []).forEach(function(e) {
        var opt1 = '<option value="' + e.id + '">' + e.label + ' (' + e.node_count + ' nodes)</option>';
        srcZone.insertAdjacentHTML('beforeend', opt1);
        dstZone.insertAdjacentHTML('beforeend', opt1);
      });

      // Populate node dropdowns
      var srcNode = document.getElementById('pps-src-node');
      var dstNode = document.getElementById('pps-dst-node');
      srcNode.innerHTML = '<option value="">-- select --</option>';
      dstNode.innerHTML = '<option value="">-- select --</option>';
      (data.nodes || []).forEach(function(n) {
        var opt2 = '<option value="' + n.id + '">' + n.label + ' [' + n.type + '] \u2014 ' + n.zone + '</option>';
        srcNode.insertAdjacentHTML('beforeend', opt2);
        dstNode.insertAdjacentHTML('beforeend', opt2);
      });
    })
    .catch(function(err) {
      console.error('PPS: failed to load enclaves', err);
    });
}

function ppsGenerate() {
  var source = _ppsSelMode === 'zone'
    ? document.getElementById('pps-src-zone').value
    : document.getElementById('pps-src-node').value;
  var dest = _ppsSelMode === 'zone'
    ? document.getElementById('pps-dst-zone').value
    : document.getElementById('pps-dst-node').value;

  if (!source || !dest) {
    alert('Please select both source and destination.');
    return;
  }
  if (source === dest) {
    alert('Source and destination must be different.');
    return;
  }

  // Reset UI
  document.getElementById('pps-matrix-section').style.display = 'none';
  document.getElementById('pps-fw-section').style.display = 'none';
  document.getElementById('pps-stats-bar').style.display = 'none';
  document.getElementById('pps-empty').style.display = 'none';
  document.getElementById('pps-export-csv-btn').style.display = 'none';
  document.getElementById('pps-export-md-btn').style.display = 'none';
  document.getElementById('pps-loading').style.display = '';

  fetch(NC_BASE + '/api/pps/' + currentTopoId + '/generate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({source: source, dest: dest, selector_type: _ppsSelMode})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    document.getElementById('pps-loading').style.display = 'none';
    if (data.error) { alert('Error: ' + data.error); return; }
    _ppsLastResult = data;
    _ppsRenderResults(data);
  })
  .catch(function(err) {
    document.getElementById('pps-loading').style.display = 'none';
    alert('PPS request failed: ' + err);
  });
}

function _ppsRenderResults(data) {
  var total = data.total_protocols + data.total_fw_rules;

  // Summary stats bar
  var statsBar = document.getElementById('pps-stats-bar');
  var insecureColor = data.insecure_protocols > 0 ? '#e74c3c' : '#2ecc71';
  statsBar.innerHTML =
    '<div style="background:#1a2332;border:1px solid #334;border-radius:6px;padding:8px 14px;text-align:center;min-width:80px;">' +
      '<div style="font-size:1.4rem;font-weight:700;color:#3498db;">' + data.total_protocols + '</div>' +
      '<div style="font-size:10px;color:#95a5a6;">Protocols</div></div>' +
    '<div style="background:#1a2332;border:1px solid #334;border-radius:6px;padding:8px 14px;text-align:center;min-width:80px;">' +
      '<div style="font-size:1.4rem;font-weight:700;color:#2ecc71;">' + data.encrypted_protocols + '</div>' +
      '<div style="font-size:10px;color:#95a5a6;">Encrypted</div></div>' +
    '<div style="background:#1a2332;border:1px solid #334;border-radius:6px;padding:8px 14px;text-align:center;min-width:80px;">' +
      '<div style="font-size:1.4rem;font-weight:700;color:' + insecureColor + ';">' + data.insecure_protocols + '</div>' +
      '<div style="font-size:10px;color:#95a5a6;">Insecure</div></div>' +
    '<div style="background:#1a2332;border:1px solid #334;border-radius:6px;padding:8px 14px;text-align:center;min-width:80px;">' +
      '<div style="font-size:1.4rem;font-weight:700;color:#f39c12;">' + data.total_fw_rules + '</div>' +
      '<div style="font-size:10px;color:#95a5a6;">FW Rules</div></div>' +
    '<div style="background:#1a2332;border:1px solid #334;border-radius:6px;padding:8px 14px;text-align:center;min-width:100px;">' +
      '<div style="font-size:0.9rem;font-weight:600;color:#dfe6e9;">' + data.source_label + '</div>' +
      '<div style="font-size:10px;color:#95a5a6;">Source</div></div>' +
    '<div style="background:#1a2332;border:1px solid #334;border-radius:6px;padding:8px 14px;text-align:center;min-width:100px;">' +
      '<div style="font-size:0.9rem;font-weight:600;color:#dfe6e9;">' + data.dest_label + '</div>' +
      '<div style="font-size:10px;color:#95a5a6;">Destination</div></div>';
  statsBar.style.display = 'flex';

  if (total === 0) {
    document.getElementById('pps-empty').style.display = '';
    return;
  }

  // Matrix title
  document.getElementById('pps-matrix-title').textContent =
    'PPS Matrix \u2014 ' + data.source_label + ' \u2194 ' + data.dest_label;

  // Protocol rows
  var tbody = document.getElementById('pps-matrix-tbody');
  tbody.innerHTML = '';
  (data.matrix || []).forEach(function(row, i) {
    var riskColor = row.risk === 'HIGH' ? '#e74c3c' : row.risk === 'LOW' ? '#2ecc71' : '#f39c12';
    var encIcon = row.encrypted ? '\uD83D\uDD12' : '\u26A0';
    var fipsIcon = row.fips_validated ? '\u2713' : '\u2014';
    var eps = (row.endpoints || []).slice(0, 3).join('<br>');
    tbody.insertAdjacentHTML('beforeend',
      '<tr class="pps-row" data-proto="' + row.protocol.toLowerCase() + '" style="border-bottom:1px solid #253040;">' +
        '<td style="padding:5px 8px;">' + (i + 1) + '</td>' +
        '<td style="padding:5px 8px;font-weight:600;">' + row.protocol + '</td>' +
        '<td style="padding:5px 8px;font-family:Consolas,monospace;font-size:11px;">' + row.port + '</td>' +
        '<td style="padding:5px 8px;">' + row.service + '</td>' +
        '<td style="padding:5px 8px;font-size:11px;">' + row.direction + '</td>' +
        '<td style="padding:5px 8px;text-align:center;">' + encIcon + '</td>' +
        '<td style="padding:5px 8px;text-align:center;">' + fipsIcon + '</td>' +
        '<td style="padding:5px 8px;"><span style="color:' + riskColor + ';">' + row.risk + '</span></td>' +
        '<td style="padding:5px 8px;font-size:11px;max-width:240px;">' + row.justification + '</td>' +
        '<td style="padding:5px 8px;font-size:10px;color:#95a5a6;">' + eps + '</td>' +
      '</tr>');
  });
  document.getElementById('pps-matrix-section').style.display = '';

  // Firewall rules
  if (data.firewall_rules && data.firewall_rules.length > 0) {
    var fwTbody = document.getElementById('pps-fw-tbody');
    fwTbody.innerHTML = '';
    data.firewall_rules.forEach(function(row, i) {
      var fwRiskColor = row.risk === 'HIGH' ? '#e74c3c' : '#f39c12';
      var fwEncIcon = row.encrypted ? '\uD83D\uDD12' : '\u2014';
      fwTbody.insertAdjacentHTML('beforeend',
        '<tr style="border-bottom:1px solid #253040;">' +
          '<td style="padding:5px 8px;">' + (i + 1) + '</td>' +
          '<td style="padding:5px 8px;font-weight:600;">' + row.protocol + '</td>' +
          '<td style="padding:5px 8px;font-family:Consolas,monospace;font-size:11px;">' + row.port + '</td>' +
          '<td style="padding:5px 8px;">' + row.service + '</td>' +
          '<td style="padding:5px 8px;font-size:11px;">' + row.direction + '</td>' +
          '<td style="padding:5px 8px;text-align:center;">' + fwEncIcon + '</td>' +
          '<td style="padding:5px 8px;"><span style="color:' + fwRiskColor + ';">' + row.risk + '</span></td>' +
          '<td style="padding:5px 8px;font-size:11px;">' + row.justification + '</td>' +
        '</tr>');
    });
    document.getElementById('pps-fw-section').style.display = '';
  }

  // Show export buttons
  document.getElementById('pps-export-csv-btn').style.display = '';
  document.getElementById('pps-export-md-btn').style.display = '';
}

function ppsFilterTable() {
  var q = document.getElementById('pps-filter').value.toLowerCase();
  document.querySelectorAll('#pps-matrix-tbody tr.pps-row').forEach(function(tr) {
    tr.style.display = tr.textContent.toLowerCase().indexOf(q) >= 0 ? '' : 'none';
  });
}

function ppsExport(fmt) {
  if (!_ppsLastResult) return;
  var source = _ppsLastResult.source_selector;
  var dest = _ppsLastResult.dest_selector;
  fetch(NC_BASE + '/api/pps/' + currentTopoId + '/export', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      source: source,
      dest: dest,
      selector_type: _ppsLastResult.selector_type,
      format: fmt
    })
  })
  .then(function(r) {
    var disposition = r.headers.get('Content-Disposition') || '';
    var fnMatch = disposition.match(/filename="(.+?)"/);
    var filename = fnMatch ? fnMatch[1] : ('pps_ssp.' + (fmt === 'csv' ? 'csv' : 'md'));
    return r.blob().then(function(blob) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    });
  })
  .catch(function(err) { alert('Export failed: ' + err); });
}

function exportRackSVG() {
  var svg = document.getElementById('rack-svg');
  if (!svg) return;
  var svgData = '<?xml version="1.0" encoding="UTF-8"?>' +
    '<svg xmlns="http://www.w3.org/2000/svg" ' + svg.outerHTML.slice(4);
  var blob = new Blob([svgData], {type: 'image/svg+xml'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'rack-elevation.svg';
  a.click();
}

/* ── Init zoom on DOMContentLoaded ────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  // Wait a tick for canvas.js to init paper
  setTimeout(initZoomWheel, 500);
  setTimeout(initClassificationBanner, 600);
  setTimeout(initPaletteStencils, 300);
});
