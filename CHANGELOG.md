# [TEMPLATE: CUI // SP-CTI]

# Changelog

All notable changes to ICDEV™ are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.2.19] - 2026-04-17

### Added
- **Failure Triage Reflex** (`tools/genesis/reflexes/failure_triage.py`) — wires `failure_triage.triage_once` into the Genesis daemon on a 30-min cadence (`args/genesis_config.yaml`). YELLOW tier. With `ICDEV_AUTOFIX_ENABLED=true` in `.env`, auto-review of failed kanban tasks now runs without manual invocation.

### Changed
- **Task-type whitelist** corrected from `{build, bug, chore, test, research}` → `{build, chore, fix, research, test}`. Counts from the live table (2026-04-17): `chore=742, build=307, fix=200, test=89, research=49, deploy=12`. `bug` had zero instances; `fix` was the third-most-common type but incorrectly excluded. `deploy` stays excluded — higher blast radius.

## [1.2.18] - 2026-04-17

### Added
- **Failure Triage — Worktree-Apply Stage** (`tools/workflow/failure_triage.py`) — `apply_patch_in_worktree()` completes the auto-fix loop. Creates an isolated worktree at `.tmp/autofix/<task>__<sig>/` on branch `autofix/<task>-<sig>`, applies the LLM patch, runs the verification command, commits on success, rolls back the whole worktree + branch on failure. Pre-apply validation rejects: shell metacharacters / non-allowlisted prefixes in `verification_command`; path traversal, nonexistent files, files not in `diag.suspect_files`, non-unique `old_string`, deny-list prefix matches. Rate budget is only consumed after validation passes. Audit trail at `.tmp/kanban/autofix-audit/<task>__<sig>.json`.
- **Second opt-in: `ICDEV_AUTOFIX_AUTOMERGE`** — even when a patch applies cleanly and verifies green, the `autofix/*` branch is NOT fast-forward merged into main unless this env is also set. Default off. Refuses to merge from a non-main checkout or a dirty working tree.

### Tests
- 35 unit tests (up from 20) — full coverage of the apply stage validation (allowlist, traversal, uniqueness, deny-list) and rejection paths (validation failures must not consume rate budget or create worktrees).

## [1.2.17] - 2026-04-17

### Added
- **Failure Triage** (`tools/workflow/failure_triage.py`) — LLM-assisted review of kanban failures bridging the gap between `self_debug.py` (recurrence-only) and manual intervention. Two-tier LLM routing: Claude for diagnose (`failure_triage_diagnose`), Ollama for patch generation (`failure_triage_patch`). Conservative defaults — `ICDEV_AUTOFIX_ENABLED=false` by default, `--dry-run` default, confidence threshold 0.85, task-type whitelist, file deny-list, rolling 5-apply/hour rate cap. Generated patches are attached to `status='suggested'` Oracle cards for human review — auto-apply dispatcher is a separate follow-up.

### Fixed
- **NIST AI 600-1 Assessor** (`tools/compliance/nist_ai_600_1_assessor.py`) — typo `self._db_path` → `self.db_path`. Was being swallowed by broad `except Exception: pass`; every GAI-* check silently returned empty. Surfaced via regression pytest `-x` hitting `test_confabulation_check_satisfied_with_data`.
- **Kanban depends-on E2E** (`tests/e2e_kanban_depends_on.py`) — step 5 (unblock-on-parent-done) now passes `bypass_verification: true` + audit reason to satisfy guard-22 (the move→done verification gate added 2026-04-14).

## [1.2.8] - 2026-03-31

