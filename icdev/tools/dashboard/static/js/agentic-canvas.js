/**
 * agentic-canvas.js — AADC-specific canvas behaviors
 * Extends design-canvas.js globals (window.paper, window.graph, getNodeColor)
 * Classification: CUI // SP-CTI
 */

(function () {
  "use strict";

  // ── Constants ────────────────────────────────────────────────────────────

  const AUTONOMY_COLORS = {
    0: "#27ae60",
    1: "#2ecc71",
    2: "#f39c12",
    3: "#e67e22",
    4: "#e74c3c",
    5: "#8e44ad",
  };

  const AUTONOMY_LABELS = {
    0: "L0 Human-Operated",
    1: "L1 Human-Delegated",
    2: "L2 Human-Supervised",
    3: "L3 Human-Initiated",
    4: "L4 Fully Autonomous",
    5: "L5 Unconstrained",
  };

  // ── State ─────────────────────────────────────────────────────────────────

  let _assessmentData = null;
  let _costData = null;
  let _riskData = null;
  const _designId = window.CANVAS_CONFIG && window.CANVAS_CONFIG.designId;
  const _apiBase = (window.CANVAS_CONFIG && window.CANVAS_CONFIG.apiBase) || "/api/agentic-ai";

  // ── Init ──────────────────────────────────────────────────────────────────

  function init() {
    _setupToolbarButtons();
    _setupRiskSidebar();
    _setupCostPanel();
    _loadAssessmentOverlays();
    _loadCostIfExists();
    _loadRisks();
  }

  // ── Toolbar buttons ───────────────────────────────────────────────────────

  function _setupToolbarButtons() {
    const toolbar = document.getElementById("aadc-toolbar-extra");
    if (!toolbar) return;

    // Cost estimate button
    const costBtn = _makeBtn("💰 Cost", "aadc-cost-btn", "#1a6496", function () {
      _runCostEstimate();
    });

    // IaC generate button
    const iacBtn = _makeBtn("⚙ IaC", "aadc-iac-btn", "#5a3e85", function () {
      _downloadIaC();
    });

    // Risk sidebar toggle
    const riskBtn = _makeBtn("🛡 Risks", "aadc-risks-btn", "#8e3a2a", function () {
      _toggleRiskSidebar();
    });

    toolbar.appendChild(costBtn);
    toolbar.appendChild(iacBtn);
    toolbar.appendChild(riskBtn);
  }

  function _makeBtn(label, id, bg, onClick) {
    const btn = document.createElement("button");
    btn.id = id;
    btn.textContent = label;
    btn.style.cssText = (
      `background:${bg};color:#fff;border:none;border-radius:4px;` +
      "padding:4px 10px;margin-left:6px;cursor:pointer;font-size:12px;"
    );
    btn.addEventListener("click", onClick);
    return btn;
  }

  // ── Autonomy badges ───────────────────────────────────────────────────────

  function _renderAutonomyBadges() {
    const paper = window.aadcPaper || (window.CANVAS_CONFIG && window.CANVAS_CONFIG._paper);
    if (!paper) return;

    document.querySelectorAll(".aadc-autonomy-badge").forEach((el) => el.remove());

    const cells = paper.model ? paper.model.getCells() : [];
    cells.forEach(function (cell) {
      if (cell.isLink && cell.isLink()) return;
      const props = cell.prop("properties") || cell.prop("attrs/label") || {};
      const level = props.autonomy_level != null ? parseInt(props.autonomy_level, 10) : -1;
      if (level < 0 || level > 5) return;

      const view = paper.findViewByModel(cell);
      if (!view) return;
      const bbox = view.getBBox();

      const badge = document.createElement("div");
      badge.className = "aadc-autonomy-badge";
      badge.title = AUTONOMY_LABELS[level] || `L${level}`;
      badge.style.cssText = (
        `position:absolute;` +
        `left:${bbox.x + bbox.width - 10}px;` +
        `top:${bbox.y - 6}px;` +
        `width:18px;height:18px;border-radius:50%;` +
        `background:${AUTONOMY_COLORS[level]};` +
        `color:#fff;font-size:9px;font-weight:bold;` +
        `display:flex;align-items:center;justify-content:center;` +
        `z-index:10;pointer-events:none;box-shadow:0 1px 3px rgba(0,0,0,.4);`
      );
      badge.textContent = `L${level}`;
      const container = paper.el || paper.$el && paper.$el[0];
      if (container) container.parentElement.appendChild(badge);
    });
  }

  // ── HITL path highlighting ────────────────────────────────────────────────

  function _applyHitlHighlights(assessment) {
    const hitlPaths = (assessment && assessment.hitl_paths) || [];
    const hitlEdgeIds = new Set(hitlPaths.flat());

    const paper = window.aadcPaper;
    if (!paper) return;

    const cells = paper.model ? paper.model.getCells() : [];
    cells.forEach(function (cell) {
      if (!cell.isLink || !cell.isLink()) return;
      const cid = cell.id;
      const view = paper.findViewByModel(cell);
      if (!view) return;

      if (hitlEdgeIds.has(cid)) {
        view.el.querySelectorAll(".connection").forEach((p) => {
          p.style.stroke = "#27ae60";
          p.style.strokeWidth = "3";
        });
      } else {
        // Check if edge connects L3+ node without HITL
        const sourceId = cell.get("source") && cell.get("source").id;
        const sourceCell = sourceId && paper.model.getCell(sourceId);
        const props = sourceCell && (sourceCell.prop("properties") || {});
        const level = props && props.autonomy_level != null ? parseInt(props.autonomy_level, 10) : 0;
        if (level >= 3) {
          view.el.querySelectorAll(".connection").forEach((p) => {
            p.style.stroke = "#e74c3c";
            p.style.strokeWidth = "2";
          });
        }
      }
    });
  }

  // ── Per-node compliance overlays ──────────────────────────────────────────

  function _renderComplianceOverlays(assessment) {
    document.querySelectorAll(".aadc-compliance-overlay").forEach((el) => el.remove());
    if (!assessment || !assessment.findings_json) return;

    const findings = _parseJSON(assessment.findings_json, []);
    const nodeScores = {};
    findings.forEach(function (f) {
      if (!f.node_id) return;
      if (!(f.node_id in nodeScores)) nodeScores[f.node_id] = { pass: 0, fail: 0 };
      if (f.passed) nodeScores[f.node_id].pass++;
      else nodeScores[f.node_id].fail++;
    });

    const paper = window.aadcPaper;
    if (!paper) return;

    Object.entries(nodeScores).forEach(function ([nodeId, counts]) {
      const cell = paper.model && paper.model.getCell(nodeId);
      if (!cell) return;
      const view = paper.findViewByModel(cell);
      if (!view) return;
      const bbox = view.getBBox();

      const total = counts.pass + counts.fail;
      const pct = total > 0 ? Math.round((counts.pass / total) * 100) : 100;
      const color = pct >= 70 ? "#27ae60" : pct >= 50 ? "#f39c12" : "#e74c3c";

      const overlay = document.createElement("div");
      overlay.className = "aadc-compliance-overlay";
      overlay.title = `Compliance: ${pct}% (${counts.fail} issues)`;
      overlay.style.cssText = (
        `position:absolute;` +
        `left:${bbox.x - 8}px;top:${bbox.y + bbox.height - 10}px;` +
        `width:20px;height:20px;border-radius:50%;` +
        `background:${color};color:#fff;font-size:8px;font-weight:bold;` +
        `display:flex;align-items:center;justify-content:center;` +
        `z-index:10;cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,.4);`
      );
      overlay.textContent = pct;
      overlay.addEventListener("click", function () {
        _showFindingsTooltip(nodeId, counts, findings, bbox);
      });

      const container = paper.el || (paper.$el && paper.$el[0]);
      if (container) container.parentElement.appendChild(overlay);
    });
  }

  function _showFindingsTooltip(nodeId, counts, findings, bbox) {
    document.querySelectorAll(".aadc-findings-tooltip").forEach((el) => el.remove());
    const nodeFails = findings.filter((f) => f.node_id === nodeId && !f.passed);

    const tip = document.createElement("div");
    tip.className = "aadc-findings-tooltip";
    tip.style.cssText = (
      `position:absolute;left:${bbox.x + bbox.width + 8}px;top:${bbox.y}px;` +
      "background:#1a2035;border:1px solid #3a4a6b;border-radius:6px;padding:10px;" +
      "min-width:220px;max-width:320px;z-index:100;font-size:12px;color:#e0e6f0;"
    );
    tip.innerHTML = `<strong>Compliance Issues</strong><hr style="border-color:#3a4a6b;margin:6px 0">` +
      (nodeFails.length
        ? nodeFails.map((f) => `<div style="margin:3px 0">⚠ ${f.title || f.check_id || "Issue"}</div>`).join("")
        : '<div style="color:#27ae60">✓ No issues found</div>') +
      `<div style="margin-top:8px;text-align:right"><button onclick="this.closest('.aadc-findings-tooltip').remove()" ` +
      `style="background:transparent;border:1px solid #3a4a6b;color:#e0e6f0;border-radius:3px;padding:2px 8px;cursor:pointer">Close</button></div>`;
    const container = document.getElementById("aadc-canvas-container") || document.body;
    container.appendChild(tip);
  }

  // ── Threat count popovers ─────────────────────────────────────────────────

  function _renderThreatBadges(assessment) {
    document.querySelectorAll(".aadc-threat-badge").forEach((el) => el.remove());
    if (!assessment || !assessment.atlas_threats) return;

    const threats = _parseJSON(assessment.atlas_threats, []);
    const nodeThreats = {};
    threats.forEach(function (t) {
      if (!t.node_id) return;
      if (!nodeThreats[t.node_id]) nodeThreats[t.node_id] = [];
      nodeThreats[t.node_id].push(t.tactic || t.name || "Threat");
    });

    const paper = window.aadcPaper;
    if (!paper) return;

    Object.entries(nodeThreats).forEach(function ([nodeId, tactics]) {
      const cell = paper.model && paper.model.getCell(nodeId);
      if (!cell) return;
      const view = paper.findViewByModel(cell);
      if (!view) return;
      const bbox = view.getBBox();

      const badge = document.createElement("div");
      badge.className = "aadc-threat-badge";
      badge.style.cssText = (
        `position:absolute;` +
        `left:${bbox.x - 6}px;top:${bbox.y - 6}px;` +
        `width:18px;height:18px;border-radius:50%;` +
        `background:#c0392b;color:#fff;font-size:9px;font-weight:bold;` +
        `display:flex;align-items:center;justify-content:center;` +
        `z-index:11;cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,.5);`
      );
      badge.textContent = tactics.length;
      badge.title = tactics.join(", ");

      badge.addEventListener("mouseenter", function (e) {
        _showThreatPopover(e, tactics, bbox);
      });
      badge.addEventListener("mouseleave", function () {
        document.querySelectorAll(".aadc-threat-popover").forEach((el) => el.remove());
      });

      const container = paper.el || (paper.$el && paper.$el[0]);
      if (container) container.parentElement.appendChild(badge);
    });
  }

  function _showThreatPopover(e, tactics, bbox) {
    document.querySelectorAll(".aadc-threat-popover").forEach((el) => el.remove());
    const pop = document.createElement("div");
    pop.className = "aadc-threat-popover";
    pop.style.cssText = (
      `position:absolute;left:${bbox.x + bbox.width + 8}px;top:${bbox.y}px;` +
      "background:#2c1a1a;border:1px solid #8b0000;border-radius:6px;padding:8px;" +
      "min-width:180px;z-index:100;font-size:11px;color:#f5c6c6;pointer-events:none;"
    );
    pop.innerHTML = "<strong>ATLAS Threats</strong><hr style=\"border-color:#8b0000;margin:4px 0\">" +
      tactics.map((t) => `<div>• ${t}</div>`).join("");
    const container = document.getElementById("aadc-canvas-container") || document.body;
    container.appendChild(pop);
  }

  // ── Risk register sidebar ─────────────────────────────────────────────────

  function _setupRiskSidebar() {
    let sidebar = document.getElementById("aadc-risk-sidebar");
    if (sidebar) return;

    sidebar = document.createElement("div");
    sidebar.id = "aadc-risk-sidebar";
    sidebar.style.cssText = (
      "position:fixed;right:-340px;top:120px;width:320px;height:calc(100vh - 140px);" +
      "background:#0f1a2e;border-left:1px solid #2a3a5b;z-index:200;" +
      "overflow-y:auto;transition:right .3s ease;padding:12px;" +
      "font-size:12px;color:#c8d8f0;"
    );
    sidebar.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <strong style="font-size:14px">🛡 Risk Register</strong>
        <button onclick="document.getElementById('aadc-risk-sidebar').style.right='-340px'"
          style="background:transparent;border:none;color:#c8d8f0;cursor:pointer;font-size:16px">×</button>
      </div>
      <div id="aadc-risk-list"><p style="color:#6a7a9a">Loading risks…</p></div>
    `;
    document.body.appendChild(sidebar);
  }

  function _toggleRiskSidebar() {
    const sidebar = document.getElementById("aadc-risk-sidebar");
    if (!sidebar) return;
    const open = sidebar.style.right === "0px";
    sidebar.style.right = open ? "-340px" : "0px";
    if (!open) _renderRisks();
  }

  function _renderRisks() {
    const list = document.getElementById("aadc-risk-list");
    if (!list) return;
    if (!_riskData) {
      list.innerHTML = '<p style="color:#6a7a9a">No risks loaded.</p>';
      return;
    }
    const bySev = { CRITICAL: [], HIGH: [], MEDIUM: [], LOW: [] };
    _riskData.forEach(function (r) {
      const sev = (r.severity || "LOW").toUpperCase();
      if (bySev[sev]) bySev[sev].push(r);
      else bySev.LOW.push(r);
    });
    const sevColors = { CRITICAL: "#c0392b", HIGH: "#e74c3c", MEDIUM: "#f39c12", LOW: "#27ae60" };
    let html = "";
    Object.entries(bySev).forEach(function ([sev, risks]) {
      if (!risks.length) return;
      html += `<div style="margin-bottom:8px">
        <span style="background:${sevColors[sev]};color:#fff;padding:2px 7px;border-radius:3px;font-size:10px;font-weight:bold">${sev}</span>
        <span style="margin-left:6px;color:#8a9aba">${risks.length} risk${risks.length !== 1 ? "s" : ""}</span>
      </div>`;
      risks.forEach(function (r) {
        const mitigated = r.status === "mitigated";
        html += `<div style="background:#162035;border-radius:4px;padding:8px;margin-bottom:6px;opacity:${mitigated ? ".5" : "1"}">
          <div style="font-weight:bold;margin-bottom:3px">${_esc(r.title || r.risk_id || "Risk")}</div>
          <div style="color:#8a9aba;margin-bottom:5px">${_esc(r.description || "")}</div>
          ${mitigated
            ? '<span style="color:#27ae60">✓ Mitigated</span>'
            : `<button onclick="window.aadcMitigateRisk('${r.id}')"
               style="background:#1a6496;color:#fff;border:none;border-radius:3px;padding:2px 8px;cursor:pointer;font-size:11px">
               Mitigate</button>`}
        </div>`;
      });
    });
    list.innerHTML = html || '<p style="color:#6a7a9a">No risks registered.</p>';
  }

  window.aadcMitigateRisk = function (riskId) {
    if (!_designId) return;
    fetch(`${_apiBase}/designs/${_designId}/risks/${riskId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "mitigated" }),
    })
      .then((r) => r.json())
      .then(function () {
        _loadRisks();
        _renderRisks();
      });
  };

  // ── Cost panel ────────────────────────────────────────────────────────────

  function _setupCostPanel() {
    const props = document.getElementById("aadc-props-panel") ||
      document.querySelector(".aadc-properties-panel");
    if (!props) return;

    const panel = document.createElement("div");
    panel.id = "aadc-cost-panel";
    panel.style.cssText = (
      "margin-top:12px;border-top:1px solid #2a3a5b;padding-top:10px;display:none;"
    );
    panel.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer"
           onclick="document.getElementById('aadc-cost-body').style.display=
             document.getElementById('aadc-cost-body').style.display==='none'?'block':'none'">
        <strong style="font-size:12px">💰 Cost Estimate</strong>
        <span id="aadc-cost-badge" style="font-size:11px;color:#3498db">—</span>
      </div>
      <div id="aadc-cost-body" style="display:none;margin-top:8px"></div>
    `;
    props.appendChild(panel);
  }

  function _refreshCostPanel() {
    const panel = document.getElementById("aadc-cost-panel");
    const body = document.getElementById("aadc-cost-body");
    const badge = document.getElementById("aadc-cost-badge");
    if (!panel || !_costData) return;
    panel.style.display = "block";
    badge.textContent = `$${_costData.total_per_run.toFixed(5)}/run`;

    let html = `<table style="width:100%;font-size:11px;border-collapse:collapse">
      <tr style="color:#6a7a9a"><th style="text-align:left">Model</th><th>$/run</th><th>$/mo</th></tr>`;
    Object.entries(_costData.model_breakdown || {}).forEach(function ([m, info]) {
      const local = info.is_local ? " 🏠" : "";
      html += `<tr>
        <td style="padding:2px 0">${m}${local}</td>
        <td style="text-align:right">$${(info.cost_per_run * (info.node_ids || []).length).toFixed(5)}</td>
        <td style="text-align:right">$${info.monthly_cost.toFixed(2)}</td>
      </tr>`;
    });
    html += `<tr style="font-weight:bold;border-top:1px solid #2a3a5b">
      <td>Total</td>
      <td style="text-align:right">$${_costData.total_per_run.toFixed(5)}</td>
      <td style="text-align:right">$${_costData.total_monthly.toFixed(2)}/mo</td>
    </tr></table>`;

    if (_costData.optimization_hints && _costData.optimization_hints.length) {
      html += `<div style="margin-top:8px;font-size:11px">`;
      _costData.optimization_hints.forEach(function (h) {
        html += `<div style="background:#1a2035;border-left:3px solid #f39c12;padding:4px 6px;margin-bottom:4px;border-radius:0 3px 3px 0">${_esc(h)}</div>`;
      });
      html += `</div>`;
    }

    html += `<button onclick="window.aadcRunCostEstimate()"
      style="margin-top:8px;background:#1a4a6e;color:#fff;border:none;border-radius:3px;
      padding:4px 10px;cursor:pointer;font-size:11px;width:100%">↻ Refresh</button>`;

    body.innerHTML = html;
    body.style.display = "block";
  }

  // ── Data loaders ──────────────────────────────────────────────────────────

  function _loadAssessmentOverlays() {
    if (!_designId) return;
    fetch(`${_apiBase}/designs/${_designId}/assessments?limit=1`)
      .then((r) => r.ok ? r.json() : null)
      .then(function (data) {
        if (!data) return;
        const assessment = Array.isArray(data) ? data[0] : (data.assessments && data.assessments[0]);
        if (!assessment) return;
        _assessmentData = assessment;
        _renderAutonomyBadges();
        _applyHitlHighlights(assessment);
        _renderComplianceOverlays(assessment);
        _renderThreatBadges(assessment);
      })
      .catch(() => {});
  }

  function _loadRisks() {
    if (!_designId) return;
    fetch(`${_apiBase}/designs/${_designId}/risks`)
      .then((r) => r.ok ? r.json() : null)
      .then(function (data) {
        if (!data) return;
        _riskData = Array.isArray(data) ? data : (data.risks || []);
        _renderRisks();
      })
      .catch(() => {});
  }

  function _loadCostIfExists() {
    if (!_designId) return;
    // Cost estimates are created on demand — don't auto-load, just surface the panel if data exists
    // Panel will show after manual trigger or if estimate was previously stored
  }

  // ── Actions ───────────────────────────────────────────────────────────────

  function _runCostEstimate() {
    if (!_designId) return;
    const btn = document.getElementById("aadc-cost-btn");
    if (btn) btn.textContent = "💰 …";
    fetch(`${_apiBase}/designs/${_designId}/cost-estimate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ runs_per_month: 1000 }),
    })
      .then((r) => r.json())
      .then(function (data) {
        _costData = data;
        _refreshCostPanel();
        if (btn) btn.textContent = "💰 Cost";
        _toast("Cost estimate updated", "success");
      })
      .catch(function () {
        if (btn) btn.textContent = "💰 Cost";
        _toast("Cost estimate failed", "error");
      });
  }

  window.aadcRunCostEstimate = _runCostEstimate;

  function _downloadIaC() {
    if (!_designId) return;
    const btn = document.getElementById("aadc-iac-btn");
    if (btn) btn.textContent = "⚙ …";
    const url = `${_apiBase}/designs/${_designId}/iac`;
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () {
      if (btn) btn.textContent = "⚙ IaC";
      _toast("IaC bundle downloading…", "info");
    }, 500);
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function _parseJSON(val, fallback) {
    if (Array.isArray(val) || (val && typeof val === "object")) return val;
    try { return JSON.parse(val); } catch (_) { return fallback; }
  }

  function _esc(str) {
    return String(str || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function _toast(msg, type) {
    const colors = { success: "#27ae60", error: "#e74c3c", info: "#2980b9" };
    const t = document.createElement("div");
    t.style.cssText = (
      `position:fixed;bottom:24px;right:24px;background:${colors[type] || "#555"};` +
      "color:#fff;padding:8px 16px;border-radius:6px;font-size:13px;z-index:9999;" +
      "box-shadow:0 2px 8px rgba(0,0,0,.3);"
    );
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3000);
  }

  // ── Wire to assessment refresh events ────────────────────────────────────

  document.addEventListener("aadc:assessment:complete", function (e) {
    if (!e.detail) return;
    _assessmentData = e.detail;
    _renderAutonomyBadges();
    _applyHitlHighlights(e.detail);
    _renderComplianceOverlays(e.detail);
    _renderThreatBadges(e.detail);
  });

  // ── Boot ──────────────────────────────────────────────────────────────────

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
