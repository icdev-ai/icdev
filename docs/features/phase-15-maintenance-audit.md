# Phase 15 — Maintenance Audit

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 15 |
| Title | Maintenance Audit |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 10 (Security Scanning), Phase 11 (Compliance Workflow) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Outdated dependencies are the number one attack vector in modern software systems. Government and DoD applications face strict compliance requirements under NIST 800-53 SI-2 (Flaw Remediation), SA-22 (Unsupported System Components), and CM-3 (Configuration Change Control). Without continuous dependency monitoring, projects accumulate technical debt, fall out of compliance, and become vulnerable to known CVEs with published exploits.

Manual dependency auditing is error-prone and does not scale across multi-language projects. Teams often discover vulnerabilities only during periodic security reviews, by which time the exposure window has been dangerously long. CISA Secure by Design Commitment 4 mandates timely security patch application, yet many organizations lack automated enforcement of remediation SLAs.

ICDEV needs an automated maintenance audit system that continuously inventories dependencies across all six supported languages, checks for known vulnerabilities against advisory databases, enforces remediation SLAs by severity, computes a maintenance health score, and auto-remediates low-risk issues while escalating critical findings for human review.

---

## 2. Goals

1. Continuously inventory all direct and transitive dependencies across Python, Java, JavaScript/TypeScript, Go, Rust, and C# projects
2. Check dependencies against vulnerability advisory databases (NVD, OSV, language-native advisories) and map findings to CVE IDs with CVSS scores
3. Enforce severity-based remediation SLAs: critical (48h), high (7d), medium (30d), low (90d)
4. Compute a deterministic maintenance health score (0-100) that feeds deployment gates
5. Auto-remediate medium and low severity vulnerabilities with test verification and rollback capability
6. Generate CUI-marked audit reports with trend analysis across audit runs
7. Feed results into SbD assessment (SbD-05 patch cadence, SbD-22 vulnerability scanning) and deployment gates
8. Support air-gapped environments via offline mode with local advisory database snapshots

---

## 3. Architecture

```
+------------------+     +---------------------+     +--------------------+
| Dependency       |---->| Vulnerability       |---->| Maintenance        |
| Scanner          |     | Checker             |     | Auditor            |
| (per-language)   |     | (CVE lookup + SLA)  |     | (score + report)   |
+------------------+     +---------------------+     +--------------------+
                                                            |
                                                            v
                                                     +--------------------+
                                                     | Remediation        |
                                                     | Engine             |
                                                     | (auto-fix + test)  |
                                                     +--------------------+
                                                            |
                                                            v
                                                     +--------------------+
                                                     | Verify + Audit     |
                                                     | (re-scan + log)    |
                                                     +--------------------+
```

The maintenance audit workflow is a 6-step pipeline:

1. **Scan** -- Inventory all dependencies across detected languages using language-native manifest files (requirements.txt, package.json, pom.xml, go.mod, Cargo.toml, .csproj)
2. **Check** -- Run vulnerability audit tools (pip-audit, npm audit, cargo-audit, etc.) and map to SLA deadlines
3. **Audit** -- Compute maintenance score, evaluate gate, generate CUI-marked report with trend analysis
4. **Remediate** -- Auto-fix eligible vulnerabilities (medium/low with fix available), create remediation branches, run tests
5. **Verify** -- Re-run security scan and test suite to confirm fixes do not introduce regressions
6. **Log** -- Record all actions in the append-only audit trail (NIST AU compliance)

---

## 4. Requirements

### 4.1 Dependency Scanning

#### REQ-15-001: Multi-Language Dependency Inventory
The system SHALL inventory all direct dependencies across Python, Java, JavaScript/TypeScript, Go, Rust, and C# by parsing their respective manifest files.

#### REQ-15-002: Staleness Classification
The system SHALL classify each dependency's staleness as: current (0d), minor (1-30d behind latest), moderate (31-90d), major (91-180d), or critical (>180d behind latest).

#### REQ-15-003: Air-Gapped Scanning
The system SHALL support an `--offline` flag that inventories dependencies from manifest files without querying remote registries, setting staleness to -1 (unknown).

### 4.2 Vulnerability Checking

#### REQ-15-004: CVE Mapping
The system SHALL map discovered vulnerabilities to CVE IDs, CVSS scores, affected version ranges, and fix availability status (fix_available, no_fix, workaround).

