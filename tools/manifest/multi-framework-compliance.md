# Multi-Framework Compliance (Phase 17)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Multi-Framework Compliance (Phase 17)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| FedRAMP Assessor [DEPRECATED] | tools/compliance/fedramp_assessor.py | FedRAMP Moderate/High baseline assessment engine | --project-id, --baseline | Assessment results + gate |
| FedRAMP Report Generator | tools/compliance/fedramp_report_generator.py | FedRAMP assessment report with control family scores | --project-id, --baseline | Report path |
| CMMC Assessor [DEPRECATED] | tools/compliance/cmmc_assessor.py | CMMC Level 2/3 assessment (14 domains) | --project-id, --level | Assessment results + gate |
| CMMC Report Generator | tools/compliance/cmmc_report_generator.py | CMMC report with domain scores and 800-171 cross-ref | --project-id, --level | Report path |
| OSCAL Generator [DEPRECATED] | tools/compliance/oscal_generator.py | NIST OSCAL 1.1.2 artifact generator (SSP, POA&M, AR, CD) | --project-id, --artifact, --format, --deep-validate | OSCAL JSON/XML path |
| OSCAL Tools | tools/compliance/oscal_tools.py | OSCAL ecosystem orchestrator: deep validation, format conversion, profile resolution, catalog operations (D302-D305) | --detect, --validate, --convert, --resolve-profile, --catalog-lookup | Detection/validation/conversion results |
| OSCAL Catalog Adapter | tools/compliance/oscal_catalog_adapter.py | Unified NIST OSCAL + ICDEV™ catalog reader with fallback chain (D304) | --lookup, --list, --stats, --family | Control data, catalog stats |
| cATO Monitor | tools/compliance/cato_monitor.py | Continuous ATO evidence freshness and readiness monitoring | --project-id, --check-freshness, --readiness | Evidence status |
| cATO Scheduler | tools/compliance/cato_scheduler.py | Schedule-based evidence collection manager | --project-id, --run-due, --upcoming | Collection schedule |
| PI Compliance Tracker | tools/compliance/pi_compliance_tracker.py | SAFe PI-cadenced compliance tracking and velocity | --project-id, --pi, --velocity, --burndown | PI metrics |

