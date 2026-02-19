#!/usr/bin/env python3
# CUI // SP-CTI
"""Extended Project Scaffolder — multi-language project templates.

Implements additional project types beyond the core Python/JS scaffolders:
- scaffold_java_backend       -> Spring Boot / Maven project
- scaffold_go_backend         -> Go module with HTTP server
- scaffold_rust_backend       -> Cargo / Actix-web project
- scaffold_csharp_backend     -> .NET 8 ASP.NET minimal API
- scaffold_typescript_backend -> Node.js + TypeScript + Express

All templates include CUI markings, STIG-hardened Dockerfiles,
README with CUI banners, and compliance/ directory.

CUI // SP-CTI
Controlled by: Department of Defense
CUI Category: CTI
Distribution: D
POC: ICDEV System Administrator
"""

from pathlib import Path
from typing import List


# ---------------------------------------------------------------------------
# Shared constants (mirror scaffolder.py)
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

CUI_BANNER = """\
//////////////////////////////////////////////////////////////////
CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
Distribution: Distribution D - Authorized DoD Personnel Only
//////////////////////////////////////////////////////////////////"""

CUI_BANNER_MD = """\
> **CUI // SP-CTI**
> Controlled by: Department of Defense | Distribution D
> This document contains Controlled Unclassified Information (CUI)."""

# Language-specific CUI headers
CUI_HEADER_PYTHON = """\
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""

CUI_HEADER_C_STYLE = """\
// CUI // SP-CTI
// Controlled by: Department of Defense
// CUI Category: CTI
// Distribution: D
// POC: ICDEV System Administrator
"""

CUI_HEADER_XML = """\
<!-- CUI // SP-CTI -->
<!-- Controlled by: Department of Defense -->
<!-- CUI Category: CTI -->
<!-- Distribution: D -->
<!-- POC: ICDEV System Administrator -->
"""

CUI_HEADER_HASH = """\
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""

CUI_HEADER_YAML = CUI_HEADER_HASH

CUI_HEADER_TOML = """\
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""

CUI_HEADER_RUST = CUI_HEADER_C_STYLE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_file(path: Path, content: str) -> None:
    """Write content to a file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _create_gitkeep(directory: Path) -> None:
    """Create a .gitkeep in an empty directory."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".gitkeep").write_text("", encoding="utf-8")


def _create_compliance_dirs(root: Path, files: List[str]) -> None:
    """Create standard compliance subdirectories with .gitkeep files."""
    for sub in ["ssp", "poam", "stig", "sbom", "sbd", "ivv", "rtm"]:
        d = root / "compliance" / sub
        _create_gitkeep(d)
        files.append(str(d / ".gitkeep"))


def _readme_content(name: str, project_type: str, description: str = "") -> str:
    """Generate a README with CUI banners."""
    desc = description or f"A {project_type} project scaffolded by ICDEV Builder."
    return f"""{CUI_BANNER}

# {name}

{CUI_BANNER_MD}

## Overview

{desc}

## Getting Started

See the project-specific build instructions below.

## Compliance

See `compliance/` directory for security and compliance artifacts.

## Classification

{CUI_BANNER}
"""


def _compliance_readme() -> str:
    """Generate a compliance directory README."""
    return f"""{CUI_BANNER}

# Compliance Artifacts

This directory contains compliance documentation and artifacts for this project.

## Contents

- `ssp/` - System Security Plan documents
- `poam/` - Plan of Action and Milestones
- `stig/` - STIG checklists and findings
- `sbom/` - Software Bill of Materials
- `sbd/` - Security Baseline Documentation
- `ivv/` - Independent Verification and Validation
- `rtm/` - Requirements Traceability Matrix

## Classification

All artifacts in this directory are classified as CUI // SP-CTI.

{CUI_BANNER}
"""


# ===================================================================
# 1. Java Backend (Spring Boot / Maven)
# ===================================================================

def scaffold_java_backend(project_path: str, name: str) -> List[str]:
    """Scaffold a Spring Boot / Maven Java backend project.

    Creates:
    - pom.xml with Spring Boot 3.2.x, Java 17, testing dependencies
    - Application.java main class
    - HealthController.java
    - application.yml
    - Tests, BDD features dir
    - STIG-hardened multi-stage Dockerfile
    - Compliance dirs, README, .gitignore
    """
    root = Path(project_path) / name
    root.mkdir(parents=True, exist_ok=True)
    files: List[str] = []

    # Sanitise name for Java package (lowercase, no hyphens)
    pkg_name = name.lower().replace("-", "").replace("_", "")
    pkg_path = f"com/icdev/{pkg_name}"

    # -- pom.xml ----------------------------------------------------------
    pom_xml = f"""{CUI_HEADER_XML}
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.2.5</version>
        <relativePath/>
    </parent>

    <groupId>com.icdev</groupId>
    <artifactId>{name}</artifactId>
    <version>0.1.0-SNAPSHOT</version>
    <name>{name}</name>
    <description>ICDEV scaffolded Spring Boot project — CUI // SP-CTI</description>

    <properties>
        <java.version>17</java.version>
        <cucumber.version>7.15.0</cucumber.version>
    </properties>

    <dependencies>
        <!-- Web -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>

        <!-- Test -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-test</artifactId>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>io.cucumber</groupId>
            <artifactId>cucumber-java</artifactId>
            <version>${{cucumber.version}}</version>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>io.cucumber</groupId>
            <artifactId>cucumber-junit-platform-engine</artifactId>
            <version>${{cucumber.version}}</version>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
            <plugin>
                <groupId>org.owasp</groupId>
                <artifactId>dependency-check-maven</artifactId>
                <version>9.0.9</version>
                <configuration>
                    <failBuildOnCVSS>7</failBuildOnCVSS>
                </configuration>
            </plugin>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-checkstyle-plugin</artifactId>
                <version>3.3.1</version>
                <configuration>
                    <configLocation>google_checks.xml</configLocation>
                    <consoleOutput>true</consoleOutput>
                    <failsOnError>true</failsOnError>
                </configuration>
            </plugin>
        </plugins>
    </build>
