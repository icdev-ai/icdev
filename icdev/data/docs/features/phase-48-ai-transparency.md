# Phase 48 — AI Transparency

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 48 |
| Title | AI Transparency |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 37 (MITRE ATLAS Integration), Phase 46 (Observability, Traceability & XAI), Phase 23 (Universal Compliance Platform) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Federal agencies deploying AI systems face an expanding set of transparency mandates. OMB Memorandum M-25-21 requires agencies to maintain AI use case inventories and publish model cards for rights-impacting and safety-impacting AI. OMB M-26-04 strengthens these requirements with ongoing monitoring and public reporting obligations. NIST AI 600-1 (the Generative AI Profile) extends the AI RMF with generative-AI-specific risks including confabulation, bias amplification, and provenance gaps. GAO-21-519SP establishes an AI Accountability Framework demanding documented governance structures, performance monitoring, and transparency reporting.

ICDEV's 15-agent multi-agent system makes autonomous decisions across code generation, compliance assessment, security scanning, and deployment. Without structured model cards, system cards, AI inventories, confabulation detection, and fairness assessment, operators cannot demonstrate compliance with these mandates during ATO submissions. Phase 46 delivers traceability and explainability at the span level, but lacks the higher-level artifact inventory and risk documentation that auditors and oversight bodies require.

Phase 48 delivers seven interconnected capabilities: model cards documenting each AI model's intended use, limitations, and evaluation metrics; system cards documenting composite multi-model systems; an AI use case inventory aligned with OMB reporting requirements; confabulation detection for LLM outputs; fairness assessment for bias evaluation; four framework-specific assessors (OMB M-25-21, OMB M-26-04, NIST AI 600-1, GAO-21-519SP); and a transparency audit that consolidates findings across all assessors. These are exposed through dashboard and portal pages, REST API endpoints, MCP tools, and a security gate.

---

## 2. Goals

