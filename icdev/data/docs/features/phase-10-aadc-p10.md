# CUI // SP-CTI
# Phase 10 — AADC Design Review, Lifecycle & Monitoring

**Canvas:** Agentic AI Design Canvas (AADC)  
**Epic key:** aadc-p10  
**Shipped:** 2026-05-03  
**Classification:** CUI // SP-CTI

---

## Summary

Phase 10 is the capstone phase of the AADC build. It adds the organizational layer that governs how AI designs move from conception to production: a 6-state lifecycle state machine with audited transitions, a multi-reviewer approval workflow with structured comment types, and a portfolio monitoring dashboard that tracks score drift and surfaces degradation alerts before they become incidents.

---

## Features Shipped

### 1. Design Lifecycle Manager (`/agentic-ai/lifecycle/<id>`)
- 6 states: DRAFT → UNDER_REVIEW → APPROVED → DEPLOYED → DEPRECATED (+ CHANGES_REQUESTED branch)
- State machine diagram with current state highlighted
- All valid transitions surfaced as action buttons; APPROVED and DEPLOYED flagged as requiring deploy gate check
- Transition form: actor (name/email) + optional reason
- Full audited transition history: from_state, to_state, actor, reason, timestamp
- 🔄 Lifecycle button on every design card + canvas AADC menu

### 2. Design Review Workflow (`/agentic-ai/review/<id>`)
- Four comment types: COMMENT / APPROVAL / CHANGE_REQUEST / REJECTION
- Derived review status: PENDING → APPROVED / CHANGES_REQUESTED / REJECTED
- Reviewer summary: last decision per reviewer shown as badge strip
- Optional node-level annotations (attach comment to specific canvas node)
- Chronological review thread with color-coded comment types
- 👁 Review button on every design card + canvas AADC menu

### 3. Portfolio Monitoring Dashboard (`/agentic-ai/monitoring`)
- Per-design score drift tracking: current vs. baseline (first assessment)
- Alert levels: CRITICAL (≥20pts drop) / HIGH (≥10pts) / MEDIUM (≥5pts) / OK
- Sparkline history bars (last 10 assessments) per design
- Summary header: CRITICAL/HIGH/MEDIUM/OK counts at a glance
- Quick-action links to run new assessment or view scorecard
- 🔔 Monitoring link in index page header + canvas AADC menu

---

## New Files

| File | Purpose |
|------|---------|
| `tools/agentic_ai_canvas/lifecycle_manager.py` | Lifecycle state machine engine |
| `tools/agentic_ai_canvas/review_workflow.py` | Multi-reviewer comment/decision engine |
| `tools/agentic_ai_canvas/monitoring_engine.py` | Score drift + alert computation |
| `tools/dashboard/templates/agentic_ai_canvas/lifecycle.html` | Lifecycle state page |
| `tools/dashboard/templates/agentic_ai_canvas/review.html` | Review workflow page |
| `tools/dashboard/templates/agentic_ai_canvas/monitoring.html` | Monitoring dashboard |
| `tools/db/migrations/112_aadc_phase10.sql` | DDL for aadc_lifecycle_states + aadc_review_comments |

---

## New DB Tables

| Table | Purpose |
|-------|---------|
| `aadc_lifecycle_states` | Audited lifecycle transition log per design (migration 112) |
| `aadc_review_comments` | Reviewer comments and decisions per design (migration 112) |

---

## New API Routes

| Method + Route | Purpose |
|----------------|---------|
| `GET /agentic-ai/lifecycle/<id>` | Lifecycle state page |
| `GET /agentic-ai/api/designs/<id>/lifecycle` | Lifecycle JSON |
| `POST /agentic-ai/api/designs/<id>/lifecycle/transition` | Execute state transition |
| `GET /agentic-ai/review/<id>` | Review workflow page |
| `GET /agentic-ai/api/designs/<id>/review` | Review comments JSON |
| `POST /agentic-ai/api/designs/<id>/review` | Add review comment/decision |
| `GET /agentic-ai/monitoring` | Monitoring dashboard |
| `GET /agentic-ai/api/monitoring` | Monitoring alerts JSON |

---

## Lifecycle State Machine

```
DRAFT ──────────────────────────────────► UNDER_REVIEW
                                              │
                              ┌───────────────┤
                              ▼               ▼
                   CHANGES_REQUESTED      APPROVED ──► DEPLOYED ──► DEPRECATED
                              │               │           │
                              └──► DRAFT ◄────┘           └──► DRAFT
```

---

## Monitoring Alert Thresholds

| Alert Level | Drift Threshold |
|-------------|----------------|
| CRITICAL | Score dropped ≥ 20 pts from baseline |
| HIGH | Score dropped ≥ 10 pts from baseline |
| MEDIUM | Score dropped ≥ 5 pts from baseline |
| OK | Score stable or improving |

---

## AADC Phase Summary (All 10 Phases Complete)

| Phase | Focus |
|-------|-------|
| P1 | Canvas Parity (undo/redo, multi-select, exports, diff) |
| P2 | Ecosystem Wiring (Genesis, MCP, activity feed, Kanban) |
| P3 | AADC-Unique (safety graph, coord matrix, provenance, simulation) |
| P4 | Competitive Edge (checkpoint, HITL gate, parallel exec, observability) |
| P5 | Governance (risk register, threat model, portfolio, OSCAL) |
| P6 | Lifecycle Readiness (ATO, regulatory, design compare, exec summary) |
| P7 | Adversarial Security (red team, design linter, accred ZIP) |
| P8 | Design Intelligence (pattern detector, impact analyzer, analytics) |
| P9 | Unified Scorecard + Deploy Gate + Findings Inbox |
| P10 | Review, Lifecycle State Machine, Monitoring |

---

*CUI // SP-CTI — ICDEV™ AADC Phase 10 (Capstone)*
