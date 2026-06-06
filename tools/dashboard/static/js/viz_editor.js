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
  var cur = 0, selId = null, dirty = false, _id = 0, gesture = null;

  function $(s, r) { return (r || document).querySelector(s); }
  function el(tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; }
  function uid(p) { _id += 1; return (p || "e") + "_" + _id + "_" + Date.now() % 100000; }
  function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function seriesColor(i) { var s = COLORS.series || ["#C8A951"]; return s[i % s.length]; }
  function curSlide() { return SLIDES[cur] || { elements: [] }; }
  function els() { return curSlide().elements || (curSlide().elements = []); }
  function selEl() { return els().filter(function (e) { return e.id === selId; })[0] || null; }
  function setDirty(v) { dirty = v; $("#dirty").textContent = v ? "● unsaved" : ""; }

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
        (st.italic ? "italic" : "normal") + ";text-align:" + (st.align || "left") + ";width:100%;height:100%;";
      d.textContent = (e.payload && e.payload.text) || "";
      d.addEventListener("dblclick", function () {
        d.contentEditable = "true"; d.focus();
        d.addEventListener("blur", function () {
          d.contentEditable = "false"; e.payload.text = d.innerText; setDirty(true);
        }, { once: true });
      });
      body.appendChild(d);
    } else if (e.type === "image") {
      var img = el("img"); img.src = (e.payload && e.payload.src) || ""; body.appendChild(img);
    } else if (e.type === "chart") {
      var cid = uid("chart"); var h = el("div"); h.id = cid; h.style.cssText = "width:100%;height:100%;";
      body.appendChild(h); renderChart(cid, e.payload);
    } else if (e.type === "table") {
      var p = e.payload || {}, html = "<table><thead><tr>";
      (p.headers || []).forEach(function (x) { html += "<th>" + esc(x) + "</th>"; });
      html += "</tr></thead><tbody>";
      (p.rows || []).forEach(function (r) { html += "<tr>" + r.map(function (c) { return "<td>" + esc(c) + "</td>"; }).join("") + "</tr>"; });
      body.innerHTML = html + "</tbody></table>";
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
    } else if (e.type === "dashboard") {
      body.innerHTML = "<div style='color:" + (COLORS.accent || "#C8A951") +
        ";padding:10px;border:1px dashed " + (COLORS.accent || "#C8A951") + ";border-radius:8px'>📊 Dashboard: " +
        esc((e.payload && e.payload.title) || "") + "</div>";
    }
  }

  /* ---- surface sizing + render ---- */
  function sizeSurface() {
    var wrap = $("#stagewrap"), s = $("#surface");
    var availW = wrap.clientWidth - 40, availH = wrap.clientHeight - 40;
    var w = availW, h = w * 9 / 16;
    if (h > availH) { h = availH; w = h * 16 / 9; }
    s.style.width = w + "px"; s.style.height = h + "px";
  }

  function renderSurface() {
    var s = $("#surface"); s.innerHTML = "";
    var list = els().slice().sort(function (a, b) { return (a.z || 0) - (b.z || 0); });
    list.forEach(function (e) {
      var d = el("div", "el" + (e.id === selId ? " sel" : ""));
      d.dataset.id = e.id;
      d.style.left = (e.x * 100) + "%"; d.style.top = (e.y * 100) + "%";
      d.style.width = (e.w * 100) + "%"; d.style.height = (e.h * 100) + "%";
      d.style.zIndex = e.z || 0;
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
      t.innerHTML = "<span class='tn'>" + (i + 1) + "</span> " + esc(sl.title || ("Slide " + (i + 1)));
      t.addEventListener("click", function () { cur = i; selId = null; renderAll(); });
      strip.appendChild(t);
    });
  }

  /* ---- drag + resize ---- */
  function onPointerDown(ev) {
    var node = ev.currentTarget; var id = node.dataset.id;
    selId = id; renderProps();
    document.querySelectorAll(".el").forEach(function (n) { n.classList.toggle("sel", n.dataset.id === id); });
    if (ev.target.classList.contains("handle")) {
      gesture = { mode: "resize", dir: ev.target.dataset.dir };
    } else {
      if (ev.target.isContentEditable) return;  // let text editing happen
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
  function onPointerUp() { document.removeEventListener("pointermove", onPointerMove); gesture = null; setDirty(true); renderProps(); }

  /* ---- properties panel ---- */
  function renderProps() {
    var p = $("#props"); var e = selEl();
    if (!e) { p.innerHTML = "<div class='empty'>Select an element to edit its properties.</div>"; return; }
    var rows = "<h4>" + e.type.toUpperCase() + " ELEMENT</h4>";
    if (e.type === "text") {
      var st = e.style || (e.style = {});
      rows += row("Font size", "<input type=number id=p-size min=8 max=120 value='" + (st.fontSize || 18) + "'>");
      rows += row("Font", "<select id=p-font>" + FONTS.map(function (f) {
        return "<option" + (f === st.fontFamily ? " selected" : "") + ">" + f + "</option>"; }).join("") + "</select>");
      rows += row("Color", "<input type=color id=p-color value='" + (st.color || "#ffffff") + "'>");
      rows += row("Style", "<span class=seg><button id=p-bold class='" + (st.bold ? "on" : "") + "'>B</button>" +
        "<button id=p-italic class='" + (st.italic ? "on" : "") + "'>I</button></span>");
      rows += row("Align", "<span class=seg><button data-al=left>L</button><button data-al=center>C</button><button data-al=right>R</button></span>");
    }
    rows += "<div class='hint'>Drag to move · corner handles to resize · double-click text to edit.</div>";
    p.innerHTML = rows;
    if (e.type === "text") wireTextProps(e);
  }
  function row(label, ctrl) { return "<div class='row'><label>" + label + "</label>" + ctrl + "</div>"; }
  function wireTextProps(e) {
    var st = e.style;
    $("#p-size").addEventListener("input", function () { st.fontSize = parseInt(this.value) || 18; refresh(); });
    $("#p-font").addEventListener("change", function () { st.fontFamily = this.value; refresh(); });
    $("#p-color").addEventListener("input", function () { st.color = this.value; refresh(); });
    $("#p-bold").addEventListener("click", function () { st.bold = !st.bold; this.classList.toggle("on"); refresh(); });
    $("#p-italic").addEventListener("click", function () { st.italic = !st.italic; this.classList.toggle("on"); refresh(); });
    document.querySelectorAll("#props [data-al]").forEach(function (b) {
      b.addEventListener("click", function () { st.align = b.dataset.al; refresh(); });
    });
  }
  function refresh() { setDirty(true); renderSurface(); }

  /* ---- toolbar actions ---- */
  function addElement(e) { e.id = uid(e.type); e.z = (els().reduce(function (m, x) { return Math.max(m, x.z || 0); }, 0)) + 1; els().push(e); selId = e.id; setDirty(true); renderAll(); }
  function addText() { addElement({ type: "text", x: 0.3, y: 0.4, w: 0.4, h: 0.12, payload: { text: "New text" },
    style: { fontSize: 24, fontFamily: "Segoe UI", color: COLORS.text || "#ffffff", bold: false, italic: false, align: "left" } }); }
  function layer(dir) { var e = selEl(); if (!e) return; e.z = (e.z || 0) + dir; setDirty(true); renderSurface(); }
  function del() { var e = selEl(); if (!e) return; curSlide().elements = els().filter(function (x) { return x.id !== e.id; }); selId = null; setDirty(true); renderAll(); }

  function uploadImage(file) {
    var fd = new FormData(); fd.append("image", file);
    fetch("/slides/api/" + DECK_ID + "/upload-image", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.url) addElement({ type: "image", x: 0.3, y: 0.25, w: 0.4, h: 0.4, payload: { src: d.url } });
        else alert("Upload failed: " + (d.error || "unknown"));
      }).catch(function (err) { alert("Upload error: " + err.message); });
  }

  function save() {
    var payload = { slides: SLIDES.map(function (sl, i) { return { position: i + 1, elements: sl.elements || [] }; }) };
    $("#save").textContent = "Saving…";
    fetch("/slides/api/" + DECK_ID + "/elements", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); }).then(function (d) {
      $("#save").textContent = "Save"; if (d.ok) setDirty(false); else alert("Save failed");
    }).catch(function (err) { $("#save").textContent = "Save"; alert("Save error: " + err.message); });
  }

  function renderAll() { sizeSurface(); renderStrip(); renderSurface(); renderProps(); }

  function init() {
    $("#add-text").addEventListener("click", addText);
    $("#add-image").addEventListener("click", function () { $("#file-input").click(); });
    $("#file-input").addEventListener("change", function () { if (this.files[0]) uploadImage(this.files[0]); this.value = ""; });
    $("#add-canvas").addEventListener("click", function () { window.location.href = "/slides/" + DECK_ID + "/add-from-canvas"; });
    $("#layer-up").addEventListener("click", function () { layer(1); });
    $("#layer-down").addEventListener("click", function () { layer(-1); });
    $("#delete-el").addEventListener("click", del);
    $("#save").addEventListener("click", save);
    document.addEventListener("keydown", function (e) {
      if ((e.key === "Delete" || e.key === "Backspace") && selId && !e.target.isContentEditable) { del(); }
      if (e.key === "s" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
    });
    window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
    window.addEventListener("resize", function () { sizeSurface(); renderSurface(); });
    // click empty surface clears selection
    $("#surface").addEventListener("pointerdown", function (e) { if (e.target.id === "surface") { selId = null; renderSurface(); renderProps(); } });
    renderAll();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
