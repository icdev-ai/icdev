# CUI // SP-CTI

# Changelog

All notable changes to ICDEV are documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Comprehensive test suite expansion (324+ new tests across 21 test files)
- CI/CD pipeline for ICDEV itself (GitHub Actions + GitLab CI)
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
- Agentic application generation producing mini-ICDEV clone child applications (D44-D53)
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
- Federated GOTCHA marketplace with 3-tier catalog (D74-D81)
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
- Initial ICDEV platform with GOTCHA 6-layer agentic framework
- ATLAS and M-ATLAS build workflows (Architect, Trace, Link, Assemble, Stress-test)
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

# CUI // SP-CTI
