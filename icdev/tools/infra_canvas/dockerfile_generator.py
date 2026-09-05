# CUI // SP-CTI
"""Infrastructure Canvas — Dockerfile & Docker Compose Generator.

Generates hardened, multi-stage Dockerfiles and a local-dev docker-compose.yml
from an IDC graph. All generated images follow DoD container hardening baselines:

  - Non-root user (UID 1001 / GID 1001)
  - Minimal base images (distroless or slim variants)
  - Multi-stage builds: builder + runtime separation
  - No secrets in image layers
  - Health check instruction in every Dockerfile
  - The floci AWS emulator automatically wired into docker-compose output

Supported runtimes (auto-detected from node properties or type):
  Python 3.12-slim    | Java 21 (Eclipse Temurin)  | Go 1.23 (distroless)
  Rust 1.78 (distroless) | .NET 8 (ASP.NET runtime) | Node 22-slim (TypeScript)

Public API
----------
generate_dockerfiles(graph) -> Dict[str, str]
    Returns {service_name: dockerfile_content} for every container-type node.

generate_docker_compose(graph, project_name="icdev-local") -> str
    Returns a docker-compose.yml string for local dev with floci wired in.

generate_all(graph, project_name="icdev-local") -> Dict[str, Any]
    Combined: {"dockerfiles": {...}, "compose": "..."}

WHAT LEAVES THIS REPO
---------------------
The compose file this module returns is a CUSTOMER artifact — it is written
into somebody else's project and rebuilt on their infrastructure, possibly
disconnected. So it carries the same emulator choices ICDEV made for itself
deliberately (flx-gen-01): the tag is PINNED and never ``:latest``, state is
persistent, the region default is the GovCloud partition, and the Docker socket
mount ships with the comment that names it as a security decision the customer
has to make rather than inherit silently.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.cloud import emulator

#: Compose service name for the emulator. Also the hostname app containers on
#: the shared bridge network reach it by.
EMULATOR_SERVICE_NAME = "floci"

#: How a CONTAINER on that network reaches the emulator. Deliberately NOT
#: ``emulator.endpoint()`` — that answers for the HOST (``localhost`` by
#: default), and a container told to talk to localhost talks to itself.
DEFAULT_COMPOSE_ENDPOINT = f"http://{EMULATOR_SERVICE_NAME}:{emulator.DEFAULT_PORT}"

# ---------------------------------------------------------------------------
# Runtime detection
# ---------------------------------------------------------------------------

_RUNTIME_ALIASES: Dict[str, str] = {
    # Python
    "python": "python", "py": "python", "flask": "python", "fastapi": "python",
    "django": "python", "lambda-python": "python",
    # Java
    "java": "java", "spring": "java", "quarkus": "java", "micronaut": "java",
    "kotlin": "java",
    # Go
    "go": "go", "golang": "go",
    # Rust
    "rust": "rust",
    # .NET / C#
    "dotnet": "dotnet", "csharp": "dotnet", "c#": "dotnet", "aspnet": "dotnet",
    "net": "dotnet",
    # TypeScript / JavaScript / Node
    "typescript": "node", "ts": "node", "javascript": "node", "js": "node",
    "node": "node", "nodejs": "node", "nextjs": "node", "nestjs": "node",
    "express": "node", "react": "node",
}

# Node types that represent deployable services/containers in IDC
_CONTAINER_TYPES = {
    "service", "container", "application", "app", "microservice",
    "api", "worker", "lambda", "function", "backend", "frontend",
    "aws-lambda", "aws-ecs-task", "azure-container-app", "gcp-cloud-run",
}


def _detect_runtime(node: Dict[str, Any]) -> str:
    """Detect runtime from node properties, type, or label."""
    # Explicit property wins
    for key in ("language", "runtime", "tech_stack", "framework"):
        val = (node.get("properties") or {}).get(key, "") or node.get(key, "")
        if val:
            normalized = val.lower().strip()
            if normalized in _RUNTIME_ALIASES:
                return _RUNTIME_ALIASES[normalized]

    # Infer from node type
    ntype = node.get("type", "").lower()
    if "python" in ntype or "lambda" in ntype:
        return "python"
    if "java" in ntype or "spring" in ntype:
        return "java"
    if "go" in ntype or "golang" in ntype:
        return "go"
    if "rust" in ntype:
        return "rust"
    if "dotnet" in ntype or "csharp" in ntype:
        return "dotnet"
    if "node" in ntype or "typescript" in ntype or "react" in ntype:
        return "node"

    # Infer from label
    label = (node.get("label") or node.get("name") or "").lower()
    for token, runtime in _RUNTIME_ALIASES.items():
        if token in label:
            return runtime

    return "generic"


def _is_deployable(node: Dict[str, Any]) -> bool:
    """Return True if this node should get a Dockerfile."""
    ntype = (node.get("type") or "").lower()
    return any(ct in ntype for ct in _CONTAINER_TYPES) or (
        (node.get("properties") or {}).get("language") is not None
    )


def _service_name(node: Dict[str, Any], idx: int) -> str:
    raw = node.get("label") or node.get("name") or f"service-{idx + 1}"
    return raw.lower().replace(" ", "-").replace("_", "-")[:40]


# ---------------------------------------------------------------------------
# Dockerfile templates (multi-stage, hardened)
# ---------------------------------------------------------------------------

def _dockerfile_python(service: str, port: int = 8000) -> str:
    return f"""\
