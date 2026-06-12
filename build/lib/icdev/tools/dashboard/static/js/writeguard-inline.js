/**
 * WriteGuard Inline Widget (D-WG-9a)
 *
 * Embeds live quality scoring into any textarea via data-writeguard attribute.
 * Shows a floating badge with score + finding count, debounced API calls.
 *
 * Usage:
 *   <textarea data-writeguard="true"></textarea>
 *   <script src="/static/js/writeguard-inline.js"></script>
 *
 * Options (data attributes):
 *   data-writeguard-debounce="800"   — ms delay after last keystroke (default: 500)
 *   data-writeguard-min-words="20"   — minimum words before analysis (default: 15)
 *   data-writeguard-position="top-right" — badge position (default: top-right)
 *
 * CUI // SP-CTI
 */
(function () {
  'use strict';

  // ── Configuration ──────────────────────────────────────────────────
  const API_URL = '/api/writeguard/analyze';
  const DEFAULT_DEBOUNCE = 500;
  const DEFAULT_MIN_WORDS = 15;

  // ── Inject styles ──────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    .wgi-wrapper {
      position: relative;
      display: inline-block;
      width: 100%;
    }
    .wgi-badge {
      position: absolute;
      top: 6px;
      right: 6px;
      display: flex;
      align-items: center;
      gap: 4px;
      padding: 3px 10px;
      border-radius: 12px;
      font-size: 0.72rem;
      font-weight: 700;
      cursor: pointer;
      z-index: 10;
      transition: opacity 0.3s, transform 0.2s;
      box-shadow: 0 1px 4px rgba(0,0,0,0.3);
      user-select: none;
    }
    .wgi-badge:hover { transform: scale(1.05); }
    .wgi-badge.good  { background: rgba(0,200,100,0.2); color: #00c864; border: 1px solid #00c864; }
    .wgi-badge.warn  { background: rgba(255,165,0,0.2); color: #ffa500; border: 1px solid #ffa500; }
    .wgi-badge.bad   { background: rgba(229,62,62,0.2); color: #e53e3e; border: 1px solid #e53e3e; }
    .wgi-badge.idle  { background: rgba(136,136,136,0.15); color: #888; border: 1px solid #555; }
    .wgi-badge .wgi-spin {
      width: 10px; height: 10px;
      border: 2px solid rgba(255,255,255,0.3);
      border-top-color: currentColor;
      border-radius: 50%;
      animation: wgi-spin 0.6s linear infinite;
    }
    @keyframes wgi-spin { to { transform: rotate(360deg); } }
    .wgi-tooltip {
      display: none;
      position: absolute;
      top: 30px;
      right: 0;
      background: #1e1e2e;
      border: 1px solid #333;
      border-radius: 8px;
      padding: 10px 14px;
      font-size: 0.78rem;
      color: #e0e0e0;
      min-width: 200px;
      max-width: 300px;
      z-index: 20;
      box-shadow: 0 4px 12px rgba(0,0,0,0.4);
      line-height: 1.5;
    }
    .wgi-tooltip.visible { display: block; }
    .wgi-tooltip-title { font-weight: 700; margin-bottom: 4px; }
    .wgi-tooltip-dim { display: flex; justify-content: space-between; padding: 1px 0; }
    .wgi-tooltip-dim-name { color: #aaa; }
    .wgi-tooltip-dim-score { font-weight: 600; }
    .wgi-tooltip-findings { margin-top: 6px; padding-top: 6px; border-top: 1px solid #333; font-size: 0.72rem; color: #aaa; }
  `;
  document.head.appendChild(style);

  // ── Init ────────────────────────────────────────────────────────────
  function initWidget(textarea) {
    if (textarea._wgiInit) return;
    textarea._wgiInit = true;

    const debounceMs = parseInt(textarea.dataset.writeguardDebounce || DEFAULT_DEBOUNCE, 10);
    const minWords = parseInt(textarea.dataset.writeguardMinWords || DEFAULT_MIN_WORDS, 10);

    // Wrap textarea
    const wrapper = document.createElement('div');
    wrapper.className = 'wgi-wrapper';
    textarea.parentNode.insertBefore(wrapper, textarea);
    wrapper.appendChild(textarea);

    // Badge
    const badge = document.createElement('div');
    badge.className = 'wgi-badge idle';
    badge.innerHTML = 'WG';
    badge.title = 'WriteGuard — click for details';
    wrapper.appendChild(badge);

    // Tooltip
    const tooltip = document.createElement('div');
    tooltip.className = 'wgi-tooltip';
    tooltip.innerHTML = '<div class="wgi-tooltip-title">WriteGuard</div><div>Type at least ' + minWords + ' words to analyze...</div>';
    wrapper.appendChild(tooltip);

    // Toggle tooltip
    badge.addEventListener('click', function (e) {
      e.stopPropagation();
      tooltip.classList.toggle('visible');
    });
    document.addEventListener('click', function () {
      tooltip.classList.remove('visible');
    });

    // Debounced analysis
    let timer = null;
    let lastHash = '';

    textarea.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        const text = textarea.value.trim();
        const wordCount = text ? text.split(/\s+/).length : 0;

        if (wordCount < minWords) {
          badge.className = 'wgi-badge idle';
          badge.innerHTML = 'WG';
          return;
        }

        // Skip if text hasn't changed
        const hash = text.length + ':' + text.substring(0, 50);
        if (hash === lastHash) return;
        lastHash = hash;

        // Show loading
        badge.className = 'wgi-badge idle';
        badge.innerHTML = '<div class="wgi-spin"></div>';

        fetch(API_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: text }),
        })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.error) {
              badge.className = 'wgi-badge idle';
              badge.innerHTML = 'WG';
              return;
            }
            renderBadge(badge, tooltip, data);
          })
          .catch(function () {
            badge.className = 'wgi-badge idle';
            badge.innerHTML = 'WG';
          });
      }, debounceMs);
    });
  }

  function renderBadge(badge, tooltip, data) {
    var score = typeof data.overall_score === 'number' ? data.overall_score : 0;
    var findings = (data.findings || []).length;

    // Badge color + text
    var cls = score >= 75 ? 'good' : (score >= 50 ? 'warn' : 'bad');
    badge.className = 'wgi-badge ' + cls;
    badge.innerHTML = Math.round(score) + (findings > 0 ? ' <span style="opacity:0.7">(' + findings + ')</span>' : '');

    // Tooltip content
    var html = '<div class="wgi-tooltip-title">WriteGuard: ' + score.toFixed(1) + '</div>';
    var dims = data.dimensions || {};
    Object.keys(dims).forEach(function (k) {
      var d = dims[k];
      if (d.status === 'unavailable') return;
      var s = typeof d.score === 'number' ? d.score : 0;
      var color = s >= 75 ? '#00c864' : (s >= 50 ? '#ffa500' : '#e53e3e');
      html += '<div class="wgi-tooltip-dim"><span class="wgi-tooltip-dim-name">' +
        escHtml(d.label || k) + '</span><span class="wgi-tooltip-dim-score" style="color:' +
        color + '">' + Math.round(s) + '</span></div>';
    });

    if (findings > 0) {
      html += '<div class="wgi-tooltip-findings">' + findings + ' finding(s) — ';
      html += '<a href="/writeguard" target="_blank" style="color:#4a9eff;text-decoration:none;">open full report</a></div>';
    }

    tooltip.innerHTML = html;
  }

  function escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── Auto-init all data-writeguard textareas ─────────────────────────
  function scanAndInit() {
    document.querySelectorAll('textarea[data-writeguard]').forEach(initWidget);
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scanAndInit);
  } else {
    scanAndInit();
  }

  // Observe for dynamically added textareas
  var observer = new MutationObserver(function (mutations) {
    mutations.forEach(function (m) {
      m.addedNodes.forEach(function (node) {
        if (node.nodeType === 1) {
          if (node.matches && node.matches('textarea[data-writeguard]')) {
            initWidget(node);
          }
          if (node.querySelectorAll) {
            node.querySelectorAll('textarea[data-writeguard]').forEach(initWidget);
          }
        }
      });
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });

  // Export for programmatic use
  window.WriteGuardInline = { init: initWidget, scan: scanAndInit };
})();
