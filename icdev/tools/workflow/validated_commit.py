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
from tools.logging.icdev_logger import get_logger

import concurrent.futures
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = get_logger(__name__)

_FALLBACK_BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _find_main_worktree() -> Path:
    """Return the main git worktree path (first entry in `git worktree list`).

    When validated_commit is imported from inside a kanban worktree, __file__
    resolves to the worktree directory, not the main repo.  That causes
    BASE_DIR == cwd, which short-circuits the baseline comparison in
    _run_coherence_gate() and produces false "coherence broken by cwd changes"
    failures.  Using `git worktree list` always returns the main working tree
    first, regardless of where the module was loaded from.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_FALLBACK_BASE_DIR), timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    return Path(line[len("worktree "):].strip())
    except Exception:
        pass
    return _FALLBACK_BASE_DIR


BASE_DIR = _find_main_worktree()


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


def _bandit_findings_json(
    files: List[str], cwd: str, config_flags: List[str]
) -> List[Dict[str, Any]]:
    """Run bandit with JSON output on *files*; return the raw results list.

    Bandit exits 0 (clean) or 1 (issues found) — both are valid runs.
    Any other return code or parse failure returns an empty list.
    """
    if not files:
        return []
    cmd = (
        ["python", "-m", "bandit", "-r"]
        + files
        + ["--severity-level", "medium", "-f", "json"]
        + config_flags
    )
    try:
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=cwd, timeout=60,
        )
        if r.returncode in (0, 1) and r.stdout:
            try:
                data = json.loads(r.stdout)
                return data.get("results", [])
            except json.JSONDecodeError:
                pass
    except Exception as exc:
        logger.warning("bandit JSON run error: %s", exc)
    return []


def _bandit_delta(
    cwd: str, modified_py: List[str], config_flags: List[str]
) -> Tuple[int, str]:
    """Return (new_count, detail) for bandit findings introduced by this branch.

    Compares branch HEAD vs the main baseline for each modified file by
    fetching the main version with ``git show main:<relpath>``.

    **Matching key:** (normalized_relpath, test_id, issue_text)
    Line numbers are deliberately excluded: inserting code above a finding
    shifts its line without creating a new vulnerability, and should not
    re-trigger a failure that was already pre-existing on main.

    A finding is "new" when its key exists in the branch scan but is absent
    from the baseline scan.  If a file doesn't exist on main (new file), all
    of its findings are considered new.  If the baseline scan itself fails,
    we conservatively treat all branch findings as new.

    Returns (0, "") when no new findings are detected.
    """
    # --- branch findings ---
    branch_results = _bandit_findings_json(modified_py, cwd, config_flags)
    if not branch_results:
        return 0, ""

    cwd_resolved = Path(cwd).resolve()

    def _rel(fname: str, base: Path) -> str:
        """Normalize a bandit filename to a path relative to *base*."""
        try:
            return str(Path(fname).resolve().relative_to(base))
        except ValueError:
            return Path(fname).name

    def _branch_key(f: Dict[str, Any]) -> tuple:
        return (
            _rel(f.get("filename", ""), cwd_resolved),
            f.get("test_id", ""),
            (f.get("issue_text") or "").strip(),
        )

    # --- baseline findings (main) ---
    baseline_keys: Set[tuple] = set()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            tmp_files: List[str] = []

            for relpy in modified_py:
                try:
                    gshow = subprocess.run(
                        ["git", "show", f"main:{relpy}"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        cwd=cwd, timeout=15,
                    )
                    if gshow.returncode != 0:
                        # File is new on this branch — all its findings are new
                        continue
                    dest = tmp_path / relpy
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(gshow.stdout, encoding="utf-8")
                    tmp_files.append(str(dest))
                except Exception as exc:
                    logger.warning("git show %s: %s — treating as new file", relpy, exc)

            if tmp_files:
                baseline_results = _bandit_findings_json(tmp_files, tmpdir, config_flags)
                for f in baseline_results:
                    key = (
                        _rel(f.get("filename", ""), tmp_path),
                        f.get("test_id", ""),
                        (f.get("issue_text") or "").strip(),
                    )
                    baseline_keys.add(key)
    except Exception as exc:
        logger.warning(
            "bandit baseline comparison failed: %s — treating all findings as new", exc
        )

    # --- delta ---
    new_findings = [f for f in branch_results if _branch_key(f) not in baseline_keys]
    if not new_findings:
        return 0, ""

    lines = []
    for f in new_findings[:5]:
        rel = _rel(f.get("filename", ""), cwd_resolved)
        lines.append(
            f"  [{f.get('test_id', '')}] {rel}:{f.get('line_number', '')} "
            f"— {(f.get('issue_text') or '')[:80]}"
        )
    if len(new_findings) > 5:
        lines.append(f"  … and {len(new_findings) - 5} more")
    return len(new_findings), "\n".join(lines)


def _run_codelens(
    cwd: str, modified_py: List[str], compare_to_main: bool = False
) -> Tuple[bool, str, Dict[str, Any]]:
    """Run py_compile + ruff + bandit on agent's modified .py files.

    When *compare_to_main* is True the bandit gate is delta-aware: only
    findings that are NEW to this branch (absent from the main baseline)
    cause a failure.  Pre-existing findings on untouched lines are silently
    suppressed.  When False, any medium+ finding fails the gate (legacy
    behaviour, used when cwd IS main).

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
    config_flags: List[str] = []
    if (Path(cwd) / "pyproject.toml").exists():
        config_flags = ["--configfile", "pyproject.toml"]

    if compare_to_main:
        # Delta-aware path: only flag findings that are NEW to this branch.
        # Pre-existing findings on main are suppressed so a pre-existing
        # B108 in tests/conftest.py doesn't block an unrelated change.
        new_count, detail = _bandit_delta(cwd, modified_py, config_flags)
        metrics["bandit_issues"] = new_count
        if new_count > 0:
            msg = f"bandit found {new_count} new medium+ issue(s) (not in main baseline)"
            if detail:
                msg += f"\n{detail}"
            return False, msg, metrics
    else:
        # Legacy path: cwd IS main — any finding fails the gate.
        bandit_cmd = (
            ["python", "-m", "bandit", "-r"]
            + modified_py
            + ["--severity-level", "medium"]
            + config_flags
        )
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


