# CUI // SP-CTI

# Goal: Cloud-Agnostic Architecture

## Purpose

Enable ICDEV to deploy on any supported Cloud Service Provider (CSP) — or on-premises — using a single, unified abstraction layer. Cloud-specific details (endpoints, SDKs, regions, compliance certifications) are encapsulated behind provider ABCs so that the GOTCHA framework, compliance engine, and all tools operate identically regardless of the underlying infrastructure.

**Why this matters:** Government and DoD customers deploy on different cloud environments — AWS GovCloud, Azure Government, GCP Assured Workloads, OCI Government Cloud, IBM Cloud for Government (IC4G), or fully air-gapped on-premises enclaves. ICDEV must support all of these without duplicating business logic. A single `cloud_config.yaml` setting switches the entire stack.

---

## When to Use

- When deploying ICDEV to a new cloud environment
- When generating Terraform, Helm, or K8s manifests for a specific CSP
- When configuring LLM routing for multi-cloud AI providers
- When validating region compliance certifications before deployment
- When onboarding a new tenant with a specific CSP requirement (Phase 21 multi-tenancy)
- When switching between government and commercial cloud modes
- When operating in air-gapped or on-premises environments

---

## Prerequisites

- [ ] Cloud configuration: `args/cloud_config.yaml` (CSP selection, cloud mode, region, per-service overrides)
- [ ] CSP certifications registry: `context/compliance/csp_certifications.json`
- [ ] LLM configuration: `args/llm_config.yaml` (multi-cloud LLM providers)
- [ ] ICDEV database initialized (`python tools/db/init_icdev_db.py`)
- [ ] CSP-specific SDK installed for target cloud (optional — graceful degradation per D231)

---

## Workflow

### Step 1: Configure Cloud Mode

Select the deployment cloud mode based on impact level and operational environment. The cloud mode drives endpoint selection, FIPS settings, and feature availability across all CSP providers.

**Tool:** `tools/cloud/cloud_mode_manager.py`

**Cloud modes (D232):**

| Mode | Description | Internet | FIPS | Impact Levels |
|------|-------------|----------|------|---------------|
| commercial | Standard commercial cloud regions | Required | No | IL2 |
| government | Government cloud with FedRAMP authorization | Required | Yes | IL2, IL4, IL5 |
| on_prem | On-premises, optional cloud services | Optional | Yes | IL2–IL6 |
| air_gapped | Fully air-gapped, no internet | No | Yes | IL2–IL6 |

**CLI:**
```bash
python tools/cloud/cloud_mode_manager.py --status --json     # Current mode and config
python tools/cloud/cloud_mode_manager.py --validate --json   # Validate against constraints
python tools/cloud/cloud_mode_manager.py --eligible --json   # List eligible modes
python tools/cloud/cloud_mode_manager.py --check-readiness   # Probe cloud services
```

**Output:** Validation results with errors (blocking) and warnings (advisory).

**Error handling:**
- Invalid cloud_mode → report error with valid options
- CSP does not support mode → report with supported modes for that CSP
- IL incompatible with mode → report with allowed modes for that IL

---

### Step 2: Resolve Cloud Providers

The CSP Provider Factory (D225) resolves the correct implementation for each cloud service based on configuration. It supports per-service CSP overrides — e.g., use AWS for secrets but Azure for storage.

**Tool:** `tools/cloud/provider_factory.py`

**Service ABCs (6 services × 6 CSPs = 36 implementations):**

| Service | ABC | AWS | Azure | GCP | OCI | IBM | Local |
|---------|-----|-----|-------|-----|-----|-----|-------|
| Secrets | `SecretsProvider` | Secrets Manager | Key Vault | Secret Manager | Vault | Secrets Manager | .env file |
| Storage | `StorageProvider` | S3 | Blob Storage | GCS | Object Storage | Cloud Object Storage | Local filesystem |
| KMS | `KMSProvider` | KMS | Key Vault | Cloud KMS | Key Management | Key Protect | Fernet |
| Monitoring | `MonitoringProvider` | CloudWatch | Azure Monitor | Cloud Monitoring | Monitoring | LogDNA/Sysdig | Local log |
| IAM | `IAMProvider` | IAM | Entra ID | Cloud IAM | Identity | IAM | Local RBAC |
| Registry | `RegistryProvider` | ECR | ACR | Artifact Registry | OCIR | Container Registry | Docker |