### Added
- **Network Design Canvas** — ACAS/Nessus vulnerability scan overlay with host-to-node matching and severity heat maps
- **Network Design Canvas** — Natural language topology queries powered by deterministic graph algorithms (NetworkX) with local LLM fallback
- **Network Design Canvas** — Cloud architecture diagram generation for all 6 CSPs
- **Network Design Canvas** — Compliance audit page with per-device STIG tracking and exportable checklists
- **Network Design Canvas** — 1,000+ device types, protocols, and interface constants
- **Network Design Canvas** — Enhanced inventory export (CSV, JSON, YAML) with advanced filtering
- **IBM Cloud IaC** — New Terraform generator for IBM Cloud (VPC, IKS, KMS, COS, LogDNA, SysDig)
- **Azure IaC** — Major expansion: AKS, Key Vault, Front Door, Application Gateway, NSG rules
- **GCP IaC** — Major expansion: GKE, Cloud KMS, Cloud Armor, VPC Service Controls
- **OCI IaC** — Major expansion: OKE, Vault, WAF, Network Security Groups, Bastion
- **SRE Operations** — Dashboard, runbook library, incident tracking, toil budgets, SLO monitoring
- **Pipeline Canvas** — Visual CI/CD pipeline designer with drag-and-drop stages and YAML export

## [Unreleased]

