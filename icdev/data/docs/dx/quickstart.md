# Quickstart: ICDEV in 5 Minutes

This guide gets you from zero to a fully compliant project with ICDEV running automatically.

---

## Prerequisites

- Python 3.10+
- Git
- Claude Code CLI installed (`npm install -g @anthropic-ai/claude-code`)
- Access to your organization's ICDEV instance (or local installation)

---

## Step 1: Create Your Project Manifest

Drop an `icdev.yaml` file in your repository root:

```yaml
# icdev.yaml — This single file configures ICDEV for your project
version: 1
project:
  name: my-app
  id: proj-my-app
  type: microservice          # microservice | monolith | cli | data-pipeline | frontend
  language: python            # python | java | go | rust | csharp | typescript

impact_level: IL4              # IL2 | IL4 | IL5 | IL6

compliance:
  frameworks:
    - fedramp_moderate         # See full list below
  cui_markings: true

profile:
  template: dod_baseline       # dod_baseline | fedramp_baseline | healthcare_baseline |
                                # financial_baseline | law_enforcement | startup

pipeline:
  on_pr:
    - sast
    - dependency_audit
    - secret_detection
    - cui_check
    - unit_tests
  on_merge:
    - ssp_generate
    - sbom_generate
    - deploy_staging
```

That's it. One file. No other ICDEV configuration needed.

---

## Step 2: Initialize

Open Claude Code in your project directory:

```bash
claude
```

Then say:

> Initialize this project with ICDEV

Claude reads your `icdev.yaml`, creates the database, scaffolds compliance artifacts, and sets up your dev profile. You'll see output like:

```
Project "my-app" initialized
  - Impact Level: IL4 (CUI)
  - Compliance: FedRAMP Moderate (325 controls mapped)
  - Dev Profile: DoD Baseline loaded
  - Database: data/icdev.db created (146 tables)
  - CUI markings: enabled
```

---

## Step 3: Build Something

Just tell Claude what you want:

> Build a user authentication module with JWT tokens and role-based access

Claude will:
1. Write failing tests first (TDD RED phase)
2. Generate implementation code (GREEN phase)
3. Refactor for quality (REFACTOR phase)
4. Apply CUI markings to all generated files
5. Run security scans (SAST, dependency audit)
6. Map to NIST 800-53 controls (AC-2, AC-3, IA-2, etc.)
7. Update SSP and compliance artifacts

You never call a tool directly. Claude orchestrates everything.

---

## Step 4: Push and Watch

```bash
git add -A && git commit -m "feat: add user auth module" && git push
```

Your CI/CD pipeline (configured by `icdev.yaml`) automatically runs:
- Security scanning (bandit, pip-audit, detect-secrets)
- STIG compliance checks
- CUI marking validation
- Unit and BDD tests
- SBOM generation

Results appear as PR status checks. If anything fails, Claude can fix it:

> Fix the failing STIG check on the auth module

---

## Step 5: Check Compliance Status

Either ask Claude:

> What's our compliance status?

Or open the dashboard:

```bash
python tools/dashboard/app.py
# Navigate to http://localhost:5000
```

---

## What Happens Behind the Scenes

You don't need to know this, but for the curious:

| What You Do | What ICDEV Does Automatically |
|-------------|-------------------------------|
| Push code | SAST, dependency audit, secret detection, container scan |
| Create a PR | STIG check, CUI validation, test execution, compliance gate evaluation |
| Merge to main | SSP/POAM regeneration, SBOM update, cATO evidence refresh |
| Talk to Claude | GOTCHA orchestration: reads goals, calls tools, applies args, references context |
| Drop `icdev.yaml` | Auto-configures pipeline, loads dev profile, sets compliance posture |

---

## Available Compliance Frameworks

Use any combination in your `icdev.yaml`:

| Framework | Key | Impact Levels |
|-----------|-----|---------------|
| FedRAMP Moderate | `fedramp_moderate` | IL2-IL5 |
| FedRAMP High | `fedramp_high` | IL4-IL6 |
| CMMC Level 2 | `cmmc_l2` | IL4-IL5 |
| CMMC Level 3 | `cmmc_l3` | IL5-IL6 |
| NIST 800-171 | `nist_800_171` | IL4-IL5 |
| CJIS Security Policy | `cjis` | IL4-IL5 |
| HIPAA Security Rule | `hipaa` | IL4-IL5 |
| HITRUST CSF | `hitrust` | IL4-IL5 |
| SOC 2 Type II | `soc2` | IL2-IL5 |
| PCI DSS v4 | `pci_dss` | IL2-IL5 |
| ISO 27001:2022 | `iso27001` | IL2-IL5 |
| DoD MOSA | `mosa` | IL4-IL6 |

ICDEV auto-deduplicates controls across frameworks via the crosswalk engine — implementing AC-2 once satisfies FedRAMP, CMMC, CJIS, HIPAA, and more simultaneously.

---

## Available Starter Templates

Dev profile templates set your coding standards automatically:

| Template | Sector | Key Standards |
|----------|--------|---------------|
| `dod_baseline` | DoD/IC | Python/Go, FIPS 140-2, trunk-based, STIG hardened |
| `fedramp_baseline` | Federal Civilian | Python/Java, FedRAMP Moderate+, squash merge |
| `healthcare_baseline` | Healthcare | HIPAA+HITRUST, SOC 2, PHI markings |
| `financial_baseline` | Financial Services | PCI DSS v4, SOC 2 Type II, 90-day rotation |
| `law_enforcement` | Law Enforcement | CJIS+FIPS, air-gapped, Mattermost only |
| `startup` | Commercial/Startup | Minimal compliance, GitHub Flow, fast iteration |

---

## Next Steps

- [Integration Tiers](integration-tiers.md) — Understand all three ways to use ICDEV
- [Claude Code Guide](claude-code-guide.md) — Master the conversational interface
- [Dev Profiles](dev-profiles.md) — Customize coding standards per tenant/project
- [CI/CD Integration](ci-cd-integration.md) — Set up automatic pipeline integration
