# FedRAMP 20x KSI + OWASP ASI (Phase 53)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## FedRAMP 20x KSI + OWASP ASI (Phase 53)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| FedRAMP 20x KSI Generator | tools/compliance/fedramp_ksi_generator.py | Generate Key Security Indicators (KSIs) for FedRAMP 20x authorization. Maps ICDEV™ evidence to 61 KSI schemas. | --project-id, --ksi-id, --all, --json | KSI evidence manifest |
| FedRAMP Auth Packager | tools/compliance/fedramp_authorization_packager.py | Bundle OSCAL SSP + KSI evidence into FedRAMP 20x authorization package | --project-id, --output-dir, --json | Authorization bundle |
| FedRAMP 20x API | tools/dashboard/api/fedramp_20x.py | Blueprint: stats, KSI list, generate, package | /api/fedramp-20x/* | REST endpoints |
| FedRAMP 20x Page | tools/dashboard/templates/fedramp_20x.html | Dashboard: stat-grid + KSI table + package status | (template) | HTML page |
| KSI Schemas | context/compliance/fedramp_20x_ksi_schemas.json | 61 KSI definitions (id, title, family, evidence_sources, nist_crosswalk) | (catalog) | JSON catalog |
| OWASP ASI Assessor | tools/compliance/owasp_asi_assessor.py | BaseAssessor for OWASP ASI01-ASI10 agentic AI risks. Maps 10 ASI risks to ICDEV™ controls via NIST 800-53 crosswalk. | --project-id, --json, --gate | Assessment JSON |
| OWASP ASI Catalog | context/compliance/owasp_agentic_asi.json | 10 ASI risk definitions with NIST crosswalk | (catalog) | JSON catalog |

