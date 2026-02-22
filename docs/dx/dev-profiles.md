# Dev Profiles & Personalization

Dev profiles let each tenant organization define their coding standards, technology preferences, and compliance requirements once. These standards then automatically flow into every ICDEV operation — code generation, scaffolding, security scanning, and LLM prompt injection — without the developer needing to specify them each time.

---

## The Problem

Without dev profiles, every tenant uses the same global defaults:
- Python 3.11, 100-char lines, black formatting
- `python:3.11-slim` base image
- Generic test coverage thresholds
- No organization-specific naming conventions

A DoD tenant using Go with 120-char lines and trunk-based development gets the same output as a startup using Python with GitHub Flow. Dev profiles fix this.

---

## How It Works

### 1. Create a Profile

Either use a starter template or define custom standards:

```bash
# From a starter template
python tools/builder/dev_profile_manager.py \
  --scope tenant --scope-id "acme-corp" \
  --create --template dod_baseline --json

# Or custom
python tools/builder/dev_profile_manager.py \
  --scope tenant --scope-id "acme-corp" \
  --create --data '{
    "language": {"primary": "go", "secondary": ["python"], "min_version": "1.21"},
    "style": {"line_length": 120, "indent_size": 4, "naming_convention": "snake_case"},
    "testing": {"min_coverage": 90, "framework": "godog"}
  }' --created-by "admin@acme.gov" --json
```

Or just tell Claude:

> Create a dev profile for our tenant using the DoD baseline template. Override the primary language to Go and set line length to 120.

### 2. ICDEV Auto-Applies It

Once a profile exists, every ICDEV operation respects it automatically:

| Operation | What the Profile Affects |
|-----------|--------------------------|
| **Code generation** | Language, naming conventions, import style, type hints, docstring format |
| **Scaffolding** | Base image, Python version, line length, .gitignore entries, README sections |
| **Security scanning** | Crypto requirements (FIPS 140-2), secret rotation period, vulnerability thresholds |
| **Compliance** | Required frameworks, CUI markings, classification headers |
| **LLM prompts** | Claude receives the relevant profile dimensions for each task type |
| **Code review** | Review checks against profile standards (coverage, naming, style) |
| **PROFILE.md** | Auto-generated narrative document of all active standards |

### 3. Developers Don't Think About It

The developer writes:

> Build a REST API for user management

Claude generates Go code (not Python) with 120-char lines, snake_case naming, trunk-based branching guidance, and FIPS 140-2 compliant crypto — because that's what the tenant profile says.

---

## 5-Layer Cascade

Profiles are scoped hierarchically. Each layer can override or extend the layer above:

```
Platform (ICDEV defaults)
    └── Tenant (organization standards)
        └── Program (program-specific overrides)
            └── Project (project-specific overrides)
                └── User (personal preferences)
```

When resolving a profile for a project, ICDEV walks up the tree and merges each layer. The merge behavior depends on the dimension:

| Behavior | Description | Example |
|----------|-------------|---------|
| `override` | Child replaces parent entirely | Language: Go replaces Python |
| `merge` | Deep dict merge | Style: child adds `indent_size` to parent's `line_length` |
| `union` | Combine lists, deduplicate | Secondary languages: parent [Python] + child [Rust] = [Python, Rust] |
| `strictest_wins` | Keep the stricter value | Min coverage: parent 80% + child 90% = 90% |

### Cascade Resolution

```bash
# See the effective profile for a project (all layers merged)
python tools/builder/dev_profile_manager.py \
  --scope project --scope-id "proj-123" --resolve --json
```

The output includes **provenance** — where each value came from:

```json
{
  "language": {
    "primary": {"value": "go", "source": "tenant", "locked": false},
    "min_version": {"value": "1.21", "source": "project", "locked": false}
  },
  "security": {
    "crypto_standard": {"value": "FIPS 140-2", "source": "tenant", "locked": true}
  }
}
```

---

## Dimension Locks

Certain standards should not be overridden at lower levels. An ISSO can lock security dimensions at the tenant level, preventing any project from weakening them.

```bash
# ISSO locks the security dimension at tenant scope
python tools/builder/dev_profile_manager.py \
  --scope tenant --scope-id "acme-corp" \
  --lock --dimension-path "security" \
  --lock-role isso --locked-by "isso@acme.gov" --json

# Project tries to override crypto_standard → BLOCKED
# The locked value from tenant scope is enforced
```

Lock governance by role:

| Role | Can Lock | Can Unlock |
|------|----------|------------|
| ISSO | security, compliance | security, compliance |
| Architect | architecture, language | architecture, language |
| PM | operations, documentation, git | operations, documentation, git |
| Admin | all dimensions | all dimensions |

---

## Version History

Every profile change creates a new immutable version (ADR D183 — no UPDATE, only INSERT). This provides a full audit trail:

```bash
# View all versions
python tools/builder/dev_profile_manager.py \
  --scope tenant --scope-id "acme-corp" --history --json

# Diff between versions
python tools/builder/dev_profile_manager.py \
  --scope tenant --scope-id "acme-corp" --diff --v1 1 --v2 3 --json

# Rollback to a previous version (creates new version with old content)
python tools/builder/dev_profile_manager.py \
  --scope tenant --scope-id "acme-corp" \
  --rollback --target-version 1 --rolled-back-by "admin" --json
```

---

## Auto-Detection

Point ICDEV at an existing codebase and it will detect coding standards automatically:

