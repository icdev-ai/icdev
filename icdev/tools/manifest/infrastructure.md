# Infrastructure

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Infrastructure
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Terraform Generator | tools/infra/terraform_generator.py | Generate Terraform for GovCloud | --project | .tf files |
| Ansible Generator | tools/infra/ansible_generator.py | Generate Ansible playbooks | --project | .yml playbooks |
| K8s Generator | tools/infra/k8s_generator.py | Generate Kubernetes manifests | --project | .yaml manifests |
| Dockerfile Generator | tools/infra/dockerfile_generator.py | STIG-hardened Dockerfiles | --project | Dockerfile |
| Pipeline Generator | tools/infra/pipeline_generator.py | Generate .gitlab-ci.yml | --project | Pipeline file |
| Rollback Manager | tools/infra/rollback.py | Deployment rollback | --project, --environment | Rollback result |
| Infra Status | tools/infra/infra_status.py | Infrastructure status report | --project | Status |
| IDC IaC Generator | tools/infra_canvas/iac_generator.py | Multi-CSP IaC emitters (Terraform, CloudFormation, Pulumi, Ansible, Helm) from IDC graph. All 6 CSPs: AWS GovCloud, Azure Gov, GCP, OCI, IBM, On-Prem. CUI headers. | generate_terraform(graph), generate_cloudformation(graph), generate_pulumi(graph), generate_ansible(graph), generate_helm(graph) | HCL str, CF YAML str, Pulumi Python str, Ansible YAML str, Helm ZIP bytes |
| DR Failover | tools/infra/dr_failover.py | ICDEV™ Disaster Recovery Failover Automation. Promotes DR read replicas, runs quarterly DR tests, reports DR health (replica lag, snapshots), and restores from cross-account RDS snapshots. RTO target 4h, RPO 15min. | `status`, `test`, `failover --confirm`, `restore --snapshot-id <id>` | JSON dict |
| DR Generator | tools/infra/dr_generator.py | Auto-registered: infra/dr_generator.py | --json | JSON |

