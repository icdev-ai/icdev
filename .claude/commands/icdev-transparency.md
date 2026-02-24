# CUI // SP-CTI

## AI Transparency & Accountability Workflow (Phase 48)

Run the complete AI transparency workflow for the current project. This orchestrates
all Phase 48 tools: AI inventory, model/system cards, assessors, confabulation detection,
fairness assessment, GAO evidence, and cross-framework audit.

### Steps

1. **AI Inventory Check** — Verify AI components are registered in the OMB M-25-21 inventory
   ```bash
   python tools/compliance/ai_inventory_manager.py --project-id "$PROJECT_ID" --list --json
   ```

2. **Model Card Generation** — Generate/update model cards for all registered AI components
   ```bash
   python tools/compliance/model_card_generator.py --project-id "$PROJECT_ID" --model-name "<name>" --json
   ```

3. **System Card Generation** — Generate/update the system-level AI card
   ```bash
   python tools/compliance/system_card_generator.py --project-id "$PROJECT_ID" --json
   ```

4. **OMB M-25-21 Assessment** — High-Impact AI classification and oversight
   ```bash
   python tools/compliance/omb_m25_21_assessor.py --project-id "$PROJECT_ID" --json
   ```

5. **OMB M-26-04 Assessment** — Unbiased AI, model cards, fairness
   ```bash
   python tools/compliance/omb_m26_04_assessor.py --project-id "$PROJECT_ID" --json
   ```

6. **NIST AI 600-1 Assessment** — GenAI risk profile (confabulation, privacy, integrity)
   ```bash
   python tools/compliance/nist_ai_600_1_assessor.py --project-id "$PROJECT_ID" --json
   ```

7. **GAO-21-519SP Assessment** — AI accountability (governance, data, performance, monitoring)
   ```bash
   python tools/compliance/gao_ai_assessor.py --project-id "$PROJECT_ID" --json
   ```

8. **Confabulation Detection Status** — Check confabulation monitoring is active
   ```bash
   python tools/security/confabulation_detector.py --project-id "$PROJECT_ID" --summary --json
   ```

9. **Fairness Assessment** — Evaluate bias/fairness compliance evidence
   ```bash
   python tools/compliance/fairness_assessor.py --project-id "$PROJECT_ID" --json
   ```

10. **GAO Evidence Package** — Compile audit evidence across all 4 GAO principles
    ```bash
    python tools/compliance/gao_evidence_builder.py --project-id "$PROJECT_ID" --json
    ```

11. **Cross-Framework Transparency Audit** — Unified report with gap analysis
    ```bash
    python tools/compliance/ai_transparency_audit.py --project-id "$PROJECT_ID" --json
    ```

### Expected Output

The final transparency audit produces:
- **Overall Transparency Score** (0-100%) — weighted: 60% framework assessments, 30% artifact completeness, 10% GAO evidence
- **Framework Assessment Scores** — per-framework coverage percentages
- **Artifact Completeness** — model cards, system cards, inventory, fairness assessments, confabulation checks
- **Gap Analysis** — prioritized list of missing items with remediation commands
- **Recommendation** — PASS or ACTION REQUIRED based on combined score and gap priorities

### Architecture Decisions
- D307: All 4 assessors use BaseAssessor ABC (D116)
- D308: Model cards follow Google Model Cards format
- D309: System cards cover full agentic system
- D310: Confabulation detector uses deterministic methods only (air-gap safe)
- D311: Fairness assessor focuses on compliance documentation evidence
- D312: AI inventory follows OMB M-25-21 schema
- D313: GAO evidence builder reuses existing ICDEV data
- D314: AI data category trigger auto-activates all 4 frameworks
- D315: COSAiS overlay mapping deferred until NIST publishes final spec
