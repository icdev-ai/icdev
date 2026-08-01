/* DIC Tech Writer — WriteGuard Inline Sidebar
 * Provides real-time WriteGuard analysis for techwriter documents.
 * Monitors the focused textarea[data-section-id] and calls /api/writeguard/analyze.
 *
 * Usage:
 *   DICTechWriterSidebar.init({ sidebarId, mode, debounceMs });
 */
(function (global) {
  'use strict';

  var _sidebarId = '';
  var _mode = 'default';
  var _debounceMs = 1500;
  var _debounceTimer = null;
  var _activeTextarea = null;
  var _lastText = '';
  var _researchWarnings = [];

  // ── Severity colours ──────────────────────────────────────────────────────
  var SEV_COLOR = { critical: '#f87171', high: '#fb923c', medium: '#fbbf24', low: '#60a5fa', suggestion: '#a78bfa' };
  var SEV_BG    = { critical: '#2e0505', high: '#2e1405', medium: '#2e1a05', low: '#050d2e', suggestion: '#1a0a2e' };

  // ── Sidebar element ───────────────────────────────────────────────────────
  function _sidebar() { return document.getElementById(_sidebarId); }

  function _setContent(html) {
    var el = _sidebar();
    if (el) {
      var body = el.querySelector('.wg-sb-body');
      if (body) body.innerHTML = html;
    }
  }

  // ── Score ring (SVG donut) ────────────────────────────────────────────────
  function _scoreRing(score) {
    var pct = Math.max(0, Math.min(100, score || 0));
    var color = pct >= 80 ? '#4ade80' : pct >= 60 ? '#fbbf24' : '#f87171';
    var r = 20, circ = 2 * Math.PI * r;
    var dash = (pct / 100) * circ;
    return '<svg width="52" height="52" viewBox="0 0 52 52">' +
      '<circle cx="26" cy="26" r="' + r + '" fill="none" stroke="#1e293b" stroke-width="5"/>' +
      '<circle cx="26" cy="26" r="' + r + '" fill="none" stroke="' + color + '" stroke-width="5"' +
      ' stroke-dasharray="' + dash.toFixed(1) + ' ' + circ.toFixed(1) + '"' +
      ' stroke-linecap="round" transform="rotate(-90 26 26)"/>' +
      '<text x="26" y="31" text-anchor="middle" fill="' + color + '" font-size="11" font-weight="700">' + pct + '</text>' +
      '</svg>';
  }

  // ── Research warnings (standards check, placeholders) ────────────────────
  function _warningsHtml() {
    if (!_researchWarnings.length) return '';
    var html = '<div class="wg-sb-warnings" style="background:#2e1a05;border-left:3px solid #fbbf24;border-radius:0 4px 4px 0;padding:7px 9px;margin-bottom:10px;font-size:11px;">' +
      '<div style="color:#fbbf24;font-weight:600;margin-bottom:3px;">DRAFT WARNINGS</div>';
    _researchWarnings.slice(0, 8).forEach(function (w) {
      html += '<div style="color:#b8d0e8;margin-bottom:3px;">⚠ ' + _esc(w) + '</div>';
    });
    html += '</div>';
    return html;
  }

  // ── Render findings ───────────────────────────────────────────────────────
  function _renderFindings(data) {
    if (!data) { _setContent(_warningsHtml() + '<p style="color:#5a7aa0;font-size:11px;margin:0;">No analysis yet.</p>'); return; }

    var score = data.overall_score || 0;
    var html = _warningsHtml() +
      '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">' +
      _scoreRing(score) +
      '<div><div style="color:#eaeaea;font-size:12px;font-weight:600;">' +
      (data.passed ? '✓ Passed' : '✗ Needs work') + '</div>' +
      '<div style="color:#7a8cb0;font-size:10px;">Mode: ' + (_mode || 'default').replace(/_/g, ' ') + '</div>' +
      '</div></div>';

    // Collect all findings from details
    var allFindings = [];
    var details = data.details || {};
    Object.keys(details).forEach(function (dim) {
      var d = details[dim];
      if (d && d.findings && !d.suppressed) {
        d.findings.forEach(function (f) {
          allFindings.push({ dim: dim, severity: f.severity || 'low', message: f.message || '', suggestion: f.suggestion || '', id: Math.random().toString(36).slice(2) });
        });
      }
    });

    // Sort by severity
    var sevOrder = { critical: 0, high: 1, medium: 2, low: 3, suggestion: 4 };
    allFindings.sort(function (a, b) { return (sevOrder[a.severity] || 4) - (sevOrder[b.severity] || 4); });

    if (allFindings.length === 0) {
      html += '<p style="color:#4ade80;font-size:11px;margin:0;">✓ No issues found.</p>';
    } else {
      allFindings.forEach(function (f) {
        var c = SEV_COLOR[f.severity] || '#94a3b8';
        var bg = SEV_BG[f.severity] || '#1e293b';
        html += '<div style="background:' + bg + ';border-left:3px solid ' + c + ';border-radius:0 4px 4px 0;padding:7px 9px;margin-bottom:7px;font-size:11px;">' +
          '<div style="color:' + c + ';font-weight:600;margin-bottom:2px;">' + (f.severity || 'low').toUpperCase() + ' · ' + f.dim.replace(/_/g, ' ') + '</div>' +
          '<div style="color:#b8d0e8;margin-bottom:4px;">' + _esc(f.message) + '</div>';
        if (f.suggestion) {
          html += '<div style="color:#7a8cb0;font-style:italic;margin-bottom:4px;">' + _esc(f.suggestion) + '</div>';
          if (_activeTextarea) {
            html += '<button onclick="DICTechWriterSidebar.applyFix(' + JSON.stringify(f.message) + ')"' +
              ' style="background:#1a2e52;color:#7ab3f0;border:1px solid #1e3a6e;border-radius:3px;padding:2px 8px;cursor:pointer;font-size:10px;">Apply fix</button>';
          }
        }
        html += '</div>';
      });
    }

    if (data.recommendations && data.recommendations.length) {
      html += '<div style="margin-top:8px;border-top:1px solid #1a3560;padding-top:8px;">';
      data.recommendations.slice(0, 3).forEach(function (r) {
        html += '<div style="color:#7a8cb0;font-size:10px;margin-bottom:4px;">• ' + _esc(r) + '</div>';
      });
      html += '</div>';
    }

    _setContent(html);
  }

  function _esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Research warnings (standards-reference validation etc.) ──────────────
  var _researchWarnings = [];

  function _warningsHtml() {
    if (!_researchWarnings.length) return '';
    var html = '<div style="background:#2e1a05;border-left:3px solid #fbbf24;border-radius:0 4px 4px 0;padding:7px 9px;margin-bottom:10px;font-size:11px;">' +
      '<div style="color:#fbbf24;font-weight:600;margin-bottom:4px;">⚠ Research warnings (' + _researchWarnings.length + ')</div>';
    _researchWarnings.forEach(function (w) {
      html += '<div style="color:#b8d0e8;margin-bottom:3px;">• ' + _esc(w) + '</div>';
    });
    html += '</div>';
    return html;
  }

  function _renderWarnings() {
    var el = _sidebar();
    if (!el) return;
    var box = el.querySelector('.wg-sb-warnings');
    if (!box) {
      box = document.createElement('div');
      box.className = 'wg-sb-warnings';
      var body = el.querySelector('.wg-sb-body');
      if (body && body.parentNode) body.parentNode.insertBefore(box, body);
      else el.appendChild(box);
    }
    box.innerHTML = _warningsHtml();
  }

  // ── Analysis call ─────────────────────────────────────────────────────────
  function _analyze() {
    var text = _activeTextarea ? _activeTextarea.value : '';
    if (!text || text === _lastText) return;
    _lastText = text;

    _setContent('<p style="color:#5a7aa0;font-size:11px;margin:0;">Analyzing…</p>');

    fetch('/api/writeguard/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text, mode: _mode })
    })
    .then(function (r) { return r.json(); })
    .then(function (data) { _renderFindings(data); })
    .catch(function () {
      _setContent('<p style="color:#f87171;font-size:11px;margin:0;">WriteGuard unavailable.</p>');
    });
  }

  // ── Debounced input handler ───────────────────────────────────────────────
  function _onInput() {
    if (_debounceTimer) clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(_analyze, _debounceMs);
  }

  // ── Bind a single textarea ────────────────────────────────────────────────
  var _boundSet = new WeakSet ? new WeakSet() : null;

  function _bindOne(ta) {
    if (_boundSet && _boundSet.has(ta)) return;
    if (_boundSet) _boundSet.add(ta);
    ta.addEventListener('focus', function () {
      _activeTextarea = ta;
      _lastText = '';  // force re-analysis on focus change
    });
    ta.addEventListener('input', _onInput);
  }

  // ── Bind to section textareas (initial + dynamic via MutationObserver) ────
  function _bindTextareas() {
    document.querySelectorAll('textarea[data-section-id]').forEach(_bindOne);

    if (window.MutationObserver) {
      var observer = new MutationObserver(function (mutations) {
        mutations.forEach(function (m) {
          m.addedNodes.forEach(function (node) {
            if (node.nodeType !== 1) return;
            if (node.tagName === 'TEXTAREA' && node.dataset && node.dataset.sectionId) {
              _bindOne(node);
            }
            node.querySelectorAll && node.querySelectorAll('textarea[data-section-id]').forEach(_bindOne);
          });
        });
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
  }

  // ── Collapse toggle ───────────────────────────────────────────────────────
  function _toggleCollapse() {
    var el = _sidebar();
    if (!el) return;
    var collapsed = el.classList.toggle('wg-sidebar-collapsed');
    var btn = el.querySelector('.wg-sb-toggle');
    if (btn) btn.textContent = collapsed ? '▶' : '◀';
  }

  // ── Public API ────────────────────────────────────────────────────────────
  global.DICTechWriterSidebar = {
    init: function (opts) {
      _sidebarId  = opts.sidebarId  || 'wg-sidebar';
      _mode       = opts.mode       || 'default';
      _debounceMs = opts.debounceMs || 1500;
      _bindTextareas();

      var toggleBtn = document.getElementById('wg-sidebar-toggle');
      if (toggleBtn) toggleBtn.addEventListener('click', _toggleCollapse);

      _setContent('<p style="color:#5a7aa0;font-size:11px;margin:0;">Start typing in a section to see WriteGuard feedback.</p>');
    },

    applyFix: function (findingMessage) {
      if (!_activeTextarea) return;
      var text = _activeTextarea.value;
      fetch('/api/writeguard/rewrite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text, mode: _mode, targets: [findingMessage] })
      })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.rewritten_text) {
          _activeTextarea.value = data.rewritten_text;
          _lastText = '';
          _analyze();
        }
      })
      .catch(function () {});
    },

    // Surface ResearchResult.warnings (standards check, unresolved
    // placeholders) from /api/techwriter/research in the sidebar.
    showWarnings: function (warnings) {
      _researchWarnings = (warnings || []).filter(Boolean);
      var el = _sidebar();
      var body = el && el.querySelector('.wg-sb-body');
      if (!body) return;
      var existing = body.querySelector('.wg-sb-warnings');
      if (existing) existing.parentNode.removeChild(existing);
      if (_researchWarnings.length) body.insertAdjacentHTML('afterbegin', _warningsHtml());
    },

    setMode: function (mode) {
      _mode = mode;
      _lastText = '';
      _analyze();
    },

    // Surface ResearchResult.warnings (e.g. standards-reference validation)
    // in the sidebar, above WriteGuard analysis findings.
    showWarnings: function (warnings) {
      _researchWarnings = Array.isArray(warnings) ? warnings : [];
      _renderWarnings();
    }
  };

}(window));
