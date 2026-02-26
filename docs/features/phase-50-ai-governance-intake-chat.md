# Phase 50 — AI Governance Integration (Intake & Chat)

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 50 |
| Title | AI Governance Integration (Intake & Chat) |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 48 (AI Transparency), Phase 49 (AI Accountability), Phase 44 (Innovation Adaptation — chat and extension hooks) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Phase 48 and Phase 49 deliver comprehensive AI transparency and accountability tooling -- model cards, AI inventories, framework assessors, oversight plans, incident response, and reassessment scheduling. However, these capabilities exist as standalone tools. They are not woven into the workflows operators actually use: requirements intake sessions and agent chat streams. An operator running a RICOAS intake session for a federal agency project will not discover that OMB M-25-21 applies until they manually run the transparency assessor after intake completes. An operator chatting with the builder agent will not learn that their project lacks a CAIO designation until they separately navigate to the accountability dashboard.

This gap means governance requirements are discovered late, after design decisions have already been made. Late discovery increases rework, delays ATO submissions, and risks non-compliance for projects that should have had governance structures from the start.

Phase 50 closes this gap by integrating AI governance into the two primary operator workflows: requirements intake and agent chat. During intake, keyword detection identifies which of the six governance pillars (transparency, accountability, fairness, safety, explainability, privacy) are relevant and auto-triggers governance probes. Readiness scoring gains a seventh dimension (ai_governance_readiness) alongside the existing five dimensions plus a newly implemented devsecops_readiness dimension. Extension hooks activate built-in handlers to inject governance context. Chat streams display governance advisories and a sidebar showing governance posture. All configuration lives in a single YAML file, no new database tables are required, and a dedicated security gate blocks projects with unaddressed governance gaps.

---

## 2. Goals

1. Detect AI governance-relevant keywords during RICOAS intake sessions and identify applicable governance pillars
2. Auto-trigger OMB M-25-21 governance requirements for federal agency projects based on intake metadata
3. Generate governance probe questions for missing pillars to capture requirements early in the intake flow
4. Add ai_governance_readiness as a seventh dimension to readiness scoring, and implement the previously stubbed devsecops_readiness dimension
5. Activate built-in extension hook handlers for governance context injection at the pre-LLM and post-tool hook points
6. Display governance advisory messages in the unified agent chat when AI governance gaps are detected
7. Render a governance sidebar in the unified chat showing real-time governance posture per project
8. Consolidate all governance integration configuration into a single `args/ai_governance_config.yaml` file
9. Reuse existing database tables from Phase 48 and Phase 49 without creating new tables

---

## 3. Architecture

```
                    AI Governance Integration
          ┌──────────────────────────────────────┐
          │        ai_governance_config.yaml      │
          │   (single config for all integration) │
          │                                       │
          │  pillars:                              │
          │    transparency, accountability,       │
          │    fairness, safety, explainability,   │
          │    privacy                             │
          │  keywords: per-pillar detection terms  │
          │  probes: per-pillar question templates │
          │  triggers: agency_type → auto-enable   │
          └──────────────┬───────────────────────┘
                         │
          ┌──────────────┼───────────────────┐
          ↓              ↓                    ↓
   Intake Integration  Chat Integration   Extension Hooks
          │              │                    │
   keyword detection   governance           built-in handlers
   pillar mapping      advisory messages    pre-LLM injection
   probe questions     sidebar posture      post-tool context
   auto-trigger        gap highlighting
   (federal agency)
          │              │                    │
          ↓              ↓                    ↓
   Readiness Scorer   Chat Manager         Extension Manager
   (7 dimensions)     (Phase 44)           (Phase 44)
   +ai_governance     advisory as           handler activation
   +devsecops         system message        via hook points
          │              │                    │
          └──────────────┼───────────────────┘
                         │
                    Existing DB Tables
              (Phase 48 + Phase 49 — no new tables)
          ai_use_case_inventory, ai_model_cards,
          ai_oversight_plans, ai_caio_registry,
          ai_reassessment_schedule, ai_fairness_assessments,
          ai_transparency_assessments, intake_sessions
```

