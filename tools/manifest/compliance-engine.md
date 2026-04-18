# Compliance Engine

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Compliance Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SSP Generator | tools/compliance/ssp_generator.py | System Security Plan generator (17 sections) | --project, --system-name | SSP document path |
| POAM Generator | tools/compliance/poam_generator.py | Plan of Action & Milestones generator | --project, --findings | POAM document path |
| STIG Checker | tools/compliance/stig_checker.py | STIG checklist auto-generation | --project, --stig-id, --target-type | Findings + checklist |
| SBOM Generator | tools/compliance/sbom_generator.py | CycloneDX SBOM generation | --project, --format | SBOM path |
| CUI Marker | tools/compliance/cui_marker.py | Apply CUI classification markings | --file, --directory | Marked file path |
| Control Mapper | tools/compliance/control_mapper.py | NIST 800-53 control mapping | --project, --control-families | Control matrix |
| NIST Lookup | tools/compliance/nist_lookup.py | NIST control reference lookup | --control-id | Control details |
| Compliance Status | tools/compliance/compliance_status.py | Compliance dashboard data (8 components incl. CSSP, SbD, IV&V) | --project | Status report |
| Classification Manager | tools/compliance/classification_manager.py | CUI/SECRET/TS markings, IL-to-baseline mapping, cross-domain controls | --impact-level, --classification, --banner, --code-header, --validate | Marking banners, baselines, validation |
| Crosswalk Engine | tools/compliance/crosswalk_engine.py | Dual-hub crosswalk engine (NIST+ISO 27001): FedRAMP, CMMC, 800-171, IL4/5/6, CJIS, HIPAA, HITRUST, SOC 2, PCI DSS, ISO 27001 | --control, --framework, --project-id, --coverage, --gap-analysis | Crosswalk mappings + coverage |
| PI Compliance Tracker | tools/compliance/pi_compliance_tracker.py | SAFe PI compliance tracking: start/close PIs, velocity, burndown, reports | --project-id, --start-pi, --velocity, --burndown, --report | PI metrics + reports |
| Complexity Compliance | tools/compliance/complexity_compliance.py | Maps cyclomatic/cognitive complexity to NIST SA-11(1/3/8) and SA-15(1/7/11) sub-controls as PDC compliance findings. Gates: SA-15(1) blocking on avg CC > 10. | --project-dir, --json, --gate, --no-trend, --control | JSON findings with control_id, severity, evidence |
| IQE Compliance Adapter | tools/iqe/adapters/compliance.py | Registers IQE collections compliance.snapshots (pi_compliance_tracking), compliance.controls (project_controls+compliance_controls JOIN), compliance.violations (poam_items). Import to activate. | import tools.iqe.adapters.compliance | Collections registered on module-level Executor |