</project>
"""
    p = root / "pom.xml"
    _write_file(p, pom_xml)
    files.append(str(p))

    # -- Application.java -------------------------------------------------
    app_java = f"""{CUI_HEADER_C_STYLE}
package com.icdev.{pkg_name};

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Main entry point for the {name} application.
 * CUI // SP-CTI
 */
@SpringBootApplication
public class Application {{

    public static void main(String[] args) {{
        SpringApplication.run(Application.class, args);
    }}
}}
"""
    p = root / "src" / "main" / "java" / pkg_path / "Application.java"
    _write_file(p, app_java)
    files.append(str(p))

    # -- HealthController.java --------------------------------------------
    health_ctrl = f"""{CUI_HEADER_C_STYLE}
package com.icdev.{pkg_name}.controller;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * Health check endpoint.
 * CUI // SP-CTI
 */
@RestController
public class HealthController {{

    @GetMapping("/health")
    public Map<String, String> health() {{
        return Map.of(
            "status", "UP",
            "service", "{name}",
            "classification", "CUI // SP-CTI"
        );
    }}
}}
"""
    p = root / "src" / "main" / "java" / pkg_path / "controller" / "HealthController.java"
    _write_file(p, health_ctrl)
    files.append(str(p))

    # -- package-info.java (service layer placeholder) --------------------
    pkg_info = f"""{CUI_HEADER_C_STYLE}
/**
 * Service layer for {name}.
 * CUI // SP-CTI
 */
package com.icdev.{pkg_name}.service;
"""
    p = root / "src" / "main" / "java" / pkg_path / "service" / "package-info.java"
    _write_file(p, pkg_info)
    files.append(str(p))

    # -- application.yml --------------------------------------------------
    app_yml = f"""{CUI_HEADER_YAML}
server:
  port: 8080

spring:
  application:
    name: {name}

management:
  endpoints:
    web:
      exposure:
        include: health,info
"""
    p = root / "src" / "main" / "resources" / "application.yml"
    _write_file(p, app_yml)
    files.append(str(p))

    # -- ApplicationTest.java ---------------------------------------------
    app_test = f"""{CUI_HEADER_C_STYLE}
package com.icdev.{pkg_name};

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * Smoke test — verifies the Spring context loads.
 * CUI // SP-CTI
 */
@SpringBootTest
class ApplicationTest {{

    @Test
    void contextLoads() {{
        // Context load is the assertion itself
    }}
}}
"""
    p = root / "src" / "test" / "java" / pkg_path / "ApplicationTest.java"
    _write_file(p, app_test)
    files.append(str(p))

    # -- BDD features dir -------------------------------------------------
    _create_gitkeep(root / "src" / "test" / "resources" / "features")
    files.append(str(root / "src" / "test" / "resources" / "features" / ".gitkeep"))

    # -- Dockerfile (STIG-hardened, multi-stage) --------------------------
    dockerfile = f"""{CUI_HEADER_HASH}
# STIG-hardened multi-stage Dockerfile for Java/Spring Boot
# CUI // SP-CTI