The integration layer reads `ai_governance_config.yaml` at startup and registers keyword patterns for each of the six governance pillars. During intake, every customer message is scanned against these patterns. When a pillar is detected, the system records it and generates follow-up probe questions for any pillars that remain unaddressed. Federal agency projects (detected via customer organization metadata or explicit `--agency` flag) automatically trigger OMB M-25-21 requirements without keyword matching.

Chat integration works through the Phase 44 extension hook system. A built-in governance handler activates at the pre-LLM hook point, querying existing governance tables to determine the project's governance posture. If gaps are found, advisory messages are injected as system-role messages in the chat context. The chat sidebar renders governance posture using the same data, with color-coded status per pillar.

---

## 4. Readiness Scoring (7 Dimensions)

Phase 50 extends the readiness scorer with two new dimensions:

| Dimension | Weight | Source | Status |
|-----------|--------|--------|--------|
| completeness | 0.25 | Intake requirements coverage | Existing |
| clarity | 0.20 | Ambiguity and gap detection | Existing |
| feasibility | 0.20 | Technical feasibility signals | Existing |
| compliance | 0.15 | NIST control coverage | Existing |
| testability | 0.10 | BDD criteria coverage | Existing |
| devsecops_readiness | 0.05 | DevSecOps profile maturity, pipeline security stages | New (D323) |
| ai_governance_readiness | 0.05 | Governance pillar coverage, oversight plans, CAIO designation | New (D323) |

The ai_governance_readiness score is computed as a weighted average of: pillar coverage (how many of the 6 applicable pillars are addressed), oversight plan presence (for rights-impacting use cases), CAIO designation (for federal projects), and model card completeness (for projects using AI models). The devsecops_readiness score queries the `devsecops_profiles` table for maturity level and the `devsecops_pipeline_audit` table for pipeline security stage coverage.

---

## 5. Extension Hook Handlers

Phase 50 activates built-in governance handlers through the Phase 44 extension hook system:

| Hook Point | Handler | Behavior |
|------------|---------|----------|
| `pre_llm_call` | `governance_context_injector` | Queries governance tables; injects system message with governance posture summary and any blocking gaps |
| `post_tool_call` | `governance_posture_updater` | After compliance/transparency tools run, refreshes cached governance posture for the chat sidebar |

Handlers are registered as built-in extensions (not external plugin files) and are activated when `ai_governance_config.yaml` sets `integration.enabled: true`. They follow the Phase 44 behavioral tier (they modify chat context), have a maximum execution time of 5 seconds per the Phase 44 safety limits, and are isolated by the existing exception handling so that handler failures do not interrupt the chat flow.

---

## 6. Chat Governance Advisory

When an agent chat stream is active for a project with AI components, the governance advisory system:

1. Queries existing tables (`ai_use_case_inventory`, `ai_oversight_plans`, `ai_caio_registry`, `ai_transparency_assessments`) at chat context initialization
2. Computes a per-pillar status: **covered** (requirements captured + assessor passing), **partial** (requirements captured, assessor not yet run or failing), **missing** (no requirements captured)
3. Injects an advisory system message when any pillar is missing or partial, formatted as: "AI Governance Advisory: [pillar] requirements not yet addressed. Consider running /icdev-transparency or /icdev-accountability."
4. Renders a sidebar panel (visible in `/chat` dashboard page) showing all six pillars with color-coded status indicators

Advisory messages are non-blocking -- they inform the operator but do not prevent chat continuation. The security gate (section 7) is the enforcement mechanism.

---

## 7. Security Gate

**AI Governance Gate:**
- **Blocking:** Federal agency project without OMB M-25-21 assessment initiated, rights-impacting use cases without oversight plan, ai_governance_readiness score below 0.3 for projects with AI components, all six governance pillars unaddressed for projects flagged as AI-dependent
- **Warning:** ai_governance_readiness below 0.5, fewer than 4 of 6 pillars addressed, devsecops_readiness below 0.3, governance advisory dismissed without action more than 3 times
- **Thresholds:** min_ai_governance_readiness=0.30, min_pillar_coverage=1 (at least 1 pillar addressed to unblock), federal_omb_assessment_required=true, oversight_plan_required=true (rights-impacting)

