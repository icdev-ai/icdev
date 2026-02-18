#!/usr/bin/env python3
"""Project Scaffolder — generates project directory structures from templates.

Implements all project types:
- scaffold_python_backend  -> pyproject.toml, src/, tests/, Dockerfile, .gitignore
- scaffold_javascript_frontend -> package.json, src/, tests/, Dockerfile, .gitignore
- scaffold_microservice    -> backend + Dockerfile + k8s/
- scaffold_api             -> Flask/FastAPI API template
- scaffold_cli             -> CLI tool template with argparse
- scaffold_data_pipeline   -> ETL pipeline template

All templates include CUI markings, README with CUI banners, compliance/ dir.
CLI: python tools/builder/scaffolder.py --project-path PATH --name "my-app" --type webapp
"""

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Dynamic classification support — use classification_manager when available,
# fall back to CUI defaults for backward compatibility.
try:
    from tools.compliance.classification_manager import (
        get_marking_banner,
        get_code_header,
        get_document_banner,
        get_classification_for_il,
    )
    _HAS_CLASSIFICATION_MGR = True
except ImportError:
    _HAS_CLASSIFICATION_MGR = False


def _get_banner(classification="CUI"):
    """Get document banner for the given classification level."""
    if _HAS_CLASSIFICATION_MGR:
        banners = get_document_banner(classification)
        return banners.get("header", _DEFAULT_CUI_BANNER)
    return _DEFAULT_CUI_BANNER


def _get_code_hdr(classification="CUI", language="python"):
    """Get code header for the given classification and language."""
    if _HAS_CLASSIFICATION_MGR:
        return get_code_header(classification, language)
    return _DEFAULT_CUI_CODE_HEADER


def _get_banner_md(classification="CUI"):
    """Get markdown-formatted banner."""
    if _HAS_CLASSIFICATION_MGR:
        banner_text = get_marking_banner(classification)
        return f"> **{banner_text.strip().splitlines()[0] if banner_text else 'CUI // SP-CTI'}**\n> Controlled by: Department of Defense | Distribution D\n> This document contains Controlled Unclassified Information (CUI).\n"
    return _DEFAULT_CUI_BANNER_MD


_DEFAULT_CUI_BANNER = """\
//////////////////////////////////////////////////////////////////
CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
Distribution: Distribution D - Authorized DoD Personnel Only
//////////////////////////////////////////////////////////////////"""

_DEFAULT_CUI_CODE_HEADER = """\
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""

_DEFAULT_CUI_BANNER_MD = """\
> **CUI // SP-CTI**
> Controlled by: Department of Defense | Distribution D
> This document contains Controlled Unclassified Information (CUI).
"""

# Backward-compatible aliases — used throughout the scaffolder
CUI_BANNER = _DEFAULT_CUI_BANNER
CUI_CODE_HEADER = _DEFAULT_CUI_CODE_HEADER
CUI_BANNER_MD = _DEFAULT_CUI_BANNER_MD


def _write_file(path: Path, content: str) -> None:
    """Write content to a file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _create_gitkeep(directory: Path) -> None:
    """Create a .gitkeep in an empty directory."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".gitkeep").write_text("", encoding="utf-8")


def _common_gitignore() -> str:
    """Return a common .gitignore for Python/JS projects."""
    return """\
# Python
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
.env

# JavaScript
node_modules/
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Coverage
htmlcov/
.coverage
coverage.xml
*.cover

# Compiled
*.so
*.dylib

# Tmp
.tmp/
tmp/
"""


def _readme_content(name: str, project_type: str, description: str = "") -> str:
    """Generate a README with CUI banners."""
    desc = description or f"A {project_type} project scaffolded by ICDEV Builder."
    return f"""{CUI_BANNER}

# {name}

{CUI_BANNER_MD}

## Overview

{desc}

## Getting Started

### Prerequisites

- Python 3.10+ (for Python projects)
- Node.js 18+ (for JavaScript projects)
- Docker (for containerized deployments)

### Installation

```bash
# Python
pip install -e .

# JavaScript
npm install
```

### Running Tests

```bash
# Python
pytest tests/

# JavaScript
npm test
```

### Running Locally

```bash
# Python
python -m src.main

# JavaScript
npm start
```

## Compliance

See `compliance/` directory for security and compliance artifacts.

## Classification

{CUI_BANNER}
"""


def _compliance_readme(classification: str = "CUI") -> str:
    """Generate a compliance directory README."""
    banner = _get_banner(classification)
    return f"""{banner}

# Compliance Artifacts

This directory contains compliance documentation and artifacts for this project.

## Contents

