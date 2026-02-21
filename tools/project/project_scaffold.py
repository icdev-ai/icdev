#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate project directory structure based on project type.

Supports: webapp, microservice, api, cli, data_pipeline, iac.
Creates language-appropriate scaffolding with CUI markings, compliance directories,
CI/CD pipelines, Dockerfiles, and test scaffolding.

Usage:
    python tools/project/project_scaffold.py --project-dir /path/to/project --type webapp
    python tools/project/project_scaffold.py --project-dir /path/to/project --type microservice --classification CUI
"""

import argparse
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# CUI banner applied to the top of generated source files
CUI_HEADER_PYTHON = '''# //CUI
# CONTROLLED UNCLASSIFIED INFORMATION
# Authorized distribution limited to authorized personnel only.
# Handling: CUI Basic per 32 CFR Part 2002
# //CUI
'''

CUI_HEADER_JS = '''// //CUI
// CONTROLLED UNCLASSIFIED INFORMATION
// Authorized distribution limited to authorized personnel only.
// Handling: CUI Basic per 32 CFR Part 2002
// //CUI
'''

CUI_HEADER_YAML = '''# //CUI
# CONTROLLED UNCLASSIFIED INFORMATION
# Authorized distribution limited to authorized personnel only.
# Handling: CUI Basic per 32 CFR Part 2002
# //CUI
'''

CUI_HEADER_DOCKERFILE = '''# //CUI
# CONTROLLED UNCLASSIFIED INFORMATION
# Authorized distribution limited to authorized personnel only.
# Handling: CUI Basic per 32 CFR Part 2002
# //CUI
'''

CUI_HEADER_MARKDOWN = '''<!-- //CUI -->
<!-- CONTROLLED UNCLASSIFIED INFORMATION -->
<!-- Authorized distribution limited to authorized personnel only. -->
<!-- Handling: CUI Basic per 32 CFR Part 2002 -->
<!-- //CUI -->
'''


def get_cui_header(file_ext: str, classification: str = "CUI") -> str:
    """Return the appropriate CUI header comment for a file extension."""
    if classification.upper() == "PUBLIC":
        return ""
    headers = {
        ".py": CUI_HEADER_PYTHON,
        ".js": CUI_HEADER_JS,
        ".ts": CUI_HEADER_JS,
        ".jsx": CUI_HEADER_JS,
        ".tsx": CUI_HEADER_JS,
        ".yml": CUI_HEADER_YAML,
        ".yaml": CUI_HEADER_YAML,
        ".tf": CUI_HEADER_PYTHON,  # Terraform uses # comments
        ".sh": CUI_HEADER_PYTHON,
        ".md": CUI_HEADER_MARKDOWN,
    }
    return headers.get(file_ext, "")


def write_file(path: Path, content: str, classification: str = "CUI") -> None:
    """Write a file, prepending CUI header if applicable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = get_cui_header(path.suffix, classification)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        if header:
            f.write(header)
            f.write("\n")
        f.write(content)


def scaffold_common(project_dir: Path, project_name: str, classification: str) -> list:
    """Create directories and files common to all project types. Returns list of created paths."""
    created = []

    # Compliance directory for ATO + CSSP artifacts
    compliance_dirs = [
        project_dir / "compliance",
        project_dir / "compliance" / "ssp",
        project_dir / "compliance" / "poam",
        project_dir / "compliance" / "stig",
        project_dir / "compliance" / "sbom",
        project_dir / "compliance" / "evidence",
        project_dir / "compliance" / "cssp",
        project_dir / "compliance" / "xacta-exports",
        project_dir / "compliance" / "sbd",
        project_dir / "compliance" / "ivv",
        project_dir / "compliance" / "rtm",
        project_dir / "siem",
        project_dir / "security",
    ]
    for d in compliance_dirs:
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d))

    # README with CUI markings
    readme_content = f"""# {project_name}

> **Classification: {classification}**

## Overview

*Project description goes here.*

## Getting Started

### Prerequisites

*List prerequisites here.*

### Installation

```bash
# Installation steps
```

## Testing

```bash
# Test commands
```

## Deployment

See `compliance/` directory for ATO artifacts and deployment authorization documentation.

## Compliance

This project maintains the following compliance artifacts in `compliance/`:

- **SSP** - System Security Plan
- **POA&M** - Plan of Action and Milestones
- **STIG** - Security Technical Implementation Guide findings
- **SBOM** - Software Bill of Materials
- **CSSP** - Cybersecurity Service Provider assessment (DI 8530.01)
- **SbD** - Secure by Design assessment (CISA, DoDI 5000.87)
- **IV&V** - Independent Verification & Validation (IEEE 1012)
- **RTM** - Requirements Traceability Matrix
- **Evidence** - CSSP evidence artifacts
- **Xacta Exports** - OSCAL/CSV exports for Xacta 360

Additional security/operational artifacts:
- `siem/` - SIEM forwarding configs (Splunk, ELK)
- `security/` - Incident Response Plan, security procedures

## Classification

This project is classified as **{classification}**. Handle all materials in accordance
with applicable handling requirements.
"""
    write_file(project_dir / "README.md", readme_content, classification)
    created.append(str(project_dir / "README.md"))

    # .gitignore
    gitignore_content = """# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
dist/
build/
.eggs/
*.egg
.venv/
venv/
env/

# Node
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.npm

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# Environment
.env
.env.local
.env.*.local

# OS
.DS_Store
Thumbs.db

# Testing
.coverage
htmlcov/
.pytest_cache/
coverage/

# Build artifacts
*.tfstate
*.tfstate.backup
.terraform/

# Secrets - NEVER commit
*.pem
*.key
credentials.json
"""
    write_file(project_dir / ".gitignore", gitignore_content)
    created.append(str(project_dir / ".gitignore"))

    return created