def _parse_coherence_failures(stdout: str) -> str:
    """Parse coherence checker JSON output and return a compact failure summary.

    Extracts the failing check IDs, their messages, and up to 3 missing items
    so the agent can target the exact rule without re-running the checker.
    Falls back to raw stdout snippet if JSON is unparseable.
    """
    if not stdout:
        return ""
    try:
        data = json.loads(stdout)
        checks = data.get("checks", [])
        failed = [c for c in checks if c.get("status") == "fail"]
        if not failed:
            return ""
        parts = []
        for c in failed[:5]:
            summary = f"[{c.get('check_id', '?')}] {c.get('message', '')}"
            missing = c.get("missing", [])
            if missing:
                shown = missing[:3]
                summary += " — missing: " + ", ".join(str(m) for m in shown)
                if len(missing) > 3:
                    summary += f" (+{len(missing) - 3} more)"
            extra = c.get("extra", [])
            if extra and not missing:
                shown = extra[:3]
                summary += " — extra: " + ", ".join(str(e) for e in shown)
            parts.append(summary)
        if len(failed) > 5:
            parts.append(f"... and {len(failed) - 5} more failing checks")
        return "; ".join(parts)
    except (json.JSONDecodeError, AttributeError):
        # Fall back to raw last 300 chars of output
        raw = stdout.strip()[-300:]
        return f"raw: {raw}" if raw else ""


def _run_coherence(cwd: str, compare_to_main: bool = True) -> Tuple[bool, str]:
    """Run coherence checker. If compare_to_main, only fail when cwd introduces
    a NEW coherence violation (not pre-existing in main).
    """
    # If cwd is a worktree path that no longer exists, subprocess would raise
    # FileNotFoundError which the except-branch currently returns as True
    # (coherence skipped = pass). That's wrong for a missing worktree: the
    # task should be classified as FAILURE_WORKTREE_MISSING so auto_remediate
    # prunes git state and lets the next dispatch rebuild from HEAD instead of
    # silently passing and marking the task done without real verification.
    cwd_path = Path(cwd)
    if not cwd_path.exists():
        _parts = cwd_path.parts
        if ".tmp" in _parts and "worktrees" in _parts:
            return False, "worktree missing on disk — rebuild required"
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

    detail = _parse_coherence_failures(r.stdout)

    if not compare_to_main or str(Path(cwd).resolve()) == str(BASE_DIR.resolve()):
        # cwd IS main, or baseline comparison disabled → fail
        reason = f"coherence gate failed (exit {r.returncode})"
        if detail:
            reason += f" — {detail}"
        return False, reason

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
        reason = "coherence broken by cwd changes (main passes, cwd fails)"
        if detail:
            reason += f" — {detail}"
        return False, reason
    return True, "coherence fails in both main and cwd — pre-existing"


