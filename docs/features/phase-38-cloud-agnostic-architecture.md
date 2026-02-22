# Phase 38 — Cloud-Agnostic Architecture

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 38 |
| Title | Cloud-Agnostic Multi-Cloud & On-Premises Architecture |
| Status | Requirements |
| Priority | P1 |
| Dependencies | Phase 21 (SaaS Multi-Tenancy), Phase 23 (Universal Compliance), Phase 24 (DevSecOps), Phase 25 (ZTA) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-21 |

---

## 1. Problem Statement

ICDEV is currently hardcoded to AWS GovCloud throughout its codebase, configuration, documentation, and architecture. References to "AWS GovCloud", "Bedrock", "AWS Secrets Manager", "EKS", "S3", "RDS", and other AWS-specific services appear in:

- `args/agent_config.yaml` — Bedrock model references
- `tools/agent/bedrock_client.py` — AWS-specific LLM client
- `tools/llm/router.py` — Routes through Bedrock
- `tools/infra/terraform_generator.py` — AWS-specific IaC
- `tools/infra/ansible_generator.py` — AWS-specific playbooks
- `k8s/` manifests — EKS assumptions
- `CLAUDE.md` — Multiple AWS GovCloud references
- `args/` configuration files — AWS service references

ICDEV's customer base spans **government, commercial, and international markets**. Customers deploy across all five major CSPs in both government and commercial regions, as well as on-premises air-gapped environments. A platform locked to AWS GovCloud cannot serve:

- **Government customers** on Azure Government, Oracle Cloud Infrastructure Government, IBM Cloud for Government, or Google Cloud for Government
- **Commercial SaaS vendors** building FedRAMP or SOC 2 compliant products on commercial cloud regions
- **Healthcare organizations** requiring HIPAA/HITRUST compliance on any CSP
- **Financial services** requiring PCI DSS/SOC 2 compliance on any CSP
- **International organizations** requiring ISO 27001, BSI C5, IRAP, or regional frameworks
- **On-premises customers** with air-gapped, no-internet deployments (SIPR, classified, or policy-driven)

ICDEV already supports **20+ compliance frameworks** (Phase 23) across sectors, **10 deployment profiles** (Phase 33) from ISV startups to GovCloud Full, and **6 programming languages**. The cloud abstraction must match the breadth of the compliance and deployment architecture.

---

## 2. Cloud & Deployment Landscape

### 2.1 Government Cloud Providers

| CSP | Government Cloud | FedRAMP | Impact Levels | Regions |
|-----|-----------------|---------|---------------|---------|
| **AWS** | AWS GovCloud | High | IL2–IL5 (IL6 via C2S/SC2S) | us-gov-west-1, us-gov-east-1 |
| **Azure** | Azure Government | High | IL2–IL5 (IL6 via Azure Gov Secret/Top Secret) | USGov Virginia, USGov Arizona, USGov Texas, USDoD Central, USDoD East |
| **Google** | Google Cloud for Government | High | IL2–IL5 (via Assured Workloads) | us-central1, us-east4 (Assured Workloads) |
| **Oracle** | OCI Government Cloud | High | IL2–IL5 (IL6 via DISA authorization) | US Gov Chicago, US Gov Phoenix, US DoD regions |
| **IBM** | IBM Cloud for Government (IC4G) | High | IL2–IL5 | Dedicated federal data centers (Colorado, North Carolina) |

### 2.2 Commercial Cloud Providers

Each CSP offers commercial regions with compliance certifications applicable to non-government workloads:

| CSP | Commercial Cloud | Key Certifications | Regions (Examples) |
|-----|-----------------|-------------------|-------------------|
| **AWS** | AWS Commercial | SOC 1/2/3, ISO 27001/27017/27018, PCI DSS, HIPAA, HITRUST, CSA STAR, GxP | us-east-1, us-west-2, eu-west-1, ap-southeast-1, etc. |
| **Azure** | Azure Commercial | SOC 1/2/3, ISO 27001/27017/27018, PCI DSS, HIPAA, HITRUST, CSA STAR, TISAX | East US, West Europe, Southeast Asia, etc. |
| **Google** | Google Cloud | SOC 1/2/3, ISO 27001/27017/27018, PCI DSS, HIPAA, HITRUST, CSA STAR | us-central1, europe-west1, asia-east1, etc. |
| **Oracle** | OCI Commercial | SOC 1/2/3, ISO 27001, PCI DSS, HIPAA, CSA STAR | us-ashburn-1, eu-frankfurt-1, ap-tokyo-1, etc. |
| **IBM** | IBM Cloud | SOC 1/2/3, ISO 27001/27017/27018, PCI DSS, HIPAA, HITRUST, CSA STAR | Dallas, Washington DC, Frankfurt, London, Tokyo, Sydney, etc. |

### 2.3 On-Premises & Air-Gapped Deployments

| Mode | Description | LLM Provider | Infrastructure |
|------|-------------|-------------|----------------|
| **On-Prem (Connected)** | Customer data center with internet access | Ollama (local) or remote API | Docker Compose, self-managed K8s, OpenShift |
| **On-Prem (Air-Gapped)** | No internet access — classified or policy-driven | Ollama (local models only) | Docker Compose, pre-loaded images, offline installer |
| **Hybrid** | On-prem compute with cloud-hosted LLM | Cloud LLM (Bedrock, Azure OpenAI, etc.) | Customer K8s + cloud API endpoints |
| **Edge / Tactical** | Deployed to forward environments with intermittent connectivity | Ollama (local) + sync-when-connected | Minimal container runtime |

### 2.4 AI/ML Services by CSP

| Capability | AWS | Azure | Google | Oracle | IBM | Local |
|-----------|-----|-------|--------|--------|-----|-------|
| **LLM/AI Service** | Amazon Bedrock | Azure OpenAI Service | Vertex AI | OCI Generative AI | watsonx.ai | Ollama |
| **Embedding Service** | Bedrock (Titan) | Azure OpenAI (ada-002) | Vertex AI (textembedding) | OCI GenAI (cohere.embed) | watsonx.ai (Slate) | Ollama (nomic-embed-text) |
| **Available Models** | Claude, Llama, Titan, Mistral | GPT-4o, GPT-4, o1, o3 | Gemini, PaLM, Claude | Cohere, Llama, Meta | Granite, Llama, Mistral | Any GGUF model |
| **Gov Cloud AI** | Bedrock in GovCloud | Azure OpenAI in AzGov | Vertex in Assured Workloads | OCI GenAI in Gov regions | watsonx on AWS GovCloud (FedRAMP) | N/A |
| **Commercial AI** | Bedrock in any region | Azure OpenAI in any region | Vertex AI in any region | OCI GenAI in any region | watsonx.ai (Dallas, Frankfurt) | N/A |
| **Air-Gap LLM** | Bedrock (isolated VPC) | Azure OpenAI (private endpoint) | Vertex (VPC-SC) | OCI GenAI (private endpoint) | watsonx (private endpoint) | Ollama (fully offline) |

### 2.5 Infrastructure Services by CSP