def scaffold_python_webapp(project_dir: Path, project_name: str, classification: str) -> list:
    """Scaffold a Python web application (Flask/FastAPI)."""
    created = scaffold_common(project_dir, project_name, classification)
    slug = project_name.lower().replace(" ", "_").replace("-", "_")

    # Source directories
    src_dirs = [
        project_dir / "src",
        project_dir / "src" / slug,
        project_dir / "src" / slug / "api",
        project_dir / "src" / slug / "models",
        project_dir / "src" / slug / "services",
        project_dir / "src" / slug / "templates",
        project_dir / "src" / slug / "static",
    ]
    for d in src_dirs:
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d))

    # Test directories (BDD)
    test_dirs = [
        project_dir / "tests",
        project_dir / "tests" / "features",
        project_dir / "tests" / "steps",
        project_dir / "tests" / "unit",
        project_dir / "tests" / "integration",
    ]
    for d in test_dirs:
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d))

    # src/__init__.py
    write_file(
        project_dir / "src" / slug / "__init__.py",
        f'"""ICDEV Project: {project_name}"""\n\n__version__ = "0.1.0"\n',
        classification,
    )
    created.append(str(project_dir / "src" / slug / "__init__.py"))

    # src/api/__init__.py
    write_file(
        project_dir / "src" / slug / "api" / "__init__.py",
        '"""API routes."""\n',
        classification,
    )
    created.append(str(project_dir / "src" / slug / "api" / "__init__.py"))

    # src/models/__init__.py
    write_file(
        project_dir / "src" / slug / "models" / "__init__.py",
        '"""Data models."""\n',
        classification,
    )
    created.append(str(project_dir / "src" / slug / "models" / "__init__.py"))

    # src/services/__init__.py
    write_file(
        project_dir / "src" / slug / "services" / "__init__.py",
        '"""Business logic services."""\n',
        classification,
    )
    created.append(str(project_dir / "src" / slug / "services" / "__init__.py"))

    # Main application entry point
    app_content = f'''"""Main application entry point for {project_name}."""

import os

from flask import Flask


def create_app(config_name: str = None) -> Flask:
    """Application factory pattern."""
    app = Flask(__name__)

    config_name = config_name or os.environ.get("FLASK_CONFIG", "development")

    # Register blueprints
    # from .api import api_bp
    # app.register_blueprint(api_bp, url_prefix="/api/v1")

    @app.route("/health")
    def health():
        return {{"status": "healthy", "service": "{project_name}"}}, 200

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
'''
    write_file(project_dir / "src" / slug / "app.py", app_content, classification)
    created.append(str(project_dir / "src" / slug / "app.py"))

    # conftest.py
    conftest_content = f'''"""Pytest configuration and shared fixtures for {project_name}."""

import pytest

from src.{slug}.app import create_app


@pytest.fixture
def app():
    """Create application for testing."""
    app = create_app("testing")
    yield app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create test CLI runner."""
    return app.test_cli_runner()
'''
    write_file(project_dir / "conftest.py", conftest_content, classification)
    created.append(str(project_dir / "conftest.py"))

    # Sample BDD feature file
    feature_content = """Feature: Health Check
  As a system administrator
  I want to verify the application is running
  So that I can confirm the deployment is successful

  Scenario: Health endpoint returns OK
    Given the application is running
    When I request the health endpoint
    Then I should receive a 200 status code
    And the response should contain "healthy"
"""
    write_file(project_dir / "tests" / "features" / "health.feature", feature_content)
    created.append(str(project_dir / "tests" / "features" / "health.feature"))

    # BDD step definitions
    steps_content = '''"""Step definitions for health check feature."""

from pytest_bdd import given, when, then, parsers, scenarios

scenarios("../features/health.feature")


@given("the application is running")
def app_running(client):
    """Ensure app is available via test client fixture."""
    pass


@when("I request the health endpoint", target_fixture="response")
def request_health(client):
    return client.get("/health")


@then("I should receive a 200 status code")
def check_status(response):
    assert response.status_code == 200


@then(parsers.parse('the response should contain "{text}"'))
def check_content(response, text):
    assert text in response.get_data(as_text=True)
'''
    write_file(project_dir / "tests" / "steps" / "test_health.py", steps_content, classification)
    created.append(str(project_dir / "tests" / "steps" / "test_health.py"))

    # pyproject.toml
    pyproject_content = f"""[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "{slug}"
version = "0.1.0"
description = "{project_name} - ICDEV managed project"
requires-python = ">=3.11"
dependencies = [
    "flask>=3.0",
    "gunicorn>=21.2",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4",
    "pytest-bdd>=7.0",
    "pytest-cov>=4.1",
    "flake8>=7.0",
    "black>=24.0",
    "bandit>=1.7",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "--cov=src --cov-report=html --cov-report=term-missing"

[tool.black]
line-length = 100
target-version = ["py311"]

[tool.bandit]
exclude_dirs = ["tests", ".venv"]
"""
    write_file(project_dir / "pyproject.toml", pyproject_content)
    created.append(str(project_dir / "pyproject.toml"))

    # Dockerfile (STIG-hardened base)
    dockerfile_content = f"""FROM python:3.11-slim AS base

LABEL maintainer="ICDEV System"
LABEL classification="{classification}"

# STIG: V-222656 - Remove unnecessary packages
RUN apt-get update && \\
    apt-get upgrade -y && \\
    apt-get install -y --no-install-recommends \\
        ca-certificates \\
        curl && \\
    apt-get autoremove -y && \\
    apt-get clean && \\
    rm -rf /var/lib/apt/lists/*

# STIG: V-222657 - Run as non-root user
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \\
    pip install --no-cache-dir .

COPY src/ ./src/

# STIG: V-222658 - Set restrictive file permissions
RUN chown -R appuser:appuser /app && \\
    chmod -R 750 /app

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "4", "src.{slug}.app:create_app()"]
"""
    write_file(project_dir / "Dockerfile", dockerfile_content, classification)
    created.append(str(project_dir / "Dockerfile"))

    # .gitlab-ci.yml
    gitlab_ci_content = f"""stages:
  - test
  - security
  - compliance
  - build
  - deploy

variables:
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"

cache:
  paths:
    - .cache/pip
    - .venv

# --- Test Stage ---
unit-tests:
  stage: test
  image: python:3.11-slim
  script:
    - pip install --upgrade pip
    - pip install .[dev]
    - pytest --junitxml=report.xml --cov=src --cov-report=xml
  artifacts:
    reports:
      junit: report.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml

# --- Security Stage ---
sast-scan:
  stage: security
  image: python:3.11-slim
  script:
    - pip install bandit
    - bandit -r src/ -f json -o bandit-report.json || true
  artifacts:
    paths:
      - bandit-report.json

dependency-audit:
  stage: security
  image: python:3.11-slim
  script:
    - pip install pip-audit
    - pip-audit --format=json --output=pip-audit-report.json || true
  artifacts:
    paths:
      - pip-audit-report.json

secret-detection:
  stage: security
  image: python:3.11-slim
  script:
    - pip install detect-secrets
    - detect-secrets scan --all-files > secrets-report.json || true
  artifacts:
    paths:
      - secrets-report.json

# --- Compliance Stage ---
compliance-check:
  stage: compliance
  image: python:3.11-slim
  script:
    - echo "Compliance checks run by ICDEV compliance engine"
    - echo "Classification: {classification}"
  allow_failure: true

# --- Build Stage ---
build-image:
  stage: build
  image: docker:24
  services:
    - docker:24-dind
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker tag $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA $CI_REGISTRY_IMAGE:latest
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
    - docker push $CI_REGISTRY_IMAGE:latest
  only:
    - main
    - develop

# --- Deploy Stage ---
deploy-dev:
  stage: deploy
  image: bitnami/kubectl:latest
  script:
    - echo "Deploying to dev environment"
  environment:
    name: development
  only:
    - develop

deploy-staging:
  stage: deploy
  image: bitnami/kubectl:latest
  script:
    - echo "Deploying to staging environment"
  environment:
    name: staging
  only:
    - main
  when: manual
"""
    write_file(project_dir / ".gitlab-ci.yml", gitlab_ci_content, classification)
    created.append(str(project_dir / ".gitlab-ci.yml"))

    return created


