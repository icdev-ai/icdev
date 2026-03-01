# Phase 33 — Modular Installation

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 33 |
| Title | Modular Installation |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 21 (SaaS Multi-Tenancy), Phase 23 (Universal Compliance) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV has grown to encompass 15 agents, 15 MCP servers, 20+ compliance frameworks, 6 programming languages, and 183 database tables. Deploying the full system requires significant infrastructure, configuration, and operational expertise. However, not every customer needs every module. An ISV startup building a SaaS product needs a builder, basic security scanning, and a dashboard -- not MBSE integration, supply chain intelligence, or DoD MOSA assessment. A healthcare organization needs HIPAA, HITRUST, and SOC 2 compliance -- not FedRAMP High, CMMC, or CJIS. A DoD program office needs everything including IL6 air-gapped support, but even they may not need the marketplace or innovation engine on day one.

The monolithic deployment model forces customers into an all-or-nothing choice: deploy everything (complex, expensive, more attack surface) or nothing (no value). There is no middle ground. Customers cannot start small and grow, cannot add compliance frameworks as their posture matures, and cannot remove modules they do not need. This creates adoption friction, increases operational cost, and expands the attack surface unnecessarily.

Modular installation solves this by decomposing ICDEV into independently deployable modules with explicit dependency resolution. Customers choose a deployment profile (or build a custom one) that matches their organizational role, compliance posture, target platform, and team size. The installer resolves dependencies, generates platform-specific artifacts (Docker Compose, K8s manifests, Helm values, .env files), and creates only the database tables required by the selected modules. Existing installations can be upgraded incrementally by adding modules or compliance frameworks without disrupting running workloads.

---

## 2. Goals

1. Decompose ICDEV into independently deployable modules with explicit dependency declarations, enabling customers to install only what they need
2. Provide 10 pre-built deployment profiles (ISV Startup, ISV Enterprise, SI Consulting, SI Enterprise, DoD Team, Healthcare, Financial, Law Enforcement, GovCloud Full, Custom) covering the most common organizational patterns
3. Implement an interactive wizard that guides new users through platform selection, compliance posture, and module choices with dependency auto-resolution
4. Generate platform-specific deployment artifacts (Docker Compose, K8s manifests, K8s RBAC, Helm values, .env templates) based on selected modules
5. Support incremental module addition and upgrade paths for existing installations without disrupting running workloads
6. Resolve module dependencies automatically: if a module requires another (e.g., marketplace requires SaaS), the installer adds the dependency module
7. Create only the database tables required by selected modules, reducing schema size and attack surface for minimal installations

---

## 3. Architecture

```
+---------------------------------------------------------------+
|                    Modular Installer                           |
|                                                                |
|  +-------------------+    +-----------------------------+      |
|  | Interactive       |    | Profile-Based               |      |
|  | Wizard            |    | Installation                |      |
|  | --interactive     |    | --profile dod_team          |      |
|  | 3 questions:      |    | Pre-built bundles:          |      |
|  |  1. Platform      |    |  ISV Startup (7 modules)    |      |
|  |  2. Compliance    |    |  DoD Team (14 modules)      |      |
|  |  3. Modules       |    |  Healthcare (9 modules)     |      |
|  +--------+----------+    +-----------+-----------------+      |
|           |                           |                        |
|           v                           v                        |
|  +---------------------------------------------------+        |
|  |           Module Dependency Resolver               |        |
|  |  installation_manifest.yaml (module definitions)   |        |
|  |  deployment_profiles.yaml (profile bundles)        |        |
|  |  Topological sort for install order                |        |
|  +---------------------------------------------------+        |
|           |                                                    |
|           v                                                    |
|  +---------------------------------------------------+        |
|  |           Platform Artifact Generator              |        |
|  |  Docker Compose | K8s Manifests | Helm Values      |        |
|  |  K8s RBAC | .env Template                          |        |
|  |  DB table groups (per-module)                      |        |
|  +---------------------------------------------------+        |
+---------------------------------------------------------------+
```

### Deployment Profiles

| Profile | Modules | Compliance | Platform | CUI |
|---------|---------|------------|----------|-----|
| ISV Startup | 7 core | None | Docker | No |
| ISV Enterprise | 11 | FedRAMP Moderate | K8s | No |
| SI Consulting | 5 + RICOAS | FedRAMP + CMMC | Docker | Yes |
| SI Enterprise | 14 | FedRAMP High + CMMC + CJIS | K8s | Yes |
| DoD Team | 14 | FedRAMP High + CMMC + FIPS + cATO | K8s | Yes |
| Healthcare | 9 | HIPAA + HITRUST + SOC 2 | K8s | No |
| Financial | 9 | PCI DSS + SOC 2 + ISO 27001 | K8s | No |
| Law Enforcement | 9 | CJIS + FIPS 199/200 | K8s | Yes |
| GovCloud Full | ALL | ALL | K8s | Yes |
| Custom | 3 minimum | User choice | User choice | Configurable |

---

## 4. Requirements

### 4.1 Module System

#### REQ-33-001: Module Definitions
The system SHALL define all ICDEV modules in `args/installation_manifest.yaml`, each with a unique identifier, description, list of dependencies, list of database table groups, list of required configuration files, and list of provided agents/services.

