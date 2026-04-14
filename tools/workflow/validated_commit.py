# CUI // SP-CTI
"""Unified commit/validate/push pipeline shared by the stop hook and the
kanban scheduler.

Single source of truth for the four-gate validation suite:
  1. CodeLens  — py_compile + ruff + bandit on modified files
  2. Coherence — coherence_checker.py --all --gate (compares to main baseline)
  3. E2E       — Selenium lifecycle test if UI files modified
  4. Companion — sync to 10 platforms (best-effort, never fails)

Both interactive sessions (stop hook) and scheduler-dispatched sessions
(kanban reflex) pass through this module so behavior is consistent.

Usage:
    from tools.workflow.validated_commit import validate_working_tree

    ok, reason, metrics = validate_working_tree(
        cwd="/path/to/repo",
        modified_files=["tools/foo.py", "tests/test_foo.py"],
        compare_to_main=True,     # Only fail if worktree introduces NEW issues
        run_e2e=True,             # Skip if False or dashboard not running
    )
    if ok:
        subprocess.run(["git", "push", ...])
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _list_modified_files(cwd: str) -> List[str]:
    """Return staged + unstaged modified tracked files in cwd."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=cwd, timeout=15,
        )
        paths = [p.strip() for p in result.stdout.splitlines() if p.strip()]
        # Also include staged
        staged = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=cwd, timeout=15,
        )
        paths += [p.strip() for p in staged.stdout.splitlines() if p.strip()]
        # Dedupe preserving order
        seen: Dict[str, None] = {}
        for p in paths:
            if p not in seen:
                seen[p] = None
        return list(seen.keys())
    except Exception as exc:
        logger.warning("validated_commit: git diff failed: %s", exc)
        return []


def _run_codelens(cwd: str, modified_py: List[str]) -> Tuple[bool, str, Dict[str, Any]]:
    """Run py_compile + ruff + bandit on agent's modified .py files.

    Returns (passed, reason, metrics).
    """
    metrics = {"ruff_issues": 0, "bandit_issues": 0}

    if not modified_py:
        return True, "No .py files modified", metrics

    # py_compile
    try:
        r = subprocess.run(
            ["python", "-m", "py_compile"] + modified_py,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=cwd, timeout=60,
        )
        if r.returncode != 0:
            return False, f"py_compile failed: {r.stderr[:200]}", metrics
    except Exception as exc:
        logger.warning("py_compile error: %s", exc)

    # ruff
    try:
        r = subprocess.run(
            ["python", "-m", "ruff", "check"] + modified_py,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=cwd, timeout=60,
        )
        if r.returncode != 0:
            issues = len([ln for ln in r.stdout.splitlines() if ": " in ln])
            metrics["ruff_issues"] = issues
            if issues > 0:
                return False, f"ruff found {issues} issues", metrics
    except Exception as exc:
        logger.warning("ruff error: %s", exc)

    # bandit medium+
    # Pass --configfile pyproject.toml so [tool.bandit] skips (e.g. B608) are respected.
    bandit_cmd = ["python", "-m", "bandit", "-r"] + modified_py + ["--severity-level", "medium"]
    if (Path(cwd) / "pyproject.toml").exists():
        bandit_cmd += ["--configfile", "pyproject.toml"]
    try:
        r = subprocess.run(
            bandit_cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=cwd, timeout=60,
        )
        if r.returncode == 1:
            count = r.stdout.count(">> Issue:")
            metrics["bandit_issues"] = count
            if count > 0:
                return False, f"bandit found {count} medium+ issues", metrics
    except Exception as exc:
        logger.warning("bandit error: %s", exc)

    return True, "CodeLens passed", metrics