- `ssp/` - System Security Plan documents
- `poam/` - Plan of Action and Milestones
- `stig/` - STIG checklists and findings
- `sbom/` - Software Bill of Materials
- `fedramp/` - FedRAMP assessment artifacts
- `cmmc/` - CMMC assessment artifacts
- `oscal/` - OSCAL machine-readable artifacts
- `emass/` - eMASS export files

## Classification

All artifacts in this directory are marked per project classification level.

{banner}
"""


def scaffold_python_backend(project_path: str, name: str) -> List[str]:
    """Scaffold a Python backend project.

    Creates:
    - pyproject.toml
    - src/main.py, src/__init__.py
    - tests/conftest.py, tests/features/.gitkeep, tests/steps/.gitkeep
    - Dockerfile
    - .gitignore
    - README.md with CUI banners
    - compliance/ directory

    Returns:
        List of created file paths.
    """
    root = Path(project_path) / name
    files = []

    # pyproject.toml
    pyproject = f"""\
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "{name}"
version = "0.1.0"
description = "Python backend project - CUI // SP-CTI"
requires-python = ">=3.10"
license = {{text = "Proprietary - CUI"}}

[project.optional-dependencies]
dev = ["pytest>=7.0", "behave>=1.2", "flake8>=6.0", "black>=23.0", "isort>=5.0"]

[tool.black]
line-length = 100

[tool.isort]
profile = "black"
line_length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
"""
    _write_file(root / "pyproject.toml", pyproject)
    files.append(str(root / "pyproject.toml"))

    # src/__init__.py
    _write_file(root / "src" / "__init__.py", f'{CUI_CODE_HEADER}\n"""Package init."""\n')
    files.append(str(root / "src" / "__init__.py"))

    # src/main.py
    main_py = f'''{CUI_CODE_HEADER}
"""Main entry point for {name}."""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    """Application entry point.

    Returns:
        Exit code (0 for success).
    """
    logger.info("Starting {name}")
    # TODO: Add application logic here
    logger.info("{name} completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
    _write_file(root / "src" / "main.py", main_py)
    files.append(str(root / "src" / "main.py"))

    # tests/conftest.py
    conftest = f'''{CUI_CODE_HEADER}
"""Pytest configuration and shared fixtures."""

import pytest


@pytest.fixture
def sample_data():
    """Provide sample test data."""
    return {{"name": "test", "status": "active"}}
'''
    _write_file(root / "tests" / "conftest.py", conftest)
    files.append(str(root / "tests" / "conftest.py"))

    # tests/features/.gitkeep and tests/steps/.gitkeep
    _create_gitkeep(root / "tests" / "features")
    files.append(str(root / "tests" / "features" / ".gitkeep"))
    _create_gitkeep(root / "tests" / "steps")
    files.append(str(root / "tests" / "steps" / ".gitkeep"))

    # Dockerfile
    dockerfile = """\
# CUI // SP-CTI
# STIG-hardened Python container
FROM python:3.11-slim AS base

# Security: run as non-root
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Install dependencies first for layer caching
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy application
COPY src/ src/

# Security: drop privileges
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD python -c "print('healthy')" || exit 1

ENTRYPOINT ["python", "-m", "src.main"]
"""
    _write_file(root / "Dockerfile", dockerfile)
    files.append(str(root / "Dockerfile"))

    # .gitignore
    _write_file(root / ".gitignore", _common_gitignore())
    files.append(str(root / ".gitignore"))

    # README.md
    _write_file(root / "README.md", _readme_content(name, "python-backend"))
    files.append(str(root / "README.md"))

    # compliance/ — includes multi-framework subdirs
    _write_file(root / "compliance" / "README.md", _compliance_readme())
    files.append(str(root / "compliance" / "README.md"))
    for sub in ["ssp", "poam", "stig", "sbom", "fedramp", "cmmc", "oscal", "emass"]:
        _create_gitkeep(root / "compliance" / sub)
        files.append(str(root / "compliance" / sub / ".gitkeep"))

    print(f"Scaffolded Python backend: {root}")
    return files


def scaffold_javascript_frontend(project_path: str, name: str) -> List[str]:
    """Scaffold a JavaScript frontend project.

    Creates:
    - package.json
    - src/index.js
    - tests/.gitkeep
    - Dockerfile
    - .gitignore
    - README.md with CUI banners
    - compliance/ directory

    Returns:
        List of created file paths.
    """
    root = Path(project_path) / name
    files = []

    # package.json
    package_json = json.dumps({
        "name": name,
        "version": "0.1.0",
        "description": "JavaScript frontend project - CUI // SP-CTI",
        "main": "src/index.js",
        "scripts": {
            "start": "node src/index.js",
            "test": "jest",
            "lint": "eslint src/",
            "format": "prettier --write src/",
            "build": "echo 'Build step placeholder'"
        },
        "devDependencies": {
            "jest": "^29.0.0",
            "eslint": "^8.0.0",
            "prettier": "^3.0.0"
        },
        "license": "UNLICENSED",
        "private": True
    }, indent=2) + "\n"
    _write_file(root / "package.json", package_json)
    files.append(str(root / "package.json"))

    # src/index.js
    index_js = f"""\
