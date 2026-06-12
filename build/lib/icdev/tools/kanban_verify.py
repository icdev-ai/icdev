#!/usr/bin/env python3
# CUI // SP-CTI
"""kanban_verify.py — run coherence gate and record detailed failure reason.

Runs coherence_checker.py, captures stdout/stderr, parses the JSON report,
extracts the first specific failing rule (tool name, rule code, file, line),
and writes a kanban_verifications row with that detail in the ``reason`` field
instead of the opaque "unknown" previously recorded.

Acceptance criterion: when the coherence gate fails, the output (and the
stored ``kanban_verifications.reason``) includes the exact lint/type/test
error in the form::

    ruff_lint: tools/foo/options.py:42: F841 local variable 'x' assigned but never used

Usage::

    python tools/kanban_verify.py --task-id <task-id> [--json]
    python tools/kanban_verify.py --task-id <task-id> --dry-run
    python tools/kanban_verify.py --task-id <task-id> --changed-files f1.py,f2.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_COHERENCE_SCRIPT = BASE_DIR / "tools" / "workflow" / "coherence_checker.py"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Coherence runner
# ---------------------------------------------------------------------------


def run_coherence_gate(
    changed_files: Optional[List[str]] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Invoke coherence_checker.py and return its parsed JSON output.

    Always captures both stdout and stderr so that Python tracebacks or ruff
    stderr messages are available for fallback parsing.

    Returns a dict with at minimum::

        {
            "overall_pass": bool,
            "stdout": str,
            "stderr": str,
            "returncode": int,
            "checks": [...],   # may be empty on parse failure
        }
    """
    cmd = [sys.executable, str(_COHERENCE_SCRIPT), "--all", "--json"]
    if changed_files:
        cmd += ["--changed-files", ",".join(changed_files)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {
            "overall_pass": False,
            "stdout": "",
            "stderr": f"coherence_checker timed out after {timeout}s",
            "returncode": -1,
            "checks": [],
            "parse_error": "timeout",
        }
    except Exception as exc:
        return {
            "overall_pass": False,
            "stdout": "",
            "stderr": str(exc),
            "returncode": -1,
            "checks": [],
            "parse_error": str(exc),
        }

    base: Dict[str, Any] = {
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
        "checks": [],
    }

    # Parse JSON from stdout
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
            base.update(parsed)
            if "overall_pass" not in base:
                base["overall_pass"] = proc.returncode == 0
            return base
        except json.JSONDecodeError as exc:
            base["parse_error"] = str(exc)

    # Fallback: infer pass from returncode
    base["overall_pass"] = proc.returncode == 0
    return base


# ---------------------------------------------------------------------------
# Error extraction — turn a failing CoherenceCheck dict into a short string
# ---------------------------------------------------------------------------


def _first_extra_or_missing(check: Dict[str, Any]) -> Optional[str]:
    """Return the first entry from ``extra`` or ``missing`` lists, or None."""
    for key in ("extra", "missing", "actual"):
        items = check.get(key) or []
        if items:
            return str(items[0])
    return None


def extract_failure_reason(gate_result: Dict[str, Any]) -> str:
    """Scan the coherence report and return the most specific failure string.

    Priority:
    1. First non-whitelisted ``extra`` / ``missing`` entry from failing checks,
       prefixed with the check_id so the tool name is always present.
       E.g. ``ruff_lint: tools/foo/options.py:42: F841 unused variable``
    2. ``message`` field from the first failing check.
    3. Stderr content (truncated) if no JSON checks were parseable.
    4. Literal "coherence gate failed (no detail)" as last resort.
    """
    checks: List[Dict[str, Any]] = gate_result.get("checks", [])
    failing = [c for c in checks if c.get("status") == "fail"]

    if failing:
        # Prefer the check with the richest ``extra`` list first
        failing_sorted = sorted(failing, key=lambda c: -len(c.get("extra") or []))
        first = failing_sorted[0]
        check_id = first.get("check_id") or first.get("check_name") or "coherence"
        specific = _first_extra_or_missing(first)
        if specific:
            return f"{check_id}: {specific}"
        msg = (first.get("message") or "").strip()
        if msg:
            return f"{check_id}: {msg}"

    # No structured checks — fall back to stderr / stdout snippets
    stderr = (gate_result.get("stderr") or "").strip()
    stdout = (gate_result.get("stdout") or "").strip()

    # Look for ruff / mypy / pytest patterns in raw text
    for line in (stderr + "\n" + stdout).splitlines():
        stripped = line.strip()
        # ruff: path:line:col: code message
        if stripped and any(
            pattern in stripped
            for pattern in ("F401", "F811", "F841", "E501", "error:", "FAILED", "AssertionError")
        ):
            return stripped[:300]

    if stderr:
        return stderr[:300]

    parse_error = gate_result.get("parse_error")
    if parse_error:
        return f"coherence gate parse error: {parse_error}"

    return "coherence gate failed (no detail)"


def build_violations_list(gate_result: Dict[str, Any]) -> List[str]:
    """Collect all extra/missing entries from every failing check."""
    violations: List[str] = []
    for check in gate_result.get("checks", []):
        if check.get("status") != "fail":
            continue
        check_id = check.get("check_id") or "coherence"
        for item in (check.get("extra") or []) + (check.get("missing") or []):
            violations.append(f"{check_id}: {item}")
    return violations


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


def write_verification_row(
    task_id: str,
    gate_result: Dict[str, Any],
    reason: str,
    violations: List[str],
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Insert a row into ``kanban_verifications`` with the parsed details."""
    passed = bool(gate_result.get("overall_pass"))
    result_str = "passed" if passed else "failed"
    row_id = f"kv-{uuid.uuid4().hex[:12]}"
    verified_at = _utcnow()

    row = {
        "id": row_id,
        "task_id": task_id,
        "verified_at": verified_at,
        "result": result_str,
        "reason": reason[:1000],
        "coherence_passed": 1 if passed else 0,
        "coherence_violations": json.dumps(violations[:50]) if violations else None,
    }

    if dry_run:
        return {"dry_run": True, "row": row}

    try:
        from tools.db.storage import get_connection  # type: ignore

        conn = get_connection()
        conn.execute(
            "INSERT INTO kanban_verifications "
            "(id, task_id, verified_at, result, reason, coherence_passed, coherence_violations) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["id"],
                row["task_id"],
                row["verified_at"],
                row["result"],
                row["reason"],
                row["coherence_passed"],
                row["coherence_violations"],
            ),
        )
        conn.commit()
        return {"written": True, "row": row}
    except Exception as exc:
        return {"written": False, "error": str(exc), "row": row}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def verify_task(
    task_id: str,
    changed_files: Optional[List[str]] = None,
    dry_run: bool = False,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Run the coherence gate for *task_id* and persist the result.

    Returns a result dict suitable for ``--json`` output::

        {
            "task_id": "...",
            "passed": bool,
            "reason": "ruff_lint: tools/foo/options.py:42: F841 ...",
            "violations": [...],
            "db": {...},
        }
    """
    gate = run_coherence_gate(changed_files=changed_files, timeout=timeout)
    passed = bool(gate.get("overall_pass"))
    reason = "coherence gate passed" if passed else extract_failure_reason(gate)
    violations = build_violations_list(gate)

    db_result = write_verification_row(
        task_id=task_id,
        gate_result=gate,
        reason=reason,
        violations=violations,
        dry_run=dry_run,
    )

    return {
        "task_id": task_id,
        "passed": passed,
        "reason": reason,
        "violations": violations,
        "gate": {
            "returncode": gate.get("returncode"),
            "overall_pass": gate.get("overall_pass"),
            "total_checks": gate.get("total_checks"),
            "failed_checks": gate.get("failed_checks"),
        },
        "db": db_result,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="kanban_verify — run coherence gate and record failure detail"
    )
    parser.add_argument("--task-id", required=True, help="Kanban task ID to verify")
    parser.add_argument(
        "--changed-files",
        default="",
        help="Comma-separated list of changed files (scopes ruff check)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse but do not write to DB")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--timeout", type=int, default=120, help="Subprocess timeout in seconds")
    args = parser.parse_args()

    changed = [f.strip() for f in args.changed_files.split(",") if f.strip()] if args.changed_files else None

    result = verify_task(
        task_id=args.task_id,
        changed_files=changed,
        dry_run=args.dry_run,
        timeout=args.timeout,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"\n=== Kanban Verify [{status}] task={result['task_id']} ===")
        print(f"  reason: {result['reason']}")
        if result["violations"]:
            print(f"  violations ({len(result['violations'])}):")
            for v in result["violations"][:10]:
                print(f"    - {v}")
        db = result.get("db", {})
        if db.get("dry_run"):
            print("  [dry-run: DB write skipped]")
        elif db.get("written"):
            print(f"  [DB row written: {db['row']['id']}]")
        else:
            print(f"  [DB write failed: {db.get('error')}]")
        print()

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
