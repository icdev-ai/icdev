# CUI // SP-CTI

# SEC: Canvas Classification Banners — All Canvases

**Project:** sec — Security Classification Banners  
**Tasks:** sec-cvs-08a through sec-cvs-08d  
**Date:** 2026-06-14  
**Status:** COMPLETE

---

## Summary

Per-design classification banners wired across all 13+ canvas templates using the standardized macros from `tools/dashboard/templates/includes/classification_macros.html`.

---

## Task Coverage

### sec-cvs-08a — IDC + NDC + SDC (Done prior)

- Infrastructure Design Canvas (`infra_canvas/`)
- Network Design Canvas (`network/`)
- Security Design Canvas (`security_canvas/`)
- Per-row `design_classification_badge` in list views
- `design_classification_banner` in detail/canvas views

### sec-cvs-08b — BDC + PDC + ODC

- **BDC** (Blockchain): Canvas does not exist; skipped.
- **PDC** (Pipeline): Already implemented prior to this task.
- **ODC** (Ops Hub, 8 templates): Added `design_classification_banner(classification)` to all 8 pages (`index`, `llm`, `incidents`, `models`, `slos`, `runbooks`, `self_healing`, `topology`). Removed raw `— {{ classification }}` from subtitle `<p>` tags. Ops Hub is a monitoring dashboard (not a per-design canvas) so page-level banner replaces per-row badge.

### sec-cvs-08c — DDC + QDC + MDC (Found already complete)

- **DDC** (Data Canvas): Already implemented.
- **QDC** (Quality Canvas): Already implemented.
- **MDC** (Migration Canvas): Already implemented.

### sec-cvs-08d — AADC + AIMC + OHC (Done prior)

- Agentic AI Design Canvas (`agentic_ai_canvas/`)
- AI/ML Canvas (`aiml_canvas/`)
- Ops Hub Canvas (covered by sec-cvs-08b for remaining pages)

---

## Macro Reference

**`design_classification_banner(design_or_cls, compartments=None, readonly=False)`**
- Full-page banner for detail/page views
- Auto-detects `design.classification` or accepts string directly (for page-level like Ops Hub)
- Renders compartment tags if present
- Auto-computes read-only indicator when user clearance < design classification

**`design_classification_badge(design_or_cls, compartments=None)`**
- Inline badge for list/table rows
- CSS class `badge-{CLASSIFICATION}` (PUBLIC=green, CUI=blue, SECRET=orange, TS=red, TS//SCI=purple)

---

## Files Changed (sec-cvs-08b ODC)

- `tools/dashboard/templates/ops_hub/index.html`
- `tools/dashboard/templates/ops_hub/llm.html`
- `tools/dashboard/templates/ops_hub/incidents.html`
- `tools/dashboard/templates/ops_hub/models.html`
- `tools/dashboard/templates/ops_hub/slos.html`
- `tools/dashboard/templates/ops_hub/runbooks.html`
- `tools/dashboard/templates/ops_hub/self_healing.html`
- `tools/dashboard/templates/ops_hub/topology.html`
- All above mirrored to `icdev/tools/dashboard/templates/ops_hub/`

---

*CUI // SP-CTI — Handle per DoD 5200.48 and DoDI 8582.01*
