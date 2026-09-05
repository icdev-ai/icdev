# Cloud-Agnostic Architecture (Phase 38 — D223-D231)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Cloud-Agnostic Architecture (Phase 38 — D223-D231)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cloud Mode Manager | tools/cloud/cloud_mode_manager.py | Cloud mode orchestrator — status, validation, readiness checks for commercial/government/on_prem/air_gapped (D232) | --status, --validate, --eligible, --check-readiness, --json | Mode validation |
| CSP Provider Factory | tools/cloud/provider_factory.py | Config-driven CSP factory from cloud_config.yaml — lazy instantiation, per-service override | service name | Provider instance |
| Secrets Provider | tools/cloud/secrets_provider.py | ABC + 5 implementations (AWS, Azure, GCP, OCI, Local) for secret management | get/put/list/delete | Secret data |
| Storage Provider | tools/cloud/storage_provider.py | ABC + 5 implementations (S3, Blob, GCS, OCI Object, Local) for object storage | upload/download/list/delete | Storage data |
| KMS Provider | tools/cloud/kms_provider.py | ABC + 5 implementations (AWS KMS, Azure KV, GCP Cloud KMS, OCI Vault, Local Fernet) for encryption | encrypt/decrypt/generate_key | Encrypted data |
| Monitoring Provider | tools/cloud/monitoring_provider.py | ABC + 5 implementations (CloudWatch, Azure Monitor, Cloud Monitoring, OCI, Local) for metrics/logs | send_metric/send_log | Metrics/logs |
| IAM Provider | tools/cloud/iam_provider.py | ABC + 5 implementations (AWS IAM, Entra ID, Cloud IAM, OCI, Local) for identity | create_role/check_permission | IAM data |
| Registry Provider | tools/cloud/registry_provider.py | ABC + 5 implementations (ECR, ACR, Artifact Registry, OCIR, Local) for container images | list/push/pull | Image data |
| CSP Health Checker | tools/cloud/csp_health_checker.py | Health check all CSP services, integrates with heartbeat daemon (D230) | --check-all, --json | Service statuses |
| CSP Region Validator | tools/cloud/region_validator.py | CSP Region Validator — compliance-driven deployment validation (D234). Validates CSP regions hold required certifications before deployment. | validate/eligible/deployment-check/list, --csp, --region, --frameworks, --impact-level, --json | Validation results |
| CSP Monitor | tools/cloud/csp_monitor.py | Autonomous CSP service monitor — scans feeds, diffs registry, generates innovation signals (D239) | --scan --all, --diff, --status, --daemon, --json | Signals + changes |
| CSP Changelog | tools/cloud/csp_changelog.py | Human-readable changelog with per-change-type recommendations (D241) | --generate, --summary, --days, --format, --json | Changelog report |
| Cloud Config | args/cloud_config.yaml | Master config: provider, region, IL, per-service CSP overrides (D225) | (data) | YAML config |
| CSP Monitor Config | args/csp_monitor_config.yaml | CSP monitoring config: sources, signals, diff engine, scheduling (D239) | (data) | YAML config |
| CSP Service Registry | context/cloud/csp_service_registry.json | Baseline CSP service catalog: 45+ services, compliance programs, regions (D240) | (data) | JSON registry |
| Azure OpenAI Provider | tools/llm/azure_openai_provider.py | Azure OpenAI Service LLM provider with government endpoints | LLMRequest | LLMResponse |
| Vertex AI Provider | tools/llm/vertex_ai_provider.py | Google Vertex AI LLM provider with Assured Workloads | LLMRequest | LLMResponse |
| OCI GenAI Provider | tools/llm/oci_genai_provider.py | Oracle OCI Generative AI LLM provider | LLMRequest | LLMResponse |
| IBM watsonx.ai Provider | tools/llm/ibm_watsonx_provider.py | IBM watsonx.ai LLM provider — Granite, Llama models via watsonx.ai SDK (D238). | LLMRequest | LLMResponse |
| Terraform Generator Azure | tools/infra/terraform_generator_azure.py | Azure Government Terraform (VNet, AKS, Azure PG, Blob, Key Vault) | --project-path, --json | .tf files |
| Terraform Generator GCP | tools/infra/terraform_generator_gcp.py | GCP Government Terraform (VPC, GKE, Cloud SQL, GCS, Secret Manager) | --project-path, --json | .tf files |
| Terraform Generator OCI | tools/infra/terraform_generator_oci.py | OCI Government Terraform (VCN, OKE, Autonomous DB, Object Storage, Vault) | --project-path, --json | .tf files |
| Terraform Generator IBM | tools/infra/terraform_generator_ibm.py | IBM Cloud Terraform generator — VPC, IKS, PostgreSQL, COS, Key Protect with CUI headers. | --project-id, --region, --json | .tf files |
| Terraform Generator On-Prem | tools/infra/terraform_generator_onprem.py | On-premises Terraform generator — self-managed K8s, Docker Compose, local PostgreSQL. | --project-id, --target k8s\|docker, --json | .tf / docker-compose files |

| AWS Emulator Seam | tools/cloud/emulator.py | The ONE AWS-emulator (floci) switch — enabled/endpoint/region/account_id/credentials/docker_backed/status; `degraded_no_docker` so a container-backed table says `unsupported_without_docker` and never `[]`. LOCALSTACK_* honoured as deprecated aliases (flx-seam-01) | (library) enabled() / endpoint() / region() / account_id() / credentials() / docker_backed() / service_supported() / status() | bool / str / tuple / Optional[bool] |