| Service | AWS | Azure | Google | Oracle | IBM | Local |
|---------|-----|-------|--------|--------|-----|-------|
| **Secrets Management** | Secrets Manager | Key Vault | Secret Manager | OCI Vault | Secrets Manager | .env / OS keyring |
| **Object Storage** | S3 | Blob Storage | Cloud Storage | Object Storage | Cloud Object Storage | Local filesystem |
| **Container Orchestration** | EKS | AKS | GKE | OKE | IKS / OpenShift | Docker / self-managed K8s |
| **Managed Database** | RDS (PostgreSQL) | Azure DB for PostgreSQL | Cloud SQL (PostgreSQL) | Autonomous Database | Databases for PostgreSQL | SQLite / local PostgreSQL |
| **IAM** | IAM / STS | Entra ID (Azure AD) | Cloud IAM | OCI IAM | IBM Cloud IAM | Local user database |
| **Monitoring** | CloudWatch | Azure Monitor | Cloud Monitoring | OCI Monitoring | IBM Cloud Monitoring | Prometheus + Grafana + ELK |
| **Key Management** | KMS | Azure Key Vault | Cloud KMS | OCI Key Management | Key Protect / HPCS | Local Fernet keys |
| **Container Registry** | ECR | ACR | Artifact Registry | OCIR | IBM Container Registry | Local Docker registry |
| **Load Balancer** | ALB / NLB | Azure LB / App Gateway | Cloud Load Balancing | OCI LB | IBM Cloud LB | nginx / HAProxy |
| **DNS** | Route 53 | Azure DNS | Cloud DNS | OCI DNS | IBM CIS (DNS) | CoreDNS / local DNS |
| **VPN / Private Network** | VPC / Transit Gateway | VNet / ExpressRoute | VPC / Cloud Interconnect | VCN / FastConnect | VPC / Direct Link | Physical network |
| **Certificate Management** | ACM | Azure App Service Certs | Certificate Manager | OCI Certificates | Certificate Manager | Let's Encrypt / self-signed |
| **SIEM / Logging** | CloudTrail + CloudWatch | Azure Sentinel | Security Command Center | OCI Logging Analytics | QRadar / Activity Tracker | ELK + Splunk |

---

## 3. Compliance Landscape

### 3.1 ICDEV-Supported Compliance Frameworks (Phase 23)

ICDEV already supports **20+ compliance frameworks** through the Universal Compliance Platform (Phase 23). The cloud abstraction layer must ensure each framework operates identically regardless of CSP or deployment mode.

#### Active Frameworks (18)

| Framework | Hub | Sector | Markets |
|-----------|-----|--------|---------|
| **NIST SP 800-53 Rev 5** | NIST | Universal | All |
| **FedRAMP Moderate** | NIST | Government | US Federal |
| **FedRAMP High** | NIST | Government | US Federal (IL4+) |
| **NIST SP 800-171 Rev 2** | NIST | Defense Industrial Base | DIB contractors |
| **CMMC Level 2/3** | NIST | Defense | DIB certification |
| **DoD CSSP (DI 8530.01)** | NIST | Defense | DoD operations |
| **CISA Secure by Design** | NIST | Software Dev | All software |
| **IEEE 1012 IV&V** | NIST | Verification | Defense/critical systems |
| **FIPS 199/200** | NIST | Categorization | All federal |
| **CNSSI 1253** | NIST | Classified | IL6/SECRET |
| **NIST SP 800-207 (ZTA)** | NIST | Architecture | Zero Trust |
| **DoD MOSA** | NIST | Architecture | Defense |
| **FBI CJIS Security Policy** | NIST | Law Enforcement | Criminal justice |
| **HIPAA Security Rule** | NIST | Healthcare | PHI handlers |
| **PCI DSS v4.0** | NIST | Financial | Payment processing |
| **HITRUST CSF v11** | NIST | Healthcare | Healthcare cert |
| **SOC 2 Type II** | NIST | Commercial | SaaS/cloud services |
| **ISO/IEC 27001:2022** | ISO | International | Global |

#### Planned Frameworks (Wave 2-3, 12 additional)

| Framework | Hub | Sector | Phase |
|-----------|-----|--------|-------|
| **ISO/IEC 27017:2015** (Cloud Security) | ISO | Cloud | 24 |
| **ISO/IEC 27018:2019** (Cloud PII) | ISO | Cloud/Privacy | 24 |
| **ISO/IEC 27701:2019** (Privacy) | ISO | Privacy | 24 |
| **Australian IRAP** | ISO | Regional (Australia) | 24 |
| **BSI C5** (Germany) | ISO | Regional (EU) | 24 |
| **UK Cyber Essentials Plus** | ISO | Regional (UK) | 24 |
| **IRS Publication 1075** | NIST | Tax | 25 |
| **TISAX** (Automotive) | ISO | Automotive | 25 |
| **K-ISMS** (Korea) | ISO | Regional (Korea) | 25 |
| **ENS** (Spain) | ISO | Regional (Spain) | 25 |
| **ISO/IEC 42001** (AI Management) | ISO | AI Governance | 25 |
| **SOC 1 Type II** | NIST | Financial/Audit | 25 |

### 3.2 CSP Compliance Program Coverage

Each CSP maintains its own compliance certifications. ICDEV must validate that the selected CSP region holds the required certifications for the tenant's compliance posture.

| Compliance Program | AWS | Azure | GCP | OCI | IBM | On-Prem |
|-------------------|-----|-------|-----|-----|-----|---------|
| **FedRAMP High** | GovCloud | Azure Gov | Assured Workloads | OCI Gov | IC4G | N/A (customer-managed) |
| **DoD IL2–IL5** | GovCloud | Azure Gov | Assured Workloads | OCI Gov | IC4G (IL2–IL5) | Customer ATO |
| **DoD IL6** | C2S/SC2S | Azure Gov Secret | Not available | OCI DoD | Not available | Customer SIPR |
| **SOC 1/2/3** | All regions | All regions | All regions | All regions | All regions | Customer audit |
| **ISO 27001** | All regions | All regions | All regions | All regions | All regions | Customer cert |
| **ISO 27017/27018** | All regions | All regions | All regions | All regions | All regions | N/A |
| **PCI DSS** | All regions | All regions | All regions | All regions | All regions | Customer PCI audit |
| **HIPAA** | All regions (BAA) | All regions (BAA) | All regions (BAA) | All regions (BAA) | All regions (BAA) | Customer responsibility |
| **HITRUST** | All regions | All regions | All regions | All regions | All regions | Customer cert |
| **CSA STAR** | All regions | All regions | All regions | All regions | All regions | N/A |
| **CJIS** | GovCloud | Azure Gov | Assured Workloads | OCI Gov | IC4G | Customer CJIS audit |
| **FIPS 140-2** | FIPS endpoints | Gov FIPS endpoints | BoringCrypto | OCI FIPS modules | Key Protect (FIPS L3) | Customer HSMs |
| **GxP** (Life Sciences) | All regions | All regions | All regions | All regions | All regions | Customer validation |
| **TISAX** (Automotive) | EU regions | EU regions | EU regions | EU regions | EU regions | Customer cert |
| **BSI C5** (Germany) | EU (Frankfurt) | EU (Germany) | EU (Frankfurt) | EU regions | EU (Frankfurt) | Customer audit |
| **IRAP** (Australia) | ap-southeast-2 | Australia | Australia | Australia | Sydney | Customer assessment |
| **K-ISMS** (Korea) | ap-northeast-2 | Korea | Asia | Asia | Asia | Customer cert |
| **ISO 42001** (AI) | Bedrock regions | Azure OpenAI regions | Vertex AI regions | OCI GenAI regions | watsonx regions | Customer responsibility |

### 3.3 Deployment Profile → Cloud Mapping

Each ICDEV deployment profile (Phase 33) maps to supported cloud modes:

| Profile | Gov Cloud | Commercial Cloud | On-Prem | Air-Gap | Typical CSPs |
|---------|-----------|-----------------|---------|---------|-------------|
| **ISV Startup** | — | Yes | Yes | — | Any commercial, Docker |
| **ISV Enterprise** | Optional | Yes | — | — | AWS/Azure/GCP commercial |
| **SI Consulting** | Yes | Yes | Yes | Optional | Any |
| **SI Enterprise** | Yes | Yes | — | Optional | Any |
| **DoD Team** | Yes | — | — | Optional | AWS GovCloud, Azure Gov, OCI Gov, IBM IC4G |
| **Healthcare** | Optional | Yes | Yes | — | Any (with BAA) |
| **Financial** | — | Yes | Yes | — | Any (PCI compliant regions) |
| **Law Enforcement** | Yes | — | Yes | Yes | GovCloud or on-prem |
| **GovCloud Full** | Yes | Yes | Yes | Yes | All |