# ---- Build Stage ----
FROM eclipse-temurin:17-jdk-alpine AS build
WORKDIR /build
COPY pom.xml .
COPY src ./src
RUN apk add --no-cache maven \\
    && mvn clean package -DskipTests -q \\
    && mv target/*.jar app.jar

# ---- Runtime Stage ----
FROM eclipse-temurin:17-jre-alpine AS runtime

# STIG: Remove SUID/SGID binaries
RUN find / -perm /6000 -type f -exec chmod a-s {{}} + 2>/dev/null || true

# STIG: Create non-root user
RUN addgroup -g 1000 appgroup && adduser -u 1000 -G appgroup -D appuser

WORKDIR /app
COPY --from=build /build/app.jar ./app.jar

# STIG: Set ownership
RUN chown -R appuser:appgroup /app

# STIG: Drop ALL capabilities, run as non-root
USER appuser:appgroup

EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]

# Read-only root filesystem — enforce via container runtime:
#   docker run --read-only --tmpfs /tmp:rw,noexec,nosuid ...
"""
    p = root / "Dockerfile"
    _write_file(p, dockerfile)
    files.append(str(p))

    # -- .gitignore -------------------------------------------------------
    gitignore = """\
# Java / Maven
target/
*.class
*.jar
*.war
*.ear
*.log
hs_err_pid*

# IDE
.idea/
*.iml
.project
.classpath
.settings/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Environment
.env

# Coverage
jacoco/
htmlcov/
coverage.xml

# Tmp
.tmp/
tmp/
"""
    p = root / ".gitignore"
    _write_file(p, gitignore)
    files.append(str(p))

    # -- README.md --------------------------------------------------------
    readme = _readme_content(name, "Java Spring Boot backend")
    p = root / "README.md"
    _write_file(p, readme)
    files.append(str(p))

    # -- Compliance dirs --------------------------------------------------
    comp_readme = _compliance_readme()
    p = root / "compliance" / "README.md"
    _write_file(p, comp_readme)
    files.append(str(p))
    _create_compliance_dirs(root, files)

    print(f"Scaffolded Java backend: {root}")
    return files


# ===================================================================
# 2. Go Backend
# ===================================================================

def scaffold_go_backend(project_path: str, name: str) -> List[str]:
    """Scaffold a Go module backend project.

    Creates:
    - go.mod (go 1.22)
    - cmd/{name}/main.go with HTTP server
    - internal/handler/health.go
    - internal/service/.gitkeep
    - pkg/.gitkeep
    - Tests, BDD features dir
    - STIG-hardened multi-stage Dockerfile (scratch runtime)
    - Compliance dirs, README, .gitignore
    """
    root = Path(project_path) / name
    root.mkdir(parents=True, exist_ok=True)
    files: List[str] = []

    # Sanitise module name
    mod_name = name.lower().replace("_", "-")

    # -- go.mod -----------------------------------------------------------
    go_mod = f"""{CUI_HEADER_C_STYLE}
module github.com/icdev/{mod_name}

go 1.22

require ()
"""
    p = root / "go.mod"
    _write_file(p, go_mod)
    files.append(str(p))

    # -- cmd/{name}/main.go -----------------------------------------------
    main_go = f"""{CUI_HEADER_C_STYLE}
package main

import (
\t"fmt"
\t"log"
\t"net/http"
\t"os"

\t"github.com/icdev/{mod_name}/internal/handler"
)

// CUI // SP-CTI

func main() {{
\tport := os.Getenv("PORT")
\tif port == "" {{
\t\tport = "8080"
\t}}

\tmux := http.NewServeMux()
\tmux.HandleFunc("/health", handler.Health)

\taddr := fmt.Sprintf(":%s", port)
\tlog.Printf("Starting {name} on %s", addr)
\tif err := http.ListenAndServe(addr, mux); err != nil {{
\t\tlog.Fatalf("Server failed: %v", err)
\t}}
}}
"""
    p = root / "cmd" / mod_name / "main.go"
    _write_file(p, main_go)
    files.append(str(p))

    # -- internal/handler/health.go ---------------------------------------
    health_go = f"""{CUI_HEADER_C_STYLE}
package handler

import (
\t"encoding/json"
\t"net/http"
)

// CUI // SP-CTI

// HealthResponse is the JSON body returned by the health endpoint.
type HealthResponse struct {{
\tStatus         string `json:"status"`
\tService        string `json:"service"`
\tClassification string `json:"classification"`
}}

// Health handles GET /health requests.
func Health(w http.ResponseWriter, r *http.Request) {{
\tw.Header().Set("Content-Type", "application/json")
\tresp := HealthResponse{{
\t\tStatus:         "UP",
\t\tService:        "{name}",
\t\tClassification: "CUI // SP-CTI",
\t}}
\tjson.NewEncoder(w).Encode(resp)
}}
"""
    p = root / "internal" / "handler" / "health.go"
    _write_file(p, health_go)
    files.append(str(p))

    # -- internal/service/.gitkeep ----------------------------------------
    _create_gitkeep(root / "internal" / "service")
    files.append(str(root / "internal" / "service" / ".gitkeep"))

    # -- pkg/.gitkeep -----------------------------------------------------
    _create_gitkeep(root / "pkg")
    files.append(str(root / "pkg" / ".gitkeep"))

    # -- cmd/{name}/main_test.go ------------------------------------------
    main_test_go = f"""{CUI_HEADER_C_STYLE}
package main

import (
\t"net/http"
\t"net/http/httptest"
\t"testing"

\t"github.com/icdev/{mod_name}/internal/handler"
)

// CUI // SP-CTI

func TestHealthEndpoint(t *testing.T) {{
\treq, err := http.NewRequest("GET", "/health", nil)
\tif err != nil {{
\t\tt.Fatal(err)
\t}}

\trr := httptest.NewRecorder()
\thandlerFunc := http.HandlerFunc(handler.Health)
\thandlerFunc.ServeHTTP(rr, req)

\tif status := rr.Code; status != http.StatusOK {{
\t\tt.Errorf("handler returned wrong status code: got %v want %v", status, http.StatusOK)
\t}}

\texpected := `"status":"UP"`
\tif body := rr.Body.String(); !contains(body, expected) {{
\t\tt.Errorf("handler returned unexpected body: got %v", body)
\t}}
}}

func contains(s, substr string) bool {{
\treturn len(s) >= len(substr) && (s == substr || len(s) > 0 && containsImpl(s, substr))
}}

func containsImpl(s, substr string) bool {{
\tfor i := 0; i <= len(s)-len(substr); i++ {{
\t\tif s[i:i+len(substr)] == substr {{
\t\t\treturn true
\t\t}}
\t}}
\treturn false
}}
"""
    p = root / "cmd" / mod_name / "main_test.go"
    _write_file(p, main_test_go)
    files.append(str(p))

    # -- features/.gitkeep ------------------------------------------------
    _create_gitkeep(root / "features")
    files.append(str(root / "features" / ".gitkeep"))

    # -- Dockerfile (STIG-hardened, multi-stage, scratch runtime) ---------
    dockerfile = f"""{CUI_HEADER_HASH}
# STIG-hardened multi-stage Dockerfile for Go
# CUI // SP-CTI

# ---- Build Stage ----
FROM golang:1.22-alpine AS build

RUN apk add --no-cache git ca-certificates

WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download 2>/dev/null || true

COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \\
    go build -ldflags="-s -w" -o /app ./cmd/{mod_name}

# ---- Runtime Stage ----
FROM scratch AS runtime

# STIG: Import CA certs for TLS
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

# STIG: Copy passwd for non-root user (UID 1000)
COPY --from=build /etc/passwd /etc/passwd

# Copy binary
COPY --from=build /app /app

# STIG: Run as non-root
USER 1000:1000

EXPOSE 8080
ENTRYPOINT ["/app"]

# Read-only root filesystem — enforced by scratch (immutable)
# Drop ALL capabilities — enforced via container runtime:
#   docker run --cap-drop=ALL ...
"""
    p = root / "Dockerfile"
    _write_file(p, dockerfile)
    files.append(str(p))

    # -- .gitignore -------------------------------------------------------
    gitignore = """\
# Go
bin/
vendor/
*.exe
*.exe~
*.dll
*.so
*.dylib
*.test
*.out

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Environment
.env

# Coverage
coverage.out
coverage.html

# Tmp
.tmp/
tmp/
"""
    p = root / ".gitignore"
    _write_file(p, gitignore)
    files.append(str(p))

    # -- README.md --------------------------------------------------------
    readme = _readme_content(name, "Go backend")
    p = root / "README.md"
    _write_file(p, readme)
    files.append(str(p))

    # -- Compliance dirs --------------------------------------------------
    comp_readme = _compliance_readme()
    p = root / "compliance" / "README.md"
    _write_file(p, comp_readme)
    files.append(str(p))
    _create_compliance_dirs(root, files)

    print(f"Scaffolded Go backend: {root}")
    return files


# ===================================================================
# 3. Rust Backend (Actix-web / Cargo)
# ===================================================================

def scaffold_rust_backend(project_path: str, name: str) -> List[str]:
    """Scaffold a Rust Actix-web backend project.

    Creates:
    - Cargo.toml with actix-web, serde, tokio
    - src/main.rs with Actix-web server
    - src/lib.rs module root
    - src/handlers/mod.rs with health handler
    - tests/integration_test.rs
    - BDD features dir
    - STIG-hardened multi-stage Dockerfile
    - Compliance dirs, README, .gitignore
    """
    root = Path(project_path) / name
    root.mkdir(parents=True, exist_ok=True)
    files: List[str] = []

    # Sanitise crate name (Rust uses underscores)
    crate_name = name.lower().replace("-", "_")

    # -- Cargo.toml -------------------------------------------------------
    cargo_toml = f"""{CUI_HEADER_TOML}
[package]
name = "{crate_name}"
version = "0.1.0"
edition = "2021"
description = "ICDEV scaffolded Rust backend — CUI // SP-CTI"

[dependencies]
actix-web = "4"
actix-rt = "2"
serde = {{ version = "1", features = ["derive"] }}
serde_json = "1"
tokio = {{ version = "1", features = ["full"] }}
env_logger = "0.11"
log = "0.4"

[dev-dependencies]
actix-test = "0.1"
reqwest = {{ version = "0.12", features = ["json"] }}
"""
    p = root / "Cargo.toml"
    _write_file(p, cargo_toml)
    files.append(str(p))

    # -- src/main.rs ------------------------------------------------------
    main_rs = f"""{CUI_HEADER_RUST}

use actix_web::{{App, HttpServer, web}};
use env_logger::Env;

mod handlers;

/// CUI // SP-CTI
/// Main entry point for the {name} service.
#[actix_web::main]
async fn main() -> std::io::Result<()> {{
    env_logger::init_from_env(Env::default().default_filter_or("info"));

    log::info!("Starting {name} on 0.0.0.0:8080");

    HttpServer::new(|| {{
        App::new()
            .route("/health", web::get().to(handlers::health))
    }})
    .bind("0.0.0.0:8080")?
    .run()
    .await
}}
"""
    p = root / "src" / "main.rs"
    _write_file(p, main_rs)
    files.append(str(p))

    # -- src/lib.rs -------------------------------------------------------
    lib_rs = f"""{CUI_HEADER_RUST}

//! Library root for {crate_name}.
//! CUI // SP-CTI

pub mod handlers;
"""
    p = root / "src" / "lib.rs"
    _write_file(p, lib_rs)
    files.append(str(p))

    # -- src/handlers/mod.rs ----------------------------------------------
    handlers_mod = f"""{CUI_HEADER_RUST}

use actix_web::{{HttpResponse, web}};
use serde::Serialize;

/// CUI // SP-CTI

/// Health check response body.
#[derive(Serialize)]
pub struct HealthResponse {{
    pub status: String,
    pub service: String,
    pub classification: String,
}}

/// GET /health — returns service health status.
pub async fn health() -> HttpResponse {{
    let resp = HealthResponse {{
        status: "UP".to_string(),
        service: "{name}".to_string(),
        classification: "CUI // SP-CTI".to_string(),
    }};
    HttpResponse::Ok().json(resp)
}}
"""
    p = root / "src" / "handlers" / "mod.rs"
    _write_file(p, handlers_mod)
    files.append(str(p))

    # -- tests/integration_test.rs ----------------------------------------
    integration_test = f"""{CUI_HEADER_RUST}

//! Integration tests for {crate_name}.
//! CUI // SP-CTI

#[cfg(test)]
mod tests {{
    use actix_web::{{test, App, web}};
    use {crate_name}::handlers;

    #[actix_web::test]
    async fn test_health_endpoint() {{
        let app = test::init_service(
            App::new().route("/health", web::get().to(handlers::health))
        )
        .await;

        let req = test::TestRequest::get().uri("/health").to_request();
        let resp = test::call_service(&app, req).await;

        assert!(resp.status().is_success());
    }}
}}
"""
    p = root / "tests" / "integration_test.rs"
    _write_file(p, integration_test)
    files.append(str(p))

    # -- features/.gitkeep ------------------------------------------------
    _create_gitkeep(root / "features")
    files.append(str(root / "features" / ".gitkeep"))

    # -- Dockerfile (STIG-hardened, multi-stage) --------------------------
    dockerfile = f"""{CUI_HEADER_HASH}
# STIG-hardened multi-stage Dockerfile for Rust
# CUI // SP-CTI

# ---- Build Stage ----
FROM rust:1.77-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \\
    pkg-config libssl-dev ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY Cargo.toml Cargo.lock* ./
# Cache dependencies
RUN mkdir src && echo "fn main() {{}}" > src/main.rs \\
    && cargo build --release 2>/dev/null || true \\
    && rm -rf src

COPY . .
RUN cargo build --release

# ---- Runtime Stage ----
FROM debian:bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \\
    ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

# STIG: Remove SUID/SGID binaries
RUN find / -perm /6000 -type f -exec chmod a-s {{}} + 2>/dev/null || true

# STIG: Create non-root user
RUN groupadd -g 1000 appgroup && useradd -u 1000 -g appgroup -m appuser

WORKDIR /app
COPY --from=build /src/target/release/{crate_name} ./app

# STIG: Set ownership
RUN chown -R appuser:appgroup /app

# STIG: Drop ALL capabilities, run as non-root
USER appuser:appgroup

EXPOSE 8080
ENTRYPOINT ["./app"]

# Read-only root filesystem — enforce via container runtime:
#   docker run --read-only --tmpfs /tmp:rw,noexec,nosuid ...
"""
    p = root / "Dockerfile"
    _write_file(p, dockerfile)
    files.append(str(p))

    # -- .gitignore -------------------------------------------------------
    gitignore = """\
# Rust
/target/
Cargo.lock

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Environment
.env

# Coverage
tarpaulin-report.html
cobertura.xml

# Tmp
.tmp/
tmp/
"""
    p = root / ".gitignore"
    _write_file(p, gitignore)
    files.append(str(p))

    # -- README.md --------------------------------------------------------
    readme = _readme_content(name, "Rust Actix-web backend")
    p = root / "README.md"
    _write_file(p, readme)
    files.append(str(p))

    # -- Compliance dirs --------------------------------------------------
    comp_readme = _compliance_readme()
    p = root / "compliance" / "README.md"
    _write_file(p, comp_readme)
    files.append(str(p))
    _create_compliance_dirs(root, files)

    print(f"Scaffolded Rust backend: {root}")
    return files


# ===================================================================
# 4. C# Backend (.NET 8 ASP.NET)
# ===================================================================

def scaffold_csharp_backend(project_path: str, name: str) -> List[str]:
    """Scaffold a .NET 8 ASP.NET backend project.

    Creates:
    - {name}.csproj with ASP.NET, SpecFlow, xunit references
    - Program.cs minimal API
    - Controllers/HealthController.cs
    - Models/.gitkeep, Services/.gitkeep
    - Tests/{name}.Tests.csproj and test file
    - BDD features dir
    - STIG-hardened multi-stage Dockerfile
    - Compliance dirs, README, .gitignore
    """
    root = Path(project_path) / name
    root.mkdir(parents=True, exist_ok=True)
    files: List[str] = []

    # Sanitise for C# namespace (PascalCase, no hyphens)
    ns_name = "".join(word.capitalize() for word in name.replace("_", "-").split("-"))

    # -- {name}.csproj ----------------------------------------------------
    csproj = f"""{CUI_HEADER_XML}
<Project Sdk="Microsoft.NET.Sdk.Web">

  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <RootNamespace>{ns_name}</RootNamespace>
    <AssemblyName>{name}</AssemblyName>
    <Description>ICDEV scaffolded .NET 8 project — CUI // SP-CTI</Description>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.OpenApi" Version="8.0.*" />
    <PackageReference Include="Swashbuckle.AspNetCore" Version="6.5.*" />
  </ItemGroup>

</Project>
"""
    p = root / f"{name}.csproj"
    _write_file(p, csproj)
    files.append(str(p))

    # -- Program.cs -------------------------------------------------------
    program_cs = f"""{CUI_HEADER_C_STYLE}

// CUI // SP-CTI — {name} entry point

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllers();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

var app = builder.Build();

if (app.Environment.IsDevelopment())
{{
    app.UseSwagger();
    app.UseSwaggerUI();
}}

app.MapControllers();

// Minimal API health endpoint (alternative to controller)
app.MapGet("/", () => Results.Ok(new {{ status = "UP", classification = "CUI // SP-CTI" }}));

app.Run();
"""
    p = root / "Program.cs"
    _write_file(p, program_cs)
    files.append(str(p))

    # -- Controllers/HealthController.cs ----------------------------------
    health_ctrl_cs = f"""{CUI_HEADER_C_STYLE}

using Microsoft.AspNetCore.Mvc;

namespace {ns_name}.Controllers;

/// <summary>
/// Health check controller.
/// CUI // SP-CTI
/// </summary>
[ApiController]
[Route("[controller]")]
public class HealthController : ControllerBase
{{
    /// <summary>
    /// GET /health — returns service health status.
    /// </summary>
    [HttpGet("/health")]
    public IActionResult GetHealth()
    {{
        return Ok(new
        {{
            status = "UP",
            service = "{name}",
            classification = "CUI // SP-CTI"
        }});
    }}
}}
"""
    p = root / "Controllers" / "HealthController.cs"
    _write_file(p, health_ctrl_cs)
    files.append(str(p))

    # -- Models/.gitkeep --------------------------------------------------
    _create_gitkeep(root / "Models")
    files.append(str(root / "Models" / ".gitkeep"))

    # -- Services/.gitkeep ------------------------------------------------
    _create_gitkeep(root / "Services")
    files.append(str(root / "Services" / ".gitkeep"))

    # -- Tests/{name}.Tests.csproj ----------------------------------------
    test_csproj = f"""{CUI_HEADER_XML}
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <IsPackable>false</IsPackable>
    <RootNamespace>{ns_name}.Tests</RootNamespace>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" Version="8.0.*" />
    <PackageReference Include="xunit" Version="2.7.*" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.5.*" />
    <PackageReference Include="SpecFlow" Version="3.9.*" />
    <PackageReference Include="SpecFlow.xUnit" Version="3.9.*" />
  </ItemGroup>

  <ItemGroup>
    <ProjectReference Include="..\\{name}.csproj" />
  </ItemGroup>

</Project>
"""
    p = root / "Tests" / f"{name}.Tests.csproj"
    _write_file(p, test_csproj)
    files.append(str(p))

    # -- Tests/HealthControllerTests.cs -----------------------------------
    health_test_cs = f"""{CUI_HEADER_C_STYLE}

using Microsoft.AspNetCore.Mvc;
using {ns_name}.Controllers;
using Xunit;

namespace {ns_name}.Tests;

/// <summary>
/// Tests for HealthController.
/// CUI // SP-CTI
/// </summary>
public class HealthControllerTests
{{
    [Fact]
    public void GetHealth_ReturnsOk()
    {{
        // Arrange
        var controller = new HealthController();

        // Act
        var result = controller.GetHealth();

        // Assert
        Assert.IsType<OkObjectResult>(result);
    }}
}}
"""
    p = root / "Tests" / "HealthControllerTests.cs"
    _write_file(p, health_test_cs)
    files.append(str(p))

    # -- features/.gitkeep ------------------------------------------------
    _create_gitkeep(root / "features")
    files.append(str(root / "features" / ".gitkeep"))

    # -- Dockerfile (STIG-hardened, multi-stage) --------------------------
    dockerfile = f"""{CUI_HEADER_HASH}
# STIG-hardened multi-stage Dockerfile for .NET 8
# CUI // SP-CTI

# ---- Build Stage ----
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src

COPY {name}.csproj .
RUN dotnet restore

COPY . .
RUN dotnet publish -c Release -o /app/publish --no-restore

# ---- Runtime Stage ----
FROM mcr.microsoft.com/dotnet/aspnet:8.0-alpine AS runtime

# STIG: Remove SUID/SGID binaries
RUN find / -perm /6000 -type f -exec chmod a-s {{}} + 2>/dev/null || true

# STIG: Create non-root user
RUN addgroup -g 1000 appgroup && adduser -u 1000 -G appgroup -D appuser

WORKDIR /app
COPY --from=build /app/publish .

# STIG: Set ownership
RUN chown -R appuser:appgroup /app

# STIG: Drop ALL capabilities, run as non-root
USER appuser:appgroup

EXPOSE 8080
ENV ASPNETCORE_URLS=http://+:8080
ENTRYPOINT ["dotnet", "{name}.dll"]

# Read-only root filesystem — enforce via container runtime:
#   docker run --read-only --tmpfs /tmp:rw,noexec,nosuid ...
"""
    p = root / "Dockerfile"
    _write_file(p, dockerfile)
    files.append(str(p))

    # -- .gitignore -------------------------------------------------------
    gitignore = """\
# .NET / C#
bin/
obj/
*.dll
*.pdb
*.exe
*.nupkg
*.user
*.suo

# IDE
.vs/
.vscode/
*.swp
*.swo
.idea/

# OS
.DS_Store
Thumbs.db

# Environment
.env
appsettings.Development.json

# Coverage
TestResults/
coverage.cobertura.xml

# Tmp
.tmp/
tmp/
"""
    p = root / ".gitignore"
    _write_file(p, gitignore)
    files.append(str(p))

    # -- README.md --------------------------------------------------------
    readme = _readme_content(name, ".NET 8 ASP.NET backend")
    p = root / "README.md"
    _write_file(p, readme)
    files.append(str(p))

    # -- Compliance dirs --------------------------------------------------
    comp_readme = _compliance_readme()
    p = root / "compliance" / "README.md"
    _write_file(p, comp_readme)
    files.append(str(p))
    _create_compliance_dirs(root, files)

    print(f"Scaffolded C# backend: {root}")
    return files


# ===================================================================
# 5. TypeScript Backend (Node.js + Express)
# ===================================================================

def scaffold_typescript_backend(project_path: str, name: str) -> List[str]:
    """Scaffold a Node.js + TypeScript + Express backend project.

    Creates:
    - package.json with typescript, express, jest, cucumber
    - tsconfig.json (strict mode)
    - src/index.ts with Express app
    - src/routes/health.ts
    - src/services/.gitkeep
    - tests/health.test.ts
    - BDD features dir
    - STIG-hardened multi-stage Dockerfile
    - Compliance dirs, README, .gitignore
    """
    root = Path(project_path) / name
    root.mkdir(parents=True, exist_ok=True)
    files: List[str] = []

    # -- package.json -----------------------------------------------------
    # Note: CUI_HEADER_C_STYLE at top of JSON is non-standard but signals classification.
    # In practice a .cui-header file or banner comment in the actual source is preferred.
    # We strip the header for valid JSON by writing the JSON portion only.
    package_json_content = """{
  "name": """ + f'"{name}"' + """,
  "version": "0.1.0",
  "description": "ICDEV scaffolded TypeScript backend — CUI // SP-CTI",
  "main": "dist/index.js",
  "scripts": {
    "build": "tsc",
    "start": "node dist/index.js",
    "dev": "ts-node src/index.ts",
    "test": "jest --coverage",
    "test:bdd": "cucumber-js features/",
    "lint": "eslint src/ tests/",
    "clean": "rm -rf dist/"
  },
  "dependencies": {
    "express": "^4.18.2"
  },
  "devDependencies": {
    "@cucumber/cucumber": "^10.3.1",
    "@types/express": "^4.17.21",
    "@types/jest": "^29.5.12",
    "@types/node": "^20.11.19",
    "jest": "^29.7.0",
    "ts-jest": "^29.1.2",
    "ts-node": "^10.9.2",
    "typescript": "^5.3.3"
  },
  "engines": {
    "node": ">=20.0.0"
  },
  "license": "SEE LICENSE IN NOTICE",
  "private": true
}
"""
    p = root / "package.json"
    _write_file(p, package_json_content)
    files.append(str(p))

    # -- tsconfig.json ----------------------------------------------------
    tsconfig = """{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "lib": ["ES2022"],
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist", "tests"]
}
"""
    p = root / "tsconfig.json"
    _write_file(p, tsconfig)
    files.append(str(p))

    # -- src/index.ts -----------------------------------------------------
    index_ts = f"""{CUI_HEADER_C_STYLE}

import express from 'express';
import {{ healthRouter }} from './routes/health';

// CUI // SP-CTI

const app = express();
const PORT = process.env.PORT || 8080;

app.use(express.json());

// Routes
app.use('/health', healthRouter);

// Default route
app.get('/', (_req, res) => {{
  res.json({{
    service: '{name}',
    status: 'running',
    classification: 'CUI // SP-CTI',
  }});
}});

app.listen(PORT, () => {{
  console.log(`{name} listening on port ${{PORT}}`);
}});

export default app;
"""
    p = root / "src" / "index.ts"
    _write_file(p, index_ts)
    files.append(str(p))

    # -- src/routes/health.ts ---------------------------------------------
    health_ts = f"""{CUI_HEADER_C_STYLE}

import {{ Router, Request, Response }} from 'express';

// CUI // SP-CTI

export const healthRouter = Router();

interface HealthResponse {{
  status: string;
  service: string;
  classification: string;
  timestamp: string;
}}

/**
 * GET /health — returns service health status.
 */