// CUI // SP-CTI
// Controlled by: Department of Defense
// CUI Category: CTI
// Distribution: D
// POC: ICDEV System Administrator

/**
 * Main entry point for {name}.
 * @module {name}
 */

'use strict';

/**
 * Initialize the application.
 */
function main() {{
    console.log('{name} started');
    // TODO: Add application logic here
}}

main();

module.exports = {{ main }};
"""
    _write_file(root / "src" / "index.js", index_js)
    files.append(str(root / "src" / "index.js"))

    # tests/.gitkeep
    _create_gitkeep(root / "tests")
    files.append(str(root / "tests" / ".gitkeep"))

    # Dockerfile
    dockerfile = """\
# CUI // SP-CTI
# Node.js container
FROM node:18-alpine AS base

# Security: run as non-root
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm ci --only=production && npm cache clean --force

# Copy application
COPY src/ src/

# Security: drop privileges
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD node -e "console.log('healthy')" || exit 1

ENTRYPOINT ["node", "src/index.js"]
"""
    _write_file(root / "Dockerfile", dockerfile)
    files.append(str(root / "Dockerfile"))

    # .gitignore
    _write_file(root / ".gitignore", _common_gitignore())
    files.append(str(root / ".gitignore"))

    # README.md
    _write_file(root / "README.md", _readme_content(name, "javascript-frontend"))
    files.append(str(root / "README.md"))

    # compliance/
    _write_file(root / "compliance" / "README.md", _compliance_readme())
    files.append(str(root / "compliance" / "README.md"))
    for sub in ["ssp", "poam", "stig", "sbom"]:
        _create_gitkeep(root / "compliance" / sub)
        files.append(str(root / "compliance" / sub / ".gitkeep"))

    print(f"Scaffolded JavaScript frontend: {root}")
    return files


def scaffold_microservice(project_path: str, name: str) -> List[str]:
    """Scaffold a microservice project.

    Combines Python backend + Dockerfile + Kubernetes manifests.

    Returns:
        List of created file paths.
    """
    # Start with Python backend
    files = scaffold_python_backend(project_path, name)
    root = Path(project_path) / name

    # k8s/deployment.yaml
    deployment_yaml = f"""\
# CUI // SP-CTI
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  labels:
    app: {name}
    classification: cui
spec:
  replicas: 2
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
      containers:
        - name: {name}
          image: {name}:latest
          ports:
            - containerPort: 8000
          resources:
            requests:
              cpu: "100m"
              memory: "128Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
"""
    _write_file(root / "k8s" / "deployment.yaml", deployment_yaml)
    files.append(str(root / "k8s" / "deployment.yaml"))

    # k8s/service.yaml
    service_yaml = f"""\
# CUI // SP-CTI
apiVersion: v1
kind: Service
metadata:
  name: {name}
  labels:
    app: {name}
spec:
  selector:
    app: {name}
  ports:
    - port: 80
      targetPort: 8000
      protocol: TCP
  type: ClusterIP
"""
    _write_file(root / "k8s" / "service.yaml", service_yaml)
    files.append(str(root / "k8s" / "service.yaml"))

    # k8s/configmap.yaml
    configmap_yaml = f"""\
# CUI // SP-CTI
apiVersion: v1
kind: ConfigMap
metadata:
  name: {name}-config
data:
  LOG_LEVEL: "INFO"
  APP_ENV: "production"
"""
    _write_file(root / "k8s" / "configmap.yaml", configmap_yaml)
    files.append(str(root / "k8s" / "configmap.yaml"))

    print(f"Scaffolded microservice (with k8s): {root}")
    return files


def scaffold_api(project_path: str, name: str) -> List[str]:
    """Scaffold a Flask/FastAPI API project.

    Returns:
        List of created file paths.
    """
    root = Path(project_path) / name
    files = []

    # pyproject.toml
    pyproject = f"""\
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "{name}"
version = "0.1.0"
description = "API project - CUI // SP-CTI"
requires-python = ">=3.10"
dependencies = ["flask>=3.0", "flask-cors>=4.0"]

