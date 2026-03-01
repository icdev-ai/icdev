# Phase 42 — Framework Planning

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 42 |
| Title | Framework Planning — Language-Specific Build Commands |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 11 (CI/CD Integration), Phase 3 (TDD Workflow) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV supports 6 first-class programming languages (Python, Java, Go, Rust, C#, TypeScript), each with distinct project structures, testing frameworks, SAST tools, linters, formatters, BDD engines, dependency management systems, and container hardening requirements. When a developer initiates a new project, they must manually determine the correct technology stack, configure CI/CD pipeline stages, set up STIG-hardened Dockerfiles, establish CUI marking injection points, and map NIST 800-53 controls — all of which vary significantly per language.

Prior to Phase 42, the `/icdev-init` and `/icdev-build` commands provided language-agnostic scaffolding that required manual customization for each language's ecosystem. This led to inconsistent project setups, missed security scanning steps (e.g., forgetting `gosec` for Go or `cargo-audit` for Rust), and incomplete CI/CD pipeline configurations. The lack of language-specific planning meant that the 12 Leverage Points of Agentic Development framework — which describes the 12 tunable dimensions of agent behavior — was not being fully utilized during project initialization.

Phase 42 introduces 6 language-specific planning commands (`/plan_python`, `/plan_java`, `/plan_go`, `/plan_rust`, `/plan_csharp`, `/plan_typescript`) that generate comprehensive, opinionated build plans tailored to each language's ecosystem. These plans serve as the "Plans" leverage point (dimension 10), providing detailed implementation blueprints that the agent follows through the ATLAS workflow.

---

## 2. Goals

1. Provide 6 language-specific planning commands that generate comprehensive build plans tailored to each supported language
2. Include project structure templates, technology stack recommendations, and STIG-hardened Dockerfiles in each plan
3. Generate CI/CD pipeline stage definitions with language-appropriate SAST, linting, formatting, and dependency auditing tools
4. Map CUI marking injection points specific to each language's comment syntax and file structure
5. Include test file structure covering unit tests, BDD scenarios, and E2E test specifications per language
6. Codify the 12 Leverage Points of Agentic Development framework as a systematic approach to tuning agent behavior

---

## 3. Architecture

```
Developer
    │
    ↓
/plan_<language> <app-name>
    │
    ↓
Language Registry (context/languages/language_registry.json)
    │
    ├── Framework Patterns (context/languages/framework_patterns.json)
    │
    ↓
Plan Generator
    │
    ├── 1. Project Structure Template
    ├── 2. Technology Stack (framework, ORM, DB, auth)
    ├── 3. STIG-Hardened Dockerfile
    ├── 4. CI/CD Pipeline Stages
    ├── 5. CUI Marking Injection Points
    ├── 6. Test File Structure (unit + BDD + E2E)
    ├── 7. Security Scanning Config (SAST, deps, secrets)
    └── 8. NIST 800-53 Control Mapping Hints
    │
    ↓
Markdown Plan Document
    │
    ↓
/icdev-init (scaffold) → ATLAS Workflow (build)
```

Each `/plan_<language>` command reads from the language registry and framework patterns to generate an 8-section build plan as a markdown document. The plan is reviewed by the developer, optionally customized, and then executed through `/icdev-init` for scaffolding followed by the ATLAS TDD workflow for implementation.

### 12 Leverage Points Framework

The 12 Leverage Points describe 12 dimensions that can be tuned to improve agent behavior:

**In Agent (Core Four):** Context, Model, Prompt, Tools
**Through Agent (Multipliers):** Standard Output, Types, Docs, Tests, Architecture, Plans, Templates, Workflows

Phase 42 planning commands embody dimension 10 (Plans) while generating artifacts that activate all other dimensions.

---

## 4. Requirements

### 4.1 Language-Specific Plans

#### REQ-42-001: Python Build Plan
The `/plan_python` command SHALL generate a build plan using Flask/FastAPI, pytest, behave, bandit, pip-audit, black+isort, and a STIG-hardened Dockerfile.

#### REQ-42-002: Java Build Plan
The `/plan_java` command SHALL generate a build plan using Spring Boot, Cucumber-JVM, checkstyle/PMD, SpotBugs, OWASP Dependency Check, google-java-format, and a STIG-hardened Dockerfile.

#### REQ-42-003: Go Build Plan
The `/plan_go` command SHALL generate a build plan using net/http or Gin, godog, golangci-lint, gosec, govulncheck, gofmt, and a STIG-hardened Dockerfile.

#### REQ-42-004: Rust Build Plan
The `/plan_rust` command SHALL generate a build plan using Actix-web, cucumber-rs, clippy, cargo-audit, rustfmt, and a STIG-hardened Dockerfile.

#### REQ-42-005: C# Build Plan
The `/plan_csharp` command SHALL generate a build plan using ASP.NET Core, SpecFlow, dotnet analyzers, SecurityCodeScan, dotnet format, and a STIG-hardened Dockerfile.

#### REQ-42-006: TypeScript Build Plan
The `/plan_typescript` command SHALL generate a build plan using Express, cucumber-js, eslint+tsc, eslint-security, npm audit, prettier, and a STIG-hardened Dockerfile.

### 4.2 Plan Content

#### REQ-42-007: NIST 800-53 Control Mapping
Each plan SHALL include NIST 800-53 control mapping hints that indicate which controls are addressed by each component of the technology stack.

#### REQ-42-008: CUI Marking Points
Each plan SHALL identify CUI marking injection points specific to the language's comment syntax (e.g., `# CUI // SP-CTI` for Python, `// CUI // SP-CTI` for Java/Go/Rust/C#/TypeScript).

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| *(No new tables)* | Phase 42 generates plan documents; execution uses existing project, task, and audit tables |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/builder/language_support.py` | Language detection and registry management — detect project languages, list supported languages |
| `tools/builder/scaffolder.py` | Project scaffolding from plan — creates directory structure, initial files, Dockerfile |
| `tools/builder/test_writer.py` | Generate test file structure per language (unit + BDD) |
| `tools/builder/code_generator.py` | TDD code generation from test files |
| `tools/builder/linter.py` | Language-appropriate linting |
| `tools/builder/formatter.py` | Language-appropriate formatting |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| — | Language-specific planning commands as Claude Code skills | Each language ecosystem has distinct tooling; generic plans lead to missed steps and misconfiguration |
| — | 12 Leverage Points framework codified | Provides systematic approach to tuning agent behavior across all 12 dimensions |
| — | Plans generate markdown documents (not code directly) | Human review step between planning and execution; developer can customize before scaffolding |

---

## 8. Security Gate

**Framework Planning Gate:**
- Generated plans must include STIG-hardened Dockerfile with non-root user, read-only rootfs, dropped capabilities
- Plans must include SAST tool configuration appropriate to the language
- Plans must include dependency audit tool appropriate to the language
- Plans must include CUI marking injection points
- Plans must reference applicable NIST 800-53 controls

---

## 9. Commands

```bash
# Language-specific planning commands (Claude Code skills)
/plan_python <app-name>       # Flask/FastAPI, pytest, behave, bandit, pip-audit
/plan_java <app-name>         # Spring Boot, Cucumber-JVM, SpotBugs, OWASP DC
/plan_go <app-name>           # net/http/Gin, godog, gosec, govulncheck
/plan_rust <app-name>         # Actix-web, cucumber-rs, clippy, cargo-audit
/plan_csharp <app-name>       # ASP.NET Core, SpecFlow, SecurityCodeScan
/plan_typescript <app-name>   # Express, cucumber-js, eslint-security, npm audit

# Language detection
python tools/builder/language_support.py --detect "/path/to/project"
python tools/builder/language_support.py --list

# Context files
# context/languages/language_registry.json — Language detection profiles
# context/languages/framework_patterns.json — Framework-specific patterns
```