def scaffold_microservice(project_dir: Path, project_name: str, classification: str) -> list:
    """Scaffold a Python microservice (FastAPI-based)."""
    created = scaffold_common(project_dir, project_name, classification)
    slug = project_name.lower().replace(" ", "_").replace("-", "_")

    # Source directories
    for d in [
        project_dir / "src",
        project_dir / "src" / slug,
        project_dir / "src" / slug / "api",
        project_dir / "src" / slug / "models",
        project_dir / "src" / slug / "services",
        project_dir / "tests",
        project_dir / "tests" / "features",
        project_dir / "tests" / "steps",
        project_dir / "tests" / "unit",
        project_dir / "tests" / "integration",
    ]:
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d))

    # __init__.py files
    write_file(project_dir / "src" / slug / "__init__.py", f'"""Microservice: {project_name}"""\n\n__version__ = "0.1.0"\n', classification)
    write_file(project_dir / "src" / slug / "api" / "__init__.py", '"""API routes."""\n', classification)
    write_file(project_dir / "src" / slug / "models" / "__init__.py", '"""Data models."""\n', classification)
    write_file(project_dir / "src" / slug / "services" / "__init__.py", '"""Services."""\n', classification)

    # Main FastAPI app
    main_content = f'''"""FastAPI microservice entry point for {project_name}."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="{project_name}",
    version="0.1.0",
    docs_url="/docs" if os.environ.get("ENVIRONMENT") != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "").split(",") if os.environ.get("CORS_ORIGINS") else [],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {{"status": "healthy", "service": "{project_name}", "version": "0.1.0"}}


@app.get("/ready")
async def readiness():
    """Readiness probe for Kubernetes."""
    return {{"ready": True}}
'''
    write_file(project_dir / "src" / slug / "main.py", main_content, classification)

    # pyproject.toml for microservice
    pyproject_content = f"""[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "{slug}"
version = "0.1.0"
description = "{project_name} microservice - ICDEV managed"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.104",
    "uvicorn[standard]>=0.24",
    "pydantic>=2.5",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4",
    "pytest-bdd>=7.0",
    "pytest-cov>=4.1",
    "pytest-asyncio>=0.23",
    "httpx>=0.25",
    "flake8>=7.0",
    "black>=24.0",
    "bandit>=1.7",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
addopts = "--cov=src --cov-report=html --cov-report=term-missing"
asyncio_mode = "auto"

[tool.black]
line-length = 100
target-version = ["py311"]
"""
    write_file(project_dir / "pyproject.toml", pyproject_content)

    # conftest.py
    conftest_content = f'''"""Pytest configuration for {project_name} microservice."""

import pytest
from httpx import AsyncClient, ASGITransport

from src.{slug}.main import app


@pytest.fixture
async def async_client():
    """Async test client for FastAPI."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
'''
    write_file(project_dir / "conftest.py", conftest_content, classification)

    # Dockerfile
    dockerfile_content = f"""FROM python:3.11-slim AS base

LABEL maintainer="ICDEV System"
LABEL classification="{classification}"

RUN apt-get update && \\
    apt-get upgrade -y && \\
    apt-get install -y --no-install-recommends ca-certificates curl && \\
    apt-get autoremove -y && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .
COPY src/ ./src/

RUN chown -R appuser:appuser /app && chmod -R 750 /app
USER appuser
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "src.{slug}.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4"]
"""
    write_file(project_dir / "Dockerfile", dockerfile_content, classification)

    # .gitlab-ci.yml (same structure as webapp)
    gitlab_ci_content = """stages:
  - test
  - security
  - compliance
  - build
  - deploy

variables:
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"

cache:
  paths:
    - .cache/pip

unit-tests:
  stage: test
  image: python:3.11-slim
  script:
    - pip install --upgrade pip && pip install .[dev]
    - pytest --junitxml=report.xml --cov=src --cov-report=xml
  artifacts:
    reports:
      junit: report.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml

sast-scan:
  stage: security
  image: python:3.11-slim
  script:
    - pip install bandit
    - bandit -r src/ -f json -o bandit-report.json || true
  artifacts:
    paths: [bandit-report.json]

dependency-audit:
  stage: security
  image: python:3.11-slim
  script:
    - pip install pip-audit
    - pip-audit --format=json --output=pip-audit-report.json || true
  artifacts:
    paths: [pip-audit-report.json]

build-image:
  stage: build
  image: docker:24
  services: [docker:24-dind]
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  only: [main, develop]
"""
    write_file(project_dir / ".gitlab-ci.yml", gitlab_ci_content, classification)

    created.extend([
        str(project_dir / "src" / slug / "__init__.py"),
        str(project_dir / "src" / slug / "main.py"),
        str(project_dir / "pyproject.toml"),
        str(project_dir / "conftest.py"),
        str(project_dir / "Dockerfile"),
        str(project_dir / ".gitlab-ci.yml"),
    ])
    return created