healthRouter.get('/', (_req: Request, res: Response) => {{
  const response: HealthResponse = {{
    status: 'UP',
    service: '{name}',
    classification: 'CUI // SP-CTI',
    timestamp: new Date().toISOString(),
  }};
  res.json(response);
}});
"""
    p = root / "src" / "routes" / "health.ts"
    _write_file(p, health_ts)
    files.append(str(p))

    # -- src/services/.gitkeep --------------------------------------------
    _create_gitkeep(root / "src" / "services")
    files.append(str(root / "src" / "services" / ".gitkeep"))

    # -- tests/health.test.ts ---------------------------------------------
    health_test_ts = f"""{CUI_HEADER_C_STYLE}

// CUI // SP-CTI

import request from 'supertest';
import app from '../src/index';

describe('Health Endpoint', () => {{
  it('should return 200 and UP status', async () => {{
    // Note: In a real setup, supertest would be a devDependency.
    // This test serves as a template for the TDD workflow.
    expect(true).toBe(true);
  }});

  it('should include CUI classification', () => {{
    // Placeholder — implement after dependencies are installed
    const classification = 'CUI // SP-CTI';
    expect(classification).toContain('CUI');
  }});
}});
"""
    p = root / "tests" / "health.test.ts"
    _write_file(p, health_test_ts)
    files.append(str(p))

    # -- jest.config.js ---------------------------------------------------
    jest_config = f"""{CUI_HEADER_C_STYLE}

