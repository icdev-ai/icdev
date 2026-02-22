# icdev.yaml Specification

The `icdev.yaml` project manifest is the single configuration file that tells ICDEV everything about your project. Drop it in your repository root and ICDEV auto-configures everything — compliance posture, CI/CD pipeline, dev profiles, security scanning, and deployment.

---

## Minimal Example

```yaml
version: 1
project:
  name: my-app
  type: api
  language: python
impact_level: IL4
```

This is enough to get started. ICDEV uses sensible defaults for everything else.

---

## Full Reference

```yaml
# icdev.yaml — Full specification
version: 1

# ─── Project Identity ───────────────────────────────────────
project:
  name: my-app                    # Human-readable project name
  id: proj-my-app                 # Unique project ID (auto-generated if omitted)
  type: microservice              # See "Project Types" below
  language: python                # Primary language (see "Supported Languages")
  description: "Mission planning API for SOF operations"

# ─── Classification ─────────────────────────────────────────
impact_level: IL4                 # IL2 | IL4 | IL5 | IL6
classification:
  level: CUI                     # UNCLASSIFIED | CUI | SECRET
  category: SP-CTI               # CUI category (SP-CTI, SP-EXPT, etc.)
  cui_markings: true              # Apply CUI banners to generated files

# ─── Compliance ──────────────────────────────────────────────
compliance:
  frameworks:                     # One or more compliance frameworks
    - fedramp_high
    - cmmc_l2
    - cjis
  ato:
    status: active                # active | conditional | expired | none
    boundary: "System X ATO"      # ATO boundary name
    continuous_monitoring: true    # Enable cATO evidence auto-refresh
  fips199:
    confidentiality: high         # low | moderate | high
    integrity: moderate
    availability: moderate

# ─── Dev Profile ─────────────────────────────────────────────
profile:
  scope: project                  # platform | tenant | program | project | user
  template: dod_baseline          # Starter template (see quickstart.md for list)
  overrides:                      # Optional overrides on top of template
    style:
      line_length: 120
      indent_size: 4
    testing:
      min_coverage: 90
    git:
      commit_format: "conventional"
      branch_strategy: trunk_based

# ─── CI/CD Pipeline ─────────────────────────────────────────
pipeline:
  platform: auto                  # auto | github | gitlab (auto-detects from remote)

  on_pr:                          # Checks that run on every pull request
    - sast                        # Static application security testing
    - dependency_audit            # Known vulnerability scan
    - secret_detection            # Hardcoded secrets/credentials
    - cui_check                   # CUI marking validation
    - stig_check                  # STIG compliance (CAT1/CAT2/CAT3)
    - unit_tests                  # pytest / JUnit / go test
    - bdd_tests                   # behave / Cucumber / godog
    - lint                        # Language-specific linter
    - format_check                # Code formatting validation

  on_merge:                       # Actions on merge to main/master
    - ssp_generate                # System Security Plan regeneration
    - poam_generate               # Plan of Action & Milestones
    - sbom_generate               # Software Bill of Materials (CycloneDX)
    - deploy_staging              # Deploy to staging environment
    - cato_refresh                # cATO evidence update

  on_schedule:                    # Periodic checks (cron)
    - cve_triage                  # New CVE scanning (daily)
    - isa_check                   # ISA expiry monitoring (weekly)
    - cato_refresh                # cATO evidence freshness (daily)
    - dependency_freshness        # Outdated dependency check (weekly)

  gates:                          # Override default gate thresholds
    stig_max_cat1: 0              # Block on any CAT1 finding (default: 0)
    stig_max_cat2: 5              # Allow up to 5 CAT2 findings (default: 0)
    min_coverage: 80              # Minimum test coverage % (default: 80)
    max_critical_vulns: 0         # Block on critical vulnerabilities (default: 0)

# ─── Deployment ──────────────────────────────────────────────
deployment:
  cloud: aws_govcloud             # aws_govcloud | aws | gcp | azure | on_prem
  region: us-gov-west-1
  platform: k8s                   # k8s | docker | ecs | lambda
  infra_as_code: terraform        # terraform | ansible | both
  container:
    base_image: python:3.11-slim  # Overridden by dev profile if set
    non_root: true
    read_only_rootfs: true

# ─── Integrations ────────────────────────────────────────────
integrations:
  jira:
    instance_url: https://org.atlassian.net
    project_key: MYAPP
    sync: bidirectional            # push | pull | bidirectional
  gitlab:
    instance_url: https://gitlab.org.mil
    project_id: 123
  servicenow:
    instance_url: https://org.service-now.com
```

---

## Project Types

| Type | Description | Default Scaffold |
|------|-------------|------------------|
| `api` | REST/GraphQL API service | Flask/FastAPI + pytest + OpenAPI |
| `microservice` | Containerized microservice | API + Dockerfile + K8s manifests |
| `monolith` | Traditional monolithic application | Full MVC scaffold |
| `cli` | Command-line tool | argparse + tests + man page |
| `data-pipeline` | Data processing pipeline | ETL scaffold + data validation |
| `frontend` | Web frontend | React/Vue + eslint + Playwright |
| `library` | Reusable library/package | Package scaffold + docs + publish |

---

## Supported Languages

| Language | Key | Scaffold Types |
|----------|-----|----------------|
| Python | `python` | python-backend, api, cli, data-pipeline |
| Java | `java` | java-backend |
| Go | `go` | go-backend |
| Rust | `rust` | rust-backend |
| C# | `csharp` | csharp-backend |
| TypeScript | `typescript` | typescript-backend, javascript-frontend |

---

## Defaults

When fields are omitted, ICDEV uses these defaults:

| Field | Default |
|-------|---------|
| `project.id` | Auto-generated from project name |
| `impact_level` | `IL4` |
| `classification.cui_markings` | `true` for IL4+, `false` for IL2 |
| `compliance.frameworks` | Inferred from impact level (IL4 = FedRAMP Moderate) |
| `profile.template` | `dod_baseline` for IL4+, `startup` for IL2 |
| `pipeline.platform` | Auto-detected from `git remote` |
| `pipeline.on_pr` | All checks enabled |
| `pipeline.on_merge` | SSP + SBOM + staging deploy |
| `deployment.cloud` | `aws_govcloud` for IL4+, `aws` for IL2 |
| `deployment.platform` | `k8s` |

---

## Environment Variable Overrides

Any `icdev.yaml` field can be overridden by environment variables for CI/CD flexibility:

```bash
# Override impact level in CI
ICDEV_IMPACT_LEVEL=IL5

# Override deployment target
ICDEV_DEPLOYMENT_CLOUD=aws_govcloud
ICDEV_DEPLOYMENT_REGION=us-gov-west-1

# Override gate thresholds for specific environments
ICDEV_GATE_MIN_COVERAGE=90
ICDEV_GATE_STIG_MAX_CAT2=0
```

Environment variables take precedence over `icdev.yaml` values, which take precedence over defaults.

---

## Validation

ICDEV validates `icdev.yaml` on every run. Common validation errors:

```
ERROR: impact_level IL6 requires classification.level = SECRET
ERROR: cjis framework requires impact_level >= IL4
ERROR: fedramp_high + deployment.cloud = aws (must be aws_govcloud for IL4+)
WARNING: No compliance frameworks specified for IL4 project
WARNING: cui_markings disabled but impact_level = IL5 (CUI markings required)
```

To validate without running anything:

```bash
python tools/project/validate_manifest.py --file icdev.yaml --json
```

Or ask Claude:

> Validate my icdev.yaml configuration
