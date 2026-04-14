# IV&V (IEEE 1012)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## IV&V (IEEE 1012)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| IV&V Assessor | tools/compliance/ivv_assessor.py | Independent Verification & Validation (9 process areas, 18 auto-checks) | --project-id, --process-area | Assessment results + report |
| IV&V Report Generator | tools/compliance/ivv_report_generator.py | IV&V certification report with CERTIFY/CONDITIONAL/DENY recommendation | --project-id, --output-dir | Report path |
| Traceability Matrix | tools/compliance/traceability_matrix.py | Requirements Traceability Matrix (RTM) with gap analysis | --project-id, --project-dir | RTM document + JSON |