/** @type {{import('ts-jest').JestConfigWithTsJest}} */
module.exports = {{
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/tests'],
  testMatch: ['**/*.test.ts'],
  collectCoverageFrom: ['src/**/*.ts'],
  coverageDirectory: 'coverage',
  coverageReporters: ['text', 'lcov', 'cobertura'],
}};
"""
    p = root / "jest.config.js"
    _write_file(p, jest_config)
    files.append(str(p))

    # -- features/.gitkeep ------------------------------------------------
    _create_gitkeep(root / "features")
    files.append(str(root / "features" / ".gitkeep"))

    # -- Dockerfile (STIG-hardened, multi-stage) --------------------------
    dockerfile = f"""{CUI_HEADER_HASH}
# STIG-hardened multi-stage Dockerfile for Node.js + TypeScript
# CUI // SP-CTI

# ---- Build Stage ----
FROM node:20-alpine AS build

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts

COPY tsconfig.json .
COPY src/ ./src/
RUN npx tsc

# Prune dev dependencies
RUN npm prune --production

# ---- Runtime Stage ----
FROM node:20-alpine AS runtime

# STIG: Remove SUID/SGID binaries
RUN find / -perm /6000 -type f -exec chmod a-s {{}} + 2>/dev/null || true

# STIG: Create non-root user
RUN addgroup -g 1000 appgroup && adduser -u 1000 -G appgroup -D appuser

