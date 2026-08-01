/* CUI // SP-CTI — Pipeline Design Canvas: Analysis + Renderers
 * runAnalysis / runScorecard dispatch and the right-panel renderers (OWASP,
 * SLSA, compliance, cost, execution time, anti-patterns, governance, scorecard),
 * plus the shared _pill/_bar/_metric/_section presentation helpers used across
 * every pipeline-* module. Split from the pipeline-canvas.js monolith (pdx-ux-01).
 *
 * The four presentation helpers are plain function declarations (global), so the
 * ndc-bridge / snippets / iac modules loaded before this file still resolve them
 * at call time. Depends on pipeline-canvas-core.js (pipelineId, getStyle,
 * updateStatus, fetchJson, pdcToast, openRightPanel) and pipeline-node-styles.js
 * (STAGE_LABELS, STAGE_COLORS). Classic script — declarations stay top-level.
 */

'use strict';

// ── Shared presentation helpers ─────────────────────────────────────────────
// Used by this file's renderers AND by pipeline-ndc-bridge.js (fetchNdcHealth),
// pipeline-iac.js (_renderValidation, deployBundle), and pipeline-snippets.js
// (_showIntegrationGuide). Function declarations → reachable regardless of the
// order these classic scripts load.

function _pill(text, color) {
  return `<span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600;background:${color};color:#fff;margin:1px 2px;">${text}</span>`;
}
function _bar(pct, color) {
  return `<div style="background:#0f2040;border-radius:4px;height:8px;margin:4px 0 8px;overflow:hidden;"><div style="width:${pct}%;height:100%;background:${color};border-radius:4px;"></div></div>`;
}
function _metric(label, value, sub) {
  return `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><span style="color:#7a8cb0;">${label}</span><span style="font-weight:600;font-family:var(--pc-mono);">${value}</span></div>` + (sub ? `<div style="font-size:10px;color:#5a6e8c;padding:0 0 4px;">${sub}</div>` : '');
}
function _section(title) {
  return `<div style="font-weight:700;font-size:12px;margin:12px 0 6px;padding-bottom:4px;border-bottom:1px solid #1e3a6e;color:#eaeaea;">${title}</div>`;
}

// ── Analysis (renders in right panel with narrative) ────────────────────────

function runAnalysis(type) {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  updateStatus('Analyzing...');
  fetchJson(`/devops/api/pipelines/${pipelineId}/analyze`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ analysis_type: type }),
  })
    .then(data => {
      const r = (data && data.result) || {};
      let html = '';
      if (type === 'security_coverage') html = _renderOWASP(r);
      else if (type === 'slsa') html = _renderSLSA(r);
      else if (type === 'compliance') html = _renderCompliance(r);
      else if (type === 'cost') html = _renderCost(r);
      else if (type === 'execution_time') html = _renderExecTime(r);
      else if (type === 'antipatterns') html = _renderAntipatterns(r);
      else if (type === 'governance') html = _renderPDCGovernance(r);
      else html = '<pre style="font-size:11px;white-space:pre-wrap;">' + JSON.stringify(r, null, 2) + '</pre>';
      const titles = {
        security_coverage: 'OWASP Top 10 Coverage',
        slsa: 'SLSA Level Assessment',
        compliance: 'Compliance Audit',
        cost: 'Cost Estimate',
        execution_time: 'Execution Time',
        antipatterns: 'Anti-Pattern Detection',
        governance: 'Pipeline Governance',
      };
      openRightPanel(titles[type] || type, html);
      updateStatus('Analysis complete');
    })
    .catch(err => {
      // fetchJson rejects on !r.ok or a body-level `error` — never claim
      // "Analysis complete" when the backend reported a failure.
      updateStatus('Analysis failed');
      pdcToast('Analysis failed: ' + (err && err.message ? err.message : 'unknown error'), 'error');
    });
}

function runScorecard() {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  updateStatus('Running scorecard...');
  fetch(`/devops/api/pipelines/${pipelineId}/scorecard`)
    .then(r => r.json())
    .then(data => {
      openRightPanel('Pipeline Scorecard', _renderScorecard(data));
      updateStatus('Scorecard complete');
    })
    .catch(() => updateStatus('Scorecard failed'));
}

