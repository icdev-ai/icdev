/* CUI // SP-CTI
 * ICDEV™ Viz Editor — freeform WYSIWYG slide composer.
 *
 * Renders a slide's positioned elements (fractional 16:9 geometry) as draggable,
 * resizable, layerable divs. Supports custom text (size/font/color/bold/italic/
 * align), images, and Viz-Kernel elements (chart/table/kpis/diagram). Saves the
 * element model back to the deck so the PPTX export stays pixel-consistent.
 */
(function () {
  "use strict";
  var DECK = window.__DECK || { slides: [], colors: {} };
  var DECK_ID = window.__DECK_ID;
  var SLIDES = DECK.slides || [];
  var COLORS = DECK.colors || {};
  var FONTS = ["Segoe UI", "Arial", "Georgia", "Times New Roman", "Courier New", "Verdana", "Tahoma"];
  var cur = 0, selId = null, dirty = false, _id = 0, gesture = null, zoom = 1;
  var undoStack = [], redoStack = [], saveTimer = null;
  // Bundled icon set (air-gap, fill=currentColor so it inherits the element colour).
  var ICONS = {
    check: "<path d='M9 16.2l-3.5-3.5L4 14.1 9 19l11-11-1.4-1.4z'/>",
    star: "<path d='M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z'/>",
    arrow: "<path d='M4 11h12.2l-5.6-5.6L12 4l8 8-8 8-1.4-1.4L16.2 13H4z'/>",
    user: "<path d='M12 12a4 4 0 100-8 4 4 0 000 8zm0 2c-2.7 0-8 1.3-8 4v2h16v-2c0-2.7-5.3-4-8-4z'/>",
    shield: "<path d='M12 1L3 5v6c0 5.6 3.8 10.7 9 12 5.2-1.3 9-6.4 9-12V5z'/>",
    cloud: "<path d='M19.4 10A7.5 7.5 0 0012 4a7.5 7.5 0 00-7.5 7A4 4 0 005 19h14a3.5 3.5 0 00.4-9z'/>",
    lock: "<path d='M18 8h-1V6A5 5 0 007 6v2H6a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V10a2 2 0 00-2-2zM9 6a3 3 0 016 0v2H9z'/>",
    bolt: "<path d='M7 2v11h3v9l7-12h-4l4-8z'/>",
    flag: "<path d='M14.4 6L14 4H5v17h2v-7h5.6l.4 2h7V6z'/>",
    heart: "<path d='M12 21.3l-1.4-1.3C5.4 15.4 2 12.3 2 8.5 2 5.4 4.4 3 7.5 3c1.7 0 3.4.8 4.5 2.1C13.1 3.8 14.8 3 16.5 3 19.6 3 22 5.4 22 8.5c0 3.8-3.4 6.9-8.6 11.5z'/>",
    circle: "<circle cx='12' cy='12' r='9'/>",
    square: "<rect x='4' y='4' width='16' height='16' rx='2'/>"
  };
  function ICON_SVG(name) {
    return "<svg width='100%' height='100%' viewBox='0 0 24 24' fill='currentColor'>" + (ICONS[name] || "") + "</svg>";
  }

  function $(s, r) { return (r || document).querySelector(s); }
  function el(tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; }
  function uid(p) { _id += 1; return (p || "e") + "_" + _id + "_" + Date.now() % 100000; }
  function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function seriesColor(i) { var s = COLORS.series || ["#C8A951"]; return s[i % s.length]; }
  function curSlide() { return SLIDES[cur] || { elements: [] }; }
  function els() { return curSlide().elements || (curSlide().elements = []); }
  function selEl() { return els().filter(function (e) { return e.id === selId; })[0] || null; }
  function setDirty(v) {
    dirty = v;
    $("#dirty").textContent = v ? "● unsaved" : "✓ saved";
    if (v) { var s = curSlide(); if (s) s.freeform = true; scheduleSave(); }
  }
  function scheduleSave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { save(true); }, 1500);  // debounced autosave
  }

  /* ---- undo / redo (full-deck element snapshots) ---- */
  function serialize() {
    return JSON.stringify({ cur: cur, slides: SLIDES.map(function (s) { return s.elements || []; }) });
  }
  function snapshot() {
    undoStack.push(serialize());
    if (undoStack.length > 60) undoStack.shift();
    redoStack = [];
  }
  function restore(str) {
    var d = JSON.parse(str);
    d.slides.forEach(function (els, i) { if (SLIDES[i]) SLIDES[i].elements = els; });
    cur = Math.min(d.cur, SLIDES.length - 1);
    selId = null; renderAll();
  }
  function undo() { if (undoStack.length) { redoStack.push(serialize()); restore(undoStack.pop()); setDirty(true); } }
  function redo() { if (redoStack.length) { undoStack.push(serialize()); restore(redoStack.pop()); setDirty(true); } }

  function whenCharts(cb) {
    function go() { (window.requestAnimationFrame || function (f) { setTimeout(f, 0); })(cb); }
    if (window.ICDEV && window.ICDEV.barChart) return go();
    setTimeout(function () { whenCharts(cb); }, 40);
  }

  /* ---- chart spec → charts.js ---- */
  function renderChart(mountId, c) {
    var t = c.chart_type || "column", labels = c.categories || [], ser = c.series || [];
    whenCharts(function () {
      var m = document.getElementById(mountId); if (!m) return;
      if (t === "pie" || t === "donut") {
        var vals = ser[0] ? ser[0].values : [];
        window.ICDEV.donutChart(mountId, { segments: vals.map(function (v, i) {
          return { label: labels[i] || ("#" + (i + 1)), value: v, color: seriesColor(i) }; }), size: 200 });
      } else if (t === "gauge") {
        var v = (ser[0] && ser[0].values[0]) || 0, mx = c.max_value || 100;
        window.ICDEV.gaugeChart(mountId, { value: mx ? v / mx : 0, label: v + (c.unit || ""), size: 200 });
      } else if (t === "line" || t === "area") {
        window.ICDEV.lineChart(mountId, { series: ser.map(function (s, i) {
          return { name: s.name, data: s.values, color: seriesColor(i) }; }), labels: labels, height: 240 });
      } else {
        window.ICDEV.barChart(mountId, { series: ser.map(function (s, i) {
          return { name: s.name, data: s.values, color: seriesColor(i) }; }), labels: labels, height: 240 });
      }
    });
  }

  /* ---- element body rendering ---- */
  function renderBody(e, body) {
    body.innerHTML = "";
    if (e.type === "text") {
      var d = el("div", "txt"); var st = e.style || {};
      d.style.cssText = "font-size:" + (st.fontSize || 18) + "px;font-family:'" + (st.fontFamily || "Segoe UI") +
        "';color:" + (st.color || "#fff") + ";font-weight:" + (st.bold ? 700 : 400) + ";font-style:" +
        (st.italic ? "italic" : "normal") + ";text-align:" + (st.align || "left") + ";width:100%;height:100%;" +
        (st.underline ? "text-decoration:underline;" : "");
      var raw = (e.payload && e.payload.text) || "";
      if (st.list && st.list !== "none") {
        d.innerHTML = raw.split("\n").map(function (l, i) {
          return esc((st.list === "number" ? (i + 1) + ". " : "• ") + l); }).join("<br>");
      } else { d.textContent = raw; }
      d.addEventListener("dblclick", function () {
        snapshot(); d.textContent = raw; d.contentEditable = "true"; d.focus();
        d.addEventListener("blur", function () {
          d.contentEditable = "false"; e.payload.text = d.innerText; setDirty(true); renderSurface();
        }, { once: true });
      });
      body.appendChild(d);
    } else if (e.type === "image") {
      var img = el("img"); img.src = (e.payload && e.payload.src) || ""; body.appendChild(img);
    } else if (e.type === "icon") {
      body.style.color = (e.style && e.style.color) || (COLORS.accent || "#C8A951");
      body.innerHTML = (e.payload && e.payload.svg) || ICON_SVG(e.payload && e.payload.name);
    } else if (e.type === "chart") {
      var cid = uid("chart"); var h = el("div"); h.id = cid; h.style.cssText = "width:100%;height:100%;";
      body.appendChild(h); renderChart(cid, e.payload);
      body.addEventListener("dblclick", function (ev) { ev.stopPropagation(); chartEditor(e); });
    } else if (e.type === "table") {
      var p = e.payload || {}, html = "<table><thead><tr>";
      (p.headers || []).forEach(function (x) { html += "<th>" + esc(x) + "</th>"; });
      html += "</tr></thead><tbody>";
      (p.rows || []).forEach(function (r) { html += "<tr>" + r.map(function (c) { return "<td>" + esc(c) + "</td>"; }).join("") + "</tr>"; });
      body.innerHTML = html + "</tbody></table>";
      body.addEventListener("dblclick", function (ev) { ev.stopPropagation(); tableEditor(e); });
    } else if (e.type === "kpis") {
      var wrap = el("div"); wrap.style.cssText = "display:flex;gap:8px;height:100%;align-items:center;";
      (e.payload.tiles || []).slice(0, 4).forEach(function (t) {
        var c = el("div"); c.style.cssText = "flex:1;background:" + (COLORS.dark || "#1e3a5f") +
          ";border-left:3px solid " + (COLORS.accent || "#C8A951") + ";border-radius:8px;padding:8px;";
        c.innerHTML = "<div style='color:" + (COLORS.subtext || "#ccc") + ";font-size:10px;text-transform:uppercase'>" +
          esc(t.label) + "</div><div style='color:" + (COLORS.accent || "#C8A951") +
          ";font-size:24px;font-weight:700'>" + esc(t.value) + esc(t.unit || "") + "</div>";
        wrap.appendChild(c);
      });
      body.appendChild(wrap);
    } else if (e.type === "diagram") {
      body.innerHTML = (e.payload && e.payload.svg) || "<div style='color:#888;padding:8px'>Diagram</div>";
    } else if (e.type === "shape") {
      renderShape(e, body);
    } else if (e.type === "dashboard") {
      body.innerHTML = "<div style='color:" + (COLORS.accent || "#C8A951") +
        ";padding:10px;border:1px dashed " + (COLORS.accent || "#C8A951") + ";border-radius:8px'>📊 Dashboard: " +
        esc((e.payload && e.payload.title) || "") + "</div>";
    }
  }

  function renderShape(e, body) {
    var p = e.payload || {}, st = e.style || {}, kind = p.shape || "rectangle";
    var fill = st.fill || (COLORS.accent || "#C8A951"), stroke = st.stroke || "transparent";
    var sw = st.strokeWidth || 0;
    if (kind === "line" || kind === "arrow") {
      var head = kind === "arrow"
        ? "<defs><marker id='ah" + e.id + "' markerWidth='10' markerHeight='8' refX='8' refY='4' orient='auto'>" +
          "<polygon points='0 0,10 4,0 8' fill='" + fill + "'/></marker></defs>" : "";
      body.innerHTML = "<svg width='100%' height='100%' viewBox='0 0 100 100' preserveAspectRatio='none'>" + head +
        "<line x1='2' y1='50' x2='98' y2='50' stroke='" + fill + "' stroke-width='" + (sw || 3) + "'" +
        (kind === "arrow" ? " marker-end='url(#ah" + e.id + ")'" : "") + "/></svg>";
    } else {
      var sh = document.createElement("div");
      sh.style.cssText = "width:100%;height:100%;background:" + fill + ";border:" + sw + "px solid " + stroke +
        ";border-radius:" + (kind === "ellipse" ? "50%" : (st.cornerRadius || 0) + "px") + ";";
      body.appendChild(sh);
    }
  }

  /* ---- surface sizing + render ---- */
  function sizeSurface() {
    var wrap = $("#stagewrap"), s = $("#surface");
    var availW = wrap.clientWidth - 40, availH = wrap.clientHeight - 40;
    var w = availW, h = w * 9 / 16;
    if (h > availH) { h = availH; w = h * 16 / 9; }
    w *= zoom; h *= zoom;
    s.style.width = w + "px"; s.style.height = h + "px";
    var lbl = $("#zoom-label"); if (lbl) lbl.textContent = zoom === 1 ? "Fit" : Math.round(zoom * 100) + "%";
  }
  function setZoom(z) { zoom = Math.max(0.25, Math.min(4, z)); sizeSurface(); renderSurface(); }

  function renderSurface() {
    var s = $("#surface"); s.innerHTML = "";
    var list = els().slice().sort(function (a, b) { return (a.z || 0) - (b.z || 0); });
    list.forEach(function (e) {
      if (e.hidden) return;  // hidden elements live only in the layers panel
      var d = el("div", "el" + (e.id === selId ? " sel" : ""));
      d.dataset.id = e.id;
      d.style.left = (e.x * 100) + "%"; d.style.top = (e.y * 100) + "%";
      d.style.width = (e.w * 100) + "%"; d.style.height = (e.h * 100) + "%";
      d.style.zIndex = e.z || 0;
      if (e.style && e.style.opacity != null) d.style.opacity = e.style.opacity;
      var body = el("div", "body"); d.appendChild(body); renderBody(e, body);
      ["nw", "ne", "sw", "se"].forEach(function (h) {
        var hd = el("div", "handle h-" + h); hd.dataset.dir = h; d.appendChild(hd);
      });
      d.addEventListener("pointerdown", onPointerDown);
      s.appendChild(d);
    });
  }

  function renderStrip() {
    var strip = $("#strip"); strip.innerHTML = "";
    SLIDES.forEach(function (sl, i) {
      var t = el("div", "thumb" + (i === cur ? " active" : ""));
      t.draggable = true; t.dataset.idx = i;
      t.innerHTML = "<span class='tn'>" + (i + 1) + "</span> <span class='tl'>" +
        esc(sl.title || ("Slide " + (i + 1))) + "</span>" +
        "<span class='thumb-actions'><button class='dup' title='Duplicate'>⧉</button>" +
        "<button class='del' title='Delete'>✕</button></span>";
      t.addEventListener("click", function (e) {
        if (e.target.tagName === "BUTTON") return; cur = i; selId = null; renderAll();
      });
      t.querySelector(".dup").addEventListener("click", function (e) { e.stopPropagation(); duplicateSlide(i); });
      t.querySelector(".del").addEventListener("click", function (e) { e.stopPropagation(); deleteSlide(i); });
      t.addEventListener("dragstart", function (e) { e.dataTransfer.setData("text/plain", String(i)); });
      t.addEventListener("dragover", function (e) { e.preventDefault(); t.classList.add("drop"); });
      t.addEventListener("dragleave", function () { t.classList.remove("drop"); });
      t.addEventListener("drop", function (e) {
        e.preventDefault(); t.classList.remove("drop");
        moveSlide(parseInt(e.dataTransfer.getData("text/plain"), 10), i);
      });
      strip.appendChild(t);
    });
    var add = el("button", "strip-add"); add.textContent = "+ Add slide";
    add.addEventListener("click", addSlide); strip.appendChild(add);
  }

  /* ---- drag + resize ---- */
  function onPointerDown(ev) {
    var node = ev.currentTarget; var id = node.dataset.id;
    selId = id; renderProps(); renderLayers();
    document.querySelectorAll(".el").forEach(function (n) { n.classList.toggle("sel", n.dataset.id === id); });
    if (selEl() && selEl().locked) { ev.preventDefault(); return; }  // locked: select only, no drag/resize
    if (ev.target.classList.contains("handle")) {
      snapshot();
      gesture = { mode: "resize", dir: ev.target.dataset.dir };
    } else {
      if (ev.target.isContentEditable) return;  // let text editing happen
      snapshot();
      gesture = { mode: "move" };
    }
    var e = selEl(); var s = $("#surface").getBoundingClientRect();
    gesture.startX = ev.clientX; gesture.startY = ev.clientY;
    gesture.ox = e.x; gesture.oy = e.y; gesture.ow = e.w; gesture.oh = e.h;
    gesture.sw = s.width; gesture.sh = s.height; gesture.e = e;
    document.addEventListener("pointermove", onPointerMove);
    document.addEventListener("pointerup", onPointerUp, { once: true });
    ev.preventDefault();
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function onPointerMove(ev) {
    if (!gesture) return;
    var dx = (ev.clientX - gesture.startX) / gesture.sw;
    var dy = (ev.clientY - gesture.startY) / gesture.sh;
    var e = gesture.e;
    if (gesture.mode === "move") {
      e.x = clamp(gesture.ox + dx, 0, 1 - e.w); e.y = clamp(gesture.oy + dy, 0, 1 - e.h);
      applySnap(e);
    } else {
      var d = gesture.dir;
      if (d.indexOf("e") >= 0) e.w = clamp(gesture.ow + dx, 0.03, 1 - e.x);
      if (d.indexOf("s") >= 0) e.h = clamp(gesture.oh + dy, 0.03, 1 - e.y);
      if (d.indexOf("w") >= 0) { var nw = clamp(gesture.ow - dx, 0.03, gesture.ox + gesture.ow); e.x = gesture.ox + (gesture.ow - nw); e.w = nw; }
      if (d.indexOf("n") >= 0) { var nh = clamp(gesture.oh - dy, 0.03, gesture.oy + gesture.oh); e.y = gesture.oy + (gesture.oh - nh); e.h = nh; }
    }
    var node = document.querySelector('.el[data-id="' + e.id + '"]');
    if (node) { node.style.left = e.x * 100 + "%"; node.style.top = e.y * 100 + "%"; node.style.width = e.w * 100 + "%"; node.style.height = e.h * 100 + "%"; }
  }

  /* ---- smart alignment guides + snapping (Figma-style) ---- */
  var SNAP = 0.012;  // fractional snap threshold (~1.2% of slide)
  function applySnap(e) {
    var vt = [0, 0.5, 1], ht = [0, 0.5, 1];  // slide edges + center
    els().forEach(function (o) {
      if (o.id === e.id) return;
      vt.push(o.x, o.x + o.w / 2, o.x + o.w); ht.push(o.y, o.y + o.h / 2, o.y + o.h);
    });
    var vlines = [], hlines = [];
    // x: snap left / center / right of moving element
    var xpts = [["l", e.x], ["c", e.x + e.w / 2], ["r", e.x + e.w]];
    var bestx = null;
    xpts.forEach(function (pt) {
      vt.forEach(function (g) {
        var diff = Math.abs(pt[1] - g);
        if (diff < SNAP && (bestx == null || diff < bestx.diff)) bestx = { diff: diff, g: g, kind: pt[0] };
      });
    });
    if (bestx) {
      if (bestx.kind === "l") e.x = bestx.g; else if (bestx.kind === "c") e.x = bestx.g - e.w / 2; else e.x = bestx.g - e.w;
      e.x = clamp(e.x, 0, 1 - e.w); vlines.push(bestx.g);
    }
    var ypts = [["t", e.y], ["m", e.y + e.h / 2], ["b", e.y + e.h]];
    var besty = null;
    ypts.forEach(function (pt) {
      ht.forEach(function (g) {
        var diff = Math.abs(pt[1] - g);
        if (diff < SNAP && (besty == null || diff < besty.diff)) besty = { diff: diff, g: g, kind: pt[0] };
      });
    });
    if (besty) {
      if (besty.kind === "t") e.y = besty.g; else if (besty.kind === "m") e.y = besty.g - e.h / 2; else e.y = besty.g - e.h;
      e.y = clamp(e.y, 0, 1 - e.h); hlines.push(besty.g);
    }
    drawGuides(vlines, hlines);
  }
  function drawGuides(vlines, hlines) {
    clearGuides();
    var s = $("#surface");
    vlines.forEach(function (x) {
      var g = el("div", "snap-guide"); g.style.cssText = "position:absolute;top:0;bottom:0;width:1px;background:#ff4da6;left:" + (x * 100) + "%;z-index:9999;pointer-events:none;";
      s.appendChild(g);
    });
    hlines.forEach(function (y) {
      var g = el("div", "snap-guide"); g.style.cssText = "position:absolute;left:0;right:0;height:1px;background:#ff4da6;top:" + (y * 100) + "%;z-index:9999;pointer-events:none;";
      s.appendChild(g);
    });
  }
  function clearGuides() { document.querySelectorAll(".snap-guide").forEach(function (g) { g.remove(); }); }

  function onPointerUp() { document.removeEventListener("pointermove", onPointerMove); gesture = null; clearGuides(); setDirty(true); renderProps(); renderLayers(); }

  /* ---- properties panel ---- */
  function renderProps() {
    var p = $("#props"); var e = selEl();
    if (!e) {
      var sl = curSlide();
      p.innerHTML = "<h4>SLIDE " + (cur + 1) + " OF " + SLIDES.length + "</h4>" +
        "<div class='row' style='display:block'><label>Speaker notes</label>" +
        "<textarea id='p-notes' rows='5' style='width:100%;margin-top:6px;'>" +
        esc((sl && sl.notes) || "") + "</textarea></div>" +
        "<div class='hint'>Select an element to edit it. Drag to move, corner handles to resize, " +
        "double-click text to edit. Ctrl+Z undo.</div>";
      var nt = $("#p-notes");
      if (nt) nt.addEventListener("input", function () { var s = curSlide(); if (s) { s.notes = this.value; setDirty(true); } });
      return;
    }
    var rows = "<h4>" + e.type.toUpperCase() + " ELEMENT</h4>";
    if (e.type === "text") {
      var st = e.style || (e.style = {});
      rows += row("Font size", "<input type=number id=p-size min=8 max=120 value='" + (st.fontSize || 18) + "'>");
      rows += row("Font", "<select id=p-font>" + FONTS.map(function (f) {
        return "<option" + (f === st.fontFamily ? " selected" : "") + ">" + f + "</option>"; }).join("") + "</select>");
      rows += row("Color", "<input type=color id=p-color value='" + (st.color || "#ffffff") + "'>");
      rows += row("Style", "<span class=seg><button id=p-bold class='" + (st.bold ? "on" : "") + "'>B</button>" +
        "<button id=p-italic class='" + (st.italic ? "on" : "") + "'>I</button>" +
        "<button id=p-underline class='" + (st.underline ? "on" : "") + "'>U</button></span>");
      rows += row("Align", "<span class=seg><button data-al=left>L</button><button data-al=center>C</button><button data-al=right>R</button></span>");
      rows += row("List", "<select id=p-list>" + ["none", "bullet", "number"].map(function (k) {
        return "<option value='" + k + "'" + ((st.list || "none") === k ? " selected" : "") + ">" +
          (k === "none" ? "None" : k === "bullet" ? "Bullet" : "Numbered") + "</option>"; }).join("") + "</select>");
    } else if (e.type === "shape") {
      var ss = e.style || (e.style = {});
      var shapes = ["rectangle", "ellipse", "line", "arrow"];
      rows += row("Shape", "<select id=p-shape>" + shapes.map(function (k) {
        return "<option" + ((e.payload.shape || "rectangle") === k ? " selected" : "") + ">" + k + "</option>"; }).join("") + "</select>");
      rows += row("Fill", "<input type=color id=p-fill value='" + (ss.fill || "#C8A951") + "'>");
      rows += row("Border", "<input type=color id=p-stroke value='" + (ss.stroke && ss.stroke !== "transparent" ? ss.stroke : "#000000") + "'>");
      rows += row("Border w", "<input type=number id=p-sw min=0 max=20 value='" + (ss.strokeWidth || 0) + "'>");
      rows += row("Radius", "<input type=number id=p-rad min=0 max=80 value='" + (ss.cornerRadius || 0) + "'>");
    }
    if (e.type !== "text" && e.type !== "shape") {
      rows += row("Edit", "<button id=p-editviz>Edit content</button>");
    }
    rows += row("Opacity", "<input type=range id=p-opacity min=0 max=100 value='" +
      Math.round(((e.style && e.style.opacity != null) ? e.style.opacity : 1) * 100) + "'>");
    rows += "<div class='hint'>Drag to move · corner handles to resize · arrows nudge · double-click charts/tables/text to edit.</div>";
    p.innerHTML = rows;
    if (e.type === "text") wireTextProps(e);
    if (e.type === "shape") wireShapeProps(e);
    var op = $("#p-opacity");
    if (op) op.addEventListener("input", function () { (e.style || (e.style = {})).opacity = this.value / 100; refresh(); });
    var ev = $("#p-editviz");
    if (ev) ev.addEventListener("click", function () {
      if (e.type === "chart") chartEditor(e); else if (e.type === "table") tableEditor(e);
    });
  }
  function wireShapeProps(e) {
    var st = e.style;
    $("#p-shape").addEventListener("change", function () { e.payload.shape = this.value; refresh(); });
    $("#p-fill").addEventListener("input", function () { st.fill = this.value; refresh(); });
    $("#p-stroke").addEventListener("input", function () { st.stroke = this.value; if (!st.strokeWidth) st.strokeWidth = 2; refresh(); });
    $("#p-sw").addEventListener("input", function () { st.strokeWidth = parseInt(this.value) || 0; refresh(); });
    $("#p-rad").addEventListener("input", function () { st.cornerRadius = parseInt(this.value) || 0; refresh(); });
  }
  function row(label, ctrl) { return "<div class='row'><label>" + label + "</label>" + ctrl + "</div>"; }
  function wireTextProps(e) {
    var st = e.style;
    $("#p-size").addEventListener("input", function () { st.fontSize = parseInt(this.value) || 18; refresh(); });
    $("#p-font").addEventListener("change", function () { st.fontFamily = this.value; refresh(); });
    $("#p-color").addEventListener("input", function () { st.color = this.value; refresh(); });
    $("#p-bold").addEventListener("click", function () { st.bold = !st.bold; this.classList.toggle("on"); refresh(); });
    $("#p-italic").addEventListener("click", function () { st.italic = !st.italic; this.classList.toggle("on"); refresh(); });
    $("#p-underline").addEventListener("click", function () { st.underline = !st.underline; this.classList.toggle("on"); refresh(); });
    $("#p-list").addEventListener("change", function () { st.list = this.value; refresh(); });
    document.querySelectorAll("#props [data-al]").forEach(function (b) {
      b.addEventListener("click", function () { st.align = b.dataset.al; refresh(); });
    });
  }
  function refresh() { setDirty(true); renderSurface(); }

  /* ---- toolbar actions ---- */
  function addElement(e) { snapshot(); e.id = uid(e.type); e.z = (els().reduce(function (m, x) { return Math.max(m, x.z || 0); }, 0)) + 1; els().push(e); selId = e.id; setDirty(true); renderAll(); }
  function addText() { addElement({ type: "text", x: 0.3, y: 0.4, w: 0.4, h: 0.12, payload: { text: "New text" },
    style: { fontSize: 24, fontFamily: "Segoe UI", color: COLORS.text || "#ffffff", bold: false, italic: false, align: "left" } }); }
  function addShape(kind) { addElement({ type: "shape", x: 0.35, y: 0.35, w: 0.3, h: 0.25,
    payload: { shape: kind || "rectangle" },
    style: { fill: COLORS.accent || "#C8A951", stroke: "transparent", strokeWidth: 0, cornerRadius: 8, opacity: 1 } }); }
  function addIcon(name) { addElement({ type: "icon", x: 0.44, y: 0.4, w: 0.1, h: 0.14,
    payload: { svg: ICON_SVG(name), name: name }, style: { color: COLORS.accent || "#C8A951", opacity: 1 } }); }
  function iconPicker() {
    var body = $("#modal-body");
    var h = "<h3>Insert Icon</h3><div style='display:grid;grid-template-columns:repeat(6,1fr);gap:10px;'>";
    Object.keys(ICONS).forEach(function (n) {
      h += "<button class='icn-pick' data-n='" + n + "' title='" + n + "' style='background:#1b2740;border:1px solid #2a3a5c;" +
        "border-radius:8px;padding:12px;cursor:pointer;color:" + (COLORS.accent || "#C8A951") + "'>" +
        ICON_SVG(n).replace("width='100%' height='100%'", "width='26' height='26'") + "</button>";
    });
    h += "</div><div class='mb-actions'><button id='icn-cancel'>Cancel</button></div>";
    body.innerHTML = h;
    body.querySelectorAll(".icn-pick").forEach(function (b) {
      b.addEventListener("click", function () { addIcon(b.dataset.n); closeModal(); });
    });
    $("#icn-cancel").addEventListener("click", closeModal);
    openModal();
  }
  function layer(dir) { var e = selEl(); if (!e) return; snapshot(); e.z = (e.z || 0) + dir; setDirty(true); renderSurface(); }
  function del() { var e = selEl(); if (!e) return; snapshot(); curSlide().elements = els().filter(function (x) { return x.id !== e.id; }); selId = null; setDirty(true); renderAll(); }
  function nudge(key, big) {
    var e = selEl(); if (!e) return; snapshot(); var s = big ? 0.05 : 0.01;
    if (key === "ArrowLeft") e.x = clamp(e.x - s, 0, 1 - e.w);
    else if (key === "ArrowRight") e.x = clamp(e.x + s, 0, 1 - e.w);
    else if (key === "ArrowUp") e.y = clamp(e.y - s, 0, 1 - e.h);
    else if (key === "ArrowDown") e.y = clamp(e.y + s, 0, 1 - e.h);
    setDirty(true); renderSurface();
  }
  function dupElement() {
    var e = selEl(); if (!e) return; snapshot();
    var copy = JSON.parse(JSON.stringify(e)); copy.id = uid(copy.type);
    copy.x = clamp(copy.x + 0.03, 0, 1 - copy.w); copy.y = clamp(copy.y + 0.03, 0, 1 - copy.h);
    copy.z = (els().reduce(function (m, x) { return Math.max(m, x.z || 0); }, 0)) + 1;
    els().push(copy); selId = copy.id; setDirty(true); renderAll();
  }

  /* ---- slide CRUD ---- */
  function reorderSlides() {
    fetch("/slides/api/" + DECK_ID + "/slides/reorder", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slide_ids: SLIDES.map(function (s) { return s.slideId; }) })
    });
  }
  function addSlide() {
    snapshot();
    fetch("/slides/api/" + DECK_ID + "/slides/add", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(function (r) { return r.json(); }).then(function (d) {
        if (!d.ok) return alert("Add slide failed");
        SLIDES.splice(cur + 1, 0, { slideId: d.slide_id, type: "content", title: "New Slide",
          elements: [], freeform: true, notes: "" });
        cur += 1; selId = null; renderAll(); reorderSlides();
      });
  }
  function duplicateSlide(idx) {
    var src = SLIDES[idx]; if (!src) return; snapshot();
    fetch("/slides/api/" + DECK_ID + "/slides/" + src.slideId + "/duplicate", { method: "POST" })
      .then(function (r) { return r.json(); }).then(function (d) {
        if (!d.ok) return alert("Duplicate failed");
        var copy = JSON.parse(JSON.stringify(src));
        copy.slideId = d.slide_id; copy.title = (src.title || "Slide") + " (copy)"; copy.freeform = true;
        SLIDES.splice(idx + 1, 0, copy); cur = idx + 1; selId = null; renderAll();
        reorderSlides(); setDirty(true);  // persist the copy's current (possibly unsaved) elements
      });
  }
  function deleteSlide(idx) {
    if (SLIDES.length <= 1) return alert("A deck needs at least one slide.");
    var sl = SLIDES[idx]; if (!confirm("Delete this slide?")) return; snapshot();
    fetch("/slides/api/" + DECK_ID + "/slides/" + sl.slideId, { method: "DELETE" })
      .then(function (r) { return r.json(); }).then(function () {
        SLIDES.splice(idx, 1); if (cur >= SLIDES.length) cur = SLIDES.length - 1;
        selId = null; renderAll(); reorderSlides();
      });
  }
  function moveSlide(from, to) {
    if (from === to || from == null) return; snapshot();
    var s = SLIDES.splice(from, 1)[0]; SLIDES.splice(to, 0, s);
    cur = to; renderAll(); reorderSlides();
  }

  /* ---- chart & table builders (data-entry, no JSON) ---- */
  function openModal() { $("#viz-modal").style.display = "flex"; }
  function closeModal() { $("#viz-modal").style.display = "none"; $("#modal-body").innerHTML = ""; }

  function chartEditor(existing) {
    var spec = existing ? JSON.parse(JSON.stringify(existing.payload)) :
      { kind: "chart", chart_type: "column", title: "New Chart", unit: "",
        categories: ["A", "B", "C"], series: [{ name: "Series 1", values: [10, 20, 15] }] };
    var body = $("#modal-body");
    function preview() {
      var pv = $("#mb-preview"); if (!pv) return;
      pv.innerHTML = "<div id='ce-prev' style='width:100%;height:240px'></div>"; renderChart("ce-prev", spec);
    }
    function render() {
      var types = ["column", "bar", "line", "area", "pie", "donut", "gauge"];
      var h = "<h3>" + (existing ? "Edit" : "Insert") + " Chart</h3><div class='mb-row'>" +
        "<label>Type <select id='ce-type'>" + types.map(function (t) {
          return "<option" + (t === spec.chart_type ? " selected" : "") + ">" + t + "</option>"; }).join("") + "</select></label>" +
        "<label>Title <input id='ce-title' value='" + esc(spec.title || "") + "'></label>" +
        "<label>Unit <input id='ce-unit' value='" + esc(spec.unit || "") + "' style='width:60px'></label></div>";
      h += "<table class='grid-tbl'><thead><tr><th>Category</th>";
      spec.series.forEach(function (s, si) { h += "<th><input data-sname='" + si + "' value='" + esc(s.name) + "'></th>"; });
      h += "<th></th></tr></thead><tbody>";
      spec.categories.forEach(function (cat, ci) {
        h += "<tr><td><input data-cat='" + ci + "' value='" + esc(cat) + "'></td>";
        spec.series.forEach(function (s, si) { h += "<td><input data-v='" + ci + "_" + si + "' value='" + (s.values[ci] != null ? s.values[ci] : 0) + "'></td>"; });
        h += "<td><button class='rmbtn' data-rmrow='" + ci + "'>✕</button></td></tr>";
      });
      h += "</tbody></table><div><button class='mb-addbtn' id='ce-addrow'>+ Category</button>" +
        "<button class='mb-addbtn' id='ce-addser'>+ Series</button></div><div id='mb-preview'></div>" +
        "<div class='mb-actions'><button id='ce-cancel'>Cancel</button>" +
        "<button class='primary' id='ce-insert'>" + (existing ? "Update" : "Insert") + "</button></div>";
      body.innerHTML = h;
      $("#ce-type").addEventListener("change", function () { spec.chart_type = this.value; preview(); });
      $("#ce-title").addEventListener("input", function () { spec.title = this.value; });
      $("#ce-unit").addEventListener("input", function () { spec.unit = this.value; });
      body.querySelectorAll("[data-cat]").forEach(function (i) { i.addEventListener("input", function () { spec.categories[+this.dataset.cat] = this.value; }); });
      body.querySelectorAll("[data-sname]").forEach(function (i) { i.addEventListener("input", function () { spec.series[+this.dataset.sname].name = this.value; preview(); }); });
      body.querySelectorAll("[data-v]").forEach(function (i) { i.addEventListener("input", function () { var p = this.dataset.v.split("_"); spec.series[+p[1]].values[+p[0]] = parseFloat(this.value) || 0; preview(); }); });
      body.querySelectorAll("[data-rmrow]").forEach(function (b) { b.addEventListener("click", function () { var ci = +this.dataset.rmrow; spec.categories.splice(ci, 1); spec.series.forEach(function (s) { s.values.splice(ci, 1); }); render(); }); });
      $("#ce-addrow").addEventListener("click", function () { spec.categories.push("New"); spec.series.forEach(function (s) { s.values.push(0); }); render(); });
      $("#ce-addser").addEventListener("click", function () { spec.series.push({ name: "Series " + (spec.series.length + 1), values: spec.categories.map(function () { return 0; }) }); render(); });
      $("#ce-cancel").addEventListener("click", closeModal);
      $("#ce-insert").addEventListener("click", function () {
        if (existing) { snapshot(); existing.payload = spec; setDirty(true); renderSurface(); }
        else { addElement({ type: "chart", x: 0.15, y: 0.2, w: 0.7, h: 0.62, payload: spec }); }
        closeModal();
      });
      preview();
    }
    render(); openModal();
  }

  function tableEditor(existing) {
    var spec = existing ? JSON.parse(JSON.stringify(existing.payload)) :
      { kind: "table", title: "New Table", headers: ["Column A", "Column B"], rows: [["", ""], ["", ""]] };
    var body = $("#modal-body");
    function render() {
      var h = "<h3>" + (existing ? "Edit" : "Insert") + " Table</h3>" +
        "<div class='mb-row'><label>Title <input id='te-title' value='" + esc(spec.title || "") + "'></label></div>" +
        "<table class='grid-tbl'><thead><tr>";
      spec.headers.forEach(function (hd, hi) { h += "<th><input data-h='" + hi + "' value='" + esc(hd) + "'></th>"; });
      h += "<th></th></tr></thead><tbody>";
      spec.rows.forEach(function (row, ri) {
        h += "<tr>";
        spec.headers.forEach(function (_, ci) { h += "<td><input data-c='" + ri + "_" + ci + "' value='" + esc(row[ci] || "") + "'></td>"; });
        h += "<td><button class='rmbtn' data-rmrow='" + ri + "'>✕</button></td></tr>";
      });
      h += "</tbody></table><div><button class='mb-addbtn' id='te-addrow'>+ Row</button>" +
        "<button class='mb-addbtn' id='te-addcol'>+ Column</button></div>" +
        "<div class='mb-actions'><button id='te-cancel'>Cancel</button>" +
        "<button class='primary' id='te-insert'>" + (existing ? "Update" : "Insert") + "</button></div>";
      body.innerHTML = h;
      $("#te-title").addEventListener("input", function () { spec.title = this.value; });
      body.querySelectorAll("[data-h]").forEach(function (i) { i.addEventListener("input", function () { spec.headers[+this.dataset.h] = this.value; }); });
      body.querySelectorAll("[data-c]").forEach(function (i) { i.addEventListener("input", function () { var p = this.dataset.c.split("_"); spec.rows[+p[0]][+p[1]] = this.value; }); });
      body.querySelectorAll("[data-rmrow]").forEach(function (b) { b.addEventListener("click", function () { spec.rows.splice(+this.dataset.rmrow, 1); render(); }); });
      $("#te-addrow").addEventListener("click", function () { spec.rows.push(spec.headers.map(function () { return ""; })); render(); });
      $("#te-addcol").addEventListener("click", function () { spec.headers.push("Column " + String.fromCharCode(65 + spec.headers.length)); spec.rows.forEach(function (r) { r.push(""); }); render(); });
      $("#te-cancel").addEventListener("click", closeModal);
      $("#te-insert").addEventListener("click", function () {
        if (existing) { snapshot(); existing.payload = spec; setDirty(true); renderSurface(); }
        else { addElement({ type: "table", x: 0.1, y: 0.2, w: 0.8, h: 0.6, payload: spec }); }
        closeModal();
      });
    }
    render(); openModal();
  }

  function uploadImage(file) {
    var fd = new FormData(); fd.append("image", file);
    fetch("/slides/api/" + DECK_ID + "/upload-image", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.url) addElement({ type: "image", x: 0.3, y: 0.25, w: 0.4, h: 0.4, payload: { src: d.url } });
        else alert("Upload failed: " + (d.error || "unknown"));
      }).catch(function (err) { alert("Upload error: " + err.message); });
  }

  function save(isAuto) {
    var payload = { slides: SLIDES.map(function (sl, i) {
      return { slide_id: sl.slideId, position: i + 1, elements: sl.elements || [],
               speaker_notes: sl.notes || "" };
    }) };
    if (!isAuto) $("#save").textContent = "Saving…";
    fetch("/slides/api/" + DECK_ID + "/elements", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); }).then(function (d) {
      $("#save").textContent = "Save";
      if (d.ok) { dirty = false; $("#dirty").textContent = "✓ saved"; }
      else if (!isAuto) alert("Save failed");
    }).catch(function (err) { $("#save").textContent = "Save"; if (!isAuto) alert("Save error: " + err.message); });
  }

  function renderLayers() {
    var list = $("#layers-list"); if (!list) return; list.innerHTML = "";
    els().slice().sort(function (a, b) { return (b.z || 0) - (a.z || 0); }).forEach(function (e) {
      var label = e.type === "text" ? ((e.payload && e.payload.text) || "Text")
        : (e.payload && e.payload.title) ? e.payload.title : e.type;
      var item = el("div", "lay-item" + (e.id === selId ? " sel" : "") + (e.hidden ? " hidden-el" : ""));
      item.innerHTML = "<span class='lay-type'>" + esc(e.type.slice(0, 4)) + "</span>" +
        "<span class='lay-label'>" + esc(String(label).slice(0, 22)) + "</span>" +
        "<button class='ly-up' title='Bring forward'>↑</button>" +
        "<button class='ly-dn' title='Send back'>↓</button>" +
        "<button class='ly-eye' title='Show/Hide'>" + (e.hidden ? "🚫" : "👁") + "</button>" +
        "<button class='ly-lock' title='Lock/Unlock'>" + (e.locked ? "🔒" : "🔓") + "</button>";
      item.addEventListener("click", function (ev) {
        if (ev.target.tagName === "BUTTON") return; selId = e.id; renderSurface(); renderProps(); renderLayers();
      });
      item.querySelector(".ly-up").addEventListener("click", function (ev) { ev.stopPropagation(); selId = e.id; layer(1); renderLayers(); });
      item.querySelector(".ly-dn").addEventListener("click", function (ev) { ev.stopPropagation(); selId = e.id; layer(-1); renderLayers(); });
      item.querySelector(".ly-eye").addEventListener("click", function (ev) { ev.stopPropagation(); snapshot(); e.hidden = !e.hidden; setDirty(true); renderSurface(); renderLayers(); });
      item.querySelector(".ly-lock").addEventListener("click", function (ev) { ev.stopPropagation(); e.locked = !e.locked; setDirty(true); renderLayers(); });
      list.appendChild(item);
    });
  }

  function renderAll() { sizeSurface(); renderStrip(); renderSurface(); renderProps(); renderLayers(); }

  function init() {
    $("#add-text").addEventListener("click", addText);
    $("#add-chart").addEventListener("click", function () { chartEditor(); });
    $("#add-table").addEventListener("click", function () { tableEditor(); });
    $("#add-shape").addEventListener("click", function () { addShape("rectangle"); });
    $("#add-icon").addEventListener("click", iconPicker);
    $("#add-image").addEventListener("click", function () { $("#file-input").click(); });
    $("#zoom-in").addEventListener("click", function () { setZoom(zoom * 1.25); });
    $("#zoom-out").addEventListener("click", function () { setZoom(zoom / 1.25); });
    $("#zoom-fit").addEventListener("click", function () { setZoom(1); });
    $("#file-input").addEventListener("change", function () { if (this.files[0]) uploadImage(this.files[0]); this.value = ""; });
    $("#add-canvas").addEventListener("click", function () { window.location.href = "/slides/" + DECK_ID + "/add-from-canvas"; });
    $("#layer-up").addEventListener("click", function () { layer(1); });
    $("#layer-down").addEventListener("click", function () { layer(-1); });
    $("#delete-el").addEventListener("click", del);
    $("#save").addEventListener("click", save);
    document.addEventListener("keydown", function (e) {
      if (e.target.isContentEditable || e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") {
        if (e.key === "s" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
        return;
      }
      if ((e.key === "Delete" || e.key === "Backspace") && selId) { del(); }
      else if (e.key === "s" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
      else if (e.key === "d" && (e.ctrlKey || e.metaKey) && selId) { e.preventDefault(); dupElement(); }
      else if (e.key.toLowerCase() === "z" && (e.ctrlKey || e.metaKey) && !e.shiftKey) { e.preventDefault(); undo(); }
      else if ((e.key.toLowerCase() === "z" && (e.ctrlKey || e.metaKey) && e.shiftKey) ||
               (e.key.toLowerCase() === "y" && (e.ctrlKey || e.metaKey))) { e.preventDefault(); redo(); }
      else if (selId && e.key.indexOf("Arrow") === 0) { nudge(e.key, e.shiftKey); e.preventDefault(); }
    });
    window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
    window.addEventListener("resize", function () { sizeSurface(); renderSurface(); });
    // click empty surface clears selection
    $("#surface").addEventListener("pointerdown", function (e) { if (e.target.id === "surface") { selId = null; renderSurface(); renderProps(); renderLayers(); } });
    renderAll();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