[project.optional-dependencies]
dev = ["pytest>=7.0", "behave>=1.2", "flake8>=6.0", "black>=23.0", "isort>=5.0"]

[tool.black]
line-length = 100

[tool.isort]
profile = "black"

[tool.pytest.ini_options]
testpaths = ["tests"]
"""
    _write_file(root / "pyproject.toml", pyproject)
    files.append(str(root / "pyproject.toml"))

    # src/__init__.py
    _write_file(root / "src" / "__init__.py", f'{CUI_CODE_HEADER}\n"""Package init."""\n')
    files.append(str(root / "src" / "__init__.py"))

    # src/app.py
    app_py = f'''{CUI_CODE_HEADER}
"""Flask application factory for {name}."""

from flask import Flask, jsonify
from flask_cors import CORS


def create_app(config: dict = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        config: Optional configuration overrides.

    Returns:
        Configured Flask app instance.
    """
    app = Flask(__name__)
    CORS(app)

    if config:
        app.config.update(config)

    @app.route("/health", methods=["GET"])
    def health():
        """Health check endpoint."""
        return jsonify({{"status": "healthy", "service": "{name}"}})

    @app.route("/api/v1/status", methods=["GET"])
    def status():
        """API status endpoint."""
        return jsonify({{
            "service": "{name}",
            "version": "0.1.0",
            "status": "operational",
        }})

    # Register blueprints here
    # from src.routes import my_blueprint
    # app.register_blueprint(my_blueprint)

    return app
'''
    _write_file(root / "src" / "app.py", app_py)
    files.append(str(root / "src" / "app.py"))

    # src/main.py
    main_py = f'''{CUI_CODE_HEADER}
"""Main entry point for {name} API."""

from src.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
'''
    _write_file(root / "src" / "main.py", main_py)
    files.append(str(root / "src" / "main.py"))

    # tests/conftest.py
    conftest = f'''{CUI_CODE_HEADER}
"""Pytest configuration and fixtures for API tests."""

import pytest
from src.app import create_app


@pytest.fixture
def app():
    """Create test application."""
    app = create_app({{"TESTING": True}})
    return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()
'''
    _write_file(root / "tests" / "conftest.py", conftest)
    files.append(str(root / "tests" / "conftest.py"))

    _create_gitkeep(root / "tests" / "features")
    files.append(str(root / "tests" / "features" / ".gitkeep"))
    _create_gitkeep(root / "tests" / "steps")
    files.append(str(root / "tests" / "steps" / ".gitkeep"))

    # Dockerfile
    dockerfile = """\
# CUI // SP-CTI
FROM python:3.11-slim AS base

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY src/ src/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["python", "-m", "src.main"]
"""
    _write_file(root / "Dockerfile", dockerfile)
    files.append(str(root / "Dockerfile"))

    _write_file(root / ".gitignore", _common_gitignore())
    files.append(str(root / ".gitignore"))

    _write_file(root / "README.md", _readme_content(name, "api"))
    files.append(str(root / "README.md"))

    _write_file(root / "compliance" / "README.md", _compliance_readme())
    files.append(str(root / "compliance" / "README.md"))
    for sub in ["ssp", "poam", "stig", "sbom"]:
        _create_gitkeep(root / "compliance" / sub)
        files.append(str(root / "compliance" / sub / ".gitkeep"))

    print(f"Scaffolded API project: {root}")
    return files


def scaffold_cli(project_path: str, name: str) -> List[str]:
    """Scaffold a CLI tool project with argparse.

    Returns:
        List of created file paths.
    """
    root = Path(project_path) / name
    files = []

    # pyproject.toml
    pyproject = f"""\
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "{name}"
version = "0.1.0"
description = "CLI tool - CUI // SP-CTI"
requires-python = ">=3.10"

[project.scripts]
{name} = "src.cli:main"

[project.optional-dependencies]
dev = ["pytest>=7.0", "flake8>=6.0", "black>=23.0"]

[tool.black]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
"""
    _write_file(root / "pyproject.toml", pyproject)
    files.append(str(root / "pyproject.toml"))

    # src/__init__.py
    _write_file(root / "src" / "__init__.py", f'{CUI_CODE_HEADER}\n"""Package init."""\n')
    files.append(str(root / "src" / "__init__.py"))

    # src/cli.py
    cli_py = f'''{CUI_CODE_HEADER}
"""Command-line interface for {name}."""

