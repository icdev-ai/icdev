/* CUI // SP-CTI — Pipeline Design Canvas: IaC Validate / Fix / Deploy / Export
 * Validate generated IaC (layers 1-3), apply suggested fixes, generate + download
 * the deployment bundle, and export the canvas to CI configs / diagrams / PDF.
 * Split from the pipeline-canvas.js monolith (pdx-ux-01).
 *
 * Depends (at runtime) on pipeline-canvas-core.js (pipelineId, updateStatus,
 * escapeHtml, openRightPanel) and pipeline-analysis.js (_section, _bar). Classic
 * script — declarations stay top-level.
 */

'use strict';

// Result of the most recent validate/auto-fix, consumed by autoFixAllWarnings.
// Module-scoped top-level (pdx-ux-01: was window._lastValidationData; writer and
// reader both live in this file so no global is needed).
let _lastValidationData = null;

// ── Validate IaC ─────────────────────────────────────────────────────────────

function validateIaC() {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  updateStatus('Validating IaC (Layers 1-3)...');
  fetch(`/devops/api/validate/${pipelineId}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_layer: 3 }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) { alert(data.error); return; }
      _lastValidationData = data;
      _renderValidation(data);
      updateStatus('Validation: ' + (data.validation || {}).gate);
    })
    .catch(err => { updateStatus('Validation failed'); alert(err); });
}

function _renderValidation(data) {
  const v = data.validation || {};
  const s = v.summary || {};
  let html = _section('IaC Validation Results');
  const gateColor = v.gate === 'pass' ? '#1e8449' : '#c0392b';
  html += `<div style="text-align:center;margin:8px 0;">
    <span style="font-size:28px;font-weight:800;color:${gateColor};">${(v.gate || '?').toUpperCase()}</span>
    <div style="font-size:11px;color:#4a5568;">Gate: ${s.passed||0} pass, ${s.failed||0} fail, ${s.warned||0} warn</div>
  </div>`;
  html += _bar(s.total > 0 ? (s.passed / s.total * 100) : 0, gateColor);

  // Auto-fix banner
  const fixable = (v.results || []).filter(r => (r.status === 'warn' || r.status === 'fail') && r.fix_hint);
  if (fixable.length > 0) {
    html += `<div style="margin:8px 0;padding:7px 10px;background:#fef9e7;border:1px solid #f39c12;border-radius:6px;display:flex;align-items:center;justify-content:space-between;">
      <span style="font-size:11px;font-weight:600;color:#7d6608;">⚠ ${fixable.length} issue(s) have suggested fix${fixable.length > 1 ? 'es' : ''}</span>
      <button class="tb-btn" style="background:#d5f0e0;color:#1e7e34;border-color:#1e7e34;font-size:11px;padding:2px 8px;font-weight:600;" onclick="autoFixAllWarnings()">✦ Auto-fix All</button>
    </div>`;
  }

  const layers = {1: 'Syntax', 2: 'Schema', 3: 'Policy', 4: 'Plan', 5: 'Deploy Test'};
  for (let layer = 1; layer <= (v.layers_run || 3); layer++) {
    const layerResults = (v.results || []).filter(r => r.layer === layer);
    if (!layerResults.length) continue;
    html += _section('Layer ' + layer + ': ' + (layers[layer] || ''));
    layerResults.forEach((r, idx) => {
      const icons  = {pass:'✓', fail:'✗', warn:'⚠', skip:'→'};
      const colors = {pass:'#1e8449', fail:'#c0392b', warn:'#b7770d', skip:'#4a5568'};
      const bgs    = {pass:'#f0fff4', fail:'#fff5f5', warn:'#fffbf0', skip:'#f8f8f8'};
      // A11y: severity is conveyed by an explicit text label + icon, never by
      // colour alone (pdx-ux-01). Screen readers and colour-blind users get the
      // word "FAIL"/"WARN"/"PASS"/"SKIP" alongside the ✗/⚠/✓/→ glyph.
      const statusLabels = {pass:'PASS', fail:'FAIL', warn:'WARN', skip:'SKIP'};
      const rid = `vr-${layer}-${idx}`;
      const needsAction = r.status === 'warn' || r.status === 'fail';

      html += `<div id="${rid}" style="margin:3px 0;padding:6px 8px;border-left:3px solid ${colors[r.status]};background:${bgs[r.status]};border-radius:0 4px 4px 0;">`;
      html += `<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:4px;">`;
      html += `<div style="flex:1;min-width:0;">
        <span style="color:${colors[r.status]};font-size:13px;margin-right:4px;">${icons[r.status]}</span>
        <span style="font-size:9px;font-weight:700;letter-spacing:.5px;color:${colors[r.status]};margin-right:4px;">${statusLabels[r.status] || (r.status || '').toUpperCase()}</span>
        <b style="font-size:11px;color:#1a1a2e;">${escapeHtml(r.check)}</b>`;
      if (r.file) html += ` <span style="color:#4a5568;font-size:10px;">(${escapeHtml(r.file)})</span>`;
      html += `<div style="font-size:10px;color:#4a5568;margin-top:2px;">${escapeHtml(r.message)}</div>`;
      if (r.details && r.details.length) {
        r.details.forEach(d => { html += `<div style="font-size:9px;color:#4a5568;margin-left:12px;">— ${escapeHtml(d)}</div>`; });
      }
      html += `</div>`;
      if (needsAction) {
        html += `<div style="display:flex;flex-direction:column;gap:3px;flex-shrink:0;">`;
        if (r.fix_hint) html += `<button class="tb-btn" style="font-size:10px;padding:2px 7px;background:#dce8fb;color:#1a5276;border-color:#aed6f1;" onclick="_toggleFix('${rid}')">Fix →</button>`;
        html += `<button class="tb-btn" style="font-size:10px;padding:2px 7px;color:#4a5568;" onclick="_dismissItem('${rid}')">Dismiss</button>`;
        html += `</div>`;
      }
      html += `</div>`;

      // Fix card (hidden by default)
      if (r.fix_hint) {
        const safeSnippet = (r.fix_snippet || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        html += `<div id="${rid}-fix" style="display:none;margin-top:6px;padding:8px;background:#eaf3fb;border-radius:4px;border:1px solid #aed6f1;">`;
        html += `<div style="font-size:10px;font-weight:700;color:#1a5276;margin-bottom:4px;">💡 Suggested Fix</div>`;
        html += `<div style="font-size:10px;color:#2c3e50;margin-bottom:6px;">${escapeHtml(r.fix_hint)}</div>`;
        if (r.fix_snippet) {
          html += `<pre style="font-size:9px;background:#d6eaf8;padding:6px;border-radius:3px;white-space:pre-wrap;color:#1a1a2e;margin:0 0 6px;font-family:var(--pc-mono);">${safeSnippet}</pre>`;
          html += `<button class="tb-btn" style="font-size:10px;padding:2px 8px;background:#1a5276;color:#fff;" onclick="_copyFix(this,'${rid}')">Copy Snippet</button>`;
        }
        html += `</div>`;
      }
      html += `</div>`;
    });
  }
  html += `<p style="font-size:10px;color:#4a5568;margin-top:8px;">Layers 1-3 run offline (air-gap safe). Layer 4+ requires terraform binary or cloud credentials.</p>`;
  openRightPanel('IaC Validation', html);
}

