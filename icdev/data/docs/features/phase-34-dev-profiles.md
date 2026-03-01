# Phase 34 — Dev Profiles & Personalization

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 34 |
| Title | Dev Profiles & Personalization |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 21 (SaaS Multi-Tenancy), Phase 33 (Modular Installation) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV generates code, tests, compliance artifacts, infrastructure configurations, and documentation across 6 programming languages and 20+ compliance frameworks. Every organization has preferences: some teams use 4-space indentation while others use tabs; some prefer snake_case while others use camelCase; some mandate 80-character line limits while others allow 120; some require Go while others use Python; some need 100% branch coverage while others accept 80% line coverage.

Currently, these preferences are either hardcoded in tool defaults or scattered across `args/` YAML files with no organizational hierarchy. A tenant-level preference (e.g., "all projects in this organization use Go with gofmt and 120-character lines") cannot cascade down to individual projects. A program-level security mandate (e.g., "all projects under this program must use FIPS-validated crypto") cannot be enforced without per-project configuration. An ISSO who locks a security dimension at the tenant level has no way to prevent project-level overrides.

Furthermore, when developers join an existing project, they must manually discover and apply coding conventions. There is no auto-detection from existing codebases, no machine-readable profile that tools can consume, and no way to inject relevant style preferences into LLM prompts during code generation. The result is inconsistent output that requires manual correction, wasting developer time and introducing style drift.

Dev Profiles solve this through a 5-layer deterministic cascade (Platform, Tenant, Program, Project, User) with 10 dimension categories, role-based lock governance, auto-detection from codebases, PROFILE.md generation, and selective LLM prompt injection based on task type.

---

## 2. Goals

1. Implement a 5-layer deterministic cascade (Platform -> Tenant -> Program -> Project -> User) where each layer can override the one above, with locked dimensions that skip-propagate (child cannot override locked parent) (D184)
2. Define 10 dimension categories covering all code generation preferences: language, style, testing, architecture, security, compliance, operations, documentation, git, and AI
3. Support role-based lock governance allowing ISSOs to lock security dimensions, admins to lock compliance dimensions, and preventing unauthorized overrides at lower cascade layers
4. Enable auto-detection from existing codebases and natural language text, producing advisory-only profile suggestions that require human acceptance before activation (D185)
5. Generate PROFILE.md files from resolved dev profiles via Jinja2 templating, providing a human-readable narrative of coding conventions for each project (D186)
6. Inject relevant profile dimensions into LLM prompts during code generation, code review, and documentation tasks, selecting only the dimensions relevant to each task type (D187)
7. Provide 6 starter templates (DoD, FedRAMP, Healthcare, Financial, Law Enforcement, Startup) with pre-configured dimension values for rapid onboarding (D188)
8. Support version history with diff, rollback, and audit trail for all profile changes using append-only semantics (D183)

---

## 3. Architecture

```
+---------------------------------------------------------------+
|                  Dev Profile Cascade (5 Layers)                |
|                                                                |
|  +-------------------+                                        |
|  | Platform Defaults |  (Hardcoded ICDEV baseline)            |
|  +--------+----------+                                        |
|           |                                                    |
|           v                                                    |
|  +-------------------+                                        |
|  | Tenant Profile    |  (Organization-wide preferences)       |
|  | [LOCKABLE]        |  ISSO/admin can lock dimensions        |
|  +--------+----------+                                        |
|           |                                                    |
|           v                                                    |
|  +-------------------+                                        |
|  | Program Profile   |  (Program/portfolio overrides)         |
|  | [LOCKABLE]        |  Program manager can lock              |
|  +--------+----------+                                        |
|           |                                                    |
|  +--------v----------+                                        |
|  | Project Profile   |  (Project-specific overrides)          |
|  | [LOCKABLE]        |  Project lead can lock                 |
|  +--------+----------+                                        |
|           |                                                    |
|           v                                                    |
|  +-------------------+                                        |
|  | User Profile      |  (Individual preferences)              |
|  +-------------------+  (Cannot override locked dimensions)   |
|                                                                |
|  Resolution: merge top-down, skip locked dimensions            |
+---------------------------------------------------------------+
```