**Per-service override (D225):** Set `ICDEV_SECRETS_PROVIDER=azure` to use Azure Key Vault for secrets while all other services use the global provider.

**CLI:** `python -c "from tools.cloud.provider_factory import CSPProviderFactory; f = CSPProviderFactory(); print(f.health_check())"`

**Error handling:**
- CSP SDK not installed → graceful degradation (D231), fall back to local provider
- Config file missing → default to local provider with warning

---

### Step 3: Validate Deployment Region

Before deploying, validate that the target CSP region holds all required compliance certifications for the project's impact level.

**Tool:** `tools/cloud/region_validator.py`

**Certification requirements by IL:**

| Impact Level | Required Certifications |
|-------------|------------------------|
| IL2 | FedRAMP Moderate |
| IL4 | FedRAMP Moderate, FIPS 140-2 |
| IL5 | FedRAMP High, FIPS 140-2, DoD IL5 |
| IL6 | FedRAMP High, FIPS 140-2, DoD IL6 |

**Catalog:** `context/compliance/csp_certifications.json` — declarative registry of region-level certifications per CSP (D233).

**CLI:**
```bash
python tools/cloud/region_validator.py --validate --csp aws --region us-gov-west-1 --required fedramp_high --json
python tools/cloud/region_validator.py --eligible --csp azure --il IL5 --json
python tools/cloud/region_validator.py --deployment-check --json
```

**Output:** Validation result with missing certifications.

**Error handling:**
- Region not in certifications catalog → reject deployment, suggest certified alternatives
- Certifications file missing → warn, allow with manual override

---

### Step 4: Generate CSP-Specific IaC

Generate Terraform modules tailored to the target CSP. Each generator produces compliant infrastructure matching the CSP's government or commercial region requirements.

**Tools:**

| Generator | Target CSP | Key Resources |
|-----------|-----------|---------------|
| `tools/infra/terraform_generator.py` | AWS GovCloud | VPC, EKS, RDS, ECR, Secrets Manager |
| `tools/infra/terraform_generator_azure.py` | Azure Government | VNet, AKS, Azure SQL, ACR, Key Vault |
| `tools/infra/terraform_generator_gcp.py` | GCP Assured Workloads | VPC, GKE, Cloud SQL, Artifact Registry |
| `tools/infra/terraform_generator_oci.py` | OCI Government | VCN, OKE, Autonomous DB, OCIR, Vault |
| `tools/infra/terraform_generator_ibm.py` | IBM Cloud (IC4G) | VPC, IKS, Databases for PostgreSQL, ICR |
| `tools/infra/terraform_generator_onprem.py` | On-premises | Docker Compose, self-managed K8s |

**Auto-dispatch (D227):** The Terraform dispatcher reads `cloud_config.yaml` or `ICDEV_CLOUD_PROVIDER` env var and delegates to the appropriate CSP-specific generator.

**CLI:**
```bash
python tools/infra/terraform_generator.py --project-id "proj-123"                    # AWS (default)
python tools/infra/terraform_generator_azure.py --project-id "proj-123" --json       # Azure
python tools/infra/terraform_generator_gcp.py --project-id "proj-123" --json         # GCP
python tools/infra/terraform_generator_oci.py --project-id "proj-123" --json         # OCI
python tools/infra/terraform_generator_ibm.py --project-id "proj-123" --json         # IBM
python tools/infra/terraform_generator_onprem.py --project-id "proj-123" --json      # On-prem
```

**Output:** Terraform `.tf` files with CSP-specific resources, FIPS endpoints, government regions, and compliance tags.

**Error handling:**
- CSP SDK not available → generate Terraform that uses CLI authentication instead
- Region not certified → block generation, suggest alternative region

---

### Step 5: Configure Multi-Cloud LLM Routing

