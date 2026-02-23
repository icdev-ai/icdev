#!/usr/bin/env python3
# CUI // SP-CTI
"""Production Remediation — auto-fix audit blockers found by /audit.

Chains after production_audit.py to auto-fix what it can, suggest fixes
for medium-confidence items, and escalate the rest.  Follows the 3-tier
confidence model from self_heal_analyzer.py (>=0.7 auto, 0.3-0.7 suggest,
<0.3 escalate).

Usage:
    python tools/testing/production_remediate.py --human --stream
    python tools/testing/production_remediate.py --auto --json
    python tools/testing/production_remediate.py --dry-run --json
    python tools/testing/production_remediate.py --check-id SEC-002 --auto --json
    python tools/testing/production_remediate.py --skip-audit --auto --json

Exit codes: 0 = all remediations succeeded or no blockers,
            1 = at least one auto-fix failed or blockers remain.

Architecture decisions: D296-D300.
"""

import argparse
import dataclasses
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "icdev.db"

# Import from production_audit.py
sys.path.insert(0, str(PROJECT_ROOT))
from tools.testing.production_audit import (
    AuditCheck,
    AuditReport,
    CHECK_REGISTRY,
    run_audit,
    _run_subprocess,
    _get_db,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RemediationAction:
    check_id: str
    check_name: str
    category: str
    confidence: float
    tier: str               # auto_fix, suggest, escalate
    status: str             # fixed, failed, suggested, escalated, skipped, dry_run
    fix_strategy: str
    fix_command: Optional[str]
    message: str
    details: dict
    duration_ms: int = 0
    verification_result: Optional[dict] = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class RemediationReport:
    timestamp: str
    source_audit: Optional[dict]
    dry_run: bool
    total_actions: int
    auto_fixed: int
    suggested: int
    escalated: int
    skipped: int
    failed: int
    verified_pass: int
    verified_fail: int
    actions: list
    duration_total_ms: int

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Remediation Registry (D296 — declarative, D26 pattern)
# ---------------------------------------------------------------------------
# Each entry: check_id -> {confidence, tier, strategy, command, suggestion}
# SEC-003 hardcoded to escalate (D297) — secrets MUST NEVER be auto-fixed.

REMEDIATION_REGISTRY: Dict[str, dict] = {
    # --- Auto-fix (confidence >= 0.7) ---
    "SEC-002": {
        "confidence": 0.80,
        "tier": "auto_fix",
        "strategy": "dep_version_bumps",
        "command": [
            sys.executable, str(PROJECT_ROOT / "tools" / "maintenance" / "remediation_engine.py"),
            "--project-id", "icdev-platform", "--auto", "--json",
        ],
        "suggestion": None,
    },
    "INT-002": {
        "confidence": 0.90,
        "tier": "auto_fix",
        "strategy": "rebuild_db_schema",
        "command": [
            sys.executable, str(PROJECT_ROOT / "tools" / "db" / "init_icdev_db.py"),
        ],
        "suggestion": None,
    },
    "PRF-001": {
        "confidence": 0.75,
        "tier": "auto_fix",
        "strategy": "apply_pending_migrations",
        "command": [
            sys.executable, str(PROJECT_ROOT / "tools" / "db" / "migrate.py"),
            "--up",
        ],
        "suggestion": None,
    },
    "CMP-006": {
        "confidence": 0.85,
        "tier": "auto_fix",
        "strategy": "regenerate_sbom",
        "command": [
            sys.executable, str(PROJECT_ROOT / "tools" / "compliance" / "sbom_generator.py"),
            "--project-dir", str(PROJECT_ROOT),
        ],
        "suggestion": None,
    },

    # --- Suggest (confidence 0.3-0.7) ---
    "SEC-001": {
        "confidence": 0.50,
        "tier": "suggest",
        "strategy": "bandit_findings_guidance",
        "command": None,
        "suggestion": (
            "Run `bandit -r tools/ -f json` to see findings. "
            "Fix each finding per test_id: B101→remove assert in production, "
            "B608→parameterize SQL, B301→use json instead of pickle, "
            "B602→avoid shell=True, B105→no hardcoded passwords."
        ),
    },
    "SEC-006": {
        "confidence": 0.45,
        "tier": "suggest",
        "strategy": "dangerous_pattern_alternatives",
        "command": None,
        "suggestion": (
            "Replace dangerous patterns with safe alternatives: "
            "eval()→ast.literal_eval(), exec()→importlib, "
            "os.system()→subprocess.run(), pickle.loads()→json.loads(), "
            "yaml.load()→yaml.safe_load()."
        ),
    },
    "CMP-002": {
        "confidence": 0.55,
        "tier": "suggest",
        "strategy": "governance_config_hints",
        "command": None,
        "suggestion": (
            "Run `python tools/testing/claude_dir_validator.py --human` "
            "to see governance failures. Fix each: add missing tables to "
            "APPEND_ONLY_TABLES, add routes to start.md, add E2E specs."
        ),
    },
    "CMP-003": {
        "confidence": 0.50,
        "tier": "suggest",
        "strategy": "append_only_hook_edit",
        "command": None,
        "suggestion": (
            "Add missing append-only tables to APPEND_ONLY_TABLES list in "
            ".claude/hooks/pre_tool_use.py. Check init_icdev_db.py for tables "
            "with 'append-only' or 'immutable' in comments."
        ),
    },
    "CMP-004": {
        "confidence": 0.40,
        "tier": "suggest",
        "strategy": "security_gates_review",
        "command": None,
        "suggestion": (
            "Review args/security_gates.yaml for missing gate definitions. "
            "Each new feature with blocking conditions needs a gate entry."
        ),
    },
    "INT-003": {
        "confidence": 0.55,
        "tier": "suggest",
        "strategy": "import_error_fix",
        "command": None,
        "suggestion": (
            "Check the failing imports: run `python -c 'import tools.MODULE'` "
            "for each failing module. Common fixes: add missing __init__.py, "
            "fix circular imports, install missing optional deps."
        ),
    },
    "PRF-004": {
        "confidence": 0.60,
        "tier": "suggest",
        "strategy": "test_collection_fix",
        "command": None,
        "suggestion": (
            "Run `pytest --collect-only tests/ 2>&1 | grep ERROR` to find "
            "collection errors. Common fixes: fix syntax errors in test files, "
            "add missing conftest.py fixtures, resolve import errors."
        ),
    },

    # --- Escalate (confidence < 0.3, human required) ---
    "SEC-003": {
        "confidence": 0.10,
        "tier": "escalate",
        "strategy": "secrets_rotation",
        "command": None,
        "suggestion": (
            "SECRETS DETECTED — requires human judgment. Rotate all detected "
            "secrets immediately. Never auto-fix secrets: rotation scope, "
            "dependent services, and credential distribution require human review."
        ),
    },
    "PLT-002": {
        "confidence": 0.15,
        "tier": "escalate",
        "strategy": "python_version_upgrade",
        "command": None,
        "suggestion": (
            "Python version below minimum required (3.9). "
            "Coordinate with ops team for system-level Python upgrade."
        ),
    },
    "PLT-003": {
        "confidence": 0.15,
        "tier": "escalate",
        "strategy": "os_stdlib_modules",
        "command": None,
        "suggestion": (
            "Missing required stdlib modules. "
            "Coordinate with ops team — may indicate broken Python installation."
        ),
    },
    "INT-001": {
        "confidence": 0.25,
        "tier": "escalate",
        "strategy": "mcp_server_code_review",
        "command": None,
        "suggestion": (
            "MCP server import failures require developer review. "
            "Check server files for syntax errors, missing dependencies, "
            "or broken imports."
        ),
    },
}


# ---------------------------------------------------------------------------
# Core remediation engine
# ---------------------------------------------------------------------------

def _get_latest_audit() -> Optional[dict]:
    """Retrieve the most recent audit report from DB."""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT id, report_json FROM production_audits ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            report = json.loads(row["report_json"] if isinstance(row, sqlite3.Row) else row[1])
            report["_db_id"] = row["id"] if isinstance(row, sqlite3.Row) else row[0]
            return report
        return None
    except Exception:
        return None


def _extract_failed_checks(audit_report: dict) -> List[dict]:
    """Extract failed checks from an audit report dict."""
    failed = []
    categories = audit_report.get("categories", {})
    for _cat_name, cat_data in categories.items():
        for check in cat_data.get("checks", []):
            if check.get("status") in ("fail", "warn"):
                failed.append(check)
    return failed


def _run_auto_fix(
    check_id: str,
    registry_entry: dict,
    dry_run: bool = False,
    stream: bool = False,
) -> Tuple[str, str, dict]:
    """Execute an auto-fix command.

    Returns (status, message, details).
    """
    cmd = registry_entry["command"]
    if not cmd:
        return "failed", "No command configured", {}

    if dry_run:
        return "dry_run", f"Would run: {' '.join(str(c) for c in cmd)}", {"command": [str(c) for c in cmd]}

    if stream:
        print(f"    [RUN] Executing: {' '.join(str(c) for c in cmd[:3])}...", file=sys.stderr)

    rc, stdout, stderr = _run_subprocess(cmd, timeout=180)

    if rc == 0:
        return "fixed", f"Auto-fix succeeded (exit 0)", {"stdout_tail": stdout[-500:] if stdout else "", "stderr_tail": stderr[-200:] if stderr else ""}
    else:
        return "failed", f"Auto-fix failed (exit {rc}): {stderr[:300]}", {"returncode": rc, "stdout_tail": stdout[-500:] if stdout else "", "stderr_tail": stderr[-500:] if stderr else ""}


def _verify_fix(check_id: str, stream: bool = False) -> Optional[AuditCheck]:
    """Re-run a single check to verify the fix worked (D298)."""
    entry = CHECK_REGISTRY.get(check_id)
    if not entry:
        return None

    fn, _cat, _sev = entry
    if stream:
        print(f"    [VERIFY] Re-running {check_id}...", file=sys.stderr)

    try:
        result, duration = _timed_check(fn)
        result.duration_ms = duration
        return result
    except Exception as e:
        return AuditCheck(
            check_id=check_id,
            check_name="verification",
            category="unknown",
            status="fail",
            severity="blocking",
            message=f"Verification error: {e}",
            details={},
        )


def _timed_check(fn):
    """Time a check function call."""
    start = time.time()
    result = fn()
    elapsed = int((time.time() - start) * 1000)
    return result, elapsed


def _store_remediation(action: RemediationAction, source_audit_id: Optional[int], dry_run: bool, report_json: Optional[str] = None):
    """Store remediation action in remediation_audit_log (append-only, D299)."""
    try:
        conn = _get_db()
        conn.execute(
            """INSERT INTO remediation_audit_log
               (source_audit_id, check_id, check_name, category,
                confidence, tier, status, fix_strategy, fix_command,
                message, details, duration_ms,
                verification_check_id, verification_status, verification_message,
                dry_run, report_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source_audit_id,
                action.check_id,
                action.check_name,
                action.category,
                action.confidence,
                action.tier,
                action.status,
                action.fix_strategy,
                action.fix_command,
                action.message,
                json.dumps(action.details),
                action.duration_ms,
                action.check_id if action.verification_result else None,
                action.verification_result.get("status") if action.verification_result else None,
                action.verification_result.get("message") if action.verification_result else None,
                1 if dry_run else 0,
                report_json,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Don't fail remediation because DB write failed


# ---------------------------------------------------------------------------
# Main remediation runner
# ---------------------------------------------------------------------------

def run_remediation(
    auto: bool = False,
    dry_run: bool = False,
    check_id: Optional[str] = None,
    category: Optional[str] = None,
    skip_audit: bool = False,
    stream: bool = False,
) -> RemediationReport:
    """Run the production remediation pipeline.

    Args:
        auto: If True, execute auto-fix commands without prompting.
        dry_run: If True, preview what would be fixed without executing.
        check_id: Target a specific check_id only.
        category: Target a specific category only.
        skip_audit: If True, use the latest stored audit instead of re-running.
        stream: If True, print progress to stderr.

    Returns:
        RemediationReport with all actions taken.
    """
    start_time = time.time()

    # Step 1: Get or run audit
    if stream:
        print("\n" + "=" * 60, file=sys.stderr)
        print("  ICDEV Production Remediation", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

    audit_report = None
    source_audit_id = None

    if skip_audit:
        if stream:
            print("\n  [SKIP] Using latest stored audit report...", file=sys.stderr)
        audit_report = _get_latest_audit()
        if audit_report:
            source_audit_id = audit_report.get("_db_id")
        else:
            if stream:
                print("  [WARN] No stored audit found. Running fresh audit...", file=sys.stderr)

    if audit_report is None:
        if stream:
            print("\n  [AUDIT] Running production audit...", file=sys.stderr)
        report_obj = run_audit(stream=stream)
        audit_report = report_obj.to_dict()
        # Get the DB ID of the just-stored audit
        latest = _get_latest_audit()
        if latest:
            source_audit_id = latest.get("_db_id")

    # Step 2: Extract failed checks
    failed_checks = _extract_failed_checks(audit_report)

    # Filter by check_id or category if specified
    if check_id:
        failed_checks = [c for c in failed_checks if c["check_id"] == check_id]
    if category:
        failed_checks = [c for c in failed_checks if c["category"] == category]

    if stream:
        print(f"\n  Found {len(failed_checks)} failed/warned check(s) to remediate.", file=sys.stderr)
        if dry_run:
            print("  Mode: DRY RUN (no changes will be made)", file=sys.stderr)

    # Step 3: Process each failed check
    actions: List[RemediationAction] = []
    auto_fixed = 0
    suggested = 0
    escalated = 0
    skipped = 0
    failed_count = 0
    verified_pass = 0
    verified_fail = 0

    for check in failed_checks:
        cid = check["check_id"]
        reg = REMEDIATION_REGISTRY.get(cid)

        if not reg:
            # No remediation registered for this check
            action = RemediationAction(
                check_id=cid,
                check_name=check.get("check_name", ""),
                category=check.get("category", ""),
                confidence=0.0,
                tier="unknown",
                status="skipped",
                fix_strategy="no_remediation_registered",
                fix_command=None,
                message=f"No remediation registered for {cid}",
                details=check.get("details", {}),
            )
            actions.append(action)
            skipped += 1
            if stream:
                print(f"\n  [SKIP] {cid}: {check.get('check_name', '')} — no remediation registered", file=sys.stderr)
            continue

        if stream:
            tier_label = {"auto_fix": "AUTO-FIX", "suggest": "SUGGEST", "escalate": "ESCALATE"}.get(reg["tier"], "UNKNOWN")
            print(f"\n  [{tier_label}] {cid}: {check.get('check_name', '')} (confidence={reg['confidence']:.2f})", file=sys.stderr)

        action_start = time.time()

        if reg["tier"] == "auto_fix":
            if auto or dry_run:
                status, message, details = _run_auto_fix(cid, reg, dry_run=dry_run, stream=stream)
            else:
                status = "skipped"
                message = "Auto-fix available but --auto not specified"
                details = {"strategy": reg["strategy"]}

            action = RemediationAction(
                check_id=cid,
                check_name=check.get("check_name", ""),
                category=check.get("category", ""),
                confidence=reg["confidence"],
                tier=reg["tier"],
                status=status,
                fix_strategy=reg["strategy"],
                fix_command=" ".join(str(c) for c in reg["command"]) if reg["command"] else None,
                message=message,
                details=details,
            )

            # Verify fix if it succeeded (D298)
            if status == "fixed":
                verification = _verify_fix(cid, stream=stream)
                if verification:
                    action.verification_result = verification.to_dict()
                    if verification.status == "pass":
                        verified_pass += 1
                        if stream:
                            print(f"    [OK] Verification passed", file=sys.stderr)
                    else:
                        verified_fail += 1
                        if stream:
                            print(f"    [!!] Verification failed: {verification.message}", file=sys.stderr)

            if status == "fixed":
                auto_fixed += 1
            elif status in ("failed",):
                failed_count += 1
            elif status == "skipped":
                skipped += 1
            elif status == "dry_run":
                auto_fixed += 1  # Count as would-fix for dry-run reporting

        elif reg["tier"] == "suggest":
            action = RemediationAction(
                check_id=cid,
                check_name=check.get("check_name", ""),
                category=check.get("category", ""),
                confidence=reg["confidence"],
                tier=reg["tier"],
                status="suggested",
                fix_strategy=reg["strategy"],
                fix_command=None,
                message=reg["suggestion"] or "Review required",
                details=check.get("details", {}),
            )
            suggested += 1
            if stream:
                print(f"    Suggestion: {reg['suggestion']}", file=sys.stderr)

        elif reg["tier"] == "escalate":
            action = RemediationAction(
                check_id=cid,
                check_name=check.get("check_name", ""),
                category=check.get("category", ""),
                confidence=reg["confidence"],
                tier=reg["tier"],
                status="escalated",
                fix_strategy=reg["strategy"],
                fix_command=None,
                message=reg["suggestion"] or "Human review required",
                details=check.get("details", {}),
            )
            escalated += 1
            if stream:
                print(f"    ESCALATED: {reg['suggestion']}", file=sys.stderr)

        else:
            action = RemediationAction(
                check_id=cid,
                check_name=check.get("check_name", ""),
                category=check.get("category", ""),
                confidence=reg.get("confidence", 0.0),
                tier=reg["tier"],
                status="skipped",
                fix_strategy=reg.get("strategy", "unknown"),
                fix_command=None,
                message=f"Unknown tier: {reg['tier']}",
                details={},
            )
            skipped += 1

        action.duration_ms = int((time.time() - action_start) * 1000)
        actions.append(action)

        # Store each action in DB (append-only, D299/D300)
        _store_remediation(action, source_audit_id, dry_run)

    # Step 4: Build report
    total_ms = int((time.time() - start_time) * 1000)

    report = RemediationReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        source_audit={"audit_id": source_audit_id, "overall_pass": audit_report.get("overall_pass", False)},
        dry_run=dry_run,
        total_actions=len(actions),
        auto_fixed=auto_fixed,
        suggested=suggested,
        escalated=escalated,
        skipped=skipped,
        failed=failed_count,
        verified_pass=verified_pass,
        verified_fail=verified_fail,
        actions=[a.to_dict() for a in actions],
        duration_total_ms=total_ms,
    )

    # Store full report JSON in DB for the last action
    if actions:
        try:
            conn = _get_db()
            conn.execute(
                """UPDATE remediation_audit_log SET report_json = ?
                   WHERE rowid = (SELECT MAX(rowid) FROM remediation_audit_log)""",
                (json.dumps(report.to_dict()),),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    return report


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _format_human(report: RemediationReport) -> str:
    """Format remediation report for human-readable terminal output."""
    lines = []
    lines.append("")
    lines.append("=" * 60)
    lines.append("  ICDEV Production Remediation Report")
    lines.append("=" * 60)
    lines.append("")

    if report.dry_run:
        lines.append("  MODE: DRY RUN (no changes were made)")
        lines.append("")

    # Summary
    lines.append(f"  Total actions:  {report.total_actions}")
    lines.append(f"  Auto-fixed:     {report.auto_fixed}")
    lines.append(f"  Suggested:      {report.suggested}")
    lines.append(f"  Escalated:      {report.escalated}")
    lines.append(f"  Skipped:        {report.skipped}")
    lines.append(f"  Failed:         {report.failed}")
    if report.verified_pass or report.verified_fail:
        lines.append(f"  Verified pass:  {report.verified_pass}")
        lines.append(f"  Verified fail:  {report.verified_fail}")
    lines.append(f"  Duration:       {report.duration_total_ms}ms")
    lines.append("")

    # Actions by tier
    for tier_name, tier_label in [("auto_fix", "AUTO-FIXED"), ("suggest", "SUGGESTIONS"), ("escalate", "ESCALATED"), ("unknown", "SKIPPED")]:
        tier_actions = [a for a in report.actions if a.get("tier") == tier_name]
        if not tier_actions:
            continue

        lines.append(f"  --- {tier_label} ---")
        for a in tier_actions:
            status_icon = {
                "fixed": "[OK]", "dry_run": "[DRY]", "suggested": "[??]",
                "escalated": "[!!]", "skipped": "[--]", "failed": "[XX]",
            }.get(a.get("status", ""), "[??]")
            lines.append(f"    {status_icon} {a['check_id']}: {a['check_name']}")
            lines.append(f"          {a['message'][:120]}")
            if a.get("verification_result"):
                v = a["verification_result"]
                v_icon = "[OK]" if v.get("status") == "pass" else "[!!]"
                lines.append(f"          Verification: {v_icon} {v.get('message', '')[:80]}")
        lines.append("")

    # Remaining skipped (no remediation)
    no_reg = [a for a in report.actions if a.get("tier") == "unknown" or a.get("fix_strategy") == "no_remediation_registered"]
    if no_reg:
        lines.append("  --- NO REMEDIATION REGISTERED ---")
        for a in no_reg:
            lines.append(f"    [--] {a['check_id']}: {a.get('check_name', '')}")
        lines.append("")

    # Overall
    still_blocked = report.failed > 0 or report.escalated > 0
    if still_blocked:
        lines.append("  RESULT: BLOCKERS REMAIN — manual intervention required")
    elif report.suggested > 0:
        lines.append("  RESULT: Suggestions pending — review recommended")
    else:
        lines.append("  RESULT: ALL CLEAR — no blockers")
    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Production Remediation — auto-fix audit blockers"
    )
    parser.add_argument("--auto", action="store_true", help="Execute auto-fix commands without prompting")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would be fixed without executing")
    parser.add_argument("--check-id", type=str, help="Target a specific check ID (e.g. SEC-002)")
    parser.add_argument("--category", type=str, help="Target a specific category (e.g. security)")
    parser.add_argument("--skip-audit", action="store_true", help="Use latest stored audit instead of re-running")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--human", action="store_true", help="Output as human-readable text")
    parser.add_argument("--stream", action="store_true", help="Stream progress to stderr")
    args = parser.parse_args()

    report = run_remediation(
        auto=args.auto,
        dry_run=args.dry_run,
        check_id=args.check_id,
        category=args.category,
        skip_audit=args.skip_audit,
        stream=args.stream,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    elif args.human or (not args.json):
        print(_format_human(report))

    # Exit code: 0 = no blockers remain, 1 = blockers remain
    has_blockers = report.failed > 0 or report.escalated > 0
    sys.exit(1 if has_blockers else 0)


if __name__ == "__main__":
    main()