import argparse
import json
import logging
import sys
from typing import List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the main command.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success).
    """
    logger.info(f"Running {name} with input: {{args.input}}")
    # TODO: Implement command logic
    result = {{"status": "success", "input": args.input}}
    if args.output_format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"Result: {{result}}")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Print version information."""
    print(f"{name} version 0.1.0")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        Configured ArgumentParser.
    """
    parser = argparse.ArgumentParser(
        prog="{name}",
        description="{name} - CUI // SP-CTI",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )
    parser.add_argument(
        "--output-format", choices=["text", "json"], default="text",
        help="Output format (default: text)"
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # run command
    p_run = sub.add_parser("run", help="Execute the main operation")
    p_run.add_argument("--input", required=True, help="Input data or file path")
    p_run.add_argument("--dry-run", action="store_true", help="Dry run mode")

    # version command
    sub.add_parser("version", help="Show version")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments (defaults to sys.argv).

    Returns:
        Exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.command:
        parser.print_help()
        return 1

    commands = {{
        "run": cmd_run,
        "version": cmd_version,
    }}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
'''
    _write_file(root / "src" / "cli.py", cli_py)
    files.append(str(root / "src" / "cli.py"))

    # tests/conftest.py
    conftest = f'''{CUI_CODE_HEADER}
"""Pytest configuration for CLI tests."""

import pytest
'''
    _write_file(root / "tests" / "conftest.py", conftest)
    files.append(str(root / "tests" / "conftest.py"))
    _create_gitkeep(root / "tests" / "features")
    files.append(str(root / "tests" / "features" / ".gitkeep"))
    _create_gitkeep(root / "tests" / "steps")
    files.append(str(root / "tests" / "steps" / ".gitkeep"))

    _write_file(root / ".gitignore", _common_gitignore())
    files.append(str(root / ".gitignore"))

    _write_file(root / "README.md", _readme_content(name, "cli"))
    files.append(str(root / "README.md"))

    _write_file(root / "compliance" / "README.md", _compliance_readme())
    files.append(str(root / "compliance" / "README.md"))
    for sub in ["ssp", "poam", "stig", "sbom"]:
        _create_gitkeep(root / "compliance" / sub)
        files.append(str(root / "compliance" / sub / ".gitkeep"))

    print(f"Scaffolded CLI project: {root}")
    return files


def scaffold_data_pipeline(project_path: str, name: str) -> List[str]:
    """Scaffold an ETL data pipeline project.

    Returns:
        List of created file paths.
    """
    root = Path(project_path) / name
    files = []

    # pyproject.toml
    pyproject = f"""\
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "{name}"
version = "0.1.0"
description = "Data pipeline (ETL) - CUI // SP-CTI"
requires-python = ">=3.10"

[project.optional-dependencies]
dev = ["pytest>=7.0", "flake8>=6.0", "black>=23.0"]

[tool.black]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
"""
    _write_file(root / "pyproject.toml", pyproject)
    files.append(str(root / "pyproject.toml"))

    # src/__init__.py
    _write_file(root / "src" / "__init__.py", f'{CUI_CODE_HEADER}\n"""Package init."""\n')
    files.append(str(root / "src" / "__init__.py"))

    # src/pipeline.py
    pipeline_py = f'''{CUI_CODE_HEADER}
"""ETL pipeline for {name}.

Implements Extract-Transform-Load pattern with error handling,
logging, and checkpoint support.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class PipelineStep:
    """A single step in the ETL pipeline."""

    def __init__(self, name: str, func: Callable, description: str = "") -> None:
        self.name = name
        self.func = func
        self.description = description

    def run(self, data: Any) -> Any:
        """Execute this pipeline step.

        Args:
            data: Input data from the previous step.

        Returns:
            Transformed data for the next step.
        """
        logger.info(f"Running step: {{self.name}}")
        start = datetime.utcnow()
        result = self.func(data)
        elapsed = (datetime.utcnow() - start).total_seconds()
        logger.info(f"Step {{self.name}} completed in {{elapsed:.2f}}s")
        return result