window._toggleFix = function(rid) {
  const el = document.getElementById(rid + '-fix');
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
};

window._dismissItem = function(rid) {
  const el = document.getElementById(rid);
  if (el) { el.style.opacity = '0.4'; el.style.pointerEvents = 'none'; }
};

window._copyFix = function(btn, rid) {
  const fixDiv = document.getElementById(rid + '-fix');
  if (!fixDiv) return;
  const pre = fixDiv.querySelector('pre');
  if (!pre) return;
  navigator.clipboard.writeText(pre.textContent).then(() => {
    const orig = btn.textContent;
    btn.textContent = '✓ Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  }).catch(() => {
    // Fallback for browsers that block clipboard
    const ta = document.createElement('textarea');
    ta.value = pre.textContent;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    btn.textContent = '✓ Copied!';
    setTimeout(() => { btn.textContent = 'Copy Snippet'; }, 1500);
  });
};

window.autoFixAllWarnings = function() {
  const data = _lastValidationData;
  if (!data) return;
  const v = data.validation || {};
  const fixable = (v.results || []).filter(r => (r.status === 'warn' || r.status === 'fail') && r.fix_hint);
  if (!fixable.length) return;
  updateStatus(`Applying ${fixable.length} fix(es)...`);
  fetch(`/devops/api/validate/${pipelineId}/fix`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fixes: fixable.map(r => ({ check: r.check, file: r.file, fix_action: r.fix_action || r.check })) }),
  })
    .then(r => r.json())
    .then(result => {
      if (result.error) { alert(result.error); return; }
      const n = result.fixed || 0;
      updateStatus(`Applied ${n} fix(es) — re-validating...`);
      _lastValidationData = result;
      _renderValidation(result);
      updateStatus('Validation: ' + (result.validation || {}).gate);
    })
    .catch(err => { updateStatus('Auto-fix failed'); console.error(err); });
};