### Security
- Remove unauthorized `process_exec` capability from `subprocess_backend.py` (finding #38, Task-9366644d1d-d5).

### Added
- **IQE v0.1 — ICDEV Query Engine** (`tools/iqe/`) — declarative `foreach / where / select` DSL for running compliance and network-health checks across all seven design-canvas databases. Ships with: hand-rolled recursive-descent parser (`parser.py`), typed AST dataclasses (`ast_nodes.py`), adapter-dispatching executor with SQL-injection-safe fallback (`executor.py`), and a 5-query NDC seed library under `context/iqe/queries/network/` (vendor inventory, BGP peer asymmetry, admin/oper mismatch, CAT I STIG open findings, capacity threshold). See `docs/features/phase-iqe-v0-1.md`.
- Comprehensive test suite expansion (324+ new tests across 21 test files)
- CI/CD pipeline for ICDEV™ itself (GitHub Actions + GitLab CI)
- REST API endpoints for Phases 22-28 capabilities
- Helm chart completion for all 15 agents
- Project documentation (README, CONTRIBUTING, CHANGELOG)

## [Phase 29-32] - 2026-02-XX

### Added
- Dashboard authentication with per-user API keys and SHA-256 hashing (D169-D172)
- RBAC with 5 roles: admin, pm, developer, isso, co (D172)
- Activity feed merging audit trail and hook events via UNION ALL query (D174)
- BYOK (Bring Your Own Key) LLM key management with Fernet AES-256 encryption (D175-D178)
- Usage tracking and cost dashboard per-user and per-provider (D177)
- Spec-kit pattern tools: quality checker, consistency analyzer, constitution manager, clarification engine, spec organizer (D156-D161)
- Proactive monitoring heartbeat daemon with 7 configurable checks (D162-D163)
- Auto-resolver for webhook-triggered issue fix with branch/PR creation (D164-D166)
- Selective skill injection via deterministic keyword-based category matching (D167)
- Time-decay memory ranking with exponential formula and per-type half-lives (D168)
- Resilience patterns: circuit breaker (3-state machine), retry with exponential backoff, correlation IDs (D146-D149)
- Database migration runner with checksum validation and multi-tenant support (D150-D151)
- Database backup and restore with optional AES-256-CBC encryption (D152)
- OpenAPI 3.0.3 spec with Swagger UI for the API gateway (D153)
- Prometheus metrics endpoint with 8 metrics (D154)
- Cross-platform compatibility module for Windows, macOS, Linux (D145)
- CUI markings added to all Python files

### Changed
- Dashboard login page updated for API key authentication flow
- Settings page expanded with LLM key management section
- Team management page updated with role-based controls

## [Phase 23-28] - 2026-01-XX

### Added
- Universal Compliance Platform with 10 data categories and composable markings (D109)
- Dual-hub crosswalk model: NIST 800-53 (US) + ISO 27001 (international) with bidirectional bridge (D111)
- 6 Wave 1 compliance frameworks: CJIS, HIPAA, HITRUST CSF v11, SOC 2 Type II, PCI DSS v4.0, ISO/IEC 27001:2022 (D116)
- Compliance auto-detection from data types with ISSO confirmation gate (D110)
- Multi-regime assessment with crosswalk deduplication (D113)
- BaseAssessor ABC pattern reducing per-framework code to approximately 60 LOC (D116)
- DevSecOps profile management with 10 stages and 5 maturity levels (D119)
- Pipeline security generation with Kyverno and OPA policy-as-code (D121)
- Image signing and attestation management
- Zero Trust Architecture: 7-pillar maturity scoring aligned to DoD ZTA Strategy (D120)
- NIST SP 800-207 compliance assessment and gate (D118)
- Service mesh generation for Istio and Linkerd (D121)
- Network segmentation with namespace isolation and microsegmentation
- PDP/PEP configuration for DISA ICAM, Zscaler, Palo Alto (D124)
- ZTA posture score as cATO evidence dimension (D123)
- DoD MOSA compliance framework (10 U.S.C. section 4401) via BaseAssessor pattern (D127)
- Modularity analysis: coupling, cohesion, circular dependency detection (D129)
- ICD and TSP document generation with CUI markings (D128)
- MOSA code enforcement via static analysis (D129)
- MOSA auto-trigger for DoD/IC customers during intake (D125)
- CLI capabilities with 4 independent optional toggles and tenant ceiling (D132)
- Remote Command Gateway with 5 channel adapters: Telegram, Slack, Teams, Mattermost, internal chat (D133)
- 8-gate security chain for remote commands (D136)
- IL-aware response filtering (D135)
- User binding ceremony for remote command authorization (D136)
- Air-gapped mode auto-disabling internet-dependent channels (D139)
- Command allowlist with per-channel overrides (D137)
- Auto-scaling: HPA manifests for all 15 agents + dashboard + API gateway (D141)
- Pod Disruption Budgets with tier-based policies (D143)
- Cross-AZ topology spread constraints (D144)
- Cloud-agnostic node autoscaler reference (D142)

## [Phase 19-22] - 2026-01-XX

### Added
- Agentic application generation producing mini-ICDEV™ clone child applications (D44-D53)
- 6-dimension fitness scoring for agentic suitability assessment (D46)
- Blueprint-driven generation with 12-step pipeline (D47)
- Grandchild prevention mechanism (D52)
- CSP MCP integration registry for AWS, GCP, Azure, Oracle
- FIPS 199 security categorization with SP 800-60 information types and high watermark (D54)
- FIPS 200 validation across all 17 minimum security areas (D55)
- CNSSI 1253 overlay auto-application for IL6/SECRET (D57)
- Dynamic SSP baseline selection from categorization (D56)
- SaaS multi-tenancy platform with API gateway (D58-D65)
- Per-tenant database isolation with strongest isolation model (D60)
- 3 authentication methods: API key, OAuth 2.0/OIDC, CAC/PIV
- 3 subscription tiers: Starter, Professional, Enterprise
- REST API and MCP Streamable HTTP transport (D62)
- Per-tenant K8s namespace provisioning (D63)
- Offline license keys with RSA-SHA256 signatures for on-prem (D64)
- Helm chart for on-prem deployment (D65)
- Federated FORGE marketplace with 3-tier catalog (D74-D81)
- 7-gate automated security scanning for marketplace assets (D76)
- IL-aware compatibility checking with high-watermark consumption rule (D77)
- Community ratings and reviews for marketplace assets
- Marketplace SBOM generation for executable assets (D81)
- LLM provider abstraction with vendor-agnostic routing (D66-D73)
- Function-level LLM routing for best-of-breed model selection (D68)
- Ollama support for air-gapped environments (D69)
- Vision LLM support for diagram extraction and UI analysis (D82-D87)

## [Phase 13-18] - 2025-XX-XX

### Added
- ATO acceleration: FedRAMP Moderate/High, CMMC Level 2/3, OSCAL generation (Phase 17)
- eMASS bidirectional sync with hybrid mode
- cATO continuous monitoring with evidence freshness tracking
- PI compliance velocity tracking
- Control crosswalk engine with dual-hub model
- Classification manager for IL2 through IL6
- MBSE integration: SysML XMI import, DOORS NG ReqIF import (Phase 18)
- Digital thread with auto-linking and coverage reporting
- Model-code generation from SysML block definitions
- Model-code drift detection and sync engine
- DoDI 5000.87 Digital Engineering Strategy compliance assessment
- Diagram extraction from SysML screenshots via vision LLM
- Application modernization: 7Rs assessment framework
- Version migration (Python 2.7 to 3.11, Java 8 to 17, etc.)
- Framework migration (Struts to Spring Boot, etc.)
- Monolith decomposition with microservice extraction
- Database migration DDL planning
- Strangler fig pattern management
- ATO compliance bridge for migration tracking

## [Phase 7-12] - 2025-XX-XX

### Added
- RICOAS Requirements Intake: conversational AI-driven intake with 5-stage pipeline
- Gap detection across 5 dimensions (completeness, clarity, feasibility, compliance, testability)
- SAFe decomposition: Epic, Capability, Feature, Story, Enabler with WSJF scoring
- Document extraction from SOW/CDD/CONOPS (shall/must/should statements)
- ATO boundary impact assessment with 4-tier classification (GREEN/YELLOW/ORANGE/RED)
- Supply chain intelligence: dependency graph, ISA lifecycle, SCRM assessment, CVE triage
- Digital Program Twin simulation: 6-dimension what-if analysis
- Monte Carlo estimation for schedule and cost
- COA generation (Speed/Balanced/Comprehensive) with comparison and selection
- External integration: bidirectional Jira, ServiceNow, GitLab sync
- DOORS NG ReqIF export
- Approval workflow management
- RTM traceability builder
- CI/CD integration: GitHub webhooks, GitLab webhooks, issue polling
- Observability: hook-based agent monitoring, HMAC-signed events, SIEM forwarding
- NLQ compliance queries via Bedrock with read-only SQL enforcement
- Parallel CI/CD via git worktree task isolation

## [Phase 1-6] - 2025-XX-XX

### Added
- Initial ICDEV™ platform with FORGE 6-layer agentic framework
- ANVIL and M-ANVIL build workflows (Architect, Navigate, Verify, Integrate, Launch)
- 15 multi-agent architecture across 3 tiers (Core, Domain, Support)
- A2A protocol (JSON-RPC 2.0 over mutual TLS) for inter-agent communication
- 14 MCP servers for Claude Code integration (stdio transport)
- Memory system: dual storage (markdown + SQLite), hybrid search (BM25 + semantic)
- Project management: create, list, status tracking
- TDD workflow: RED, GREEN, REFACTOR cycle with 6 language support
- Builder tools: scaffolding, code generation, linting, formatting
- NIST 800-53 Rev 5 compliance: SSP, POAM, STIG checklist, SBOM generation
- CUI marking system for IL4/IL5/IL6 artifacts
- DoD CSSP (DI 8530.01) compliance assessment
- CISA Secure by Design assessment
- IEEE 1012 IV&V assessment
- Security scanning: SAST (Bandit), dependency audit, secret detection, container scanning
- Infrastructure generation: Terraform, Ansible, K8s manifests, CI/CD pipelines
- Knowledge base with self-healing pattern detection
- Monitoring: log analysis, metrics, alerts, health checks
- Web dashboard with Flask (project status, compliance, security, agent management)
- STIG-hardened Docker containers for all agents
- Kubernetes manifests with network policies (default deny)
- Audit trail: append-only, immutable, NIST AU compliant

# [TEMPLATE: CUI // SP-CTI]
