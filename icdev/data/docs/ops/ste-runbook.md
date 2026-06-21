# ICDEV™ STE Initialization Playbook
**Classification:** CUI // SP-CTI  
**Authority:** CISO, DoD/IC  
**Applicability:** IL6 / SIPR deployments; Secure Technical Environment (STE) / Secure Technical Network (STN)

---

## 1. Overview

This runbook covers ICDEV™ deployment into a Secure Technical Environment (STE) for SECRET-level operations on SIPR. It establishes an air-gapped, FIPS-compliant deployment with no public cloud connectivity.

**STE = air-gapped compute environment with SIPR-only ingress/egress.**  
**STN = the SIPR-routed network fabric carrying ICDEV™ traffic.**

Minimum requirements before proceeding:
- DoD-issued PKI certs from DISA (server cert + CA bundle)
- SIPR enclave provisioned with namespace `icdev-ste`
- Ollama running locally at port 11434 with approved models
- Air-gap container registry (Harbor or equivalent) mirroring ICDEV™ images

---

## 2. Pre-Deployment Checklist

Run the validator to confirm readiness:
```bash
export ICDEV_DEPLOY_MODE=STE
export ICDEV_LLM_PROVIDER=ollama
export ICDEV_PKI_SIPR_CERT_PATH=/etc/icdev/pki/server.crt
# ... set all other required env vars (see Section 4)
python tools/airgap/ste_validator.py --validate --strict --json
```

All required checks must pass before proceeding. Advisory checks (Ollama reachable in CI) may be skipped.

---

## 3. Network Isolation

Apply SIPR-only NetworkPolicy:
```bash
kubectl apply -f k8s/ste/networkpolicy-sipr.yaml
```

This policy:
- Blocks all egress except intra-namespace (`icdev-ste`) and kube-system DNS
- Allows loopback to Ollama (port 11434)
- Blocks all ingress from outside the `icdev-ste` namespace

Verify no external DNS resolution occurs:
```bash
kubectl exec -n icdev-ste deploy/icdev-api -- curl -m 5 https://api.openai.com 2>&1 | grep -i "timed out\|refused"
```

---

## 4. PKI Configuration

### 4.1 Mount DISA-Issued Certs
Replace template values in `k8s/ste/secret-pki.yaml`:
```bash
# Generate secret from actual DISA certs (never commit certs to SCM)
kubectl create secret tls icdev-sipr-pki \
  --cert=/path/to/disa-server.crt \
  --key=/path/to/disa-server.key \
  --namespace=icdev-ste

# Add CA bundle separately
kubectl create secret generic icdev-sipr-ca \
  --from-file=ca.crt=/path/to/disa-root-ca-bundle.crt \
  --namespace=icdev-ste
```

### 4.2 Configure Revocation (Fail-Closed)
```bash
export ICDEV_PKI_CRL_URL=http://crl.disa.mil/getcrl?YourCRLUrl
export ICDEV_PKI_OCSP_URL=http://ocsp.disa.mil/
export ICDEV_PKI_STRICT_REVOCATION=true
```

### 4.3 mTLS for A2A Agents
Dev certs must be replaced with DISA-issued certs for SIPR:
```bash
# Verify current certs are NOT self-signed dev certs
openssl x509 -in data/certs/orchestrator.crt -noout -issuer | grep -i "ICDEV Dev CA"
# If the above matches, replace before deploying to STE
```

---

## 5. Air-Gap LLM Routing

All LLM traffic must route through local Ollama:
```bash
export ICDEV_LLM_PROVIDER=ollama
export OLLAMA_BASE_URL=http://localhost:11434
```

Disable two-tier routing in `args/llm_config.yaml`:
```yaml
two_tier:
  enabled: false
```

Apply the ConfigMap:
```bash
kubectl apply -f k8s/ste/configmap-ste.yaml
```

Approved Ollama models for STE (add to `args/llm_config.yaml`):
- `llama3.1:8b` — code, QA
- `mistral:7b` — general reasoning
- `phi3:mini` — lightweight utility tasks

