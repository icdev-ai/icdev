# Goal: Maintenance Audit Workflow

## Description
Continuously assess and remediate project dependencies to maintain security, compliance, and governance posture. This workflow detects outdated dependencies, checks for known vulnerabilities, enforces remediation SLAs, and auto-implements fixes.

**Standards:**
- NIST 800-53 SI-2 (Flaw Remediation)
- NIST 800-53 SA-22 (Unsupported System Components)
- NIST 800-53 CM-3 (Configuration Change Control)
- CISA SbD Commitment 4 (Security Patches)

**Why this matters:** Outdated dependencies are the #1 attack vector. This workflow ensures continuous compliance and reduces exposure window through automated detection and remediation with SLA enforcement.

---

## Prerequisites
- [ ] Project initialized (`goals/init_project.md` completed)
- [ ] Project has dependency files (requirements.txt, package.json, pom.xml, go.mod, Cargo.toml, *.csproj)
- [ ] SBOM generated (`goals/compliance_workflow.md`)
- [ ] Security scans completed (`goals/security_scan.md`)

---

## Process

### Step 1: Scan Dependencies
**Tool:** `python tools/maintenance/dependency_scanner.py --project-id <id>`

Inventories all dependencies across detected languages. Checks package registries for latest versions. Calculates staleness (days behind latest).

**Outputs:**
- Dependency inventory stored in `project_dependencies` table
- Per-language summary (total deps, outdated count, avg staleness)
- Staleness flags: current (0d), minor (1-30d), moderate (31-90d), major (91-180d), critical (>180d)

**Air-gapped mode:** Use `--offline` flag. Dependencies inventoried from manifest files but latest versions unknown. Staleness set to -1 (unknown).

### Step 2: Check Vulnerabilities
**Tool:** `python tools/maintenance/vulnerability_checker.py --project-id <id>`

Runs language-native audit tools (pip-audit, npm audit, cargo-audit, etc.). Maps findings to SLA deadlines. Stores in `dependency_vulnerabilities` table.

**SLA Mapping:**
| Severity | Deadline | Auto-remediate |
|----------|----------|---------------|
| Critical | 48 hours | No (manual approval) |
| High | 7 days | No (manual approval) |
| Medium | 30 days | Yes |
| Low | 90 days | Yes |

**Outputs:**
- Vulnerability records with CVE IDs, CVSS scores, affected versions
- SLA deadline assignments based on severity
- Fix availability status (fix_available, no_fix, workaround)

### Step 3: Run Maintenance Audit
**Tool:** `python tools/maintenance/maintenance_auditor.py --project-id <id>`

Orchestrates full audit: scoring, SLA compliance, trend analysis, CUI-marked report generation.

**Scoring Formula:**
```
Start at 100 points
- Each overdue critical SLA: -20 points
- Each overdue high SLA: -10 points
- Each overdue medium SLA: -5 points
- Each overdue low SLA: -2 points
- Each critical staleness dep (>180d): -3 points
- Each major staleness dep (91-180d): -1 point
Floor at 0, cap at 100
```

**Gate Evaluation:**
- Score >= 80: PASS (healthy)
- Score 50-79: WARN (at_risk, non-blocking)
- Score < 50: FAIL (critical, blocks deployment)

**Outputs:**
- Maintenance score (0-100)
- SLA compliance percentage
- Trend analysis (vs. previous audit)
- CUI-marked markdown report at `reports/<project>/maintenance_audit_YYYY-MM-DD.md`

### Step 4: Remediate
**Tool:** `python tools/maintenance/remediation_engine.py --project-id <id> --auto`

Auto-updates dependency files, creates remediation branches, runs tests, tracks actions.

**Auto-remediation rules:**
- Medium and low severity: auto-fixed (bump to patched version)
- Critical and high severity: generate fix plan, require manual approval
- Dry-run mode: preview all changes without applying

**Process:**
1. Identify eligible vulnerabilities (medium/low with fix_available)
2. Generate updated dependency file (requirements.txt, package.json, etc.)
3. Create git branch `remediate/<project-id>/<date>`
4. Run test suite to verify no breakage
5. If tests pass: commit changes, record action
6. If tests fail: rollback, flag for manual review

**Outputs:**
- Remediation action records (what changed, test results, branch name)
- Updated dependency files (if not dry-run)
- Rollback log for any failed remediations