def scaffold_api(project_dir: Path, project_name: str, classification: str) -> list:
    """Scaffold a REST API project (Python FastAPI, similar to microservice but API-focused)."""
    # API is structurally similar to microservice with additional OpenAPI focus
    created = scaffold_microservice(project_dir, project_name, classification)

    slug = project_name.lower().replace(" ", "_").replace("-", "_")

    # Add API-specific schema models file
    schema_content = '''"""Pydantic schemas for API request/response models."""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(description="Service status")
    service: str = Field(description="Service name")
    version: str = Field(description="Service version")


class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str = Field(description="Error type")
    message: str = Field(description="Human-readable error message")
    detail: Optional[str] = Field(default=None, description="Additional detail")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
'''
    write_file(project_dir / "src" / slug / "models" / "schemas.py", schema_content, classification)
    created.append(str(project_dir / "src" / slug / "models" / "schemas.py"))

    return created


def scaffold_cli(project_dir: Path, project_name: str, classification: str) -> list:
    """Scaffold a CLI tool project (Python with Click)."""
    created = scaffold_common(project_dir, project_name, classification)
    slug = project_name.lower().replace(" ", "_").replace("-", "_")

    for d in [
        project_dir / "src",
        project_dir / "src" / slug,
        project_dir / "src" / slug / "commands",
        project_dir / "tests",
        project_dir / "tests" / "unit",
    ]:
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d))

    # __init__.py
    write_file(project_dir / "src" / slug / "__init__.py", f'"""CLI tool: {project_name}"""\n\n__version__ = "0.1.0"\n', classification)
    write_file(project_dir / "src" / slug / "commands" / "__init__.py", '"""CLI commands."""\n', classification)

    # Main CLI entry
    cli_content = f'''"""CLI entry point for {project_name}."""

import click


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """{project_name} - ICDEV managed CLI tool."""
    pass


@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
def status(verbose: bool):
    """Show current status."""
    click.echo("Status: OK")
    if verbose:
        click.echo("Verbose mode enabled")


if __name__ == "__main__":
    cli()
'''
    write_file(project_dir / "src" / slug / "cli.py", cli_content, classification)

    # pyproject.toml
    pyproject_content = f"""[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "{slug}"
version = "0.1.0"
description = "{project_name} CLI tool - ICDEV managed"
requires-python = ">=3.11"
dependencies = [
    "click>=8.1",
    "rich>=13.0",
]

[project.scripts]
{slug} = "src.{slug}.cli:cli"

[project.optional-dependencies]
dev = [
    "pytest>=7.4",
    "pytest-cov>=4.1",
    "flake8>=7.0",
    "black>=24.0",
    "bandit>=1.7",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=src --cov-report=term-missing"

[tool.black]
line-length = 100
"""
    write_file(project_dir / "pyproject.toml", pyproject_content)

    # conftest.py
    conftest_content = f'''"""Pytest config for {project_name} CLI."""

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner():
    """Click CLI test runner."""
    return CliRunner()
'''
    write_file(project_dir / "conftest.py", conftest_content, classification)

    # Dockerfile
    dockerfile_content = f"""FROM python:3.11-slim

LABEL maintainer="ICDEV System"
LABEL classification="{classification}"

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .
COPY src/ ./src/
RUN chown -R appuser:appuser /app && chmod -R 750 /app
USER appuser

ENTRYPOINT ["{slug}"]
"""
    write_file(project_dir / "Dockerfile", dockerfile_content, classification)

    # .gitlab-ci.yml
    gitlab_ci_content = """stages:
  - test
  - security
  - build

unit-tests:
  stage: test
  image: python:3.11-slim
  script:
    - pip install --upgrade pip && pip install .[dev]
    - pytest --junitxml=report.xml --cov=src --cov-report=xml
  artifacts:
    reports:
      junit: report.xml

sast-scan:
  stage: security
  image: python:3.11-slim
  script:
    - pip install bandit
    - bandit -r src/ -f json -o bandit-report.json || true
  artifacts:
    paths: [bandit-report.json]

build-image:
  stage: build
  image: docker:24
  services: [docker:24-dind]
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  only: [main]
"""
    write_file(project_dir / ".gitlab-ci.yml", gitlab_ci_content, classification)

    created.extend([
        str(project_dir / "src" / slug / "__init__.py"),
        str(project_dir / "src" / slug / "cli.py"),
        str(project_dir / "pyproject.toml"),
        str(project_dir / "conftest.py"),
        str(project_dir / "Dockerfile"),
        str(project_dir / ".gitlab-ci.yml"),
    ])
    return created


