# Deployment & Infrastructure Guide

## Overview

ICDEV supports deployment across six cloud service providers, on-premises infrastructure, and air-gapped environments. All containers are STIG-hardened. Infrastructure is generated via Terraform, Ansible, and Helm with cloud-mode-aware configuration.

**Supported deployment targets:**
- AWS GovCloud (default)
- Azure Government
- GCP Assured Workloads
- OCI Government Cloud
- IBM Cloud for Government (IC4G)
- On-premises / Air-gapped (Local)

---

## Docker Images

All Dockerfiles reside in `docker/` and follow STIG hardening requirements:
- Non-root execution (UID 1000)
- Read-only root filesystem
- Drop ALL Linux capabilities
- Minimal base packages
- Resource limits enforced

### Available Images

| Dockerfile | Component | Port |
|-----------|-----------|------|
| `docker/Dockerfile.agent-base` | Base image for all agents | - |
| `docker/Dockerfile.dashboard` | Flask dashboard | 5000 |
| `docker/Dockerfile.api-gateway` | SaaS API gateway (gunicorn) | 8443 |
| `docker/Dockerfile.mbse-agent` | MBSE agent | 8451 |
| `docker/Dockerfile.modernization-agent` | Modernization agent | 8452 |
| `docker/Dockerfile.requirements-analyst-agent` | Requirements Analyst agent | 8453 |
| `docker/Dockerfile.supply-chain-agent` | Supply Chain agent | 8454 |
| `docker/Dockerfile.simulation-agent` | Simulation agent | 8455 |
| `docker/Dockerfile.integration-agent` | Integration agent | 8456 |
| `docker/Dockerfile.devsecops-agent` | DevSecOps/ZTA agent | 8457 |
| `docker/Dockerfile.gateway-agent` | Remote Command Gateway agent | 8458 |

### Building Images

```bash
# Build the base agent image
docker build -f docker/Dockerfile.agent-base -t icdev-agent-base:latest .

# Build a specific agent
docker build -f docker/Dockerfile.dashboard -t icdev-dashboard:latest .
docker build -f docker/Dockerfile.api-gateway -t icdev-api-gateway:latest .

# Build all images
for df in docker/Dockerfile.*; do
  name=$(basename "$df" | sed 's/Dockerfile\./icdev-/')
  docker build -f "$df" -t "$name:latest" .
done
```

### Container Security Verification

```bash
# Verify non-root execution
docker run --rm icdev-dashboard:latest id
# Expected: uid=1000(icdev) gid=1000(icdev)

# Verify read-only filesystem
docker run --rm --read-only icdev-dashboard:latest ls /
# Should succeed (container designed for read-only rootfs)

# Verify dropped capabilities
docker run --rm icdev-dashboard:latest cat /proc/1/status | grep Cap
```

---

## Kubernetes Manifests

All K8s manifests are in the `k8s/` directory.

### Core Manifests

| Manifest | Purpose |
|----------|---------|
| `k8s/namespace.yaml` | `icdev` namespace with labels |
| `k8s/configmap.yaml` | Shared configuration (non-secret) |
| `k8s/secrets.yaml` | Secret references (API keys, TLS certs) |
| `k8s/network-policies.yaml` | Default-deny ingress/egress with explicit allowlists |
| `k8s/ingress.yaml` | Ingress controller configuration |
| 16+ deployment+service pairs | One per agent, dashboard, and API gateway |

### Applying Manifests

```bash
# Create namespace and base resources
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/network-policies.yaml

# Deploy all agents and services
kubectl apply -f k8s/

# Verify deployments
kubectl get pods -n icdev
kubectl get svc -n icdev
```

### Network Policies

All namespaces use **default-deny** network policies. Explicit ingress and egress rules are defined per service:

- Agents communicate only with the Orchestrator and their required peers
- Dashboard accepts ingress from the ingress controller only
- API gateway accepts ingress from the load balancer only
- Database pods accept connections only from their designated services

---

## Auto-Scaling

### Horizontal Pod Autoscaler (HPA)

```bash
# Apply HPA manifests for all 18 components
kubectl apply -f k8s/hpa.yaml

# Verify HPA status
kubectl get hpa -n icdev
```

