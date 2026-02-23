# [TEMPLATE: CUI // SP-CTI]
# Plan Python Application — ICDEV Framework-Specific Build Command

Generate a comprehensive build plan for a Python application with ICDEV compliance scaffolding.

## Application Name: $ARGUMENTS

## Project Structure
```
$ARGUMENTS/
├── src/
│   ├── __init__.py
│   ├── app.py                  # Flask/FastAPI entry point
│   ├── config.py               # Environment-based configuration
│   ├── models/                 # SQLAlchemy/dataclass models
│   ├── routes/                 # API route blueprints
│   ├── services/               # Business logic layer
│   └── utils/                  # Shared utilities
├── tests/
│   ├── conftest.py             # pytest fixtures
│   ├── unit/                   # Unit tests (pytest)
│   └── integration/            # Integration tests
├── features/
│   ├── steps/                  # Behave step definitions
│   └── *.feature               # Gherkin BDD scenarios
├── docker/
│   └── Dockerfile              # STIG-hardened (non-root, minimal)
├── k8s/
│   ├── deployment.yaml         # K8s deployment manifest
│   └── service.yaml            # K8s service manifest
├── .gitlab-ci.yml              # GitLab CI/CD pipeline
├── requirements.txt            # Pinned dependencies
├── requirements-dev.txt        # Dev/test dependencies
├── setup.py                    # Package configuration
├── pyproject.toml              # Build system config
└── README.md                   # CUI-marked documentation
```

## Technology Stack
- **Framework:** Flask (preferred, D3) or FastAPI
- **Testing:** pytest + pytest-cov + behave (BDD)
- **Linting:** ruff (replaces flake8+isort+black)
- **SAST:** bandit
- **Dependency Audit:** pip-audit
- **Formatting:** black + isort (or ruff format)
- **Secret Detection:** detect-secrets

## STIG-Hardened Dockerfile
```dockerfile
# CUI // SP-CTI
FROM python:3.11-slim AS base
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
USER appuser
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"
CMD ["python", "-m", "gunicorn", "-w", "4", "-b", "0.0.0.0:8080", "src.app:create_app()"]
# CUI // SP-CTI
```

## CI/CD Pipeline Stages
1. **lint** — ruff check + ruff format --check
2. **sast** — bandit -r src/ -f json
3. **test** — pytest tests/ --cov=src --cov-report=xml
4. **bdd** — behave features/
5. **audit** — pip-audit -r requirements.txt
6. **secrets** — detect-secrets scan
7. **sbom** — cyclonedx-py generate
8. **build** — docker build with STIG hardening
9. **deploy** — kubectl apply (staging first, then prod)

## CUI Marking Injection Points
- All `.py` files: `# CUI // SP-CTI` as first line
- All `.yaml` files: `# CUI // SP-CTI` as first line
- All `.md` files: `# CUI // SP-CTI` as first line
- Dockerfile: First and last line
- API responses: `X-Classification: CUI` header

## NIST 800-53 Control Mapping
| Control | Implementation |
|---------|---------------|
| AC-2 | Flask-Login/JWT with role-based access |
| AU-2 | Structured logging to ELK/Splunk |
| AU-3 | Log: who, what, when, where, outcome |
| SC-8 | TLS 1.2+ enforced, FIPS 140-2 |
| SC-13 | hashlib with FIPS mode |
| SI-10 | Input validation via marshmallow/pydantic |
| CM-7 | Minimal base image, non-root user |

## Instructions
1. Read the goal file: `goals/build_app.md` (ATLAS workflow)
2. Use `/icdev-init` to scaffold the project
3. Follow TDD: Write tests FIRST (RED), then implement (GREEN), then refactor
4. Run security scanning after each feature
5. Generate compliance artifacts before deployment
