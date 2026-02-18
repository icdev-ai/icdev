#!/usr/bin/env python3
"""Generate STIG-hardened Dockerfiles for Python and Node.js applications.
Multi-stage builds, non-root user, no shell, minimal base, health checks, CUI labels."""

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
    "# Generator: ICDev Dockerfile Generator\n"
    "# //CUI\n"
)


def _cui_header() -> str:
    return CUI_HEADER.format(timestamp=datetime.utcnow().isoformat())


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Python Dockerfile
# ---------------------------------------------------------------------------
def generate_python(project_path: str, config: dict = None) -> list:
    """Generate STIG-hardened multi-stage Python Dockerfile."""
    config = config or {}
    python_version = config.get("python_version", "3.11")
    app_port = config.get("port", 8080)
    app_module = config.get("app_module", "app.main:app")
    project_name = config.get("project_name", "icdev-app")

    docker_dir = Path(project_path) / "docker"

    dockerfile = f"""{_cui_header()}
# =============================================================================
# Stage 1: Builder — install dependencies in a full image
# =============================================================================
FROM python:{python_version}-slim AS builder

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1

WORKDIR /build

# Install build dependencies
RUN apt-get update && \\
    apt-get install -y --no-install-recommends \\
        gcc \\
        libpq-dev \\
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# =============================================================================
# Stage 2: Production — minimal runtime image
# =============================================================================
FROM python:{python_version}-slim AS production

# --- CUI Classification Labels ---
LABEL classification="CUI" \\
      cui.category="CTI" \\
      cui.dissemination="NOFORN" \\
      maintainer="icdev-team" \\
      org.opencontainers.image.title="{project_name}" \\
      org.opencontainers.image.description="STIG-hardened Python application" \\
      org.opencontainers.image.vendor="ICDev" \\
      org.opencontainers.image.created="{datetime.utcnow().isoformat()}"

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    APP_PORT={app_port} \\
    LOG_LEVEL=info

# Install only runtime dependencies (no compiler, no shell tools)
RUN apt-get update && \\
    apt-get install -y --no-install-recommends \\
        libpq5 \\
        ca-certificates \\
        curl \\
    && rm -rf /var/lib/apt/lists/* \\
    && apt-get purge -y --auto-remove

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Create non-root user with no shell
RUN groupadd --gid 65534 nonroot && \\
    useradd --uid 65534 --gid 65534 --no-create-home --shell /usr/sbin/nologin nonroot

# Create app directory with correct ownership
RUN mkdir -p /app /tmp/app && \\
    chown -R nonroot:nonroot /app /tmp/app

WORKDIR /app

# Copy application code
COPY --chown=nonroot:nonroot . .

# Remove unnecessary files
RUN rm -rf /app/.git /app/.env /app/tests /app/Dockerfile /app/docker-compose* \\
    && find /app -name "*.pyc" -delete \\
    && find /app -name "__pycache__" -type d -exec rm -rf {{}} + 2>/dev/null || true

# Switch to non-root user
USER nonroot:nonroot

# Expose application port
EXPOSE {app_port}

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
    CMD curl -f http://localhost:{app_port}/health || exit 1

# Run with gunicorn for production
CMD ["python", "-m", "gunicorn", "{app_module}", \\
     "--bind", "0.0.0.0:{app_port}", \\
     "--workers", "4", \\
     "--worker-class", "uvicorn.workers.UvicornWorker", \\
     "--timeout", "120", \\
     "--access-logfile", "-", \\
     "--error-logfile", "-"]
"""
    p = _write(docker_dir / "Dockerfile.python", dockerfile)
    files = [str(p)]

    # .dockerignore
    dockerignore = """\
.git
.gitignore
.env
.env.*
*.pyc
__pycache__
.pytest_cache
.mypy_cache
.coverage
htmlcov
tests/
docs/
*.md
Dockerfile*
docker-compose*
.dockerignore
.vscode
.idea
node_modules
.tmp
"""
    p2 = _write(docker_dir / ".dockerignore", dockerignore)
    files.append(str(p2))

    return files


