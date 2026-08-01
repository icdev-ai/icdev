/**
 * WriteGuard Embedded (D-WG-14)
 *
 * Full WriteGuard analysis + rewrite embedded in any page (proposals, govcon).
 * Provides tab-level rendering, diff view, and in-place acceptance.
 *
 * Usage:
 *   ICDEV.writeguard.renderTab(containerId, text, opts);
 *
 * CUI // SP-CTI
 */
(function () {
  'use strict';

  const API = {
    analyze: '/api/writeguard/analyze',
    rewrite: '/api/writeguard/rewrite',
    styleMatch: '/api/writeguard/style-match',
    classify: '/api/writeguard/classify',
  };

  // ── Utilities ────────────────────────────────────────────────────────
  function escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function scoreClass(s) {
    if (s >= 75) return 'score-good';
    if (s >= 50) return 'score-warn';
    return 'score-bad';
  }

  function barClass(s) {
    if (s >= 75) return 'bar-good';
    if (s >= 50) return 'bar-warn';
    return 'bar-bad';
  }

  function renderMarkdown(text) {
    // nav-sec-08: prefer the shared fail-closed sanitizer (marked + DOMPurify)
    // so draft/LLM/shared-document content previewed here cannot inject markup.
    if (typeof window.safeMarkdown === 'function') {
      return window.safeMarkdown(text);
    }
    if (typeof marked !== 'undefined' && marked.parse) {
      try {
        marked.setOptions({ gfm: true, breaks: true, sanitize: false });
        return marked.parse(text, { breaks: true, gfm: true });
      } catch (e) {}
    }
    // Minimal fallback
    return text
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
      .replace(/^### (.+)$/gm, '<h3>$1</h3>')
      .replace(/^## (.+)$/gm, '<h2>$1</h2>')
      .replace(/^# (.+)$/gm, '<h1>$1</h1>')
      .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/~~(.+?)~~/g, '<del>$1</del>')
      .replace(/`(.+?)`/g, '<code>$1</code>')
      .replace(/^\|(.+)\|\s*$/gm, function (_, row) {
        const cells = row.split('|').map(function (c) { return c.trim(); }).filter(Boolean);
        return '<tr>' + cells.map(function (c) { return '<td style="padding:4px 8px;border:1px solid #dee2e6">' + c + '</td>'; }).join('') + '</tr>';
      })
      .replace(/(<tr>.*?<\/tr>\n?)+/gs, function (match) { return '<table style="border-collapse:collapse;width:100%;margin:8px 0">' + match + '</table>'; })
      .replace(/- \[x\] /gi, '<li style="list-style:none">✅ ')
      .replace(/- \[ \] /g, '<li style="list-style:none">☐ ')
      .replace(/^- (.+)$/gm, '<li>$1</li>')
      .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
      .replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')
      .replace(/^\> (.+)$/gm, '<blockquote>$1</blockquote>')
      .replace(/\n\n/g, '</p><p>')
      .replace(/\n/g, '<br>') || '<p></p>';
  }

  // ── Tab rendering ────────────────────────────────────────────────────
  function renderTab(containerId, text, opts) {
    opts = opts || {};
    const container = document.getElementById(containerId);
    if (!container) { console.error('WriteGuardEmbedded: container not found', containerId); return; }

    const draftId = opts.draftId || '';
    const sectionId = opts.sectionId || '';
    const oppId = opts.oppId || '';

    container.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem;flex-wrap:wrap;gap:0.5rem;">
        <div>
          <button class="btn btn-primary btn-sm" id="wg-emb-analyze-btn" style="font-size:0.82rem;">Analyze Draft</button>
          <button class="btn btn-secondary btn-sm" id="wg-emb-rewrite-btn" style="display:none;font-size:0.82rem;margin-left:0.4rem;">Rewrite</button>
        </div>
        <div id="wg-emb-status" style="font-size:0.78rem;color:var(--text-muted);"></div>
      </div>

      <div id="wg-emb-results" style="display:none;">
        <!-- Composite gauge -->
        <div style="text-align:center;margin-bottom:1rem;">
          <div id="wg-emb-overall" style="font-size:2.8rem;font-weight:800;line-height:1;color:var(--accent-blue);">—</div>
          <div style="font-size:0.8rem;color:var(--text-muted);margin-top:0.25rem;">Overall Score (0–100)</div>
          <div style="display:flex;gap:0.4rem;flex-wrap:wrap;justify-content:center;align-items:center;margin-top:0.4rem;">
            <span id="wg-emb-badge" class="badge badge-info" style="font-size:0.72rem;padding:0.2rem 0.6rem;">PENDING</span>
          </div>
        </div>

        <!-- Dimension bars -->
        <div style="margin-bottom:0.75rem;">
          <div style="font-size:0.8rem;font-weight:600;color:var(--text-primary);margin-bottom:0.5rem;">Dimension Scores</div>
          <div id="wg-emb-dims" style="display:flex;flex-direction:column;gap:0.55rem;"></div>
        </div>

        <!-- Findings -->
        <div id="wg-emb-findings-wrap" style="display:none;">
          <div style="font-size:0.85rem;font-weight:600;color:var(--text-primary);margin-bottom:0.5rem;">
            Findings <span id="wg-emb-finding-count" style="color:var(--text-muted);font-weight:400;"></span>
          </div>
          <div id="wg-emb-findings" style="display:flex;flex-direction:column;gap:0.45rem;max-height:260px;overflow-y:auto;"></div>
        </div>

        <!-- Recommendations -->
        <div id="wg-emb-recs-wrap" style="display:none;margin-top:0.75rem;background:rgba(74,158,255,0.07);border-left:3px solid var(--accent-blue);border-radius:4px;padding:0.75rem;">
          <div style="font-size:0.8rem;font-weight:700;color:var(--accent-blue);margin-bottom:0.4rem;">Recommendations</div>
          <ul id="wg-emb-recs" style="margin:0;padding-left:1.1rem;"></ul>
        </div>
      </div>

      <!-- Rewrite result modal (inline) -->
      <div id="wg-emb-rewrite-modal" style="display:none;margin-top:0.75rem;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:8px;padding:1rem;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">
          <span style="font-size:0.85rem;font-weight:700;color:var(--text-primary);">Rewritten Text</span>
          <span id="wg-emb-rewrite-delta" style="font-size:0.78rem;font-weight:600;"></span>
        </div>
        <div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:0.4rem;" id="wg-emb-rewrite-changes"></div>
        <div style="display:flex;gap:0.5rem;margin-bottom:0.5rem;">
          <button type="button" id="wg-emb-rewrite-tab-source" class="wg-emb-tab-btn active" onclick="ICDEV.writeguard.setRewriteTab('source')" style="background:var(--bg-primary);color:var(--text-primary);border:1px solid var(--border-color);border-radius:4px;padding:0.25rem 0.6rem;font-size:0.78rem;cursor:pointer;">Source</button>
          <button type="button" id="wg-emb-rewrite-tab-preview" class="wg-emb-tab-btn" onclick="ICDEV.writeguard.setRewriteTab('preview')" style="background:var(--bg-primary);color:var(--text-muted);border:1px solid var(--border-color);border-radius:4px;padding:0.25rem 0.6rem;font-size:0.78rem;cursor:pointer;">Preview</button>
        </div>
        <textarea id="wg-emb-rewrite-text" style="width:100%;min-height:160px;background:var(--bg-primary);color:var(--text-primary);border:1px solid var(--border-color);border-radius:6px;padding:0.6rem;font-family:inherit;font-size:0.85rem;resize:vertical;box-sizing:border-box;" readonly></textarea>
        <div id="wg-emb-rewrite-preview" style="display:none;min-height:160px;background:var(--bg-primary);border:1px solid var(--border-color);border-radius:6px;padding:0.6rem;overflow:auto;line-height:1.7;font-size:0.9rem;"></div>
        <div style="display:flex;gap:0.5rem;margin-top:0.5rem;">
          <button class="btn btn-success btn-sm" id="wg-emb-accept-btn" style="font-size:0.82rem;">Accept & Save Draft</button>
          <button class="btn btn-secondary btn-sm" id="wg-emb-reject-btn" style="font-size:0.82rem;">Discard</button>
        </div>
      </div>
    `;

    // Wire buttons
    document.getElementById('wg-emb-analyze-btn').addEventListener('click', function () {
      runAnalyze(text, opts);
    });
    document.getElementById('wg-emb-rewrite-btn').addEventListener('click', function () {
      runRewrite(text, opts);
    });
    document.getElementById('wg-emb-accept-btn').addEventListener('click', function () {
      acceptRewrite(opts);
    });
    document.getElementById('wg-emb-reject-btn').addEventListener('click', function () {
      document.getElementById('wg-emb-rewrite-modal').style.display = 'none';
    });
  }

  // ── Analysis ─────────────────────────────────────────────────────────
  async function runAnalyze(text, opts) {
    const statusEl = document.getElementById('wg-emb-status');
    const btn = document.getElementById('wg-emb-analyze-btn');
    const rewriteBtn = document.getElementById('wg-emb-rewrite-btn');
    statusEl.textContent = 'Analyzing…';
    btn.disabled = true;

    try {
      const resp = await fetch(API.analyze, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, mode: opts.mode || 'default' }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error) {
        statusEl.textContent = data.error || 'Analysis failed';
        btn.disabled = false;
        return;
      }
      renderResults(data);
      statusEl.textContent = 'Analysis complete.';
      btn.disabled = false;
      rewriteBtn.style.display = 'inline-block';
    } catch (err) {
      statusEl.textContent = 'Network error: ' + err.message;
      btn.disabled = false;
    }
  }

  function renderResults(data) {
    const resultsEl = document.getElementById('wg-emb-results');
    const overallEl = document.getElementById('wg-emb-overall');
    const badgeEl = document.getElementById('wg-emb-badge');
    const dimsEl = document.getElementById('wg-emb-dims');
    const findingsWrap = document.getElementById('wg-emb-findings-wrap');
    const findingsEl = document.getElementById('wg-emb-findings');
    const findingCount = document.getElementById('wg-emb-finding-count');
    const recsWrap = document.getElementById('wg-emb-recs-wrap');
    const recsEl = document.getElementById('wg-emb-recs');

    resultsEl.style.display = 'block';

    const score = typeof data.overall_score === 'number' ? data.overall_score : 0;
    overallEl.textContent = score.toFixed(1);
    overallEl.className = scoreClass(score);

    if (data.passed) {
      badgeEl.textContent = 'PASSED';
      badgeEl.className = 'badge badge-success';
    } else {
      badgeEl.textContent = 'NEEDS REVIEW';
      badgeEl.className = 'badge badge-warning';
    }

    // Dimensions
    dimsEl.innerHTML = '';
    const dims = data.dimensions || {};
    Object.entries(dims).forEach(function ([key, dim]) {
      const s = typeof dim.score === 'number' ? dim.score : 0;
      const unavail = dim.status === 'unavailable';
      const suppressed = dim.suppressed === true;
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:0.5rem;';
      row.innerHTML = `
        <span style="font-size:0.8rem;width:100px;flex-shrink:0;color:var(--text-primary);">${escHtml(dim.label || key)}</span>
        <div style="flex:1;background:var(--bg-primary);border-radius:4px;height:7px;overflow:hidden;">
          <div style="height:100%;border-radius:4px;transition:width 0.5s ease;${suppressed || unavail ? 'background:rgba(255,255,255,0.08);' : 'background:var(--accent-blue);'}width:${suppressed || unavail ? 0 : s}%;"></div>
        </div>
        <span style="font-size:0.78rem;font-weight:600;width:36px;text-align:right;flex-shrink:0;color:${suppressed ? '#888' : unavail ? '#666' : s >= 75 ? '#00c864' : s >= 50 ? '#ffa500' : '#e53e3e'};">
          ${suppressed ? 'n/a' : unavail ? '—' : Math.round(s)}
        </span>
      `;
      dimsEl.appendChild(row);
    });

    // Findings
    const findings = data.findings || [];
    if (findings.length > 0) {
      findingsWrap.style.display = 'block';
      findingCount.textContent = '(' + findings.length + ')';
      findingsEl.innerHTML = '';
      findings.forEach(function (f) {
        const sev = (f.severity || 'medium').toLowerCase();
        const colors = {
          critical: 'background:rgba(229,62,62,0.15);color:#e53e3e;border-color:rgba(229,62,62,0.4);',
          high: 'background:rgba(255,100,50,0.15);color:#ff6432;border-color:rgba(255,100,50,0.4);',
          medium: 'background:rgba(255,165,0,0.15);color:#ffa500;border-color:rgba(255,165,0,0.4);',
          low: 'background:rgba(74,158,255,0.15);color:#4a9eff;border-color:rgba(74,158,255,0.4);',
          info: 'background:rgba(136,136,136,0.15);color:#888;border-color:rgba(136,136,136,0.4);',
        };
        const item = document.createElement('div');
        item.style.cssText = 'background:var(--bg-primary);border-radius:6px;padding:0.5rem 0.65rem;font-size:0.8rem;display:flex;gap:0.5rem;align-items:flex-start;';
        item.innerHTML = `
          <span style="font-size:0.68rem;font-weight:700;padding:0.15rem 0.45rem;border-radius:3px;flex-shrink:0;text-transform:uppercase;letter-spacing:0.04em;margin-top:1px;${colors[sev] || colors.medium}">${escHtml(sev)}</span>
          <div style="flex:1;line-height:1.4;">
            <div>${escHtml(f.message || f.description || '')}</div>
            ${f.suggestion ? '<div style="color:var(--text-muted);font-size:0.75rem;margin-top:0.2rem;">' + escHtml(f.suggestion) + '</div>' : ''}
            ${f.dimension ? '<div style="font-size:0.72rem;color:#888;margin-top:0.15rem;">' + escHtml(f.dimension) + '</div>' : ''}
          </div>
        `;
        findingsEl.appendChild(item);
      });
    } else {
      findingsWrap.style.display = 'none';
    }

    // Recommendations
    const recs = data.recommendations || [];
    if (recs.length > 0) {
      recsWrap.style.display = 'block';
      recsEl.innerHTML = recs.map(function (r) { return '<li style="font-size:0.8rem;color:var(--text-primary);margin-bottom:0.2rem;">' + escHtml(r) + '</li>'; }).join('');
    } else {
      recsWrap.style.display = 'none';
    }
  }

  // ── Rewrite ──────────────────────────────────────────────────────────
  async function runRewrite(text, opts) {
    const statusEl = document.getElementById('wg-emb-status');
    const rewriteBtn = document.getElementById('wg-emb-rewrite-btn');
    const modal = document.getElementById('wg-emb-rewrite-modal');
    statusEl.textContent = 'Rewriting…';
    rewriteBtn.disabled = true;

    try {
      const resp = await fetch(API.rewrite, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, mode: opts.mode || 'default' }),
      });
      const data = await resp.json();
      if (!resp.ok || data.error) {
        statusEl.textContent = data.error || 'Rewrite failed';
        rewriteBtn.disabled = false;
        return;
      }

      const delta = data.score_improvement || 0;
      const deltaColor = delta > 0 ? '#00c864' : (delta < 0 ? '#e53e3e' : '#888');
      document.getElementById('wg-emb-rewrite-delta').innerHTML =
        'Score: ' + (data.original_score?.toFixed(1) || '—') + ' → ' + (data.rewritten_score?.toFixed(1) || '—') +
        ' <span style="color:' + deltaColor + '">(' + (delta > 0 ? '+' : '') + delta.toFixed(1) + ')</span>';

      const changeCount = data.change_count || 0;
      document.getElementById('wg-emb-rewrite-changes').textContent =
        changeCount > 0 ? changeCount + ' fix' + (changeCount > 1 ? 'es' : '') + ' applied' : 'No deterministic fixes needed';

      document.getElementById('wg-emb-rewrite-text').value = data.rewritten_text || '';
      setRewriteTab('source');
      modal.style.display = 'block';
      statusEl.textContent = 'Rewrite complete.';
      rewriteBtn.disabled = false;
    } catch (err) {
      statusEl.textContent = 'Rewrite error: ' + err.message;
      rewriteBtn.disabled = false;
    }
  }

  function setRewriteTab(mode) {
    const sourceEl = document.getElementById('wg-emb-rewrite-text');
    const previewEl = document.getElementById('wg-emb-rewrite-preview');
    const sourceTab = document.getElementById('wg-emb-rewrite-tab-source');
    const previewTab = document.getElementById('wg-emb-rewrite-tab-preview');
    if (!sourceEl || !previewEl) return;
    if (mode === 'preview') {
      sourceEl.style.display = 'none';
      previewEl.style.display = 'block';
      previewEl.innerHTML = renderMarkdown(sourceEl.value);
      sourceTab.classList.remove('active');
      previewTab.classList.add('active');
      if (sourceTab.style) sourceTab.style.color = 'var(--text-muted)';
      if (previewTab.style) previewTab.style.color = '#fff';
    } else {
      sourceEl.style.display = 'block';
      previewEl.style.display = 'none';
      sourceTab.classList.add('active');
      previewTab.classList.remove('active');
      if (sourceTab.style) sourceTab.style.color = '#fff';
      if (previewTab.style) previewTab.style.color = 'var(--text-muted)';
    }
  }

  // ── Accept rewrite ───────────────────────────────────────────────────
  async function acceptRewrite(opts) {
    const text = document.getElementById('wg-emb-rewrite-text').value;
    if (!text) return;

    const statusEl = document.getElementById('wg-emb-status');
    statusEl.textContent = 'Saving draft…';

    // If we have a section_id, insert a new draft row
    if (opts.sectionId) {
      try {
        const resp = await fetch('/api/govcon/drafts/' + opts.draftId + '/rewrite-save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ draft_content: text, reviewed_by: 'writeguard_inline', reason: 'Accepted WriteGuard rewrite' }),
        });
        const data = await resp.json();
        if (!resp.ok || data.error) {
          statusEl.textContent = data.error || 'Save failed';
          return;
        }
        statusEl.textContent = 'Draft saved. Refresh to see updated content.';
        document.getElementById('wg-emb-rewrite-modal').style.display = 'none';
        // Optionally reload page or update draft render
        if (typeof location !== 'undefined') location.reload();
      } catch (err) {
        statusEl.textContent = 'Save error: ' + err.message;
      }
    } else {
      // No backend target — just copy to clipboard or alert
      statusEl.textContent = 'No draft target. Copied to clipboard.';
      if (typeof navigator !== 'undefined' && navigator.clipboard) {
        navigator.clipboard.writeText(text);
      }
    }
  }

  // ── Lightweight grammar check (mini action) ────────────────────────
  async function runGrammarCheck(text, onResult) {
    try {
      const resp = await fetch(API.analyze, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, mode: 'default', dimensions: ['grammar', 'spelling', 'punctuation'] }),
      });
      const data = await resp.json();
      if (!resp.ok) { onResult({ error: data.error || 'Check failed' }); return; }
      const grammarFindings = (data.findings || []).filter(function (f) {
        return ['grammar', 'spelling', 'punctuation'].includes(f.category);
      });
      onResult({
        score: data.overall_score,
        findings: grammarFindings,
        dimensions: data.dimensions,
      });
    } catch (err) {
      onResult({ error: err.message });
    }
  }

  // ── Export ───────────────────────────────────────────────────────────
  window.ICDEV = window.ICDEV || {};
  window.ICDEV.writeguard = {
    renderTab: renderTab,
    analyze: runAnalyze,
    rewrite: runRewrite,
    setRewriteTab: setRewriteTab,
    grammarCheck: runGrammarCheck,
    renderMarkdown: renderMarkdown,
  };
})();