---

## 4. Goals

1. Abstract all cloud-provider-specific code behind a **CSP Abstraction Layer** using the existing D66 provider pattern (ABC + implementations)
2. Support **all five CSPs (AWS, Azure, GCP, Oracle, IBM) in both government and commercial cloud regions** as first-class deployment targets
3. Support **on-premises and air-gapped** deployments with no cloud dependencies
4. Enable **multi-cloud deployments** where different tenants can be hosted on different CSPs, cloud modes, or on-premises
5. Generate **CSP-specific IaC** (Terraform modules per CSP) from a single abstract specification
6. Maintain **air-gap compatibility** across all deployment modes
7. Ensure **compliance equivalence** — same security posture regardless of CSP or deployment mode
8. Support **all 20+ ICDEV compliance frameworks** (Phase 23) across every deployment mode — not limited to FedRAMP/DoD
9. Enable **compliance-driven CSP region validation** — system validates that the selected CSP region holds required certifications for the tenant's compliance posture
10. Support **commercial SaaS deployments** for ISV, healthcare, financial, and international customers without requiring government cloud regions

---

## 5. Architecture

### 5.1 CSP Abstraction Layer

```
+--------------------------------------------------------------------+
|                        ICDEV Platform                               |
|               (cloud-agnostic application logic)                    |
+--------------------------------------------------------------------+
|                    CSP Abstraction Layer                            |
| +-------+ +-------+ +-------+ +-------+ +-------+ +-----------+   |
| |  AWS  | | Azure | |Google | |Oracle | |  IBM  | |   Local   |   |
| |  Gov/ | |  Gov/ | |  Gov/ | |  Gov/ | |  Gov/ | | On-Prem / |   |
| | Comm  | | Comm  | | Comm  | | Comm  | | Comm  | | Air-Gap   |   |
| +-------+ +-------+ +-------+ +-------+ +-------+ +-----------+   |
+--------------------------------------------------------------------+
|  Services Abstracted:                                  |
|  - LLM/AI (llm_provider.py)                 EXISTING  |
|  - Embeddings (embedding_provider.py)        EXISTING  |
|  - Secrets (secrets_provider.py)                 NEW   |
|  - Storage (storage_provider.py)                 NEW   |
|  - Container Registry (registry_provider.py)     NEW   |
|  - Monitoring (monitoring_provider.py)           NEW   |
|  - Key Management (kms_provider.py)              NEW   |
|  - IAM (iam_provider.py)                         NEW   |
|  - IaC Generation (iac_provider.py)              NEW   |
+-------------------------------------------------------+
|  Compliance Layer (Phase 23 — cloud-independent):      |
|  - 20+ frameworks via dual-hub crosswalk               |
|  - Auto-detection from data categories                 |
|  - Multi-regime gate enforcement                       |
+-------------------------------------------------------+
```

### 5.2 Provider Pattern (Extending D66)

Each abstracted service follows the existing provider pattern:

```python
# Abstract base (ABC)
class SecretsProvider(ABC):
    @abstractmethod
    def get_secret(self, name: str) -> str: ...
    @abstractmethod
    def put_secret(self, name: str, value: str) -> None: ...
    @abstractmethod
    def list_secrets(self, prefix: str) -> list: ...

# AWS implementation (works for both GovCloud and commercial)
class AWSSecretsProvider(SecretsProvider):
    def get_secret(self, name): ...  # boto3 secretsmanager

# Azure implementation (works for both AzureGovernment and AzureCloud)
class AzureSecretsProvider(SecretsProvider):
    def get_secret(self, name): ...  # azure-keyvault-secrets

# Google implementation (works for both Assured Workloads and commercial)
class GCPSecretsProvider(SecretsProvider):
    def get_secret(self, name): ...  # google-cloud-secret-manager

# Oracle implementation (works for both OCI Gov and commercial)
class OCISecretsProvider(SecretsProvider):
    def get_secret(self, name): ...  # oci-sdk

# IBM implementation (works for both IC4G and commercial)
class IBMSecretsProvider(SecretsProvider):
    def get_secret(self, name): ...  # ibm-cloud-sdk-core + ibm-secrets-manager-sdk

# Local/Air-gap implementation
class LocalSecretsProvider(SecretsProvider):
    def get_secret(self, name): ...  # .env file or OS keyring
```

### 5.3 Configuration-Driven CSP Selection

```yaml
# args/cloud_config.yaml
cloud:
  provider: aws          # aws | azure | gcp | oci | ibm | local
  region: us-east-1      # CSP-specific region
  cloud_mode: commercial # commercial | government | on_prem | air_gapped
  air_gapped: false

  # Compliance context (determines required CSP certifications)
  compliance_regimes: []   # Populated by compliance_detector.py
  # Example: [fedramp_high, hipaa, pci_dss] — system validates CSP region
  # has required certs before allowing deployment

  # Provider-specific overrides
  aws:
    account_type: commercial    # commercial | govcloud | c2s
    fips_endpoints: false       # true for IL4+ / CJIS / FTI
    bedrock_region: us-east-1

  azure:
    cloud: AzureCloud           # AzureCloud | AzureUSGovernment | AzureUSGovernmentSecret | AzureChinaCloud
    openai_endpoint: https://icdev.openai.azure.com/

  gcp:
    project_id: icdev-prod
    assured_workloads: false    # true for government workloads
    region: us-central1

  oci:
    tenancy_ocid: ocid1.tenancy.oc1...
    compartment_ocid: ocid1.compartment.oc1...
    region: us-ashburn-1

  ibm:
    api_key: ${IBM_CLOUD_API_KEY:-}
    region: us-south               # us-south (Dallas), us-east (Washington DC), eu-de (Frankfurt)
    resource_group: ${IBM_RESOURCE_GROUP:-default}
    cos_instance_id: ${IBM_COS_INSTANCE_ID:-}
    watsonx_project_id: ${IBM_WATSONX_PROJECT_ID:-}

  local:
    secrets_backend: env_file   # env_file | os_keyring | vault (HashiCorp)
    storage_backend: filesystem # filesystem | minio
    llm_backend: ollama         # ollama | vllm | none

  # Per-service CSP overrides (optional — defaults per provider)
  services:
    secrets: aws           # Override individual services
    storage: aws
    llm: azure             # Can mix: LLM from Azure, storage from AWS
    monitoring: local      # Local fallback
```

#### Example Configurations by Profile

```yaml
# ISV Startup — commercial AWS, SOC 2 compliance
cloud:
  provider: aws
  region: us-east-1
  cloud_mode: commercial
  aws:
    account_type: commercial
    fips_endpoints: false

# Healthcare — Azure commercial, HIPAA + HITRUST
cloud:
  provider: azure
  region: eastus
  cloud_mode: commercial
  azure:
    cloud: AzureCloud

# DoD Team — AWS GovCloud, FedRAMP High + CMMC
cloud:
  provider: aws
  region: us-gov-west-1
  cloud_mode: government
  aws:
    account_type: govcloud
    fips_endpoints: true

# Financial — GCP commercial, PCI DSS + SOC 2 + ISO 27001
cloud:
  provider: gcp
  region: us-central1
  cloud_mode: commercial
  gcp:
    assured_workloads: false

# Law Enforcement — on-prem air-gapped, CJIS
cloud:
  provider: local
  cloud_mode: air_gapped
  air_gapped: true
  local:
    llm_backend: ollama
    secrets_backend: os_keyring

# International — OCI commercial, ISO 27001 + BSI C5
cloud:
  provider: oci
  region: eu-frankfurt-1
  cloud_mode: commercial

# Federal Civilian — IBM Cloud for Government, FedRAMP High
cloud:
  provider: ibm
  region: us-east
  cloud_mode: government
  ibm:
    resource_group: icdev-fedramp
```

