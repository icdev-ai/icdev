# CUI // SP-CTI
# /audit — Production Readiness Audit

Run comprehensive pre-production validation across 6 categories:
platform, security, compliance, integration, performance, documentation.

30 checks with streaming results and consolidated report.

## Variables

CATEGORIES: all (or comma-separated: platform,security,compliance,integration,performance,documentation)

## Workflow

1. **Parse Arguments**
   If `$ARGUMENTS` contains category names (e.g., `security`, `compliance`), pass them via `--category`.
   If `$ARGUMENTS` is empty or `all`, run all 6 categories.

2. **Run Production Audit**
   ```bash
   python tools/testing/production_audit.py --human --stream [--category CATEGORIES]
   ```

   If `$ARGUMENTS` contains `--json`:
   ```bash
   python tools/testing/production_audit.py --json [--category CATEGORIES]
   ```

3. **Review Results**
   The tool runs 30 checks in dependency order:
   - **Platform** (PLT-001..004): Python version, stdlib, OS compat, Dockerfiles
   - **Security** (SEC-001..006): SAST, deps, secrets, prompt injection, OWASP, patterns
   - **Compliance** (CMP-001..006): CUI markings, governance, append-only, gates, XAI, SBOM
   - **Integration** (INT-001..005): MCP servers, DB schema, imports, dashboard, API gateway
   - **Performance** (PRF-001..004): Migrations, backup config, resilience, test collection
   - **Documentation** (DOC-001..005): CLAUDE.md accuracy, manifests, routes, skills

4. **Report to User**
   Present the final summary:
   - Overall status: **READY** (0 blockers) or **BLOCKED** (blockers exist)
   - Pass/fail/warn/skip counts per category
   - **Blockers** — must fix before production (severity=blocking + status=fail)
   - **Warnings** — should fix but not blocking
   - Total duration

5. **If Blockers Found**
   List each blocker with its check ID and suggest remediation:
   - SEC-001 (SAST): `bandit -r tools/ -f json` to see findings, fix code
   - SEC-002 (deps): `pip-audit` to see CVEs, update packages
   - SEC-003 (secrets): `detect-secrets scan` to find leaked credentials
   - CMP-002 (governance): `python tools/testing/claude_dir_validator.py --human`
   - INT-001 (MCP): Check syntax of failing MCP server files
   - INT-002 (DB): Run `python tools/db/init_icdev_db.py` to rebuild schema
   - PRF-004 (tests): Run `pytest tests/ --co -q` to see collection errors

6. **Chain to /remediate**
   If blockers were found (exit code 1), automatically chain to `/remediate`:
   ```bash
   python tools/testing/production_remediate.py --auto --human --stream
   ```
   This will:
   - Auto-fix what it can (deps, DB schema, migrations, SBOM)
   - Suggest fixes for medium-confidence items (SAST, patterns, governance)
   - Escalate items requiring human review (secrets, system Python, MCP code)
   - Re-run affected checks to verify fixes

## Notes
- Results are stored in the `production_audits` table for trend tracking
- Remediation results stored in `remediation_audit_log` (append-only)
- Exit code 0 = all blocking checks pass, 1 = at least one blocker failed
- The tool gracefully skips checks when optional tools are not installed
- Dashboard page health check (INT-004) requires the dashboard to be running
