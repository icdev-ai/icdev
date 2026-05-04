/* CUI // SP-CTI */
/* Quality Design Canvas — QDC-specific JavaScript */
/* Extends design-canvas.js with UQS scoring, SA-11 gates, runbook execution */

(function () {
  'use strict';

  var API = window.API_BASE || '/quality/api';
  var DESIGN_ID = window.DESIGN_ID || '';

  /* ── Right Panel helpers ─────────────────────────────────────────────── */

  function openRightPanel(title) {
    var panel = document.getElementById('dc-right-panel');
    if (panel) { panel.classList.add('open'); panel.style.display = 'block'; }
    var t = document.getElementById('dc-panel-title');
    if (t) t.textContent = title || 'Results';
  }

  function closeRightPanel() {
    var panel = document.getElementById('dc-right-panel');
    if (panel) { panel.classList.remove('open'); panel.style.display = 'none'; }
  }

  function setPanelBody(html) {
    var b = document.getElementById('dc-panel-body');
    if (b) b.innerHTML = html;
  }

  /* ── Grade helpers ───────────────────────────────────────────────────── */

  function gradeColor(score) {
    if (score >= 90) return '#27ae60';
    if (score >= 80) return '#2196f3';
    if (score >= 70) return '#f39c12';
    if (score >= 60) return '#e67e22';
    return '#e94560';
  }

  function gradeLabel(score) {
    if (score >= 90) return 'A';
    if (score >= 80) return 'B';
    if (score >= 70) return 'C';
    if (score >= 60) return 'D';
    return 'F';
  }

  function severityColor(sev) {
    if (sev === 'CAT1') return '#e94560';
    if (sev === 'CAT2') return '#f39c12';
    return '#7a8cb0';
  }

  /* ── Assess ──────────────────────────────────────────────────────────── */

  window.canvasAssess = function () {
    if (!DESIGN_ID) return;
    openRightPanel('Assessing...');
    setPanelBody('<div style="color:#7a8cb0;padding:20px;text-align:center;">Running quality assessment...</div>');

    fetch(API + '/designs/' + DESIGN_ID + '/assess', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var score = d.score || 0;
        var uqs = d.uqs_score || 0;
        var findings = d.findings || [];
        var summary = d.summary || {};

        var html = '<div style="text-align:center;margin-bottom:16px;">';
        html += '<div style="font-size:36px;font-weight:700;color:' + gradeColor(uqs) + ';">' + uqs.toFixed(1) + '</div>';
        html += '<div style="font-size:14px;color:#7a8cb0;">Unified Quality Score</div>';
        html += '<span style="background:' + gradeColor(uqs) + ';color:#fff;padding:2px 10px;border-radius:4px;font-size:13px;font-weight:700;">Grade ' + gradeLabel(uqs) + '</span>';
        html += '</div>';

        html += '<div style="border-top:1px solid #1e3a6e;padding-top:10px;margin-top:10px;">';
        html += '<div style="font-size:12px;color:#eaeaea;font-weight:600;margin-bottom:8px;">Compliance Score: ' + score.toFixed(1) + '%</div>';
        var bySev = summary.by_severity || {};
        html += '<div style="display:flex;gap:12px;margin-bottom:12px;">';
        html += '<span style="color:#e94560;font-size:11px;">CAT1: ' + (bySev.CAT1 || 0) + '</span>';
        html += '<span style="color:#f39c12;font-size:11px;">CAT2: ' + (bySev.CAT2 || 0) + '</span>';
        html += '<span style="color:#7a8cb0;font-size:11px;">CAT3: ' + (bySev.CAT3 || 0) + '</span>';
        html += '</div></div>';

        if (findings.length > 0) {
          html += '<div style="font-size:12px;color:#eaeaea;font-weight:600;margin:8px 0;">Findings (' + findings.length + ')</div>';
          findings.forEach(function (f) {
            html += '<div style="padding:6px 0;border-top:1px solid rgba(255,255,255,0.05);font-size:11px;">';
            html += '<span style="color:' + severityColor(f.severity) + ';font-weight:600;">' + f.severity + '</span> ';
            html += '<span style="color:#eaeaea;">' + f.title + '</span>';
            if (f.detail) html += '<div style="color:#5a6e8c;font-size:10px;margin-top:2px;">' + f.detail + '</div>';
            html += '</div>';
          });
        } else {
          html += '<div style="color:#27ae60;font-size:12px;padding:8px 0;">All quality checks passed.</div>';
        }

        openRightPanel('Assessment Results');
        setPanelBody(html);
      })
      .catch(function (e) {
        setPanelBody('<div style="color:#e94560;padding:12px;">Assessment failed: ' + e + '</div>');
      });
  };

  /* ── Gaps ─────────────────────────────────────────────────────────────── */

  window.canvasGaps = function () {
    if (!DESIGN_ID) return;
    window.location.href = '/quality/remediation/' + DESIGN_ID;
  };

  /* ── UQS ─────────────────────────────────────────────────────────────── */

  window.showUQS = function () {
    if (!DESIGN_ID) return;
    openRightPanel('Loading UQS...');

    fetch(API + '/designs/' + DESIGN_ID + '/uqs')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var uqs = d.uqs_score || 0;
        var dims = d.dimensions || {};
        var html = '<div style="text-align:center;margin-bottom:16px;">';
        html += '<div style="font-size:42px;font-weight:700;color:' + gradeColor(uqs) + ';">' + uqs.toFixed(1) + '</div>';
        html += '<div style="font-size:12px;color:#7a8cb0;">Unified Quality Score</div></div>';

        html += '<div style="font-size:12px;color:#eaeaea;font-weight:600;margin:12px 0 8px;">Dimension Breakdown</div>';
        var dimNames = { code_quality: 'Code Quality', security_posture: 'Security Posture', test_coverage: 'Test Coverage', runtime_health: 'Runtime Health', compliance_readiness: 'Compliance Readiness' };
        Object.keys(dimNames).forEach(function (k) {
          var v = dims[k] || 0;
          html += '<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:11px;">';
          html += '<span style="color:#7a8cb0;">' + dimNames[k] + '</span>';
          html += '<span style="color:' + gradeColor(v) + ';font-weight:600;">' + v.toFixed(1) + '</span>';
          html += '</div>';
          html += '<div style="background:#0a1628;border-radius:2px;height:4px;margin-bottom:6px;">';
          html += '<div style="background:' + gradeColor(v) + ';height:4px;border-radius:2px;width:' + Math.min(v, 100) + '%;"></div></div>';
        });

        openRightPanel('UQS Breakdown');
        setPanelBody(html);
      })
      .catch(function (e) {
        setPanelBody('<div style="color:#e94560;">Failed to load UQS: ' + e + '</div>');
      });
  };

  /* ── Gates ────────────────────────────────────────────────────────────── */

  window.showGates = function () {
    if (!DESIGN_ID) return;
    openRightPanel('Loading Gates...');

    fetch(API + '/designs/' + DESIGN_ID + '/gates')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var gates = d.gates || [];
        var html = '<div style="font-size:12px;color:#eaeaea;font-weight:600;margin-bottom:8px;">SA-11 Gate Status (' + gates.length + ')</div>';

        if (gates.length === 0) {
          html += '<div style="color:#5a6e8c;font-size:11px;">No gate results. Run an assessment first.</div>';
        } else {
          html += '<table style="width:100%;border-collapse:collapse;font-size:11px;">';
          html += '<tr style="color:#7a8cb0;"><th style="text-align:left;padding:4px;">Gate</th><th style="text-align:center;padding:4px;">Control</th><th style="text-align:center;padding:4px;">Status</th></tr>';
          gates.forEach(function (g) {
            var statusIcon = g.status === 'pass' ? '✓' : g.status === 'fail' ? '✗' : '—';
            var statusColor = g.status === 'pass' ? '#27ae60' : g.status === 'fail' ? '#e94560' : '#5a6e8c';
            html += '<tr style="border-top:1px solid rgba(255,255,255,0.05);">';
            html += '<td style="padding:4px;color:#eaeaea;">' + g.gate_id + '</td>';
            html += '<td style="padding:4px;text-align:center;color:#2196f3;font-family:monospace;">' + (g.sa11_control || '—') + '</td>';
            html += '<td style="padding:4px;text-align:center;color:' + statusColor + ';font-weight:700;">' + statusIcon + '</td>';
            html += '</tr>';
          });
          html += '</table>';
        }

        openRightPanel('SA-11 Gate Mapping');
        setPanelBody(html);
      })
      .catch(function (e) {
        setPanelBody('<div style="color:#e94560;">Failed to load gates: ' + e + '</div>');
      });
  };

  /* ── Execute Runbook ──────────────────────────────────────────────────── */

  window.executeRunbook = function (id) {
    openRightPanel('Executing Runbook...');
    setPanelBody('<div style="color:#7a8cb0;padding:20px;text-align:center;">Running...</div>');

    fetch(API + '/runbooks/' + id + '/execute', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var html = '<div style="font-size:12px;color:#27ae60;margin-bottom:8px;">Runbook executed</div>';
        html += '<pre style="color:#7a8cb0;font-size:11px;white-space:pre-wrap;">' + JSON.stringify(d, null, 2) + '</pre>';
        setPanelBody(html);
      })
      .catch(function (e) {
        setPanelBody('<div style="color:#e94560;">Runbook failed: ' + e + '</div>');
      });
  };

  /* ── Tab switching ───────────────────────────────────────────────────── */

  window.switchTab = function (tabId, el) {
    var tabs = document.querySelectorAll('.dc-tab');
    var contents = document.querySelectorAll('.dc-tab-content');
    tabs.forEach(function (t) {
      t.classList.remove('active');
      t.style.color = '#7a8cb0';
      t.style.borderBottomColor = 'transparent';
    });
    contents.forEach(function (c) { c.style.display = 'none'; });
    if (el) {
      el.classList.add('active');
      el.style.color = '#2196f3';
      el.style.borderBottomColor = '#2196f3';
    }
    var target = document.getElementById(tabId);
    if (target) target.style.display = 'block';
  };

  /* ── Export toggle ───────────────────────────────────────────────────── */

  window.toggleExportMenu = function () {
    var d = document.getElementById('export-dropdown');
    if (d) d.style.display = d.style.display === 'none' ? 'block' : 'none';
  };

  window.canvasExport = function (fmt) {
    if (!DESIGN_ID) return;
    fetch(API + '/export/' + DESIGN_ID + '/' + fmt, { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (d) { alert('Export complete: ' + fmt); })
      .catch(function (e) { alert('Export failed: ' + e); });
    var dd = document.getElementById('export-dropdown');
    if (dd) dd.style.display = 'none';
  };

  /* ── Expose closeRightPanel globally ────────────────────────────────── */
  window.closeRightPanel = closeRightPanel;

})();
