# Security Operations Guide

## Overview

ICDEV implements defense-in-depth security across the full SDLC. Security is enforced through deterministic scanning tools, compliance assessors, blocking security gates, AI-specific threat defense, Zero Trust Architecture, and self-healing remediation. All security events are recorded in an append-only audit trail (NIST AU compliance).

---

## Security Scanning Tools

### SAST (Static Application Security Testing)

```bash
python tools/security/sast_runner.py --project-dir "/path/to/project"
```

Runs language-appropriate SAST scanners:
- **Python:** Bandit (SQL injection, XSS, hardcoded secrets)
- **Java:** SpotBugs
- **JavaScript/TypeScript:** eslint-security
- **Go:** gosec
- **Rust:** cargo-audit
- **C#:** SecurityCodeScan

### Dependency Audit

```bash
python tools/security/dependency_auditor.py --project-dir "/path/to/project"
```

Scans dependencies for known vulnerabilities:
- **Python:** pip-audit
- **Java:** OWASP Dependency Check
- **JavaScript/TypeScript:** npm audit
- **Go:** govulncheck
- **Rust:** cargo-audit
- **C#:** dotnet list (vulnerable packages)

### Secret Detection

```bash
python tools/security/secret_detector.py --project-dir "/path/to/project"
```

Detects hardcoded secrets, API keys, tokens, and credentials in source code. Uses detect-secrets with custom ICDEV plugins.

### Container Scanning

```bash
python tools/security/container_scanner.py --image "my-image:latest"
```

Scans container images for OS-level vulnerabilities, misconfigurations, and compliance violations.

---

## Dangerous Pattern Detection

Unified scanner across 6 languages for detecting dangerous code patterns:

```bash
python tools/security/code_pattern_scanner.py --project-dir "/path/to/project" --json
```

Detected patterns by language:

| Language | Critical Patterns |
|----------|------------------|
| Python | `eval()`, `exec()`, `os.system()`, `pickle.loads()`, `subprocess.call(shell=True)` |
| Java | `Runtime.exec()`, deserialization, SQL string concatenation |
| Go | `unsafe` package, `exec.Command` with user input |
| Rust | `unsafe` blocks, `std::process::Command` with user input |
| C# | `Process.Start()`, `SqlCommand` with concatenation |
| TypeScript | `eval()`, `Function()` constructor, `innerHTML` assignment |
| Universal | Hardcoded IPs, base64-encoded strings, known backdoor patterns |

Configuration: `args/code_pattern_config.yaml`

Security gate thresholds: `max_critical=0`, `max_high=0`, `max_medium=10`

---

## AI Security (Phase 37)

### Prompt Injection Detection

Five detection categories protect against adversarial prompt manipulation:

1. **Role Hijacking** - Attempts to override system role ("You are now...")
2. **Delimiter Attacks** - Injection via markdown, XML, or code block delimiters
3. **Instruction Injection** - Direct instruction override ("Ignore previous instructions")
4. **Data Exfiltration** - Attempts to extract system prompts or training data
5. **Encoded Payloads** - Base64, hex, or unicode-encoded attack payloads

```bash
# Detect prompt injection in text
python tools/security/prompt_injection_detector.py --text "ignore previous instructions" --json

# Scan a file
python tools/security/prompt_injection_detector.py --file /path/to/file --json

# Gate evaluation (blocks on detection)
python tools/security/prompt_injection_detector.py --project-dir /path --gate --json
```

### AI Telemetry

All AI interactions are logged with SHA-256 hashed prompts and responses (privacy-preserving audit per D216):

```bash
# AI usage summary
python tools/security/ai_telemetry_logger.py --summary --json

# Anomaly detection (24-hour window)
python tools/security/ai_telemetry_logger.py --anomalies --window-hours 24 --json

# Behavioral drift detection
python tools/security/ai_telemetry_logger.py --drift --json
python tools/security/ai_telemetry_logger.py --drift --agent-id "builder-agent" --json
```

### MITRE ATLAS Red Teaming

Red teaming is opt-in only (D219). Never auto-executes adversarial tests.

```bash
# Run all 6 ATLAS red team techniques
python tools/security/atlas_red_team.py --project-id "proj-123" --json

# Test specific technique
python tools/security/atlas_red_team.py --project-id "proj-123" --technique AML.T0051 --json

# Run behavioral red team tests (OWASP Agentic)
python tools/security/atlas_red_team.py --behavioral --json
python tools/security/atlas_red_team.py --behavioral --brt-technique BRT-001 --json
```

### AI Bill of Materials (AI BOM)

Tracks all AI/ML components, models, and training data lineage:

```bash
# Generate AI BOM
python tools/security/ai_bom_generator.py --project-id "proj-123" --project-dir . --json

# AI BOM gate check
python tools/security/ai_bom_generator.py --project-id "proj-123" --gate
```

---

## OWASP Agentic AI Security (Phase 45)

### Behavioral Drift Detection

Monitors agent behavior for statistical anomalies using z-score baseline comparison:

```bash
python tools/security/ai_telemetry_logger.py --drift --json
python tools/security/ai_telemetry_logger.py --drift --agent-id "builder-agent" --json
```

Configuration: 7-day baseline window, z-score threshold in `args/owasp_agentic_config.yaml`.

### Tool Chain Validation

Validates that agent tool invocation sequences do not match dangerous patterns:

```bash
# List tool chain rules
python tools/security/tool_chain_validator.py --rules --json

# Gate check
python tools/security/tool_chain_validator.py --gate --project-id "proj-123" --json
```

Default rules (sliding-window sequence matching):
1. Secrets access followed by external network call
2. File read followed by data exfiltration pattern
3. Privilege escalation followed by deployment
4. Rapid burst tool invocations (>20 in 60 seconds)

### Output Content Safety

Validates agent outputs for classification leaks and PII:

```bash
# Validate specific text
python tools/security/agent_output_validator.py --text "some output" --json

# Gate evaluation
python tools/security/agent_output_validator.py --gate --project-id "proj-123" --json
```

Detects: classification marking leaks, SSN patterns, credential patterns, private keys.

### Agent Trust Scoring

Dynamic trust scores with decay on violations and recovery on clean behavior:

```bash
# Compute trust score for an agent
python tools/security/agent_trust_scorer.py --score --agent-id "builder-agent" --json

# Check agent access authorization
python tools/security/agent_trust_scorer.py --check --agent-id "builder-agent" --json

# All agent trust scores
python tools/security/agent_trust_scorer.py --all --json

# Gate evaluation
python tools/security/agent_trust_scorer.py --gate --project-id "proj-123" --json
```

Trust levels:
- **Trusted** (>= 0.70): Full access
- **Probationary** (0.30 - 0.69): Restricted access, additional logging
- **Untrusted** (< 0.30): Blocked from sensitive operations

### MCP Per-Tool RBAC

Deny-first role-based access control for MCP tool invocations:

```bash
# Check if a role can use a tool
python tools/security/mcp_tool_authorizer.py --check --role developer --tool scaffold --json

# List permissions for a role
python tools/security/mcp_tool_authorizer.py --list --role pm --json

# Validate full RBAC configuration
python tools/security/mcp_tool_authorizer.py --validate --json
```

Five roles: admin, pm, developer, isso, co. Configuration in `args/owasp_agentic_config.yaml`.

### OWASP Agentic Assessment

```bash
# Full assessment (17 automated checks)
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --json

# Gate evaluation
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --gate
```

---

## Compliance Assessors

All assessors follow the BaseAssessor ABC pattern (D116) with crosswalk integration, gate evaluation, and CLI support.

### Core Frameworks

```bash
# NIST 800-53 Rev 5 control mapping
python tools/compliance/control_mapper.py --activity "code.commit" --project-id "proj-123"

# NIST control lookup
python tools/compliance/nist_lookup.py --control "AC-2"

# Crosswalk query (one control, all frameworks)
python tools/compliance/crosswalk_engine.py --control AC-2

# Coverage across all applicable frameworks
python tools/compliance/crosswalk_engine.py --project-id "proj-123" --coverage
```

### Federal Frameworks

```bash
# FedRAMP (Moderate or High baseline)
python tools/compliance/fedramp_assessor.py --project-id "proj-123" --baseline moderate
python tools/compliance/fedramp_report_generator.py --project-id "proj-123"

# CMMC Level 2/3
python tools/compliance/cmmc_assessor.py --project-id "proj-123" --level 2
python tools/compliance/cmmc_report_generator.py --project-id "proj-123"

# OSCAL generation
python tools/compliance/oscal_generator.py --project-id "proj-123" --artifact ssp

# eMASS sync
python tools/compliance/emass/emass_sync.py --project-id "proj-123" --mode hybrid

# cATO monitoring
python tools/compliance/cato_monitor.py --project-id "proj-123" --check-freshness
```

### Industry Frameworks

