# [TEMPLATE: CUI // SP-CTI]

# ICDEV On-Premises Installation Guide

This document covers the installation, configuration, and management of ICDEV in an air-gapped on-premises Kubernetes environment.

## Prerequisites

| Requirement | Minimum Version | Notes |
|-------------|----------------|-------|
| Kubernetes cluster | 1.25+ | OpenShift 4.12+ also supported |
| Helm | 3.12+ | Used for chart deployment |
| kubectl | 1.25+ | Configured with cluster access |
| Docker or Podman | 20.10+ / 4.0+ | For loading container images |
| Storage class | - | ReadWriteOnce for database PVC |
| License key | - | Obtain from ICDEV team |

**Resource requirements (minimum):**
- 4 vCPUs, 8 GB RAM for ICDEV workloads
- 20 GB persistent storage for internal PostgreSQL
- Additional resources for monitored applications

## Quick Start

```bash
# 1. Load container images (air-gapped)
docker load < icdev-images.tar.gz

# 2. Run the installer (Python -- works in air-gapped / Windows / Linux)
python install.py --license /path/to/license.json

# Alternative: shell script (Linux/macOS only)
# ./install.sh --license /path/to/license.json

# 3. Access the platform
kubectl port-forward -n icdev svc/icdev-api-gateway 8443:8443
# Open https://localhost:8443
```

That is all that is needed for a basic installation with default settings. The installer handles namespace creation, secrets, TLS, and Helm deployment automatically.

> **Note:** `install.py` (Python) is the preferred installer. It is cross-platform (Windows, Linux, macOS) and works in air-gapped environments where bash may not be available. `install.sh` is provided as a convenience for Linux/macOS users.

## Configuration

### values.yaml Reference

Copy and customise `deploy/helm/values.yaml` for your environment:

```bash
cp deploy/helm/values.yaml my-values.yaml
# Edit my-values.yaml
python install.py --license license.json --values my-values.yaml
```

**Key settings:**

| Setting | Default | Description |
|---------|---------|-------------|
| `global.impactLevel` | IL4 | Impact level: IL2, IL4, IL5, IL6 |
| `apiGateway.replicas` | 2 | API gateway replica count |
| `platformDb.type` | internal | `internal` (StatefulSet) or `external` (customer DB) |
| `platformDb.host` | (empty) | Required when `type: external` |
| `tls.enabled` | true | Enable TLS termination |
| `bedrock.region` | us-gov-west-1 | AWS Bedrock region |
| `monitoring.enabled` | true | Enable Prometheus metrics |
| `networkPolicy.enabled` | true | Deploy default-deny network policies |
| `cui.bannerTop` | CUI // SP-CTI | CUI banner text |

### Impact Levels

| Level | Classification | Network | Encryption |
|-------|---------------|---------|------------|
| IL2 | Public | Standard | TLS 1.2+ |
| IL4 | CUI | GovCloud | FIPS 140-2 |
| IL5 | CUI (dedicated) | GovCloud dedicated | FIPS 140-2 |
| IL6 | SECRET | SIPR only | NSA Type 1 |

Set the impact level in your values file:

```yaml
global:
  impactLevel: IL5
```

## Air-Gapped Installation

### Building the Image Tarball

On a connected machine, build and export all container images:

```bash
# Pull or build all ICDEV images
docker pull icdev/api-gateway:21.0.0
docker pull icdev/orchestrator:21.0.0
docker pull icdev/builder:21.0.0
docker pull icdev/compliance:21.0.0
docker pull icdev/security:21.0.0
docker pull icdev/postgresql:15-hardened

# Save to tarball
docker save \
  icdev/api-gateway:21.0.0 \
  icdev/orchestrator:21.0.0 \
  icdev/builder:21.0.0 \
  icdev/compliance:21.0.0 \
  icdev/security:21.0.0 \
  icdev/postgresql:15-hardened \
  | gzip > icdev-images.tar.gz
```

### Transfer to Air-Gapped Environment

Transfer the following files to the air-gapped cluster:
- `icdev-images.tar.gz` -- container images
- `deploy/` -- Helm chart and installer
- `license.json` -- your license key
- TLS certificate and key (if using custom certs)

### Install

```bash
python install.py \
  --namespace icdev \
  --license /path/to/license.json \
  --images /path/to/icdev-images.tar.gz \
  --tls-cert /path/to/tls.crt \
  --tls-key /path/to/tls.key \
  --values /path/to/my-values.yaml
```

## TLS Setup

### Option 1: Provide Your Own Certificate

```bash
python install.py --tls-cert server.crt --tls-key server.key --license license.json
```