# Dockerfile — {service} (Python)
# Multi-stage: builder installs deps; runtime is slim with no build tools.
# Non-root UID 1001. Health check on HTTP /health.
# Generated by ICDEV™ Infrastructure Canvas

### ── Build stage ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder
WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    gcc libpq-dev \\
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \\
    pip install --prefix=/install --no-cache-dir -r requirements.txt

### ── Runtime stage ───────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="{service}"

# Create non-root user
RUN groupadd -g 1001 appgroup && useradd -u 1001 -g appgroup -s /sbin/nologin -M appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=appuser:appgroup . .

# Drop privileges
USER 1001

EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:{port}/health')" || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{port}"]
"""


def _dockerfile_java(service: str, port: int = 8080) -> str:
    return f"""\
# Dockerfile — {service} (Java 21 / Spring Boot)
# Multi-stage: Maven build then Eclipse Temurin JRE runtime.
# Non-root UID 1001. Health check via /actuator/health.
# Generated by ICDEV™ Infrastructure Canvas

### ── Build stage ─────────────────────────────────────────────────────────
FROM eclipse-temurin:21-jdk-jammy AS builder
WORKDIR /app

COPY pom.xml .
COPY .mvn .mvn
COPY mvnw .
RUN ./mvnw dependency:go-offline -q

COPY src src
RUN ./mvnw package -DskipTests -q

### ── Runtime stage ───────────────────────────────────────────────────────
FROM eclipse-temurin:21-jre-jammy AS runtime
LABEL org.opencontainers.image.title="{service}"

RUN groupadd -g 1001 appgroup && useradd -u 1001 -g appgroup -s /sbin/nologin -M appuser