def scaffold_data_pipeline(project_dir: Path, project_name: str, classification: str) -> list:
    """Scaffold a data pipeline project."""
    created = scaffold_common(project_dir, project_name, classification)
    slug = project_name.lower().replace(" ", "_").replace("-", "_")

    for d in [
        project_dir / "src",
        project_dir / "src" / slug,
        project_dir / "src" / slug / "extractors",
        project_dir / "src" / slug / "transformers",
        project_dir / "src" / slug / "loaders",
        project_dir / "dags",
        project_dir / "tests",
        project_dir / "tests" / "unit",
        project_dir / "tests" / "integration",
        project_dir / "data" / "raw",
        project_dir / "data" / "processed",
        project_dir / "data" / "staging",
    ]:
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d))

    # __init__.py files
    write_file(project_dir / "src" / slug / "__init__.py", f'"""Data Pipeline: {project_name}"""\n\n__version__ = "0.1.0"\n', classification)
    write_file(project_dir / "src" / slug / "extractors" / "__init__.py", '"""Data extractors."""\n', classification)
    write_file(project_dir / "src" / slug / "transformers" / "__init__.py", '"""Data transformers."""\n', classification)
    write_file(project_dir / "src" / slug / "loaders" / "__init__.py", '"""Data loaders."""\n', classification)

    # Pipeline entry point
    pipeline_content = f'''"""Main pipeline orchestration for {project_name}."""

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class Pipeline:
    """ETL pipeline orchestrator."""

    def __init__(self, name: str = "{project_name}"):
        self.name = name
        self.started_at = None
        self.completed_at = None

    def extract(self, source: str) -> dict:
        """Extract data from source."""
        logger.info("Extracting from %s", source)
        return {{"source": source, "rows": [], "extracted_at": datetime.now(timezone.utc).isoformat()}}

    def transform(self, data: dict) -> dict:
        """Transform extracted data."""
        logger.info("Transforming %d rows", len(data.get("rows", [])))
        return {{**data, "transformed": True, "transformed_at": datetime.now(timezone.utc).isoformat()}}

    def load(self, data: dict, target: str) -> dict:
        """Load transformed data to target."""
        logger.info("Loading to %s", target)
        return {{**data, "target": target, "loaded_at": datetime.now(timezone.utc).isoformat()}}

    def run(self, source: str, target: str) -> dict:
        """Execute full ETL pipeline."""
        self.started_at = datetime.now(timezone.utc)
        logger.info("Pipeline '%s' starting", self.name)

        data = self.extract(source)
        data = self.transform(data)
        result = self.load(data, target)

        self.completed_at = datetime.now(timezone.utc)
        result["duration_seconds"] = (self.completed_at - self.started_at).total_seconds()
        logger.info("Pipeline '%s' completed in %.2fs", self.name, result["duration_seconds"])
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pipeline = Pipeline()
    result = pipeline.run(source="input", target="output")
    print(result)
'''
    write_file(project_dir / "src" / slug / "pipeline.py", pipeline_content, classification)

    # pyproject.toml
    pyproject_content = f"""[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "{slug}"
version = "0.1.0"
description = "{project_name} data pipeline - ICDEV managed"
requires-python = ">=3.11"
dependencies = [
    "pandas>=2.1",
    "sqlalchemy>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4",
    "pytest-cov>=4.1",
    "flake8>=7.0",
    "black>=24.0",
    "bandit>=1.7",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=src --cov-report=term-missing"
"""
    write_file(project_dir / "pyproject.toml", pyproject_content)

    # conftest.py
    conftest_content = f'''"""Pytest config for {project_name} pipeline."""

import pytest

from src.{slug}.pipeline import Pipeline


@pytest.fixture
def pipeline():
    """Create a pipeline instance for testing."""
    return Pipeline(name="test-pipeline")
'''
    write_file(project_dir / "conftest.py", conftest_content, classification)

    # Dockerfile
    dockerfile_content = f"""FROM python:3.11-slim

LABEL maintainer="ICDEV System"
LABEL classification="{classification}"

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .
COPY src/ ./src/
RUN chown -R appuser:appuser /app && chmod -R 750 /app
USER appuser

CMD ["python", "-m", "src.{slug}.pipeline"]
"""
    write_file(project_dir / "Dockerfile", dockerfile_content, classification)

    # .gitlab-ci.yml
    gitlab_ci_content = """stages:
  - test
  - security
  - build

unit-tests:
  stage: test
  image: python:3.11-slim
  script:
    - pip install --upgrade pip && pip install .[dev]
    - pytest --junitxml=report.xml --cov=src --cov-report=xml
  artifacts:
    reports:
      junit: report.xml

sast-scan:
  stage: security
  image: python:3.11-slim
  script:
    - pip install bandit
    - bandit -r src/ -f json -o bandit-report.json || true
  artifacts:
    paths: [bandit-report.json]

build-image:
  stage: build
  image: docker:24
  services: [docker:24-dind]
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  only: [main]
"""
    write_file(project_dir / ".gitlab-ci.yml", gitlab_ci_content, classification)

    created.extend([
        str(project_dir / "src" / slug / "__init__.py"),
        str(project_dir / "src" / slug / "pipeline.py"),
        str(project_dir / "pyproject.toml"),
        str(project_dir / "conftest.py"),
        str(project_dir / "Dockerfile"),
        str(project_dir / ".gitlab-ci.yml"),
    ])
    return created


