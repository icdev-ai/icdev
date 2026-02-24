# Phase 49 — AI Accountability

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 49 |
| Title | AI Accountability |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 48 (AI Transparency), Phase 46 (Observability, Traceability & XAI), Phase 37 (MITRE ATLAS Integration) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Phase 48 delivers transparency artifacts -- model cards, system cards, AI inventories, and framework assessors -- but transparency alone is insufficient. Federal AI mandates require accountability: designated responsible officials, human oversight plans, appeal mechanisms for AI-affected decisions, ethics review processes, incident response procedures, and scheduled reassessments.

OMB M-25-21 mandates that agencies designate a Chief AI Officer (CAIO) responsible for AI governance. OMB M-26-04 requires ongoing monitoring with scheduled reassessments and documented incident response. GAO-21-519SP demands accountability structures including appeal mechanisms for AI decisions that affect individual rights. NIST AI RMF Govern 1.3 requires defined roles and responsibilities, while Govern 4.1 mandates organizational processes for AI incident response.

Without accountability tooling, ICDEV cannot enforce oversight plans for its agentic workflows, track CAIO designations per project, provide appeal channels for AI-affected decisions, conduct structured ethics reviews, log and respond to AI incidents, or schedule periodic reassessments. The four Phase 48 assessors also require hardening -- their initial checks used placeholder evidence; Phase 49 replaces these with real database queries across 14 checks spanning all four assessors.

Phase 49 delivers six accountability capabilities: oversight plan management with role assignments, CAIO designation registry, appeal tracking for rights-impacting AI decisions, structured ethics reviews, AI incident response logging and management, and reassessment scheduling with automated due-date tracking. It also hardens all four Phase 48 assessors to perform real DB-backed checks, and exposes everything through dashboard, portal, REST API, MCP tools, and a dedicated security gate.

---

## 2. Goals