```bash
# Scan a repository
python tools/builder/profile_detector.py --repo-path /path/to/repo --json
```

Detection scans:
- **File extensions** → primary/secondary languages
- **`.editorconfig`** → indent style, line length, charset
- **`package.json` / `pyproject.toml`** → dependencies, tools
- **Git log** → commit format, branch naming patterns
- **CI/CD configs** → pipeline tools, deployment targets
- **Python AST analysis** → naming conventions (snake_case vs camelCase)

Detection is **advisory only** (ADR D185) — detected values must be explicitly accepted before they become part of the profile.

---

## PROFILE.md

ICDEV generates a human-readable `PROFILE.md` document from the resolved profile. This is a narrative summary of all active standards, their provenance, and enforcement status.

```bash
python tools/builder/profile_md_generator.py \
  --scope project --scope-id "proj-123" \
  --output PROFILE.md --store --json
```

Example output:

```markdown
# PROFILE.md

> Auto-generated from dev profile cascade resolution.

## Overview

| Property | Value |
|----------|-------|
| Scope | project |
| Scope ID | proj-123 |
| Layers | platform → tenant → project |

## Language

- **Primary Language**: Go (source: tenant)
- **Secondary Languages**: Python (source: platform)
- **Minimum Version**: 1.21 (source: project)

## Style

- **Line Length**: 120 (source: tenant) 🔒 LOCKED
- **Indent Size**: 4 (source: platform)
- **Naming Convention**: snake_case (source: tenant)
...
```

PROFILE.md is **read-only** — edit the profile, not the document. Regenerate after changes.

---

## Starter Templates

Six sector-specific templates provide opinionated defaults:

| Template | Key | Sector | Key Standards |
|----------|-----|--------|---------------|
| DoD Baseline | `dod_baseline` | DoD/IC | Python/Go, FIPS 140-2, trunk-based, STIG, 100-char lines |
| FedRAMP Baseline | `fedramp_baseline` | Federal Civilian | Python/Java, FedRAMP Moderate+, squash merge, 120-char lines |
| Healthcare | `healthcare_baseline` | Healthcare | HIPAA+HITRUST, SOC 2, PHI markings, 90-day key rotation |
| Financial | `financial_baseline` | Financial Services | PCI DSS v4, SOC 2 Type II, AES-256, 90-day rotation |
| Law Enforcement | `law_enforcement` | Law Enforcement | CJIS+FIPS, air-gapped, Mattermost only, federal naming conventions |
| Startup | `startup` | Commercial | Minimal compliance, GitHub Flow, fast iteration, 88-char lines |

Templates are stored in `context/profiles/*.yaml` and can be customized per tenant.

---

## Integration with icdev.yaml

Set the dev profile in your project manifest:

```yaml
# icdev.yaml
profile:
  template: dod_baseline
  overrides:
    style:
      line_length: 120
    testing:
      min_coverage: 90
```

ICDEV loads this on initialization and creates the profile automatically. The developer never needs to run `dev_profile_manager.py` directly.

---

## 10 Dimension Categories

Each profile covers 10 dimension categories with configurable fields:

| Category | Key Fields | Example Values |
|----------|-----------|----------------|
| **Language** | primary, secondary, min_version, package_manager | `go`, `[python]`, `1.21`, `go mod` |
| **Style** | line_length, indent_size, naming_convention, docstring_format | `120`, `4`, `snake_case`, `google` |
| **Testing** | framework, min_coverage, mutation_testing, property_testing | `godog`, `90`, `false`, `false` |
| **Architecture** | patterns, max_complexity, dependency_injection | `[hexagonal, cqrs]`, `10`, `true` |
| **Security** | crypto_standard, secret_rotation_days, sast_tool, min_tls | `FIPS 140-2`, `90`, `gosec`, `1.3` |
| **Compliance** | required_frameworks, cui_markings, classification_headers | `[fedramp_high]`, `true`, `true` |
| **Operations** | container_base_image, k8s_resource_limits, health_check | `golang:1.21-alpine`, `true`, `true` |
| **Documentation** | readme_sections, adr_required, changelog_format | `[overview, api, deploy]`, `true`, `keep-a-changelog` |
| **Git** | commit_format, branch_strategy, merge_strategy, protected_branches | `conventional`, `trunk_based`, `squash`, `[main]` |
| **AI** | llm_provider, code_review_model, prefer_local, max_tokens | `bedrock`, `sonnet`, `false`, `4096` |

---

## Common Workflows

### "Our org just adopted ICDEV — set up standards for everyone"

1. Create tenant-level profile from template:
   > Create a dev profile for our tenant using the FedRAMP baseline

2. Lock security and compliance dimensions:
   > Lock the security and compliance dimensions at the tenant level. ISSO is the lock owner.

3. Each project inherits tenant standards automatically. Projects can override non-locked dimensions.

### "I want my project to use different standards from the org default"

Create a project-level override:
> Update our project's dev profile: change primary language to Rust, set line length to 100

The cascade resolution merges your project overrides with the tenant defaults. Locked dimensions stay as-is.

### "Show me what standards apply to my project right now"

> Resolve the dev profile for this project and show me where each value comes from

This shows the effective profile with provenance (which layer set each value) and lock status.

### "We're onboarding a new repo — detect its existing standards"

> Scan the repository at /path/to/repo and create a dev profile from what you find

ICDEV detects language, style, testing, and git conventions from the existing codebase. You review the detection results and accept them to create the profile.
