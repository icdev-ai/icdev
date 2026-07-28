# SWFT/SLSA + Cross-Phase Orchestration (Phase 54)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## SWFT/SLSA + Cross-Phase Orchestration (Phase 54)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| SLSA Attestation Generator | tools/compliance/slsa_attestation_generator.py | Generate SLSA v1.0 provenance statements and VEX documents from build pipeline evidence | --project-id, --generate, --verify, --vex, --json | SLSA provenance + VEX |
| SWFT Evidence Bundler | tools/compliance/swft_evidence_bundler.py | Bundle DoD SWFT evidence package (SLSA, SBOM, VEX, scan results) | --project-id, --output-dir, --json | SWFT bundle |
| Workflow Composer | tools/orchestration/workflow_composer.py | Declarative cross-phase workflow engine using YAML templates + TopologicalSorter DAG; runs `node_type: mcp` steps through the same executor as Studio | --template, --project-id, --run-id, --dry-run, --list, --json | Workflow execution plan + results |
| ATO Workflow Template | args/workflow_templates/ato_acceleration.yaml | Workflow: categorize → assess → SSP → POAM → SBOM | (template) | YAML workflow |
| Security Workflow Template | args/workflow_templates/security_hardening.yaml | Workflow: SAST → deps → secrets → OWASP → ANVIL | (template) | YAML workflow |
| Compliance Workflow Template | args/workflow_templates/full_compliance.yaml | Workflow: detect → multi-regime assess → crosswalk | (template) | YAML workflow |
| Build Workflow Template | args/workflow_templates/build_deploy.yaml | Workflow: scaffold → test → build → lint → deploy | (template) | YAML workflow |