def scaffold_iac(project_dir: Path, project_name: str, classification: str) -> list:
    """Scaffold an Infrastructure-as-Code project (Terraform + Ansible)."""
    created = scaffold_common(project_dir, project_name, classification)
    project_name.lower().replace(" ", "_").replace("-", "_")

    for d in [
        project_dir / "terraform" / "modules",
        project_dir / "terraform" / "environments" / "dev",
        project_dir / "terraform" / "environments" / "staging",
        project_dir / "terraform" / "environments" / "prod",
        project_dir / "ansible" / "roles",
        project_dir / "ansible" / "inventory",
        project_dir / "ansible" / "playbooks",
        project_dir / "k8s" / "base",
        project_dir / "k8s" / "overlays" / "dev",
        project_dir / "k8s" / "overlays" / "staging",
        project_dir / "k8s" / "overlays" / "prod",
        project_dir / "scripts",
        project_dir / "tests",
    ]:
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d))

    # Terraform main.tf
    tf_main_content = f'''terraform {{
  required_version = ">= 1.5.0"

  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}

  backend "s3" {{
    # Configure per environment in environments/*/backend.hcl
  }}
}}

provider "aws" {{
  region = var.aws_region

  default_tags {{
    tags = {{
      Project        = "{project_name}"
      Environment    = var.environment
      Classification = "{classification}"
      ManagedBy      = "terraform"
      Owner          = "ICDEV"
    }}
  }}
}}
'''
    write_file(project_dir / "terraform" / "main.tf", tf_main_content, classification)

    # Terraform variables.tf
    tf_vars_content = f'''variable "aws_region" {{
  description = "AWS GovCloud region"
  type        = string
  default     = "us-gov-west-1"
}}

variable "environment" {{
  description = "Deployment environment"
  type        = string
  validation {{
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }}
}}

variable "project_name" {{
  description = "Project name"
  type        = string
  default     = "{project_name}"
}}

variable "classification" {{
  description = "Data classification level"
  type        = string
  default     = "{classification}"
}}
'''
    write_file(project_dir / "terraform" / "variables.tf", tf_vars_content, classification)

    # Terraform outputs.tf
    tf_outputs_content = '''output "project_name" {
  description = "Project name"
  value       = var.project_name
}

output "environment" {
  description = "Current environment"
  value       = var.environment
}
'''
    write_file(project_dir / "terraform" / "outputs.tf", tf_outputs_content, classification)

    # Ansible inventory
    inventory_content = """[all:vars]
ansible_user=ec2-user
ansible_ssh_private_key_file=~/.ssh/id_rsa

[webservers]
# Add hosts here

[databases]
# Add hosts here
"""
    write_file(project_dir / "ansible" / "inventory" / "hosts.ini", inventory_content)

    # Ansible site.yml
    site_content = f"""---
# Site-wide playbook for {project_name}
# Classification: {classification}

- name: Configure base security hardening
  hosts: all
  become: true
  roles:
    - role: security-baseline

- name: Configure web servers
  hosts: webservers
  become: true
  roles: []
"""
    write_file(project_dir / "ansible" / "playbooks" / "site.yml", site_content, classification)

    # K8s kustomization.yaml
    k8s_base_content = """apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources: []

commonLabels:
  app.kubernetes.io/managed-by: icdev
"""
    write_file(project_dir / "k8s" / "base" / "kustomization.yaml", k8s_base_content, classification)

    # .gitlab-ci.yml for IaC
    gitlab_ci_content = """stages:
  - validate
  - plan
  - apply
  - verify

terraform-validate:
  stage: validate
  image: hashicorp/terraform:1.6
  script:
    - cd terraform
    - terraform init -backend=false
    - terraform validate
    - terraform fmt -check

terraform-plan:
  stage: plan
  image: hashicorp/terraform:1.6
  script:
    - cd terraform
    - terraform init
    - terraform plan -out=tfplan
  artifacts:
    paths: [terraform/tfplan]
  only: [main, develop]

terraform-apply:
  stage: apply
  image: hashicorp/terraform:1.6
  script:
    - cd terraform
    - terraform init
    - terraform apply -auto-approve tfplan
  dependencies: [terraform-plan]
  only: [main]
  when: manual

ansible-lint:
  stage: validate
  image: python:3.11-slim
  script:
    - pip install ansible-lint
    - ansible-lint ansible/
  allow_failure: true
"""
    write_file(project_dir / ".gitlab-ci.yml", gitlab_ci_content, classification)

    created.extend([
        str(project_dir / "terraform" / "main.tf"),
        str(project_dir / "terraform" / "variables.tf"),
        str(project_dir / "terraform" / "outputs.tf"),
        str(project_dir / "ansible" / "inventory" / "hosts.ini"),
        str(project_dir / "ansible" / "playbooks" / "site.yml"),
        str(project_dir / "k8s" / "base" / "kustomization.yaml"),
        str(project_dir / ".gitlab-ci.yml"),
    ])
    return created