WORKDIR /app
COPY --from=builder --chown=appuser:appgroup /app/target/*.jar app.jar

USER 1001
EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD curl -sf http://localhost:{port}/actuator/health || exit 1

ENTRYPOINT ["java", "-XX:+UseContainerSupport", "-XX:MaxRAMPercentage=75.0", "-jar", "app.jar"]
"""


def _dockerfile_go(service: str, port: int = 8080) -> str:
    return f"""\
# Dockerfile — {service} (Go 1.23)
# Multi-stage: Go builder → Google distroless static runtime.
# Non-root (distroless nonroot). CGO disabled for static binary.
# Generated by ICDEV™ Infrastructure Canvas

### ── Build stage ─────────────────────────────────────────────────────────
FROM golang:1.23-bookworm AS builder
WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \\
    go build -ldflags="-w -s" -o /server ./cmd/server

### ── Runtime stage ───────────────────────────────────────────────────────
FROM gcr.io/distroless/static-debian12:nonroot AS runtime
LABEL org.opencontainers.image.title="{service}"

COPY --from=builder /server /server

EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD ["/server", "-health"]

USER nonroot:nonroot
ENTRYPOINT ["/server"]
"""


def _dockerfile_rust(service: str, port: int = 8080) -> str:
    return f"""\
# Dockerfile — {service} (Rust)
# Multi-stage: cargo build → distroless static runtime.
# Statically linked binary, zero runtime dependencies.
# Generated by ICDEV™ Infrastructure Canvas

### ── Build stage ─────────────────────────────────────────────────────────
FROM rust:1.78-slim AS builder
WORKDIR /app

# Cache dependencies
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo "fn main() {{}}" > src/main.rs
RUN cargo build --release --locked
RUN rm src/main.rs

# Build actual binary
COPY src src
RUN touch src/main.rs && cargo build --release --locked

### ── Runtime stage ───────────────────────────────────────────────────────
FROM gcr.io/distroless/static-debian12:nonroot AS runtime
LABEL org.opencontainers.image.title="{service}"

COPY --from=builder /app/target/release/{service.replace("-","_")} /server

EXPOSE {port}
USER nonroot:nonroot

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD ["/server", "--health"]

ENTRYPOINT ["/server"]
"""


def _dockerfile_dotnet(service: str, port: int = 8080) -> str:
    return f"""\
# Dockerfile — {service} (.NET 8 / ASP.NET Core)
# Multi-stage: SDK build → ASP.NET runtime.
# Non-root UID 1001.
# Generated by ICDEV™ Infrastructure Canvas

### ── Build stage ─────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim AS builder
WORKDIR /src

COPY *.csproj .
RUN dotnet restore --locked-mode

COPY . .
RUN dotnet publish -c Release -o /publish --no-restore

### ── Runtime stage ───────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/aspnet:8.0-bookworm-slim AS runtime
LABEL org.opencontainers.image.title="{service}"

RUN groupadd -g 1001 appgroup && useradd -u 1001 -g appgroup -s /sbin/nologin -M appuser

WORKDIR /app
COPY --from=builder --chown=appuser:appgroup /publish .

ENV ASPNETCORE_URLS=http://+:{port}
ENV DOTNET_RUNNING_IN_CONTAINER=true

USER 1001
EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD curl -sf http://localhost:{port}/health || exit 1

ENTRYPOINT ["dotnet", "{service}.dll"]
"""


def _dockerfile_node(service: str, port: int = 3000) -> str:
    return f"""\
# Dockerfile — {service} (Node 22 / TypeScript)
# Multi-stage: npm ci + tsc build → slim Node runtime.
# Non-root UID 1001.
# Generated by ICDEV™ Infrastructure Canvas

### ── Build stage ─────────────────────────────────────────────────────────
FROM node:22-bookworm-slim AS builder
WORKDIR /app

COPY package*.json ./
RUN npm ci --ignore-scripts

COPY tsconfig.json* ./
COPY src src
RUN npm run build

# Prune dev dependencies
RUN npm prune --production

### ── Runtime stage ───────────────────────────────────────────────────────
FROM node:22-bookworm-slim AS runtime
LABEL org.opencontainers.image.title="{service}"

RUN groupadd -g 1001 appgroup && useradd -u 1001 -g appgroup -s /sbin/nologin -M appuser

WORKDIR /app
COPY --from=builder --chown=appuser:appgroup /app/node_modules ./node_modules
COPY --from=builder --chown=appuser:appgroup /app/dist ./dist
COPY --from=builder --chown=appuser:appgroup /app/package.json .

USER 1001
EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \\
    CMD node -e "require('http').get('http://localhost:{port}/health', r => process.exit(r.statusCode===200?0:1))" || exit 1

CMD ["node", "dist/index.js"]
"""


def _dockerfile_generic(service: str, port: int = 8080) -> str:
    return f"""\
# Dockerfile — {service} (generic / undetected runtime)
# Replace the base image and CMD to match your actual runtime.
# Non-root UID 1001. Add a health check matching your /health endpoint.
# Generated by ICDEV™ Infrastructure Canvas

FROM debian:12-slim AS runtime
LABEL org.opencontainers.image.title="{service}"

RUN groupadd -g 1001 appgroup && useradd -u 1001 -g appgroup -s /sbin/nologin -M appuser

WORKDIR /app
COPY --chown=appuser:appgroup . .

USER 1001
EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \\
    CMD curl -sf http://localhost:{port}/health || exit 1

# TODO: replace with your actual entrypoint
CMD ["/app/server"]
"""


_GENERATORS = {
    "python": _dockerfile_python,
    "java": _dockerfile_java,
    "go": _dockerfile_go,
    "rust": _dockerfile_rust,
    "dotnet": _dockerfile_dotnet,
    "node": _dockerfile_node,
    "generic": _dockerfile_generic,
}

_DEFAULT_PORTS = {
    "python": 8000,
    "java": 8080,
    "go": 8080,
    "rust": 8080,
    "dotnet": 8080,
    "node": 3000,
    "generic": 8080,
}

# ---------------------------------------------------------------------------
# Public generators
# ---------------------------------------------------------------------------

def generate_dockerfiles(graph: Dict[str, Any]) -> Dict[str, str]:
    """Generate a Dockerfile for each deployable service node in the IDC graph.

    Returns {service_name: dockerfile_content}.
    Service names are derived from node labels, lowercased and hyphenated.
    Duplicate service names get a suffix hash.
    """
    nodes: List[Dict[str, Any]] = graph.get("nodes", [])
    results: Dict[str, str] = {}
    seen_names: Dict[str, int] = {}

    for idx, node in enumerate(nodes):
        if not _is_deployable(node):
            continue

        runtime = _detect_runtime(node)
        raw_name = _service_name(node, idx)

        # Deduplicate service names
        if raw_name in seen_names:
            seen_names[raw_name] += 1
            name = f"{raw_name}-{seen_names[raw_name]}"
        else:
            seen_names[raw_name] = 0
            name = raw_name

        props = node.get("properties") or {}
        port = int(props.get("port") or props.get("container_port") or _DEFAULT_PORTS.get(runtime, 8080))

        gen_fn = _GENERATORS.get(runtime, _dockerfile_generic)
        results[name] = gen_fn(name, port)

    return results


def generate_docker_compose(
    graph: Dict[str, Any],
    project_name: str = "icdev-local",
    emulator_endpoint: str = DEFAULT_COMPOSE_ENDPOINT,
    emulator_region: str = "",
) -> str:
    """Generate a docker-compose.yml for local development.

    Includes:
      - A service entry for each deployable node (build: . placeholder)
      - The floci AWS emulator wired in automatically, on a PINNED tag
      - All services on a shared bridge network
      - AWS endpoint env vars pre-configured for the emulator

    ``emulator_endpoint`` is how a CONTAINER on this compose network reaches the
    emulator, so it defaults to the service name and NOT to the seam's
    ``FLOCI_ENDPOINT`` (which names the emulator from the HOST, ``localhost`` by
    default — writing that into an app container points it at itself).
    ``emulator_region`` defaults to the seam's region, ICDEV's GovCloud
    partition.
    """
    emulator_region = emulator_region or emulator.region()
    nodes: List[Dict[str, Any]] = graph.get("nodes", [])
    services: List[str] = []
    seen_names: Dict[str, int] = {}

    for idx, node in enumerate(nodes):
        if not _is_deployable(node):
            continue

        runtime = _detect_runtime(node)
        raw_name = _service_name(node, idx)
        if raw_name in seen_names:
            seen_names[raw_name] += 1
            name = f"{raw_name}-{seen_names[raw_name]}"
        else:
            seen_names[raw_name] = 0
            name = raw_name

        props = node.get("properties") or {}
        port = int(props.get("port") or props.get("container_port") or _DEFAULT_PORTS.get(runtime, 8080))

        svc = f"""\
  {name}:
    build:
      context: ./{name}
      dockerfile: Dockerfile
    image: {project_name}/{name}:dev
    container_name: {project_name}-{name}
    ports:
      - "{port}:{port}"
    environment:
      - AWS_ENDPOINT_URL={emulator_endpoint}
      - AWS_DEFAULT_REGION={emulator_region}
      - AWS_ACCESS_KEY_ID=test
      - AWS_SECRET_ACCESS_KEY=test
    networks:
      - {project_name}-net
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:{port}/health"]
      interval: 30s
      timeout: 5s
      retries: 3"""
        services.append(svc)

    services_block = "\n\n".join(services) if services else "  # No deployable services detected in design"

    port = emulator.DEFAULT_PORT
    return f"""\
# docker-compose.yml — {project_name} local development
# Generated by ICDEV™ Infrastructure Canvas
# floci provides AWS service emulation locally (MIT, port {port}, a documented
# LocalStack drop-in that keeps the /_localstack/health path).
# Start: docker compose up -d
# AWS CLI against the emulator: aws --endpoint-url=http://localhost:{port} s3 ls
#
# NEVER source a performance, cost or capacity claim from emulator timings. An
# emulator reproduces the AWS *API contract*, not its performance
# characteristics.

version: "3.9"

services:

  # ── floci — AWS emulation ──────────────────────────────────────────────
  #
  # SUPPLY CHAIN: the tag is PINNED and must never become `:latest`. A moving
  # tag makes "the image we tested" unanswerable and a disconnected rebuild
  # unreproducible. Pin by digest (@sha256:...) and record it in your SBOM
  # before any real deployment.
  {EMULATOR_SERVICE_NAME}:
    image: {emulator.DEFAULT_IMAGE}
    container_name: {project_name}-{EMULATOR_SERVICE_NAME}
    ports:
      # Loopback-only: an emulator holding the host Docker socket must not be
      # reachable off-host. Services on this network still reach it by name.
      - "127.0.0.1:{port}:{port}"
    environment:
      # State survives a restart. The bind mount below holds emulator state,
      # not source — add it to your .gitignore before the first run.
      FLOCI_STORAGE_MODE: persistent
      FLOCI_DEFAULT_REGION: {emulator_region}
      FLOCI_DEFAULT_ACCOUNT_ID: ${{FLOCI_ACCOUNT_ID:-000000000000}}
      AWS_DEFAULT_REGION: {emulator_region}
    volumes:
      - ./data/{EMULATOR_SERVICE_NAME}:/var/lib/{EMULATOR_SERVICE_NAME}
      # ── THE DOCKER SOCKET IS A SECURITY DECISION, AND IT IS YOURS ───────
      # A container holding the host Docker socket is ROOT-EQUIVALENT ON THE
      # HOST. It is mounted because container-backed services (Lambda, RDS,
      # ElastiCache, OpenSearch, MSK, ECS/EC2/EKS) cannot work without it.
      # If your design uses none of those, DELETE the next line — everything
      # else (S3, DynamoDB, SQS, SNS, ECR, IAM, SSM, STS, KMS, ...) is served
      # in-process and needs no socket. Keep it only with that trade recorded.
      # The default spelling has a LEADING DOUBLE SLASH so it serves both
      # hosts: Docker Desktop exposes the engine to Linux containers there and
      # the double slash survives MSYS/Git Bash path conversion, while on Linux
      # //x and /x name the same file.
      - ${{FLOCI_DOCKER_SOCKET_MOUNT:-//var/run/docker.sock}}:/var/run/docker.sock
    networks:
      - {project_name}-net
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:{port}{emulator.HEALTH_PATH}"]
      interval: 30s
      timeout: 10s
      # A JVM emulator bringing up many service backends needs a long runway.
      start_period: 60s
      retries: 5
    restart: unless-stopped

  # ── Application services ───────────────────────────────────────────────
{services_block}

networks:
  {project_name}-net:
    driver: bridge
"""


def generate_all(
    graph: Dict[str, Any],
    project_name: str = "icdev-local",
) -> Dict[str, Any]:
    """Generate both Dockerfiles and docker-compose in one call.

    Returns::

        {
            "dockerfiles": {"service-name": "# Dockerfile\\n...", ...},
            "compose": "version: '3.9'\\n...",
            "service_count": 3,
            "runtimes_detected": {"python": 2, "go": 1}
        }
    """
    dockerfiles = generate_dockerfiles(graph)
    # The REGION is this deployment's (the seam's, GovCloud by default) and
    # travels into the generated file. The ENDPOINT deliberately does not:
    # emulator.endpoint() answers for the HOST — `http://localhost:4566` unless
    # an operator set FLOCI_ENDPOINT — and writing a loopback URL into an app
    # container's AWS_ENDPOINT_URL points that container at ITSELF. Inside the
    # generated network the emulator is reached by service name.
    compose = generate_docker_compose(
        graph,
        project_name=project_name,
        emulator_region=emulator.region(),
    )

    # Tally runtimes
    nodes: List[Dict[str, Any]] = graph.get("nodes", [])
    runtimes: Dict[str, int] = {}
    for idx, node in enumerate(nodes):
        if _is_deployable(node):
            r = _detect_runtime(node)
            runtimes[r] = runtimes.get(r, 0) + 1

    return {
        "dockerfiles": dockerfiles,
        "compose": compose,
        "service_count": len(dockerfiles),
        "runtimes_detected": runtimes,
    }