# ---------------------------------------------------------------------------
# Node.js Dockerfile
# ---------------------------------------------------------------------------
def generate_node(project_path: str, config: dict = None) -> list:
    """Generate STIG-hardened multi-stage Node.js Dockerfile."""
    config = config or {}
    node_version = config.get("node_version", "20")
    app_port = config.get("port", 3000)
    project_name = config.get("project_name", "icdev-app")

    docker_dir = Path(project_path) / "docker"

    dockerfile = f"""{_cui_header()}
# =============================================================================
# Stage 1: Dependencies — install node_modules
# =============================================================================
FROM node:{node_version}-alpine AS deps

WORKDIR /build

# Install only production dependencies
COPY package.json package-lock.json* yarn.lock* ./
RUN if [ -f package-lock.json ]; then \\
        npm ci --only=production --ignore-scripts; \\
    elif [ -f yarn.lock ]; then \\
        yarn install --production --frozen-lockfile; \\
    else \\
        npm install --only=production --ignore-scripts; \\
    fi

# =============================================================================
# Stage 2: Builder — build application (for TypeScript/bundled projects)
# =============================================================================
FROM node:{node_version}-alpine AS builder

WORKDIR /build

COPY package.json package-lock.json* yarn.lock* ./
RUN if [ -f package-lock.json ]; then \\
        npm ci --ignore-scripts; \\
    elif [ -f yarn.lock ]; then \\
        yarn install --frozen-lockfile; \\
    else \\
        npm install --ignore-scripts; \\
    fi

COPY . .
RUN if [ -f tsconfig.json ]; then \\
        npx tsc --build; \\
    fi && \\
    if [ -f next.config.js ] || [ -f next.config.mjs ]; then \\
        npx next build; \\
    fi

# =============================================================================
# Stage 3: Production — minimal runtime
# =============================================================================
FROM node:{node_version}-alpine AS production

# --- CUI Classification Labels ---
LABEL classification="CUI" \\
      cui.category="CTI" \\
      cui.dissemination="NOFORN" \\
      maintainer="icdev-team" \\
      org.opencontainers.image.title="{project_name}" \\
      org.opencontainers.image.description="STIG-hardened Node.js application" \\
      org.opencontainers.image.vendor="ICDev" \\
      org.opencontainers.image.created="{datetime.utcnow().isoformat()}"

ENV NODE_ENV=production \\
    APP_PORT={app_port} \\
    LOG_LEVEL=info

# Install only what we need at runtime and harden
RUN apk add --no-cache \\
        curl \\
        tini \\
    && apk del --purge apk-tools \\
    && rm -rf /var/cache/apk/* /tmp/* /root/.npm

# Create non-root user
RUN addgroup -g 65534 -S nonroot && \\
    adduser -u 65534 -S -G nonroot -s /sbin/nologin nonroot

# Create app directory
RUN mkdir -p /app /tmp/app && \\
    chown -R nonroot:nonroot /app /tmp/app

WORKDIR /app

# Copy production dependencies from deps stage
COPY --from=deps --chown=nonroot:nonroot /build/node_modules ./node_modules

# Copy built application from builder stage
COPY --from=builder --chown=nonroot:nonroot /build/dist ./dist
COPY --from=builder --chown=nonroot:nonroot /build/package.json ./

# Remove unnecessary files that may have been copied
RUN rm -rf .git .env tests __tests__ *.test.* *.spec.* .eslintrc* .prettierrc* \\
    tsconfig* jest.config* docker-compose* Dockerfile*

# Switch to non-root user
USER nonroot:nonroot

# Expose application port
EXPOSE {app_port}

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
    CMD curl -f http://localhost:{app_port}/health || exit 1

# Use tini as PID 1 for proper signal handling
ENTRYPOINT ["/sbin/tini", "--"]

# Start the application
CMD ["node", "dist/index.js"]
"""
    p = _write(docker_dir / "Dockerfile.node", dockerfile)
    files = [str(p)]

    # .dockerignore (if not already created by Python generator)
    dockerignore_path = docker_dir / ".dockerignore"
    if not dockerignore_path.exists():
        dockerignore = """\
.git
.gitignore
.env
.env.*
node_modules
.next
dist
*.md
Dockerfile*
docker-compose*
.dockerignore
.vscode
.idea
coverage
__tests__
tests
*.test.*
*.spec.*
.nyc_output
.tmp
"""
        p2 = _write(dockerignore_path, dockerignore)
        files.append(str(p2))

    return files


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate STIG-hardened Dockerfiles")
    parser.add_argument("--project-path", required=True, help="Target project directory")
    parser.add_argument(
        "--type",
        required=True,
        choices=["python", "node", "both"],
        help="Dockerfile type: python, node, or both",
    )
    parser.add_argument("--port", type=int, default=None, help="Application port")
    parser.add_argument("--project-name", default="icdev-app", help="Project name for labels")
    args = parser.parse_args()

    config = {"project_name": args.project_name}
    if args.port:
        config["port"] = args.port

    all_files = []
    types = ["python", "node"] if args.type == "both" else [args.type]

    for t in types:
        if t == "python":
            files = generate_python(args.project_path, config)
        else:
            files = generate_node(args.project_path, config)
        all_files.extend(files)
        print(f"[dockerfile] Generated {t}: {len(files)} files")

    print(f"\n[dockerfile] Total files generated: {len(all_files)}")
    for f in all_files:
        print(f"  -> {f}")


if __name__ == "__main__":
    main()