### 5.4 Per-Tenant CSP Assignment

In multi-tenant SaaS mode (Phase 21), each tenant can be assigned a different CSP and cloud mode:

```
Tenant A (DoD)             -> AWS GovCloud IL5
Tenant B (IC)              -> AWS C2S IL6
Tenant C (DoD)             -> Azure Government IL5
Tenant D (LEA)             -> OCI Government IL4
Tenant E (Healthcare ISV)  -> Azure Commercial (HIPAA BAA)
Tenant F (FinTech SaaS)    -> AWS Commercial (PCI DSS + SOC 2)
Tenant G (EU Enterprise)   -> GCP eu-west1 (ISO 27001 + BSI C5)
Tenant H (Automotive)      -> Azure EU (ISO 27001 + TISAX)
Tenant I (Startup)         -> AWS Commercial (SOC 2)
Tenant J (Federal Civilian) -> IBM Cloud for Government (FedRAMP High)
Tenant K (Air-Gapped)      -> Local (on-prem, no CSP)
```

### 5.5 IaC Generation Per CSP

The existing `tools/infra/terraform_generator.py` will be refactored to generate CSP-specific Terraform modules:

```
deploy/terraform/
  modules/
    aws/
      eks.tf, rds.tf, s3.tf, secrets.tf, kms.tf, vpc.tf, iam.tf
    azure/
      aks.tf, postgresql.tf, blob.tf, keyvault.tf, kms.tf, vnet.tf, entra.tf
    gcp/
      gke.tf, cloudsql.tf, gcs.tf, secretmanager.tf, kms.tf, vpc.tf, iam.tf
    oci/
      oke.tf, autonomous_db.tf, objectstorage.tf, vault.tf, kms.tf, vcn.tf, iam.tf
    ibm/
      iks.tf, postgresql.tf, cos.tf, secrets_manager.tf, key_protect.tf, vpc.tf, iam.tf
    common/
      network_policy.tf, monitoring.tf, k8s_base.tf
  environments/
    govcloud/main.tf        # AWS GovCloud (FedRAMP High, IL5)
    azgov/main.tf           # Azure Government (FedRAMP High, IL5)
    gcpgov/main.tf          # Google Assured Workloads (FedRAMP High)
    ocigov/main.tf          # OCI Government
    ibmgov/main.tf          # IBM Cloud for Government (IC4G)
    aws-commercial/main.tf  # AWS Commercial (SOC 2, PCI, HIPAA)
    azure-commercial/main.tf # Azure Commercial
    gcp-commercial/main.tf  # GCP Commercial
    oci-commercial/main.tf  # OCI Commercial
    ibm-commercial/main.tf  # IBM Cloud Commercial
    on-prem/main.tf         # On-premises (Docker/K8s, no cloud)
    airgap/main.tf          # Air-gapped (no CSP, offline)
```

---

## 6. Requirements

### 6.1 CSP Abstraction Layer

#### REQ-38-001: Provider Abstract Base Classes
The system SHALL define abstract base classes (ABCs) for each abstracted cloud service: Secrets, Storage, Container Registry, Monitoring, Key Management, IAM, and IaC Generation.

#### REQ-38-002: Five CSP Implementations
The system SHALL provide concrete implementations of each service ABC for: AWS, Azure, Google Cloud, Oracle Cloud Infrastructure, and IBM Cloud. Each implementation SHALL support both government and commercial cloud modes.

#### REQ-38-003: Local/Air-Gap Implementation
The system SHALL provide a local implementation of each service ABC that uses no cloud services, suitable for on-premises and air-gapped environments. This includes:
- Secrets: .env file, OS keyring, or HashiCorp Vault
- Storage: Local filesystem or MinIO
- Container Registry: Local Docker registry
- Monitoring: Local logging (existing ELK/Prometheus)
- Key Management: Local key files or Fernet encryption
- IAM: Local user database

#### REQ-38-004: Graceful SDK Degradation (D73 Pattern)
Each CSP provider SHALL handle missing SDKs gracefully. If `boto3` is not installed, the AWS provider SHALL raise a clear error at instantiation rather than import time. The system SHALL not require all five CSP SDKs simultaneously.

#### REQ-38-005: Configuration-Driven Selection
CSP selection SHALL be driven by `args/cloud_config.yaml` with support for:
- Global provider selection
- Cloud mode selection (commercial, government, on_prem, air_gapped)
- Per-service provider override (e.g., LLM from Azure, storage from AWS)
- Per-tenant CSP assignment in multi-tenant mode
- Environment variable overrides (ICDEV_CLOUD_PROVIDER, ICDEV_CLOUD_MODE, etc.)

#### REQ-38-006: Cloud Mode Awareness
Each CSP provider SHALL accept a `cloud_mode` parameter that configures region selection, endpoint URLs, and FIPS settings:
- **commercial**: Standard regions, standard endpoints
- **government**: Government regions (GovCloud, AzGov, Assured Workloads, OCI Gov), FIPS endpoints where available
- **on_prem**: No cloud APIs, local implementations only
- **air_gapped**: No internet, local implementations only, offline model inference

### 6.2 LLM Provider Abstraction (Extend Existing)

#### REQ-38-010: Extend LLM Router for All Clouds
The existing `tools/llm/router.py` and `tools/llm/embedding_provider.py` SHALL be extended to support:
- **AWS**: Amazon Bedrock (existing) — Claude, Llama, Titan, Mistral (GovCloud + commercial)
- **Azure**: Azure OpenAI Service — GPT-4o, GPT-4, o1, o3 (AzureCloud + AzureGovernment)
- **Google**: Vertex AI — Gemini, Claude (via Vertex) (commercial + Assured Workloads)
- **Oracle**: OCI Generative AI — Cohere, Llama (commercial + government)
- **IBM**: watsonx.ai — Granite, Llama, Mistral (commercial + IC4G via AWS GovCloud)
- **Local**: Ollama (existing) — any local model (on-prem + air-gapped)

#### REQ-38-011: Cloud-Mode-Aware LLM Endpoints
Each LLM provider SHALL support both government and commercial endpoints:
- AWS GovCloud: Bedrock in us-gov-west-1 (FIPS)
- AWS Commercial: Bedrock in us-east-1, us-west-2, etc.
- Azure Government: Azure OpenAI in *.openai.azure.us
- Azure Commercial: Azure OpenAI in *.openai.azure.com
- Google Assured Workloads: Vertex AI with VPC-SC
- Google Commercial: Vertex AI in any region
- Oracle Government: OCI GenAI in gov regions
- Oracle Commercial: OCI GenAI in any region
- IBM Government: watsonx on AWS GovCloud (FedRAMP authorized)
- IBM Commercial: watsonx.ai in Dallas, Frankfurt, etc.

#### REQ-38-012: LLM Fallback Chains Per CSP
The fallback chain (D37) SHALL be configurable per CSP and cloud mode:
```yaml
# Example: Azure commercial fallback chain
azure_commercial_fallback:
  - azure/gpt-4o
  - azure/gpt-4
  - ollama/codestral

# Example: On-prem air-gapped (no cloud LLM)
local_fallback:
  - ollama/llama3.1
  - ollama/codestral
  - ollama/mistral
```

### 6.3 Secrets Management Abstraction

#### REQ-38-020: Secrets Provider Interface
The system SHALL abstract all secret access behind a `SecretsProvider` interface with implementations for:
- AWS Secrets Manager (GovCloud + commercial)
- Azure Key Vault (AzGov + AzureCloud)
- Google Cloud Secret Manager (Assured Workloads + commercial)
- Oracle OCI Vault (government + commercial)
- IBM Cloud Secrets Manager (IC4G + commercial)
- Local: .env file, OS keyring, or HashiCorp Vault (on-prem / air-gap)

