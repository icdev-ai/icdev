# IV&V (IEEE 1012)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## IV&V (IEEE 1012)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| IV&V Assessor | tools/compliance/ivv_assessor.py | Independent Verification & Validation (9 process areas, 18 auto-checks) | --project-id, --process-area | Assessment results + report |
| IV&V Report Generator | tools/compliance/ivv_report_generator.py | IV&V certification report with CERTIFY/CONDITIONAL/DENY recommendation | --project-id, --output-dir | Report path |
| Traceability Matrix | tools/compliance/traceability_matrix.py | Requirements Traceability Matrix (RTM) with gap analysis | --project-id, --project-dir | RTM document + JSON |
| Portable IV&V / SbD / RTM Result | tools/compliance/portable_result.py | icx-ivv-01: register an application imported from its icos-apptwin bundle (canonical create_project, no scaffold) and emit IV&V, SbD and RTM as dated, portable bundle sections (kind automated, expires_at, the reports' own recommendation and score rules) for ICDEV[OS] | register --bundle --project-dir; ivv/sbd/rtm --project-id --emit-json [--valid-days] [--bundle: the result records its subject, icx-sub-01] | Bundle section JSON |
