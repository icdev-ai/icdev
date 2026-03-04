# CUI // SP-CTI
# Goal: AI Accountability (Phase 49)

## Overview
Implement the accountability half of AI Transparency & Accountability.
Phase 48 delivered transparency (model cards, system cards, AI inventory, confabulation detection, fairness assessment, 4 framework assessors). Phase 49 delivers accountability (human oversight, appeals, CAIO designation, incident response, ethics reviews, reassessment scheduling).

## When to Use
- After Phase 48 AI Transparency is complete
- When a project uses AI components that require accountability evidence
- When `data_classifications` includes AI data category
- When assessors report accountability gaps (M25-OVR-*, M26-REV-*, GAO-MON-*, FAIR-6/7)

## Tools

| Tool | Purpose |
|------|---------|
| `tools/compliance/accountability_manager.py` | Central coordinator: oversight plans, CAIO, appeals, ethics reviews, reassessments |
| `tools/compliance/ai_impact_assessor.py` | Algorithmic impact assessment (M26-IMP-1) |
| `tools/compliance/ai_incident_response.py` | AI-specific incident tracking (M25-RISK-4, GAO-MON-3) |
| `tools/compliance/ai_reassessment_scheduler.py` | Periodic reassessment tracking (M25-INV-3, GAO-MON-4) |
| `tools/compliance/ai_accountability_audit.py` | Cross-framework accountability audit (all 4 frameworks) |

## Workflow

1. **Register Oversight Plan** — Create human oversight plan for AI system
2. **Designate CAIO** — Assign Chief AI Officer / responsible official
3. **Submit Ethics Review** — Document ethics framework, legal compliance, opt-out policy
4. **Run Impact Assessment** — Evaluate algorithmic impact on affected populations
5. **Schedule Reassessments** — Set periodic review cadence
6. **Run Accountability Audit** — Cross-framework gap analysis
7. **Address Gaps** — File appeals, log incidents, remediate as needed

## Database Tables (6 new)

| Table | Purpose | Mutability |
|-------|---------|------------|
| `ai_oversight_plans` | Human oversight plans | Append-only (D6) |
| `ai_accountability_appeals` | Appeal tracking | Append-only (D6) |
| `ai_caio_registry` | CAIO/responsible official | UPDATE allowed |
| `ai_incident_log` | AI-specific incidents | Append-only (D6) |
| `ai_reassessment_schedule` | Reassessment cadence | UPDATE allowed |
| `ai_ethics_reviews` | Ethics reviews with boolean flags | Append-only (D6) |

## Assessor Fixes (14 checks across 4 assessors)

### OMB M-25-21 (6 fixes)
- M25-OVR-1: Human oversight plan → `ai_oversight_plans`
- M25-OVR-3: Appeal process → `ai_accountability_appeals`
- M25-OVR-4: CAIO designation → `ai_caio_registry`
- M25-INV-2: Responsible official → `ai_use_case_inventory.responsible_official IS NOT NULL`
- M25-INV-3: Reassessment schedule → `ai_reassessment_schedule`
- M25-RISK-4: Incident response → `ai_incident_log`

### OMB M-26-04 (3 fixes)
- M26-REV-2: Appeal process → `ai_accountability_appeals`
- M26-REV-3: Opt-out policy → `ai_ethics_reviews.opt_out_policy = 1`
- M26-IMP-1: Impact assessment → `ai_ethics_reviews.review_type = 'impact_assessment'`

### GAO-21-519SP (5 fixes)
- GAO-MON-2: Feedback collection → `audit_trail.event_type LIKE '%feedback%'`
- GAO-MON-3: Incident detection → `ai_incident_log`
- GAO-MON-4: Reassessment schedule → `ai_reassessment_schedule`
- GAO-GOV-2: Legal compliance → `ai_ethics_reviews.legal_compliance_matrix = 1`
- GAO-GOV-3: Ethics framework → `ai_ethics_reviews` (any record)

### Fairness (4 fixes + gate)
- FAIR-1: Bias testing policy → `ai_ethics_reviews.review_type = 'bias_testing_policy'`
- FAIR-3: Disparity analysis → `ai_ethics_reviews.pre_deployment_review = 1`
- FAIR-6: Human review → `ai_oversight_plans`
- FAIR-7: Appeal process → `ai_accountability_appeals`
- Gate threshold: 50% → 25% (D321)

## Security Gate: `ai_accountability`
- **Blocking:** CAIO not designated for high-impact, oversight plan missing, critical incident unresolved, reassessment overdue >90 days
- **Warning:** Appeal process not defined, ethics review not conducted, impact assessment missing, fairness gate not passing

## Architecture Decisions
- D316: Accountability tables append-only except CAIO/reassessment (officials change, schedules shift)
- D317: Single coordinator tool (accountability_manager.py) consolidates 7 functions
- D318: AI incident log separate from audit_trail (AI-specific corrective action events)
- D319: Boolean flags (opt_out_policy, legal_compliance_matrix, pre_deployment_review) for fast assessor checks
- D320: Impact assessment stored in ai_ethics_reviews with review_type='impact_assessment'
- D321: Fairness gate lowered to 25% — achievable with DB-only checks (no project_dir required)

## Edge Cases
- Project with no AI components: accountability tools return empty results gracefully
- Multiple CAIO designations: latest entry takes precedence (query ORDER BY created_at DESC LIMIT 1)
- Overdue reassessments: `check_overdue()` returns items where `next_due < now`
- Appeal resolution: requires `resolved_by` field — accountability chain is tracked