#### REQ-38-021: Secret Rotation Support
Each secrets provider SHALL support automated secret rotation where the CSP supports it (AWS, Azure, GCP all support this natively). Local providers SHALL support manual rotation with configurable reminders.

#### REQ-38-022: FIPS 140-2 Compliance
Secrets providers SHALL use FIPS 140-2 validated cryptographic modules when required by the tenant's compliance posture:
- **Always required**: IL4+, FedRAMP High, CJIS, FTI (IRS Pub 1075)
- **Recommended**: HIPAA, PCI DSS, HITRUST
- **Optional**: SOC 2, ISO 27001, commercial SaaS

### 6.4 Storage Abstraction

#### REQ-38-030: Storage Provider Interface
The system SHALL abstract all object storage behind a `StorageProvider` interface with implementations for:
- AWS S3 (GovCloud + commercial)
- Azure Blob Storage (AzGov + AzureCloud)
- Google Cloud Storage (Assured Workloads + commercial)
- Oracle Object Storage (government + commercial)
- IBM Cloud Object Storage (IC4G + commercial)
- Local filesystem or MinIO (on-prem / air-gap)

#### REQ-38-031: Artifact Delivery
The existing artifact delivery engine (`tools/saas/artifacts/delivery_engine.py`) SHALL use the storage abstraction for pushing compliance artifacts to tenant storage, regardless of CSP.

### 6.5 Container Orchestration

#### REQ-38-040: K8s Manifest Compatibility
All K8s manifests in `k8s/` SHALL be CSP-agnostic. CSP-specific configurations (storage classes, load balancer annotations, node selectors) SHALL be extracted into a per-CSP values overlay.

#### REQ-38-041: Helm Chart CSP Values
The Helm chart (`deploy/helm/`) SHALL include per-CSP values files:
- `values-aws.yaml` — EKS-specific (GP2/GP3 storage, ALB ingress, EBS CSI)
- `values-azure.yaml` — AKS-specific (Azure Disk, Azure Ingress, Azure Files)
- `values-gcp.yaml` — GKE-specific (Persistent Disk, Cloud Ingress, Filestore)
- `values-oci.yaml` — OKE-specific (Block Volume, OCI LB, File Storage)
- `values-ibm.yaml` — IKS/OpenShift-specific (IBM Block Storage, IBM LB, IBM File Storage)
- `values-on-prem.yaml` — Self-managed K8s (local-path storage, nginx ingress, NFS)
- `values-docker.yaml` — Docker Compose equivalent values for development

#### REQ-38-042: Managed K8s Provisioning
The namespace provisioner (`tools/saas/infra/namespace_provisioner.py`) SHALL support EKS, AKS, GKE, OKE, IKS, Red Hat OpenShift on IBM Cloud, self-managed K8s, and OpenShift for creating per-tenant namespaces.

### 6.6 IaC Generation

#### REQ-38-050: Multi-CSP Terraform
The system SHALL generate Terraform modules for all five CSPs (government and commercial modes) plus on-premises from a single abstract infrastructure specification.

#### REQ-38-051: CSP-Specific Terraform Modules
Terraform modules SHALL be organized by CSP:
- `deploy/terraform/modules/aws/` — VPC, EKS, RDS, S3, Secrets Manager, KMS, IAM
- `deploy/terraform/modules/azure/` — VNet, AKS, Azure PG, Blob, Key Vault, KMS, Entra ID
- `deploy/terraform/modules/gcp/` — VPC, GKE, Cloud SQL, GCS, Secret Manager, KMS, IAM
- `deploy/terraform/modules/oci/` — VCN, OKE, Autonomous DB, Object Storage, Vault, KMS, IAM
- `deploy/terraform/modules/ibm/` — VPC, IKS/OpenShift, Databases for PG, COS, Secrets Manager, Key Protect, IAM
- `deploy/terraform/modules/common/` — K8s base, network policies, monitoring
- `deploy/terraform/modules/on-prem/` — Docker Compose, local K8s, local storage

#### REQ-38-052: Ansible Playbook Abstraction
Ansible playbooks SHALL use CSP-specific variable files rather than hardcoded AWS references.

### 6.7 Monitoring and Observability

#### REQ-38-060: Monitoring Provider Interface
The system SHALL abstract monitoring behind a `MonitoringProvider` interface supporting:
- AWS: CloudWatch + CloudTrail (GovCloud + commercial)
- Azure: Azure Monitor + Azure Sentinel (AzGov + AzureCloud)
- Google: Cloud Monitoring + Security Command Center (Assured Workloads + commercial)
- Oracle: OCI Monitoring + Logging Analytics (government + commercial)
- IBM: IBM Cloud Monitoring (Sysdig) + IBM Log Analysis + QRadar (IC4G + commercial)
- Local: Prometheus + Grafana + ELK (existing — on-prem / air-gap / development)

#### REQ-38-061: SIEM Integration Per CSP
SIEM forwarding SHALL support CSP-native SIEM services in addition to existing ELK/Splunk integration.

### 6.8 Tenant Isolation Per Deployment Mode

#### REQ-38-070: CSP-Aware Tenant Isolation (Government)
Tenant isolation for government workloads SHALL be implemented per CSP by impact level:

| Impact Level | AWS | Azure | Google | Oracle | IBM |
|-------------|-----|-------|--------|--------|-----|
| IL2–IL4 | Dedicated K8s namespace | Dedicated K8s namespace | Dedicated K8s namespace | Dedicated K8s namespace | Dedicated K8s namespace |
| IL5 | Dedicated node pool + VPC peering | Dedicated node pool + VNet peering | Dedicated node pool + VPC-SC | Dedicated compartment | Dedicated IC4G worker pool |
| IL6 | Dedicated AWS sub-account (C2S) | Azure Gov Secret (dedicated) | Not available | OCI DoD region (dedicated) | Not available |

#### REQ-38-071: IL6 CSP Restrictions
IL6/SECRET workloads SHALL only be permitted on CSPs with certified IL6 environments:
- AWS: C2S / SC2S
- Azure: Azure Government Secret / Top Secret
- Oracle: DISA-authorized DoD regions
- Google: **Not supported for IL6** (system SHALL reject IL6 tenant creation on GCP)
- IBM: **Not supported for IL6** (system SHALL reject IL6 tenant creation on IBM Cloud)
- Local: Air-gapped on-prem with customer-managed SIPR infrastructure

#### REQ-38-072: Commercial Cloud Tenant Isolation
Tenant isolation for commercial workloads SHALL be implemented based on compliance regime:

| Compliance | Isolation Model | CSP Requirement |
|-----------|-----------------|-----------------|
| SOC 2 | Dedicated K8s namespace | Any commercial region |
| ISO 27001 | Dedicated K8s namespace | Any certified region |
| PCI DSS | Dedicated namespace + network segmentation | PCI-certified region, CDE isolation |
| HIPAA | Dedicated namespace + encryption at rest | BAA-covered region, PHI encryption |
| HITRUST | Dedicated namespace + access controls | BAA-covered region |
| CJIS | Dedicated node pool + VPC/VNet isolation | Government region or CJIS-approved facility |
| GxP | Dedicated namespace + audit trail | GxP-validated environment |

#### REQ-38-073: On-Premises Tenant Isolation
On-premises deployments SHALL support tenant isolation through:
- Dedicated Docker networks or K8s namespaces (multi-tenant on-prem)
- Dedicated physical or VM instances (high-isolation on-prem)
- Network segmentation via firewall rules and network policies

### 6.9 Compliance-Driven CSP Region Validation