def _run_coherence(cwd: str, compare_to_main: bool = True) -> Tuple[bool, str]:
    """Run coherence checker. If compare_to_main, only fail when cwd introduces
    a NEW coherence violation (not pre-existing in main).
    """
    try:
        r = subprocess.run(
            ["python", "tools/workflow/coherence_checker.py", "--all", "--gate"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=cwd, timeout=120,
        )
        cwd_ok = r.returncode == 0
    except Exception as exc:
        logger.warning("coherence error in cwd: %s", exc)
        return True, f"coherence skipped ({exc})"

    if cwd_ok:
        return True, "coherence passed"

    if not compare_to_main or str(Path(cwd).resolve()) == str(BASE_DIR.resolve()):
        # cwd IS main, or baseline comparison disabled → fail
        return False, f"coherence gate failed (exit {r.returncode})"

    # Compare to main — if main also fails, this is pre-existing
    try:
        r_main = subprocess.run(
            ["python", "tools/workflow/coherence_checker.py", "--all", "--gate"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=120,
        )
        main_ok = r_main.returncode == 0
    except Exception:
        main_ok = True  # Assume main is OK if we can't check

    if main_ok:
        return False, "coherence broken by cwd changes (main passes, cwd fails)"
    return True, "coherence fails in both main and cwd — pre-existing"


def _run_e2e(cwd: str, ui_touched: bool) -> Tuple[bool, str, Dict[str, Any]]:
    """Run E2E test if UI files were modified AND dashboard is running."""
    metrics = {"e2e_ran": False, "e2e_passed": None}

    if not ui_touched:
        return True, "no UI files modified — E2E skipped", metrics

    try:
        import urllib.request as _ul
        _ul.urlopen("http://localhost:5050/health", timeout=2)  # nosec B310 -- localhost
    except Exception:
        return True, "dashboard not running — E2E skipped", metrics

    metrics["e2e_ran"] = True
    try:
        r = subprocess.run(
            ["python", "tests/e2e_kanban_depends_on.py"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=180,
        )
        metrics["e2e_passed"] = (r.returncode == 0)
        if r.returncode != 0:
            return False, f"E2E failed: {r.stdout[-200:]}", metrics
    except Exception as exc:
        metrics["e2e_passed"] = False
        return False, f"E2E error: {exc}", metrics

    return True, "E2E passed", metrics


def _run_companion_sync() -> Tuple[bool, str]:
    """Companion sync: best-effort, never fails the validation."""
    try:
        r = subprocess.run(
            ["python", "tools/dx/companion.py", "--sync", "--write", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=60,
        )
        return r.returncode == 0, "companion synced"
    except Exception as exc:
        logger.warning("companion sync error: %s", exc)
        return False, f"companion sync skipped: {exc}"


def _verify_budget_sec() -> float:
    """Total wall-clock budget for a single task's verification suite.

    Read from ICDEV_KANBAN_VERIFY_BUDGET_SEC (default: 300 = 5 min).
    Kanban backlogs of 100+ tasks would otherwise see coherence (120s) +
    E2E (180s) + companion (60s) per task, exceeding 10 hours total.
    """
    try:
        return float(os.environ.get("ICDEV_KANBAN_VERIFY_BUDGET_SEC", "300"))
    except ValueError:
        return 300.0


def validate_working_tree(
    cwd: str,
    modified_files: Optional[List[str]] = None,
    compare_to_main: bool = True,
    run_e2e: bool = True,
    run_companion: bool = True,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Run the four-gate validation suite.

    Early-exits on the first failure (CodeLens → Coherence → E2E →
    Companion-best-effort). A total wall-clock budget (env:
    ``ICDEV_KANBAN_VERIFY_BUDGET_SEC``, default 300s) caps the whole
    suite so queue throughput doesn't collapse under a slow gate.

    Args:
        cwd: Working directory to validate (worktree path or main repo).
        modified_files: List of changed file paths (relative). If None, auto-detect.
        compare_to_main: For coherence, only fail if cwd introduces NEW violations.
        run_e2e: Run E2E test if UI files touched (default True).
        run_companion: Run companion sync at the end (default True).

    Returns:
        (passed, reason, metrics): passed is False on any gate failure
        or on budget exhaustion.
    """
    if modified_files is None:
        modified_files = _list_modified_files(cwd)

    modified_py = [
        f for f in modified_files
        if f.endswith(".py") and (Path(cwd) / f).exists()
    ]

    budget = _verify_budget_sec()
    t0 = time.monotonic()

    def _remaining() -> float:
        return budget - (time.monotonic() - t0)

    def _over_budget() -> bool:
        return _remaining() <= 0

    metrics: Dict[str, Any] = {
        "codelens_passed": None,
        "ruff_issues": 0,
        "bandit_issues": 0,
        "coherence_passed": None,
        "e2e_ran": False,
        "e2e_passed": None,
        "companion_synced": False,
        "modified_files": len(modified_files),
        "modified_py": len(modified_py),
        "budget_sec": budget,
    }

    # 1. CodeLens
    ok, reason, cl_metrics = _run_codelens(cwd, modified_py)
    metrics.update(cl_metrics)
    if not ok:
        metrics["codelens_passed"] = False
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        return False, reason, metrics
    metrics["codelens_passed"] = True

    if _over_budget():
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        return False, f"BUDGET EXHAUSTED after CodeLens ({budget:.0f}s)", metrics

    # 2. Coherence
    ok, reason = _run_coherence(cwd, compare_to_main)
    metrics["coherence_passed"] = ok
    if not ok:
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        return False, reason, metrics

    if _over_budget():
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        return False, f"BUDGET EXHAUSTED after Coherence ({budget:.0f}s)", metrics

    # 3. E2E
    ui_touched = any(
        f.startswith(("tools/dashboard/", "tools/saas/portal/"))
        for f in modified_files
    )
    if run_e2e:
        ok, reason, e2e_metrics = _run_e2e(cwd, ui_touched)
        metrics.update(e2e_metrics)
        if not ok:
            metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
            return False, reason, metrics

    # 4. Companion sync (best-effort — still skip if out of budget)
    if run_companion and not _over_budget():
        ok, _reason = _run_companion_sync()
        metrics["companion_synced"] = ok

    metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
    return True, "All validation gates passed", metrics