// ── Analysis Renderers ──────────────────────────────────────────────────────

function _renderOWASP(r) {
  const pct = r.coverage_pct || 0;
  const color = pct >= 80 ? '#27ae60' : pct >= 50 ? '#f39c12' : '#e74c3c';
  let html = _metric('Coverage', pct + '%', `${r.covered_count || 0} of ${r.total_categories || 10} OWASP categories covered`);
  html += _bar(pct, color);

  // Narrative
  if (pct >= 90) html += '<p style="font-size:11px;color:#27ae60;">Excellent coverage. Your pipeline addresses nearly all OWASP Top 10 categories. Consider periodic pen testing to validate runtime behavior.</p>';
  else if (pct >= 70) html += '<p style="font-size:11px;color:#f39c12;">Good coverage with gaps. Review the uncovered categories below and consider adding targeted scanners.</p>';
  else if (pct >= 40) html += '<p style="font-size:11px;color:#e74c3c;">Moderate coverage. Significant gaps exist. Your pipeline is vulnerable to multiple OWASP Top 10 attack classes.</p>';
  else html += '<p style="font-size:11px;color:#e74c3c;">Critical: Minimal security scanning. Most OWASP Top 10 categories are unaddressed. Add SAST, SCA, and DAST scanners immediately.</p>';

  html += _section('Covered Categories');
  (r.covered || []).forEach(c => { html += _pill(c, '#27ae60'); });

  if ((r.uncovered || []).length) {
    html += _section('Uncovered (Gaps)');
    (r.uncovered || []).forEach(c => { html += _pill(c, '#e74c3c'); });
    html += '<div style="margin-top:8px;font-size:11px;color:#7a8cb0;">';
    const gapAdvice = {
      'A01-Broken-Access': 'Add DAST scanner (OWASP ZAP) to detect access control flaws at runtime.',
      'A02-Crypto-Failures': 'Add secret detection (Gitleaks, TruffleHog) to catch exposed credentials.',
      'A03-Injection': 'Add SAST scanner (Semgrep, SonarQube) for injection detection.',
      'A04-Insecure-Design': 'Add SAST with design pattern rules (CodeQL, SonarQube).',
      'A05-Misconfig': 'Add IaC scanner (Checkov, tfsec) for infrastructure misconfiguration.',
      'A06-Vuln-Components': 'Add SCA scanner (Trivy, Grype, Snyk) for dependency vulnerabilities.',
      'A07-XSS': 'Add SAST + DAST scanners for cross-site scripting detection.',
      'A08-Deserialization': 'Add SAST scanner with deserialization rules.',
      'A09-Logging-Monitoring': 'Add runtime security (Falco, Wazuh) for logging/monitoring gaps.',
      'A10-SSRF': 'Add DAST scanner and runtime monitoring for SSRF detection.',
    };
    (r.uncovered || []).forEach(c => {
      if (gapAdvice[c]) html += '<div style="margin:4px 0;"><b style="color:#e94560;">' + c + ':</b> ' + gapAdvice[c] + '</div>';
    });
    html += '</div>';
  }

  // Scanner detail
  if (r.scanner_coverage && Object.keys(r.scanner_coverage).length) {
    html += _section('Scanner Contribution');
    for (const [scanner, cats] of Object.entries(r.scanner_coverage)) {
      const style = getStyle(scanner);
      html += `<div style="margin:3px 0;"><span style="color:${style.stroke};font-weight:600;">${style.label}</span>: ${cats.map(c => _pill(c, '#0f3460')).join('')}</div>`;
    }
  }
  return html;
}

