# CUI // SP-CTI
# Phase 9 — AADC Unified Scorecard, Deployment Gate & Findings Inbox

**Canvas:** Agentic AI Design Canvas (AADC)  
**Epic key:** aadc-p9  
**Shipped:** 2026-05-03  
**Classification:** CUI // SP-CTI

---

## Summary

Phase 9 synthesizes all prior AADC analysis phases into three capstone features: a unified 8-dimension design scorecard that collapses every governance metric into one health score, a deployment readiness gate that produces APPROVED/CONDITIONAL/BLOCKED verdicts with a downloadable CI/CD YAML, and a cross-design findings inbox that aggregates all findings from assessments, lint, red team, ATO, regulatory, and risk register into a single searchable feed.

---

## Features Shipped

### 1. Unified Design Scorecard (`/agentic-ai/scorecard/<id>`)
- 8 weighted dimensions: Assessment (25%), ATO Readiness (20%), Regulatory (15%), Red Team Resilience (15%), Lint Quality (10%), Structural Resilience (10%), Risk Posture (5%)
- Per-dimension status: green (≥80) / amber (≥60) / red (<60) / missing
- Overall health label: HEALTHY / AT_RISK / DEGRADED / CRITICAL
- Weighted average health score (0-100%)
- Quick-action links to all sub-pages
- 🎯 Scorecard button on every design card on index page + canvas AADC menu

### 2. Deployment Gate (`/agentic-ai/deploy-gate/<id>`)
- Verdict: APPROVED / CONDITIONAL / BLOCKED
- Hard blockers: CRITICAL unmitigated red team scenario, assessment <40, ATO <40, lint <40
- Soft warnings: HIGH unmitigated red team, any score <70, open CRITICAL risk items
- Downloadable `gate-check.yaml` for GitLab CI / GitHub Actions integration
- Gate YAML includes: verdict, timestamp, blockers, warnings, all check scores
- 🚦 Gate button on every design card + canvas AADC menu

### 3. Findings Inbox (`/agentic-ai/findings`)
- Aggregates from 6 sources: NIST/OWASP assessment findings, lint issues, red team unmitigated scenarios, ATO failed items, regulatory gaps, open CRITICAL/HIGH risk items
- Unified severity (CRITICAL/HIGH/MEDIUM/LOW), sortable by severity then source
- Filter by severity, source, or design
- Severity badge summary in header
- 📬 Findings link in index page header + canvas AADC menu

---

## New Files

| File | Purpose |
|------|---------|
| `tools/agentic_ai_canvas/scorecard.py` | 8-dimension weighted scorecard engine |
| `tools/agentic_ai_canvas/deploy_gate.py` | Deploy gate verdict + YAML generator |
| `tools/agentic_ai_canvas/findings_inbox.py` | Cross-analysis findings aggregator |
| `tools/dashboard/templates/agentic_ai_canvas/scorecard.html` | Scorecard page |
| `tools/dashboard/templates/agentic_ai_canvas/deploy_gate.html` | Deploy gate page |
| `tools/dashboard/templates/agentic_ai_canvas/findings.html` | Findings inbox page |
| `tools/db/migrations/111_aadc_phase9.sql` | DDL for aadc_scorecard_snapshots + aadc_deploy_gates |

---

## New DB Tables

| Table | Purpose |
|-------|---------|
| `aadc_scorecard_snapshots` | Scorecard snapshot per design (migration 111) |
| `aadc_deploy_gates` | Deploy gate verdict snapshot per design (migration 111) |

---

## New API Routes

| Method + Route | Purpose |
|----------------|---------|
| `GET /agentic-ai/scorecard/<id>` | Unified scorecard page |
| `GET /agentic-ai/api/designs/<id>/scorecard` | Scorecard JSON |
| `GET /agentic-ai/deploy-gate/<id>` | Deployment gate page |
| `GET /agentic-ai/api/designs/<id>/deploy-gate` | Gate verdict JSON |
| `GET /agentic-ai/api/designs/<id>/deploy-gate/download` | Gate check YAML download |
| `GET /agentic-ai/findings` | Findings inbox page (filterable) |
| `GET /agentic-ai/api/findings` | Findings feed JSON |

---

## Scorecard Weights

| Dimension | Weight | Source |
|-----------|--------|--------|
| NIST AI RMF / OWASP Assessment | 25% | `aadc_assessments` |
| ATO Readiness | 20% | `ato_readiness.py` |
| Regulatory Compliance | 15% | `regulatory_tracker.py` |
| Adversarial Resilience | 15% | `red_team.py` |
| Design Lint Quality | 10% | `auto_recommend.py` |
| Structural Resilience | 10% | `impact_analyzer.py` |
| Risk Register Posture | 5% | `aadc_risk_items` |

---

## Deploy Gate Hard Blockers

| Blocker | Threshold |
|---------|-----------|
| Assessment score | < 40% |
| ATO readiness score | < 40% |
| Lint score | < 40% |
| CRITICAL unmitigated red team scenario | Any |

---

*CUI // SP-CTI — ICDEV™ AADC Phase 9*
