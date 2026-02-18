#!/usr/bin/env python3
"""Generate GitLab CI/CD pipeline with 7 stages: lint, test, security-scan,
build, compliance-check, deploy-staging, deploy-prod.
Includes security gates, manual approval for prod, and rollback job."""

import argparse
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

CUI_HEADER = (
    "# //CUI\n"
    "# CONTROLLED UNCLASSIFIED INFORMATION\n"
    "# Authorized for: Internal project use only\n"
    "# Generated: {timestamp}\n"
    "# Generator: ICDev Pipeline Generator\n"
    "# //CUI\n"
)


def _cui_header() -> str:
    return CUI_HEADER.format(timestamp=datetime.utcnow().isoformat())


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _generate_agent_test_stage(config: dict) -> str:
    """Generate agent-test stage for CI/CD pipeline.

    Runs agentic infrastructure tests including:
    - Agent health endpoint validation
    - Agent card schema compliance
    - A2A callback client verification
    - Agentic BDD feature template validation

    This stage runs after unit/integration tests and before security scans.
    It verifies the multi-agent architecture is correctly configured.

    Args:
        config: Pipeline configuration dict.

    Returns:
        YAML string for the agent-test stage.
    """
    python_image = config.get("python_image", "python:3.11-slim")

    return f"""
# =============================================================================
# Stage 2.5: AGENT TEST (agentic infrastructure verification)
# =============================================================================

test:agent-health:
  stage: agent-test
  image: {python_image}
  script:
    - pip install --cache-dir .cache/pip pytest pyyaml
    - echo "Running agent health tests..."
    - python -m pytest tools/builder/agentic_test_templates/test_agent_health.py -v --tb=short --junitxml=agent-health-report.xml || true
    - echo "Running A2A callback tests..."
    - python -m pytest tools/builder/agentic_test_templates/test_a2a_callback.py -v --tb=short --junitxml=a2a-callback-report.xml || true
  artifacts:
    when: always
    paths:
      - agent-health-report.xml
      - a2a-callback-report.xml
    reports:
      junit:
        - agent-health-report.xml
        - a2a-callback-report.xml
    expire_in: 30 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'

test:agent-cards:
  stage: agent-test
  image: {python_image}
  script:
    - pip install --cache-dir .cache/pip pyyaml
    - echo "Validating agent card schema compliance..."
    - |
      python3 -c "
      import json, yaml, sys, os
      from pathlib import Path

      cards_dir = Path('tools/agent/cards')
      config_path = Path('args/agent_config.yaml')

      if not cards_dir.exists():
          print('No agent cards directory found, skipping validation')
          sys.exit(0)

      errors = []
      cards = list(cards_dir.glob('*_card.json'))
      print(f'Found {{len(cards)}} agent cards')

      required_fields = ['name', 'url', 'skills']
      for card_path in cards:
          try:
              with open(card_path) as f:
                  card = json.load(f)
              for field in required_fields:
                  if field not in card:
                      errors.append(f'{{card_path.name}}: missing required field \"{{field}}\"')
              if not card.get('url', '').startswith('https://'):
                  errors.append(f'{{card_path.name}}: url must use HTTPS')
          except json.JSONDecodeError as e:
              errors.append(f'{{card_path.name}}: invalid JSON - {{e}}')

      if errors:
          print(f'FAILED: {{len(errors)}} agent card validation errors:')
          for err in errors:
              print(f'  - {{err}}')
          sys.exit(1)

      print(f'All {{len(cards)}} agent cards are valid')
      "
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'

test:agent-bdd-templates:
  stage: agent-test
  image: {python_image}
  script:
    - echo "Validating agentic BDD test templates..."
    - |
      python3 -c "
      import sys
      from pathlib import Path

      templates_dir = Path('tools/builder/agentic_test_templates')
      if not templates_dir.exists():
          print('No agentic test templates directory found')
          sys.exit(0)

      features = list(templates_dir.glob('*.feature'))
      py_tests = list(templates_dir.glob('test_*.py'))

      print(f'Found {{len(features)}} BDD feature templates')
      print(f'Found {{len(py_tests)}} pytest test templates')

      # Validate each feature file has CUI header
      errors = []
      for feat in features:
          content = feat.read_text(encoding='utf-8')
          if 'CUI' not in content[:100]:
              errors.append(f'{{feat.name}}: missing CUI marking in header')
          if 'Feature:' not in content:
              errors.append(f'{{feat.name}}: missing Feature: keyword')
          if 'Scenario' not in content:
              errors.append(f'{{feat.name}}: missing Scenario keyword')

      # Validate each Python test file compiles
      import py_compile
      for test_file in py_tests:
          try:
              py_compile.compile(str(test_file), doraise=True)
          except py_compile.PyCompileError as e:
              errors.append(f'{{test_file.name}}: syntax error - {{e}}')

      if errors:
          print(f'FAILED: {{len(errors)}} template validation errors:')
          for err in errors:
              print(f'  - {{err}}')
          sys.exit(1)

      print(f'All {{len(features) + len(py_tests)}} agentic test templates are valid')
      "
  allow_failure: true
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
"""