class Pipeline:
    """ETL pipeline with ordered steps and error handling."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.steps: List[PipelineStep] = []
        self.checkpoints: Dict[str, Any] = {{}}

    def add_step(self, name: str, func: Callable, description: str = "") -> "Pipeline":
        """Add a step to the pipeline.

        Args:
            name: Step name.
            func: Callable that takes data and returns transformed data.
            description: Human-readable description.

        Returns:
            Self for chaining.
        """
        self.steps.append(PipelineStep(name, func, description))
        return self

    def run(self, initial_data: Any = None, resume_from: Optional[str] = None) -> Any:
        """Execute the full pipeline.

        Args:
            initial_data: Starting data for the pipeline.
            resume_from: Optional step name to resume from (uses checkpoint).

        Returns:
            Final output data.
        """
        logger.info(f"Starting pipeline: {{self.name}} ({{len(self.steps)}} steps)")
        data = initial_data
        start_idx = 0

        if resume_from:
            for i, step in enumerate(self.steps):
                if step.name == resume_from:
                    start_idx = i
                    data = self.checkpoints.get(resume_from, data)
                    logger.info(f"Resuming from step: {{resume_from}}")
                    break

        for step in self.steps[start_idx:]:
            try:
                data = step.run(data)
                self.checkpoints[step.name] = data
            except Exception as e:
                logger.error(f"Pipeline failed at step {{step.name}}: {{e}}")
                raise

        logger.info(f"Pipeline {{self.name}} completed successfully")
        return data


def extract(source: Any) -> Any:
    """Extract data from source.

    Args:
        source: Data source (file path, URL, or raw data).

    Returns:
        Raw extracted data.
    """
    logger.info(f"Extracting data from source")
    # TODO: Implement extraction logic
    return source


def transform(data: Any) -> Any:
    """Transform extracted data.

    Args:
        data: Raw data to transform.

    Returns:
        Transformed data.
    """
    logger.info(f"Transforming data")
    # TODO: Implement transformation logic
    return data


def load(data: Any) -> Any:
    """Load transformed data to destination.

    Args:
        data: Transformed data to load.

    Returns:
        Load result/confirmation.
    """
    logger.info(f"Loading data to destination")
    # TODO: Implement load logic
    return {{"status": "loaded", "timestamp": datetime.utcnow().isoformat()}}


def create_default_pipeline() -> Pipeline:
    """Create the default ETL pipeline.

    Returns:
        Configured Pipeline instance.
    """
    return (
        Pipeline("{name}")
        .add_step("extract", extract, "Extract data from source")
        .add_step("transform", transform, "Transform and clean data")
        .add_step("load", load, "Load data to destination")
    )
'''
    _write_file(root / "src" / "pipeline.py", pipeline_py)
    files.append(str(root / "src" / "pipeline.py"))

    # src/main.py
    main_py = f'''{CUI_CODE_HEADER}
"""Main entry point for {name} data pipeline."""

import argparse
import logging
import sys

from src.pipeline import create_default_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> int:
    """Run the data pipeline."""
    parser = argparse.ArgumentParser(description="{name} data pipeline")
    parser.add_argument("--source", help="Data source path or URL")
    parser.add_argument("--resume-from", help="Step name to resume from")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    args = parser.parse_args()

    pipeline = create_default_pipeline()
    result = pipeline.run(initial_data=args.source, resume_from=args.resume_from)
    print(f"Pipeline result: {{result}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
    _write_file(root / "src" / "main.py", main_py)
    files.append(str(root / "src" / "main.py"))

    # tests/conftest.py
    conftest = f'''{CUI_CODE_HEADER}
"""Pytest configuration for pipeline tests."""

import pytest
'''
    _write_file(root / "tests" / "conftest.py", conftest)
    files.append(str(root / "tests" / "conftest.py"))
    _create_gitkeep(root / "tests" / "features")
    files.append(str(root / "tests" / "features" / ".gitkeep"))
    _create_gitkeep(root / "tests" / "steps")
    files.append(str(root / "tests" / "steps" / ".gitkeep"))

    # Dockerfile
    dockerfile = """\
# CUI // SP-CTI
FROM python:3.11-slim AS base

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser
WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY src/ src/

USER appuser

