# CUI // SP-CTI
# /remediate — Auto-fix Production Audit Blockers

Auto-fix blockers found by `/audit` using a 3-tier confidence model.
Chains after `/audit` to fix what it can, suggest fixes for medium-confidence
items, and escalate the rest for human review.

## Variables

MODE: auto (or: dry-run, check-id, category)

## Workflow

1. **Parse Arguments**
   - If `$ARGUMENTS` is empty or `auto`: run with `--auto --stream`
   - If `$ARGUMENTS` contains `dry-run` or `--dry-run`: run with `--dry-run --stream`
   - If `$ARGUMENTS` contains a check ID (e.g., `SEC-002`): run with `--check-id SEC-002 --auto`
   - If `$ARGUMENTS` contains `--skip-audit`: reuse the latest stored audit (faster)
   - If `$ARGUMENTS` contains `--json`: add `--json` for machine-readable output
   - If `$ARGUMENTS` contains a category name: run with `--category <name>`

2. **Run Production Remediation**
   ```bash
   python tools/testing/production_remediate.py --auto --human --stream [OPTIONS]
   ```

   For dry-run preview:
   ```bash
   python tools/testing/production_remediate.py --dry-run --human --stream
   ```

   For JSON output:
   ```bash
   python tools/testing/production_remediate.py --auto --json --stream
   ```

3. **Review Results**
   The tool processes failed checks from the audit in 3 tiers:

   **Auto-fix (confidence >= 0.7):**
   - SEC-002: Dependency version bumps via remediation_engine.py
   - INT-002: Rebuild DB schema via init_icdev_db.py
   - PRF-001: Apply pending migrations via migrate.py
   - CMP-006: Regenerate SBOM via sbom_generator.py

   **Suggest (confidence 0.3-0.7):**
   - SEC-001: Bandit findings with per-test_id fix guidance
   - SEC-006: Dangerous patterns with safe alternatives
   - CMP-002: Governance failures with config fix hints
   - CMP-003: Missing append-only tables with hook edit suggestions
   - CMP-004: Missing security gates
   - INT-003: Import/syntax errors per file
   - PRF-004: Test collection errors per test file

   **Escalate (confidence < 0.3, human required):**
   - SEC-003: Secrets — rotation requires human judgment, NEVER auto-fix
   - PLT-002/003: System-level Python/OS — ops team coordination
   - INT-001: MCP server code errors — developer review required

4. **Report to User**
   Present the remediation summary:
   - **Auto-fixed**: count of successfully auto-fixed items
   - **Suggested**: count of items with suggested fixes
   - **Escalated**: count of items requiring human review
   - **Skipped**: count of items with no registered remediation
   - **Failed**: count of auto-fix attempts that failed
   - **Verified**: pass/fail counts from re-running affected checks

5. **If Blockers Remain**
   After remediation, if blockers still exist:
   - List remaining escalated items with specific guidance
   - List failed auto-fixes with error details
   - Recommend running `/audit` again after manual fixes

## Notes
- Auto-fix commands are verified by re-running only the affected check (D298)
- SEC-003 (secrets) ALWAYS escalated, NEVER auto-fixed (D297)
- Results stored in `remediation_audit_log` table (append-only, D299)
- Dry-run records "dry_run" status rows for audit trail completeness (D300)
- Use `--skip-audit` to reuse the latest audit (faster when just re-running fixes)
