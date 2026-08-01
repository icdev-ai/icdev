/* CUI // SP-CTI — Pipeline Design Canvas: Snippet Integration Engine
 * Templates modal + snippet insertion, classification/air-gap rules, the
 * auto-connect suggestion rule engine, and the integration guide overlay.
 * Split from the pipeline-canvas.js monolith (pdx-ux-01).
 *
 * Depends (at runtime) on pipeline-canvas-core.js (graph, pushUndo, markDirty,
 * updateStatus, createNode, createLink, _getCanvasBounds, _findFreePosition,
 * escapeHtml/escapeAttr, fetchJson, pdcToast, openRightPanel), pipeline-node-
 * styles.js (PIPELINE_TYPE_SETS), and pipeline-analysis.js (_section, _pill).
 * Classic script — declarations stay top-level.
 */

'use strict';

// ── Templates & Snippets (drawer / modal) ───────────────────────────────────

function openTemplatesModal() { document.getElementById('pc-templates-modal')?.classList.add('open'); }
function closeTemplatesModal(){ document.getElementById('pc-templates-modal')?.classList.remove('open'); }

function loadTemplate(tplId) {
  fetchJson(`/devops/api/templates/${tplId}/load`, { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    .then(data => {
      if (data && data.id) { window.location.href = `/devops/canvas/${data.id}`; }
      else { pdcToast('Template loaded but no pipeline id was returned.', 'error'); }
    })
    .catch(err => pdcToast('Failed to load template: ' + (err && err.message ? err.message : 'unknown error'), 'error'));
}

function toggleSnippets() { document.querySelector('.pc-snippet-drawer')?.classList.toggle('open'); }

// ── Classification & Security Levels ────────────────────────────────────────

const CLASSIFICATION_RANK = { 'public': 0, 'IL2': 0, 'unclassified': 0, 'CUI': 1, 'IL4': 1, 'IL5': 2, 'SECRET': 3, 'IL6': 3, 'TOP SECRET': 4 };

function _nodeClassification(el) {
  const type = el.get ? el.get('nodeType') || '' : el.type || '';
  const label = el.get ? (el.attr?.('label/text') || '') : el.label || '';
  const config = el.get ? (el.get('configData') || {}) : el.config || {};
  if (config.classification) return config.classification;
  if (config.network === 'SIPR' || /SIPR|SECRET|IL6/i.test(label)) return 'SECRET';
  if (/JWICS|TOP.SECRET|TS\b/i.test(label)) return 'TOP SECRET';
  if (/CUI|IL4|IL5|govcloud/i.test(label)) return 'CUI';
  if (type.startsWith('pipeline-sipr') || type.startsWith('boundary-secret')) return 'SECRET';
  if (type.startsWith('pipeline-jwics') || type.startsWith('boundary-topsecret')) return 'TOP SECRET';
  if (type.startsWith('boundary-govcloud')) return 'CUI';
  if (type.startsWith('boundary-commercial')) return 'public';
  return null; // Unknown — treat as same-level
}

function _isAirgapped(snippetData) {
  const tags = snippetData.tags || [];
  const il = (snippetData.impact_level || '').toUpperCase();
  const cl = (snippetData.classification_level || '').toUpperCase();
  return tags.includes('airgap') || il === 'IL6' || cl === 'SECRET' || cl === 'TOP SECRET' ||
         (snippetData.graph_json?.nodes || []).some(n => ['pipeline-sipr','pipeline-jwics','cds-data-diode','sneakernet','vuln-db-mirror','package-mirror'].includes(n.type));
}

function _crossDomainViolation(srcClass, tgtClass) {
  const srcRank = CLASSIFICATION_RANK[srcClass] ?? 1;
  const tgtRank = CLASSIFICATION_RANK[tgtClass] ?? 1;
  if (Math.abs(srcRank - tgtRank) >= 2) return { blocked: true, reason: 'Cross-domain: requires CDS Guard or data diode (CNSS Policy 15, SC-7)' };
  if (srcRank >= 3 && tgtRank <= 0) return { blocked: true, reason: 'SECRET/TS cannot connect directly to public (requires CDS)' };
  if (srcRank !== tgtRank && srcRank >= 2) return { blocked: false, reason: 'Different classification levels — encryption required (SC-8, SC-13)' };
  return null;
}

// Suggestions produced by the most recent _showIntegrationGuide, consumed by
// applyAllSuggestions. Module-scoped top-level (pdx-ux-01: was window._pending-
// Suggestions; both writer and reader live in this file so no global needed).
let _pendingSuggestions = [];

// ── Smart Snippet Insertion ─────────────────────────────────────────────────

function loadSnippet(snippetId) {
  fetchJson(`/devops/api/snippets/${snippetId}`)
    .then(data => {
      if (!data || !data.graph_json) { pdcToast('Snippet has no graph content to load.', 'error'); return; }
      pushUndo();
      const g = data.graph_json;
      const snippetNodes = g.nodes || [];
      const snippetEdges = g.edges || [];
      const isAirgap = _isAirgapped(data);
      const snippetClass = data.classification_level || 'CUI';

      // ── Step 1: Collision-free placement ──
      // Compute the snippet's own bounding box, then ask the shared
      // _findFreePosition helper (pipeline-canvas-core.js) for a free slot for a
      // box of that size, seeded just to the right of existing content.
      // (pdx-ux-01: replaces the snippet engine's bespoke spiral-overlap loop —
      // one collision helper now serves drop, click-to-add, and snippets.)
      const snMinX = Math.min(...snippetNodes.map(n => n.x || 0));
      const snMinY = Math.min(...snippetNodes.map(n => n.y || 0));
      const snMaxX = Math.max(...snippetNodes.map(n => (n.x || 0) + 110));
      const snMaxY = Math.max(...snippetNodes.map(n => (n.y || 0) + 60));
      const snW = snMaxX - snMinX + 60;
      const snH = snMaxY - snMinY + 60;

      const bounds = _getCanvasBounds();
      const seedX = bounds.maxX > 0 ? bounds.maxX + 100 : 50;
      const seedY = bounds.minY !== Infinity ? bounds.minY : 50;
      const spot = _findFreePosition(seedX, seedY, snW, snH);
      const offsetX = spot.x - snMinX;
      const offsetY = spot.y - snMinY;

      // ── Step 2: Insert nodes with unique IDs ──
      const prefix = 'sn' + Date.now().toString(36) + '-';
      const idMap = {};
      const newNodes = [];
      snippetNodes.forEach(n => {
        const newId = prefix + (n.id || joint.util.uuid());
        idMap[n.id] = newId;
        const node = createNode(n.type, (n.x || 0) + offsetX, (n.y || 0) + offsetY, n.label, newId);
        newNodes.push({ id: newId, type: n.type, label: n.label, el: node });
      });

      // ── Step 3: Insert edges with remapped IDs ──
      snippetEdges.forEach(e => {
        createLink(idMap[e.source] || e.source, idMap[e.target] || e.target, e.label);
      });

      // ── Step 4: Build integration suggestions ──
      const suggestions = _buildIntegrationSuggestions(newNodes, data, isAirgap, snippetClass);

      // ── Step 5: Show integration guide if there are existing nodes ──
      // Show integration guide if there are existing nodes on canvas
      const existingCount = graph.getElements().filter(el => !newNodes.some(sn => sn.id === el.id) && !el.get('isBoundary')).length;
      if (existingCount > 0) {
        _showIntegrationGuide(data, suggestions, isAirgap, snippetClass);
      }

      markDirty();
      toggleSnippets();
      updateStatus(`Loaded snippet: ${data.name} (${newNodes.length} nodes)`);
    })
    .catch(err => pdcToast('Failed to load snippet: ' + (err && err.message ? err.message : 'unknown error'), 'error'));
}

// ── Auto-Connect Rule Engine ────────────────────────────────────────────────

function _buildIntegrationSuggestions(snippetNodes, snippetData, isAirgap, snippetClass) {
  const existingEls = graph.getElements().filter(el => !el.get('isBoundary') && !snippetNodes.some(sn => sn.id === el.id));
  if (!existingEls.length) return [];

  const suggestions = [];

  snippetNodes.forEach(sn => {
    const snType = sn.type;
    const snClass = snippetClass;

    existingEls.forEach(existing => {
      const exType = existing.get('nodeType') || '';
      const exLabel = existing.attr('label/text') || '';
      const exClass = _nodeClassification(existing) || 'CUI';

      // Classification check
      const violation = _crossDomainViolation(snClass, exClass);
      if (violation && violation.blocked) return; // Hard block

      // Air-gap check: snippet is air-gapped but existing node is cloud-managed
      if (isAirgap && PIPELINE_TYPE_SETS.CLOUD_MANAGED.has(exType)) return; // No cloud connections for air-gapped

      // ── Rule 1: SCM → CI engine (source triggers build)
      if (PIPELINE_TYPE_SETS.SCM.has(snType) && PIPELINE_TYPE_SETS.CI_ENGINE.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'push trigger', reason: 'Source control triggers CI pipeline', priority: 1, protocol: 'webhook' });
      }
      if (PIPELINE_TYPE_SETS.CI_ENGINE.has(snType) && PIPELINE_TYPE_SETS.SCM.has(exType)) {
        suggestions.push({ from: existing, to: sn, label: 'push trigger', reason: 'Source control triggers CI pipeline', priority: 1, protocol: 'webhook' });
      }

      // ── Rule 2: CI engine → Scanner (build triggers scan)
      if (PIPELINE_TYPE_SETS.CI_ENGINE.has(snType) && PIPELINE_TYPE_SETS.SCANNER.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'scan', reason: 'CI triggers security scanning', priority: 2, protocol: 'CI step' });
      }

      // ── Rule 3: CI/Scanner → Registry (push artifacts)
      if ((PIPELINE_TYPE_SETS.CI_ENGINE.has(snType) || PIPELINE_TYPE_SETS.SCANNER.has(snType)) && PIPELINE_TYPE_SETS.REGISTRY.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'push image', reason: 'Push built/scanned artifacts to registry', priority: 3, protocol: 'OCI/Docker' });
      }
      if (PIPELINE_TYPE_SETS.REGISTRY.has(snType) && PIPELINE_TYPE_SETS.CI_ENGINE.has(exType)) {
        suggestions.push({ from: existing, to: sn, label: 'push image', reason: 'Push built artifacts to registry', priority: 3, protocol: 'OCI/Docker' });
      }

      // ── Rule 4: Registry → K8s deploy (deploy from registry)
      if (PIPELINE_TYPE_SETS.REGISTRY.has(snType) && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'deploy', reason: 'Deploy images from registry to cluster', priority: 4, protocol: 'kubectl/ArgoCD' });
      }
      if (PIPELINE_TYPE_SETS.K8S.has(snType) && PIPELINE_TYPE_SETS.REGISTRY.has(exType)) {
        suggestions.push({ from: existing, to: sn, label: 'pull image', reason: 'Cluster pulls images from registry', priority: 4, protocol: 'OCI pull' });
      }

      // ── Rule 5: Signer → Registry (sign artifacts in registry)
      if (PIPELINE_TYPE_SETS.SIGNER.has(snType) && PIPELINE_TYPE_SETS.REGISTRY.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'sign', reason: 'Sign artifacts in registry', priority: 3, protocol: 'Cosign/Notation' });
      }

      // ── Rule 6: Policy engine → K8s (admission control)
      if (PIPELINE_TYPE_SETS.POLICY.has(snType) && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'admission', reason: 'Policy admission control on cluster', priority: 4, protocol: 'webhook' });
      }

      // ── Rule 7: Monitor → K8s (observe cluster)
      if (PIPELINE_TYPE_SETS.MONITOR.has(snType) && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: existing, to: sn, label: 'metrics/logs', reason: 'Monitor cluster health and security', priority: 5, protocol: 'Prometheus/syslog' });
      }
      if (PIPELINE_TYPE_SETS.K8S.has(snType) && PIPELINE_TYPE_SETS.MONITOR.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'metrics/logs', reason: 'Cluster sends telemetry to monitoring', priority: 5, protocol: 'Prometheus/syslog' });
      }

      // ── Rule 8: Air-gap infra → scanner (offline DB feeds scanner)
      if (snType === 'vuln-db-mirror' && PIPELINE_TYPE_SETS.SCANNER.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'offline DB', reason: 'Offline vulnerability database for air-gapped scanning', priority: 2, protocol: 'file' });
      }
      if (snType === 'package-mirror' && PIPELINE_TYPE_SETS.CI_ENGINE.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'dependencies', reason: 'Package mirror provides dependencies in air-gap', priority: 2, protocol: 'HTTP' });
      }

      // ── Rule 9: Monitor → SLO (metrics feed SLOs)
      if (PIPELINE_TYPE_SETS.MONITOR.has(snType) && PIPELINE_TYPE_SETS.SRE_SLO.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'SLI metrics', reason: 'Monitoring feeds SLI metrics into SLO tracking', priority: 3, protocol: 'Prometheus/OTel' });
      }
      if (PIPELINE_TYPE_SETS.SRE_SLO.has(snType) && PIPELINE_TYPE_SETS.MONITOR.has(exType)) {
        suggestions.push({ from: existing, to: sn, label: 'SLI metrics', reason: 'Monitoring feeds SLI metrics into SLO tracking', priority: 3, protocol: 'Prometheus/OTel' });
      }

      // ── Rule 10: SLO → Incident (error budget breach triggers incident)
      if (PIPELINE_TYPE_SETS.SRE_SLO.has(snType) && PIPELINE_TYPE_SETS.SRE_INCIDENT.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'budget breach', reason: 'Error budget exhaustion triggers incident creation', priority: 3, protocol: 'webhook/alert' });
      }

      // ── Rule 11: Incident → Runbook (incident triggers automated response)
      if (PIPELINE_TYPE_SETS.SRE_INCIDENT.has(snType) && PIPELINE_TYPE_SETS.SRE_RUNBOOK.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'auto-respond', reason: 'Incident triggers automated runbook execution', priority: 3, protocol: 'webhook' });
      }

      // ── Rule 12: Chaos → K8s (chaos experiments target cluster)
      if (PIPELINE_TYPE_SETS.SRE_CHAOS.has(snType) && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'fault inject', reason: 'Chaos experiment injects faults into cluster', priority: 4, protocol: 'K8s API' });
      }

      // ── Rule 13: K8s → SRE resilience (cluster feeds resilience scoring)
      if (PIPELINE_TYPE_SETS.K8S.has(snType) && PIPELINE_TYPE_SETS.SRE_RESILIENCE.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'resilience data', reason: 'Cluster health data feeds resilience scoring', priority: 5, protocol: 'metrics' });
      }

      // ── Rule 14: CI → DORA (pipeline events feed DORA metrics)
      if (PIPELINE_TYPE_SETS.CI_ENGINE.has(snType) && PIPELINE_TYPE_SETS.SRE_DORA.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'pipeline events', reason: 'CI/CD pipeline events feed DORA metric calculation', priority: 4, protocol: 'webhook/API' });
      }

      // ── Rule 15: NDC topology → K8s (infrastructure hosts cluster)
      if (snType === 'ndc-topology' && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'hosts', reason: 'NDC topology provides network infrastructure for K8s cluster', priority: 1, protocol: 'VPC/VNet' });
      }
      if (PIPELINE_TYPE_SETS.K8S.has(snType) && exType === 'ndc-topology') {
        suggestions.push({ from: existing, to: sn, label: 'hosts', reason: 'NDC topology provides network infrastructure for K8s cluster', priority: 1, protocol: 'VPC/VNet' });
      }

      // ── Rule 10: Hybrid connectivity → on-prem or cloud K8s
      if (PIPELINE_TYPE_SETS.HYBRID_CONNECT.has(snType) && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'connectivity', reason: 'Hybrid link provides network path to cluster', priority: 2, protocol: snType.includes('vpn') ? 'IPSec' : 'dedicated circuit' });
      }
      if (PIPELINE_TYPE_SETS.HYBRID_CONNECT.has(snType) && PIPELINE_TYPE_SETS.ONPREM.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'connectivity', reason: 'Hybrid link connects to on-prem infrastructure', priority: 2, protocol: snType.includes('vpn') ? 'IPSec' : 'dedicated circuit' });
      }

      // ── Rule 11: On-prem DC → K8s/registry (on-prem hosts services)
      if (PIPELINE_TYPE_SETS.ONPREM.has(snType) && (PIPELINE_TYPE_SETS.K8S.has(exType) || PIPELINE_TYPE_SETS.REGISTRY.has(exType))) {
        suggestions.push({ from: sn, to: existing, label: 'hosts', reason: 'On-premises infrastructure hosts this service', priority: 3, protocol: 'LAN' });
      }

      // Add encryption warning for cross-classification
      if (violation && !violation.blocked) {
        const last = suggestions[suggestions.length - 1];
        if (last) last.warning = violation.reason;
      }
    });
  });

  // Deduplicate and sort by priority
  const seen = new Set();
  return suggestions
    .filter(s => {
      const key = (s.from.id || s.from.el?.id) + '->' + (s.to.id || s.to.get?.('id'));
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => a.priority - b.priority)
    .slice(0, 12);
}