ENTRYPOINT ["python", "-m", "src.main"]
"""
    _write_file(root / "Dockerfile", dockerfile)
    files.append(str(root / "Dockerfile"))

    _write_file(root / ".gitignore", _common_gitignore())
    files.append(str(root / ".gitignore"))

    _write_file(root / "README.md", _readme_content(name, "data-pipeline"))
    files.append(str(root / "README.md"))

    _write_file(root / "compliance" / "README.md", _compliance_readme())
    files.append(str(root / "compliance" / "README.md"))
    for sub in ["ssp", "poam", "stig", "sbom"]:
        _create_gitkeep(root / "compliance" / sub)
        files.append(str(root / "compliance" / sub / ".gitkeep"))

    print(f"Scaffolded data pipeline: {root}")
    return files


# Dispatch table
SCAFFOLDERS = {
    "python-backend": scaffold_python_backend,
    "backend": scaffold_python_backend,
    "javascript-frontend": scaffold_javascript_frontend,
    "frontend": scaffold_javascript_frontend,
    "microservice": scaffold_microservice,
    "api": scaffold_api,
    "webapp": scaffold_api,  # Alias
    "cli": scaffold_cli,
    "data-pipeline": scaffold_data_pipeline,
    "data_pipeline": scaffold_data_pipeline,
    "etl": scaffold_data_pipeline,
}

# Phase 16: Multi-language scaffolders (Java, Go, Rust, C#, TypeScript)
try:
    import importlib.util
    _ext_path = Path(__file__).parent / "scaffolder_extended.py"
    if _ext_path.exists():
        _spec = importlib.util.spec_from_file_location("scaffolder_extended", _ext_path)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        SCAFFOLDERS["java-backend"] = _mod.scaffold_java_backend
        SCAFFOLDERS["java"] = _mod.scaffold_java_backend
        SCAFFOLDERS["go-backend"] = _mod.scaffold_go_backend
        SCAFFOLDERS["go"] = _mod.scaffold_go_backend
        SCAFFOLDERS["rust-backend"] = _mod.scaffold_rust_backend
        SCAFFOLDERS["rust"] = _mod.scaffold_rust_backend
        SCAFFOLDERS["csharp-backend"] = _mod.scaffold_csharp_backend
        SCAFFOLDERS["csharp"] = _mod.scaffold_csharp_backend
        SCAFFOLDERS["dotnet"] = _mod.scaffold_csharp_backend
        SCAFFOLDERS["typescript-backend"] = _mod.scaffold_typescript_backend
        SCAFFOLDERS["typescript"] = _mod.scaffold_typescript_backend
except Exception:
    pass  # Extended scaffolders not available


def _log_audit(project_path: str, name: str, project_type: str, files: List[str]) -> None:
    """Log scaffolding to audit trail."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            """INSERT INTO audit_trail (project_id, event_type, actor, action, details, affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                None,
                "project_created",
                "builder/scaffolder",
                f"Scaffolded {project_type} project: {name}",
                json.dumps({"type": project_type, "name": name}),
                json.dumps(files[:20]),  # Limit file list
                "CUI",
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: audit logging failed: {e}")


def _run_agentic_generation(args, base_files):
    """Run agentic generation pipeline after base scaffold.

    Phase 19: When --agentic is set, this runs:
    1. Load or generate fitness scorecard
    2. Generate blueprint via app_blueprint.py
    3. Generate child app via child_app_generator.py

    The base scaffold provides the language-specific project structure.
    The agentic pipeline adds GOTCHA framework, agents, memory, CI/CD, etc.
    """
    import importlib.util

    project_path = Path(args.project_path) / args.name

    # Step 1: Load fitness scorecard
    scorecard = None
    if args.fitness_scorecard:
        scorecard_path = Path(args.fitness_scorecard)
        if scorecard_path.exists():
            scorecard = json.load(open(scorecard_path))
            print(f"  Loaded fitness scorecard: {scorecard.get('overall_score', 'N/A')}")
        else:
            print(f"  Warning: Scorecard not found at {scorecard_path}, using defaults")

    if not scorecard:
        # Generate default scorecard indicating agentic architecture
        scorecard = {
            "component": args.name,
            "overall_score": 6.5,
            "scores": {
                "data_complexity": 5, "decision_complexity": 7,
                "user_interaction": 6, "integration_density": 7,
                "compliance_sensitivity": 7, "scale_variability": 5,
            },
            "recommendations": {"architecture": "agent"},
        }

    # Step 2: Parse user decisions
    user_decisions = {}
    if args.user_decisions:
        try:
            ud_path = Path(args.user_decisions)
            if ud_path.exists():
                user_decisions = json.load(open(ud_path))
            else:
                user_decisions = json.loads(args.user_decisions)
        except (json.JSONDecodeError, Exception) as e:
            print(f"  Warning: Could not parse user-decisions: {e}")

    # Step 3: Generate blueprint
    bp_mod_path = Path(__file__).parent / "app_blueprint.py"
    if not bp_mod_path.exists():
        print("  Error: app_blueprint.py not found — cannot run agentic generation")
        return

    spec = importlib.util.spec_from_file_location("app_blueprint", bp_mod_path)
    bp_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp_mod)

    blueprint = bp_mod.generate_blueprint(
        scorecard=scorecard,
        user_decisions=user_decisions,
        app_name=args.name,
        port_offset=getattr(args, "port_offset", 1000),
        cloud_provider=getattr(args, "cloud_provider", "aws"),
        cloud_region=getattr(args, "cloud_region", "us-gov-west-1"),
        govcloud=getattr(args, "govcloud", False),
        parent_callback_url=getattr(args, "parent_callback_url", None),
        impact_level=getattr(args, "impact_level", "IL4"),
    )
    print(f"  Blueprint generated: {blueprint.get('blueprint_id', 'N/A')}")
    print(f"    Capabilities: {sum(1 for v in blueprint.get('capabilities', {}).values() if v)}")
    print(f"    Agents: {len(blueprint.get('agents', []))}")

    # Step 4: Generate child app (overlay onto existing scaffold)
    gen_mod_path = Path(__file__).parent / "child_app_generator.py"
    if not gen_mod_path.exists():
        print("  Error: child_app_generator.py not found")
        return

    spec2 = importlib.util.spec_from_file_location("child_app_generator", gen_mod_path)
    gen_mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(gen_mod)

    # The child app generator overlays onto the already-scaffolded directory
    results = gen_mod.generate_child_app(
        blueprint=blueprint,
        project_path=args.project_path,
        name=args.name,
        icdev_root=BASE_DIR,
        db_path=DB_PATH,
    )

    status = results.get("status", "unknown")
    steps = results.get("steps", {})
    succeeded = sum(1 for r in steps.values() if r.get("status") == "success")
    print(f"  Agentic generation: {status} ({succeeded}/{len(steps)} steps)")

    if results.get("errors"):
        for err in results["errors"]:
            print(f"    Error: {err}")