1. Generate and maintain model cards for every AI model used by ICDEV agents, stored in `ai_model_cards` with versioned metadata
2. Generate system cards documenting composite multi-agent workflows where multiple models interact
3. Maintain an AI use case inventory aligned with OMB M-25-21 and M-26-04 reporting categories (rights-impacting, safety-impacting, presumed-risk)
4. Detect confabulation in LLM outputs using reference-grounded verification and confidence scoring
5. Assess fairness across demographic dimensions using statistical parity and disparate impact metrics
6. Provide four framework-specific assessors with automated checks: OMB M-25-21 (federal AI governance), OMB M-26-04 (federal AI transparency), NIST AI 600-1 (generative AI risk profile), GAO-21-519SP (AI accountability framework)
7. Consolidate all transparency findings into a unified audit report with cross-framework coverage analysis
8. Expose transparency data through dashboard (/ai-transparency), portal (/portal/ai-transparency), REST API (/api/v1/ai-transparency/*), and MCP tools

---

## 3. Architecture

```
                      AI Transparency Stack
             ┌───────────────────────────────────┐
             │         Transparency Audit         │
             │  (ai_transparency_audit.py)        │
             │   Consolidates all assessor        │
             │   results + coverage analysis      │
             └────────────────┬──────────────────┘
                              │
        ┌─────────┬───────────┼───────────┬──────────┐
        ↓         ↓           ↓           ↓          ↓
   OMB M-25-21  OMB M-26-04  NIST AI   GAO-21-519SP │
   Assessor     Assessor     600-1     Assessor      │
   (BaseAssessor D116)       Assessor                │
        │         │           │           │          │
        └─────────┴───────────┼───────────┴──────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ↓                     ↓                      ↓
   Model Cards          System Cards         AI Inventory
   (ai_model_cards)     (ai_system_cards)    (ai_use_case_inventory)
   per-model metadata   composite systems    OMB-aligned registry
        │                     │                      │
        ├─────────────────────┼──────────────────────┤
        ↓                     ↓                      ↓
   Confabulation         Fairness               Crosswalk
   Detector              Assessor               (NIST 800-53
   reference-grounded    statistical parity      US hub)
   verification          disparate impact
        │                     │                      │
        └─────────────────────┼──────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ↓                     ↓                      ↓
   /ai-transparency     REST API              MCP Tools
   (dashboard + portal) /api/v1/ai-           (unified gateway)
                        transparency/*
```

All four assessors follow the BaseAssessor pattern (D116), crosswalking through the NIST 800-53 US hub to cascade coverage to FedRAMP, CMMC, and other connected frameworks. Model cards and system cards are versioned and stored with CUI markings applied at generation time (D5). The AI inventory table schema aligns with OMB reporting fields so that federal agencies can export directly for their annual AI use case inventory submissions.

---

## 4. Database Schema

| Table | Purpose |
|-------|---------|
| `ai_model_cards` | Model card storage — model_id, model_name, provider, version, intended_use, limitations, evaluation_metrics (JSON), training_data_summary, ethical_considerations, created_at, updated_at, created_by, project_id, classification |
| `ai_system_cards` | System card storage — system_id, system_name, description, component_models (JSON array of model_ids), interaction_patterns, risk_category, deployment_context, human_oversight_level, project_id, created_at, updated_at |
| `ai_use_case_inventory` | AI use case registry — use_case_id, name, description, agency_component, risk_classification (rights_impacting/safety_impacting/presumed_risk/minimal_risk), deployment_status, responsible_official, review_date, omb_reporting_year, project_id, created_at |
| `ai_confabulation_log` | Confabulation detection results — detection_id, project_id, agent_id, trace_id, input_hash, output_hash, confidence_score, grounding_source, detected_claims (JSON), verified_at |
| `ai_fairness_assessments` | Fairness assessment results — assessment_id, project_id, model_id, dimension, metric_name, metric_value, threshold, passed, assessed_at |
| `ai_transparency_assessments` | Consolidated transparency audit results — assessment_id, project_id, framework, check_id, status, evidence, assessed_at |

---

## 5. Tools

| Tool | Purpose |
|------|---------|
| `tools/compliance/ai_model_card_generator.py` | Generate and manage model cards with versioned metadata, CUI markings, evaluation metrics |
| `tools/compliance/ai_inventory_manager.py` | AI use case inventory CRUD — register, classify, export for OMB reporting |
| `tools/compliance/confabulation_detector.py` | Reference-grounded confabulation detection — claim extraction, source verification, confidence scoring |
| `tools/compliance/fairness_assessor.py` | Fairness assessment — statistical parity, disparate impact, equalized odds across demographic dimensions |
| `tools/compliance/omb_m25_21_assessor.py` | OMB M-25-21 assessor — federal AI governance checks (inventory completeness, risk classification, oversight plans) |
| `tools/compliance/omb_m26_04_assessor.py` | OMB M-26-04 assessor — federal AI transparency checks (public reporting, model documentation, monitoring) |
| `tools/compliance/nist_ai_600_assessor.py` | NIST AI 600-1 assessor — generative AI risk profile checks (confabulation, CBRN, bias, provenance, IP) |
| `tools/compliance/gao_ai_assessor.py` | GAO-21-519SP assessor — AI accountability framework checks (governance, monitoring, transparency, accountability) |
| `tools/compliance/ai_transparency_audit.py` | Consolidated transparency audit — runs all 4 assessors, cross-framework coverage analysis, unified report |
| `tools/dashboard/api/ai_transparency.py` | Flask API blueprint for /ai-transparency dashboard and REST endpoints |
| `tools/dashboard/templates/ai_transparency.html` | Dashboard template — model card viewer, inventory table, assessment gauges, confabulation log |

---

## 6. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D307 | Model cards stored as versioned rows in ai_model_cards (no UPDATE, insert new version) | Consistent with D6 append-only audit pattern; preserves model card history for compliance audits |
| D308 | System cards reference model_ids via JSON array (not foreign key join table) | Composite systems change composition over time; JSON array captures point-in-time snapshot with version |
| D309 | AI inventory schema aligns with OMB reporting fields (risk_classification, agency_component, omb_reporting_year) | Federal agencies export directly from DB for annual OMB AI use case inventory submissions; no format transformation needed |
| D310 | Confabulation detector uses claim extraction + source verification (not LLM self-evaluation) | LLM self-evaluation is unreliable for detecting its own confabulations; grounded verification against source documents is deterministic |
| D311 | Fairness assessor uses statistical parity and disparate impact (4/5ths rule) | Industry-standard metrics recognized by EEOC and federal courts; deterministic computation, air-gap safe |
| D312 | Four assessors via BaseAssessor pattern (D116) with NIST 800-53 crosswalk | ~150-250 LOC each; cascade through US hub to FedRAMP/CMMC/CJIS per D111 dual-hub model |
| D313 | Transparency audit consolidates all 4 assessors into single report with deduplication | Auditors need unified view, not 4 separate reports; crosswalk deduplication prevents double-counting shared controls |
| D314 | Confabulation log stores input/output hashes (not plaintext) | Consistent with D216 AI telemetry privacy-preserving pattern; plaintext only with ICDEV_CONTENT_TRACING_ENABLED |
| D315 | Dashboard /ai-transparency page uses existing charts.js SVG patterns (D94) | Zero-dependency, air-gap safe; gauge for coverage, bar chart for assessor results, table for inventory |

---

## 7. Security Gate

**AI Transparency Gate:**
- **Blocking:** AI use case inventory empty for projects with AI components, model cards missing for deployed models, OMB M-25-21 assessment not completed for federal agency projects, rights-impacting use cases without human oversight documentation
- **Warning:** Fairness assessment older than 90 days, confabulation detection not enabled, NIST AI 600-1 coverage below 80%, system cards missing for multi-model workflows
- **Thresholds:** inventory_required=true, model_cards_required=true, min_omb_coverage_pct=80, fairness_max_age_days=90, confabulation_detection_required=true (for generative AI projects)

---

## 8. Commands

```bash
# Model cards
python tools/compliance/ai_model_card_generator.py --project-id "proj-123" --generate --json
python tools/compliance/ai_model_card_generator.py --project-id "proj-123" --model-id "claude-sonnet" --json
python tools/compliance/ai_model_card_generator.py --project-id "proj-123" --list --json

# AI inventory
python tools/compliance/ai_inventory_manager.py --project-id "proj-123" --register --json
python tools/compliance/ai_inventory_manager.py --project-id "proj-123" --list --json
python tools/compliance/ai_inventory_manager.py --project-id "proj-123" --export --format omb --json
python tools/compliance/ai_inventory_manager.py --project-id "proj-123" --classify --json

# Confabulation detection
python tools/compliance/confabulation_detector.py --project-id "proj-123" --text "LLM output" --sources "ref.md" --json
python tools/compliance/confabulation_detector.py --project-id "proj-123" --trace-id "<id>" --json
python tools/compliance/confabulation_detector.py --project-id "proj-123" --summary --json

# Fairness assessment
python tools/compliance/fairness_assessor.py --project-id "proj-123" --model-id "claude-sonnet" --json
python tools/compliance/fairness_assessor.py --project-id "proj-123" --all-models --json

# Framework assessors
python tools/compliance/omb_m25_21_assessor.py --project-id "proj-123" --json
python tools/compliance/omb_m25_21_assessor.py --project-id "proj-123" --gate
python tools/compliance/omb_m26_04_assessor.py --project-id "proj-123" --json
python tools/compliance/nist_ai_600_assessor.py --project-id "proj-123" --json
python tools/compliance/gao_ai_assessor.py --project-id "proj-123" --json

# Consolidated transparency audit
python tools/compliance/ai_transparency_audit.py --project-id "proj-123" --json
python tools/compliance/ai_transparency_audit.py --project-id "proj-123" --human --stream

# Dashboard page: /ai-transparency
# Portal page: /portal/ai-transparency
# REST API: GET/POST /api/v1/ai-transparency/model-cards
#            GET/POST /api/v1/ai-transparency/inventory
#            GET /api/v1/ai-transparency/confabulation
#            GET/POST /api/v1/ai-transparency/fairness
#            POST /api/v1/ai-transparency/audit
# MCP tools: ai_model_card_generate, ai_inventory_manage, confabulation_detect,
#            fairness_assess, transparency_audit
# Slash command: /icdev-transparency

# Configuration
# args/ai_transparency_config.yaml — model card templates, inventory categories,
#   confabulation thresholds, fairness metrics, OMB reporting fields
# args/security_gates.yaml — ai_transparency gate conditions
# context/compliance/omb_m25_21_requirements.json — OMB M-25-21 requirements catalog
# context/compliance/omb_m26_04_requirements.json — OMB M-26-04 requirements catalog
# context/compliance/nist_ai_600_requirements.json — NIST AI 600-1 requirements catalog
# context/compliance/gao_21_519sp_requirements.json — GAO-21-519SP requirements catalog
```

---

## 9. Related

- [Phase 46: Observability, Traceability & XAI](phase-46-observability-traceability-xai.md) -- Span-level tracing and AgentSHAP attribution
- [Phase 37: MITRE ATLAS Integration](phase-37-mitre-atlas-integration.md) -- AI security assessors and OWASP LLM Top 10
- [Phase 23: Universal Compliance Platform](phase-23-universal-compliance-platform.md) -- BaseAssessor pattern and dual-hub crosswalk
- [Phase 49: AI Accountability](phase-49-ai-accountability.md) -- Oversight plans, CAIO registry, incident response