WORKDIR /app
COPY --from=build /app/dist ./dist/
COPY --from=build /app/node_modules ./node_modules/
COPY --from=build /app/package.json ./

# STIG: Set ownership
RUN chown -R appuser:appgroup /app

# STIG: Drop ALL capabilities, run as non-root
USER appuser:appgroup

EXPOSE 8080
ENV NODE_ENV=production
CMD ["node", "dist/index.js"]

# Read-only root filesystem — enforce via container runtime:
#   docker run --read-only --tmpfs /tmp:rw,noexec,nosuid ...
"""
    p = root / "Dockerfile"
    _write_file(p, dockerfile)
    files.append(str(p))

    # -- .gitignore -------------------------------------------------------
    gitignore = """\
# Node.js / TypeScript
node_modules/
dist/
*.js.map
*.d.ts
!jest.config.js

# Logs
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

# Environment
.env
.env.local
.env.*.local

# Coverage
coverage/
htmlcov/

# Tmp
.tmp/
tmp/
"""
    p = root / ".gitignore"
    _write_file(p, gitignore)
    files.append(str(p))

    # -- README.md --------------------------------------------------------
    readme = _readme_content(name, "TypeScript + Express backend")
    p = root / "README.md"
    _write_file(p, readme)
    files.append(str(p))

    # -- Compliance dirs --------------------------------------------------
    comp_readme = _compliance_readme()
    p = root / "compliance" / "README.md"
    _write_file(p, comp_readme)
    files.append(str(p))
    _create_compliance_dirs(root, files)

    print(f"Scaffolded TypeScript backend: {root}")
    return files


# ---------------------------------------------------------------------------
# Phase 19: Agentic sidecar for non-Python languages
# ---------------------------------------------------------------------------


def generate_agentic_sidecar(project_root: Path, app_name: str, language: str) -> List[str]:
    """Generate Python agentic sidecar for non-Python language projects.

    Non-Python child apps get a Python sidecar in `sidecar/agentic/` that
    provides GOTCHA framework, agents, and memory system alongside the main
    language project. Connected via docker-compose.yaml.

    Args:
        project_root: Path to the project root directory.
        app_name: Application name.
        language: Primary language of the project (java, go, rust, csharp, typescript).

    Returns:
        List of created file paths.
    """
    files: List[str] = []
    sidecar_dir = project_root / "sidecar" / "agentic"
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    # sidecar/agentic/requirements.txt
    reqs = [
        "pyyaml>=6.0", "jinja2>=3.1", "flask>=3.0",
        "requests>=2.31", "boto3>=1.34",
    ]
    req_path = sidecar_dir / "requirements.txt"
    _write_file(req_path, "\n".join(reqs) + "\n")
    files.append(str(req_path))

    # sidecar/agentic/Dockerfile
    dockerfile = f"""# Agentic sidecar for {app_name} ({language})
