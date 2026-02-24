# CUI // SP-CTI

## AI Accountability Workflow (Phase 49)

Run the complete AI accountability workflow for the current project. This orchestrates
all Phase 49 tools: oversight plans, CAIO designation, appeal tracking, incident response,
ethics reviews, reassessment scheduling, and cross-framework accountability audit.

### Steps

1. **Accountability Summary** — Check current accountability posture
   ```bash
   python tools/compliance/accountability_manager.py --project-id "$PROJECT_ID" --summary --json
   ```

2. **Oversight Plan Check** — Verify human oversight plans are registered (M25-OVR-1)
   ```bash
   python tools/compliance/accountability_manager.py --project-id "$PROJECT_ID" --list-plans --json
   ```

3. **CAIO Designation Check** — Verify Chief AI Officer is designated (M25-OVR-4)
   ```bash
   python tools/compliance/accountability_manager.py --project-id "$PROJECT_ID" --list-caio --json
   ```

4. **Appeal Process Check** — Verify appeal mechanism is registered (M25-OVR-3, M26-REV-2, FAIR-7)
   ```bash
   python tools/compliance/accountability_manager.py --project-id "$PROJECT_ID" --list-appeals --json
   ```

5. **Incident Response Check** — Check AI incident tracking status (M25-RISK-4, GAO-MON-3)
   ```bash
   python tools/compliance/ai_incident_response.py --project-id "$PROJECT_ID" --stats --json
   ```

6. **Ethics Review Status** — Check ethics reviews are conducted (GAO-GOV-2/3, M26-REV-3)
   ```bash
   python tools/compliance/accountability_manager.py --project-id "$PROJECT_ID" --list-ethics --json
   ```

7. **Impact Assessment Status** — Check algorithmic impact assessments (M26-IMP-1)
   ```bash
   python tools/compliance/ai_impact_assessor.py --project-id "$PROJECT_ID" --summary --json
   ```

8. **Reassessment Schedule Check** — Verify reassessment schedules exist (M25-INV-3, GAO-MON-4)
   ```bash
   python tools/compliance/ai_reassessment_scheduler.py --project-id "$PROJECT_ID" --summary --json
   ```

9. **Overdue Reassessments** — Check for overdue reassessments
   ```bash
   python tools/compliance/ai_reassessment_scheduler.py --project-id "$PROJECT_ID" --overdue --json
   ```

10. **Cross-Framework Accountability Audit** — Unified report with gap analysis
    ```bash
    python tools/compliance/ai_accountability_audit.py --project-id "$PROJECT_ID" --json
    ```

### Expected Output

The final accountability audit produces:
- **Overall Accountability Score** (0-100%) — weighted across 13 checks
- **Per-Framework Status** — M25-21, M26-04, GAO, Fairness coverage
- **Gap Analysis** — prioritized list of missing accountability evidence with severity and remediation
- **Recommendation** — PASS or ACTION REQUIRED

### Common Actions

Register an oversight plan:
```bash
python tools/compliance/accountability_manager.py --project-id "$PROJECT_ID" --register-plan --plan-name "Human Oversight Plan" --json
```

Designate a CAIO:
```bash
python tools/compliance/accountability_manager.py --project-id "$PROJECT_ID" --designate-caio --name "Jane Smith" --role "CAIO" --json
```

File an appeal:
```bash
python tools/compliance/accountability_manager.py --project-id "$PROJECT_ID" --file-appeal --appellant "John Doe" --ai-system "Classifier v2" --json
```

Log an incident:
```bash
python tools/compliance/ai_incident_response.py --project-id "$PROJECT_ID" --log --type bias_detected --severity high --description "Disparate impact detected" --json
```

Submit an ethics review:
```bash
python tools/compliance/accountability_manager.py --project-id "$PROJECT_ID" --submit-ethics --review-type "bias_testing_policy" --json
```

### Architecture Decisions
- D316: Accountability tables are append-only (D6) except ai_caio_registry and ai_reassessment_schedule
- D317: Accountability manager is a single coordinator tool (not 13 separate tools)
- D318: AI incident log is separate from audit_trail (AI-specific events requiring corrective action)
- D319: Ethics reviews store boolean flags for fast assessor checks
- D320: Impact assessment stored in ai_ethics_reviews with review_type='impact_assessment'
- D321: Fairness gate lowered to 25% to be achievable with DB-only checks
