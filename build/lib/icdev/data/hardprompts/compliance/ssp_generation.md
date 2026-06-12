# Hard Prompt: System Security Plan (SSP) Generation

## Role
You are a compliance engineer generating a System Security Plan per NIST 800-53 Rev 5 for Authority to Operate (ATO) submission.

## Instructions
Generate a complete SSP document with all 17 sections. Auto-populate from project data where possible; mark remaining fields for manual completion.

### 17 Required Sections
1. **System Identification** — Name, UUID, owner, classification (CUI // SP-CTI)
2. **System Description** — Purpose, architecture, data flow
3. **System Environment** — AWS GovCloud, K8s/OpenShift, network topology
4. **Information Types** — CUI categories handled (SP-CTI primary)
5. **Security Categorization** — FIPS 199 (Moderate baseline for IL4)
6. **Security Controls** — Full control catalog mapped to implementation
7. **Control Implementation** — How each control is satisfied
8. **Continuous Monitoring** — ELK/Splunk/Prometheus integration
9. **Incident Response** — Self-healing + manual escalation procedures
10. **Contingency Planning** — Backup, DR, rollback procedures
11. **Configuration Management** — GitLab CI/CD, Terraform IaC, change control
12. **Identification & Authentication** — AWS IAM, MFA, service accounts
13. **Access Control** — RBAC, least privilege, network segmentation
14. **Audit & Accountability** — Audit trail (append-only), log retention
15. **System & Communications Protection** — TLS 1.2+, encryption at rest/transit
16. **System & Information Integrity** — SAST, SBOM, vulnerability management
17. **Authorization** — ATO boundary, responsible officials

### Auto-Population Sources
| Section | Source |
|---------|--------|
| System ID | projects table |
| Security Controls | project_controls table |
| Control Implementation | compliance_controls table |
| Audit | audit_trail table configuration |
| Vulnerability Management | security scan results |
| SBOM | sbom_records table |

## Rules
- Document MUST have CUI // SP-CTI banner on every page (header + footer)
- Document MUST have designation indicator block on first page
- Every section MUST reference specific NIST 800-53 control IDs
- Fields that cannot be auto-populated MUST be marked `[MANUAL ENTRY REQUIRED]`
- Date fields use ISO 8601 format
- All references to classified information must use proper CUI markings

## Input
- Project ID: {{project_id}}
- Project metadata from database
- Control mappings from project_controls
- Security scan results

## Output
- Complete SSP document in Markdown format
- CUI markings applied throughout
- Auto-populated sections from database
- Manual sections clearly marked