### 10 Dimension Categories

| Category | Example Settings |
|----------|-----------------|
| Language | Primary language, secondary languages, package manager |
| Style | Indentation (spaces/tabs), line length, naming convention (snake_case/camelCase) |
| Testing | Coverage target, test framework, BDD framework, test strategy |
| Architecture | Pattern (microservices/monolith), API style (REST/gRPC), data layer |
| Security | Crypto mode (FIPS/standard), vulnerability thresholds, SAST rules |
| Compliance | Active frameworks, impact level, classification |
| Operations | Container runtime, orchestrator, CI/CD platform |
| Documentation | Docstring style, README template, inline comment density |
| Git | Commit message format, branch naming, merge strategy |
| AI | Model preference, temperature, max tokens, prompt style |

---

## 4. Requirements

### 4.1 Profile Cascade

#### REQ-34-001: Five-Layer Cascade (D184)
The system SHALL resolve dev profiles through a deterministic 5-layer cascade: Platform -> Tenant -> Program -> Project -> User, where each layer merges over the one above.

#### REQ-34-002: Locked Dimension Skip-Propagation
When a dimension is locked at a higher layer, lower layers SHALL NOT be able to override that dimension. The locked value propagates through all child layers unchanged.

#### REQ-34-003: Version-Based Immutability (D183)
Profile updates SHALL create new versions (no UPDATE on `dev_profiles` table), consistent with the append-only pattern (D6). Each version has a monotonically increasing version number.

#### REQ-34-004: Deterministic Resolution
Profile cascade resolution SHALL be fully deterministic: given the same set of profiles at all layers, the resolved profile SHALL always produce identical output.

### 4.2 Lock Governance

#### REQ-34-005: Role-Based Locking
The system SHALL support dimension locking with role requirements: `isso` role required to lock/unlock `security` dimensions, `admin` role required to lock/unlock `compliance` dimensions.

#### REQ-34-006: Lock Audit Trail
All lock and unlock operations SHALL be recorded in the `dev_profile_locks` table with the locking role, actor identity, timestamp, and lock scope (dimension path).

### 4.3 Auto-Detection

#### REQ-34-007: Codebase Detection (D185)
The system SHALL auto-detect profile dimensions from existing codebases by analyzing file extensions, configuration files (pyproject.toml, package.json, go.mod), linter configs, and code patterns.

#### REQ-34-008: Text-Based Detection
The system SHALL detect profile dimensions from natural language text (e.g., "We use Go, snake_case, 120-char lines") using keyword matching against the dimension registry.

#### REQ-34-009: Advisory-Only Detection
Auto-detected profile dimensions SHALL be advisory only (D110 pattern), requiring explicit human acceptance before being applied to the profile.

### 4.4 Profile Output

#### REQ-34-010: PROFILE.md Generation (D186)
The system SHALL generate a PROFILE.md file from the resolved dev profile via Jinja2 templating, producing a human-readable narrative of all coding conventions, style rules, and configuration decisions.

#### REQ-34-011: LLM Prompt Injection (D187)
The system SHALL inject relevant profile dimensions into LLM prompts during task execution, selecting only the dimensions relevant to the task type: code generation gets language+style, code review gets testing+security, documentation gets documentation+style.

#### REQ-34-012: Starter Templates (D188)
The system SHALL provide 6 starter templates in `context/profiles/*.yaml` (DoD, FedRAMP, Healthcare, Financial, Law Enforcement, Startup) with pre-configured dimension values for rapid onboarding.

### 4.5 Version Management

#### REQ-34-013: Version History
The system SHALL maintain a complete version history for every profile, including version number, change summary, changed-by identity, and timestamp.

#### REQ-34-014: Version Diff
The system SHALL support diffing between any two versions of a profile, showing which dimensions were added, removed, or modified.