### Option 2: Self-Signed (Development Only)

If no certificate is provided, the installer generates a self-signed cert automatically. Replace it before production use:

```bash
kubectl create secret tls icdev-tls \
  --cert=production.crt \
  --key=production.key \
  --namespace icdev \
  --dry-run=client -o yaml | kubectl apply -f -
```

### Option 3: cert-manager

If cert-manager is available in the cluster, add annotations in your values file:

```yaml
ingress:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
```

## Database Setup

### Internal PostgreSQL (Default)

The Helm chart deploys a STIG-hardened PostgreSQL 15 StatefulSet. Data is persisted via a PVC.

```yaml
platformDb:
  type: internal
  storage:
    size: 50Gi
    storageClass: gp3-encrypted
```

### External PostgreSQL (Recommended for Production)

Point ICDEV to your existing PostgreSQL or RDS instance:

```yaml
platformDb:
  type: external
  host: my-rds-instance.abc123.us-gov-west-1.rds.amazonaws.com
  port: 5432
  name: icdev_platform
  user: icdev
  existingSecret: my-db-credentials   # K8s secret with key "password"
```

Create the secret:

```bash
kubectl create secret generic my-db-credentials \
  --from-literal=password='YourSecurePassword' \
  --namespace icdev
```

## License Management

### Viewing License Info

```bash
# From within a pod
kubectl exec -n icdev deploy/icdev-api-gateway -- \
  python /app/tools/saas/licensing/license_validator.py --validate --json

# Locally (if Python available)
python tools/saas/licensing/license_validator.py \
  --license-file /path/to/license.json \
  --validate
```

### Updating a License

```bash
kubectl create secret generic icdev-license \
  --from-file=license.json=/path/to/new-license.json \
  --namespace icdev \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart pods to pick up new license
kubectl rollout restart deployment -n icdev -l app.kubernetes.io/instance=icdev
```

### License Fields

| Field | Description |
|-------|-------------|
| `license_id` | Unique identifier |
| `customer` | Organisation name |
| `tier` | starter, pro, enterprise, unlimited |
| `max_projects` | Max concurrent projects (-1 = unlimited) |
| `max_users` | Max platform users (-1 = unlimited) |
| `allowed_il_levels` | Authorised impact levels |
| `features` | Enabled feature flags (cato, fedramp, cmmc, etc.) |
| `expires_at` | Expiry date (ISO 8601) |
| `signature` | RSA-SHA256 signature (offline verification) |

## Upgrading

### Standard Upgrade

```bash
# Load new images
docker load < icdev-images-22.0.0.tar.gz

# Upgrade Helm release
helm upgrade icdev deploy/helm \
  --namespace icdev \
  --values my-values.yaml \
  --set image.tag=22.0.0 \
  --wait --timeout 600s
```

### Rollback

```bash
helm rollback icdev --namespace icdev
```

### View Upgrade History

```bash
helm history icdev --namespace icdev
```

## Troubleshooting

### Pods Not Starting

```bash
# Check pod status
kubectl get pods -n icdev

# Describe a failing pod
kubectl describe pod <pod-name> -n icdev

# Check logs
kubectl logs <pod-name> -n icdev --previous
```

**Common causes:**
- Image not loaded: run `docker load < icdev-images.tar.gz`
- License secret missing: check `kubectl get secret icdev-license -n icdev`
- Insufficient resources: check node capacity with `kubectl describe nodes`

### License Validation Failures

```bash
# Check license secret content
kubectl get secret icdev-license -n icdev -o jsonpath='{.data.license\.json}' | base64 -d | python -m json.tool

# Validate manually
python tools/saas/licensing/license_validator.py --license-file license.json --validate --json
```

**Common causes:**
- Expired license: check `expires_at` field
- Missing public key: ensure `args/license_public_key.pem` is deployed
- Tampered license: signature will not verify if any field was modified

### Database Connection Issues

```bash
# Check DB pod
kubectl get pods -n icdev -l app.kubernetes.io/component=database

# Check DB logs
kubectl logs -n icdev -l app.kubernetes.io/component=database

# Test connection from API gateway pod
kubectl exec -n icdev deploy/icdev-api-gateway -- \
  pg_isready -h icdev-db -U icdev
```

### Network Policy Issues

If pods cannot communicate, verify network policies:

```bash
kubectl get networkpolicy -n icdev
kubectl describe networkpolicy -n icdev
```

For debugging, temporarily disable network policies:

```yaml
# In values.yaml
networkPolicy:
  enabled: false
```

---

CUI // SP-CTI