def main():
    parser = argparse.ArgumentParser(description="Project scaffolding from templates")
    parser.add_argument("--project-path", required=True, help="Parent directory for the project")
    parser.add_argument("--name", required=True, help="Project name")
    parser.add_argument(
        "--type",
        required=True,
        choices=sorted(set(SCAFFOLDERS.keys())),
        help="Project type to scaffold",
    )

    # Phase 26: MOSA scaffolding flag
    parser.add_argument(
        "--mosa", action="store_true",
        help="Add MOSA directories: interfaces/, docs/icd/, docs/tsp/, openapi/")

    # Phase 19: Agentic generation flags
    agentic_group = parser.add_argument_group("agentic generation (Phase 19)")
    agentic_group.add_argument(
        "--agentic", action="store_true",
        help="Generate mini-ICDEV clone with GOTCHA framework, agents, memory, CI/CD")
    agentic_group.add_argument(
        "--fitness-scorecard", type=str, default=None,
        help="Path to fitness scorecard JSON (from agentic_fitness.py)")
    agentic_group.add_argument(
        "--user-decisions", type=str, default=None,
        help="User decisions JSON string or path to JSON file")
    agentic_group.add_argument(
        "--port-offset", type=int, default=1000,
        help="Port offset from ICDEV base ports (default: 1000)")
    agentic_group.add_argument(
        "--parent-callback-url", type=str, default=None,
        help="URL for parent ICDEV A2A callback")
    agentic_group.add_argument(
        "--cloud-provider", type=str, default="aws",
        choices=["aws", "gcp", "azure", "oracle"],
        help="Target cloud provider (default: aws)")
    agentic_group.add_argument(
        "--cloud-region", type=str, default="us-gov-west-1",
        help="Target deployment region (default: us-gov-west-1)")
    agentic_group.add_argument(
        "--govcloud", action="store_true",
        help="Enable GovCloud/Gov-region endpoints")
    agentic_group.add_argument(
        "--impact-level", type=str, default="IL4",
        choices=["IL2", "IL4", "IL5", "IL6"],
        help="DoD Impact Level (default: IL4)")

    args = parser.parse_args()

    # Run base scaffold
    scaffolder = SCAFFOLDERS[args.type]
    files = scaffolder(args.project_path, args.name)

    _log_audit(args.project_path, args.name, args.type, files)

    print(f"\nScaffolded {len(files)} files for '{args.name}' ({args.type})")

    # Phase 26: If --mosa, create MOSA directory structure
    if getattr(args, 'mosa', False):
        mosa_dirs = ["interfaces", "docs/icd", "docs/tsp", "openapi"]
        proj_root = os.path.join(args.project_path, args.name)
        for d in mosa_dirs:
            dp = os.path.join(proj_root, d)
            os.makedirs(dp, exist_ok=True)
            gitkeep = os.path.join(dp, ".gitkeep")
            if not os.path.exists(gitkeep):
                with open(gitkeep, "w") as f:
                    f.write("")
                files.append(gitkeep)
        print(f"  MOSA directories created: {', '.join(mosa_dirs)}")

    # Phase 19: If --agentic, run the full agentic generation pipeline
    if args.agentic:
        print("\n--- Agentic Generation (Phase 19) ---")
        try:
            _run_agentic_generation(args, files)
        except Exception as e:
            print(f"  Agentic generation failed: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
