# [TEMPLATE: CUI // SP-CTI]
# Goal: DevSecOps CI/CD Pipeline Pattern

## Purpose

Deploy and configure a production-grade DevSecOps CI/CD pipeline from the ICDEV™
starter template. This pattern covers lint, unit tests, SAST, sandbox isolation,
BDD, Docker build, and Helm lint — each with a defined security gate. Used by the
AISG Pattern Library (`id='devsecops-cicd'`) to give teams with no prior pipeline
experience a working, FedRAMP-ready pipeline in under 30 minutes.

---

## When to Use

- New GitLab project needs a DevSecOps CI/CD baseline
- Existing pipeline lacks SAST, sandbox isolation, or security gate enforcement
- Team is onboarding to ICDEV™ and needs a working pipeline fast
- Pattern deployed via `/icdev-pattern deploy devsecops-cicd`

---

## Prerequisites

- [ ] GitLab project with `Maintainer` access (for CI/CD variable configuration)
- [ ] Runner with Docker-in-Docker support (for `security:sandbox` and `docker:build` stages)
- [ ] Python 3.11+ available on runner
- [ ] Ollama accessible at `$OLLAMA_HOST` (for `kanban-build` and `kanban-chore` jobs)
- [ ] `requirements.txt` present in repo root
- [ ] `deploy/helm/` present for `helm:lint` stage

---

## Starter Files

The `.gitlab-ci.yml` in this repository is the **authoritative starter file**.
Do not modify the stage list or gate conditions without understanding downstream impact.

### Pipeline Stages (in order)

| Stage | Job(s) | Gate Policy |
|-------|--------|-------------|
| `lint` | `lint` | Blocks on E/F/W violations — `allow_failure: false` |
| `test` | `test:unit` | JUnit report; blocks on test failures |
| `security` | `security:bandit`, `security:openclaw-gate` | bandit allows failure; openclaw blocks if enabled |
| `sandbox` | `security:sandbox` | Blocks on container execution failure (MR/non-main branches) |
| `bdd` | `bdd` | BDD report; allows failure (informational) |
| `docker` | `docker:build` | Builds 8 STIG-hardened images |
| `helm` | `helm:lint` | Blocks on Helm chart validation errors |

### Kanban Pipeline Jobs (triggered via API)

| Job | Trigger Condition | LLM |
|-----|-------------------|-----|
| `kanban-plan` | `KANBAN_TASK_TYPE == plan\|research\|architect` | Claude (remote) |
| `kanban-build` | `KANBAN_TASK_TYPE == build\|fix` | Ollama qwen3.5 (local) |
| `kanban-test` | `KANBAN_TASK_TYPE == test` | None |
| `kanban-deploy` | `KANBAN_TASK_TYPE == deploy` | None |
| `kanban-chore` | `KANBAN_TASK_TYPE == chore` | Ollama qwen3.5 (local) |
| `kanban-scan` | `KANBAN_TASK_TYPE == scan\|compliance` | None |

---

## Process

### Step 1: Configure GitLab CI/CD Variables

Set the following variables in **Settings → CI/CD → Variables**:

**Required (pipeline will fail without these):**

| Variable | Scope | Description |
|----------|-------|-------------|
| `ICDEV_CLASSIFICATION` | All | `CUI` for IL4/IL5, `SECRET` for IL6 |
| `ICDEV_IMPACT_LEVEL` | All | `IL2`, `IL4`, `IL5`, or `IL6` |
| `ICDEV_DASHBOARD_SECRET` | All | Flask session secret (random 32+ chars) |

**Required for Kanban pipeline jobs:**

| Variable | Scope | Description |
|----------|-------|-------------|
| `KANBAN_TASK_ID` | Pipeline trigger only | Task UUID — set by trigger API |
| `KANBAN_TASK_TYPE` | Pipeline trigger only | `plan`, `build`, `test`, `deploy`, `chore`, `scan` |
| `KANBAN_PROMPT` | Pipeline trigger only | Base64-encoded task prompt |
| `KANBAN_LLM_MODE` | Pipeline trigger only | `claude`, `ollama`, or `none` |
| `OLLAMA_HOST` | All | `http://<ollama-server>:11434` for build/chore jobs |

**Required for Claude-powered plan jobs:**

| Variable | Scope | Description |
|----------|-------|-------------|
| `ANTHROPIC_API_KEY` | All (masked) | API key for `kanban-plan` jobs |

**Optional — enable specific gates:**

| Variable | Value | Effect |
|----------|-------|--------|
| `ICDEV_OPENCLAW_ENABLED` | `true` | Activates OpenClaw marketplace bridge gate |
| `ICDEV_NO_LLM` | `true` | Forces all jobs to no-LLM mode (air-gap) |
| `LLM_TWO_TIER_ENABLED` | `false` | Disables cloud LLM fallback (Ollama-only) |
| `ICDEV_BYOK_ENABLED` | `true` | Enables per-user BYOK key management |

**Tool:** Review `docs/operations/cicd-env-vars.md` for full variable catalog with examples.

```bash
# Verify all required variables are set before first pipeline run
python tools/ci/pipeline_config_generator.py --dir . --platform gitlab --dry-run --json
```