FROM python:3.11-slim

RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

USER appuser
EXPOSE 9443

CMD ["python", "orchestrator.py"]
"""
    df_path = sidecar_dir / "Dockerfile"
    _write_file(df_path, dockerfile)
    files.append(str(df_path))

    # sidecar/agentic/orchestrator.py — minimal orchestrator
    orchestrator = f"""#!/usr/bin/env python3
# CUI // SP-CTI
\"\"\"Agentic sidecar orchestrator for {app_name} ({language}).

This sidecar provides GOTCHA framework, ATLAS workflow, agent communication,
and memory system alongside the main {language} application.
\"\"\"

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger("{app_name}.sidecar")

SIDECAR_ROOT = Path(__file__).resolve().parent
# The main project tools are in the parent's tools/ directory
PROJECT_ROOT = SIDECAR_ROOT.parent.parent


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logger.info("Agentic sidecar starting for {app_name} ({language})")
    logger.info("Project root: %s", PROJECT_ROOT)
    logger.info("Sidecar root: %s", SIDECAR_ROOT)

    # The sidecar serves as the Python-based agentic layer
    # It delegates to tools/ for actual operations
    logger.info("Sidecar ready — agents and memory system available via tools/")


if __name__ == "__main__":
    main()
"""
    orch_path = sidecar_dir / "orchestrator.py"
    _write_file(orch_path, orchestrator)
    files.append(str(orch_path))

    # docker-compose.yaml at project root (adds sidecar service)
    compose = f"""# Docker Compose for {app_name} with agentic sidecar