function _renderSLSA(r) {
  const level = r.achieved_level || 0;
  const colors = ['#e74c3c', '#f39c12', '#f1c40f', '#27ae60', '#16a085'];
  const color = colors[Math.min(level, 4)];
  let html = `<div style="text-align:center;margin:8px 0;"><span style="font-size:36px;font-weight:800;color:${color};">L${level}</span></div>`;
  html += _bar(level * 25, color);

  const descs = [
    'No supply chain guarantees. Artifacts have no provenance or signing.',
    'Build process is documented. Provenance exists but is not tamper-resistant.',
    'Tamper-resistant build service. Source is version-controlled, build is authenticated.',
    'Hardened builds. Build-as-code, ephemeral environments, isolated execution.',
    'Highest assurance. Hermetic and reproducible builds with full attestation chain.',
  ];
  html += `<p style="font-size:11px;color:#7a8cb0;margin:8px 0;">${descs[level]}</p>`;

  html += _section('Evidence Checklist');
  const evidence = r.evidence || {};
  const evidenceLabels = {
    build_process_documented: 'Build process documented',
    version_controlled_source: 'Source in version control',
    build_service_authenticated: 'Authenticated build service',
    build_as_code: 'Build defined as code',
    ephemeral_environment: 'Ephemeral build environment',
    isolated_builds: 'Isolated build execution',
    hermetic_builds: 'Hermetic builds (no network)',
    reproducible_builds: 'Reproducible builds',
  };
  for (const [key, label] of Object.entries(evidenceLabels)) {
    const met = evidence[key] || false;
    html += `<div style="display:flex;align-items:center;gap:6px;padding:2px 0;"><span style="color:${met ? '#27ae60' : '#e74c3c'};font-size:14px;">${met ? '&#10003;' : '&#10007;'}</span><span style="color:${met ? '#eaeaea' : '#7a8cb0'};">${label}</span></div>`;
  }

  html += _section('Attestation');
  html += _metric('Provenance', r.has_provenance ? 'Yes' : 'Missing', r.has_provenance ? 'SLSA provenance attestation found in pipeline' : 'Add SLSA Generator or in-toto node');
  html += _metric('Signing', r.has_signing ? 'Yes' : 'Missing', r.has_signing ? 'Artifact signing configured' : 'Add Cosign or Notation node');

  if (level < 3) {
    html += _section('Recommendations to Reach L' + (level + 1));
    if (level === 0) html += '<p style="font-size:11px;">Add a CI/CD engine (GitLab CI, Tekton) and an SLSA provenance generator.</p>';
    else if (level === 1) html += '<p style="font-size:11px;">Use an authenticated build service and add artifact signing (Cosign).</p>';
    else if (level === 2) html += '<p style="font-size:11px;">Switch to Tekton or Bazel for hermetic, isolated builds with ephemeral runners.</p>';
  }
  return html;
}

function _renderCompliance(r) {
  const total = (r.passed || 0) + (r.failed || 0);
  const pct = total > 0 ? Math.round(r.passed / total * 100) : 0;
  const color = pct >= 80 ? '#27ae60' : pct >= 50 ? '#f39c12' : '#e74c3c';
  let html = _metric('Score', pct + '%', `${r.passed || 0} passed, ${r.failed || 0} failed of ${total} rules`);
  html += _bar(pct, color);

  if (pct >= 90) html += '<p style="font-size:11px;color:#27ae60;">Strong compliance posture. Pipeline meets most DevSecOps framework requirements.</p>';
  else if (pct >= 60) html += '<p style="font-size:11px;color:#f39c12;">Moderate compliance. Several controls are missing. Review findings below.</p>';
  else html += '<p style="font-size:11px;color:#e74c3c;">Significant compliance gaps. Pipeline does not meet baseline DevSecOps requirements. Remediation required before ATO.</p>';

  if ((r.findings || []).length) {
    html += _section('Findings (' + r.findings.length + ')');
    // Group by severity
    const cat1 = r.findings.filter(f => f.severity === 'CAT1');
    const cat2 = r.findings.filter(f => f.severity === 'CAT2');

    if (cat1.length) {
      html += `<div style="color:#e74c3c;font-weight:700;margin:6px 0 2px;">CAT I — Critical (${cat1.length})</div>`;
      cat1.forEach(f => {
        html += `<div style="margin:4px 0;padding:6px 8px;background:#2b0f0f;border-left:3px solid #e74c3c;border-radius:0 4px 4px 0;">`;
        html += `<div style="font-weight:600;font-size:11px;">${f.rule_id}: ${f.title}</div>`;
        html += `<div style="font-size:10px;color:#7a8cb0;margin-top:2px;">${(f.frameworks || []).join(', ')}</div>`;
        html += '</div>';
      });
    }
    if (cat2.length) {
      html += `<div style="color:#f39c12;font-weight:700;margin:6px 0 2px;">CAT II — Moderate (${cat2.length})</div>`;
      cat2.forEach(f => {
        html += `<div style="margin:4px 0;padding:6px 8px;background:#2b1a0f;border-left:3px solid #f39c12;border-radius:0 4px 4px 0;">`;
        html += `<div style="font-weight:600;font-size:11px;">${f.rule_id}: ${f.title}</div>`;
        html += `<div style="font-size:10px;color:#7a8cb0;margin-top:2px;">${(f.frameworks || []).join(', ')}</div>`;
        html += '</div>';
      });
    }
  }
  return html;
}

