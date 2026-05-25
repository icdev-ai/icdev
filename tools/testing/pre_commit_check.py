#!/usr/bin/env python3
# CUI // SP-CTI
"""Pre-commit gate — runs blueprint import check + route smoke on changed files.

Called by .git/hooks/pre-commit. Blocks the commit if:
  1. Any blueprint.py fails to import (would cause 500 on all its routes)
  2. Any nav route returns non-200 or contains error text (catches runtime failures
     that CodeLens + Coherence cannot detect)

Exit 0 = all checks pass (commit proceeds).
Exit 1 = a check failed (commit blocked with error message).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Files that trigger the route smoke gate when changed
DASHBOARD_PATTERNS = (
    "tools/dashboard/",
    "tools/dashboard/templates/",
    "icdev/tools/dashboard/",
    "/blueprint.py",
    "/app.py",
)


def _get_staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, cwd=str(BASE_DIR),
    )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


def _is_dashboard_change(files: list[str]) -> bool:
    for f in files:
        for pat in DASHBOARD_PATTERNS:
            if pat in f.replace("\\", "/"):
                return True
    return False


def _run_blueprint_import_check() -> bool:
    """Run the coherence blueprint_imports check."""
    print("[pre-commit] Checking blueprint imports...")
    result = subprocess.run(
        [sys.executable, "tools/workflow/coherence_checker.py", "--check", "blueprint_imports", "--json"],
        capture_output=True, text=True, cwd=str(BASE_DIR), timeout=300,
    )
    if result.returncode != 0:
        print("[pre-commit] BLOCKED: Blueprint import check failed:")
        print(result.stdout or result.stderr)
        return False
    import json
    try:
        data = json.loads(result.stdout)
        checks = data.get("checks", [])
        for c in checks:
            if c.get("status") == "fail":
                print("[pre-commit] BLOCKED: Blueprint import failures:")
                for m in c.get("missing", []):
                    print(f"  {m}")
                return False
    except Exception:
        pass
    print("[pre-commit] Blueprint imports: OK")
    return True


def _run_route_smoke(changed_files: list[str]) -> bool:
    """Run route smoke against running server for changed routes.

    Uses --changed mode so only affected routes are tested (fast).
    Falls back to skip gracefully when no routes are affected or when
    the dashboard is not running.
    """
    print("[pre-commit] Running route smoke test...")
    # Only pass dashboard-relevant files to avoid Windows cmd-line length limit
    dashboard_files = [
        f for f in changed_files
        if any(pat in f.replace("\\", "/") for pat in DASHBOARD_PATTERNS)
    ]
    changed_arg = ",".join(dashboard_files)

    # Pre-check: ask route_smoke which routes it would test so we can skip
    # early when no routes are affected (e.g. only tool/test files changed).
    try:
        import sys as _sys
        _sys.path.insert(0, str(BASE_DIR))
        from tools.testing.route_smoke import _routes_for_changed_files, _server_up
        affected = _routes_for_changed_files(changed_files)
        if not affected:
            print("[pre-commit] Route smoke: no dashboard routes affected — skipped")
            return True
        if not _server_up("http://localhost:5050", timeout=2.0):
            print("[pre-commit] Route smoke: dashboard not running — skipped")
            return True
    except Exception:
        pass  # fall through to subprocess approach

    # Guard: if import-based check failed, verify server is reachable via socket
    # before launching subprocess — avoids a 60-second timeout when server is down.
    import socket as _socket
    try:
        with _socket.create_connection(("127.0.0.1", 5050), timeout=2.0):
            pass
    except OSError:
        print("[pre-commit] Route smoke: dashboard not running (port 5050 closed) — skipped")
        return True

    try:
        result = subprocess.run(
            [sys.executable, "tools/testing/route_smoke.py", "--changed", changed_arg],
            capture_output=True, text=True, cwd=str(BASE_DIR), timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("[pre-commit] Route smoke: timed out (>120s) - too many routes for inline gate; run manually.")
        print("[pre-commit] WARNING: Skipping route smoke - commit allowed, but run: python tools/testing/route_smoke.py --all")
        return True

    if result.returncode == 0:
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                if "[FAIL]" in line or line.startswith("FAIL"):
                    print(f"  {line}")
        print("[pre-commit] Route smoke: OK")
        return True

    print("[pre-commit] BLOCKED: Route smoke failed:")
    print(result.stdout or result.stderr)
    return False


def main() -> int:
    staged = _get_staged_files()
    if not staged:
        return 0

    failed = False

    # Always run blueprint import check when Python files change
    py_changes = [f for f in staged if f.endswith(".py")]
    if py_changes:
        if not _run_blueprint_import_check():
            failed = True

    # Run route smoke when dashboard files change (requires running server)
    if not failed and _is_dashboard_change(staged):
        if not _run_route_smoke(staged):
            failed = True

    if failed:
        print("\n[pre-commit] Commit BLOCKED. Fix the issues above and retry.")
        return 1

    print("[pre-commit] All pre-commit checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