---

## 8. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D322 | Six governance pillars (transparency, accountability, fairness, safety, explainability, privacy) defined in ai_governance_config.yaml with per-pillar keywords | Declarative keyword patterns enable intake detection without LLM; add/remove pillars without code changes (D26 pattern) |
| D323 | Readiness scoring extended to 7 dimensions; devsecops_readiness now implemented (was stubbed) | Both dimensions share the weighted-average pattern from D21; weights kept low (0.05 each) to avoid disrupting existing readiness thresholds |
| D324 | Extension hooks use built-in handlers (not external plugin files) for governance | Governance is a core ICDEV capability, not a tenant extension; built-in handlers avoid file I/O overhead and cannot be accidentally deleted |
| D325 | Handlers activated via config flag (integration.enabled) not code change | Consistent with D44 flag-based backward compatibility; omitting the flag produces identical behavior to pre-Phase 50 |
| D326 | Governance sidebar in chat renders from cached posture (refreshed by post_tool_call hook) | Avoids per-message DB queries; posture changes infrequently (only after compliance tool runs), so cache invalidation on tool completion is sufficient |
| D327 | Advisory messages injected as system-role messages (not user-role) | System messages are non-interruptive in the chat flow; they inform the LLM context without appearing as user commands |
| D328 | Single config file (ai_governance_config.yaml) for all integration settings | Operators configure pillar keywords, probe templates, auto-triggers, readiness weights, and hook activation in one place; consistent with single-file config pattern used by other phases |
| D329 | No new database tables -- reuses Phase 48 and Phase 49 tables | All governance data already exists in ai_use_case_inventory, ai_oversight_plans, ai_caio_registry, ai_transparency_assessments, ai_fairness_assessments, ai_reassessment_schedule; integration layer is read-only against these tables |
| D330 | Security gate (ai_governance) is separate from ai_transparency and ai_accountability gates | Integration-level gate checks cross-cutting posture (pillar coverage, readiness score) that cannot be captured by individual framework gates |

---

## 9. Commands

```bash
# Governance integration is automatic during intake and chat when enabled.
# Manual commands for testing and inspection:

# Check governance pillar detection for text
python tools/requirements/intake_engine.py --session-id "<id>" --message "We need fairness testing for our AI classifier" --json
# (governance pillars detected in response metadata)

# Check readiness with governance dimension
python tools/requirements/readiness_scorer.py --session-id "<id>" --json
# (response includes ai_governance_readiness and devsecops_readiness dimensions)

# Run governance gate check
python tools/compliance/ai_transparency_audit.py --project-id "proj-123" --gate
python tools/compliance/ai_accountability_audit.py --project-id "proj-123" --gate
# (ai_governance gate is evaluated as part of multi-regime assessment)

# Chat with governance advisory (start dashboard, navigate to /chat)
python tools/dashboard/app.py
# Governance sidebar and advisory messages appear automatically for AI projects

# Configuration
# args/ai_governance_config.yaml — pillar definitions, keywords, probe templates,
#   auto-triggers, readiness weights, hook activation, sidebar settings
# args/security_gates.yaml — ai_governance gate conditions
```

---

## 10. Related

- [Phase 48: AI Transparency](phase-48-ai-transparency.md) -- Model cards, AI inventory, framework assessors queried by governance integration
- [Phase 49: AI Accountability](phase-49-ai-accountability.md) -- Oversight plans, CAIO registry, incident response queried by governance integration
- [Phase 44: Innovation Adaptation](phase-44-innovation-adaptation.md) -- Chat manager and extension hook system used for governance injection
- [Phase 29: Proactive Monitoring](phase-29-proactive-monitoring.md) -- Heartbeat daemon for reassessment due-date notifications