function _renderCost(r) {
  let html = _metric('Per Run', '$' + (r.per_run_cost || 0).toFixed(4));
  html += _metric('Runs/Month', r.runs_per_month || 0);
  html += _metric('Monthly Variable', '$' + (r.monthly_variable || 0).toFixed(2), 'Per-run costs x frequency');
  html += _metric('Monthly Fixed', '$' + (r.monthly_fixed || 0).toFixed(2), 'Licensing and platform fees');
  html += `<div style="margin:12px 0;padding:10px;background:#0f3460;border-radius:6px;text-align:center;">`;
  html += `<div style="font-size:10px;color:#7a8cb0;">Estimated Monthly Total</div>`;
  html += `<div style="font-size:28px;font-weight:800;color:#3498db;">$${(r.total_monthly || 0).toFixed(2)}</div>`;
  html += '</div>';

  if ((r.details || []).length) {
    html += _section('Per-Run Breakdown');
    r.details.forEach(d => {
      const style = getStyle(d.type);
      html += _metric(style.label, '$' + (d.per_run || 0).toFixed(4));
    });
  }

  html += '<p style="font-size:10px;color:#5a6e8c;margin-top:8px;">Estimates based on public pricing. Open source tools show $0 (compute cost not included). Actual costs vary by usage.</p>';
  return html;
}

function _renderExecTime(r) {
  const total = r.total_minutes || 0;
  const color = total <= 10 ? '#27ae60' : total <= 30 ? '#f39c12' : '#e74c3c';
  let html = `<div style="text-align:center;margin:8px 0;"><span style="font-size:36px;font-weight:800;color:${color};">${total}</span><span style="font-size:14px;color:#7a8cb0;"> min</span></div>`;

  if (total <= 10) html += '<p style="font-size:11px;color:#27ae60;">Fast pipeline. Developers get quick feedback on commits.</p>';
  else if (total <= 30) html += '<p style="font-size:11px;color:#f39c12;">Moderate execution time. Consider parallelizing independent scan stages.</p>';
  else html += '<p style="font-size:11px;color:#e74c3c;">Slow pipeline. Developers may skip CI or stack commits. Parallelize stages and optimize scan scopes.</p>';

  if (r.stage_breakdown) {
    html += _section('Stage Breakdown');
    const sorted = Object.entries(r.stage_breakdown).sort((a, b) => b[1] - a[1]);
    sorted.forEach(([stage, min]) => {
      const stageLabel = STAGE_LABELS[stage] || stage;
      const stageColor = STAGE_COLORS[stage] || '#7a8cb0';
      const pct = total > 0 ? Math.round(min / total * 100) : 0;
      html += `<div style="margin:4px 0;"><div style="display:flex;justify-content:space-between;font-size:11px;"><span style="color:${stageColor};font-weight:600;">${stageLabel}</span><span>${min} min (${pct}%)</span></div>`;
      html += _bar(pct, stageColor);
      html += '</div>';
    });
  }
  return html;
}