HPA uses `autoscaling/v2` API with three tier profiles:

| Tier | Components | Min | Max | CPU Target | Memory Target |
|------|-----------|-----|-----|-----------|--------------|
| Core | Orchestrator, Architect | 2 | 8 | 70% | 80% |
| Domain | Builder, Compliance, Security, Infra, MBSE, Modernization, Requirements, Supply Chain, Simulation, DevSecOps, Gateway | 1 | 5 | 75% | 85% |
| Support | Knowledge, Monitor | 1 | 3 | 80% | 85% |
| Dashboard | Dashboard, API Gateway | 2 | 10 | 60% | 70% |

Configuration: `args/scaling_config.yaml`

### Pod Disruption Budgets (PDB)

```bash
# Apply PDB manifests
kubectl apply -f k8s/pdb.yaml

# Verify PDB status
kubectl get pdb -n icdev
```

PDB policy per tier:
- **Core agents + Dashboard + Gateway:** `minAvailable=1` (always at least one pod running)
- **Domain + Support agents:** `maxUnavailable=1` (at most one pod down during disruption)

### Node Autoscaling

```bash
# Apply cluster autoscaler reference deployment
kubectl apply -f k8s/node-autoscaler.yaml
```

Options per cloud provider (configurable in `args/scaling_config.yaml`):
- **AWS EKS:** Cluster Autoscaler (default) or Karpenter (optimized)
- **GCP GKE:** GKE Autopilot or Cluster Autoscaler
- **Azure AKS:** AKS cluster-autoscaler
- **On-premises:** Manual node management

### Topology Spread

Cross-AZ topology spread constraints are configured with `whenUnsatisfiable: ScheduleAnyway` (D144) to prioritize availability over strict even distribution.

### Verifying Scaling

```bash
# Check HPA status and current replicas
kubectl get hpa -n icdev

# Check pod resource usage (requires Metrics Server)
kubectl top pods -n icdev

# Check PDB status during maintenance
kubectl get pdb -n icdev

# Watch scaling events
kubectl get events -n icdev --field-selector reason=SuccessfulRescale
```

---

## Helm Chart

### Installation

```bash
# Default installation
helm install icdev deploy/helm/ --values deploy/helm/values.yaml

# With autoscaling
helm install icdev deploy/helm/ --set autoscaling.enabled=true

# With CSP-specific overrides
helm install icdev deploy/helm/ --values deploy/helm/values-aws.yaml
helm install icdev deploy/helm/ --values deploy/helm/values-azure.yaml
helm install icdev deploy/helm/ --values deploy/helm/values-gcp.yaml
helm install icdev deploy/helm/ --values deploy/helm/values-oci.yaml
helm install icdev deploy/helm/ --values deploy/helm/values-ibm.yaml
helm install icdev deploy/helm/ --values deploy/helm/values-on-prem.yaml
helm install icdev deploy/helm/ --values deploy/helm/values-docker.yaml
```

### Chart Structure

```
deploy/helm/
  Chart.yaml              # Chart metadata and version
  values.yaml             # Default values (all overridable)
  values-aws.yaml         # AWS GovCloud overrides
  values-azure.yaml       # Azure Government overrides
  values-gcp.yaml         # GCP Assured Workloads overrides
  values-oci.yaml         # OCI Government Cloud overrides
  values-ibm.yaml         # IBM Cloud for Government overrides
  values-on-prem.yaml     # On-premises/air-gapped overrides
  values-docker.yaml      # Docker Compose development overrides
  templates/              # K8s manifest templates
```

### Upgrade and Rollback

```bash
# Upgrade with new values
helm upgrade icdev deploy/helm/ --values deploy/helm/values.yaml

# Rollback to previous release
helm rollback icdev 1

# Check release history
helm history icdev
```

---

## Multi-Cloud Terraform Generation

### Terraform Generator (Dispatcher)

The Terraform dispatcher auto-detects the CSP from `args/cloud_config.yaml` or `ICDEV_CLOUD_PROVIDER` environment variable and delegates to the appropriate CSP-specific generator.

