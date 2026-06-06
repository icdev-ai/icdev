/* ── canvas-tooltips.js ─────────────────────────────────────────────────────────
 * Enhanced hover tooltips for the NDC canvas. Adds link-level tooltips that the
 * existing _initHeatmapTooltip IIFE in network-canvas.js does not cover (it
 * short-circuits on non-elements). Node tooltips remain owned by that IIFE —
 * this module only fills the link gap and provides a graceful fallback when
 * paper/graph aren't ready yet.
 *
 * Entry point: initEnhancedTooltips(paper, graph, getStyle)
 * Called from network-canvas.js:initCanvasTooltips() during initCanvas().
 */
(function () {
  'use strict';

  function _esc(s) {
    var d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function _ensureTip() {
    var id = 'nc-link-tooltip';
    var el = document.getElementById(id);
    if (el) return el;
    el = document.createElement('div');
    el.id = id;
    el.className = 'nc-link-tooltip';
    el.style.cssText = [
      'position:fixed', 'z-index:350', 'pointer-events:none',
      'display:none', 'max-width:320px',
      'background:#0f1e35', 'color:#eaeaea',
      'border:1px solid #1e3a6e', 'border-radius:6px',
      'padding:8px 10px', 'font-size:11px',
      'font-family:Segoe UI, system-ui, sans-serif',
      'box-shadow:0 4px 16px rgba(0,0,0,0.5)'
    ].join(';');
    document.body.appendChild(el);
    return el;
  }

  function _endpointLabel(graph, endpointRef) {
    if (!endpointRef) return '(none)';
    var cell = endpointRef.id ? graph.getCell(endpointRef.id) : null;
    if (!cell) return '(orphan: ' + _esc(endpointRef.id || '?') + ')';
    var lbl = cell.attr && cell.attr('label/text');
    return lbl || cell.id;
  }

  function _linkHtml(link, graph) {
    var src = _endpointLabel(graph, link.get('source'));
    var tgt = _endpointLabel(graph, link.get('target'));
    var protocol = link.get('protocol') || '';
    var labels = link.get('labels') || [];
    var labelText = '';
    if (labels.length && labels[0].attrs && labels[0].attrs.text) {
      labelText = labels[0].attrs.text.text || '';
    }
    var cable = link.get('cableData') || {};
    var config = link.get('linkConfig') || {};

    var html = '<div style="font-weight:600;margin-bottom:4px;color:#4fc3f7;">Link</div>';
    html += '<div><span style="color:#8899aa;">From:</span> ' + _esc(src) + '</div>';
    html += '<div><span style="color:#8899aa;">To:</span> ' + _esc(tgt) + '</div>';
    if (protocol) html += '<div><span style="color:#8899aa;">Protocol:</span> ' + _esc(protocol) + '</div>';
    if (labelText) html += '<div><span style="color:#8899aa;">Label:</span> ' + _esc(labelText) + '</div>';
    if (cable.type) html += '<div><span style="color:#8899aa;">Cable:</span> ' + _esc(cable.type) + '</div>';
    if (config.bandwidth) html += '<div><span style="color:#8899aa;">Bandwidth:</span> ' + _esc(config.bandwidth) + '</div>';
    if (config.vlan) html += '<div><span style="color:#8899aa;">VLAN:</span> ' + _esc(config.vlan) + '</div>';
    return html;
  }

  function initEnhancedTooltips(paper, graph, _getStyle) {
    if (!paper || !graph) {
      console.warn('initEnhancedTooltips: paper/graph not ready, skipping');
      return;
    }
    var tip = _ensureTip();

    document.addEventListener('mousemove', function (e) {
      if (tip.style.display === 'block') {
        tip.style.left = (e.clientX + 12) + 'px';
        tip.style.top = (e.clientY - 8) + 'px';
      }
    });

    paper.on('link:mouseenter', function (linkView) {
      var link = linkView.model;
      if (!link || !link.isLink()) return;
      tip.innerHTML = _linkHtml(link, graph);
      tip.style.display = 'block';
    });
    paper.on('link:mouseleave', function () {
      tip.style.display = 'none';
    });
  }

  // Expose on window so network-canvas.js's typeof guard picks it up
  window.initEnhancedTooltips = initEnhancedTooltips;
})();