function _renderAntipatterns(r) {
  const findings = r.findings || [];
  const total = findings.length;
  const critical = findings.filter(f => f.severity === 'critical').length;
  const high = findings.filter(f => f.severity === 'high').length;
  const medium = findings.filter(f => f.severity === 'medium').length;

  let html = '';
  if (total === 0) {
    html += '<div style="text-align:center;margin:12px 0;color:#27ae60;font-size:20px;font-weight:700;">No Anti-Patterns Detected</div>';
    html += '<p style="font-size:11px;color:#7a8cb0;text-align:center;">Your pipeline design follows DevSecOps best practices.</p>';
    return html;
  }

  html += _metric('Total Findings', total);
  if (critical) html += _metric('Critical', critical, 'Must fix before deployment');
  if (high) html += _metric('High', high, 'Strongly recommended');
  if (medium) html += _metric('Medium', medium, 'Advisory');
  html += _bar((1 - total / 15) * 100, total > 3 ? '#e74c3c' : total > 1 ? '#f39c12' : '#27ae60');

  // Group by severity
  const sevOrder = ['critical', 'high', 'medium'];
  const sevColors = {critical: '#e74c3c', high: '#f39c12', medium: '#7a8cb0'};
  const sevLabels = {critical: 'CRITICAL', high: 'HIGH', medium: 'MEDIUM'};

  sevOrder.forEach(sev => {
    const sevFindings = findings.filter(f => f.severity === sev);
    if (!sevFindings.length) return;
    html += _section(sevLabels[sev] + ' (' + sevFindings.length + ')');
    sevFindings.forEach(f => {
      html += `<div style="margin:4px 0;padding:8px;background:#0f2040;border-left:3px solid ${sevColors[sev]};border-radius:0 4px 4px 0;">`;
      html += `<div style="font-weight:600;font-size:11px;color:${sevColors[sev]};">${f.id}: ${f.title}</div>`;
      html += `<div style="font-size:10px;color:#7a8cb0;margin:3px 0;">${f.description}</div>`;
      html += `<div style="font-size:10px;color:#27ae60;margin:3px 0;"><b>Fix:</b> ${f.recommendation}</div>`;
      if (f.frameworks && f.frameworks.length) {
        html += '<div style="margin-top:3px;">';
        f.frameworks.forEach(fw => { html += _pill(fw, '#0f3460'); });
        html += '</div>';
      }
      html += '</div>';
    });
  });
  return html;
}

function _renderPDCGovernance(r) {
  const score = r.score || 0;
  const grade = r.grade || '?';
  const maturity = r.maturity || {};
  const cats = r.categories || {};
  const recs = r.recommendations || [];
  const sColor = score >= 80 ? '#27ae60' : score >= 60 ? '#f39c12' : '#e74c3c';

  let html = `<div style="background:#0f2040;border-radius:8px;padding:12px;margin-bottom:12px;text-align:center;">
    <div style="font-size:38px;font-weight:700;color:${sColor};">${score}</div>
    <div style="font-size:11px;color:#7a8cb0;">Pipeline Governance Score / 100 · Grade <strong style="color:${sColor};">${grade}</strong></div>
  </div>`;
  html += _bar(score, sColor);
  html += `<div style="background:#16213e;border:1px solid #9b59b644;border-radius:6px;padding:8px 12px;margin-bottom:12px;">
    <div style="font-size:12px;font-weight:700;color:#9b59b6;">Level ${maturity.level||1} — ${maturity.label||''}</div>
    <div style="font-size:10px;color:#5a6e8c;">${maturity.description||''}</div>
  </div>`;
  html += `<div style="font-size:11px;font-weight:700;color:#7a8cb0;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">Pillars (${r.passed_checks}/${r.total_checks} passed)</div>`;
  Object.entries(cats).forEach(([pillar, c]) => {
    const pct = c.pct || 0;
    const pColor = pct >= 80 ? '#27ae60' : pct >= 50 ? '#f39c12' : '#e74c3c';
    html += `<div style="margin-bottom:7px;"><div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:2px;"><span style="color:#eaeaea;">${pillar}</span><span style="color:${pColor};font-weight:600;">${pct}% (${c.passed}/${c.total})</span></div>${_bar(pct, pColor)}</div>`;
  });
  if (recs.length) {
    html += `<div style="font-size:11px;font-weight:700;color:#7a8cb0;text-transform:uppercase;letter-spacing:.5px;margin:10px 0 6px;">Top Recommendations</div>`;
    recs.slice(0, 6).forEach(rec => {
      const bColor = rec.priority === 'CAT1' ? '#e74c3c' : rec.priority === 'CAT2' ? '#f39c12' : '#7a8cb0';
      html += `<div style="padding:5px 8px;border-left:3px solid ${bColor};background:#0d1b2a;border-radius:0 4px 4px 0;margin-bottom:4px;font-size:11px;color:#eaeaea;">${rec.title}</div>`;
    });
  }
  return html;
}

