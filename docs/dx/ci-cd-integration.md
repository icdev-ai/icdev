# CI/CD Integration

ICDEV integrates with both GitHub Actions and GitLab CI/CD. The pipeline auto-attaches based on your `icdev.yaml` configuration and runs compliance, security, and testing checks on every push.

---

## How It Works

```
Developer pushes code
        |
        v
  Platform auto-detected (GitHub / GitLab)
        |
        v
  icdev.yaml loaded → pipeline stages determined
        |
        v
  +-- PR Checks (on_pr) ──────────────────+
  |  SAST, deps, secrets, CUI, STIG,      |
  |  unit tests, BDD, lint, format         |
  +────────────────────────────────────────+
        |
        v (on merge)
  +-- Merge Actions (on_merge) ────────────+
  |  SSP regen, POAM update, SBOM,         |
  |  staging deploy, cATO refresh          |
  +────────────────────────────────────────+
        |
        v (scheduled)
  +-- Periodic Checks (on_schedule) ───────+
  |  CVE triage, ISA expiry, dependency    |
  |  freshness, cATO evidence              |
  +────────────────────────────────────────+
```

---

## GitHub Actions

### Quick Setup

Add this workflow file to your repository:

```yaml
# .github/workflows/icdev.yml
name: ICDEV Compliance Pipeline

on:
  pull_request:
    branches: [main, master]
  push:
    branches: [main, master]
  schedule:
    - cron: '0 6 * * *'  # Daily at 6 AM UTC

jobs:
  icdev-pr-checks:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install ICDEV dependencies
        run: pip install -r requirements.txt

      - name: Initialize ICDEV database
        run: python tools/db/init_icdev_db.py

      - name: SAST Scan
        run: python tools/security/sast_runner.py --project-dir . --json > .tmp/sast.json

      - name: Dependency Audit
        run: python tools/security/dependency_auditor.py --project-dir . --json > .tmp/deps.json

      - name: Secret Detection
        run: python tools/security/secret_detector.py --project-dir . --json > .tmp/secrets.json

      - name: CUI Marking Validation
        run: python tools/compliance/cui_marker.py --validate --project-dir . --json > .tmp/cui.json

      - name: STIG Compliance Check
        run: python tools/compliance/stig_checker.py --project-id "${{ github.repository }}" --json > .tmp/stig.json

      - name: Unit Tests
        run: pytest tests/ -v --tb=short --junitxml=.tmp/test-results.xml

      - name: BDD Tests
        run: behave features/ --format json -o .tmp/bdd-results.json || true

      - name: Gate Evaluation
        run: |
          python -c "
          import json, sys
          stig = json.load(open('.tmp/stig.json'))
          cat1 = stig.get('summary', {}).get('cat1_count', 0)
          if cat1 > 0:
              print(f'BLOCKED: {cat1} CAT1 STIG findings')
              sys.exit(1)
          print('All gates passed')
          "

  icdev-merge-artifacts:
    if: github.event_name == 'push' && (github.ref == 'refs/heads/main' || github.ref == 'refs/heads/master')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install ICDEV dependencies
        run: pip install -r requirements.txt

      - name: Initialize ICDEV
        run: python tools/db/init_icdev_db.py

      - name: Generate SSP
        run: python tools/compliance/ssp_generator.py --project-id "${{ github.repository }}" --json

      - name: Generate SBOM
        run: python tools/compliance/sbom_generator.py --project-dir . --json

      - name: Upload Compliance Artifacts
        uses: actions/upload-artifact@v4
        with:
          name: compliance-artifacts
          path: artifacts/

  icdev-scheduled:
    if: github.event_name == 'schedule'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install ICDEV dependencies
        run: pip install -r requirements.txt

      - name: CVE Triage
        run: python tools/supply_chain/cve_triager.py --project-id "${{ github.repository }}" --sla-check --json

      - name: Dependency Freshness
        run: python tools/maintenance/dependency_scanner.py --project-id "${{ github.repository }}" --json
```

### Webhook Integration (Advanced)

For real-time issue-driven workflows, configure the ICDEV webhook server:

1. Deploy the webhook server:
   ```bash
   python tools/ci/triggers/webhook_server.py
   ```

2. Configure GitHub webhook:
   - URL: `https://your-icdev-instance/gh-webhook`
   - Events: Issues, Pull Requests, Issue Comments
   - Content type: `application/json`

3. Use workflow commands in issue bodies:
   ```
   /icdev_sdlc           # Full lifecycle: Plan → Build → Test → Review
   /icdev_plan           # Planning only
   /icdev_build run_id:abc12345   # Build (requires prior plan)
   ```

---

## GitLab CI/CD

### Quick Setup

Add this to your `.gitlab-ci.yml`:

```yaml
# .gitlab-ci.yml
stages:
  - security
  - compliance
  - test
  - artifacts
  - deploy

variables:
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"

cache:
  paths:
    - .cache/pip/

.icdev-setup: &icdev-setup
  image: python:3.11-slim
  before_script:
    - pip install -r requirements.txt
    - python tools/db/init_icdev_db.py

# ── Security Stage ──────────────────────────────────────
sast:
  <<: *icdev-setup
  stage: security
  script:
    - python tools/security/sast_runner.py --project-dir . --json > sast.json
  artifacts:
    reports:
      sast: sast.json

dependency_audit:
  <<: *icdev-setup
  stage: security
  script:
    - python tools/security/dependency_auditor.py --project-dir . --json > deps.json

secret_detection:
  <<: *icdev-setup
  stage: security
  script:
    - python tools/security/secret_detector.py --project-dir . --json > secrets.json

# ── Compliance Stage ────────────────────────────────────
stig_check:
  <<: *icdev-setup
  stage: compliance
  script:
    - python tools/compliance/stig_checker.py --project-id "$CI_PROJECT_ID" --json > stig.json
    - |
      python -c "
      import json, sys
      stig = json.load(open('stig.json'))
      cat1 = stig.get('summary', {}).get('cat1_count', 0)
      if cat1 > 0:
          print(f'BLOCKED: {cat1} CAT1 STIG findings')
          sys.exit(1)
      "

cui_check:
  <<: *icdev-setup
  stage: compliance
  script:
    - python tools/compliance/cui_marker.py --validate --project-dir . --json

# ── Test Stage ──────────────────────────────────────────
unit_tests:
  <<: *icdev-setup
  stage: test
  script:
    - pytest tests/ -v --tb=short --cov --cov-report=xml
  coverage: '/TOTAL.*\s+(\d+%)/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml

bdd_tests:
  <<: *icdev-setup
  stage: test
  script:
    - behave features/ --format json -o bdd-results.json
  allow_failure: true

# ── Artifact Generation (main branch only) ──────────────
generate_ssp:
  <<: *icdev-setup
  stage: artifacts
  script:
    - python tools/compliance/ssp_generator.py --project-id "$CI_PROJECT_ID" --json
  only:
    - main
    - master

generate_sbom:
  <<: *icdev-setup
  stage: artifacts
  script:
    - python tools/compliance/sbom_generator.py --project-dir . --json
  only:
    - main
    - master
  artifacts:
    paths:
      - artifacts/

# ── Deploy (main branch only) ──────────────────────────
deploy_staging:
  <<: *icdev-setup
  stage: deploy
  script:
    - python tools/infra/pipeline_generator.py --project-id "$CI_PROJECT_ID" --json
  only:
    - main
    - master
  when: manual
```

### GitLab Task Board Integration

ICDEV can monitor GitLab issues for workflow commands:

```bash
# Start the GitLab task monitor (polls every 20s)
python tools/ci/triggers/gitlab_task_monitor.py

# Tag issues with workflow commands:
# Add tag: {{icdev: icdev_sdlc}} → triggers full SDLC pipeline
# Add tag: {{icdev: icdev_plan}} → triggers planning only
```

---

## Pipeline Customization via icdev.yaml

The `pipeline` section of `icdev.yaml` controls which checks run and when:

```yaml
pipeline:
  # Only run these checks on PRs (remove any you don't need)
  on_pr:
    - sast
    - dependency_audit
    - secret_detection
    - cui_check
    - stig_check
    - unit_tests
    # - bdd_tests        # Commented out = disabled
    # - lint
    # - format_check

  # Only run these on merge to main
  on_merge:
    - ssp_generate
    - sbom_generate
    # - deploy_staging    # Manual deploy instead

  # Override gate thresholds
  gates:
    stig_max_cat1: 0      # Always block on CAT1
    stig_max_cat2: 3      # Allow up to 3 CAT2 in dev (tighten for prod)
    min_coverage: 80
    max_critical_vulns: 0
```

---

## Platform Auto-Detection

ICDEV auto-detects whether you're using GitHub or GitLab by inspecting `git remote get-url origin`:

| Remote URL Pattern | Detected Platform |
|-------------------|-------------------|
| `github.com/...` | GitHub |
| `gitlab.com/...` or `gitlab.*.mil/...` | GitLab |

Override with:
```yaml
pipeline:
  platform: gitlab  # Force GitLab even if remote is GitHub
```

---

## Security Considerations

- **Secrets**: Never store ICDEV API keys in pipeline YAML. Use GitHub Secrets or GitLab CI/CD Variables.
- **CUI markings**: The pipeline validates CUI markings are present but doesn't generate them. CUI generation happens at code-write time.
- **Air-gapped environments**: For IL5/IL6, use GitLab CI runners within the classified network. ICDEV tools work offline (all stdlib dependencies).
- **Audit trail**: All pipeline actions are logged to the ICDEV audit trail (append-only, NIST AU compliant).