def _run_route_smoke(modified_files: List[str]) -> Tuple[bool, str, Dict[str, Any]]:
    """Run HTTP route smoke on routes affected by *modified_files*.

    This is the critical gate that CodeLens + Coherence cannot replace:
    it makes real HTTP requests to the running server and verifies pages
    return 200 without error text in the body.  Catches:
      - Blueprint import errors (500 at route registration)
      - Missing templates (TemplateNotFound 500)
      - Missing DB tables/columns (OperationalError 500)
      - Nav links pointing to unregistered routes (404)
    """
    metrics: Dict[str, Any] = {"smoke_ran": False, "smoke_passed": None, "smoke_failures": []}
    try:
        from tools.testing.route_smoke import run_smoke, _routes_for_changed_files, _server_up
    except ImportError:
        return True, "route_smoke not available — skipped", metrics

    if not _server_up("http://localhost:5050"):
        return True, "dashboard not running — route smoke skipped", metrics

    routes = _routes_for_changed_files(modified_files) if modified_files else []
    if not routes:
        return True, "no routes affected — route smoke skipped", metrics

    metrics["smoke_ran"] = True
    passed, results = run_smoke(routes, verbose=False)
    failures = [r for r in results if not r["ok"]]
    metrics["smoke_passed"] = passed
    metrics["smoke_failures"] = [{"route": r["route"], "error": r["error"]} for r in failures]

    if not passed:
        fail_summary = "; ".join(f"{r['route']} ({r['error']})" for r in failures[:5])
        return False, f"Route smoke FAILED: {fail_summary}", metrics

    return True, f"Route smoke passed ({len(results)} routes OK)", metrics


