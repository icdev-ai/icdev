# DevSecOps & Zero Trust Architecture (Phase 24-25)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## DevSecOps & Zero Trust Architecture (Phase 24-25)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| DevSecOps Profile Manager | tools/devsecops/profile_manager.py | DevSecOps profile CRUD, maturity detection, assessment | --project-id, --create, --detect, --assess, --update, --json | Profile + maturity level |
| ZTA Maturity Scorer | tools/devsecops/zta_maturity_scorer.py | 7-pillar ZTA maturity scoring (DoD ZTA Strategy) | --project-id, --pillar, --all, --trend, --json | Pillar scores + aggregate |
| Pipeline Security Generator | tools/devsecops/pipeline_security_generator.py | Profile-driven GitLab CI security stage generation | --project-id, --json | YAML security stages |
| Policy Generator | tools/devsecops/policy_generator.py | Kyverno/OPA policy-as-code generation (pod security, registry, RBAC) | --project-id, --engine kyverno\|opa, --json | Policy YAML/Rego |
| Attestation Manager | tools/devsecops/attestation_manager.py | Image signing (Cosign/Notation) + SBOM attestation (SLSA Level 3) | --project-id, --generate, --verify, --json | Signing config + attestation |
| Service Mesh Generator | tools/devsecops/service_mesh_generator.py | Istio/Linkerd service mesh config generation (mTLS, AuthzPolicy) | --project-id, --mesh istio\|linkerd, --json | Service mesh YAML |
| ZTA Terraform Generator | tools/devsecops/zta_terraform_generator.py | ZTA security modules (GuardDuty, SecurityHub, WAF, Config Rules) | --project-path, --modules, --json | .tf files |
| Network Segmentation Generator | tools/devsecops/network_segmentation_generator.py | Namespace isolation + per-pod microsegmentation NetworkPolicies | --project-path, --namespaces, --services, --json | NetworkPolicy YAML |
| PDP Config Generator | tools/devsecops/pdp_config_generator.py | PDP/PEP configuration (Zscaler, Palo Alto, DISA ICAM) | --project-id, --pdp-type, --mesh, --json | PDP/PEP config |
| NIST 800-207 Assessor | tools/compliance/nist_800_207_assessor.py | NIST SP 800-207 ZTA compliance assessment (BaseAssessor pattern) | --project-id, --gate, --json | Assessment + gate |
| MCP DevSecOps Server | tools/mcp/devsecops_server.py | MCP server for DevSecOps/ZTA tools (12 tools) | stdio | JSON-RPC responses |

| Canonical Asset Identity | tools/assets/identity.py | ONE asset identity across the three ZT/asset stacks (7-pillar ZTA on project_id, NSA ZIG on sha256(hostname), NDC/PVM on ni_devices.id); resolvers, ingest, and the device -> ZT decision -> attack surface -> enclave join | --ingest, --list, --posture <asset>, --fleet, --stats, --json | asset_identity rows + joined posture |