**Expected output:** JSON with `"errors": []`. Any entry in `errors` means a required
configuration is missing.

**Error handling:**
- `"No valid icdev.yaml found"` → copy `.env.example` to `.env`, run `python tools/db/init_icdev_db.py`
- Runner missing Docker-in-Docker → configure runner with `privileged = true` in `config.toml`

---

### Step 2: Customize Stage Scripts

**Tool:** Edit `.gitlab-ci.yml` directly. Key customization points:

#### 2a. Target Python version
```yaml
default:
  image: python:3.11-slim  # Change to 3.12-slim for newer projects
```

#### 2b. Ruff lint scope
```yaml
lint:
  script:
    - pip install ruff
    - ruff check tools/ tests/ --select E,F,W  # Add src/ if applicable
```

#### 2c. Pytest scope
```yaml
test:unit:
  script:
    - pytest tests/ -v --tb=short --ignore=tests/e2e --junitxml=report.xml
    # Add --cov=tools --cov-report=xml for coverage
```

#### 2d. Security thresholds (bandit)
```yaml
security:bandit:
  script:
    - bandit -r tools/ -ll -f json -o bandit-report.json
    # Change -ll (LOW+) to -lll (HIGH only) for production hardening
```

#### 2e. Helm chart path
```yaml
helm:lint:
  script:
    - helm lint deploy/helm/      # Update path if chart moved
    - helm template icdev deploy/helm/ > /dev/null
```

**Verify:** After edits, run:
```bash
# Validate YAML syntax
python -c "import yaml; yaml.safe_load(open('.gitlab-ci.yml'))" && echo "YAML valid"
```

---

### Step 3: Verify Each Stage Gate

Run each stage validation locally before pushing:

```bash
# Stage 1: lint
pip install ruff
ruff check tools/ tests/ --select E,F,W
# Expected: exit 0

# Stage 2: test
pytest tests/ -v --tb=short --ignore=tests/e2e --junitxml=report.xml
# Expected: exit 0, JUnit XML written

# Stage 3: security
pip install bandit
bandit -r tools/ -ll -f json -o bandit-report.json
# Expected: report written (exit 0 or 1 — allowed to fail)

# Stage 4: sandbox (requires Docker)
python tools/security/sandbox_executor.py --health --json
# Expected: {"healthy": true}

# Stage 5: bdd
pip install behave
behave features/ --no-capture --format json -o behave-report.json || true
# Expected: JSON report written

# Stage 6: docker (requires Docker)
docker build -f docker/Dockerfile.agent-base -t icdev/agent-base:test .
# Expected: exit 0

# Stage 7: helm (requires helm CLI)
helm lint deploy/helm/ && helm template icdev deploy/helm/ > /dev/null
# Expected: exit 0
```

**Tool:** Full gate validation shortcut:
```bash
python tools/testing/health_check.py --json
```

**Expected output:** `"overall_health": "healthy"` with all component statuses passing.

**Error handling:**
- Lint failures → fix violations reported by ruff, commit, re-run
- Test failures → check JUnit report (`report.xml`) for failing test names
- Bandit issues → review `bandit-report.json`, add `# nosec` for known false positives with justification
- Helm errors → run `helm lint deploy/helm/ --debug` for detailed validation output

---

### Step 4: Push and Monitor First Pipeline Run

```bash
git add .gitlab-ci.yml
git commit -m "ci: configure DevSecOps pipeline (aisg-pattern-devsecops-cicd)"
git push origin HEAD
```

Monitor pipeline at: `https://<gitlab-host>/<namespace>/<project>/-/pipelines`

**Expected:** All stages green except `security:bandit` (allowed_failure: true) and `bdd`
(allowed_failure: true).

---

## Success Criteria

- [ ] All required CI/CD variables set in GitLab project settings
- [ ] `lint` stage passes (`ruff` exit 0)
- [ ] `test:unit` stage passes (JUnit report has 0 failures)
- [ ] `security:bandit` stage produces report artifact (`bandit-report.json`)
- [ ] `security:sandbox` stage passes (sandbox health check healthy)
- [ ] `docker:build` stage completes all 8 images
- [ ] `helm:lint` stage validates chart without errors
- [ ] `kanban-*` jobs trigger correctly when `KANBAN_TASK_ID` is set via pipeline API

---

## Tools Used

| Tool | Purpose |
|------|---------|
| `tools/ci/pipeline_config_generator.py` | Validate / generate pipeline from `icdev.yaml` |
| `tools/testing/health_check.py` | Local pre-push gate validation |
| `tools/security/sandbox_executor.py` | LLM sandbox isolation check |
| `tools/security/sast_runner.py` | SAST scan driver |
| `tools/compliance/export.py` | Compliance artifact export for scan jobs |
| `tools/dx/companion.py` | Sync pipeline config to all AI platform configs |

## Related Goals

- `goals/devsecops_workflow.md` — Full DevSecOps profile detection and lifecycle
- `goals/parallel_cicd.md` — Parallel execution with git worktree isolation
- `goals/cicd_integration.md` — GitHub + GitLab webhook and polling integration
- `goals/deploy_workflow.md` — IaC generation, Terraform, Helm deployment