#### REQ-33-002: Dependency Resolution
The installer SHALL automatically resolve module dependencies using topological sorting, adding required dependency modules when a module is selected.

#### REQ-33-003: Minimum Installation
The minimum installation SHALL require exactly 3 modules: core (database, memory, audit), builder (scaffolding, code generation, testing), and dashboard (web UI).

#### REQ-33-004: Database Table Groups
Each module SHALL declare its database table groups so that `init_icdev_db.py` creates only the tables needed by installed modules, reducing schema size for minimal installations.

### 4.2 Installation Modes

#### REQ-33-005: Interactive Wizard
The system SHALL provide an interactive wizard (`--interactive`) that guides users through 3 setup questions: target platform (Docker/K8s/Helm), compliance frameworks, and module selection with visual dependency tree.

#### REQ-33-006: Profile-Based Installation
The system SHALL support profile-based installation (`--profile <name>`) using 10 pre-built deployment profiles that bundle modules, compliance frameworks, and platform defaults.

#### REQ-33-007: Compliance Posture Configuration
The system SHALL support compliance posture selection (`--compliance <frameworks>`) that auto-selects required modules and enables corresponding assessment gates.

### 4.3 Platform Artifacts

#### REQ-33-008: Docker Compose Generation
The system SHALL generate Docker Compose files containing only the services for selected modules, with appropriate resource limits, network configuration, and volume mounts.

#### REQ-33-009: Kubernetes Manifest Generation
The system SHALL generate K8s manifests (deployments, services, network policies, RBAC) for selected modules, using the existing STIG-hardened container configurations.

#### REQ-33-010: Helm Values Generation
The system SHALL generate Helm values files (`values.yaml`) with module-specific overrides for on-premises deployment via the existing Helm chart.

#### REQ-33-011: Environment Template Generation
The system SHALL generate `.env` template files containing only the environment variables required by selected modules.

### 4.4 Upgrade and Addition

#### REQ-33-012: Module Addition
The system SHALL support adding modules to an existing installation (`--add-module <name>`) without disrupting running workloads, applying only the incremental database migrations and configuration changes.

#### REQ-33-013: Compliance Addition
The system SHALL support adding compliance frameworks to an existing installation (`--add-compliance <framework>`) with automatic dependency resolution.

#### REQ-33-014: Upgrade Discovery
The system SHALL provide an upgrade command (`--upgrade`) that shows which modules can be added to the current installation, including estimated resource impact.

#### REQ-33-015: Installation Status
The system SHALL provide a status command (`--status`) that reports installed modules, active compliance frameworks, database table count, and configuration completeness.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| (No new tables) | Phase 33 uses the existing `schema_migrations` table (D150) for tracking module installations; module state is derived from installed table groups |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/installer/installer.py` | Main installer entry point: interactive wizard, profile-based, add-module, upgrade, status |
| `tools/installer/module_registry.py` | Module dependency resolution, validation, table group mapping |
| `tools/installer/compliance_configurator.py` | Compliance posture selection, framework-to-module mapping, list postures |
| `tools/installer/platform_setup.py` | Platform artifact generation: Docker Compose, K8s manifests, K8s RBAC, Helm values, .env templates |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D58 | SaaS layer wraps existing tools, does not rewrite them | Module boundaries follow existing tool boundaries; installer activates/deactivates, never rewrites |
| D60 | Separate database per tenant | Module installation is per-tenant; each tenant can have a different module set |
| D150 | Lightweight migration runner with `schema_migrations` table | Module additions trigger incremental migrations, not full reinit; checksum validation prevents drift |

---

## 8. Security Gate

**Module Installation Gate:**
- Dependency resolution must complete without circular dependencies before installation proceeds
- All selected compliance frameworks must have corresponding assessment tools present in the installation
- Database migration checksums must validate against expected values before applying incremental changes
- Platform artifacts must pass validation: Docker Compose `config` check, K8s `kubeval` validation (when available), Helm `lint`
- Air-gapped installations must have all container images pre-loaded (no registry pull at install time)
- Module removal is not supported for modules with active audit trail data (append-only preservation)

---

## 9. Commands

```bash
# Interactive wizard -- guided setup
python tools/installer/installer.py --interactive

# Profile-based installation
python tools/installer/installer.py --profile dod_team --compliance fedramp_high,cmmc --platform k8s
python tools/installer/installer.py --profile isv_startup --platform docker
python tools/installer/installer.py --profile healthcare --compliance hipaa,hitrust

# Add features to existing installation
python tools/installer/installer.py --add-module marketplace
python tools/installer/installer.py --add-compliance hipaa
python tools/installer/installer.py --upgrade                   # Show what can be added

# Status and validation
python tools/installer/installer.py --status --json
python tools/installer/module_registry.py --validate
python tools/installer/compliance_configurator.py --list-postures

# Platform artifact generation
python tools/installer/platform_setup.py --generate docker --modules core,llm,builder,dashboard
python tools/installer/platform_setup.py --generate k8s-rbac --modules core,builder
python tools/installer/platform_setup.py --generate env --modules core,llm
python tools/installer/platform_setup.py --generate helm-values --modules core,llm,builder
```

**CUI // SP-CTI**
