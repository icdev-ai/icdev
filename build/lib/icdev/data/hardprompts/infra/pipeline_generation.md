# Hard Prompt: GitLab CI/CD Pipeline Generation

## Role
You are a DevSecOps engineer generating a GitLab CI/CD pipeline with 7 stages and security gates.

## Instructions
Generate a `.gitlab-ci.yml` with full compliance and security integration.

### 7-Stage Pipeline
```yaml
# CUI // SP-CTI
stages:
  - lint
  - test
  - security-scan
  - build
  - compliance-check
  - deploy-staging
  - deploy-prod
```

### Stage Details

#### 1. Lint
```yaml
lint:
  stage: lint
  script:
    - pip install flake8 black isort
    - flake8 src/ --max-line-length 120
    - black --check src/
    - isort --check src/
  allow_failure: false
```

#### 2. Test
```yaml
test:
  stage: test
  script:
    - pip install -r requirements.txt
    - pip install pytest pytest-cov behave
    - pytest tests/ --cov=src --cov-report=xml --junitxml=report.xml
    - behave features/ || true
  coverage: '/TOTAL.*\s+(\d+%)/'
  artifacts:
    reports:
      junit: report.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
```

#### 3. Security Scan
```yaml
security-scan:
  stage: security-scan
  script:
    - pip install bandit pip-audit
    - bandit -r src/ -f json -o bandit-report.json || true
    - pip-audit --format json --output pip-audit-report.json || true
    - python tools/security/secret_detector.py --project-dir . --output secrets-report.json
  artifacts:
    reports:
      sast: bandit-report.json
    paths:
      - "*-report.json"
```

#### 4. Build
```yaml
build:
  stage: build
  script:
    - docker build -t $ECR_REPO:$CI_COMMIT_SHA -f Dockerfile .
    - docker tag $ECR_REPO:$CI_COMMIT_SHA $ECR_REPO:latest
    - docker push $ECR_REPO:$CI_COMMIT_SHA
    - docker push $ECR_REPO:latest
```

#### 5. Compliance Check
```yaml
compliance-check:
  stage: compliance-check
  script:
    - python tools/compliance/stig_checker.py --project-id $PROJECT_ID --profile webapp
    - python tools/compliance/sbom_generator.py --project-dir . --project-id $PROJECT_ID
    - python tools/compliance/cui_marker.py --verify --directory .
  allow_failure: false
```

#### 6. Deploy Staging
```yaml
deploy-staging:
  stage: deploy-staging
  script:
    - kubectl apply -f k8s/staging/
    - kubectl rollout status deployment/$APP_NAME -n staging --timeout=300s
  environment:
    name: staging
    url: https://staging.{{domain}}
  only:
    - develop
    - merge_requests
```

#### 7. Deploy Production
```yaml
deploy-prod:
  stage: deploy-prod
  script:
    - kubectl apply -f k8s/production/
    - kubectl rollout status deployment/$APP_NAME -n production --timeout=300s
  environment:
    name: production
    url: https://{{domain}}
  when: manual  # Requires explicit approval
  only:
    - main
```

### Security Gates (Blocking)
| Gate | Stage | Failure Action |
|------|-------|---------------|
| Lint errors | lint | Block pipeline |
| Test failures | test | Block pipeline |
| SAST HIGH findings | security-scan | Block pipeline |
| Critical CVEs | security-scan | Block pipeline |
| Secrets detected | security-scan | Block pipeline |
| STIG CAT1 | compliance-check | Block pipeline |
| Missing CUI markings | compliance-check | Block pipeline |

### Auto-Rollback
```yaml
.rollback:
  script:
    - kubectl rollout undo deployment/$APP_NAME -n $ENVIRONMENT
  when: on_failure
```

## Rules
- Pipeline file MUST have CUI marking comment at top
- Security scan stage MUST run before build (shift-left)
- Production deploy MUST require manual approval (`when: manual`)
- ALL security gates are blocking (`allow_failure: false`)
- Artifacts (test reports, scan results) MUST be preserved
- Use GitLab CI variables for secrets (never hardcode)
- Cache pip/npm dependencies for performance
- Auto-rollback on deployment failure

## Input
- Project ID: {{project_id}}
- Project name: {{project_name}}
- Stages: {{stages}} (default all 7)
- ECR repository: {{ecr_repo}}

## Output
- .gitlab-ci.yml with all stages configured
- CUI markings applied
- Security gates configured
