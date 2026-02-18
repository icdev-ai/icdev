# Gap Detection Analysis Prompt

> CUI // SP-CTI

Analyze the following requirements set for gaps and missing elements.

## Input
- Session ID: {{session_id}}
- Impact Level: {{impact_level}}
- Requirements: {{requirements_json}}
- Current NIST control coverage: {{control_coverage}}

## Analysis Tasks

1. **Security Gaps**: Check if requirements address all critical NIST 800-53 control families for the impact level:
   - AC (Access Control) — authentication, authorization, account management
   - AU (Audit) — logging, audit trail, event monitoring
   - IA (Identification & Authentication) — CAC/PIV, MFA, credential management
   - SC (System & Communications Protection) — encryption, boundary protection
   - SI (System & Information Integrity) — input validation, error handling, malware protection
   - IR (Incident Response) — detection, reporting, containment
   - CP (Contingency Planning) — backup, recovery, failover

2. **Data Gaps**: Check for missing data requirements:
   - Data classification and marking
   - Data retention and disposal
   - Data backup and recovery
   - Data integrity and validation

3. **Interface Gaps**: For each external system mentioned:
   - Protocol specified? (REST/SOAP/MQ/file)
   - Authentication method specified?
   - ISA/MOU identified?
   - Data format specified?

4. **Operational Gaps**: Check for missing operational requirements:
   - Monitoring and alerting
   - Disaster recovery
   - Maintenance windows
   - Capacity planning

5. **Testability Gaps**: Check for requirements without acceptance criteria:
   - No Given/When/Then
   - No measurable threshold
   - Subjective language only

## Output Format
```json
{
  "gaps": [
    {
      "gap_id": "GAP-xxx",
      "category": "security|data|interface|operational|testability",
      "severity": "critical|high|medium|low",
      "description": "What is missing",
      "affected_controls": ["AC-2", "IA-2"],
      "recommendation": "What to ask the customer",
      "suggested_question": "Specific question to ask"
    }
  ],
  "summary": {
    "total_gaps": 0,
    "critical": 0,
    "high": 0,
    "medium": 0,
    "low": 0,
    "categories_with_gaps": []
  }
}
```
