/**
 * Migration Design Canvas — JointJS canvas + analysis panel logic.
 *
 * Extends the shared design-canvas.js with migration-specific analysis
 * buttons (Assess, Gaps, Readiness, Stats) that populate the right panel.
 *
 * Requires window.CANVAS_CONFIG to be set before loading:
 *   {
 *     containerId: 'canvas-container',
 *     designId: 'new' | 'mc-xxxx',
 *     apiBase: '/migration-canvas/api',
 *     graphJson: '{"nodes":[],"edges":[]}',
 *     accentColor: '#27ae60',
 *   }
 */

/* ── Category colors for migration node types ──────────────────────────── */
const MC_COLORS = {
  'src': { fill: '#0f2b3a', stroke: '#3498db' },   // Sources (blue)
  'tgt': { fill: '#1a2e1a', stroke: '#27ae60' },   // Targets (green)
  'pat': { fill: '#2b1a2e', stroke: '#9b59b6' },   // Patterns (purple)
  'mid': { fill: '#2b1a1a', stroke: '#e94560' },   // Middleware (red)
  'ctl': { fill: '#1a2e2e', stroke: '#1abc9c' },   // Controls (teal)
  'wave': { fill: '#1a1a2e', stroke: '#f39c12' },  // Waves (orange)
  'plan': { fill: '#1a1a2e', stroke: '#e67e22' },  // Planning (amber)
  'default': { fill: '#16213e', stroke: '#5a6e8c' },
};

// Inject MC_COLORS into shared CATEGORY_COLORS if design-canvas.js loaded
if (typeof CATEGORY_COLORS !== 'undefined') {
  Object.assign(CATEGORY_COLORS, MC_COLORS);
}

/* ── Right Panel helpers ───────────────────────────────────────────────── */

function openRightPanel(title, html) {
  const panel = document.getElementById('dc-right-panel');
  document.getElementById('dc-panel-title').textContent = title;
  document.getElementById('dc-panel-body').innerHTML = html;
  panel.classList.add('open');
}

function closeRightPanel() {
  document.getElementById('dc-right-panel').classList.remove('open');
}

/* ── Export menu (supplements design-canvas.js) ────────────────────────── */

function toggleExportMenu() {
  const dd = document.getElementById('export-dropdown');
  dd.style.display = dd.style.display === 'none' ? 'block' : 'none';
}