### Step 5: Verify
Re-run security scan and test suite to confirm fixes don't break anything.

**Tool:** `python tools/security/dependency_auditor.py --project-dir <path>`

Verify:
- [ ] No new vulnerabilities introduced
- [ ] All tests still pass
- [ ] SBOM updated to reflect new versions

### Step 6: Log to Audit Trail
**Tool:** `python tools/audit/audit_logger.py --event-type "maintenance.audit" --actor "maintenance-agent" --action "Maintenance audit complete" --project-id <id>`

Record:
- Audit timestamp and score
- Vulnerabilities found and remediated
- SLA compliance status
- Gate evaluation result

---

## Success Criteria
- [ ] All dependencies inventoried across all detected languages
- [ ] Vulnerability check completed against advisory databases
- [ ] Maintenance score computed (target: >= 70)
- [ ] SLA deadlines set for all open vulnerabilities
- [ ] No overdue critical or high SLAs
- [ ] Remediation actions tracked for all fixes
- [ ] CUI-marked audit report generated
- [ ] Audit trail entries logged

---

## Schedule
- **On every build:** Quick dependency scan (cached versions)
- **Weekly:** Full maintenance audit with registry checks
- **On new CVE disclosure:** Immediate vulnerability check
- **Before deployment:** Gate evaluation (blocking if score < 50)

---

## SLA Thresholds
| Severity | Deadline | Auto-remediate | Escalation |
|----------|----------|---------------|------------|
| Critical | 48 hours | No (manual) | Immediate notification to security team |
| High | 7 days | No (manual) | Daily reminder after day 3 |
| Medium | 30 days | Yes | Weekly summary |
| Low | 90 days | Yes | Monthly summary |

---

## Edge Cases & Notes
1. **Air-gapped environments:** Use --offline flag. Dependencies inventoried but latest versions unknown. Staleness set to -1. Vulnerability checks use local advisory database snapshot.
2. **Transitive dependencies:** SBOM includes transitives but scanner focuses on direct deps. Transitive vuln fixes require updating the direct dep that pulls them in.
3. **Version pinning:** Some projects intentionally pin old versions. Use `risk_accept` status in `dependency_vulnerabilities` to document accepted risk with justification.
4. **Multi-language projects:** Scanner detects all languages automatically. Each gets its own audit tool chain (pip-audit for Python, npm audit for Node.js, cargo-audit for Rust, etc.).
5. **Feeds SbD:** Maintenance audit results feed SbD-05 (patch cadence) and SbD-22 (vulnerability scanning) assessments.
6. **Remediation conflicts:** If two vulnerabilities require conflicting version bumps, flag for manual resolution and document in POAM.
7. **EOL dependencies:** Dependencies with no maintainer activity >1 year flagged as unsupported per NIST SA-22. Recommend replacement.

---

## GOTCHA Layer Mapping
| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| Dependency scan | Tools | dependency_scanner.py |
| Vulnerability check | Tools | vulnerability_checker.py |
| Maintenance audit | Tools | maintenance_auditor.py |
| Remediation | Tools | remediation_engine.py |
| Sequence decisions | Orchestration | AI (you) |
| SLA thresholds | Args | maintenance_config.yaml |
| Gate thresholds | Args | security_gates.yaml |
| Assessment template | Hard Prompts | maintenance_assessment.md |
| NIST standards | Context | NIST 800-53 SI-2, SA-22, CM-3 |

---

## Related Files
- **Tools:** `tools/maintenance/dependency_scanner.py`, `tools/maintenance/vulnerability_checker.py`, `tools/maintenance/maintenance_auditor.py`, `tools/maintenance/remediation_engine.py`
- **MCP Server:** `tools/mcp/maintenance_server.py`
- **Skill:** `.claude/skills/icdev-maintain/SKILL.md`
- **Hard Prompt:** `hardprompts/maintenance/maintenance_assessment.md`
- **Args:** `args/maintenance_config.yaml`, `args/security_gates.yaml`
- **Feeds from:** `goals/security_scan.md` (SAST/dep findings), `goals/compliance_workflow.md` (SBOM)
- **Feeds into:** `goals/sbd_ivv_workflow.md` (SbD-05, SbD-22), `goals/deploy_workflow.md` (deployment gate)

---

## Changelog
- 2026-02-15: Initial creation (Phase 16H)
