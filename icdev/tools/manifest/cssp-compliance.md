# CSSP Compliance (DI 8530.01)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## CSSP Compliance (DI 8530.01)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| CSSP Assessor [DEPRECATED] | tools/compliance/cssp_assessor.py | CSSP assessment across 5 functional areas | --project-id, --functional-area | Assessment results + report |
| CSSP Report Generator | tools/compliance/cssp_report_generator.py | CSSP certification report generation | --project-id, --output-dir | Report path |
| Incident Response Plan | tools/compliance/incident_response_plan.py | IR plan per CSSP SOC requirements | --project-id, --output-dir | IR plan path |
| SIEM Config Generator | tools/compliance/siem_config_generator.py | Splunk + ELK forwarding configs | --project-dir, --targets | Config file paths |
| CSSP Evidence Collector | tools/compliance/cssp_evidence_collector.py | Collect and index evidence for CSSP | --project-id, --project-dir | Evidence manifest |

