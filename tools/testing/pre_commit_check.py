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
        capture_output=True, text=True, cwd=str(BASE_DIR), timeout=60,
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
    """Run route smoke against running server for changed routes."""
    print("[pre-commit] Running route smoke test...")
    changed_arg = ",".join(changed_files)
    result = subprocess.run(
        [sys.executable, "tools/testing/route_smoke.py", "--changed", changed_arg],
        capture_output=True, text=True, cwd=str(BASE_DIR), timeout=120,
    )
    if result.returncode == 0:
        # Print only failures (stdout has per-route lines)
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                if "[FAIL]" in line or "FAIL" == line[:4]:
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
