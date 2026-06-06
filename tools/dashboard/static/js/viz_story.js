/* CUI // SP-CTI
 * ICDEV™ Viz Story runtime — interactive, Tableau-style data storytelling.
 *
 * Reads window.__DECK (built by tools/viz/deck_model.py) and renders an
 * interactive presenter: charts via charts.js (hover tooltips, legend toggle,
 * animation), Tableau-style dashboards with live filters + drill-down, and
 * Story-Point insight annotations per step. Fully client-side, air-gap safe.
 */
(function () {
  "use strict";
  var DECK = window.__DECK || { slides: [], colors: {} };
  var SLIDES = DECK.slides || [];
  var COLORS = DECK.colors || {};
  var cur = 0;
  var notesOn = false;
  var _cid = 0;

  function $(id) { return document.getElementById(id); }
  function uid(p) { _cid += 1; return (p || "viz") + "_" + _cid; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function seriesColor(i) {
    var s = COLORS.series || ["#C8A951"];
    return s[i % s.length];
  }
  function fmtNum(v) {
    if (typeof v !== "number" || !isFinite(v)) return String(v);
    var r = Math.round(v * 100) / 100;
    return r.toLocaleString ? r.toLocaleString("en-US") : String(r);
  }
  function whenChartsReady(cb) {
    // Defer to the next frame so the mount node is committed to the DOM and has
    // a measured width before charts.js reads container.offsetWidth.
    function ready() {
      if (window.requestAnimationFrame) requestAnimationFrame(cb);
      else setTimeout(cb, 0);
    }
    if (window.ICDEV && window.ICDEV.barChart) return ready();
    setTimeout(function () { whenChartsReady(cb); }, 40);
  }

  /* ---- ChartSpec → charts.js -------------------------------------------- */
  function renderChart(mountId, chart) {
    var t = chart.chart_type || "column";
    var labels = chart.categories || [];
    var ser = chart.series || [];
    whenChartsReady(function () {
      if (!$(mountId)) return;
      if (t === "pie" || t === "donut") {
        var vals = ser[0] ? ser[0].values : [];
        var seg = vals.map(function (v, i) {
          return { label: labels[i] || ("#" + (i + 1)), value: v, color: seriesColor(i) };
        });
        window.ICDEV.donutChart(mountId, { segments: seg, size: 300, thickness: 46 });
      } else if (t === "gauge") {
        var v = (ser[0] && ser[0].values[0]) || 0;
        var mx = chart.max_value || 100;
        window.ICDEV.gaugeChart(mountId, { value: mx ? v / mx : 0, label: v + (chart.unit || ""), size: 300 });
      } else if (t === "line" || t === "area") {
        window.ICDEV.lineChart(mountId, {
          series: ser.map(function (s, i) { return { name: s.name, data: s.values, color: seriesColor(i) }; }),
          labels: labels, height: 380, area: t === "area"
        });
      } else {
        window.ICDEV.barChart(mountId, {
          series: ser.map(function (s, i) { return { name: s.name, data: s.values, color: seriesColor(i) }; }),
          labels: labels, height: 380
        });
      }
    });
  }

  function kpiCards(kpis) {
    var wrap = document.createElement("div");
    wrap.className = "viz-kpis";
    (kpis.tiles || []).slice(0, 6).forEach(function (t) {
      var c = document.createElement("div");
      c.className = "viz-kpi";
      c.innerHTML = '<div class="k-label">' + esc(t.label) + '</div>' +
        '<div class="k-value">' + esc(t.value) + '<span class="k-unit">' + esc(t.unit || "") + '</span></div>' +
        (t.delta ? '<div class="k-delta">' + esc(t.delta) + '</div>' : "");
      wrap.appendChild(c);
    });
    return wrap;
  }

  function tableEl(tbl) {
    var d = document.createElement("div");
    d.className = "viz-table-wrap";
    var h = '<table class="viz-table"><thead><tr>';
    (tbl.headers || []).forEach(function (x) { h += "<th>" + esc(x) + "</th>"; });
    h += "</tr></thead><tbody>";
    (tbl.rows || []).forEach(function (r) {
      h += "<tr>" + r.map(function (c) { return "<td>" + esc(c) + "</td>"; }).join("") + "</tr>";
    });
    h += "</tbody></table>";
    d.innerHTML = h;
    return d;
  }

  /* ---- Dataset aggregation (Tableau-style live filtering) --------------- */
  function aggregate(rows, colIndex, dimIdx, measIdx, agg) {
    var groups = {};
    rows.forEach(function (r) {
      var k = String(r[dimIdx]);
      var v = measIdx >= 0 ? parseFloat(r[measIdx]) || 0 : 1;
      if (!groups[k]) groups[k] = { sum: 0, n: 0 };
      groups[k].sum += v; groups[k].n += 1;
    });
    var cats = Object.keys(groups);
    var vals = cats.map(function (k) {
      var g = groups[k];
      if (agg === "avg") return g.n ? g.sum / g.n : 0;
      if (agg === "count") return g.n;
      return g.sum;
    });
    return { cats: cats, vals: vals };
  }

  function applyFilters(dataset, active) {
    var cols = dataset.columns;
    return (dataset.rows || []).filter(function (r) {
      for (var dim in active) {
        if (active[dim] && active[dim] !== "(all)") {
          var ci = cols.indexOf(dim);
          if (ci >= 0 && String(r[ci]) !== active[dim]) return false;
        }
      }
      return true;
    });
  }

  function renderDashboard(mount, dash) {
    var grid = document.createElement("div");
    grid.className = "viz-grid";
    mount.appendChild(grid);

    var dataset = dash.dataset;
    var active = {};

    function renderTiles() {
      grid.innerHTML = "";
      var rows = dataset ? applyFilters(dataset, active) : null;
      (dash.tiles || []).forEach(function (tile) {
        var cell = document.createElement("div");
        cell.className = "viz-cell";
        cell.style.gridColumn = "span " + (tile.w || 6);
        grid.appendChild(cell);

        if (tile.datasetKpi && dataset) {
          // Filter-aware KPI tiles (recompute from the active row set).
          var defs = tile.datasetKpi.defs || [];
          var ktiles = defs.map(function (d) {
            var val = 0;
            if (d.kind === "count") { val = rows.length; }
            else if (d.kind === "distinct") {
              var ci = dataset.columns.indexOf(d.field);
              var set = {}; rows.forEach(function (r) { set[String(r[ci])] = 1; });
              val = Object.keys(set).length;
            } else if (d.kind === "sum") {
              var mi = dataset.columns.indexOf(d.field);
              rows.forEach(function (r) { val += parseFloat(r[mi]) || 0; });
            }
            return { label: d.label, value: fmtNum(val), unit: "" };
          });
          cell.appendChild(kpiCards({ tiles: ktiles }));
        } else if (tile.datasetChart && dataset) {
          var dc = tile.datasetChart;
          var dimIdx = dataset.columns.indexOf(dc.dimension);
          var measIdx = dc.measure ? dataset.columns.indexOf(dc.measure) : -1;
          var ag = aggregate(rows, dataset.columns, dimIdx, measIdx, dc.agg || "sum");
          var spec = {
            chart_type: dc.chart_type || "column", unit: dc.unit || "",
            categories: ag.cats, series: [{ name: dc.measure || "count", values: ag.vals }]
          };
          cell.innerHTML = '<div class="cell-title">' + esc(dc.title || "") + "</div>";
          var cid = uid("dc");
          var holder = document.createElement("div"); holder.id = cid; cell.appendChild(holder);
          renderChart(cid, spec);
        } else if (tile.spec) {
          var sp = tile.spec, k = sp.kind;
          if (k === "kpis") { cell.appendChild(kpiCards(sp)); }
          else if (k === "table") { cell.appendChild(tableEl(sp)); }
          else {
            if (sp.title) cell.innerHTML = '<div class="cell-title">' + esc(sp.title) + "</div>";
            var cid2 = uid("st"); var h2 = document.createElement("div"); h2.id = cid2;
            cell.appendChild(h2); renderChart(cid2, sp);
          }
        }
      });
    }

    // Filter bar (Tableau-style) — re-aggregates all tiles on change.
    if (dataset && (dash.filters || []).length) {
      var bar = document.createElement("div");
      bar.className = "viz-filterbar";
      dash.filters.forEach(function (dim) {
        var ci = dataset.columns.indexOf(dim);
        if (ci < 0) return;
        var opts = ["(all)"];
        dataset.rows.forEach(function (r) {
          var v = String(r[ci]); if (opts.indexOf(v) < 0) opts.push(v);
        });
        var lbl = document.createElement("label");
        lbl.innerHTML = "<span>" + esc(dim) + "</span>";
        var sel = document.createElement("select");
        opts.forEach(function (o) {
          var op = document.createElement("option"); op.value = o; op.textContent = o; sel.appendChild(op);
        });
        sel.addEventListener("change", function () { active[dim] = sel.value; renderTiles(); });
        lbl.appendChild(sel); bar.appendChild(lbl);
      });
      mount.insertBefore(bar, grid);
    }
    renderTiles();
  }

  /* ---- Freeform element rendering (read-only; mirrors the editor) ------- */
  function renderElementBody(el, body) {
    var p = el.payload || {}, st = el.style || {};
    if (el.type === "text") {
      var d = document.createElement("div");
      d.style.cssText = "width:100%;height:100%;white-space:pre-wrap;word-break:break-word;font-size:" +
        (st.fontSize || 18) + "px;font-family:'" + (st.fontFamily || "Segoe UI") + "';color:" +
        (st.color || "#fff") + ";font-weight:" + (st.bold ? 700 : 400) + ";font-style:" +
        (st.italic ? "italic" : "normal") + ";text-align:" + (st.align || "left") + ";" +
        (st.underline ? "text-decoration:underline;" : "");
      var raw = p.text || "";
      if (st.list && st.list !== "none") {
        d.innerHTML = raw.split("\n").map(function (l, i) {
          return esc((st.list === "number" ? (i + 1) + ". " : "• ") + l); }).join("<br>");
      } else { d.textContent = raw; }
      body.appendChild(d);
    } else if (el.type === "image" || el.type === "icon") {
      if (el.type === "icon") body.style.color = st.color || (COLORS.accent || "#C8A951");
      if (p.svg) { body.innerHTML = p.svg; }
      else { var img = document.createElement("img"); img.src = p.src || "";
        img.style.cssText = "width:100%;height:100%;object-fit:contain;"; body.appendChild(img); }
    } else if (el.type === "chart") {
      var cid = uid("ffc"); var h = document.createElement("div");
      h.id = cid; h.style.cssText = "width:100%;height:100%;"; body.appendChild(h); renderChart(cid, p);
    } else if (el.type === "table") {
      body.appendChild(tableEl(p));
    } else if (el.type === "kpis") {
      body.appendChild(kpiCards(p));
    } else if (el.type === "diagram") {
      body.innerHTML = p.svg || "";
    } else if (el.type === "shape") {
      var sh = document.createElement("div");
      sh.style.cssText = "width:100%;height:100%;background:" + (st.fill || COLORS.dark || "#1e3a5f") +
        ";border:" + (st.strokeWidth || 0) + "px solid " + (st.stroke || "transparent") +
        ";border-radius:" + (p.shape === "ellipse" ? "50%" : (st.cornerRadius || 0) + "px") + ";";
      body.appendChild(sh);
    }
  }

  function renderFreeform(slideEl, s) {
    slideEl.style.cssText = "height:100%;display:flex;align-items:center;justify-content:center;";
    var stage = $("viz-stage");
    var availW = stage.clientWidth, availH = stage.clientHeight;
    var w = availW, h = w * 9 / 16;
    if (h > availH) { h = availH; w = h * 16 / 9; }
    var surf = document.createElement("div");
    surf.style.cssText = "position:relative;width:" + w + "px;height:" + h + "px;background:" +
      (COLORS.bg || "#0A1628") + ";overflow:hidden;";
    (s.elements || []).slice().sort(function (a, b) { return (a.z || 0) - (b.z || 0); }).forEach(function (el) {
      if (el.hidden) return;
      var d = document.createElement("div");
      d.style.cssText = "position:absolute;left:" + (el.x * 100) + "%;top:" + (el.y * 100) +
        "%;width:" + (el.w * 100) + "%;height:" + (el.h * 100) + "%;overflow:hidden;opacity:" +
        ((el.style && el.style.opacity != null) ? el.style.opacity : 1) + ";";
      renderElementBody(el, d);
      surf.appendChild(d);
    });
    slideEl.appendChild(surf);
  }

  /* ---- Slide rendering -------------------------------------------------- */
  function renderSlide(s) {
    var stage = $("viz-stage");
    stage.innerHTML = "";
    var slide = document.createElement("section");
    slide.className = "viz-slide";

    if (s.freeform && s.elements && s.elements.length) {
      renderFreeform(slide, s);
      stage.appendChild(slide);
      var insF = $("viz-insight");
      if (s.insight) { insF.innerHTML = '<span class="i-dot"></span>' + esc(s.insight); insF.style.display = "block"; }
      else { insF.style.display = "none"; }
      $("viz-notes-body").textContent = s.notes || "";
      updateChrome(); highlightRail();
      return;
    }

    if (s.type === "title") {
      slide.innerHTML = '<div class="hero"><h1>' + esc(s.title) + "</h1>" +
        '<p class="sub">ICDEV™ · A System That Builds Systems</p></div>';
    } else if (s.type === "quote") {
      var q = (s.bullets && s.bullets[0]) || s.title;
      slide.innerHTML = '<div class="quote">“' + esc(q) + "”</div>";
    } else {
      var head = '<h2 class="s-title">' + esc(s.title) + "</h2>";
      slide.innerHTML = head;
      var body = document.createElement("div"); body.className = "s-body"; slide.appendChild(body);

      if (s.type === "chart") {
        var cid = uid("ch"); var hold = document.createElement("div");
        hold.id = cid; hold.className = "chart-host"; body.appendChild(hold);
        if (s.chart && s.chart.title) {
          var ct = document.createElement("div"); ct.className = "cell-title"; ct.textContent = s.chart.title;
          body.insertBefore(ct, hold);
        }
        renderChart(cid, s.chart);
      } else if (s.type === "kpis") {
        body.appendChild(kpiCards(s.kpis));
      } else if (s.type === "table") {
        body.appendChild(tableEl(s.table));
      } else if (s.type === "dashboard") {
        renderDashboard(body, s.dashboard);
      } else if (s.type === "diagram") {
        body.innerHTML = '<div class="diagram">' + (s.svg || "") + "</div>";
      } else if (s.image) {
        body.innerHTML = '<img class="slide-img" src="' + esc(s.image) + '" alt="visual">';
      } else if (s.bullets && s.bullets.length) {
        body.innerHTML = "<ul class='bullets'>" +
          s.bullets.map(function (b) { return "<li>" + esc(b) + "</li>"; }).join("") + "</ul>";
      }
    }
    stage.appendChild(slide);

    // Story-Point insight annotation
    var ins = $("viz-insight");
    if (s.insight) { ins.innerHTML = '<span class="i-dot"></span>' + esc(s.insight); ins.style.display = "block"; }
    else { ins.style.display = "none"; }

    // Speaker notes
    $("viz-notes-body").textContent = s.notes || "";
    updateChrome();
    highlightRail();
  }

  /* ---- Chrome: rail, progress, counter, notes --------------------------- */
  function buildRail() {
    var rail = $("viz-rail");
    SLIDES.forEach(function (s, i) {
      var item = document.createElement("button");
      item.className = "rail-item";
      item.innerHTML = '<span class="rail-n">' + (i + 1) + "</span>" +
        '<span class="rail-t">' + esc(s.title || ("Slide " + (i + 1))) + "</span>";
      item.addEventListener("click", function () { cur = i; renderSlide(SLIDES[cur]); });
      rail.appendChild(item);
    });
  }
  function highlightRail() {
    var items = document.querySelectorAll(".rail-item");
    for (var i = 0; i < items.length; i++) items[i].classList.toggle("active", i === cur);
  }
  function updateChrome() {
    $("viz-counter").textContent = (cur + 1) + " / " + SLIDES.length;
    $("viz-progress").style.width = ((cur + 1) / SLIDES.length * 100) + "%";
  }
  function go(d) {
    var n = Math.max(0, Math.min(SLIDES.length - 1, cur + d));
    if (n !== cur) { cur = n; renderSlide(SLIDES[cur]); }
  }

  function init() {
    if (!SLIDES.length) return;
    buildRail();
    renderSlide(SLIDES[0]);

    document.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight" || e.key === " " || e.key === "PageDown") { go(1); e.preventDefault(); }
      else if (e.key === "ArrowLeft" || e.key === "PageUp") { go(-1); }
      else if (e.key === "Home") { cur = 0; renderSlide(SLIDES[0]); }
      else if (e.key === "End") { cur = SLIDES.length - 1; renderSlide(SLIDES[cur]); }
      else if (e.key === "s" || e.key === "S" || e.key === "n" || e.key === "N") {
        notesOn = !notesOn; $("viz-notes").classList.toggle("show", notesOn);
      } else if (e.key === "f" || e.key === "F") {
        if (!document.fullscreenElement) document.documentElement.requestFullscreen();
        else document.exitFullscreen();
      } else if (e.key === "Escape") {
        if (document.fullscreenElement) document.exitFullscreen();
        else if (DECK.deckId != null) window.location.href = "/slides/" + DECK.deckId;
      } else if (e.key === "r" || e.key === "R") { $("viz-rail").classList.toggle("open"); }
    });

    var resizeT;
    window.addEventListener("resize", function () {
      clearTimeout(resizeT); resizeT = setTimeout(function () { renderSlide(SLIDES[cur]); }, 200);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