```bash
# CJIS Security Policy
python tools/compliance/cjis_assessor.py --project-id "proj-123" --json

# HIPAA Security Rule
python tools/compliance/hipaa_assessor.py --project-id "proj-123" --json

# HITRUST CSF v11
python tools/compliance/hitrust_assessor.py --project-id "proj-123" --json

# SOC 2 Type II
python tools/compliance/soc2_assessor.py --project-id "proj-123" --json

# PCI DSS v4.0
python tools/compliance/pci_dss_assessor.py --project-id "proj-123" --json

# ISO/IEC 27001:2022
python tools/compliance/iso27001_assessor.py --project-id "proj-123" --json
```

### AI-Specific Frameworks

```bash
# MITRE ATLAS v5.4.0
python tools/compliance/atlas_assessor.py --project-id "proj-123" --json
python tools/compliance/atlas_report_generator.py --project-id "proj-123" --json

# OWASP LLM Top 10
python tools/compliance/owasp_llm_assessor.py --project-id "proj-123" --json

# NIST AI RMF 1.0 (4 functions, 12 subcategories)
python tools/compliance/nist_ai_rmf_assessor.py --project-id "proj-123" --json

# ISO/IEC 42001:2023
python tools/compliance/iso42001_assessor.py --project-id "proj-123" --json

# OWASP Agentic AI
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --json

# XAI (Explainable AI)
python tools/compliance/xai_assessor.py --project-id "proj-123" --json
```

### Security Categorization

```bash
# FIPS 199 categorization
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --categorize --json

# FIPS 200 validation (17 areas)
python tools/compliance/fips200_validator.py --project-id "proj-123" --json

# Multi-regime assessment (all applicable frameworks)
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --json
```

---

## Security Gates

All security gates are blocking. Deployments, merges, and releases cannot proceed until gate conditions are satisfied.

### Code Review Gate

| Condition | Threshold |
|-----------|-----------|
| Approvals | >= 1 required |
| Comments | All resolved |
| SAST | Clean (0 findings) |
| Secrets | None detected |
| CUI markings | Present on all artifacts |

### Merge Gate

| Condition | Threshold |
|-----------|-----------|
| Tests | All passing |
| Coverage | >= 80% |
| STIG CAT1 | 0 findings |
| Critical vulnerabilities | 0 |
| SBOM | Current |

### Deploy Gate

| Condition | Threshold |
|-----------|-----------|
| Staging tests | All passing |
| Compliance artifacts | Current |
| Change request | Approved |
| Rollback plan | Documented |

### FedRAMP Gate

| Condition | Threshold |
|-----------|-----------|
| High-priority controls | 0 other_than_satisfied |
| Encryption | FIPS 140-2 required |

### CMMC Gate

| Condition | Threshold |
|-----------|-----------|
| Level 2 practices | 0 not_met |
| Evidence currency | Within 90 days |

### cATO Gate

| Condition | Threshold |
|-----------|-----------|
| Critical control evidence | 0 expired |
| Readiness | >= 50% |

### AI Security Gate

| Condition | Threshold |
|-----------|-----------|
| Prompt injection defense | Active |
| AI telemetry | Enabled |
| AI BOM | Present |
| ATLAS coverage | >= 80% |
| Agent permissions | Configured |

### OWASP Agentic Gate

| Condition | Threshold |
|-----------|-----------|
| Agent trust | No agent below untrusted threshold |
| Tool chain violations | 0 critical |
| Output classification leaks | 0 critical |
| Behavioral drift | No critical drift |
| MCP authorization | Configured |
| Min trust score | >= 0.30 |

### ZTA Gate

| Condition | Threshold |
|-----------|-----------|
| ZTA maturity | >= Advanced (0.34) for IL4+ |
| mTLS | Enforced when service mesh active |
| NetworkPolicy | Default-deny required |
| Pillar scores | No pillar at 0.0 |

### Additional Gates

- **DES Gate:** 0 non_compliant on critical DoDI 5000.87 requirements
- **Migration Gate:** ATO coverage >= 95% maintained during modernization
- **RICOAS Gate:** Readiness >= 0.7, 0 unresolved critical gaps
- **Supply Chain Gate:** 0 critical SCRM risks, 0 expired ISAs, 0 overdue critical CVE SLAs, 0 Section 889 prohibited vendors
- **FIPS 199 Gate:** Categorization required for ATO projects
- **FIPS 200 Gate:** 0 not_satisfied areas, all 17 areas assessed, coverage >= 80%
- **Multi-Regime Gate:** All applicable frameworks must pass individual gates
- **HIPAA Gate:** 0 not_satisfied on Administrative/Technical Safeguards, FIPS 140-2 for PHI
- **PCI DSS Gate:** 0 not_satisfied on Requirements 3-4, 6, 10
- **CJIS Gate:** 0 not_satisfied on Policy Areas 4, 5, 6, 10
- **DevSecOps Gate:** 0 critical policy violations, 0 missing attestations, 0 unresolved critical SAST, 0 detected secrets
- **MOSA Gate:** 0 external interfaces without ICD, 0 circular dependencies, modularity >= 0.6
- **Translation Gate:** Syntax errors, API surface < 90%, compliance coverage < 95%, secrets detected
- **Marketplace Gates:** 7-gate publish pipeline + Gate 8 (prompt injection) + Gate 9 (behavioral sandbox)
- **Observability/XAI Gate:** Tracing active, provenance graph populated, XAI assessment completed