#### REQ-15-005: SLA Assignment
The system SHALL assign remediation deadlines based on severity: critical (48 hours), high (7 days), medium (30 days), low (90 days).

### 4.3 Scoring and Reporting

#### REQ-15-006: Maintenance Score
The system SHALL compute a deterministic maintenance score (0-100) using the formula: start at 100, deduct -20/critical SLA overdue, -10/high, -5/medium, -2/low, -3/critical staleness dep, -1/major staleness dep, floor at 0.

#### REQ-15-007: Gate Evaluation
The system SHALL evaluate a deployment gate: PASS (score >= 80), WARN (50-79, non-blocking), FAIL (< 50, blocks deployment).

#### REQ-15-008: CUI-Marked Reports
The system SHALL generate markdown audit reports with CUI // SP-CTI banners at `reports/<project>/maintenance_audit_YYYY-MM-DD.md`.

### 4.4 Remediation

#### REQ-15-009: Auto-Remediation Rules
The system SHALL auto-remediate medium and low severity vulnerabilities with available fixes. Critical and high severity SHALL require manual approval.

#### REQ-15-010: Dry-Run Mode
The system SHALL support a `--dry-run` flag that previews all remediation changes without modifying any files.

#### REQ-15-011: Test Verification
The system SHALL run the project test suite after applying remediation changes. If tests fail, the system SHALL rollback changes and flag for manual review.

#### REQ-15-012: EOL Detection
The system SHALL flag dependencies with no maintainer activity exceeding 1 year as unsupported per NIST SA-22 and recommend replacements.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `project_dependencies` | Dependency inventory: name, version, latest_version, staleness_days, language, manifest_file |
| `dependency_vulnerabilities` | CVE findings: cve_id, cvss_score, severity, affected_versions, fix_available, sla_deadline, status |
| `maintenance_audits` | Audit history: project_id, score, sla_compliance_pct, trend_json, report_path, timestamp |
| `remediation_actions` | Fix records: vulnerability_id, action_type, branch_name, test_result, rollback_log, timestamp |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/maintenance/dependency_scanner.py` | Multi-language dependency inventory with staleness calculation |
| `tools/maintenance/vulnerability_checker.py` | CVE lookup, CVSS scoring, SLA deadline assignment |
| `tools/maintenance/maintenance_auditor.py` | Orchestrates full audit: score computation, trend analysis, CUI report generation |
| `tools/maintenance/remediation_engine.py` | Auto-fix eligible vulnerabilities, branch creation, test verification, rollback |
| `tools/mcp/maintenance_server.py` | MCP server exposing scan_dependencies, check_vulnerabilities, run_maintenance_audit, remediate tools |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D13 | Python `ast` for Python analysis; regex-based parsing for Java/C# | Air-gap safe, zero external dependencies |
| D6 | Audit trail is append-only/immutable | NIST 800-53 AU compliance; no UPDATE/DELETE on audit records |
| D66 | Provider abstraction (ABC + implementations) per language audit tool | Each language gets its own scanner chain (pip-audit, npm audit, cargo-audit, etc.) |

---

## 8. Security Gate

**Maintenance Audit Gate:**
- Score < 50 blocks deployment (FAIL)
- 0 overdue critical SLA vulnerabilities permitted at deploy time
- 0 overdue high SLA vulnerabilities permitted at deploy time
- EOL dependencies flagged per NIST SA-22 must have documented risk acceptance or replacement plan
- SBOM must be regenerated after any remediation

---

## 9. Commands

```bash
# Scan all dependencies
python tools/maintenance/dependency_scanner.py --project-id "proj-123"

# Check vulnerabilities against advisory databases
python tools/maintenance/vulnerability_checker.py --project-id "proj-123"

# Run full maintenance audit (score + report)
python tools/maintenance/maintenance_auditor.py --project-id "proj-123"
python tools/maintenance/maintenance_auditor.py --project-id "proj-123" --human

# Preview remediation changes (dry-run)
python tools/maintenance/remediation_engine.py --project-id "proj-123" --dry-run

# Auto-fix eligible vulnerabilities
python tools/maintenance/remediation_engine.py --project-id "proj-123" --auto

# Verify post-remediation
python tools/security/dependency_auditor.py --project-dir "/path/to/project"

# Log to audit trail
python tools/audit/audit_logger.py --event-type "maintenance.audit" --actor "maintenance-agent" --action "Maintenance audit complete" --project-id "proj-123"
```
