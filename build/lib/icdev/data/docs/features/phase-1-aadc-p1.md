# CUI // SP-CTI
# Phase 1 — AADC Canvas Parity

**Canvas:** Agentic AI Design Canvas (AADC)  
**Epic key:** aadc-p1  
**Shipped:** 2026-04-30  
**Classification:** CUI // SP-CTI

---

## Summary

Phase 1 brings the AADC canvas to full parity with professional diagramming tools by adding keyboard-driven undo/redo, rubber-band multi-select, copy/paste with smart offset, three export formats (draw.io XML, PDF, CSV), a compliance legend overlay, and a full version diff viewer backed by a new `aadc_design_versions` table.

---

## Features Shipped

### 1. Undo/Redo History Stack
- `Ctrl+Z` / `Ctrl+Y` (or `Ctrl+Shift+Z`) in canvas.html
- History capped at 50 states; stored in JS memory per session
- Covers: node add/move/delete, edge add/delete, property edits

### 2. Multi-Select
- Rubber-band drag-select: hold and drag on empty canvas to lasso nodes
- `Shift+click` to toggle individual nodes into/out of selection
- Move, delete, copy all selected nodes as a group

### 3. Copy/Paste
- `Ctrl+C` copies selected nodes (and edges between them)
- `Ctrl+V` pastes with +20px/+20px offset to avoid overlap
- New UUIDs assigned to pasted nodes; preserves labels and types

### 4. Export to draw.io XML
- Client-side: serializes canvas graph to draw.io MXGraph XML
- Triggers download as `<design-name>.drawio`
- Nodes map to draw.io shapes; edges map to connectors

### 5. PDF Export (`tools/agentic_ai_canvas/export_pdf.py`)
- `GET /agentic-ai/api/designs/<id>/export/pdf` → PDF download
- Server-side via ReportLab; renders node list, edge list, assessment summary
- CUI classification banner on every page

### 6. CSV Export
- Client-side: exports nodes CSV and edges CSV as two files
- Columns: id, label, type, autonomy_level, properties (nodes); source, target, label (edges)

### 7. Compliance Legend Overlay
- Toggle button in canvas toolbar
- Color-coded legend panel: green (safe), amber (caution), red (risk) by node category
- Overlays color indicators on canvas nodes matching NIST AI RMF / OWASP risk tiers

### 8. Version Diff Viewer
- DB: `aadc_design_versions` table — snapshot per save (graph_json, version label, created_at)
- Backend: `tools/agentic_ai_canvas/version_diff.py` — `diff_versions(v1, v2)` → {added, removed, changed} node/edge sets
- UI: version panel in canvas.html — list all saved versions, select any two to compare, visual diff overlay (green = added, red = removed, amber = changed)

---

## New Files

| File | Purpose |
|------|---------|
| `tools/agentic_ai_canvas/export_pdf.py` | Server-side PDF export (ReportLab) |
| `tools/agentic_ai_canvas/version_diff.py` | Graph version diff engine |

---

## New DB Tables

| Table | Purpose |
|-------|---------|
| `aadc_design_versions` | Version snapshots per design (auto-saved on every PUT) |

---

## New API Routes

| Method + Route | Purpose |
|----------------|---------|
| `GET /agentic-ai/api/designs/<id>/export/pdf` | PDF download |
| `GET /agentic-ai/api/designs/<id>/versions` | List all saved versions |
| `GET /agentic-ai/api/designs/<id>/versions/<vid>/diff` | Diff two versions |

---

*CUI // SP-CTI — ICDEV™ AADC Phase 1*
