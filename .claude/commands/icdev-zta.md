# CUI // SP-CTI

# /icdev-zta — Zero Trust Architecture

Assess ZTA maturity (7 DoD pillars), run NIST SP 800-207 compliance assessments,
generate service mesh configs (Istio/Linkerd), create network micro-segmentation
policies, configure PDP/PEP integration, and monitor ZTA posture for cATO.

## Workflow

### 1. ZTA Maturity Scoring
```bash
# Score all 7 pillars + weighted aggregate
python tools/devsecops/zta_maturity_scorer.py --project-id "$ARGUMENTS" --all --json

# Score individual pillar
python tools/devsecops/zta_maturity_scorer.py --project-id "$ARGUMENTS" --pillar user_identity --json

# View maturity trend over time
python tools/devsecops/zta_maturity_scorer.py --project-id "$ARGUMENTS" --trend --json
```

### 2. NIST SP 800-207 Assessment
```bash
# Run full NIST 800-207 ZTA assessment
python tools/compliance/nist_800_207_assessor.py --project-id "$ARGUMENTS" --json

# Gate check
python tools/compliance/nist_800_207_assessor.py --project-id "$ARGUMENTS" --gate

# Include in multi-regime assessment
python tools/compliance/multi_regime_assessor.py --project-id "$ARGUMENTS" --json
```

### 3. Service Mesh Generation
```bash
# Generate Istio configs (PeerAuth, AuthzPolicy, VirtualService, DestinationRule)
python tools/devsecops/service_mesh_generator.py --project-id "$ARGUMENTS" --mesh istio --json

# Generate Linkerd configs (Server, ServerAuthorization, ServiceProfile)
python tools/devsecops/service_mesh_generator.py --project-id "$ARGUMENTS" --mesh linkerd --json
```

### 4. Network Micro-Segmentation
```bash
# Generate namespace isolation policies (default-deny ingress/egress)
python tools/devsecops/network_segmentation_generator.py --project-path /path --namespaces "app,data,monitoring" --json

# Generate per-pod microsegmentation
python tools/devsecops/network_segmentation_generator.py --project-path /path --services "api,worker,db" --json
```

### 5. ZTA Terraform Modules
```bash
# Generate all ZTA security modules (GuardDuty, SecurityHub, WAF, Config Rules, VPC Flow Logs, Secrets Rotation)
python tools/devsecops/zta_terraform_generator.py --project-path /path --modules all --json

# Generate specific modules
python tools/devsecops/zta_terraform_generator.py --project-path /path --modules guardduty,waf --json
```

### 6. PDP/PEP Configuration
```bash
# Generate PDP reference documentation
python tools/devsecops/pdp_config_generator.py --project-id "$ARGUMENTS" --pdp-type disa_icam --json

# Generate PEP config (Istio AuthorizationPolicy pointing to external PDP)
python tools/devsecops/pdp_config_generator.py --project-id "$ARGUMENTS" --pdp-type zscaler --mesh istio --json
```

### 7. cATO Posture Monitoring
```bash
# Check ZTA posture evidence freshness for cATO
python tools/compliance/cato_monitor.py --project-id "$ARGUMENTS" --check-freshness

# Full cATO readiness (includes ZTA posture dimension)
python tools/compliance/cato_monitor.py --project-id "$ARGUMENTS" --readiness
```

## Decision Flow

1. **Starting ZTA?** → Score maturity first (`--all`) to establish baseline
2. **Need compliance?** → Run NIST 800-207 assessment + crosswalk to 800-53
3. **Need mTLS?** → Generate Istio (full-featured) or Linkerd (lightweight) service mesh
4. **Need segmentation?** → Generate namespace isolation + per-pod microsegmentation
5. **Need AWS security?** → Generate ZTA Terraform modules (GuardDuty, SecurityHub, WAF)
6. **External PDP?** → Configure PDP reference + PEP integration
7. **Pursuing cATO?** → Monitor ZTA posture as evidence dimension

## ZTA 7 Pillars (DoD ZTA Strategy)
| Pillar | Weight | Description |
|--------|--------|-------------|
| User/Identity | 20% | MFA, ICAM, identity verification |
| Device | 15% | Device posture, health attestation, MDM |
| Network | 15% | Micro-segmentation, encrypted channels |
| Application/Workload | 15% | App security, container hardening |
| Data | 15% | Data classification, encryption, DLP |
| Visibility & Analytics | 10% | SIEM, logging, anomaly detection |
| Automation & Orchestration | 10% | SOAR, auto-remediation |

## Related Commands
- `/icdev-devsecops` — DevSecOps profile & pipeline security
- `/icdev-comply` — Compliance artifact generation
- `/icdev-deploy` — Infrastructure generation (Terraform, K8s)
- `/icdev-secure` — Security scanning