#### REQ-38-080: Region Compliance Validation
The system SHALL validate that the selected CSP region holds the required certifications for the tenant's compliance posture before allowing deployment. For example:
- Tenant requiring HIPAA → CSP region must have BAA coverage
- Tenant requiring FedRAMP High → Must use government cloud region
- Tenant requiring PCI DSS → Region must be PCI certified
- Tenant requiring BSI C5 → Must use EU region with C5 certification
- Tenant requiring IRAP → Must use Australia region with IRAP assessment

#### REQ-38-081: CSP Compliance Certification Registry
The system SHALL maintain a registry of CSP regions and their compliance certifications in `context/compliance/csp_certifications.json`. This registry maps each CSP region to its active certifications, enabling automated validation.

#### REQ-38-082: Compliance Gap Warning
When a tenant's compliance posture cannot be fully satisfied by the selected CSP region, the system SHALL warn with specific gaps and suggest alternative regions or CSPs.

### 6.10 Documentation and Configuration

#### REQ-38-090: Cloud Configuration File
The system SHALL use `args/cloud_config.yaml` as the single source of truth for cloud provider selection, cloud mode, region configuration, and service mapping.

#### REQ-38-091: CLAUDE.md Updates
All AWS-specific references in CLAUDE.md SHALL be replaced with cloud-agnostic language, with CSP-specific details moved to `args/cloud_config.yaml`.

#### REQ-38-092: CSP MCP Registry
The existing `context/agentic/csp_mcp_registry.yaml` (which already supports multi-cloud for child apps) SHALL be extended to cover ICDEV's own MCP server configuration per CSP.

#### REQ-38-093: Deployment Profile Updates
The deployment profiles in `args/deployment_profiles.yaml` SHALL be updated to include cloud mode recommendations per profile (commercial, government, on-prem).

---

## 7. Database Schema Changes

### Modified Tables

| Table | Change |
|-------|--------|
| `tenants` (platform.db) | Add `cloud_provider` column (aws/azure/gcp/oci/ibm/local) |
| `tenants` (platform.db) | Add `cloud_region` column |
| `tenants` (platform.db) | Add `cloud_mode` column (commercial/government/on_prem/air_gapped) |
| `deployments` (icdev.db) | Add `cloud_provider`, `cloud_region`, and `cloud_mode` columns |
| `agent_config` (icdev.db) | Add `llm_provider` column to track which CSP provides LLM per agent |

### New Tables

| Table | Purpose |
|-------|---------|
| `cloud_provider_status` | Health status per CSP per service (provider, service, status, last_check, latency_ms) |
| `csp_region_certifications` | Compliance certifications per CSP region (region, framework, cert_date, expiry) |

---

## 8. New Tools

| Tool | Purpose |
|------|---------|
| `tools/cloud/provider_factory.py` | Factory for creating CSP-specific provider instances (govcloud + commercial + local) |
| `tools/cloud/secrets_provider.py` | ABC + implementations for secrets management |
| `tools/cloud/storage_provider.py` | ABC + implementations for object storage |
| `tools/cloud/registry_provider.py` | ABC + implementations for container registry |
| `tools/cloud/monitoring_provider.py` | ABC + implementations for monitoring/logging |
| `tools/cloud/kms_provider.py` | ABC + implementations for key management |
| `tools/cloud/iam_provider.py` | ABC + implementations for IAM |
| `tools/cloud/csp_health_checker.py` | Health check across all configured CSP services |
| `tools/cloud/csp_monitor.py` | Autonomous CSP service monitor — scans feeds, diffs registry, generates signals (D239) |
| `tools/cloud/csp_changelog.py` | Human-readable changelog generator for CSP service changes |
| `tools/cloud/region_validator.py` | Validates CSP region compliance certifications against tenant requirements |
| `tools/infra/terraform_generator_azure.py` | Azure-specific Terraform generation |
| `tools/infra/terraform_generator_gcp.py` | GCP-specific Terraform generation |
| `tools/infra/terraform_generator_oci.py` | OCI-specific Terraform generation |
| `tools/infra/terraform_generator_ibm.py` | IBM Cloud-specific Terraform generation |
| `tools/infra/terraform_generator_onprem.py` | On-premises Terraform generation (Docker/local K8s) |

### Modified Tools

| Tool | Change |
|------|--------|
| `tools/llm/router.py` | Add Azure OpenAI, Vertex AI, OCI GenAI, IBM watsonx providers; cloud mode awareness |
| `tools/llm/embedding_provider.py` | Add Azure, GCP, OCI, IBM embedding providers |
| `tools/agent/bedrock_client.py` | Preserved for backward compat (D70); new calls use LLM router |
| `tools/infra/terraform_generator.py` | Refactor to CSP-agnostic orchestrator |
| `tools/infra/ansible_generator.py` | CSP-specific variable files |
| `tools/infra/k8s_generator.py` | CSP-agnostic manifests with CSP overlays |
| `tools/saas/infra/namespace_provisioner.py` | Multi-CSP namespace creation (EKS, AKS, GKE, OKE, IKS, OpenShift, self-managed K8s) |
| `tools/saas/artifacts/delivery_engine.py` | Use storage abstraction |
| `tools/compliance/compliance_detector.py` | Add CSP region certification validation |

### New Configuration Files

| File | Purpose |
|------|---------|
| `context/compliance/csp_certifications.json` | CSP region → compliance certification mapping |
| `args/csp_monitor_config.yaml` | CSP monitoring configuration — sources, signals, diff engine, scheduling (D239) |
| `context/cloud/csp_service_registry.json` | Baseline CSP service catalog — services, compliance programs, regions, status (D240) |

---

## 9. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D223 | CSP abstraction follows D66 provider pattern (ABC + implementations) | Consistent with existing LLM and embedding provider patterns |
| D224 | `args/cloud_config.yaml` is single source of truth for CSP selection | Consistent with D71 (llm_config.yaml is single source for LLM routing) |
| D225 | Per-service CSP override allowed (e.g., LLM from Azure, storage from AWS) | Some customers use best-of-breed across CSPs; DoD hybrid cloud is common |
| D226 | Local/air-gap implementation for every service ABC | Consistent with D69 (fallback chains end with local); air-gap is a first-class deployment mode |
| D227 | IL6 restricted to certified CSPs (AWS C2S, Azure Gov Secret, OCI DoD) | Google Cloud and IBM Cloud do not have IL6 certification; system must enforce |
| D228 | Bedrock client preserved for backward compatibility (extends D70) | Existing callers continue to work; new code uses LLM router |
| D229 | Terraform modules organized by CSP with common base | Maximizes reuse while allowing CSP-specific customization |
| D230 | CSP health checking integrated into heartbeat daemon (Phase 29) | Proactive detection of CSP service degradation |
| D231 | CSP SDKs are optional dependencies (extends D73 graceful degradation) | Only install SDK for the CSP you deploy to; no bloated requirements.txt |
| D232 | Each CSP provider supports both government and commercial cloud modes | Avoids duplicating provider classes; cloud_mode parameter configures endpoints/regions |
| D233 | CSP region compliance certification registry as JSON catalog | Consistent with D26 (declarative JSON rules without code changes); enables automated validation |
| D234 | Compliance-driven deployment validation using Phase 23 crosswalk engine | Tenant compliance posture determines required CSP certifications; reuses existing framework infrastructure |
| D235 | On-premises deployment uses same provider ABCs with local implementations | No special-case code for on-prem; local provider is a first-class CSP alongside AWS/Azure/GCP/OCI |
| D236 | Commercial cloud is the default; government cloud requires explicit opt-in | Most customers are commercial; GovCloud is a specialized configuration, not the baseline |
| D237 | IBM Cloud supported as 5th CSP with IKS/OpenShift for K8s and watsonx for AI | IBM has FedRAMP High (IC4G), strong federal civilian presence, and OpenShift is common in DoD/IC |
| D238 | IBM watsonx on AWS GovCloud treated as IBM LLM provider (not AWS) | IBM manages the watsonx layer; underlying AWS infra is transparent to ICDEV |
| D239 | CSP monitoring integrated as Innovation Engine source (Phase 35) | Reuses existing signal scoring, triage, and solution generation pipeline; CSP changes treated as innovation signals with category mapping and government/compliance boosts |
| D240 | Declarative CSP service registry as JSON catalog (extends D26 pattern) | Baseline of all CSP services, compliance programs, regions, and FIPS status; monitor diffs live data against registry to detect changes; human review required before registry updates |
| D241 | CSP changelog generates actionable recommendations per change type | Each change type (deprecation, compliance scope change, breaking API change, etc.) maps to specific files and actions; enables ISSO and architects to respond systematically |