function canvasExport(format) {
  const dd = document.getElementById('export-dropdown');
  dd.style.display = 'none';

  if (format === 'json') {
    const graphData = graph ? graph.toJSON() : {};
    const blob = new Blob([JSON.stringify(graphData, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'migration-design.json';
    a.click();
  } else if (format === 'svg') {
    if (paper) {
      const svgEl = paper.svg;
      const serializer = new XMLSerializer();
      const svgStr = serializer.serializeToString(svgEl);
      const blob = new Blob([svgStr], { type: 'image/svg+xml' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'migration-design.svg';
      a.click();
    }
  }
}

/* ── Analysis: Assess ──────────────────────────────────────────────────── */

function canvasAssess() {
  const cfg = window.CANVAS_CONFIG || {};
  const designId = cfg.designId;
  if (designId === 'new') {
    openRightPanel('Assessment', '<p style="color:#f39c12;">Save the design first before running assessment.</p>');
    return;
  }
  openRightPanel('Assessment', '<p style="color:#7a8cb0;">Running assessment...</p>');
  fetch(`${cfg.apiBase}/designs/${designId}/assess`, { method: 'POST' })
    .then(r => r.json())
    .then(data => {
      const scoreColor = data.score >= 80 ? '#27ae60' : data.score >= 60 ? '#f39c12' : '#e74c3c';
      let html = `
        <div class="rp-score" style="color:${scoreColor}">${data.score}</div>
        <div class="rp-grade">Grade: <span style="font-weight:700;color:${scoreColor}">${data.grade}</span></div>
        <div style="display:flex;gap:10px;justify-content:center;margin-bottom:16px;">
          <span style="background:#e74c3c;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">CAT1: ${data.cat1_count}</span>
          <span style="background:#f39c12;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">CAT2: ${data.cat2_count}</span>
          <span style="background:#3498db;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">CAT3: ${data.cat3_count}</span>
        </div>
      `;
      if (data.readiness) {
        html += `
          <div class="rp-label">Readiness: ${data.readiness.overall}%</div>
          <div class="rp-meter"><div class="rp-meter-fill" style="width:${data.readiness.overall}%;background:${scoreColor}"></div></div>
          <div style="font-size:11px;color:#7a8cb0;margin-bottom:12px;">${data.readiness.summary}</div>
        `;
      }
      if (data.findings && data.findings.length) {
        html += '<div style="font-size:11px;font-weight:700;margin-bottom:8px;color:#eaeaea;">Findings</div>';
        data.findings.forEach(f => {
          const cls = f.severity === 'CAT1' ? '' : f.severity === 'CAT2' ? ' cat2' : ' cat3';
          html += `<div class="rp-finding${cls}"><div style="font-weight:600;font-size:11px;margin-bottom:2px;">${f.rule_id}: ${f.title}</div><div style="font-size:10px;color:#7a8cb0;">${f.detail}</div></div>`;
        });
      } else {
        html += '<p style="color:#27ae60;font-size:12px;">No findings — design passes all compliance rules.</p>';
      }
      openRightPanel('Assessment Results', html);
    })
    .catch(() => {
      openRightPanel('Assessment', '<p style="color:#e74c3c;">Assessment failed. Check console.</p>');
    });
}

/* ── Analysis: Gaps ────────────────────────────────────────────────────── */

function canvasGaps() {
  const cfg = window.CANVAS_CONFIG || {};
  const designId = cfg.designId;
  if (designId === 'new') {
    openRightPanel('Gap Analysis', '<p style="color:#f39c12;">Save the design first.</p>');
    return;
  }
  openRightPanel('Gap Analysis', '<p style="color:#7a8cb0;">Detecting gaps...</p>');
  fetch(`${cfg.apiBase}/designs/${designId}/gaps`)
    .then(r => r.json())
    .then(data => {
      let html = `<div style="font-size:14px;font-weight:700;margin-bottom:12px;">${data.total} gap(s) detected</div>`;
      if (data.gaps && data.gaps.length) {
        data.gaps.forEach(g => {
          const cls = g.severity === 'high' ? '' : ' medium';
          html += `<div class="rp-gap${cls}"><div style="font-weight:600;font-size:11px;">${g.type.replace(/_/g, ' ')}</div><div style="font-size:10px;color:#7a8cb0;margin:2px 0;">${g.description}</div><div style="font-size:10px;color:#27ae60;">Fix: ${g.recommendation}</div></div>`;
        });
      } else {
        html += '<p style="color:#27ae60;font-size:12px;">No gaps detected — design is complete.</p>';
      }
      openRightPanel('Gap Analysis', html);
    });
}

/* ── Analysis: Readiness ───────────────────────────────────────────────── */

function canvasReadiness() {
  const cfg = window.CANVAS_CONFIG || {};
  const designId = cfg.designId;
  if (designId === 'new') {
    openRightPanel('Readiness', '<p style="color:#f39c12;">Save the design first.</p>');
    return;
  }
  openRightPanel('Readiness', '<p style="color:#7a8cb0;">Computing readiness...</p>');
  fetch(`${cfg.apiBase}/designs/${designId}/readiness`)
    .then(r => r.json())
    .then(data => {
      const scoreColor = data.overall >= 80 ? '#27ae60' : data.overall >= 60 ? '#f39c12' : '#e74c3c';
      const dims = [
        { label: 'Completeness', val: data.completeness },
        { label: 'Compliance', val: data.compliance },
        { label: 'Risk Mitigation', val: data.risk_mitigation },
        { label: 'Planning', val: data.planning },
      ];
      let html = `
        <div class="rp-score" style="color:${scoreColor}">${data.overall}%</div>
        <div style="text-align:center;font-size:12px;color:#7a8cb0;margin-bottom:16px;">${data.summary}</div>
      `;
      dims.forEach(d => {
        const c = d.val >= 80 ? '#27ae60' : d.val >= 60 ? '#f39c12' : '#e74c3c';
        html += `<div class="rp-label">${d.label}: ${d.val}%</div><div class="rp-meter"><div class="rp-meter-fill" style="width:${d.val}%;background:${c}"></div></div>`;
      });
      openRightPanel('Migration Readiness', html);
    });
}

/* ── Analysis: Stats ───────────────────────────────────────────────────── */

function canvasStats() {
  const cfg = window.CANVAS_CONFIG || {};
  const designId = cfg.designId;
  if (designId === 'new') {
    openRightPanel('Statistics', '<p style="color:#f39c12;">Save the design first.</p>');
    return;
  }
  openRightPanel('Statistics', '<p style="color:#7a8cb0;">Loading stats...</p>');
  fetch(`${cfg.apiBase}/designs/${designId}/stats`)
    .then(r => r.json())
    .then(data => {
      let html = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">
          <div style="text-align:center;padding:12px;background:#16213e;border-radius:6px;"><div style="font-size:24px;font-weight:700;color:#27ae60;">${data.total_nodes}</div><div style="font-size:10px;color:#7a8cb0;">Total Nodes</div></div>
          <div style="text-align:center;padding:12px;background:#16213e;border-radius:6px;"><div style="font-size:24px;font-weight:700;color:#3498db;">${data.total_edges}</div><div style="font-size:10px;color:#7a8cb0;">Total Edges</div></div>
          <div style="text-align:center;padding:12px;background:#16213e;border-radius:6px;"><div style="font-size:24px;font-weight:700;color:#e94560;">${data.sources}</div><div style="font-size:10px;color:#7a8cb0;">Sources</div></div>
          <div style="text-align:center;padding:12px;background:#16213e;border-radius:6px;"><div style="font-size:24px;font-weight:700;color:#27ae60;">${data.targets}</div><div style="font-size:10px;color:#7a8cb0;">Targets</div></div>
          <div style="text-align:center;padding:12px;background:#16213e;border-radius:6px;"><div style="font-size:24px;font-weight:700;color:#9b59b6;">${data.patterns}</div><div style="font-size:10px;color:#7a8cb0;">Patterns</div></div>
          <div style="text-align:center;padding:12px;background:#16213e;border-radius:6px;"><div style="font-size:24px;font-weight:700;color:#1abc9c;">${data.controls}</div><div style="font-size:10px;color:#7a8cb0;">Controls</div></div>
        </div>
      `;
      if (data.strategy_distribution && Object.keys(data.strategy_distribution).length) {
        html += '<div style="font-size:11px;font-weight:700;margin-bottom:8px;color:#eaeaea;">Strategy Distribution</div>';
        Object.entries(data.strategy_distribution).forEach(([k, v]) => {
          const label = k.replace('pat-', '').replace(/-/g, ' ');
          html += `<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:11px;"><span style="text-transform:capitalize;">${label}</span><span style="color:#27ae60;font-weight:600;">${v}</span></div>`;
        });
      }
      openRightPanel('Design Statistics', html);
    });
}

/* ── Analysis: Oracle Anticipation ──────────────────────────────────── */

function canvasOracle() {
  const cfg = window.CANVAS_CONFIG || {};
  openRightPanel('Oracle Anticipation', '<p style="color:#7a8cb0;">Running anticipatory analysis...</p>');
  fetch(`${cfg.apiBase}/oracle/migration`)
    .then(r => r.json())
    .then(data => {
      let html = `<div style="font-size:14px;font-weight:700;margin-bottom:12px;color:#f39c12;">${data.count} prediction(s)</div>`;
      if (data.predictions && data.predictions.length) {
        data.predictions.forEach(p => {
          const sevColor = p.severity === 'critical' ? '#e74c3c' : p.severity === 'warning' ? '#f39c12' : '#3498db';
          html += `<div style="padding:8px;border-left:3px solid ${sevColor};margin-bottom:8px;background:#1a1a2e;border-radius:0 4px 4px 0;">`;
          html += `<div style="font-weight:600;font-size:11px;margin-bottom:2px;">${p.title}</div>`;
          html += `<div style="font-size:10px;color:#7a8cb0;margin-bottom:4px;">${p.description}</div>`;
          html += `<div style="font-size:10px;display:flex;gap:8px;margin-bottom:4px;">`;
          html += `<span style="color:${sevColor};font-weight:600;">${p.severity.toUpperCase()}</span>`;
          html += `<span style="color:#7a8cb0;">Confidence: ${(p.confidence * 100).toFixed(0)}%</span>`;
          html += `</div>`;
          if (p.recommendations && p.recommendations.length) {
            html += `<div style="font-size:10px;color:#27ae60;">`;
            p.recommendations.forEach(r => { html += `<div>&#x2192; ${r}</div>`; });
            html += `</div>`;
          }
          html += `</div>`;
        });
      } else {
        html += '<p style="color:#27ae60;font-size:12px;">No anticipatory risks detected. Migration designs look healthy.</p>';
      }
      if (data.error) {
        html += `<p style="color:#e74c3c;font-size:10px;margin-top:8px;">Warning: ${data.error}</p>`;
      }
      openRightPanel('Oracle Anticipation', html);
    })
    .catch(() => {
      openRightPanel('Oracle', '<p style="color:#e74c3c;">Oracle analysis failed.</p>');
    });
}

/* ── Close export dropdown on outside click ────────────────────────────── */
document.addEventListener('click', function(e) {
  const dd = document.getElementById('export-dropdown');
  if (dd && !e.target.closest('.dc-export-group')) {
    dd.style.display = 'none';
  }
});
