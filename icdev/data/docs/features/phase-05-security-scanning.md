# Phase 5 — Security Scanning

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 5 |
| Title | Comprehensive Security Scanning Pipeline |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 1 (GOTCHA Framework Foundation), Phase 3 (TDD/BDD Testing Framework) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

A single undetected vulnerability in a government system can be a national security incident. No single security scanning tool catches everything -- static analysis finds code-level flaws but misses dependency vulnerabilities; dependency auditors find known CVEs but miss hardcoded secrets; secret detectors find credentials but miss container misconfigurations. Defense-in-depth requires layering multiple complementary scanners and aggregating their results.

Traditional security scanning is performed as a late-stage gate, often as a checkbox exercise before deployment. By this point, the cost of remediation is high, schedules are compressed, and pressure mounts to accept risk rather than fix findings. In government environments, this pattern leads to POAMs full of overdue items and eventual ATO revocation.

ICDEV integrates a comprehensive 4-scanner security pipeline that runs early and often: SAST (static application security testing) via Bandit, dependency auditing via pip-audit, secret detection via detect-secrets, and container scanning via Trivy. Quality gates enforce zero tolerance for critical/high findings and detected secrets. Findings feed directly into POAM generation (Phase 4). The pipeline blocks deployment until all gates pass -- no exceptions without Authorizing Official written approval.

---

## 2. Goals

