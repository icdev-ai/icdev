# CUI // SP-CTI
# Phase 5 — AADC Governance & Reporting

**Canvas:** Agentic AI Design Canvas (AADC)  
**Epic key:** aadc-p5  
**Shipped:** 2026-05-03  
**Classification:** CUI // SP-CTI

---

## Summary

Phase 5 adds four governance and compliance reporting features to the AADC canvas, enabling DoD/Gov teams to manage AI system risk, run automated threat modeling, track portfolio health, and export OSCAL artifacts for FedRAMP/ATO packages.

---

## Features Shipped

### 1. Risk Register (`/agentic-ai/risks/<id>`)
- Full-page risk register per design (new template: `risks.html`)
- CRUD: add/edit/delete risk items with title, description, category, severity, likelihood, status, owner, mitigation
- **Import Findings** — auto-promotes assessment findings to risk items via `POST /api/designs/<id>/risks/import-findings`
- Summary cards: Total / Open / Critical Open / Mitigated / Residual Risk
- Color-coded severity (CRITICAL/HIGH/MEDIUM/LOW) and status badges
- Index page now shows "⚠ Risks" button on every design card

### 2. STRIDE + ATLAS Threat Model
- **Canvas menu entry:** AADC → 🗡 Threat Model
- Modal with "↻ Generate" button — calls `POST /api/designs/<id>/threat-model`
- 11 STRIDE rules covering all 6 categories (Spoofing/Tampering/Repudiation/Information Disclosure/DoS/Elevation of Privilege)
- MITRE ATLAS TTP mapping (11 entries across 7 tactics) per node type
- Persisted in `aadc_threat_models` table; re-generating overwrites previous
- Renders: summary bar, STRIDE findings table, ATLAS TTPs list

### 3. Portfolio Analytics (index page)
- Auto-loads on `/agentic-ai/` page — no user action required
- Cross-design aggregation: avg score, compliance bands (green ≥70 / amber 50–70 / red <50 / unscored)
- Portfolio health badge: `AT_RISK` / `COMPLIANT` / `IMPROVING` / `NON_COMPLIANT`
- Open risk counts, autonomy distribution, top 5 designs by score
- Backed by `GET /api/portfolio`

### 4. OSCAL Export
- **Canvas menu entry:** AADC → 📋 OSCAL Export
- Generates NIST OSCAL 1.1 Component Definition JSON and triggers browser download
- Maps 25 AADC node types to NIST SP 800-53 Rev 5 controls (e.g., `hitl-gate` → AC-3/AC-6/CM-9/SI-12)
- Emits `export_oscal` activity feed event
- Status bar shows control count after export

---

## New Files

| File | Purpose |
|------|---------|
| `tools/agentic_ai_canvas/risk_register.py` | Risk CRUD helpers + `summarize_register()` |
| `tools/agentic_ai_canvas/threat_model.py` | STRIDE + ATLAS generator |
| `tools/agentic_ai_canvas/portfolio.py` | Cross-design aggregation |
| `tools/agentic_ai_canvas/oscal_export.py` | OSCAL 1.1 Component Definition export |
| `tools/dashboard/templates/agentic_ai_canvas/risks.html` | Risk register page |
| `tools/db/migrations/107_aadc_phase5.sql` | DDL for aadc_risk_items + aadc_threat_models |

---

## New DB Tables

| Table | Purpose |
|-------|---------|
| `aadc_risk_items` | Per-design risk register items |
| `aadc_threat_models` | STRIDE + ATLAS threat model snapshots |

---

## New API Routes

| Method + Route | Purpose |
|----------------|---------|
| `GET /agentic-ai/risks/<id>` | Risk register page |
| `GET/POST /agentic-ai/api/designs/<id>/risks` | List / create risks |
| `PUT/DELETE /agentic-ai/api/designs/<id>/risks/<rid>` | Update / delete risk |
| `POST /agentic-ai/api/designs/<id>/risks/import-findings` | Promote findings to risks |
| `GET/POST /agentic-ai/api/designs/<id>/threat-model` | Get / generate threat model |
| `GET /agentic-ai/api/portfolio` | Portfolio analytics |
| `GET /agentic-ai/api/designs/<id>/oscal` | OSCAL export |
| `GET /agentic-ai/api/designs/<id>/oscal/control-coverage` | Control coverage summary |

---

## Compliance Mapping

| Feature | Framework |
|---------|-----------|
| Risk Register | NIST AI RMF MANAGE function |
| Threat Model (STRIDE) | NIST SP 800-30, STRIDE |
| Threat Model (ATLAS) | MITRE ATLAS |
| OSCAL Export | NIST OSCAL 1.1 / FedRAMP / ATO packages |
| Portfolio Analytics | NIST AI RMF GOVERN function |

---

*CUI // SP-CTI — ICDEV™ AADC Phase 5*