Full gate configuration: `args/security_gates.yaml`

---

## Zero Trust Architecture

### ZTA 7-Pillar Maturity Scoring

Based on the DoD Zero Trust Strategy (Traditional, Advanced, Optimal):

```bash
# Score all 7 pillars
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --all --json

# Score individual pillar
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --pillar user_identity --json

# Maturity trend over time
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --trend --json
```

Seven pillars: User Identity, Device, Network, Application/Workload, Data, Visibility/Analytics, Automation/Orchestration.

### NIST 800-207 Assessment

```bash
python tools/compliance/nist_800_207_assessor.py --project-id "proj-123" --json
python tools/compliance/nist_800_207_assessor.py --project-id "proj-123" --gate
```

### Service Mesh Generation

```bash
# Istio service mesh
python tools/devsecops/service_mesh_generator.py --project-id "proj-123" --mesh istio --json

# Linkerd service mesh
python tools/devsecops/service_mesh_generator.py --project-id "proj-123" --mesh linkerd --json
```

### Network Segmentation

```bash
# Namespace isolation
python tools/devsecops/network_segmentation_generator.py \
  --project-path /path --namespaces "app,data" --json

# Microsegmentation
python tools/devsecops/network_segmentation_generator.py \
  --project-path /path --services "api,db" --json
```

### PDP/PEP Configuration

ICDEV generates PEP (Policy Enforcement Point) configurations but does not implement PDP (Policy Decision Point) itself (D124). Supported PDP references:

```bash
# DISA ICAM PDP config
python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --pdp-type disa_icam --json

# Zscaler PDP with Istio mesh
python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --pdp-type zscaler --mesh istio --json
```

---

## Secrets Management

Secrets are managed through a CSP-abstracted interface (D225). No secrets in code or config files.

| CSP | Service |
|-----|---------|
| AWS | AWS Secrets Manager |
| Azure | Azure Key Vault |
| GCP | GCP Secret Manager |
| OCI | OCI Vault |
| IBM | IBM Cloud Secrets Manager |
| Local | `.env` file (development only) |

All secret references in K8s use `ExternalSecret` or CSP-native secret injection. The `tools/cloud/provider_factory.py` resolves the appropriate secrets provider based on `args/cloud_config.yaml`.

---

## Self-Healing Security

The self-healing system automatically remediates known security issues based on confidence scoring:

| Confidence | Action |
|------------|--------|
| >= 0.7 | Auto-remediate (no human approval) |
| 0.3 - 0.7 | Suggest fix, require human approval |
| < 0.3 | Escalate with full context |

Rate limits:
- Maximum 5 auto-heals per hour
- 10-minute cooldown between same-pattern heals

```bash
# Analyze a failure
python tools/knowledge/self_heal_analyzer.py --failure-id "fail-123"

# Get recommendations
python tools/knowledge/recommendation_engine.py --project-id "proj-123"

# Pattern detection
python tools/knowledge/pattern_detector.py --log-data "/path/to/logs"
```

---

## Production Audit

30 checks across 6 categories for production readiness:

```bash
# Full audit with streaming output
python tools/testing/production_audit.py --human --stream

# JSON output
python tools/testing/production_audit.py --json

# Single category
python tools/testing/production_audit.py --category security --json

# Multiple categories
python tools/testing/production_audit.py --category security,compliance --json

# Gate evaluation (exit code 0=pass, 1=fail)
python tools/testing/production_audit.py --gate --json
```

### Audit Categories

| Category | Checks |
|----------|--------|
| Platform | Database health, migration status, backup recency, disk usage, service health |
| Security | SAST findings, secret detection, dependency vulnerabilities, container scan, prompt injection defense |
| Compliance | NIST 800-53 coverage, FedRAMP status, CMMC status, CUI markings, SBOM currency |
| Integration | Agent health, A2A connectivity, MCP server status, external system sync |
| Performance | Response times, error rates, resource utilization, queue depth |
| Documentation | CLAUDE.md currency, goal coverage, tool manifest completeness |

---

## Production Remediation

Auto-fix audit blockers with a 3-tier confidence model:

```bash
# Auto-fix with streaming output
python tools/testing/production_remediate.py --human --stream

# Auto-fix all (JSON)
python tools/testing/production_remediate.py --auto --json

# Dry run (preview fixes without applying)
python tools/testing/production_remediate.py --dry-run --human --stream

# Fix a specific check
python tools/testing/production_remediate.py --check-id SEC-002 --auto

# Reuse latest audit results (skip re-audit)
python tools/testing/production_remediate.py --skip-audit --auto --json
```

### Confidence Tiers

| Confidence | Action | Examples |
|------------|--------|----------|
| >= 0.7 | Auto-fix applied | Missing CUI banners, outdated SBOM, missing .gitignore entries |
| 0.3 - 0.7 | Suggestion provided | Configuration changes, dependency upgrades with breaking changes |
| < 0.3 | Escalated to human | Architecture changes, compliance interpretation questions |

Remediation actions are recorded in the `remediation_audit_log` table (append-only). Verification re-runs confirm fixes were applied correctly.

---

## Incident Response

### CSSP Compliance (DI 8530.01)

```bash
# Incident response plan generation
python tools/compliance/incident_response_plan.py --project-id "proj-123"

# SIEM configuration
python tools/compliance/siem_config_generator.py --project-dir "/path" --targets splunk elk

# Evidence collection
python tools/compliance/cssp_evidence_collector.py --project-id "proj-123" --project-dir "/path"

# CSSP assessment
python tools/compliance/cssp_assessor.py --project-id "proj-123" --functional-area all
```

### Monitoring and Alerting

```bash
# Log analysis
python tools/monitor/log_analyzer.py --source elk --query "error"

# Health check
python tools/monitor/health_checker.py --target "http://service:8080/health"

# Heartbeat daemon (proactive monitoring)
python tools/monitor/heartbeat_daemon.py          # Continuous monitoring
python tools/monitor/heartbeat_daemon.py --once    # Single check pass
python tools/monitor/heartbeat_daemon.py --status --json  # Check status

# Auto-resolver (webhook-triggered)
python tools/monitor/auto_resolver.py --analyze --alert-file alert.json --json
python tools/monitor/auto_resolver.py --resolve --alert-file alert.json --json
```

---

## Observability and Traceability (Phase 46)

### Distributed Tracing

Dual-mode tracer: OTel (production with MLflow backend) or SQLite (air-gapped):

```bash
# Check active tracer
python -c "from tools.observability import get_tracer; print(type(get_tracer()).__name__)"
```

Auto-detection: `ICDEV_MLFLOW_TRACKING_URI` set triggers OTel mode; otherwise SQLite mode.

### Provenance

W3C PROV-AGENT standard for entity/activity lineage:

```bash
# Query provenance lineage
python tools/observability/provenance/prov_query.py --entity-id "<id>" --direction backward --json

# Export PROV-JSON
python tools/observability/provenance/prov_export.py --project-id "proj-123" --json
```

### AgentSHAP

Post-hoc tool attribution via Monte Carlo Shapley values:

```bash
# SHAP analysis on a specific trace
python tools/observability/shap/agent_shap.py --trace-id "<trace-id>" --iterations 1000 --json

# SHAP for last N traces
python tools/observability/shap/agent_shap.py --project-id "proj-123" --last-n 10 --json
```

### XAI Assessment

```bash
# XAI compliance assessment (10 checks)
python tools/compliance/xai_assessor.py --project-id "proj-123" --json

# XAI gate evaluation
python tools/compliance/xai_assessor.py --project-id "proj-123" --gate
```

Configuration: `args/observability_tracing_config.yaml`

Content tracing policy: hash-only by default. Plaintext content tracing requires `ICDEV_CONTENT_TRACING_ENABLED=true`. CUI environments must never enable plaintext content tracing without explicit approval.

---

## Related Configuration

| File | Purpose |
|------|---------|
| `args/security_gates.yaml` | All gate definitions and blocking thresholds |
| `args/owasp_agentic_config.yaml` | Behavioral drift, tool chain rules, trust scoring, MCP RBAC |
| `args/code_pattern_config.yaml` | Dangerous pattern definitions per language |
| `args/zta_config.yaml` | ZTA 7-pillar model, service mesh, policy engines |
| `args/devsecops_config.yaml` | DevSecOps profile schema, maturity levels |
| `args/observability_tracing_config.yaml` | Tracer config, sampling, retention, content policy |
| `args/resilience_config.yaml` | Circuit breaker and retry settings |
| `args/monitoring_config.yaml` | ELK/Splunk/Prometheus endpoints, SLA targets |
| `args/cloud_config.yaml` | CSP selection (determines secrets provider) |