Verify no cloud model calls occur:
```bash
python -c "from tools.airgap.detector import is_airgap; print(is_airgap())"
# Expected: True
```

---

## 6. Required Environment Variables

Apply via `k8s/ste/configmap-ste.yaml` or set in pod environment:

| Variable | Required Value | Purpose |
|----------|---------------|---------|
| `ICDEV_DEPLOY_MODE` | `STE` | Activates STE mode + validator checks |
| `ICDEV_LLM_PROVIDER` | `ollama` | Forces air-gap LLM routing |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama endpoint |
| `ICDEV_STORAGE_BACKEND` | `sqlite` | No cloud DB |
| `ICDEV_MFA_REQUIRED` | `true` | Enforce MFA for all users |
| `ICDEV_PKI_STRICT_REVOCATION` | `true` | Fail-closed on CRL/OCSP error |
| `ICDEV_FIPS_MODE` | `true` | FIPS 140-2 crypto enforcement |
| `ICDEV_CANVAS_ACCESS_GATE` | `true` | Enforce explicit canvas grants |
| `ICDEV_DEVICE_TRUST_REQUIRED` | `true` | Device posture verification |
| `ICDEV_CONTINUOUS_AUTH_ENABLED` | `true` | Session risk monitoring |
| `ICDEV_SESSION_MAX_AGE_MINUTES` | `240` | 4h max session (IL6 requirement) |
| `ICDEV_PDP_STRICT` | `true` | PDP fail-closed |
| `CLASSIFICATION` | `SECRET` | Global classification marking |
| `ICDEV_PKI_SIPR_CERT_PATH` | `/etc/icdev/pki/server.crt` | DISA server cert path |

---

## 7. SIEM Integration

Forward audit trail to SIEM before declaring STE operational:

**Splunk:**
```bash
# In splunk forwarder config:
[monitor:///opt/icdev/.logs/*.ndjson]
index = icdev_ste
sourcetype = icdev_audit
```

**Elasticsearch:**
```bash
export ICDEV_SIEM_ENDPOINT=https://elk.sipr.internal:9200
export ICDEV_SIEM_INDEX=icdev-audit-ste
```

---

## 8. Post-Deployment Verification

```bash
# 1. STE readiness
python tools/airgap/ste_validator.py --validate --strict --json

# 2. ZTA maturity (must be >= 0.85 for IL6)
python tools/devsecops/zta_maturity_scorer.py --project-id ste-prod --all --json

# 3. Health check (no external calls)
python tools/testing/health_check.py --json

# 4. Security scan
python -m bandit -r tools/ --severity-level medium

# 5. Canvas access gate (verify DENY-ALL posture)
python tools/security/canvas_access.py --check testuser tenant1 proposals --json
# Expected: {"permitted": false}
```

---

## 9. CISO Gate Sign-Off

Before IL6 ATO sign-off, CISO must validate:

- [ ] `ste_validator.py --validate --strict` passes all required checks
- [ ] ZTA maturity score ≥ 0.85 (Optimal) for all 7 pillars
- [ ] All A2A certs issued by DISA PKI (not self-signed dev certs)
- [ ] SIPR NetworkPolicy applied and verified (no external DNS resolution)
- [ ] CRL/OCSP configured and tested with revoked serial → denied
- [ ] SIEM forwarding confirmed (audit trail arriving at Splunk/ELK)
- [ ] MFA enrolled for all STE-authorized users
- [ ] Canvas access grants populated — no default-allow entries remain
- [ ] `ICDEV_FIPS_MODE=true` and FIPS-validated OpenSSL in container image
- [ ] `data/certs/` contains DISA-issued certs, not dev CA

---

## 10. Rollback Procedure

If STE initialization fails:
```bash
# Remove namespace (WARNING: destroys all data)
kubectl delete namespace icdev-ste

# Re-provision from air-gap registry
kubectl apply -f k8s/ste/
```

Contact ISSM before any rollback that removes audit data from a classified system.

---

*Classification: CUI // SP-CTI — Handle in accordance with DoD CUI policy (32 CFR Part 2002)*
