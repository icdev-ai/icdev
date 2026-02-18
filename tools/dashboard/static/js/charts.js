// CUI // SP-CTI
// ICDEV Dashboard — SVG Chart Library (Zero Dependencies)
// Classification: CUI // SP-CTI
(function () {
  'use strict';

  var NS = window.ICDEV || (window.ICDEV = {});

  // ─── Helpers ─────────────────────────────────────────────────────────────────────

  var _tooltipEl = null;

  /** Delegate to shared ICDEV.escapeHTML (api.js). */
  function escapeHTML(str) {
    return ICDEV.escapeHTML ? ICDEV.escapeHTML(str) : String(str || '');
  }

  function cssVar(name, fallback) {
    var val = getComputedStyle(document.documentElement).getPropertyValue(name);
    return val && val.trim() ? val.trim() : fallback;
  }

  function palette(key) {
    var map = {
      bg:        ['--bg-card',          '#16213e'],
      border:    ['--border-color',     '#2a2a40'],
      muted:     ['--text-muted',       '#6c6c80'],
      secondary: ['--text-secondary',   '#a0a0b8'],
      blue:      ['--accent-blue',      '#4a90d9'],
      blueLight: ['--accent-blue-light','#6db3f8'],
      green:     ['--status-green',     '#28a745'],
      red:       ['--status-red',       '#dc3545'],
      yellow:    ['--status-yellow',    '#ffc107']
    };
    var entry = map[key];
    return entry ? cssVar(entry[0], entry[1]) : '#ffffff';
  }

  function svgEl(tag, attrs) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    }
    return el;
  }

  function niceScale(minVal, maxVal, ticks) {
    ticks = ticks || 5;
    if (maxVal === minVal) { maxVal = minVal + 1; }
    var range = maxVal - minVal;
    var rough = range / ticks;
    var mag = Math.pow(10, Math.floor(Math.log10(rough)));
    var residual = rough / mag;
    var nice;
    if (residual <= 1.5) nice = 1;
    else if (residual <= 3) nice = 2;
    else if (residual <= 7) nice = 5;
    else nice = 10;
    var step = nice * mag;
    var lo = Math.floor(minVal / step) * step;
    var hi = Math.ceil(maxVal / step) * step;
    var values = [];
    for (var v = lo; v <= hi + step * 0.01; v += step) {
      values.push(Math.round(v * 1e10) / 1e10);
    }
    return { min: lo, max: hi, step: step, values: values };
  }

  function getTooltip() {
    if (_tooltipEl) return _tooltipEl;
    _tooltipEl = document.createElement('div');
    _tooltipEl.style.cssText =
      'position:fixed;pointer-events:none;z-index:99999;padding:6px 10px;' +
      'border-radius:4px;font-size:12px;line-height:1.5;white-space:nowrap;' +
      'opacity:0;transition:opacity .15s;' +
      'background:' + palette('bg') + ';border:1px solid ' + palette('border') + ';' +
      'color:' + palette('secondary') + ';box-shadow:0 2px 8px rgba(0,0,0,.4);';
    document.body.appendChild(_tooltipEl);
    return _tooltipEl;
  }

  function showTooltip(html, evt) {
    var tip = getTooltip();
    tip.innerHTML = html;
    tip.style.opacity = '1';
    var x = evt.clientX + 12;
    var y = evt.clientY - 10;
    var rect = tip.getBoundingClientRect();
    if (x + rect.width > window.innerWidth - 8) x = evt.clientX - rect.width - 12;
    if (y + rect.height > window.innerHeight - 8) y = evt.clientY - rect.height - 10;
    if (y < 4) y = 4;
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  }

  function hideTooltip() {
    var tip = getTooltip();
    tip.style.opacity = '0';
  }

  function ensureAnimStyle() {
    if (document.getElementById('icdev-chart-anim')) return;
    var style = document.createElement('style');
    style.id = 'icdev-chart-anim';
    style.textContent =
      '@keyframes icdev-line-draw{from{stroke-dashoffset:var(--dash-len)}to{stroke-dashoffset:0}}' +
      '@keyframes icdev-bar-grow{from{transform:scaleY(0)}to{transform:scaleY(1)}}' +
      '@keyframes icdev-donut-draw{from{stroke-dashoffset:var(--seg-len)}to{stroke-dashoffset:0}}' +
      '@keyframes icdev-gauge-draw{from{stroke-dashoffset:var(--gauge-len)}to{stroke-dashoffset:0}}';
    document.head.appendChild(style);
  }

  function clearContainer(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    el.innerHTML = '';
    return el;
  }

  function buildLegend(series, toggleCb) {
    var wrap = document.createElement('div');
    wrap.style.cssText =
      'display:flex;flex-wrap:wrap;gap:12px;justify-content:center;' +
      'margin-top:8px;font-size:12px;color:' + palette('secondary') + ';';
    series.forEach(function (s, i) {
      var item = document.createElement('span');
      item.style.cssText = 'display:inline-flex;align-items:center;gap:4px;cursor:pointer;opacity:1;';
      var swatch = document.createElement('span');
      swatch.style.cssText =
        'width:10px;height:10px;border-radius:2px;display:inline-block;background:' +
        escapeHTML(s.color || palette('blue')) + ';';
      item.appendChild(swatch);
      item.appendChild(document.createTextNode(escapeHTML(s.name || s.label || '')));
      item.addEventListener('click', function () {
        s._hidden = !s._hidden;
        item.style.opacity = s._hidden ? '0.35' : '1';
        if (toggleCb) toggleCb(i, s._hidden);
      });
      wrap.appendChild(item);
    });
    return wrap;
  }

  // ——— 1. Sparkline ———————————————————————————————————————————————————————

  NS.sparkline = function (containerId, data, options) {
    var container = clearContainer(containerId);
    if (!container || !data || !data.length) return;
    var opts = options || {};
    var w = opts.width || 80;
    var h = opts.height || 24;
    var color = opts.color || palette('blue');
    var fillOp = opts.fillOpacity != null ? opts.fillOpacity : 0.15;

    var min = Math.min.apply(null, data);
    var max = Math.max.apply(null, data);
    if (max === min) { max = min + 1; }
    var pad = 1;

    var svg = svgEl('svg', {
      viewBox: '0 0 ' + w + ' ' + h,
      width: w, height: h,
      role: 'img',
      'aria-label': 'Sparkline chart with ' + data.length + ' data points'
    });
    svg.style.display = 'block';

    var uid = 'sp-' + Math.random().toString(36).substr(2, 6);
    var points = data.map(function (v, i) {
      var x = pad + (i / (data.length - 1)) * (w - pad * 2);
      var y = pad + (1 - (v - min) / (max - min)) * (h - pad * 2);
      return x.toFixed(2) + ',' + y.toFixed(2);
    });

    if (fillOp > 0) {
      var defs = svgEl('defs');
      var grad = svgEl('linearGradient', { id: uid, x1: '0', y1: '0', x2: '0', y2: '1' });
      var s1 = svgEl('stop', { offset: '0%', 'stop-color': color, 'stop-opacity': fillOp });
      var s2 = svgEl('stop', { offset: '100%', 'stop-color': color, 'stop-opacity': '0' });
      grad.appendChild(s1);
      grad.appendChild(s2);
      defs.appendChild(grad);
      svg.appendChild(defs);

      var fillPts = points.join(' ') +
        ' ' + (pad + w - pad * 2).toFixed(2) + ',' + h +
        ' ' + pad.toFixed(2) + ',' + h;
      svg.appendChild(svgEl('polygon', {
        points: fillPts, fill: 'url(#' + uid + ')', stroke: 'none'
      }));
    }

    svg.appendChild(svgEl('polyline', {
      points: points.join(' '),
      fill: 'none', stroke: color,
      'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round'
    }));

    container.appendChild(svg);
  };

  // ——— 2. Line Chart ——————————————————————————————————————————————————————

  NS.lineChart = function (containerId, opts) {
    var container = clearContainer(containerId);
    if (!container) return;
    ensureAnimStyle();

    opts = opts || {};
    var series   = opts.series || [];
    var labels   = opts.labels || [];
    var H        = opts.height || 220;
    var W        = container.offsetWidth || 400;
    var padL = 48, padR = 12, padT = 16, padB = 32;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;

    // Determine y-axis scale across all visible series
    var allVals = [];
    series.forEach(function (s) {
      if (!s._hidden) (s.data || []).forEach(function (v) { allVals.push(v); });
    });
    if (!allVals.length) allVals = [0, 1];
    var scale = niceScale(
      Math.min.apply(null, allVals),
      Math.max.apply(null, allVals)
    );

    var svg = svgEl('svg', {
      width: W, height: H, viewBox: '0 0 ' + W + ' ' + H,
      role: 'img', 'aria-label': 'Line chart: ' + series.map(function (s) { return s.name; }).join(', ')
    });
    svg.style.display = 'block';

    // Grid lines + y-axis labels
    scale.values.forEach(function (v) {
      var y = padT + plotH - ((v - scale.min) / (scale.max - scale.min)) * plotH;
      svg.appendChild(svgEl('line', {
        x1: padL, y1: y, x2: W - padR, y2: y,
        stroke: palette('border'), 'stroke-width': '1'
      }));
      var txt = svgEl('text', {
        x: padL - 6, y: y + 4, fill: palette('muted'),
        'font-size': '10', 'text-anchor': 'end'
      });
      txt.textContent = v >= 1000 ? (v / 1000).toFixed(1) + 'k' : String(v);
      svg.appendChild(txt);
    });

    // X-axis labels
    var labelStep = Math.max(1, Math.ceil(labels.length / 10));
    labels.forEach(function (lbl, i) {
      if (i % labelStep !== 0 && i !== labels.length - 1) return;
      var x = padL + (labels.length > 1 ? (i / (labels.length - 1)) * plotW : plotW / 2);
      var txt = svgEl('text', {
        x: x, y: H - 4, fill: palette('muted'),
        'font-size': '10', 'text-anchor': 'middle'
      });
      txt.textContent = escapeHTML(String(lbl));
      svg.appendChild(txt);
    });

    // Draw each series
    series.forEach(function (s, si) {
      if (s._hidden) return;
      var color = s.color || palette('blue');
      var pts = (s.data || []).map(function (v, i) {
        var x = padL + (s.data.length > 1 ? (i / (s.data.length - 1)) * plotW : plotW / 2);
        var y = padT + plotH - ((v - scale.min) / (scale.max - scale.min)) * plotH;
        return { x: x, y: y, v: v };
      });
      if (!pts.length) return;

      // Polyline path
      var pathStr = pts.map(function (p) { return p.x.toFixed(2) + ',' + p.y.toFixed(2); }).join(' ');
      var totalLen = 0;
      for (var k = 1; k < pts.length; k++) {
        totalLen += Math.hypot(pts[k].x - pts[k - 1].x, pts[k].y - pts[k - 1].y);
      }
      var poly = svgEl('polyline', {
        points: pathStr, fill: 'none', stroke: color,
        'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
        'stroke-dasharray': totalLen, 'stroke-dashoffset': totalLen,
        style: '--dash-len:' + totalLen + ';animation:icdev-line-draw .8s ease-out ' + (si * 0.15) + 's forwards'
      });
      svg.appendChild(poly);

      // Data point dots + tooltips
      pts.forEach(function (p, pi) {
        var dot = svgEl('circle', {
          cx: p.x, cy: p.y, r: '3', fill: color, stroke: palette('bg'), 'stroke-width': '1.5',
          style: 'cursor:pointer'
        });
        dot.addEventListener('mouseenter', function (e) {
          dot.setAttribute('r', '5');
          var lbl = labels[pi] != null ? '<b>' + escapeHTML(labels[pi]) + '</b><br>' : '';
          showTooltip(lbl + escapeHTML(s.name) + ': <b>' + p.v + '</b>', e);
        });
        dot.addEventListener('mouseleave', function () { dot.setAttribute('r', '3'); hideTooltip(); });
        dot.addEventListener('mousemove', function (e) { showTooltip(null, e); });
        svg.appendChild(dot);
      });
    });

    container.appendChild(svg);

    // Legend with toggle
    if (opts.showLegend !== false && series.length > 1) {
      container.appendChild(buildLegend(series, function () {
        NS.lineChart(containerId, opts);
      }));
    }
  };

  // ——— 3. Bar Chart ——————————————————————————————————————————————————————

  NS.barChart = function (containerId, opts) {
    var container = clearContainer(containerId);
    if (!container) return;
    ensureAnimStyle();

    opts = opts || {};
    var series   = opts.series || [];
    var labels   = opts.labels || [];
    var H        = opts.height || 220;
    var W        = container.offsetWidth || 400;
    var padL = 48, padR = 12, padT = 16, padB = 32;
    var plotW = W - padL - padR;
    var plotH = H - padT - padB;

    var visibleSeries = series.filter(function (s) { return !s._hidden; });
    var allVals = [0];
    visibleSeries.forEach(function (s) {
      (s.data || []).forEach(function (v) { allVals.push(v); });
    });
    var scale = niceScale(0, Math.max.apply(null, allVals));

    var svg = svgEl('svg', {
      width: W, height: H, viewBox: '0 0 ' + W + ' ' + H,
      role: 'img', 'aria-label': 'Bar chart: ' + series.map(function (s) { return s.name; }).join(', ')
    });
    svg.style.display = 'block';

    // Grid + y-axis
    scale.values.forEach(function (v) {
      var y = padT + plotH - ((v - scale.min) / (scale.max - scale.min)) * plotH;
      svg.appendChild(svgEl('line', {
        x1: padL, y1: y, x2: W - padR, y2: y,
        stroke: palette('border'), 'stroke-width': '1'
      }));
      var txt = svgEl('text', {
        x: padL - 6, y: y + 4, fill: palette('muted'),
        'font-size': '10', 'text-anchor': 'end'
      });
      txt.textContent = v >= 1000 ? (v / 1000).toFixed(1) + 'k' : String(v);
      svg.appendChild(txt);
    });

    var groupCount = labels.length || 1;
    var groupW     = plotW / groupCount;
    var barGap     = 2;
    var serCount   = visibleSeries.length || 1;
    var barW       = Math.max(4, (groupW - barGap * (serCount + 1)) / serCount);
    var baseline   = padT + plotH;

    labels.forEach(function (lbl, gi) {
      // X-axis label
      var gx = padL + gi * groupW + groupW / 2;
      var txt = svgEl('text', {
        x: gx, y: H - 4, fill: palette('muted'),
        'font-size': '10', 'text-anchor': 'middle'
      });
      txt.textContent = escapeHTML(String(lbl));
      svg.appendChild(txt);

      // Bars for each visible series
      visibleSeries.forEach(function (s, si) {
        var val = (s.data || [])[gi] || 0;
        var barH = ((val - scale.min) / (scale.max - scale.min)) * plotH;
        if (barH < 1) barH = 1;
        var bx = padL + gi * groupW + barGap + si * (barW + barGap);
        var by = baseline - barH;
        var color = s.color || palette('blue');

        var rect = svgEl('rect', {
          x: bx, y: by, width: barW, height: barH, rx: '2',
          fill: color,
          'transform-origin': bx + 'px ' + baseline + 'px',
          style: 'animation:icdev-bar-grow .5s ease-out ' + (gi * 0.04 + si * 0.08) + 's both;cursor:pointer'
        });
        rect.addEventListener('mouseenter', function (e) {
          rect.setAttribute('opacity', '0.8');
          showTooltip(escapeHTML(s.name) + ': <b>' + val + '</b>', e);
        });
        rect.addEventListener('mouseleave', function () { rect.setAttribute('opacity', '1'); hideTooltip(); });
        rect.addEventListener('mousemove', function (e) {
          showTooltip(escapeHTML(s.name) + ': <b>' + val + '</b>', e);
        });
        svg.appendChild(rect);

        // Optional value label above bar
        if (opts.showValues) {
          var valTxt = svgEl('text', {
            x: bx + barW / 2, y: by - 4, fill: palette('secondary'),
            'font-size': '9', 'text-anchor': 'middle'
          });
          valTxt.textContent = val;
          svg.appendChild(valTxt);
        }
      });
    });

    container.appendChild(svg);

    if (opts.showLegend !== false && series.length > 1) {
      container.appendChild(buildLegend(series, function () {
        NS.barChart(containerId, opts);
      }));
    }
  };

  // ——— 4. Donut Chart ————————————————————————————————————————————————————

  NS.donutChart = function (containerId, opts) {
    var container = clearContainer(containerId);
    if (!container) return;
    ensureAnimStyle();

    opts = opts || {};
    var segments    = opts.segments || [];
    var size        = opts.size || 180;
    var thickness   = opts.thickness || 28;
    var R           = (size - thickness) / 2;
    var cx          = size / 2;
    var cy          = size / 2;
    var circumf     = 2 * Math.PI * R;

    var total = 0;
    segments.forEach(function (s) { if (!s._hidden) total += (s.value || 0); });
    if (total === 0) total = 1;

    var svg = svgEl('svg', {
      width: size, height: size, viewBox: '0 0 ' + size + ' ' + size,
      role: 'img', 'aria-label': 'Donut chart: ' + segments.map(function (s) { return s.label; }).join(', ')
    });
    svg.style.cssText = 'display:block;margin:0 auto;';

    // Background track ring
    svg.appendChild(svgEl('circle', {
      cx: cx, cy: cy, r: R, fill: 'none',
      stroke: palette('border'), 'stroke-width': thickness
    }));

    var offset = 0;
    segments.forEach(function (seg, i) {
      if (seg._hidden) return;
      var frac    = (seg.value || 0) / total;
      var segLen  = frac * circumf;
      var color   = seg.color || palette('blue');

      var circle = svgEl('circle', {
        cx: cx, cy: cy, r: R, fill: 'none',
        stroke: color, 'stroke-width': thickness,
        'stroke-dasharray': segLen + ' ' + (circumf - segLen),
        'stroke-dashoffset': -offset,
        transform: 'rotate(-90 ' + cx + ' ' + cy + ')',
        style: '--seg-len:' + segLen + ';animation:icdev-donut-draw .6s ease-out ' + (i * 0.12) + 's both;cursor:pointer'
      });
      circle.addEventListener('mouseenter', function (e) {
        circle.setAttribute('stroke-width', thickness + 4);
        showTooltip(
          '<b>' + escapeHTML(seg.label) + '</b><br>' +
          seg.value + ' (' + (frac * 100).toFixed(1) + '%)', e
        );
      });
      circle.addEventListener('mouseleave', function () {
        circle.setAttribute('stroke-width', thickness);
        hideTooltip();
      });
      circle.addEventListener('mousemove', function (e) {
        showTooltip(
          '<b>' + escapeHTML(seg.label) + '</b><br>' +
          seg.value + ' (' + (frac * 100).toFixed(1) + '%)', e
        );
      });
      svg.appendChild(circle);
      offset += segLen;
    });

    // Center text
    if (opts.centerLabel) {
      var cl = svgEl('text', {
        x: cx, y: cy - (opts.centerSubLabel ? 6 : 0),
        fill: palette('secondary'), 'font-size': '22', 'font-weight': 'bold', 'text-anchor': 'middle',
        'dominant-baseline': 'central'
      });
      cl.textContent = escapeHTML(opts.centerLabel);
      svg.appendChild(cl);
    }
    if (opts.centerSubLabel) {
      var csl = svgEl('text', {
        x: cx, y: cy + 16,
        fill: palette('muted'), 'font-size': '11', 'text-anchor': 'middle',
        'dominant-baseline': 'central'
      });
      csl.textContent = escapeHTML(opts.centerSubLabel);
      svg.appendChild(csl);
    }

    container.appendChild(svg);

    if (opts.showLegend !== false) {
      container.appendChild(buildLegend(segments, function () {
        NS.donutChart(containerId, opts);
      }));
    }
  };

  // ——— 5. Gauge Chart ————————————————————————————————————————————————————

  NS.gaugeChart = function (containerId, opts) {
    var container = clearContainer(containerId);
    if (!container) return;
    ensureAnimStyle();

    opts = opts || {};
    var value      = Math.max(0, Math.min(1, opts.value || 0));
    var thresholds = opts.thresholds || { good: 0.7, warning: 0.4 };
    var label      = opts.label || '';
    var size       = opts.size || 180;
    var thickness  = opts.thickness || 18;
    var R          = (size - thickness) / 2;
    var cx         = size / 2;
    var cy         = size / 2 + 10;

    // Semicircle runs pi (left) to 0 (right) — total arc = pi * R
    var halfCircumf = Math.PI * R;

    // Choose color based on thresholds
    var color;
    if (value >= thresholds.good) {
      color = palette('green');
    } else if (value >= thresholds.warning) {
      color = palette('yellow');
    } else {
      color = palette('red');
    }

    var svgH = size / 2 + thickness + 28;
    var svg = svgEl('svg', {
      width: size, height: svgH, viewBox: '0 0 ' + size + ' ' + svgH,
      role: 'img', 'aria-label': 'Gauge chart: ' + (value * 100).toFixed(0) + '% ' + label
    });
    svg.style.cssText = 'display:block;margin:0 auto;';

    // Background track (full semicircle)
    svg.appendChild(svgEl('circle', {
      cx: cx, cy: cy, r: R, fill: 'none',
      stroke: palette('border'), 'stroke-width': thickness,
      'stroke-dasharray': halfCircumf + ' ' + halfCircumf,
      'stroke-dashoffset': 0,
      transform: 'rotate(180 ' + cx + ' ' + cy + ')',
      'stroke-linecap': 'round'
    }));

    // Value arc
    var arcLen = value * halfCircumf;
    var gapLen = halfCircumf - arcLen;
    var valueArc = svgEl('circle', {
      cx: cx, cy: cy, r: R, fill: 'none',
      stroke: color, 'stroke-width': thickness,
      'stroke-dasharray': arcLen + ' ' + (halfCircumf + gapLen),
      'stroke-dashoffset': 0,
      transform: 'rotate(180 ' + cx + ' ' + cy + ')',
      'stroke-linecap': 'round',
      style: '--gauge-len:' + arcLen + ';animation:icdev-gauge-draw .8s ease-out forwards'
    });
    svg.appendChild(valueArc);

    // Value text (center)
    var valText = svgEl('text', {
      x: cx, y: cy - 6,
      fill: color, 'font-size': '28', 'font-weight': 'bold',
      'text-anchor': 'middle', 'dominant-baseline': 'central'
    });
    valText.textContent = (value * 100).toFixed(0) + '%';
    svg.appendChild(valText);

    // Label text (below arc)
    if (label) {
      var lblText = svgEl('text', {
        x: cx, y: cy + 22,
        fill: palette('muted'), 'font-size': '12',
        'text-anchor': 'middle', 'dominant-baseline': 'central'
      });
      lblText.textContent = escapeHTML(label);
      svg.appendChild(lblText);
    }

    // Min / Max labels at arc ends
    var minTxt = svgEl('text', {
      x: cx - R - 2, y: cy + 14, fill: palette('muted'),
      'font-size': '9', 'text-anchor': 'middle'
    });
    minTxt.textContent = '0';
    svg.appendChild(minTxt);

    var maxTxt = svgEl('text', {
      x: cx + R + 2, y: cy + 14, fill: palette('muted'),
      'font-size': '9', 'text-anchor': 'middle'
    });
    maxTxt.textContent = '100';
    svg.appendChild(maxTxt);

    container.appendChild(svg);
  };

})();
