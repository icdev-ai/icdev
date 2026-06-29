/* ICDEV Slide Deck — Excalidraw-style Sketch Renderer (via rough.js)
 * Requires rough.js v4.6.6 UMD (window.rough) loaded before this script.
 * Exposes: window.ICDEVExcalidrawRenderer
 *
 * Accepts standard Excalidraw element format JSON arrays.
 * Canvas size: 800 × 450 (elements authored in this coordinate space).
 */
(function (global) {
  'use strict';

  var ICDEVExcalidrawRenderer = {

    /* render(svgElement, elements) → void
     * svgElement: an <svg> DOM element (800×450 viewBox recommended)
     * elements: Excalidraw-format element array
     */
    render: function (svgEl, elements) {
      if (!window.rough) {
        svgEl.innerHTML = '<text x="20" y="40" fill="#c8d2dc" font-size="16" font-family="sans-serif">rough.js not loaded — sketch unavailable</text>';
        return;
      }
      if (!Array.isArray(elements) || !elements.length) return;

      // Ensure SVG has dimensions
      if (!svgEl.getAttribute('viewBox')) {
        svgEl.setAttribute('viewBox', '0 0 800 450');
      }
      svgEl.setAttribute('width', svgEl.getAttribute('width') || '100%');
      svgEl.setAttribute('height', svgEl.getAttribute('height') || '100%');
      svgEl.innerHTML = '';

      var rc = rough.svg(svgEl);

      // Render non-text elements first (z-order: shapes below text)
      elements.forEach(function (el) {
        if (el.type === 'text') return;
        renderElement(rc, svgEl, el);
      });

      // Render text elements on top
      elements.forEach(function (el) {
        if (el.type !== 'text') return;
        renderText(svgEl, el);
      });
    }
  };

  function roughOpts(el) {
    return {
      stroke: el.strokeColor || '#e8e8e8',
      strokeWidth: el.strokeWidth || 2,
      fill: (el.backgroundColor && el.backgroundColor !== 'transparent')
        ? el.backgroundColor : undefined,
      fillStyle: el.fillStyle || 'hachure',
      roughness: el.roughness !== undefined ? el.roughness : 1,
      bowing: 1,
    };
  }

  function renderElement(rc, svgEl, el) {
    var node = null;
    var opts = roughOpts(el);
    var x = el.x || 0, y = el.y || 0;
    var w = el.width || 100, h = el.height || 60;

    try {
      if (el.type === 'rectangle') {
        node = rc.rectangle(x, y, w, h, opts);

      } else if (el.type === 'ellipse') {
        node = rc.ellipse(x + w / 2, y + h / 2, w, h, opts);

      } else if (el.type === 'diamond') {
        var pts = [
          [x + w / 2, y],
          [x + w, y + h / 2],
          [x + w / 2, y + h],
          [x, y + h / 2],
          [x + w / 2, y],
        ];
        node = rc.linearPath(pts, opts);

      } else if (el.type === 'arrow' || el.type === 'line') {
        var pts2 = el.points || [[0, 0], [w, 0]];
        var p0 = [x + pts2[0][0], y + pts2[0][1]];
        var p1 = [x + pts2[pts2.length - 1][0], y + pts2[pts2.length - 1][1]];
        node = rc.line(p0[0], p0[1], p1[0], p1[1], opts);

        if (el.type === 'arrow') {
          // Arrowhead
          var dx = p1[0] - p0[0], dy = p1[1] - p0[1];
          var len = Math.sqrt(dx * dx + dy * dy) || 1;
          var ux = dx / len, uy = dy / len;
          var ahLen = 12;
          var ahAngle = Math.PI / 6;
          var ah1 = [
            p1[0] - ahLen * (ux * Math.cos(ahAngle) - uy * Math.sin(ahAngle)),
            p1[1] - ahLen * (uy * Math.cos(ahAngle) + ux * Math.sin(ahAngle)),
          ];
          var ah2 = [
            p1[0] - ahLen * (ux * Math.cos(ahAngle) + uy * Math.sin(ahAngle)),
            p1[1] - ahLen * (uy * Math.cos(ahAngle) - ux * Math.sin(ahAngle)),
          ];
          var ah1node = rc.line(p1[0], p1[1], ah1[0], ah1[1], opts);
          var ah2node = rc.line(p1[0], p1[1], ah2[0], ah2[1], opts);
          svgEl.appendChild(ah1node);
          svgEl.appendChild(ah2node);
        }
      }

      if (node) {
        if (el.opacity !== undefined && el.opacity < 100) {
          node.style = node.style || '';
          node.setAttribute('opacity', el.opacity / 100);
        }
        svgEl.appendChild(node);
      }
    } catch (err) {
      // Silently skip malformed elements
    }
  }

  function renderText(svgEl, el) {
    var svgNS = 'http://www.w3.org/2000/svg';
    var txt = document.createElementNS(svgNS, 'text');
    txt.setAttribute('x', el.x || 0);
    txt.setAttribute('y', (el.y || 0) + (el.fontSize || 18));
    txt.setAttribute('fill', el.strokeColor || '#ffffff');
    txt.setAttribute('font-size', el.fontSize || 18);
    txt.setAttribute('font-family', el.fontFamily || 'system-ui, "Segoe UI", sans-serif');
    if (el.fontStyle === 'italic') txt.setAttribute('font-style', 'italic');
    if (el.fontWeight === 'bold' || el.fontWeight === '700') txt.setAttribute('font-weight', 'bold');
    txt.textContent = el.text || '';
    svgEl.appendChild(txt);
  }

  global.ICDEVExcalidrawRenderer = ICDEVExcalidrawRenderer;
}(window));