```bash
# AWS GovCloud (default)
python tools/infra/terraform_generator.py --project-id "proj-123"

# Azure Government
python tools/infra/terraform_generator.py --project-id "proj-123" --csp azure

# GCP Assured Workloads
python tools/infra/terraform_generator.py --project-id "proj-123" --csp gcp

# OCI Government Cloud
python tools/infra/terraform_generator.py --project-id "proj-123" --csp oci

# IBM Cloud for Government
python tools/infra/terraform_generator_ibm.py --project-id "proj-123" --region us-south --json

# On-premises (K8s or Docker)
python tools/infra/terraform_generator_onprem.py --project-id "proj-123" --target k8s --json
python tools/infra/terraform_generator_onprem.py --project-id "proj-123" --target docker --json
```

### What Gets Generated

Each CSP generator produces:
- VPC/VNet/VCN networking with appropriate government region endpoints
- Compute resources (EKS/AKS/GKE/OKE/IKS)
- Database infrastructure (RDS/Azure SQL/Cloud SQL/Autonomous DB)
- Secrets management (per-CSP service)
- Storage (S3/Blob/GCS/Object Storage)
- KMS encryption (per-CSP service)
- IAM roles and policies
- Monitoring configuration

### Ansible Generation

```bash
python tools/infra/ansible_generator.py --project-id "proj-123"
```

Generates Ansible playbooks for server configuration, security hardening, and application deployment.

### Pipeline Generation

```bash
python tools/infra/pipeline_generator.py --project-id "proj-123"
```

Generates GitLab CI/CD pipeline configuration with stages for build, test, security scan, compliance check, and deploy.

---

## Cloud Mode Configuration

The `cloud_mode` setting in `args/cloud_config.yaml` controls endpoint selection and feature availability:

| Mode | Description | Key Constraints |
|------|-------------|-----------------|
| `commercial` | Standard cloud regions | No IL4+ data |
| `government` | Government-certified regions (default) | GovCloud endpoints, FIPS 140-2 |
| `on_prem` | Self-managed infrastructure | Customer-provided compute and storage |
| `air_gapped` | No internet connectivity | Local LLM (Ollama), local package repos, no external APIs |

### Configuration

```yaml
# args/cloud_config.yaml
cloud_provider: aws          # aws, azure, gcp, oci, ibm, local
cloud_mode: government       # commercial, government, on_prem, air_gapped
region: us-gov-west-1        # CSP-specific region
impact_level: IL5            # IL2, IL4, IL5, IL6
```

Environment variable override: `ICDEV_CLOUD_PROVIDER`

---

## Region Validation

Before deploying, validate that the target region holds the required compliance certifications:

```bash
# Validate a specific region
python tools/cloud/region_validator.py validate \
  --csp aws \
  --region us-gov-west-1 \
  --frameworks fedramp_high,cjis \
  --json

# List eligible regions for a compliance requirement
python tools/cloud/region_validator.py eligible \
  --csp azure \
  --frameworks hipaa \
  --json

# Full deployment readiness check
python tools/cloud/region_validator.py deployment-check \
  --csp aws \
  --region us-gov-west-1 \
  --impact-level IL5 \
  --frameworks hipaa \
  --json

# List all regions for a CSP
python tools/cloud/region_validator.py list --csp aws --json
```

Region certifications are maintained in `context/cloud/csp_certifications.json`. The validator blocks deployment to regions lacking required certifications before Terraform or Helm generation (D234).

---

## CSP Health Monitoring

```bash
# Check all configured cloud services
python tools/cloud/csp_health_checker.py --check --json

# Check cloud mode validity
python tools/cloud/cloud_mode_manager.py --validate --json

# Check service readiness
python tools/cloud/cloud_mode_manager.py --check-readiness --json

# Monitor CSP service changes
python tools/cloud/csp_monitor.py --scan --all --json
python tools/cloud/csp_monitor.py --status --json
```

Health status is stored in the `cloud_provider_status` table for trend tracking.

---

## Air-Gapped Deployment

### Offline Installer

```bash
cd deploy/offline/

# Python installer
python install.py

# Shell script installer
bash install.sh
```

See `deploy/offline/README.md` for:
- Pre-bundled dependency preparation
- Container image loading from tarballs
- Local PyPI mirror setup
- Ollama model pre-download

### Air-Gapped Constraints