1. Manage AI oversight plans with role assignments, escalation paths, and human-in-the-loop requirements per project and use case
2. Maintain a CAIO designation registry tracking responsible officials, their authority scope, and delegation chains
3. Track appeals for AI-affected decisions with status lifecycle (submitted, under_review, resolved, escalated) and resolution documentation
4. Conduct structured ethics reviews with multi-reviewer workflows, scoring rubrics, and version-tracked review artifacts
5. Log and manage AI incidents with severity classification, root cause analysis, remediation tracking, and lessons learned
6. Schedule and enforce periodic reassessments with automated due-date tracking and overdue alerting
7. Harden 14 assessor checks across OMB M-25-21, OMB M-26-04, NIST AI 600-1, and GAO-21-519SP with real DB queries replacing placeholder evidence
8. Assess cross-framework accountability posture through a unified audit that identifies gaps across all mandates
9. Expose accountability data through dashboard (/ai-accountability), portal (/portal/ai-accountability), REST API (/api/v1/ai-accountability/*), and MCP tools

---

## 3. Architecture

```
                     AI Accountability Stack
            ┌────────────────────────────────────┐
            │    Cross-Framework Accountability   │
            │         Audit (unified)             │
            │  (ai_accountability_audit.py)       │
            └────────────────┬───────────────────┘
                             │
       ┌──────────┬──────────┼──────────┬──────────┐
       ↓          ↓          ↓          ↓          ↓
  OMB M-25-21  OMB M-26-04  NIST AI  GAO-21-519SP │
  (hardened    (hardened     600-1    (hardened     │
   DB checks)  DB checks)  (hardened  DB checks)   │
       │          │        DB checks)     │        │
       └──────────┴──────────┼──────────┴──────────┘
                             │
       ┌──────────┬──────────┼──────────┬──────────┐
       ↓          ↓          ↓          ↓          ↓
  Oversight   CAIO        Appeal     Ethics     Incident
  Plans       Registry    Tracking   Reviews    Response
  (ai_over-   (ai_caio_  (ai_ac-    (ai_eth-   (ai_inci-
  sight_       registry)  countab-   ics_       dent_log)
  plans)                  ility_     reviews)
                          appeals)
       │          │          │          │          │
       └──────────┴──────────┼──────────┴──────────┘
                             │
                    Reassessment Scheduler
                  (ai_reassessment_schedule)
                    automated due-date
                    tracking + alerting
                             │
       ┌─────────────────────┼─────────────────────┐
       ↓                     ↓                      ↓
  /ai-accountability    REST API              MCP Tools
  (dashboard + portal)  /api/v1/ai-           (unified gateway)
                        accountability/*
```

Oversight plans define human-in-the-loop checkpoints within agentic workflows. Each plan specifies which decisions require human approval, the escalation path when approval is denied, and the roles authorized to approve. The CAIO registry tracks designated officials per project with authority scope (project-level, program-level, or agency-level) and delegation chains. Appeals follow a four-state lifecycle and are linked to specific AI decisions via trace IDs from Phase 46 provenance tracking.

The reassessment scheduler maintains a calendar of required periodic reviews. When a reassessment comes due, it triggers notifications through the heartbeat daemon (Phase 29) and blocks the accountability security gate until the reassessment is completed.

---

## 4. Database Schema

| Table | Purpose |
|-------|---------|
| `ai_oversight_plans` | Oversight plan storage — plan_id, project_id, use_case_id, plan_name, roles (JSON), escalation_path (JSON), hitl_checkpoints (JSON), approval_required, status (active/draft/archived), created_at, created_by, version |
| `ai_caio_registry` | CAIO designation tracking — designation_id, project_id, official_name, official_email, authority_scope (project/program/agency), delegation_chain (JSON), effective_date, expiry_date, status (active/expired/revoked) |
| `ai_accountability_appeals` | Appeal tracking — appeal_id, project_id, decision_trace_id, appellant, reason, status (submitted/under_review/resolved/escalated), reviewer, resolution, resolution_date, created_at |
| `ai_ethics_reviews` | Ethics review records — review_id, project_id, use_case_id, reviewer_id, review_type (initial/periodic/incident_triggered), score (JSON rubric), findings, recommendations, status (pending/in_progress/completed/requires_changes), version, created_at |
| `ai_incident_log` | AI incident tracking — incident_id, project_id, severity (critical/high/medium/low), title, description, root_cause, affected_systems (JSON), remediation_status (open/investigating/mitigated/resolved/closed), lessons_learned, reported_by, reported_at, resolved_at |
| `ai_reassessment_schedule` | Reassessment scheduling — schedule_id, project_id, assessment_type, frequency_days, last_completed, next_due, status (on_track/due/overdue), assigned_to, created_at |

---

## 5. Tools

| Tool | Purpose |
|------|---------|
| `tools/compliance/accountability_manager.py` | Oversight plan CRUD — create, update, list, archive plans; CAIO designation management; role assignment with escalation paths |
| `tools/compliance/ai_incident_response.py` | AI incident lifecycle management — log, investigate, remediate, resolve, close; severity classification; lessons learned extraction |
| `tools/compliance/ai_reassessment_scheduler.py` | Reassessment scheduling — create schedules, check due dates, trigger notifications, mark completions, overdue alerting |
| `tools/compliance/ai_impact_assessor.py` | AI impact assessment — evaluate rights-impact and safety-impact dimensions for use cases; generate impact classification for OMB reporting |
| `tools/compliance/ai_accountability_audit.py` | Cross-framework accountability audit — runs hardened assessor checks, gap analysis across all mandates, unified report generation |
| `tools/compliance/omb_m25_21_assessor.py` | (Hardened) OMB M-25-21 assessor — real DB checks for CAIO designation, inventory completeness, oversight plans, risk classification |
| `tools/compliance/omb_m26_04_assessor.py` | (Hardened) OMB M-26-04 assessor — real DB checks for public reporting readiness, monitoring active, reassessment schedule, incident response |
| `tools/compliance/nist_ai_600_assessor.py` | (Hardened) NIST AI 600-1 assessor — real DB checks for confabulation detection active, fairness assessment current, provenance tracking, content tracing policy |
| `tools/compliance/gao_ai_assessor.py` | (Hardened) GAO-21-519SP assessor — real DB checks for governance structures, performance monitoring, transparency reporting, accountability mechanisms |
| `tools/dashboard/api/ai_accountability.py` | Flask API blueprint for /ai-accountability dashboard and REST endpoints |
| `tools/dashboard/templates/ai_accountability.html` | Dashboard template — oversight plans, CAIO registry, appeal tracker, incident timeline, reassessment calendar |

---

## 6. Assessor Hardening Detail

Phase 49 replaces placeholder evidence in 14 assessor checks with real database queries:

| Assessor | Check ID | Before (Phase 48) | After (Phase 49) |
|----------|----------|-------------------|-------------------|
| OMB M-25-21 | M25-001 | Placeholder evidence | Query `ai_use_case_inventory` for completeness |
| OMB M-25-21 | M25-002 | Placeholder evidence | Query `ai_caio_registry` for active designation |
| OMB M-25-21 | M25-003 | Placeholder evidence | Query `ai_oversight_plans` for rights-impacting use cases |
| OMB M-25-21 | M25-004 | Placeholder evidence | Query `ai_use_case_inventory` for risk_classification coverage |
| OMB M-26-04 | M26-001 | Placeholder evidence | Query `ai_model_cards` for public reporting completeness |
| OMB M-26-04 | M26-002 | Placeholder evidence | Query `ai_reassessment_schedule` for active schedules |
| OMB M-26-04 | M26-003 | Placeholder evidence | Query `ai_incident_log` for response procedures documented |
| NIST AI 600-1 | AI600-001 | Placeholder evidence | Query `ai_confabulation_log` for detection activity |
| NIST AI 600-1 | AI600-002 | Placeholder evidence | Query `ai_fairness_assessments` for current results |
| NIST AI 600-1 | AI600-003 | Placeholder evidence | Query `prov_entities`/`prov_relations` for provenance chain |
| NIST AI 600-1 | AI600-004 | Placeholder evidence | Check `ICDEV_CONTENT_TRACING_ENABLED` env + xai_assessments |
| GAO | GAO-001 | Placeholder evidence | Query `ai_caio_registry` + `ai_oversight_plans` for governance |
| GAO | GAO-002 | Placeholder evidence | Query `ai_telemetry` + `ai_fairness_assessments` for monitoring |
| GAO | GAO-003 | Placeholder evidence | Query `ai_accountability_appeals` for appeal mechanisms |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D316 | Oversight plans versioned via insert-new-row pattern (no UPDATE) | Consistent with D6 append-only audit; preserves oversight plan history for compliance audits and ATO evidence |
| D317 | CAIO registry supports delegation chains as JSON array | Federal agencies delegate CAIO authority to program/project levels; JSON captures chain without additional join tables |
| D318 | Appeals linked to decisions via trace_id from Phase 46 provenance | Provides full traceability from appeal to the specific AI decision, tool calls, and agent that produced it |
| D319 | Ethics reviews use multi-reviewer scoring rubric stored as JSON | Different organizations use different rubrics; JSON schema allows customization without code changes (D26 pattern) |
| D320 | Incident response reuses self-healing severity model (critical/high/medium/low) | Consistent with existing self-healing thresholds (Phase 8); operators already understand the severity taxonomy |
| D321 | Reassessment scheduler integrates with heartbeat daemon (Phase 29) for notifications | Reuses existing notification infrastructure (audit trail + SSE + gateway channels per D163) instead of building separate alerting |

---

## 8. Security Gate

**AI Accountability Gate:**
- **Blocking:** No active CAIO designation for federal agency projects, oversight plans missing for rights-impacting AI use cases, unresolved critical AI incidents older than SLA (72 hours for critical), appeals in submitted state without reviewer assignment for more than 5 business days
- **Warning:** Reassessments overdue by more than 30 days, ethics reviews not completed for new use cases, incident response plan not documented, fewer than 80% of assessor checks passing
- **Thresholds:** caio_required=true (federal projects), oversight_plan_required=true (rights-impacting), critical_incident_sla_hours=72, appeal_assignment_sla_days=5, reassessment_overdue_warning_days=30, min_assessor_coverage_pct=80

---

## 9. Commands

```bash
# Oversight plans
python tools/compliance/accountability_manager.py --project-id "proj-123" --create-plan --json
python tools/compliance/accountability_manager.py --project-id "proj-123" --list-plans --json
python tools/compliance/accountability_manager.py --project-id "proj-123" --plan-id "<id>" --json

# CAIO designation
python tools/compliance/accountability_manager.py --project-id "proj-123" --designate-caio \
  --official-name "Jane Smith" --official-email "jane@agency.gov" --scope agency --json
python tools/compliance/accountability_manager.py --project-id "proj-123" --caio-status --json

# Appeal tracking
python tools/compliance/accountability_manager.py --project-id "proj-123" --submit-appeal \
  --decision-trace-id "<trace-id>" --reason "Incorrect classification" --json
python tools/compliance/accountability_manager.py --project-id "proj-123" --list-appeals --json
python tools/compliance/accountability_manager.py --appeal-id "<id>" --resolve \
  --resolution "Decision reversed" --json

# Ethics reviews
python tools/compliance/accountability_manager.py --project-id "proj-123" --create-review \
  --use-case-id "<id>" --reviewer-id "reviewer@mil" --json
python tools/compliance/accountability_manager.py --project-id "proj-123" --list-reviews --json

# AI incident response
python tools/compliance/ai_incident_response.py --project-id "proj-123" --log \
  --severity critical --title "Model hallucination in compliance report" --json
python tools/compliance/ai_incident_response.py --project-id "proj-123" --list --json
python tools/compliance/ai_incident_response.py --incident-id "<id>" --investigate --json
python tools/compliance/ai_incident_response.py --incident-id "<id>" --resolve \
  --root-cause "Training data gap" --lessons-learned "Add validation step" --json

# Reassessment scheduling
python tools/compliance/ai_reassessment_scheduler.py --project-id "proj-123" --create \
  --assessment-type transparency_audit --frequency-days 90 --json
python tools/compliance/ai_reassessment_scheduler.py --project-id "proj-123" --check-due --json
python tools/compliance/ai_reassessment_scheduler.py --project-id "proj-123" --mark-complete \
  --schedule-id "<id>" --json
python tools/compliance/ai_reassessment_scheduler.py --project-id "proj-123" --overdue --json

# Impact assessment
python tools/compliance/ai_impact_assessor.py --project-id "proj-123" --use-case-id "<id>" --json
python tools/compliance/ai_impact_assessor.py --project-id "proj-123" --all --json

# Cross-framework accountability audit
python tools/compliance/ai_accountability_audit.py --project-id "proj-123" --json
python tools/compliance/ai_accountability_audit.py --project-id "proj-123" --human --stream
python tools/compliance/ai_accountability_audit.py --project-id "proj-123" --gate

# Dashboard page: /ai-accountability
# Portal page: /portal/ai-accountability
# REST API: GET/POST /api/v1/ai-accountability/oversight-plans
#            GET/POST /api/v1/ai-accountability/caio
#            GET/POST /api/v1/ai-accountability/appeals
#            GET/POST /api/v1/ai-accountability/ethics-reviews
#            GET/POST /api/v1/ai-accountability/incidents
#            GET/POST /api/v1/ai-accountability/reassessments
#            POST /api/v1/ai-accountability/audit
# MCP tools: accountability_manage, ai_incident_log, ai_reassessment_schedule,
#            ai_impact_assess, ai_accountability_audit
# Slash command: /icdev-accountability

# Configuration
# args/ai_accountability_config.yaml — oversight plan templates, CAIO scope rules,
#   appeal SLAs, ethics rubrics, incident severity mapping, reassessment frequencies
# args/security_gates.yaml — ai_accountability gate conditions
```

---

## 10. Related

- [Phase 48: AI Transparency](phase-48-ai-transparency.md) -- Model cards, AI inventory, framework assessors (hardened by this phase)
- [Phase 46: Observability, Traceability & XAI](phase-46-observability-traceability-xai.md) -- Trace IDs for appeal linkage, provenance for assessor checks
- [Phase 29: Proactive Monitoring](phase-29-proactive-monitoring.md) -- Heartbeat daemon for reassessment notifications
- [Phase 8: Self-Healing](phase-08-self-healing.md) -- Severity model reused for incident classification
- [Phase 50: AI Governance Integration](phase-50-ai-governance-intake-chat.md) -- Intake and chat integration for governance pillars