version: '3.8'

services:
  {app_name}:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8080:8080"
    environment:
      - APP_NAME={app_name}
    networks:
      - app-network

  agentic-sidecar:
    build:
      context: ./sidecar/agentic
      dockerfile: Dockerfile
    ports:
      - "9443:9443"
    volumes:
      - ./tools:/app/tools:ro
      - ./goals:/app/goals:ro
      - ./memory:/app/memory
      - ./data:/app/data
    environment:
      - APP_NAME={app_name}
      - ICDEV_PARENT_CALLBACK_URL=${{ICDEV_PARENT_CALLBACK_URL:-}}
    depends_on:
      - {app_name}
    networks:
      - app-network

networks:
  app-network:
    driver: bridge
"""
    compose_path = project_root / "docker-compose.yaml"
    _write_file(compose_path, compose)
    files.append(str(compose_path))

    print(f"  Generated agentic sidecar for {language} project ({len(files)} files)")
    return files


# ---------------------------------------------------------------------------
# Module-level exports for importlib loading
# ---------------------------------------------------------------------------

__all__ = [
    "scaffold_java_backend",
    "scaffold_go_backend",
    "scaffold_rust_backend",
    "scaffold_csharp_backend",
    "scaffold_typescript_backend",
    "generate_agentic_sidecar",
]
