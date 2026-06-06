# CUI // SP-CTI
# Phase 6 — AADC Lifecycle & Deployment Readiness

**Canvas:** Agentic AI Design Canvas (AADC)  
**Epic key:** aadc-p6  
**Shipped:** 2026-05-04  
**Classification:** CUI // SP-CTI

---

## Summary

Phase 6 closes the AADC governance loop with four deployment-readiness features: an interactive ATO readiness checklist, a regulatory gap tracker (EU AI Act / DoD AI Ethics / OMB), a side-by-side design comparison engine, and an executive summary report for CISO/C-suite briefings.

---

## Features Shipped

### 1. ATO Readiness Checklist (`/agentic-ai/ato/<id>`)
- 15 checklist items across FedRAMP, OMB M-25-21, DoD AI Ethics, CMMC L2
- Each item maps to a specific NIST 800-53 / OMB control and checks for required node types
- Domain-filtered items (safety-impacting / rights-impacting) — only shown when relevant
- Summary cards: total checks, passed, failed, score%, ATO ready/not-ready badge
- Per-framework score bars
- Remediation advice for each failed item

### 2. Regulatory Tracker (`canvas AADC menu → 📜 Regulatory`)
- Modal with "↻ Analyze" button — calls `GET /api/designs/<id>/regulatory`
- 14 requirements across EU AI Act, DoD AI Ethics, OMB M-25-21, OMB M-26-04
- Shows: compliant/gap status, severity, framework scores as mini-bars
- Results persisted in `aadc_regulatory_gaps` table

### 3. Design Comparison (`⚖ button on each design card`)
- Pick any two designs; compare side-by-side in a modal
- Metrics: Overall / NIST AI RMF / OWASP scores, open risks, node count, autonomy level
- Δ column shows improvement/regression in green/red
- Node delta: lists nodes added/removed between designs
- Winner declared in summary verdict
- Backed by `POST /api/designs/compare`

### 4. Executive Summary Report (`/agentic-ai/exec-summary/<id>`)
- Combined posture score: 50% assessment + 30% ATO + 20% regulatory
- Posture rating: EXCELLENT / GOOD / FAIR / POOR / UNRATED with color coding
- Top 3 open risks + Top 3 threats (from STRIDE/ATLAS threat model)
- Key Strengths + Critical Gaps (sourced from assessment findings + ATO + regulatory)
- Recommended Actions list (max 5, prioritized by CRITICAL severity)
- ATO + Regulatory score bars by framework
- Print-friendly (native browser print button)
- Linked from: index page card (📊 Brief), ATO page, canvas AADC menu

---

## New Files

| File | Purpose |
|------|---------|
| `tools/agentic_ai_canvas/ato_readiness.py` | ATO checklist engine (15 items, 4 frameworks) |
| `tools/agentic_ai_canvas/regulatory_tracker.py` | Regulatory gap analysis (14 reqs, 4 frameworks) |
| `tools/agentic_ai_canvas/design_compare.py` | Two-design comparison engine |
| `tools/agentic_ai_canvas/exec_summary.py` | Executive summary report generator |
| `tools/dashboard/templates/agentic_ai_canvas/ato.html` | ATO readiness page |
| `tools/dashboard/templates/agentic_ai_canvas/exec_summary.html` | Executive brief page |
| `tools/db/migrations/108_aadc_phase6.sql` | DDL for aadc_ato_reports + aadc_regulatory_gaps |

---

## New DB Tables

| Table | Purpose |
|-------|---------|
| `aadc_ato_reports` | ATO readiness report snapshots per design |
| `aadc_regulatory_gaps` | Regulatory gap analysis snapshots per design |

---

## New API Routes

| Method + Route | Purpose |
|----------------|---------|
| `GET /agentic-ai/ato/<id>` | ATO readiness page |
| `GET /agentic-ai/exec-summary/<id>` | Executive summary page |
| `GET /agentic-ai/api/designs/<id>/ato` | ATO readiness JSON |
| `GET /agentic-ai/api/designs/<id>/regulatory` | Regulatory gap analysis JSON |
| `GET /agentic-ai/api/designs/<id>/exec-summary` | Executive summary JSON |
| `POST /agentic-ai/api/designs/compare` | Compare two designs |

---

## Compliance Coverage

| Feature | Frameworks |
|---------|-----------|
| ATO Readiness | FedRAMP High, OMB M-25-21, DoD AI Ethics, CMMC L2 |
| Regulatory Tracker | EU AI Act (Art. 9-15), DoD AI Ethics, OMB M-25-21, OMB M-26-04 |
| Executive Summary | Synthesizes all AADC assessments into CISO-ready brief |

---

*CUI // SP-CTI — ICDEV™ AADC Phase 6*