function _renderScorecard(data) {
  let html = '';
  // Overall grade
  const owasp = data.security_coverage?.coverage_pct || 0;
  const slsa = data.slsa_level?.achieved_level || 0;
  const comp = data.compliance?.score_pct || 0;
  const overall = Math.round((owasp + slsa * 25 + comp) / 3);
  const grade = overall >= 90 ? 'A' : overall >= 80 ? 'B' : overall >= 60 ? 'C' : overall >= 40 ? 'D' : 'F';
  const gradeColor = { A: '#27ae60', B: '#2ecc71', C: '#f39c12', D: '#e67e22', F: '#e74c3c' }[grade];
  html += `<div style="text-align:center;margin:8px 0;"><span style="font-size:48px;font-weight:800;color:${gradeColor};">${grade}</span><div style="font-size:11px;color:#7a8cb0;">Overall Grade (${overall}%)</div></div>`;

  html += _section('Security');
  html += _metric('OWASP Coverage', owasp + '%');
  html += _bar(owasp, owasp >= 80 ? '#27ae60' : owasp >= 50 ? '#f39c12' : '#e74c3c');

  html += _section('Supply Chain');
  const slsaColor = ['#e74c3c', '#f39c12', '#f1c40f', '#27ae60', '#16a085'][Math.min(slsa, 4)];
  html += _metric('SLSA Level', 'L' + slsa);
  html += _bar(slsa * 25, slsaColor);

  html += _section('Compliance');
  html += _metric('Rules Passed', comp + '%', `${data.compliance?.passed || 0} of ${(data.compliance?.passed || 0) + (data.compliance?.failed || 0)} rules`);
  html += _bar(comp, comp >= 80 ? '#27ae60' : comp >= 50 ? '#f39c12' : '#e74c3c');

  // Anti-patterns
  const ap = data.antipatterns || {};
  if (ap.total > 0) {
    html += _section('Anti-Patterns');
    html += _metric('Critical', ap.critical || 0);
    html += _metric('High', ap.high || 0);
    html += _metric('Medium', ap.medium || 0);
    const apScore = Math.max(0, 100 - (ap.critical || 0) * 30 - (ap.high || 0) * 15 - (ap.medium || 0) * 5);
    html += _bar(apScore, apScore >= 70 ? '#27ae60' : apScore >= 40 ? '#f39c12' : '#e74c3c');
  } else {
    html += _section('Anti-Patterns');
    html += '<div style="color:#27ae60;font-size:12px;font-weight:600;">None detected</div>';
  }

  html += _section('Operations');
  html += _metric('Est. Cost/mo', '$' + (data.cost_estimate?.total_monthly || 0).toFixed(2));
  html += _metric('Execution Time', (data.execution_time?.total_minutes || 0) + ' min');
  html += _metric('Node Count', data.node_count || 0);
  html += _metric('Edge Count', data.edge_count || 0);
  html += _metric('Stage Coverage', (data.stages_covered || 0) + ' / ' + (data.total_stages || 12));

  return html;
}