1. Run Static Application Security Testing (SAST) using Bandit (Python), eslint-security (JavaScript), SpotBugs (Java), gosec (Go), cargo-audit (Rust), SecurityCodeScan (C#)
2. Audit all dependencies (direct and transitive) against CVE databases using pip-audit, npm audit, OWASP Dependency Check, govulncheck, or dotnet list
3. Detect hardcoded secrets (API keys, passwords, private keys, tokens, connection strings) using detect-secrets with zero tolerance
4. Scan container images for OS-level and application-level vulnerabilities using Trivy
5. Enforce quality gates that block deployment: 0 critical/high vulnerabilities, 0 detected secrets, 0 CAT1 STIG findings
6. Generate a consolidated security report aggregating all scanner results
7. Feed all findings into the POAM generation pipeline for compliance traceability

---

## 3. Architecture

### 3.1 Security Scanning Pipeline

```
+--------+     +--------+     +--------+     +--------+
| SAST   |---->| Dep    |---->| Secret |---->|Container|
| Scanner|     | Audit  |     | Detect |     | Scanner |
| bandit |     |pip-audit|    |detect- |     | trivy   |
|        |     |        |     |secrets |     |         |
+---+----+     +---+----+     +---+----+     +---+----+
    |              |              |              |
    v              v              v              v
+-------+     +-------+     +-------+     +-------+
| Gate 1|     | Gate 2|     | Gate 3|     | Gate 4|
| 0 crit|     | 0 crit|     | 0 sec |     | 0 crit|
| 0 high|     | 0 high|     | rets  |     | 0 high|
+---+---+     +---+---+     +---+---+     +---+---+
    |              |              |              |
    +------+-------+------+------+------+-------+
           |              |             |
           v              v             v
       ALL PASS?      ANY FAIL?    CONSOLIDATED
       Proceed to     BLOCKED:     SECURITY
       compliance     Remediate    REPORT
       workflow       findings     (Step 6)
```

### 3.2 Quality Gates

| Gate | Threshold | Blocks Deployment |
|------|-----------|-------------------|
| Critical vulnerabilities (SAST) | 0 | YES |
| High vulnerabilities (SAST) | 0 | YES |
| Detected secrets | 0 | YES |
| CAT1 STIG findings | 0 | YES |
| High dependency vulnerabilities | 0 | YES |
| Medium dependency vulnerabilities | <= 5 with documented POAM | NO (with POAM) |
| Low findings | Unlimited | NO |

### 3.3 Scanner Coverage by Language

| Language | SAST | Dep Audit | Secret Detection | Container |
|----------|------|-----------|-----------------|-----------|
| Python | Bandit | pip-audit | detect-secrets | Trivy |
| Java | SpotBugs | OWASP Dependency Check | detect-secrets | Trivy |
| JavaScript/TS | eslint-security | npm audit | detect-secrets | Trivy |
| Go | gosec | govulncheck | detect-secrets | Trivy |
| Rust | cargo-audit | cargo-audit | detect-secrets | Trivy |
| C# | SecurityCodeScan | dotnet list | detect-secrets | Trivy |

---

## 4. Requirements

### 4.1 SAST

#### REQ-05-001: Static Analysis Execution
The system SHALL run language-appropriate SAST scanners against all source code in the project directory.

#### REQ-05-002: Finding Classification
SAST findings SHALL be classified by severity (CRITICAL, HIGH, MEDIUM, LOW) and include file path, line number, and CWE reference.

#### REQ-05-003: False Positive Management
The system SHALL support `# nosec` comment annotations for documented false positives with justification recorded in the POAM.

### 4.2 Dependency Audit

#### REQ-05-004: Transitive Dependency Scanning
The dependency auditor SHALL scan all direct and transitive dependencies, not just top-level declarations.

#### REQ-05-005: CVE Correlation
Each vulnerability finding SHALL include the CVE identifier, affected package version, and recommended fix version.

#### REQ-05-006: Unfixable Vulnerabilities
When a vulnerability has no available fix, the system SHALL document it in the POAM with "vendor dependency" status.

### 4.3 Secret Detection

#### REQ-05-007: Zero Tolerance
The secret detection gate SHALL enforce zero detected secrets. Any detection is a gate failure.

#### REQ-05-008: Secret Types
The detector SHALL identify API keys, passwords, private keys, tokens, and connection strings across all source files.

#### REQ-05-009: Immediate Rotation
When secrets are detected, the system SHALL recommend immediate credential rotation and provide remediation steps (remove from code, add to .gitignore, verify git history).

#### REQ-05-010: Baseline Management
The system SHALL support a `.secrets.baseline` file for documenting allowed patterns with justification.

### 4.4 Container Scanning

#### REQ-05-011: OS and Application Scanning
The container scanner SHALL check both OS-level packages and application-level dependencies within the container image.

#### REQ-05-012: Misconfiguration Detection
The container scanner SHALL check for misconfigurations: running as root, secrets in image, writable filesystem.

#### REQ-05-013: Skip Conditions
Container scanning SHALL be skipped (marked N/A) when no Dockerfile exists or Docker is not running, without failing the overall pipeline.

### 4.5 Reporting and Integration

#### REQ-05-014: Consolidated Report
The system SHALL generate a consolidated security report aggregating findings from all scanners with severity rollups, top risks, and remediation recommendations.

#### REQ-05-015: POAM Feed
All findings SHALL be available for import by the POAM generator (Phase 4) with severity-appropriate timelines.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `security_findings` | Aggregated findings from all scanners with severity, CVE, CWE, file, line |
| `audit_trail` | Append-only log of scan executions and gate results |
| `sbom_components` | Component inventory for dependency correlation |
| `prompt_injection_log` | Prompt injection detection events (Phase 37 extension) |
| `ai_telemetry` | AI usage telemetry with SHA-256 hashed prompts/responses |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/security/sast_runner.py` | Run static application security testing (Bandit, eslint-security, etc.) |
| `tools/security/dependency_auditor.py` | Audit dependencies against CVE databases (pip-audit, npm audit, etc.) |
| `tools/security/secret_detector.py` | Detect hardcoded secrets in source code (detect-secrets) |
| `tools/security/container_scanner.py` | Scan container images for vulnerabilities (Trivy) |
| `tools/security/code_pattern_scanner.py` | Detect dangerous code patterns across 6 languages |
| `tools/audit/audit_logger.py` | Log scan events and gate results to audit trail |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D5 | CUI markings applied at generation time | Security reports carry classification markings from creation |
| D6 | Audit trail is append-only/immutable | Scan results cannot be retroactively modified; NIST AU compliance |
| D278 | Dangerous pattern detection: unified scanner across 6 languages | Callable from marketplace, translation, child app generation, and security scanning; declarative YAML patterns |

---

## 8. Security Gate

**Security Scanning Gate (Code Review & Merge):**
- 0 critical/high SAST findings
- 0 critical/high dependency vulnerabilities
- 0 detected secrets
- 0 critical container vulnerabilities (or N/A if no containers)
- No exceptions without Authorizing Official written approval

**Code Patterns Gate:**
- max_critical = 0
- max_high = 0
- max_medium = 10

**Scan Frequency:**
- Every commit: incremental SAST on changed files
- Every merge request: full pipeline (all 4 scanners)
- Weekly: full deep scan
- Emergency: zero-day CVE response triggers immediate scan

---

## 9. Commands

```bash
# Individual scanners
python tools/security/sast_runner.py --project-dir "/path"
python tools/security/dependency_auditor.py --project-dir "/path"
python tools/security/secret_detector.py --project-dir "/path"
python tools/security/container_scanner.py --image "my-image:latest"

# Dangerous code pattern detection
python tools/security/code_pattern_scanner.py --project-dir "/path" --json
python tools/security/code_pattern_scanner.py --project-dir "/path" --gate --json

# Security skill (runs full pipeline)
/icdev-secure    # Run security scanning (SAST, deps, secrets, container)

# Audit logging
python tools/audit/audit_logger.py --event-type "security_scan_complete" \
  --actor "security-agent" --action "Scan completed" --project-id "proj-123"
```