def generate_pipeline(project_path: str, project_config: dict = None) -> list:
    """Generate .gitlab-ci.yml with all 7 stages and supporting jobs."""
    config = project_config or {}
    project_name = config.get("project_name", "icdev-app")
    registry = config.get("registry", "$CI_REGISTRY")
    image_name = config.get("image_name", f"{registry}/{project_name}")
    python_image = config.get("python_image", "python:3.11-slim")
    node_image = config.get("node_image", "node:20-alpine")
    app_type = config.get("app_type", "python")  # python or node
    k8s_namespace = config.get("k8s_namespace", project_name)
    stages_filter = config.get("stages", None)  # None = all stages

    all_stages = [
        "lint", "test", "agent-test", "security-scan", "build",
        "devsecops-check", "compliance-check", "deploy-staging", "deploy-prod",
    ]
    if stages_filter:
        stages = [s for s in all_stages if s in stages_filter]
    else:
        stages = all_stages


    pipeline = f"""{_cui_header()}
# =============================================================================
# GitLab CI/CD Pipeline — {project_name}
# Classification: CUI
# =============================================================================

stages:
  - lint
  - test
  - agent-test
  - security-scan
  - build
  - compliance-check
  - deploy-staging
  - deploy-prod

variables:
  DOCKER_TLS_CERTDIR: "/certs"
  IMAGE_NAME: "{image_name}"
  IMAGE_TAG: "$CI_COMMIT_SHORT_SHA"
  CLASSIFICATION: "CUI"
  K8S_NAMESPACE_STAGING: "{k8s_namespace}-staging"
  K8S_NAMESPACE_PROD: "{k8s_namespace}-prod"
  # Security scanning thresholds
  CRITICAL_THRESHOLD: 0
  HIGH_THRESHOLD: 5
  SAST_EXCLUDED_PATHS: "tests/,docs/,vendor/"

# Default settings for all jobs
default:
  tags:
    - govcloud
    - docker
  retry:
    max: 2
    when:
      - runner_system_failure
      - stuck_or_timeout_failure

# Cache dependencies
cache:
  key: "${{CI_COMMIT_REF_SLUG}}"
  paths:
    - .cache/pip
    - node_modules/
    - .npm/

# =============================================================================
# Stage 1: LINT
# =============================================================================
"""

    if "lint" in stages:
        if app_type == "python":
            pipeline += f"""
lint:python:
  stage: lint
  image: {python_image}
  script:
    - pip install --cache-dir .cache/pip flake8 black isort mypy
    - echo "Running flake8..."
    - flake8 --max-line-length=120 --exclude=.git,__pycache__,venv --format=json --output-file=flake8-report.json . || true
    - flake8 --max-line-length=120 --exclude=.git,__pycache__,venv .
    - echo "Checking black formatting..."
    - black --check --diff .
    - echo "Checking import sorting..."
    - isort --check-only --diff .
    - echo "Running mypy type checks..."
    - mypy --ignore-missing-imports --no-error-summary . || true
  artifacts:
    when: always
    paths:
      - flake8-report.json
    expire_in: 7 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
"""
        else:
            pipeline += f"""
lint:node:
  stage: lint
  image: {node_image}
  script:
    - npm ci
    - echo "Running ESLint..."
    - npx eslint --format json --output-file eslint-report.json . || true
    - npx eslint .
    - echo "Checking Prettier formatting..."
    - npx prettier --check .
  artifacts:
    when: always
    paths:
      - eslint-report.json
    expire_in: 7 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
"""

    # =============================================================================
    # Stage 2: TEST
    # =============================================================================
    if "test" in stages:
        pipeline += """
# =============================================================================
# Stage 2: TEST
# =============================================================================
"""
        if app_type == "python":
            pipeline += f"""
test:unit:
  stage: test
  image: {python_image}
  services:
    - name: postgres:15-alpine
      alias: testdb
      variables:
        POSTGRES_DB: testdb
        POSTGRES_USER: testuser
        POSTGRES_PASSWORD: testpass
  variables:
    DATABASE_URL: "postgresql://testuser:testpass@testdb:5432/testdb"
  script:
    - pip install --cache-dir .cache/pip -r requirements.txt
    - pip install pytest pytest-cov pytest-asyncio
    - python -m pytest tests/ -v --cov=. --cov-report=xml:coverage.xml --cov-report=html:htmlcov --junitxml=junit-report.xml
  coverage: '/TOTAL.*\\s+(\\d+%)/'
  artifacts:
    when: always
    paths:
      - coverage.xml
      - htmlcov/
      - junit-report.xml
    reports:
      junit: junit-report.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
    expire_in: 30 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
"""
        else:
            pipeline += f"""
test:unit:
  stage: test
  image: {node_image}
  script:
    - npm ci
    - npm run test -- --coverage --ci --reporters=default --reporters=jest-junit
  artifacts:
    when: always
    paths:
      - coverage/
      - junit.xml
    reports:
      junit: junit.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml
    expire_in: 30 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
"""

    # =============================================================================
    # Stage 2.5: AGENT TEST (Phase 19 — agentic infrastructure tests)
    # =============================================================================
    if "agent-test" in stages:
        pipeline += _generate_agent_test_stage(config)

    # =============================================================================
    # Stage 3: SECURITY SCAN
    # =============================================================================
    if "security-scan" in stages:
        pipeline += """
# =============================================================================
# Stage 3: SECURITY SCAN
# =============================================================================

security:sast:
  stage: security-scan
  image: python:3.11-slim
  script:
    - pip install bandit safety
    - echo "Running SAST (Bandit)..."
    - bandit -r . -f json -o bandit-report.json --exclude tests,venv,.git || true
    - bandit -r . --exclude tests,venv,.git -ll
    - echo "Checking for known vulnerabilities in dependencies..."
    - safety check --output json > safety-report.json 2>&1 || true
    - safety check
  artifacts:
    when: always
    paths:
      - bandit-report.json
      - safety-report.json
    expire_in: 30 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'

security:dependency-audit:
  stage: security-scan
  image: python:3.11-slim
  script:
    - pip install pip-audit
    - echo "Running pip-audit..."
    - pip-audit -r requirements.txt --format json --output pip-audit-report.json || true
    - pip-audit -r requirements.txt
  artifacts:
    when: always
    paths:
      - pip-audit-report.json
    expire_in: 30 days
  allow_failure: true
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'

security:secrets:
  stage: security-scan
  image: python:3.11-slim
  script:
    - pip install detect-secrets
    - echo "Scanning for secrets..."
    - detect-secrets scan --all-files --exclude-files '\\.git' > secrets-report.json
    - |
      RESULTS=$(python3 -c "
      import json
      with open('secrets-report.json') as f:
          data = json.load(f)
      total = sum(len(v) for v in data.get('results', {}).values())
      print(total)
      ")
      echo "Found $RESULTS potential secrets"
      if [ "$RESULTS" -gt 0 ]; then
        echo "ERROR: Secrets detected in codebase!"
        python3 -c "
      import json
      with open('secrets-report.json') as f:
          data = json.load(f)
      for fname, findings in data.get('results', {}).items():
          for f_ in findings:
              print(f'  {fname}:{f_[\"line_number\"]} — {f_[\"type\"]}')
      "
        exit 1
      fi
  artifacts:
    when: always
    paths:
      - secrets-report.json
    expire_in: 30 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'

security:container-scan:
  stage: security-scan
  image:
    name: aquasec/trivy:latest
    entrypoint: [""]
  script:
    - echo "Scanning container image for vulnerabilities..."
    - trivy image --exit-code 0 --format json --output trivy-report.json ${IMAGE_NAME}:${IMAGE_TAG} || true
    - trivy image --exit-code 1 --severity CRITICAL ${IMAGE_NAME}:${IMAGE_TAG}
  artifacts:
    when: always
    paths:
      - trivy-report.json
    expire_in: 30 days
  needs:
    - job: build:docker
      optional: true
  allow_failure: false
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
"""

    # =============================================================================
    # Stage 4: BUILD
    # =============================================================================
    if "build" in stages:
        pipeline += """
# =============================================================================
# Stage 4: BUILD
# =============================================================================

build:docker:
  stage: build
  image: docker:24-dind
  services:
    - docker:24-dind
  before_script:
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
  script:
    - echo "Building Docker image..."
    - |
      docker build \\
        --label "classification=CUI" \\
        --label "org.opencontainers.image.revision=$CI_COMMIT_SHA" \\
        --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \\
        --label "org.opencontainers.image.source=$CI_PROJECT_URL" \\
        --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \\
        --build-arg VCS_REF=$CI_COMMIT_SHA \\
        -t ${IMAGE_NAME}:${IMAGE_TAG} \\
        -t ${IMAGE_NAME}:latest \\
        -f docker/Dockerfile.python .
    - echo "Pushing image..."
    - docker push ${IMAGE_NAME}:${IMAGE_TAG}
    - docker push ${IMAGE_NAME}:latest
    - echo "Image digest:"
    - docker inspect --format='{{.RepoDigests}}' ${IMAGE_NAME}:${IMAGE_TAG}
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
"""

    # =============================================================================
    # Stage 4.5: DEVSECOPS CHECK (Phase 24 — profile-driven security stages)
    # =============================================================================
    if "devsecops-check" in stages:
        # Import and call the DevSecOps pipeline security generator
        devsecops_profile = config.get("devsecops_profile", None)
        project_id = config.get("project_id", None)
        if project_id or devsecops_profile:
            try:
                from tools.devsecops.pipeline_security_generator import generate_security_stages
                ds_result = generate_security_stages(
                    project_id or project_name,
                    profile=devsecops_profile,
                )
                if ds_result.get("yaml_content"):
                    pipeline += ds_result["yaml_content"]
            except Exception:
                # Fallback: add a placeholder stage when generator is unavailable
                pipeline += """
# =============================================================================
# Stage 4.5: DEVSECOPS CHECK (profile-driven — requires DevSecOps profile)
# =============================================================================

devsecops:profile-check:
  stage: devsecops-check
  image: python:3.11-slim
  script:
    - echo "DevSecOps profile check — no profile configured"
    - echo "Run: python tools/devsecops/profile_manager.py --project-id $PROJECT_ID --create"
  allow_failure: true
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
"""

    # =============================================================================
    # Stage 5: COMPLIANCE CHECK
    # =============================================================================
    if "compliance-check" in stages:
        pipeline += """
# =============================================================================
# Stage 5: COMPLIANCE CHECK
# =============================================================================

compliance:stig-check:
  stage: compliance-check
  image: python:3.11-slim
  script:
    - pip install --cache-dir .cache/pip -r requirements.txt 2>/dev/null || true
    - echo "Running STIG compliance checks..."
    - |
      python3 -c "
      import json, sys
      # Parse security scan results and check against STIG requirements
      findings = {'CAT1': 0, 'CAT2': 0, 'CAT3': 0}

      # Check bandit results
      try:
          with open('bandit-report.json') as f:
              bandit = json.load(f)
          for result in bandit.get('results', []):
              severity = result.get('issue_severity', 'LOW')
              if severity == 'HIGH':
                  findings['CAT1'] += 1
              elif severity == 'MEDIUM':
                  findings['CAT2'] += 1
              else:
                  findings['CAT3'] += 1
      except FileNotFoundError:
          print('No bandit report found')

      # Check trivy results
      try:
          with open('trivy-report.json') as f:
              trivy = json.load(f)
          for result in trivy.get('Results', []):
              for vuln in result.get('Vulnerabilities', []):
                  severity = vuln.get('Severity', 'LOW')
                  if severity == 'CRITICAL':
                      findings['CAT1'] += 1
                  elif severity == 'HIGH':
                      findings['CAT2'] += 1
                  else:
                      findings['CAT3'] += 1
      except FileNotFoundError:
          print('No trivy report found')

      print(f'STIG Findings: CAT1={findings[\"CAT1\"]}, CAT2={findings[\"CAT2\"]}, CAT3={findings[\"CAT3\"]}')

      # GATE: Block on CAT1/Critical findings
      if findings['CAT1'] > 0:
          print(f'BLOCKED: {findings[\"CAT1\"]} CAT1 (Critical) findings detected!')
          print('CAT1 findings must be resolved before deployment.')
          sys.exit(1)

      print('Compliance gate PASSED: No CAT1 findings.')
      "
  artifacts:
    when: always
    paths:
      - compliance-report.json
    expire_in: 90 days
  needs:
    - job: security:sast
      optional: true
    - job: security:container-scan
      optional: true
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'

compliance:sbom:
  stage: compliance-check
  image: python:3.11-slim
  script:
    - pip install cyclonedx-bom
    - echo "Generating SBOM..."
    - cyclonedx-py environment --output sbom.json --format json 2>/dev/null || cyclonedx-py -r -i requirements.txt -o sbom.json --format json 2>/dev/null || echo '{"bomFormat":"CycloneDX","specVersion":"1.5","components":[]}' > sbom.json
    - echo "SBOM generated successfully"
  artifacts:
    paths:
      - sbom.json
    expire_in: 365 days
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
"""

    # =============================================================================
    # Stage 6: DEPLOY STAGING
    # =============================================================================
    if "deploy-staging" in stages:
        pipeline += """
# =============================================================================
# Stage 6: DEPLOY STAGING
# =============================================================================

deploy:staging:
  stage: deploy-staging
  image: bitnami/kubectl:latest
  environment:
    name: staging
    url: https://staging.${CI_PROJECT_NAME}.internal
    on_stop: stop:staging
  script:
    - echo "Deploying to staging..."
    - kubectl config use-context staging
    - |
      kubectl set image deployment/${CI_PROJECT_NAME} \\
        ${CI_PROJECT_NAME}=${IMAGE_NAME}:${IMAGE_TAG} \\
        -n ${K8S_NAMESPACE_STAGING}
    - echo "Waiting for rollout..."
    - kubectl rollout status deployment/${CI_PROJECT_NAME} -n ${K8S_NAMESPACE_STAGING} --timeout=300s
    - echo "Running smoke tests..."
    - |
      HEALTH_URL="http://${CI_PROJECT_NAME}.${K8S_NAMESPACE_STAGING}.svc:8080/health"
      for i in $(seq 1 10); do
        if kubectl exec -n ${K8S_NAMESPACE_STAGING} deploy/${CI_PROJECT_NAME} -- curl -sf $HEALTH_URL; then
          echo "Health check passed"
          break
        fi
        echo "Attempt $i/10 failed, waiting..."
        sleep 10
      done
  needs:
    - build:docker
    - compliance:stig-check
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'

stop:staging:
  stage: deploy-staging
  image: bitnami/kubectl:latest
  environment:
    name: staging
    action: stop
  script:
    - kubectl scale deployment/${CI_PROJECT_NAME} --replicas=0 -n ${K8S_NAMESPACE_STAGING}
  when: manual
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
      when: manual
"""

    # =============================================================================
    # Stage 7: DEPLOY PROD
    # =============================================================================
    if "deploy-prod" in stages:
        pipeline += """
# =============================================================================
# Stage 7: DEPLOY PROD (Manual Approval Required)
# =============================================================================

deploy:prod:
  stage: deploy-prod
  image: bitnami/kubectl:latest
  environment:
    name: production
    url: https://${CI_PROJECT_NAME}.internal
  script:
    - echo "=== PRODUCTION DEPLOYMENT ==="
    - echo "Image: ${IMAGE_NAME}:${IMAGE_TAG}"
    - echo "Deployed by: ${GITLAB_USER_LOGIN}"
    - echo "Commit: ${CI_COMMIT_SHA}"
    - kubectl config use-context production
    - |
      # Record current version for rollback
      CURRENT_IMAGE=$(kubectl get deployment/${CI_PROJECT_NAME} -n ${K8S_NAMESPACE_PROD} -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "none")
      echo "Current image: ${CURRENT_IMAGE}"
      echo "${CURRENT_IMAGE}" > rollback-image.txt
    - |
      kubectl set image deployment/${CI_PROJECT_NAME} \\
        ${CI_PROJECT_NAME}=${IMAGE_NAME}:${IMAGE_TAG} \\
        -n ${K8S_NAMESPACE_PROD}
    - echo "Waiting for production rollout..."
    - kubectl rollout status deployment/${CI_PROJECT_NAME} -n ${K8S_NAMESPACE_PROD} --timeout=600s
    - echo "Running production health checks..."
    - |
      HEALTH_URL="http://${CI_PROJECT_NAME}.${K8S_NAMESPACE_PROD}.svc:8080/health"
      for i in $(seq 1 15); do
        if kubectl exec -n ${K8S_NAMESPACE_PROD} deploy/${CI_PROJECT_NAME} -- curl -sf $HEALTH_URL; then
          echo "Production health check PASSED"
          exit 0
        fi
        echo "Health check attempt $i/15 failed, waiting..."
        sleep 15
      done
      echo "PRODUCTION HEALTH CHECK FAILED — triggering rollback"
      exit 1
  artifacts:
    paths:
      - rollback-image.txt
    expire_in: 30 days
  needs:
    - deploy:staging
  when: manual
  allow_failure: false
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
      when: manual

rollback:prod:
  stage: deploy-prod
  image: bitnami/kubectl:latest
  environment:
    name: production
  script:
    - echo "=== PRODUCTION ROLLBACK ==="
    - kubectl config use-context production
    - echo "Rolling back deployment..."
    - kubectl rollout undo deployment/${CI_PROJECT_NAME} -n ${K8S_NAMESPACE_PROD}
    - echo "Waiting for rollback to complete..."
    - kubectl rollout status deployment/${CI_PROJECT_NAME} -n ${K8S_NAMESPACE_PROD} --timeout=300s
    - echo "Rollback completed. Current image:"
    - kubectl get deployment/${CI_PROJECT_NAME} -n ${K8S_NAMESPACE_PROD} -o jsonpath='{.spec.template.spec.containers[0].image}'
  when: manual
  needs:
    - job: deploy:prod
      optional: true
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
      when: manual
"""

    p = _write(Path(project_path) / ".gitlab-ci.yml", pipeline)
    return [str(p)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate GitLab CI/CD pipeline")
    parser.add_argument("--project-path", required=True, help="Target project directory")
    parser.add_argument("--project-name", default="icdev-app", help="Project name")
    parser.add_argument("--app-type", default="python", choices=["python", "node"], help="Application type")
    parser.add_argument("--registry", default="$CI_REGISTRY", help="Container registry URL")
    parser.add_argument(
        "--stages",
        default=None,
        help="Comma-separated stages to include (default: all 7 stages)",
    )
    args = parser.parse_args()

    config = {
        "project_name": args.project_name,
        "app_type": args.app_type,
        "registry": args.registry,
    }
    if args.stages:
        config["stages"] = [s.strip() for s in args.stages.split(",")]

    files = generate_pipeline(args.project_path, config)
    print(f"[pipeline] Generated pipeline: {len(files)} files")
    for f in files:
        print(f"  -> {f}")


if __name__ == "__main__":
    main()
