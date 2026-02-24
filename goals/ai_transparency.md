# CUI // SP-CTI
# Goal: AI Transparency & Accountability (Phase 48)

## Purpose
Bridge the vocabulary gap between ICDEV's technical AI capabilities (XAI, SHAP, telemetry)
and government stakeholder terminology (model cards, High-Impact AI, bias testing, GAO audit
readiness). Implements 4 new compliance frameworks and 8 supporting tools.

## Trigger
- User runs `/icdev-transparency`
- AI data category detected in project (auto-triggers via D314)
- Manual: any Phase 48 tool invoked via CLI or MCP

## Frameworks
| Framework | Source | Requirements | Assessor |
|-----------|--------|-------------|----------|
| OMB M-25-21 | Nov 2025 | 15 (AI inventory, high-impact classification, risk management, oversight) | `omb_m25_21_assessor.py` |
| OMB M-26-04 | Jan 2026 | 16 (model cards, bias testing, fairness, human review, impact assessment) | `omb_m26_04_assessor.py` |
| NIST AI 600-1 | Jul 2024 | 18 (12 GAI risk categories: confabulation, privacy, integrity, CBRN, etc.) | `nist_ai_600_1_assessor.py` |
| GAO-21-519SP | Jun 2021 | 16 (4 principles: governance, data, performance, monitoring) | `gao_ai_assessor.py` |

## Tools
| Tool | File | Purpose |
|------|------|---------|
| AI Inventory Manager | `tools/compliance/ai_inventory_manager.py` | OMB M-25-21 public AI use case inventory |
| Model Card Generator | `tools/compliance/model_card_generator.py` | OMB M-26-04 / Google Model Cards format |
| System Card Generator | `tools/compliance/system_card_generator.py` | System-level AI documentation |
| Confabulation Detector | `tools/security/confabulation_detector.py` | NIST AI 600-1 GAI.1 hallucination detection |
| Fairness Assessor | `tools/compliance/fairness_assessor.py` | OMB M-26-04 bias/fairness compliance evidence |
| GAO Evidence Builder | `tools/compliance/gao_evidence_builder.py` | GAO-21-519SP audit evidence compilation |
| AI Transparency Audit | `tools/compliance/ai_transparency_audit.py` | Cross-framework unified transparency report |

## Workflow
1. Check AI inventory for registered components
2. Generate model cards for each AI model
3. Generate system card for the project
4. Run all 4 framework assessors
5. Check confabulation detection status
6. Run fairness assessment
7. Build GAO evidence package
8. Run cross-framework transparency audit
9. Report gaps with remediation commands

## Gates
- **Blocking**: high_impact_ai_not_classified, model_cards_missing, ai_inventory_incomplete, gao_evidence_gaps, confabulation_detection_not_active
- **Warning**: system_card_stale, fairness_assessment_not_conducted, bias_mitigation_not_documented, appeal_process_not_defined

## Database Tables (9 new)
- `omb_m25_21_assessments` — BaseAssessor standard schema
- `omb_m26_04_assessments` — BaseAssessor standard schema
- `nist_ai_600_1_assessments` — BaseAssessor standard schema
- `gao_ai_assessments` — BaseAssessor standard schema
- `model_cards` — id, project_id, model_name, card_data, card_hash, version, created_at
- `system_cards` — id, project_id, card_data, card_hash, version, created_at
- `confabulation_checks` — id, project_id, check_type, input_hash, result, risk_score, findings_count, created_at
- `ai_use_case_inventory` — id, project_id, name, purpose, risk_level, classification, deployment_status, etc.
- `fairness_assessments` — id, project_id, assessment_data, overall_score, created_at

## Architecture Decisions
- **D307**: BaseAssessor ABC pattern (D116) — ~150-200 LOC each, automatic gate/CLI/crosswalk
- **D308**: Google Model Cards format — open standard, Gov AI community
- **D309**: System cards are ICDEV-specific — broader than model cards
- **D310**: Confabulation detector — deterministic methods only, air-gap safe
- **D311**: Fairness assessor — compliance documentation evidence, not statistical bias testing
- **D312**: AI inventory — OMB M-25-21 schema for government reporting
- **D313**: GAO evidence builder — reuses existing ICDEV data, no new collection
- **D314**: AI data category trigger — auto-activates all 4 frameworks
- **D315**: COSAiS overlay — deferred until NIST publishes final spec (late 2026)

## Success Criteria
- All 4 assessors produce valid JSON output with --json flag
- Crosswalk engine returns Phase 48 frameworks for mapped NIST controls
- Gate evaluation works (--gate flag)
- Dashboard /ai-transparency page renders with stat grid and tables
- AI data category trigger auto-activates frameworks when AI components detected
- 120+ tests pass across 6 test files