// ── Integration Guide Overlay ───────────────────────────────────────────────

function _showIntegrationGuide(snippetData, suggestions, isAirgap, snippetClass) {
  const clsBadge = snippetClass === 'SECRET' ? '#e74c3c' : snippetClass === 'CUI' ? '#f39c12' : '#27ae60';
  let html = _section('Snippet: ' + escapeHtml(snippetData.name));
  html += '<div style="margin-bottom:8px;">';
  html += _pill(snippetClass, clsBadge);
  html += _pill(snippetData.impact_level || 'IL4', '#0f3460');
  html += _pill('SLSA ' + (snippetData.slsa_level || 'L0'), '#5b6abf');
  if (isAirgap) html += _pill('AIR-GAPPED', '#c0392b');
  html += '</div>';
  if (snippetData.description) {
    html += '<p style="font-size:11px;color:#7a8cb0;margin:0 0 8px;">' + escapeHtml(snippetData.description) + '</p>';
  }

  // Security warnings
  if (isAirgap) {
    html += '<div style="background:#2b0f0f;border-left:3px solid #e74c3c;padding:6px 10px;border-radius:0 4px 4px 0;margin-bottom:8px;font-size:11px;">';
    html += '<b style="color:#e74c3c;">Air-Gapped Mode</b><br>';
    html += 'Cloud-managed services are excluded from auto-connect. Only on-premises and self-hosted tools are suggested.';
    html += '</div>';
  }

  // Suggestions
  if (suggestions.length) {
    html += _section('Suggested Connections (' + suggestions.length + ')');
    suggestions.forEach((s, idx) => {
      const fromLabel = s.from.label || s.from.attr?.('label/text') || '?';
      const toLabel = s.to.label || s.to.attr?.('label/text') || '?';
      const fromId = s.from.id || s.from.el?.id;
      const toId = s.to.id || s.to.get?.('id');

      html += '<div style="background:#0f2040;border:1px solid #1e3a6e;border-radius:6px;padding:8px;margin:4px 0;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
      html += '<div><span style="color:#3498db;font-weight:600;">' + escapeHtml(fromLabel) + '</span>';
      html += ' <span style="color:#e94560;">→</span> ';
      html += '<span style="color:#27ae60;font-weight:600;">' + escapeHtml(toLabel) + '</span></div>';
      // data-* + addEventListener: node ids/label never enter a JS string sink.
      html += '<button class="tb-btn integ-connect-btn" style="font-size:10px;padding:2px 8px;" data-from="' + escapeAttr(fromId) + '" data-to="' + escapeAttr(toId) + '" data-label="' + escapeAttr(s.label || '') + '">Connect</button>';
      html += '</div>';
      html += '<div style="font-size:10px;color:#7a8cb0;margin-top:3px;">' + s.reason + '</div>';
      html += '<div style="font-size:9px;color:#5a6e8c;">Protocol: ' + (s.protocol || 'auto') + '</div>';
      if (s.warning) {
        html += '<div style="font-size:9px;color:#f39c12;margin-top:2px;">⚠ ' + s.warning + '</div>';
      }
      html += '</div>';
    });
    html += '<button class="tb-btn" style="margin-top:8px;width:100%;text-align:center;" onclick="applyAllSuggestions()">Connect All Suggested</button>';
  } else {
    html += '<p style="font-size:11px;color:#7a8cb0;">No automatic connections suggested. Drag links manually to connect nodes.</p>';
  }

  openRightPanel('Integration Guide', html);
  // Wire Connect buttons via listeners (data-* args, no inline JS-string sink).
  document.querySelectorAll('.pc-config-body .integ-connect-btn').forEach(btn => {
    btn.addEventListener('click', e => applyIntegrationLink(
      btn.dataset.from, btn.dataset.to, btn.dataset.label, e.currentTarget));
  });
  // Store suggestions for "Connect All"
  _pendingSuggestions = suggestions;
}

function applyIntegrationLink(fromId, toId, label, el) {
  pushUndo();
  createLink(fromId, toId, label);
  markDirty();
  // Visual feedback: flash the button
  const btn = el || (typeof event !== 'undefined' ? event?.target : null);
  if (btn) { if (btn.style) btn.style.background = '#27ae60'; btn.textContent = '✓'; }
}

function applyAllSuggestions() {
  const suggestions = _pendingSuggestions || [];
  if (!suggestions.length) return;
  pushUndo();
  suggestions.forEach(s => {
    const fromId = s.from.id || s.from.el?.id;
    const toId = s.to.id || s.to.get?.('id');
    createLink(fromId, toId, s.label || '');
  });
  markDirty();
  updateStatus('Applied ' + suggestions.length + ' connections');
  _pendingSuggestions = [];
}
