# Goals Manifest

> Index of all goal workflows. Check here before starting any task.

| Goal | File | Description |
|------|------|-------------|
| ATLAS Workflow | goals/build_app.md | 5-step process for building full-stack apps: Architect, Trace, Link, Assemble, Stress-test |
| Project Init | goals/init_project.md | Initialize new ICDEV project with full compliance scaffolding |
| TDD Workflow | goals/tdd_workflow.md | True TDD: Gherkin feature first, step definitions, then implementation |
| Compliance Workflow | goals/compliance_workflow.md | Generate all ATO + CSSP artifacts (SSP, POAM, STIG, SBOM, CUI, CSSP assessment, IR plan, SIEM, evidence, Xacta sync) |
| Security Scanning | goals/security_scan.md | Run comprehensive security scanning pipeline |
| Deployment Workflow | goals/deploy_workflow.md | Terraform + Ansible + K8s deployment with CI/CD and rollback |
| Code Review | goals/code_review.md | Enforced review gates with security and compliance checks |
| Self-Healing | goals/self_healing.md | Pattern detection, auto-correction, knowledge recording |
| Monitoring | goals/monitoring.md | Production observability, alerting, and log analysis |
| Dashboard | goals/dashboard.md | Web dashboard for business users |
| Agent Management | goals/agent_management.md | A2A agent lifecycle, task routing, and discovery |
| Integration Testing | goals/integration_testing.md | Multi-layer testing: unit, BDD, E2E (Playwright), security/compliance gates |
| CI/CD Integration | goals/cicd_integration.md | GitHub + GitLab dual-platform webhooks, polling, and workflow automation |
| SbD & IV&V Workflow | goals/sbd_ivv_workflow.md | Secure by Design assessment + IV&V certification (CISA, IEEE 1012, DoDI 5000.87) |
| Maintenance Audit | goals/maintenance_audit.md | Dependency scanning, vulnerability checking, SLA enforcement, auto-remediation |
| ATO Acceleration | goals/ato_acceleration.md | Multi-framework ATO pursuit: FedRAMP + CMMC + OSCAL + eMASS + cATO monitoring |
| MBSE Integration | goals/mbse_integration.md | Model-Based Systems Engineering: SysML import, DOORS NG, digital thread, model-code sync, DES compliance, M-ATLAS workflow |
| App Modernization | goals/modernization_workflow.md | Legacy app modernization: 7Rs assessment, version/framework migration, monolith decomposition, strangler fig, ATO compliance bridge |
| Requirements Intake | goals/requirements_intake.md | AI-driven conversational requirements intake, gap detection, SAFe decomposition, readiness scoring, document extraction (RICOAS Phase 1) |
| Boundary & Supply Chain | goals/boundary_supply_chain.md | ATO boundary impact (4-tier), supply chain dependency graph, ISA lifecycle, SCRM, CVE triage (RICOAS Phase 2) |
| Simulation Engine | goals/simulation_engine.md | Digital Program Twin — 6-dimension what-if simulation, Monte Carlo, COA generation & comparison (RICOAS Phase 3) |
| External Integration | goals/external_integration.md | Bidirectional Jira/ServiceNow/GitLab sync, DOORS NG ReqIF export, approval workflows, RTM traceability (RICOAS Phase 4) |
| Observability | goals/observability.md | Hook-based agent monitoring: tool usage tracking, HMAC-signed events, agent execution framework, SIEM forwarding (Phase 39) |
| NLQ Compliance | goals/nlq_compliance.md | Natural language compliance queries via Bedrock, read-only SQL enforcement, SSE dashboard events (Phase 40) |
| Parallel CI/CD | goals/parallel_cicd.md | Git worktree task isolation, GitLab {{icdev: workflow}} tag routing, parallel workflow execution (Phase 41) |
| Framework Planning | goals/framework_planning.md | Language-specific build commands (Python/Java/Go/Rust/C#/TypeScript), 12 Leverage Points framework (Phase 42) |
| Multi-Agent Orchestration | goals/multi_agent_orchestration.md | Opus 4.6 multi-agent: DAG workflow, parallel execution, collaboration patterns, domain authority vetoes, agent memory |
| Security Categorization | goals/security_categorization.md | FIPS 199/200 categorization with SP 800-60 types, high watermark, CNSSI 1253, dynamic baseline |
| Agentic Generation | `goals/agentic_generation.md` | Generate mini-ICDEV clone apps with GOTCHA/ATLAS |
| SaaS Multi-Tenancy | goals/saas_multi_tenancy.md | Multi-tenant SaaS: API gateway (REST+MCP SSE), per-tenant DB, 3 auth methods, subscription tiers, artifact delivery, tenant portal, Helm on-prem |
| Marketplace | goals/marketplace.md | Federated GOTCHA asset marketplace: publish, install, search, review, sync skills/goals/hardprompts/context/args/compliance across tenant orgs with 7-gate security pipeline (Phase 22) |
| Universal Compliance | goals/universal_compliance.md | Universal Compliance Platform: 10 data categories, dual-hub crosswalk (NIST+ISO), 6 Wave 1 frameworks, auto-detection, multi-regime assessment (Phase 23) |
| DevSecOps Workflow | goals/devsecops_workflow.md | DevSecOps profile management, maturity assessment, pipeline security generation, policy-as-code (Kyverno/OPA), image signing & attestation (Phase 24) |
| Zero Trust Architecture | goals/zero_trust_architecture.md | ZTA 7-pillar maturity scoring, NIST SP 800-207 compliance, service mesh generation (Istio/Linkerd), network segmentation, PDP/PEP config, cATO posture monitoring (Phase 25) |
| MOSA Workflow | goals/mosa_workflow.md | DoD MOSA (10 U.S.C. §4401): MOSA assessment, modularity analysis, ICD/TSP generation, code enforcement, intake auto-detection, cATO evidence (Phase 26) |
| CLI Capabilities | goals/cli_capabilities.md | Optional Claude CLI features: CI/CD automation, parallel agents, container execution, scripted intake — 4 independent toggles with tenant ceiling and cost controls (Phase 27) |
| Remote Command Gateway | goals/remote_command_gateway.md | Remote Command Gateway: messaging channel integration, 8-gate security chain, IL-aware response filtering, user binding, air-gapped/connected mode (Phase 28) |
| Cross-Language Translation | goals/cross_language_translation.md | LLM-assisted cross-language code translation: 5-phase hybrid pipeline (Extract→Type-Check→Translate→Assemble→Validate+Repair), 30 directional pairs, pass@k candidates, mock-and-continue, compliance bridge, Dashboard+Portal visibility (Phase 43, D242-D256) |
| OWASP Agentic Security | goals/owasp_agentic_security.md | OWASP Agentic AI security: 8-gap implementation — behavioral drift detection, tool chain validation, output content safety, formal threat model, dynamic trust scoring, MCP per-tool RBAC, behavioral red teaming, compliance assessor (Phase 45, D257-D264) |
| Agentic Threat Model | goals/agentic_threat_model.md | Formal STRIDE + OWASP T1-T17 agentic threat model with trust boundaries, MCP server threat surface, residual risk analysis (Phase 45, D263) |
| Observability & XAI | goals/observability_traceability_xai.md | Distributed tracing (OTel+SQLite), W3C PROV-AGENT provenance, AgentSHAP tool attribution, XAI compliance assessor (10 checks), dashboard pages (/traces, /provenance, /xai), MCP server (Phase 46, D280-D290) |
| AI Transparency | goals/ai_transparency.md | AI Transparency: OMB M-25-21/M-26-04, NIST AI 600-1, GAO-21-519SP — model cards, system cards, AI inventory, confabulation detection, fairness assessment, cross-framework audit (Phase 48, D307-D315) |
| AI Accountability | goals/ai_accountability.md | AI Accountability: oversight plans, CAIO designation, appeals, incident response, ethics reviews, reassessment scheduling, cross-framework audit, assessor fixes (Phase 49, D316-D321) |
| AI Governance Intake | goals/ai_governance_intake.md | AI governance integration: RICOAS intake detection (6 pillars), 7th readiness dimension, chat extension advisory, governance sidebar, auto-trigger for federal agencies (Phase 50, D322-D330) |
| Code Intelligence | goals/code_intelligence.md | Code quality self-analysis: AST metrics (cyclomatic/cognitive complexity, nesting, params), 5 smell detectors, maintainability scoring, runtime feedback (test-to-source), production audit (5 CODE checks), Innovation Engine integration, pattern-based TDD learning (Phase 52, D331-D337) |
| Creative Engine | goals/creative_engine.md | Customer-centric feature opportunity discovery: auto-discover competitors, scan review sites/forums/GitHub, extract pain points, 3-dimension scoring, trend detection, template-based spec generation, Innovation Engine bridge (Phase 58, D351-D360) |
| GovCon Intelligence | goals/govcon_intelligence.md | GovCon capture-to-delivery flywheel: SAM.gov scanning, requirement mining, ICDEV capability mapping, gap analysis, two-tier LLM response drafting, compliance auto-population, competitive intelligence, proposal lifecycle integration (Phase 59, D361-D374) |