---

## 10. CSP SDK Dependencies

| CSP | SDK Package | Required When |
|-----|------------|---------------|
| AWS | `boto3` | `cloud.provider: aws` |
| Azure | `azure-identity`, `azure-keyvault-secrets`, `azure-storage-blob`, `azure-mgmt-containerservice` | `cloud.provider: azure` |
| Google | `google-cloud-secret-manager`, `google-cloud-storage`, `google-cloud-aiplatform` | `cloud.provider: gcp` |
| Oracle | `oci` | `cloud.provider: oci` |
| IBM | `ibm-cloud-sdk-core`, `ibm-cos-sdk`, `ibm-secrets-manager-sdk`, `ibm-watsonx-ai` | `cloud.provider: ibm` |
| Local | *(none — stdlib only)* | `cloud.provider: local` or air-gapped |

All SDKs are optional. The system SHALL function with only the SDK for the configured CSP installed. On-premises deployments require zero cloud SDKs.

---

## 11. Implementation Sub-Phases

### Sub-Phase 38A: Core Abstraction Layer
**Scope:** Create `tools/cloud/` with ABCs and implementations for Secrets, Storage, and KMS. Create `args/cloud_config.yaml` with cloud_mode support. Refactor hardcoded AWS references. Build CSP region certification registry.

**Deliverables:**
- `tools/cloud/provider_factory.py` (cloud mode awareness)
- `tools/cloud/secrets_provider.py` (6 implementations: AWS, Azure, GCP, OCI, IBM, Local × gov/commercial modes)
- `tools/cloud/storage_provider.py` (6 implementations)
- `tools/cloud/kms_provider.py` (6 implementations)
- `tools/cloud/region_validator.py`
- `args/cloud_config.yaml` (updated with cloud_mode)
- `context/compliance/csp_certifications.json`
- Refactored code removing hardcoded AWS references

### Sub-Phase 38B: LLM Multi-Cloud
**Scope:** Extend LLM router for Azure OpenAI, Vertex AI, OCI GenAI, and IBM watsonx. Add government and commercial cloud endpoints. Configure per-CSP fallback chains.

**Deliverables:**
- `tools/llm/azure_openai_provider.py` (AzureCloud + AzGov endpoints)
- `tools/llm/vertex_ai_provider.py` (commercial + Assured Workloads)
- `tools/llm/oci_genai_provider.py` (commercial + gov)
- `tools/llm/ibm_watsonx_provider.py` (commercial + IC4G via AWS GovCloud)
- Updated `args/llm_config.yaml` with all providers and cloud modes
- Government and commercial endpoint configurations

### Sub-Phase 38C: IaC Multi-Cloud
**Scope:** Generate Terraform modules for all five CSPs (government + commercial). Refactor Ansible playbooks. Create per-CSP Helm values. Add on-premises Terraform.

**Deliverables:**
- `deploy/terraform/modules/aws/` (refactored from existing)
- `deploy/terraform/modules/azure/` (new)
- `deploy/terraform/modules/gcp/` (new)
- `deploy/terraform/modules/oci/` (new)
- `deploy/terraform/modules/ibm/` (new)
- `deploy/terraform/modules/common/` (extracted)
- `deploy/terraform/modules/on-prem/` (new)
- `deploy/terraform/environments/` (govcloud, commercial, on-prem per CSP)
- `deploy/helm/values-aws.yaml`, `values-azure.yaml`, `values-gcp.yaml`, `values-oci.yaml`, `values-ibm.yaml`, `values-on-prem.yaml`
- Refactored Ansible playbooks with CSP variable files

### Sub-Phase 38D: Monitoring, IAM, and Tenant Integration
**Scope:** Monitoring abstraction, IAM abstraction, per-tenant CSP assignment, CSP health checking, commercial tenant isolation.

**Deliverables:**
- `tools/cloud/monitoring_provider.py` (6 implementations: AWS, Azure, GCP, OCI, IBM, Local)
- `tools/cloud/iam_provider.py` (6 implementations)
- `tools/cloud/registry_provider.py` (6 implementations)
- `tools/cloud/csp_health_checker.py`
- Modified tenant manager for per-tenant CSP assignment (gov + commercial + on-prem)
- Modified namespace provisioner for multi-CSP (EKS, AKS, GKE, OKE, IKS) + self-managed K8s + OpenShift
- Compliance-driven CSP region validation integrated with Phase 23

### Sub-Phase 38E: Deployment Profile Updates & Documentation
**Scope:** Update all 10 deployment profiles with cloud mode support. Update CLAUDE.md. Update documentation to remove AWS-only language.

**Deliverables:**
- Updated `args/deployment_profiles.yaml` with cloud mode per profile
- Updated `CLAUDE.md` with cloud-agnostic language
- Updated `args/cloud_config.yaml` with example configs per profile
- Updated installation wizard to prompt for cloud mode

### Sub-Phase 38F: CSP Service Monitoring & Auto-Update
**Scope:** Autonomous monitoring of all five CSPs for service additions, deprecations, compliance scope changes, and breaking API changes. Integrates with Innovation Engine (Phase 35) for signal scoring and triage.

**Deliverables:**
- `tools/cloud/csp_monitor.py` (CSP service scanner with RSS/API/HTML adapters)
- `tools/cloud/csp_changelog.py` (human-readable changelog generator with recommendations)
- `args/csp_monitor_config.yaml` (sources, signals, diff engine, scheduling)
- `context/cloud/csp_service_registry.json` (baseline catalog of 45+ services across 5 CSPs)
- Innovation Engine integration (csp_monitor source in innovation_config.yaml)
- Dashboard SSE integration for real-time CSP change notifications

---

## 12. CSP Service Monitoring (D239–D241)

### 12.1 Problem
CSPs continuously release new services, deprecate old ones, add compliance certifications, expand to new regions, and make breaking API changes. Without automated monitoring:
- ICDEV's service registry becomes stale
- New services are not evaluated for provider integration
- Compliance scope changes go undetected (services added/removed from FedRAMP, HIPAA, PCI, etc.)
- Breaking API changes cause provider failures in production
- Region expansions are missed, limiting deployment options

### 12.2 Architecture

```
CSP Feeds (RSS/API/HTML)                    Innovation Engine (Phase 35)
  AWS What's New ─────┐                     ┌─→ SCORE (signal_ranker.py)
  Azure Updates ──────┤                     │    ↓
  GCP Release Notes ──┼→ csp_monitor.py ────┼─→ TRIAGE (triage_engine.py)
  OCI Release Notes ──┤   │ scan            │    ↓
  IBM Announcements ──┘   │ classify        ├─→ GENERATE (solution_generator.py)
                          │ dedup           │    ↓
                          ↓                 └─→ BUILD/PUBLISH (ATLAS + marketplace)
                    innovation_signals
                    (source='csp_monitor')
                          │
                          ↓
                    csp_changelog.py ──→ Markdown/JSON reports
                          │
                          ↓
                    Registry diff ──→ Human review ──→ Registry update
```

