# Universal Compliance Platform (Phase 23)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Universal Compliance Platform (Phase 23)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Universal Classification Mgr | tools/compliance/universal_classification_manager.py | Composable data markings for 10 categories (CUI, PHI, PCI, CJIS, etc.) | --banner, --code-header, --detect, --validate, --add-category | Composite banners, headers, validation |
| Base Assessor | tools/compliance/base_assessor.py | ABC base class for all Wave 1+ assessors (crosswalk, gate, CLI) | (imported by subclasses) | Assessment + gate results |
| CJIS Assessor | tools/compliance/cjis_assessor.py | FBI CJIS Security Policy v5.9.4 assessment | --project-id, --gate, --json | CJIS compliance + gate |
| HIPAA Assessor | tools/compliance/hipaa_assessor.py | HIPAA Security Rule (45 CFR 164) assessment | --project-id, --gate, --json | HIPAA compliance + gate |
| HITRUST Assessor | tools/compliance/hitrust_assessor.py | HITRUST CSF v11 assessment | --project-id, --gate, --json | HITRUST compliance + gate |
| SOC 2 Assessor | tools/compliance/soc2_assessor.py | SOC 2 Type II Trust Service Criteria assessment | --project-id, --gate, --json | SOC 2 compliance + gate |
| PCI DSS Assessor | tools/compliance/pci_dss_assessor.py | PCI DSS v4.0 assessment | --project-id, --gate, --json | PCI DSS compliance + gate |
| ISO 27001 Assessor | tools/compliance/iso27001_assessor.py | ISO/IEC 27001:2022 assessment (international hub) | --project-id, --gate, --json | ISO 27001 compliance + gate |
| Resolve Marking | tools/compliance/resolve_marking.py | Central classification marking resolver — determines banner, code header, grep pattern per project (ADR D132) | --project-id, --json, --banner-only, --code-header LANG, --check-required | Marking dict (marking_required, banner, code_header, grep_pattern, vision_assertion) |
| Compliance Detector | tools/compliance/compliance_detector.py | Auto-detect applicable frameworks from data categories | --project-id, --apply, --confirm, --json | Detected frameworks |
| Multi-Regime Assessor | tools/compliance/multi_regime_assessor.py | Unified multi-framework assessment + gate + minimal controls | --project-id, --gate, --minimal-controls, --json | Unified report + prioritized controls |

