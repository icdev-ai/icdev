# [TEMPLATE: CUI // SP-CTI]

# /icdev-devsecops — DevSecOps Profile & Pipeline Security

Manage DevSecOps security profiles, assess maturity, generate pipeline security stages,
create policy-as-code (Kyverno/OPA), and manage image signing & SBOM attestation.

## Workflow

### 1. Profile Management
```bash
# Create a DevSecOps profile for a project
python tools/devsecops/profile_manager.py --project-id "$ARGUMENTS" --create --maturity level_3_defined --json

# View existing profile
python tools/devsecops/profile_manager.py --project-id "$ARGUMENTS" --json

# Auto-detect maturity from project artifacts
python tools/devsecops/profile_manager.py --project-id "$ARGUMENTS" --detect --json

# Assess current maturity level
python tools/devsecops/profile_manager.py --project-id "$ARGUMENTS" --assess --json

# Update profile stages
python tools/devsecops/profile_manager.py --project-id "$ARGUMENTS" --update --stages '["sast","sca","secret_detection","container_scan","policy_as_code"]' --json
```

### 2. Pipeline Security Generation
```bash
# Generate profile-driven pipeline security stages (.gitlab-ci.yml)
python tools/devsecops/pipeline_security_generator.py --project-id "$ARGUMENTS" --json
```

### 3. Policy-as-Code Generation
```bash
# Generate Kyverno policies (K8s-native YAML)
python tools/devsecops/policy_generator.py --project-id "$ARGUMENTS" --engine kyverno --json

# Generate OPA/Gatekeeper policies (Rego)
python tools/devsecops/policy_generator.py --project-id "$ARGUMENTS" --engine opa --json
```

### 4. Image Signing & Attestation
```bash
# Generate signing config (Cosign/Notation)
python tools/devsecops/attestation_manager.py --project-id "$ARGUMENTS" --generate --json

# Generate SLSA Level 3 attestation pipeline
python tools/devsecops/attestation_manager.py --project-id "$ARGUMENTS" --generate --json

# Verify attestations
python tools/devsecops/attestation_manager.py --project-id "$ARGUMENTS" --verify --image "registry.mil/app:latest" --json
```

## Decision Flow

1. **First time?** → Create profile with `--create`, or `--detect` to auto-detect from existing CI/CD
2. **Need pipeline stages?** → Run pipeline security generator after profile exists
3. **Need admission control?** → Generate Kyverno (K8s-native) or OPA (Rego) policies
4. **Need supply chain attestation?** → Generate signing config + attestation pipeline

## Related Commands
- `/icdev-zta` — Zero Trust Architecture maturity & infrastructure
- `/icdev-secure` — Security scanning (SAST, deps, secrets, containers)
- `/icdev-comply` — Compliance artifact generation
- `/icdev-deploy` — Infrastructure and CI/CD pipeline generation
