# Goal: Requirements Intake & Decomposition (RICOAS Phase 1)

## Purpose

Transform vague customer requirements into structured, decomposed, MBSE-traced, compliance-validated work items through AI-driven conversational intake.

## When to Use

- Customer provides new requirements (SOW, CDD, CONOPS, verbal)
- Existing requirements need refinement or gap analysis
- Requirements need SAFe decomposition (Epic > Feature > Story)
- BDD acceptance criteria generation needed
- Readiness assessment before proceeding to architecture/build

## Workflow

### Stage 1: Session Setup

1. Create intake session: `create_intake_session` (MCP) or `python tools/requirements/intake_engine.py --project-id <id> --customer-name <name> --json`
2. Session stores: customer info, impact level (IL2-IL6), classification, ATO context

### Stage 2: Conversational Intake

1. Process customer messages via `process_intake_turn` (MCP) or CLI `--message`
2. Engine extracts requirements from each turn automatically
3. Detects ambiguities in real-time (patterns from context/requirements/ambiguity_patterns.json)
4. Detects gap signals (security, performance, data, compliance)
5. Detects ATO boundary impact signals
6. Every 5 turns (configurable), auto-runs gap detection and readiness scoring

### Stage 3: Document Upload (Optional)

1. Customer uploads SOW/CDD/CONOPS via `upload_document`
2. Extract requirements via `extract_document`
3. Extracted requirements merge into session's requirement set
4. Supports: PDF (pypdf), DOCX (python-docx), TXT, MD

### Stage 4: Gap Detection & Readiness Scoring

1. Run gap detection: `detect_gaps` — checks security, compliance, testability, interfaces, data
2. Gaps reference NIST 800-53 controls and provide remediation recommendations
3. Run readiness scoring: `score_readiness` — 5 dimensions (completeness, clarity, feasibility, compliance, testability)
4. Thresholds: 0.7 = proceed to decomposition, 0.8 = proceed to COA, 0.9 = proceed to implementation
5. Score trend tracked across turns to show progress

### Stage 5: SAFe Decomposition

1. Decompose requirements: `decompose_requirements` with target level (epic/feature/story)
2. Generates SAFe hierarchy: Epic > Capability > Feature > Story > Enabler
3. T-shirt size estimation per item (XS through XXL)
4. WSJF scoring for prioritization
5. Optional BDD acceptance criteria (Gherkin Given/When/Then)

### Stage 6: Export & Handoff

1. Export requirements: `python tools/requirements/intake_engine.py --session-id <id> --export --json`
2. Decomposed items ready for Architect agent (ATLAS workflow)
3. Requirements link to digital thread for MBSE traceability
4. Audit trail records all intake events

---

## Tools Used

| Tool | Purpose |
|------|---------|
| tools/requirements/intake_engine.py | Conversational intake, session management |
| tools/requirements/gap_detector.py | Gap and ambiguity detection |
| tools/requirements/readiness_scorer.py | 5-dimension readiness scoring |
| tools/requirements/decomposition_engine.py | SAFe hierarchy decomposition |
| tools/requirements/document_extractor.py | Document upload and extraction |
| tools/mcp/requirements_server.py | MCP server (10 tools) |

## Args

- `args/ricoas_config.yaml` — Readiness weights, thresholds, gap detection settings, cost models

## Context

- `context/requirements/gap_patterns.json` — 10 gap detection patterns with NIST mappings
- `context/requirements/ambiguity_patterns.json` — 15 ambiguity patterns with clarification suggestions
- `context/requirements/safe_templates.json` — SAFe hierarchy templates and WSJF formula
- `context/requirements/document_extraction_rules.json` — Extraction rules per document type
- `context/requirements/readiness_rubric.json` — 5-dimension scoring rubric

## Hard Prompts

- `hardprompts/requirements/intake_conversation.md` — Intake agent system prompt
- `hardprompts/requirements/gap_detection.md` — Gap analysis prompt
- `hardprompts/requirements/decomposition.md` — SAFe decomposition prompt
- `hardprompts/requirements/document_extraction.md` — Document extraction prompt
- `hardprompts/requirements/bdd_generation.md` — BDD criteria generation prompt
- `hardprompts/requirements/readiness_assessment.md` — Readiness scoring prompt

---

## Edge Cases

- Customer provides contradictory requirements → flag as gap with both references
- Document extraction finds 0 requirements → suggest different document type or manual entry
- Readiness score stuck below threshold → show trend, suggest specific areas to address
- Session resumed after long gap → summarize previous context to customer
- Impact level change mid-session → re-run boundary analysis on all requirements

---

## Success Criteria

- All requirements captured with type, priority, and source
- Readiness score >= 0.7 before decomposition
- Zero unresolved critical gaps
- SAFe items have acceptance criteria and estimates
- Full audit trail of intake process

---

## GOTCHA Layer Mapping

| Intake Stage | GOTCHA Layer |
|--------------|--------------|
| Session Setup | Goals (define what to capture) |
| Conversational Intake | Orchestration (AI guides conversation) |
| Document Upload | Tools (extraction scripts) |
| Gap Detection | Context (gap patterns, ambiguity patterns) |
| Readiness Scoring | Args (thresholds, weights) |
| SAFe Decomposition | Hard Prompts (decomposition templates) |

---

## Related Files

- **Goal:** `goals/build_app.md` — ATLAS workflow (receives decomposed requirements)
- **Goal:** `goals/mbse_integration.md` — MBSE digital thread (links to requirements)
- **Goal:** `goals/compliance_workflow.md` — Compliance artifacts (informed by gap analysis)
- **Skill:** `.claude/skills/icdev-intake/SKILL.md` — Claude Code slash command

---

## Changelog