When `cloud_mode: air_gapped`:
- All LLM inference via Ollama (local)
- Package installation from local mirror only
- No external API calls (NVD, GitHub, etc.)
- Mattermost + internal_chat only for Remote Gateway (internet channels auto-disabled per D139)
- `prefer_local: true` in `args/llm_config.yaml` for local model fallback chains

### LLM Configuration for Air-Gapped

```yaml
# args/llm_config.yaml
prefer_local: true
providers:
  ollama:
    base_url: http://localhost:11434/v1
    models:
      code_generation: codellama:13b
      embeddings: nomic-embed-text
      vision: llava:13b
```

Set `OLLAMA_BASE_URL=http://localhost:11434/v1` for the Ollama provider.

---

## Rollback

```bash
# Roll back a specific deployment
python tools/infra/rollback.py --deployment-id "deploy-123"
```

Rollback plans are a prerequisite for the Deploy Gate. Every deployment must have a documented rollback procedure before promotion to production.

### K8s Rollback

```bash
# Roll back a K8s deployment
kubectl rollout undo deployment/icdev-builder -n icdev

# Roll back to a specific revision
kubectl rollout undo deployment/icdev-builder --to-revision=2 -n icdev

# Check rollout history
kubectl rollout history deployment/icdev-builder -n icdev
```

### Helm Rollback

```bash
# Roll back to previous Helm release
helm rollback icdev

# Roll back to specific revision
helm rollback icdev 3

# Check history
helm history icdev
```

---

## Database Operations

### Initialization

```bash
# Initialize the main ICDEV database (193 tables)
python tools/db/init_icdev_db.py
```

### Migrations

```bash
# Check migration status
python tools/db/migrate.py --status --json

# Apply pending migrations
python tools/db/migrate.py --up

# Apply to specific version
python tools/db/migrate.py --up --target 005

# Dry run (preview changes)
python tools/db/migrate.py --up --dry-run

# Roll back migrations
python tools/db/migrate.py --down --target 003

# Validate checksums
python tools/db/migrate.py --validate --json

# Apply to all tenant databases
python tools/db/migrate.py --up --all-tenants
```

### Backup and Restore

```bash
# Backup single database
python tools/db/backup.py --backup --db icdev --json

# Backup all databases
python tools/db/backup.py --backup --all --json

# Backup tenant databases
python tools/db/backup.py --backup --tenants --json

# Restore from backup
python tools/db/backup.py --restore --backup-file path/to/backup.bak

# Verify backup integrity
python tools/db/backup.py --verify --backup-file path/to/backup.bak

# List available backups
python tools/db/backup.py --list --json

# Prune old backups (30-day retention)
python tools/db/backup.py --prune --retention-days 30
```

Backups use `sqlite3.backup()` API for WAL-safe online backup (SQLite) or `pg_dump` for PostgreSQL. Optional AES-256-CBC encryption via PBKDF2 with 600K iterations (D152).

---

## Platform Compatibility

```bash
# Run platform compatibility check
python tools/testing/platform_check.py           # Human output
python tools/testing/platform_check.py --json    # JSON output
```

Supported platforms: Linux, macOS, Windows. Platform-specific utilities are in `tools/compat/platform_utils.py`:

```python
from tools.compat.platform_utils import IS_WINDOWS, IS_MACOS, IS_LINUX
from tools.compat.platform_utils import get_temp_dir, get_npx_cmd, get_home_dir
from tools.compat.platform_utils import ensure_utf8_console
```

---

## Related Configuration

| File | Purpose |
|------|---------|
| `args/cloud_config.yaml` | CSP selection, cloud mode, region, impact level, per-service overrides |
| `args/scaling_config.yaml` | HPA profiles, PDB config, topology spread, node autoscaler |
| `args/agent_config.yaml` | 15 agent definitions with ports, TLS certs, Bedrock model config |
| `args/resilience_config.yaml` | Circuit breaker and retry settings per service |
| `args/db_config.yaml` | Migration settings, backup retention, encryption |
| `args/csp_monitor_config.yaml` | CSP service monitoring sources and scheduling |
| `context/cloud/csp_service_registry.json` | Baseline CSP service catalog (45+ services) |
| `context/cloud/csp_certifications.json` | Region compliance certifications |