Route LLM calls to the appropriate cloud AI service based on provider configuration. Each CSP has its own LLM service with government and commercial endpoint variants.

**Tool:** `tools/llm/router.py` (LLMRouter)

**LLM providers (D228):**

| CSP | LLM Service | Government Endpoint | Models |
|-----|------------|--------------------| -------|
| AWS | Amazon Bedrock | bedrock.us-gov-west-1.amazonaws.com | Claude, Titan, Llama |
| Azure | Azure OpenAI | *.openai.azure.us | GPT-4, GPT-4o |
| GCP | Vertex AI | us-*-aiplatform.googleapis.com | Gemini, Claude-via-Vertex |
| OCI | OCI GenAI | genai.*.oci.oraclecloud.com | Cohere, Llama |
| IBM | watsonx.ai | watsonx.*.cloud.ibm.com | Granite, Llama, Slate (embed) |
| Local | Ollama | localhost:11434 | Any local model |

**Fallback chains (D37):** Configurable per CSP and cloud mode. Air-gapped deployments set `prefer_local: true` — chains end with local Ollama models.

**CLI:** `python -c "from tools.llm.router import LLMRouter; r = LLMRouter(); print(r.get_provider_for_function('code_generation'))"`

**Error handling:**
- Primary LLM provider unavailable → fall through to next in fallback chain
- All cloud providers down → fall back to Ollama local if available

---

### Step 6: Generate Helm Value Overlays

Generate CSP-specific Helm value files for K8s deployment.

**Overlays (D229):**

| File | CSP | Key Settings |
|------|-----|-------------|
| `deploy/helm/values-aws.yaml` | AWS GovCloud | ECR registry, RDS endpoints, KMS ARN |
| `deploy/helm/values-azure.yaml` | Azure Government | ACR registry, Azure SQL, Key Vault URI |
| `deploy/helm/values-gcp.yaml` | GCP | Artifact Registry, Cloud SQL, KMS key |
| `deploy/helm/values-oci.yaml` | OCI | OCIR registry, Autonomous DB, Vault OCID |
| `deploy/helm/values-ibm.yaml` | IBM Cloud | ICR registry, PostgreSQL, Key Protect |
| `deploy/helm/values-on-prem.yaml` | On-prem | Local registry, local DB, Fernet |
| `deploy/helm/values-docker.yaml` | Docker Compose | Local development configuration |

**CLI:** `helm install icdev deploy/helm/ -f deploy/helm/values-<csp>.yaml`

---

### Step 7: Monitor Cloud Service Health

Continuously monitor all configured cloud services and detect CSP-level changes (API deprecations, new regions, compliance certification updates).

**Tools:**
- `tools/cloud/csp_health_checker.py` — Probe all CSP services, store in `cloud_provider_status` table (D230)
- `tools/cloud/csp_monitor.py` — Monitor CSP service changes via RSS/API feeds (D239)
- `tools/cloud/csp_changelog.py` — Generate changelog of CSP service changes

**CLI:**
```bash
python tools/cloud/csp_health_checker.py --check-all --json              # Health check all services
python tools/cloud/csp_health_checker.py --check-service secrets --json  # Check specific service
python tools/cloud/csp_health_checker.py --history --hours 24 --json     # Health history
python tools/cloud/csp_monitor.py --scan --all --json                    # Monitor CSP changes
python tools/cloud/csp_changelog.py --generate --json                    # Generate changelog
```

**Integration:** CSP monitor feeds into Phase 35 Innovation Engine as an innovation signal source (D239).

**Error handling:**
- CSP service unreachable → mark as unhealthy, alert, do not block other services
- Health check timeout → retry once, then mark degraded

---

## Outputs

- CSP provider instances (Secrets, Storage, KMS, Monitoring, IAM, Registry)
- Cloud mode validation results
- Region certification validation results
- CSP-specific Terraform modules (`.tf` files)
- Helm value overlays (`values-<csp>.yaml`)
- Health check records (`cloud_provider_status` table)
- CSP change signals (Innovation Engine integration)
- LLM routing configuration (per-function provider resolution)

---

## Error Handling