#### REQ-34-015: Version Rollback
The system SHALL support rolling back a profile to a previous version by creating a new version with the old content (consistent with append-only semantics).

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `dev_profiles` | Profile versions: scope, scope_id, version, dimensions_json, created_by, change_summary, created_at |
| `dev_profile_locks` | Dimension locks: scope, scope_id, dimension_path, lock_role, locked_by, locked_at, unlocked_at |
| `dev_profile_detections` | Auto-detection results: scope_id, detected_dimensions_json, source (repo/text), accepted, detected_at |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/builder/dev_profile_manager.py` | Full profile CRUD: create, get, resolve cascade, update, lock/unlock, diff, rollback, inject, history |
| `tools/builder/profile_detector.py` | Auto-detect profile dimensions from repository analysis or natural language text |
| `tools/builder/profile_md_generator.py` | Generate PROFILE.md from resolved profile via Jinja2 templating |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D183 | Version-based immutability -- no UPDATE on `dev_profiles`, insert new version | Consistent with D6 append-only pattern; full version history preserved for audit |
| D184 | 5-layer deterministic cascade with locked dimension skip-propagation | Organizational hierarchy maps naturally to Platform/Tenant/Program/Project/User; locks prevent unauthorized overrides |
| D185 | Auto-detection is advisory only -- requires human acceptance | Consistent with D110 compliance auto-detection; prevents false-positive profile contamination |
| D186 | PROFILE.md generated from dev_profile via Jinja2 (consistent with D50 dynamic CLAUDE.md) | Read-only narrative, not separately editable; always reflects actual resolved profile |
| D187 | LLM injection uses selective dimension extraction per task context | Code gen gets language+style; review gets testing+security; documentation gets documentation+style; reduces irrelevant context |
| D188 | Starter templates in `context/profiles/*.yaml` | Consistent with `context/requirements/default_constitutions.json` pattern; 6 sector-specific templates for rapid onboarding |

---

## 8. Security Gate

**Dev Profile Gate:**
- Locked dimensions cannot be overridden at lower cascade layers regardless of user role
- ISSO role required to lock/unlock `security` dimensions
- Admin role required to lock/unlock `compliance` dimensions
- All profile changes recorded in versioned append-only `dev_profiles` table
- All lock/unlock operations recorded in `dev_profile_locks` with actor identity
- Auto-detected profiles require explicit human acceptance before activation
- Profile injection into LLM prompts excludes sensitive dimensions (API keys, credentials) by default

---

## 9. Commands

```bash
# Create profile from template
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --create --template dod_baseline --json

# Create with explicit data
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --create --data '{"language":{"primary":"go"}}' --created-by "admin" --json

# Get and resolve profiles
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --get --json
python tools/builder/dev_profile_manager.py --scope project --scope-id "proj-123" --resolve --json

# Update (creates new version)
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --update --changes '{"style":{"line_length":120}}' --change-summary "Update line length" --updated-by "admin" --json

# Lock/unlock dimensions
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --lock --dimension-path "security" --lock-role isso --locked-by "isso@mil" --json
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --unlock --dimension-path "security" --unlocked-by "isso@mil" --role isso --json

# Version management
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --diff --v1 1 --v2 3 --json
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --rollback --target-version 1 --rolled-back-by "admin" --json
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --history --json

# Auto-detection
python tools/builder/profile_detector.py --repo-path /path/to/repo --json
python tools/builder/profile_detector.py --text "We use Go, snake_case, 120-char lines" --json

# PROFILE.md generation
python tools/builder/profile_md_generator.py --scope project --scope-id "proj-123" --json
python tools/builder/profile_md_generator.py --scope project --scope-id "proj-123" --output /path/PROFILE.md --store

# LLM prompt injection
python tools/builder/dev_profile_manager.py --scope project --scope-id "proj-123" --inject --task-type code_generation --json
```

**CUI // SP-CTI**