// ── Deploy IaC Bundle ────────────────────────────────────────────────────────

function deployBundle() {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  updateStatus('Generating deployment bundle...');
  fetch(`/devops/api/deploy/${pipelineId}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_csp: 'auto' }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) { alert(data.error); return; }
      // Show summary in right panel
      let html = _section('Deployment Summary');
      html += '<pre style="font-size:10px;white-space:pre-wrap;color:#eaeaea;background:#0f2040;padding:8px;border-radius:4px;">' + (data.summary || '') + '</pre>';
      html += _section('Generated Files (' + (data.files || []).length + ')');
      (data.files || []).forEach(f => {
        html += '<div style="font-size:10px;color:#7a8cb0;padding:1px 0;">' + f + '</div>';
      });
      html += _section('Download');
      html += '<button class="tb-btn" style="background:#27ae60;color:#fff;padding:6px 16px;margin:8px 0;" onclick="downloadBundle()">Download Bundle (.zip)</button>';
      html += '<p style="font-size:10px;color:#5a6e8c;margin-top:4px;">Contains Terraform, Helm values, Ansible playbooks, CI config, and deploy.sh</p>';
      openRightPanel('Deploy IaC Bundle', html);
      updateStatus('Bundle generated');
      // downloadBundle() re-fetches the zip on demand, so no client-side bundle
      // cache is kept (pdx-ux-01: removed the dead window._lastDeployBundle).
    })
    .catch(err => { updateStatus('Deploy generation failed'); alert(err); });
}

function downloadBundle() {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  updateStatus('Downloading zip...');
  fetch(`/devops/api/deploy/${pipelineId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_csp: 'auto', format: 'zip' }),
  })
    .then(r => {
      if (!r.ok) throw new Error('Download failed');
      return r.blob();
    })
    .then(blob => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'devops-deploy-bundle.zip';
      a.click();
      URL.revokeObjectURL(a.href);
      updateStatus('Bundle downloaded');
    })
    .catch(err => { updateStatus('Download failed'); alert(err); });
}

// ── Export ───────────────────────────────────────────────────────────────────

function exportAs(fmt) {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  fetch(`/devops/api/export/${pipelineId}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ format: fmt }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) { alert(data.error); return; }
      const blob = new Blob([data.content], { type: 'text/plain' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = data.filename;
      a.click();
      URL.revokeObjectURL(a.href); // release the blob URL after the download starts
      updateStatus(`Exported: ${data.filename}`);
    });
}

// exportCanvasPDF — the toolbar "Export PDF" button previously called a function
// that only existed in network-canvas.js (never loaded here), always throwing a
// ReferenceError. Provide the same trivially-correct, honest implementation the
// NDC uses: open the browser print dialog (Print → Save as PDF).
function exportCanvasPDF() {
  window.print();
}