- If CSP SDK is not installed: degrade gracefully to local provider (D231), warn in logs
- If cloud_config.yaml is missing: default to local provider, warn
- If region lacks required certifications: block deployment, suggest certified alternatives
- If LLM provider is unavailable: fall through fallback chain (D37)
- If health check fails: mark service unhealthy, continue with other services
- If cloud mode is incompatible with impact level: error with valid mode options

---

## Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D225 | CSP abstraction uses ABC + 6 implementations per service | Interface + adapters isolate vendor logic; consistent with D66 provider pattern |
| D226 | Multi-cloud Terraform generators produce CSP-specific IaC | Each CSP has unique resource types and naming; shared abstraction isn't possible for IaC |
| D227 | Terraform dispatcher auto-detects CSP from config/env | Single entry point, automatic routing based on cloud_config.yaml |
| D228 | LLM multi-cloud: Azure OpenAI, Vertex AI, OCI GenAI, IBM watsonx.ai | Best-of-breed LLM per CSP; fallback chains ensure availability |
| D229 | Helm value overlays per CSP | Helm values are the standard K8s configuration mechanism; per-CSP overrides compose cleanly |
| D230 | CSP health checker stores status in cloud_provider_status table | Enables trend analysis and alerting for cloud service degradation |
| D231 | CSP SDKs are optional dependencies | Only install SDK for target CSP; avoid bloated requirements.txt |
| D232 | cloud_mode controls endpoint selection per CSP | Single config field drives government/commercial/on-prem/air-gapped behavior |
| D233 | CSP certifications as declarative JSON catalog | Consistent with D26 pattern; human-maintained, machine-validated |
| D234 | Region validator blocks uncertified deployments | Prevents accidental deployment to non-compliant regions |
| D236 | On-prem Terraform targets Docker Compose and self-managed K8s | No cloud provider block required; works fully offline |
| D237 | IBM Cloud follows D66 ABC pattern with IBM SDKs | Consistent provider architecture; IBM COS uses S3-compatible ibm_boto3 |
| D238 | IBM watsonx.ai LLM via ibm-watsonx-ai SDK | Granite + Llama model families; Slate for embeddings |
| D239 | CSP monitor feeds into Innovation Engine | Reuses Phase 35 signal scoring and triage pipeline for CSP changes |

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| Cloud Mode Selection | Tools | `cloud_mode_manager.py` |
| Provider Resolution | Tools | `provider_factory.py` |
| Region Validation | Tools | `region_validator.py` |
| Terraform Generation | Tools | `terraform_generator_*.py` |
| LLM Routing | Tools | `router.py` (LLMRouter) |
| Health Monitoring | Tools | `csp_health_checker.py`, `csp_monitor.py` |
| Cloud configuration | Args | `args/cloud_config.yaml` |
| LLM configuration | Args | `args/llm_config.yaml` |
| CSP monitor configuration | Args | `args/csp_monitor_config.yaml` |
| Region certifications | Context | `context/compliance/csp_certifications.json` |
| CSP MCP registry | Context | `context/agentic/csp_mcp_registry.yaml` |

---

## Related Files

- **Goals:** `goals/deploy_workflow.md` (deployment pipeline), `goals/modular_installation.md` (installer cloud mode), `goals/saas_multi_tenancy.md` (per-tenant CSP)
- **Tools:** `tools/cloud/` (provider factory, ABCs, health checker, region validator, monitor), `tools/infra/` (Terraform generators), `tools/llm/` (multi-cloud LLM router)
- **Args:** `args/cloud_config.yaml`, `args/llm_config.yaml`, `args/csp_monitor_config.yaml`
- **Context:** `context/compliance/csp_certifications.json`, `context/agentic/csp_mcp_registry.yaml`
- **Helm:** `deploy/helm/values-*.yaml` (per-CSP overlays)
- **Tests:** `tests/test_ibm_providers.py`, `tests/test_cloud_providers.py`

---

## Changelog

- 2026-02-21: Initial creation — Cloud-Agnostic Architecture goal with 7-step workflow (cloud mode, provider resolution, region validation, IaC generation, LLM routing, Helm overlays, health monitoring), architecture decisions D225-D239