def scaffold_js_frontend(project_dir: Path, project_name: str, classification: str) -> list:
    """Scaffold a JavaScript/TypeScript frontend project (React)."""
    created = scaffold_common(project_dir, project_name, classification)
    slug = project_name.lower().replace(" ", "-")

    for d in [
        project_dir / "src",
        project_dir / "src" / "components",
        project_dir / "src" / "pages",
        project_dir / "src" / "hooks",
        project_dir / "src" / "services",
        project_dir / "src" / "utils",
        project_dir / "src" / "types",
        project_dir / "tests",
        project_dir / "tests" / "unit",
        project_dir / "tests" / "integration",
        project_dir / "public",
    ]:
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d))

    # package.json
    package_json = {
        "name": slug,
        "version": "0.1.0",
        "private": True,
        "description": f"{project_name} - ICDEV managed frontend",
        "scripts": {
            "dev": "vite",
            "build": "tsc && vite build",
            "preview": "vite preview",
            "test": "vitest run",
            "test:watch": "vitest",
            "test:coverage": "vitest run --coverage",
            "lint": "eslint src/ --ext .ts,.tsx",
            "format": "prettier --write src/"
        },
        "dependencies": {
            "react": "^18.2.0",
            "react-dom": "^18.2.0",
            "react-router-dom": "^6.20.0"
        },
        "devDependencies": {
            "@types/react": "^18.2.0",
            "@types/react-dom": "^18.2.0",
            "@vitejs/plugin-react": "^4.2.0",
            "typescript": "^5.3.0",
            "vite": "^5.0.0",
            "vitest": "^1.0.0",
            "@testing-library/react": "^14.1.0",
            "@testing-library/jest-dom": "^6.1.0",
            "eslint": "^8.55.0",
            "prettier": "^3.1.0"
        }
    }
    pkg_path = project_dir / "package.json"
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(pkg_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(package_json, f, indent=2)
        f.write("\n")
    created.append(str(pkg_path))

    # src/App.tsx
    app_content = f'''import React from 'react';

const App: React.FC = () => {{
  return (
    <div className="app">
      <header>
        <h1>{project_name}</h1>
        <p>Classification: {classification}</p>
      </header>
      <main>
        <p>Application content goes here.</p>
      </main>
    </div>
  );
}};

export default App;
'''
    write_file(project_dir / "src" / "App.tsx", app_content, classification)

    # src/main.tsx
    main_content = '''import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
'''
    write_file(project_dir / "src" / "main.tsx", main_content, classification)

    # tsconfig.json
    tsconfig = {
        "compilerOptions": {
            "target": "ES2020",
            "useDefineForClassFields": True,
            "lib": ["ES2020", "DOM", "DOM.Iterable"],
            "module": "ESNext",
            "skipLibCheck": True,
            "moduleResolution": "bundler",
            "allowImportingTsExtensions": True,
            "resolveJsonModule": True,
            "isolatedModules": True,
            "noEmit": True,
            "jsx": "react-jsx",
            "strict": True,
            "noUnusedLocals": True,
            "noUnusedParameters": True,
            "noFallthroughCasesInSwitch": True
        },
        "include": ["src"],
        "references": [{"path": "./tsconfig.node.json"}]
    }
    ts_path = project_dir / "tsconfig.json"
    with open(ts_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(tsconfig, f, indent=2)
        f.write("\n")
    created.append(str(ts_path))

    # Dockerfile
    dockerfile_content = f"""FROM node:20-slim AS build

LABEL maintainer="ICDEV System"
LABEL classification="{classification}"

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

FROM nginx:1.25-alpine

COPY --from=build /app/dist /usr/share/nginx/html

RUN addgroup -S appuser && adduser -S appuser -G appuser
RUN chown -R appuser:appuser /usr/share/nginx/html && \\
    chmod -R 750 /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD wget -q --spider http://localhost:80/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
"""
    write_file(project_dir / "Dockerfile", dockerfile_content, classification)

    # .gitlab-ci.yml
    gitlab_ci_content = """stages:
  - test
  - security
  - build
  - deploy

cache:
  paths:
    - node_modules/

unit-tests:
  stage: test
  image: node:20-slim
  script:
    - npm ci --ignore-scripts
    - npm run test:coverage
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml

lint:
  stage: test
  image: node:20-slim
  script:
    - npm ci --ignore-scripts
    - npm run lint

dependency-audit:
  stage: security
  image: node:20-slim
  script:
    - npm audit --json > npm-audit-report.json || true
  artifacts:
    paths: [npm-audit-report.json]

build-image:
  stage: build
  image: docker:24
  services: [docker:24-dind]
  script:
    - docker build -t $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA .
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
    - docker push $CI_REGISTRY_IMAGE:$CI_COMMIT_SHA
  only: [main, develop]
"""
    write_file(project_dir / ".gitlab-ci.yml", gitlab_ci_content, classification)

    created.extend([
        str(project_dir / "src" / "App.tsx"),
        str(project_dir / "src" / "main.tsx"),
        str(project_dir / "Dockerfile"),
        str(project_dir / ".gitlab-ci.yml"),
    ])
    return created


# Dispatcher mapping project types to scaffold functions
SCAFFOLDERS = {
    "webapp": scaffold_python_webapp,
    "microservice": scaffold_microservice,
    "api": scaffold_api,
    "cli": scaffold_cli,
    "data_pipeline": scaffold_data_pipeline,
    "iac": scaffold_iac,
    "frontend": scaffold_js_frontend,
}


def scaffold_project(project_dir: str, project_type: str, project_name: str = None, classification: str = "CUI") -> dict:
    """Main entry point: scaffold a project of the given type.

    Args:
        project_dir: Path to the project directory (will be created if needed).
        project_type: One of webapp, microservice, api, cli, data_pipeline, iac, frontend.
        project_name: Human-readable project name (derived from dir if omitted).
        classification: Data classification (CUI, FOUO, Public).

    Returns:
        dict with project_dir, type, classification, and list of created files/dirs.
    """
    project_path = Path(project_dir)
    project_path.mkdir(parents=True, exist_ok=True)

    if not project_name:
        project_name = project_path.name.replace("-", " ").replace("_", " ").title()

    if project_type not in SCAFFOLDERS:
        raise ValueError(f"Unknown project type '{project_type}'. Supported: {list(SCAFFOLDERS.keys())}")

    scaffolder = SCAFFOLDERS[project_type]
    created = scaffolder(project_path, project_name, classification)

    return {
        "project_dir": str(project_path),
        "project_name": project_name,
        "type": project_type,
        "classification": classification,
        "files_created": len(created),
        "paths": created,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate project directory structure based on type"
    )
    parser.add_argument(
        "--project-dir", required=True,
        help="Path to the project directory to scaffold"
    )
    parser.add_argument(
        "--type", required=True, choices=list(SCAFFOLDERS.keys()),
        help="Project type"
    )
    parser.add_argument(
        "--name",
        help="Project name (defaults to directory name)"
    )
    parser.add_argument(
        "--classification", default="CUI", choices=["CUI", "FOUO", "Public"],
        help="Data classification level"
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format"
    )
    args = parser.parse_args()

    result = scaffold_project(
        project_dir=args.project_dir,
        project_type=args.type,
        project_name=args.name,
        classification=args.classification,
    )

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"Scaffolded {result['type']} project: {result['project_name']}")
        print(f"  Directory: {result['project_dir']}")
        print(f"  Classification: {result['classification']}")
        print(f"  Files/dirs created: {result['files_created']}")
        print()
        for p in result["paths"]:
            # Show relative path from project dir for readability
            try:
                rel = Path(p).relative_to(result["project_dir"])
                print(f"  {rel}")
            except ValueError:
                print(f"  {p}")


if __name__ == "__main__":
    main()