### 12.3 Signal Flow

1. **SCAN** — `csp_monitor.py` fetches CSP announcement feeds (RSS/Atom), filters by keywords
2. **CLASSIFY** — Each announcement is classified as: `new_service`, `service_deprecation`, `compliance_scope_change`, `region_expansion`, `api_breaking_change`, `security_update`, `pricing_change`, `certification_change`
3. **SCORE** — Community score assigned per change type (0.3–0.9), boosted for government (×1.3) and compliance (×1.5) relevance
4. **STORE** — Signals stored in `innovation_signals` table (append-only, D6) with `source='csp_monitor'`
5. **DIFF** — Signals compared against `context/cloud/csp_service_registry.json` to detect registry changes
6. **TRIAGE** — Innovation Engine pipeline scores and triages signals for solution generation
7. **REPORT** — `csp_changelog.py` generates changelogs with per-change-type recommendations

### 12.4 Change Type Mapping

| Change Type | Category | Score | Urgency | Action |
|-------------|----------|-------|---------|--------|
| `new_service` | infrastructure | 0.6 | low | Evaluate for provider integration |
| `service_deprecation` | modernization | 0.8 | high | Plan migration, update Terraform |
| `compliance_scope_change` | compliance_gap | 0.9 | critical | Review csp_certifications.json |
| `region_expansion` | infrastructure | 0.4 | low | Update registry regions |
| `api_breaking_change` | modernization | 0.9 | critical | Update provider implementation |
| `security_update` | security_vulnerability | 0.7 | high | Review advisory, patch |
| `pricing_change` | developer_experience | 0.3 | low | Update cost models |
| `certification_change` | compliance_gap | 0.9 | critical | Review deployment eligibility |

### 12.5 CSP Service Registry

The registry (`context/cloud/csp_service_registry.json`) is the baseline catalog of all CSP services tracked by ICDEV. It records:
- Service name, category, and description
- Government and commercial availability
- Compliance programs in scope (FedRAMP, HIPAA, PCI DSS, etc.)
- FIPS 140-2 validation status and level
- Available regions (government + commercial)
- ICDEV provider mapping (secrets, storage, kms, monitoring, iam, registry, ai_ml)

Registry updates require human review by default (`require_review: true` in config). Backups are created before every update.

### 12.6 Commands

```bash
# Scan all CSPs for service updates
python tools/cloud/csp_monitor.py --scan --all --json

# Scan specific CSP
python tools/cloud/csp_monitor.py --scan --csp aws --json

# Diff registry against recent signals (offline-capable)
python tools/cloud/csp_monitor.py --diff --json

# Monitor status
python tools/cloud/csp_monitor.py --status --json

# Apply signal to registry (with backup)
python tools/cloud/csp_monitor.py --update-registry --signal-id "sig-xxx" --json

# Generate changelog (last 30 days)
python tools/cloud/csp_changelog.py --generate --days 30 --json
python tools/cloud/csp_changelog.py --generate --days 7 --format markdown --output .tmp/csp_changelogs/

# Summary statistics
python tools/cloud/csp_changelog.py --summary --json

# Continuous daemon mode
python tools/cloud/csp_monitor.py --daemon --json
```

---

## 13. Security Considerations

### 13.1 Compliance Equivalence
The system SHALL maintain identical security posture regardless of CSP or deployment mode. All security gates, compliance checks, and CUI/PHI/PCI markings apply equally across all CSPs and on-premises deployments.

### 13.2 FIPS 140-2 Across CSPs
CSPs SHALL use FIPS 140-2 validated modules when required by the tenant's compliance posture:

| CSP | FIPS Endpoint | Required By |
|-----|--------------|-------------|
| AWS | *.fips.us-gov-west-1.amazonaws.com (GovCloud) or *.fips.us-east-1.amazonaws.com (commercial) | FedRAMP, CJIS, FTI, IL4+ |
| Azure | Azure Government FIPS endpoints or Azure Commercial FIPS endpoints | FedRAMP, CJIS, FTI, IL4+ |
| Google | BoringCrypto FIPS module | FedRAMP, CJIS, FTI, IL4+ |
| Oracle | OCI FIPS validated modules | FedRAMP, CJIS, FTI, IL4+ |
| IBM | Key Protect FIPS 140-2 L3 HSM / Hyper Protect Crypto Services FIPS 140-2 L4 | FedRAMP, CJIS, FTI, IL4+ |
| On-Prem | Customer-managed FIPS modules or HSMs | Per compliance posture |

### 13.3 CSP Authorization Validation
The system SHALL validate CSP region authorization before deployment based on the tenant's compliance requirements:
- **FedRAMP workloads**: Only FedRAMP-authorized regions
- **HIPAA workloads**: Only regions with Business Associate Agreement (BAA) coverage
- **PCI DSS workloads**: Only PCI-certified regions with CDE isolation
- **CJIS workloads**: Only CJIS-approved regions or facilities
- **IL4+ workloads**: Only government cloud regions with FIPS endpoints
- **ISO 27001 workloads**: Any certified region (all major CSP regions)
- **BSI C5 workloads**: EU regions with C5 certification
- **IRAP workloads**: Australia regions with IRAP assessment
- **SOC 2 workloads**: Any SOC 2-audited region (all major CSP regions)

### 13.4 Data Residency
Data SHALL remain within the configured cloud region. Cross-region or cross-CSP data movement SHALL require explicit authorization and classification review based on the tenant's compliance posture:
- **CUI / IL4+**: Cross-region prohibited without ISSO authorization
- **PHI (HIPAA)**: Cross-region requires BAA coverage at destination
- **PCI**: Cross-region requires PCI scope update
- **CJIS**: Cross-region requires CJIS Security Addendum at destination
- **EU data (BSI C5, ISO)**: Data must remain within EU per GDPR/Schrems II considerations
- **SOC 2 / ISO 27001**: Cross-region documented in risk assessment

### 13.5 Encryption Requirements by Compliance Regime

| Regime | At Rest | In Transit | Key Management |
|--------|---------|------------|---------------|
| FedRAMP High | AES-256 (FIPS 140-2) | TLS 1.2+ (FIPS 140-2) | CSP KMS (FIPS 140-2 L3) |
| HIPAA | AES-256 | TLS 1.2+ | CSP KMS or customer-managed |
| PCI DSS v4.0 | AES-256 | TLS 1.2+ | HSM or CSP KMS |
| CJIS | AES-256 (FIPS 140-2) | TLS 1.2+ (FIPS 140-2) | FIPS 140-2 validated |
| SOC 2 | AES-256 (recommended) | TLS 1.2+ | CSP KMS |
| ISO 27001 | Per risk assessment | TLS 1.2+ | Per risk assessment |
| IL6/SECRET | AES-256 (FIPS 140-2, NSA Type 1) | TLS 1.3 (NSA approved) | HSM (FIPS 140-2 L3+) |

---

## 14. Security Gate

**Cloud Deployment Gate:**
- CSP region has required compliance certifications for the tenant's compliance posture (REQ-38-080)
- FIPS 140-2 endpoints active when required by compliance regime
- CSP health check passing for all required services
- Tenant isolation level appropriate for compliance regime (REQ-38-070/072/073)
- IL6 workloads only on certified CSPs (REQ-38-071)
- HIPAA workloads only on BAA-covered regions
- PCI DSS workloads only on PCI-certified regions with CDE isolation
- CJIS workloads only on CJIS-approved regions/facilities
- No cross-region data movement without authorization appropriate to classification
- All CSP credentials stored via secrets provider (not hardcoded)
- Encryption standards met per compliance regime (Section 13.5)
- On-premises deployments validated for required compliance controls
- CSP service registry current within configured scan interval (D240)
- No critical CSP monitor signals (compliance_scope_change, certification_change) unreviewed for >7 days