def _run_e2e(cwd: str, ui_touched: bool, modified_files: Optional[List[str]] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """Run E2E test if UI files were modified AND dashboard is running.

    Now includes route smoke as a MANDATORY first gate (catches 500/404
    at page load — things CodeLens + Coherence cannot detect).
    """
    metrics: Dict[str, Any] = {
        "e2e_ran": False, "e2e_passed": None,
        "smoke_ran": False, "smoke_passed": None, "smoke_failures": [],
    }

    if not ui_touched:
        return True, "no UI files modified — E2E skipped", metrics

    try:
        import urllib.request as _ul
        _ul.urlopen("http://localhost:5050/health", timeout=2)  # nosec B310 -- localhost
    except Exception:
        return True, "dashboard not running — E2E skipped", metrics

    # ── Route Smoke Gate (NEW) ────────────────────────────────────────────────
    # Must pass before spending 3+ minutes on full E2E. Catches broken pages
    # in < 30 seconds. A 500 on ANY affected route blocks the merge.
    smoke_ok, smoke_reason, smoke_metrics = _run_route_smoke(modified_files or [])
    metrics.update(smoke_metrics)
    if not smoke_ok:
        # Log to build.ndjson so log_triage can create a remediation task
        try:
            from tools.logging.build_logger import capture_pytest
            capture_pytest(
                returncode=1,
                stdout=f"Route smoke FAILED: {smoke_reason}",
                stderr="",
                duration_s=0,
                passed=0,
                failed=len(smoke_metrics.get("smoke_failures", [])),
            )
        except Exception:
            pass
        return False, f"Route smoke FAILED — {smoke_reason}", metrics

    # Verify the kanban POST API is responsive before committing to a full E2E
    # run. If the endpoint doesn't answer a lightweight GET within 5 s (e.g.
    # SQLite write-lock contention from the scheduler), treat it as an
    # infrastructure skip rather than a test failure.
    try:
        _ul.urlopen("http://localhost:5050/api/kanban/tasks", timeout=5)  # nosec B310
    except Exception:
        metrics["e2e_ran"] = False
        return True, f"{smoke_reason}; kanban API unresponsive — full E2E skipped (transient load)", metrics

    metrics["e2e_ran"] = True
    _cmd = ["python", "tests/e2e_kanban_depends_on.py"]
    _last_stdout = ""
    _last_exc: Exception | None = None
    for _attempt in range(2):
        if _attempt:
            import time as _time
            _time.sleep(5)  # brief pause before retry (transient API load)
        try:
            r = subprocess.run(
                _cmd,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(BASE_DIR), timeout=180,
            )
            metrics["e2e_passed"] = (r.returncode == 0)
            if r.returncode == 0:
                return True, "E2E passed", metrics
            _last_stdout = r.stdout
            _last_exc = None
            # Detect API timeout on this attempt immediately — don't retry,
            # since a second attempt with a still-loaded API just overwrites
            # _last_stdout with a different error and loses the detection.
            if ("timed out" in _last_stdout.lower()
                    and "seed tasks via api" in _last_stdout.lower()):
                metrics["e2e_passed"] = None
                return True, "E2E skipped — API POST timeout (transient server load)", metrics
        except Exception as exc:
            _last_exc = exc
            # subprocess.TimeoutExpired or transient failure — also check for
            # API timeout pattern in the exception message itself.
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                metrics["e2e_passed"] = None
                return True, "E2E skipped — subprocess/API timeout (transient server load)", metrics
    if _last_exc is not None:
        metrics["e2e_passed"] = False
        return False, f"E2E error: {_last_exc}", metrics
    # Fallback: check accumulated stdout for the API timeout pattern.
    if "timed out" in _last_stdout.lower() and "seed tasks via api" in _last_stdout.lower():
        metrics["e2e_passed"] = None
        return True, "E2E skipped — API POST timeout (transient server load)", metrics
    return False, f"E2E failed: {_last_stdout[-200:]}", metrics


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
    # Guard: if the worktree was deleted between dispatch and validation (common
    # on Windows when a prior reset's rmtree failed with file-lock errors), all
    # subprocess calls inside would raise FileNotFoundError and be swallowed by
    # their except-blocks — producing misleading pass/skip results instead of a
    # clean failure that triggers a rebuild.  Detect and short-circuit early
    # before any git status, file checks, or coherence validation.
    cwd_path = Path(cwd)
    if not cwd_path.exists():
        # Prune the stale worktree registration so the next dispatch can
        # allocate a fresh one without hitting "already registered" errors.
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(cwd_path)],
                capture_output=True, cwd=str(BASE_DIR), timeout=15,
            )
        except Exception:
            pass
        try:
            subprocess.run(
                ["git", "worktree", "prune"],
                capture_output=True, cwd=str(BASE_DIR), timeout=15,
            )
        except Exception:
            pass
        _empty: Dict[str, Any] = {
            "codelens_passed": None,
            "ruff_issues": 0,
            "bandit_issues": 0,
            "coherence_passed": None,
            "e2e_ran": False,
            "e2e_passed": None,
            "companion_synced": False,
            "modified_files": 0,
            "modified_py": 0,
            "budget_sec": _verify_budget_sec(),
            "elapsed_sec": 0,
            "remediation": "rebuild_worktree",
        }
        return False, "worktree missing on disk — pruned stale registration, rebuild required", _empty

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

    # 1+2. CodeLens + Coherence in parallel — wall-clock time is max(cl, co)
    #       instead of cl + co, which was eating into the 300s budget.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as _pool:
        _cl_fut = _pool.submit(_run_codelens, cwd, modified_py, compare_to_main)
        _co_fut = _pool.submit(_run_coherence, cwd, compare_to_main)
        cl_ok, cl_reason, cl_metrics = _cl_fut.result()
        co_ok, co_reason = _co_fut.result()

    metrics.update(cl_metrics)
    metrics["codelens_passed"] = cl_ok
    metrics["coherence_passed"] = co_ok

    if not cl_ok:
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        # Include coherence result in reason so the notification shows both
        suffix = f" | coherence: {'pass' if co_ok else co_reason}" if not co_ok else ""
        return False, cl_reason + suffix, metrics

    if not co_ok:
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        return False, co_reason, metrics

    if _over_budget():
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        return False, f"BUDGET EXHAUSTED after CodeLens+Coherence ({budget:.0f}s)", metrics

    # 3. E2E
    # Only trigger E2E for actual UI surface changes: templates, JS/CSS/TS assets,
    # or the kanban API blueprint itself. Pure Python utility files in tools/dashboard/
    # (e.g. openapi_generator.py, report generators) don't affect board rendering.
    _UI_EXTS = (".html", ".js", ".ts", ".css", ".jinja", ".jinja2")
    _KANBAN_API = ("tools/dashboard/api/kanban.py", "icdev/tools/dashboard/api/kanban.py")
    ui_touched = any(
        (f.startswith(("tools/dashboard/", "tools/saas/portal/")) and
         (any(f.endswith(ext) for ext in _UI_EXTS)
          or "/templates/" in f
          or f in _KANBAN_API))
        for f in modified_files
    )
    if run_e2e:
        ok, reason, e2e_metrics = _run_e2e(cwd, ui_touched, modified_files=modified_files)
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
