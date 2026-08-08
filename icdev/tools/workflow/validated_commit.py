# CUI // SP-CTI
"""Unified commit/validate/push pipeline shared by the stop hook and the
kanban scheduler.

Single source of truth for the four-gate validation suite:
  1. CodeLens  — py_compile + ruff + bandit on modified files
  2. Coherence — coherence_checker.py --tier fast --gate, diff-scoped, compared
                 per-check-id against a cached main baseline
  3. E2E       — Selenium lifecycle test if UI files modified
  4. Companion — sync to 10 platforms (best-effort, never fails)

The coherence gate runs the FAST tier: the three whole-app checks that
dominate its cost (blueprint_imports, openapi_parity, llm_router_api) are
deferred to the post-merge sweep unless the diff touches their trigger
surface. A cold kanban worktree measured 328s on the full tier against a 120s
subprocess budget, so the gate timed out — and returned a PASS — on every
task. See tools/genesis/reflexes/coherence_sweep.py for the full-tier sweep.

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
import re
import subprocess
import tempfile
import threading
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
                    dest.write_text(gshow.stdout, encoding="utf-8", newline="")
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


def _extract_report_json(stdout: str) -> Optional[Dict[str, Any]]:
    """Pull the coherence report out of stdout, tolerating leading noise.

    Modules imported by the heavy checks (canvas ``db/init_db.py``) print
    ``[init_db] ...`` banners at import time, so the JSON document is often not
    the first thing on the stream. Those prints now go to stderr, but a plain
    ``json.loads(stdout)`` silently turned ANY such regression into "no failure
    detail available" — so scan for the first line that opens a JSON object and
    parse from there as a permanent belt-and-braces.
    """
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        pass
    lines = stdout.splitlines()
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("{"):
            try:
                return json.loads("\n".join(lines[idx:]))
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def _failing_check_ids(report: Optional[Dict[str, Any]]) -> Set[str]:
    """Ids of every check reporting ``fail`` in *report*."""
    if not report:
        return set()
    return {
        str(c.get("check_id"))
        for c in report.get("checks", [])
        if c.get("status") == "fail"
    }


def _parse_coherence_failures(stdout: str, only: Optional[Set[str]] = None) -> str:
    """Parse coherence checker JSON output and return a compact failure summary.

    Extracts the failing check IDs, their messages, and up to 3 missing items
    so the agent can target the exact rule without re-running the checker.
    When *only* is given, restricts the summary to those check ids (used to
    report just the NEW failures a change introduced).
    Falls back to raw stdout snippet if JSON is unparseable.
    """
    if not stdout:
        return ""
    data = _extract_report_json(stdout)
    if data is not None:
        checks = data.get("checks", [])
        failed = [c for c in checks if c.get("status") == "fail"]
        if only is not None:
            failed = [c for c in failed if str(c.get("check_id")) in only]
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
    # Unparseable output — fall back to the raw tail so something reaches the agent.
    raw = stdout.strip()[-300:]
    return f"raw: {raw}" if raw else ""


# Coherence gate tuning. The gate runs the FAST tier (whole-app heavies are
# deferred to the post-merge sweep) so it finishes well inside its timeout; a
# cold kanban worktree measured 328s on the full tier against a 120s budget,
# which meant the gate timed out on literally every task.
_COHERENCE_TIERS = ("fast", "full")
_BASELINE_TTL_SEC = 86400


def _coherence_tier() -> str:
    tier = os.environ.get("ICDEV_COHERENCE_GATE_TIER", "fast").strip().lower()
    return tier if tier in _COHERENCE_TIERS else "fast"


def _coherence_timeout_sec() -> int:
    try:
        return max(30, int(os.environ.get("ICDEV_COHERENCE_TIMEOUT_SEC", "180")))
    except ValueError:
        return 180


# Windows caps a command line at ~8191 chars. Past this budget we stop passing
# the diff and ask for the full tier instead — an unscoped fast tier would skip
# the heavies without the diff evidence that justifies skipping them.
_MAX_CHANGED_FILES_ARGV = 5000


def _coherence_cmd(tier: str, changed_files: Optional[List[str]]) -> List[str]:
    """Build the checker argv, degrading to --tier full when the diff won't fit."""
    joined = ",".join(f for f in (changed_files or []) if f)
    if joined and len(joined) <= _MAX_CHANGED_FILES_ARGV:
        return [
            "python", "tools/workflow/coherence_checker.py",
            "--tier", tier, "--gate", "--changed-files", joined,
        ]
    if joined:
        logger.info(
            "coherence: %d changed files exceed the argv budget — running full tier",
            len(changed_files or []),
        )
        return ["python", "tools/workflow/coherence_checker.py", "--tier", "full", "--gate"]
    return ["python", "tools/workflow/coherence_checker.py", "--tier", tier, "--gate"]


def _run_cancellable(
    cmd: List[str],
    cwd: str,
    timeout: float,
    cancel_event: Optional[threading.Event] = None,
) -> Tuple[Optional[int], str, str]:
    """Run *cmd*, killable early via *cancel_event*.

    stdout/stderr go to temp files rather than pipes so a full pipe buffer can
    never deadlock the poll loop on a checker that prints thousands of lines.

    Returns ``(returncode, stdout, status)`` where status is one of
    ``ok`` / ``timeout`` / ``cancelled`` / ``error:...``. returncode is None
    unless status is ``ok``.
    """
    out_path = err_path = None
    try:
        fd_out, out_path = tempfile.mkstemp(suffix=".coh.out")
        fd_err, err_path = tempfile.mkstemp(suffix=".coh.err")
        status = "ok"
        with os.fdopen(fd_out, "w", encoding="utf-8") as fo, \
                os.fdopen(fd_err, "w", encoding="utf-8") as fe:
            proc = subprocess.Popen(  # nosec B603 — fixed argv, shell=False
                cmd, cwd=cwd, stdout=fo, stderr=fe,
            )
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    status = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    status = "timeout"
                    break
                time.sleep(0.25)
            if status != "ok":
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    pass
        stdout = Path(out_path).read_text(encoding="utf-8", errors="replace")
        return (proc.returncode if status == "ok" else None), stdout, status
    except Exception as exc:
        logger.warning("coherence subprocess error: %s", exc)
        return None, "", f"error:{exc}"
    finally:
        for path in (out_path, err_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _main_head_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(BASE_DIR), timeout=10,
        )
        if r.returncode == 0:
            return r.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def _main_baseline_failures(tier: str, timeout: float) -> Optional[Set[str]]:
    """Check ids already failing on the main checkout, cached per main HEAD.

    The baseline only moves when main moves, but it used to be recomputed for
    every single task — a second full coherence run per validation. Cache it on
    disk (``.tmp/`` is gitignored) keyed by the main HEAD sha so a queue of N
    tasks pays for it once instead of N times.

    Returns None when the baseline could not be established.
    """
    sha = _main_head_sha()
    cache = BASE_DIR / ".tmp" / f"coherence_baseline_{tier}_{sha[:12]}.json"
    try:
        if cache.exists() and (time.time() - cache.stat().st_mtime) < _BASELINE_TTL_SEC:
            data = json.loads(cache.read_text(encoding="utf-8"))
            return set(data.get("failing", []))
    except Exception as exc:
        logger.debug("coherence baseline cache unreadable: %s", exc)

    _rc, stdout, status = _run_cancellable(
        _coherence_cmd(tier, None), str(BASE_DIR), timeout
    )
    if status != "ok":
        logger.warning("coherence baseline on main not established: %s", status)
        return None
    report = _extract_report_json(stdout)
    if report is None:
        logger.warning("coherence baseline on main: unparseable checker output")
        return None
    failing = _failing_check_ids(report)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({"sha": sha, "tier": tier, "failing": sorted(failing)}),
            encoding="utf-8", newline="",
        )
    except Exception as exc:
        logger.debug("coherence baseline cache not written: %s", exc)
    return failing


def _run_coherence(
    cwd: str,
    compare_to_main: bool = True,
    changed_files: Optional[List[str]] = None,
    cancel_event: Optional[threading.Event] = None,
    timeout: Optional[float] = None,
    tier: Optional[str] = None,
) -> Tuple[Optional[bool], str]:
    """Run the coherence gate against *cwd*.

    *tier* overrides ICDEV_COHERENCE_GATE_TIER — phase-exit gates ask for
    ``full`` because they validate a whole phase, not one task's diff.

    Returns ``(passed, reason)`` where passed is:
      * ``True``  — no NEW failing check relative to the main baseline
      * ``False`` — cwd introduces at least one check main does not fail
      * ``None``  — the gate could not be evaluated (timeout / cancelled /
        unparseable output). This is deliberately NOT True: the previous
        version returned a pass on timeout, which recorded ``coherence_passed=1``
        for tasks whose coherence had never actually run.

    Comparison is per-check-id, not binary. The old code asked only "does main
    also fail?" — so a single unrelated pre-existing failure on main (there was
    one) turned the entire gate into an unconditional pass.
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

    tier = (tier or _coherence_tier()).strip().lower()
    if tier not in _COHERENCE_TIERS:
        tier = "fast"
    budget = float(timeout or _coherence_timeout_sec())

    rc, stdout, status = _run_cancellable(
        _coherence_cmd(tier, changed_files), cwd, budget, cancel_event
    )
    if status == "cancelled":
        return None, "coherence cancelled — an earlier gate already failed"
    if status == "timeout":
        return None, (
            f"coherence NOT EVALUATED — exceeded {budget:.0f}s on tier '{tier}'"
        )
    if status.startswith("error"):
        return None, f"coherence NOT EVALUATED — {status}"

    report = _extract_report_json(stdout)
    if report is None:
        return None, "coherence NOT EVALUATED — unparseable checker output"

    cwd_failing = _failing_check_ids(report)
    if not cwd_failing:
        return True, f"coherence passed (tier={tier}, exit={rc})"

    detail = _parse_coherence_failures(stdout)

    if not compare_to_main or str(cwd_path.resolve()) == str(BASE_DIR.resolve()):
        # cwd IS main, or baseline comparison disabled → fail
        reason = f"coherence gate failed (exit {rc})"
        if detail:
            reason += f" — {detail}"
        return False, reason

    baseline = _main_baseline_failures(tier, budget)
    if baseline is None:
        # Neither "broken by this change" nor "pre-existing" is provable.
        return None, (
            "coherence NOT EVALUATED — cwd failed but the main baseline could "
            f"not be established; cwd failures: {', '.join(sorted(cwd_failing))}"
        )

    new_failures = cwd_failing - baseline
    if not new_failures:
        return True, (
            f"coherence: {len(cwd_failing)} pre-existing failure(s) on main, "
            "none introduced here"
        )
    return False, (
        "coherence broken by this change — NEW: "
        + ", ".join(sorted(new_failures))
        + (f" — {_parse_coherence_failures(stdout, only=new_failures)}" if stdout else "")
    )


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


# Broad-change fallback, in preference order. Deliberately the two cheapest
# whole-app specs (7 + 3 tests) rather than the ~15-minute full suite: the point
# is that SOMETHING relevant runs, inside a budget the caller actually has.
_BROAD_UI_SMOKE_SPECS = ("key_pages_smoke.spec.ts", "nav_smoke.spec.ts")


def _playwright_specs_for_changed_files(modified_files: Optional[List[str]]) -> List[str]:
    """Map changed UI files -> relevant tests/e2e/<slug>*.spec.ts spec(s).

    Modeled on route_smoke._routes_for_changed_files. Returns [] for backend-only
    diffs. Capped at 2 specs, because the full suite is ~15 minutes — far past
    this caller's budget, and the kanban agent's whole wall-clock allowance.

    A UI change that maps to NOTHING used to fall through to the Selenium
    fallback, which runs one kanban-depends-on test touching none of the changed
    pages — so the task reported "E2E verification" having exercised nothing
    relevant. Two cases caused that and both now resolve to the broad SMOKE
    specs (``key_pages_smoke`` 7 tests + ``nav_smoke`` 3, seconds not minutes):

    * **too-broad changes** (``app.py``, ``base.html``) affect every page, so no
      single slug is right — but "every page" is exactly what a smoke spec is.
    * **unmatched slugs**, e.g. ``templates/index.html`` -> slug ``index``, and
      there is no ``index*.spec.ts``. Measured against recent merges, this was
      half the UI commits sampled.
    """
    files = modified_files or []
    specs_dir = BASE_DIR / "tests" / "e2e"
    if not specs_dir.is_dir():
        return []
    ui_exts = (".html", ".js", ".ts", ".css", ".jinja", ".jinja2")
    slugs: Set[str] = set()
    saw_ui = False
    for f in files:
        fp = f.replace("\\", "/")
        # Too broad for a slug match — fall to the smoke specs below, not to
        # Selenium. Keep scanning: a diff touching app.py AND a canvas template
        # should still run that canvas's spec.
        if fp.endswith("app.py") or fp.endswith("/base.html") or "/templates/base.html" in fp:
            saw_ui = True
            continue
        is_ui = any(fp.endswith(e) for e in ui_exts) or "/templates/" in fp or fp.endswith("blueprint.py")
        if is_ui:
            saw_ui = True
        if not is_ui:
            continue
        parts = fp.split("/")
        # template subdir: .../templates/<slug>/...
        if "templates" in parts:
            i = parts.index("templates")
            if i + 1 < len(parts) - 1:
                slugs.add(parts[i + 1])
        # canvas dir: tools/<canvas>/... (skip the generic "dashboard" bucket)
        if len(parts) > 2 and parts[0] in ("tools", "icdev"):
            idx = 1 if parts[0] == "tools" else 2
            if idx < len(parts) and parts[idx] not in ("dashboard",):
                slugs.add(parts[idx])
        # basename slug: kanban.html -> kanban, task_pipeline.js -> task_pipeline
        base = parts[-1]
        for e in ui_exts:
            if base.endswith(e):
                slugs.add(base[: -len(e)])
                break
    specs: List[str] = []
    for slug in sorted(slugs):
        if not slug or not re.match(r"^[a-z0-9_]+$", slug):
            continue
        for cand in sorted(specs_dir.glob(slug + "*.spec.ts")):
            p = str(cand)
            if cand.is_file() and p not in specs:
                specs.append(p)

    # UI changed but nothing matched: run the broad smoke specs rather than
    # handing the task to a Selenium test that exercises none of it.
    if saw_ui and not specs:
        for name in _BROAD_UI_SMOKE_SPECS:
            cand = specs_dir / name
            if cand.is_file():
                specs.append(str(cand))
    return specs[:2]


def _run_playwright(cwd: str, specs: List[str], time_budget: float) -> Tuple[Optional[bool], str, Dict[str, Any]]:
    """Run the mapped Playwright spec(s) via e2e_runner.run_playwright_native.

    Returns (passed | None, reason, metrics). None = not_run (unavailable /
    npx or browser missing / timeout / error) → caller falls back to Selenium.
    NEVER raises. ``cwd``/``time_budget`` are advisory (the runner uses its own
    PROJECT_ROOT + internal timeout for a single spec).
    """
    if not specs:
        return None, "no playwright spec mapped", {}
    try:
        from tools.testing.e2e_runner import run_playwright_native
    except Exception as exc:
        return None, f"playwright runner import failed: {exc}", {}
    ran_any = False
    all_pass = True
    details: List[str] = []
    for spec in specs:
        run_id = "pipeline-e2e-" + os.path.basename(spec).replace(".spec.ts", "")
        try:
            results = run_playwright_native(run_id, logger, test_file=spec)
        except Exception as exc:
            return None, f"playwright unavailable/skipped: {exc}", {}
        if not results:
            return None, "playwright produced no results (npx/browser missing?)", {}
        ran_any = True
        spec_pass = all(getattr(r, "passed", False) for r in results)
        all_pass = all_pass and spec_pass
        details.append(os.path.basename(spec) + ":" + ("pass" if spec_pass else "fail"))
    if not ran_any:
        return None, "playwright did not run", {}
    return all_pass, "; ".join(details), {}


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

    # ── Per-task Playwright (preferred E2E engine) ────────────────────────────
    # Run the Playwright spec(s) mapped to the task's changed files. Falls through
    # to the existing Selenium test below when no spec maps or Playwright can't
    # run (npx/browser missing, error, timeout). A Playwright FAILURE blocks the
    # task ONLY when KANBAN_PIPELINE_ENFORCE is on; otherwise it's record-only.
    _pw_specs = _playwright_specs_for_changed_files(modified_files)
    if _pw_specs:
        _pw_passed, _pw_reason, _pw_metrics = _run_playwright(cwd, _pw_specs, 120.0)
        if _pw_passed is not None:  # Playwright actually ran → it's the E2E verdict
            metrics["e2e_ran"] = True
            metrics["e2e_passed"] = _pw_passed
            metrics["e2e_engine"] = "playwright"
            if _pw_passed:
                return True, f"E2E Playwright passed ({_pw_reason})", metrics
            if _pipeline_enforce():
                return False, f"E2E Playwright FAILED: {_pw_reason}", metrics
            return True, f"E2E Playwright failed (record-only; {_pw_reason})", metrics
        # not_run → fall through to the existing Selenium path (unchanged)
        metrics["e2e_engine"] = "selenium"
    else:
        metrics["e2e_engine"] = "selenium"

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


def _pipeline_enforce() -> bool:
    """Governed Delivery Pipeline enforcement switch. Default OFF: the Phase 2
    gates (pytest, conformance) RUN and RECORD their result but never block a
    task's completion. Flip KANBAN_PIPELINE_ENFORCE=1 to make them blocking."""
    return os.environ.get("KANBAN_PIPELINE_ENFORCE", "0").lower() in ("1", "true", "yes")


def _run_pytest(
    cwd: str, modified_files: List[str], time_budget: float
) -> Tuple[Optional[bool], List[str]]:
    """Unit-test gate — runs pytest ONLY on the task's changed test files (never
    the whole suite, which would blow the budget). Returns (passed, failed_ids):
      - (None, []) when no test files changed or pytest is unavailable → not_run
      - (True/False, [...]) on a real run.
    Never raises; degrades to not_run."""
    test_files = [
        f for f in modified_files
        if f.endswith(".py") and (
            "/test_" in f.replace("\\", "/")
            or Path(f).name.startswith("test_")
            or f.replace("\\", "/").startswith("tests/")
        )
    ]
    if not test_files:
        return None, []
    timeout = max(15, min(120, int(time_budget)))
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = cwd
        env.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")
        proc = subprocess.run(  # nosec B603 — fixed args, shell=False
            ["python", "-m", "pytest", *test_files, "-q", "--no-header",
             "-p", "no:cacheprovider", "-o", "addopts="],
            cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=env,
        )
    except Exception as exc:
        logger.debug("pytest gate: run skipped (%s)", exc)
        return None, []
    out = (proc.stdout or "") + (proc.stderr or "")
    failed = re.findall(r"^FAILED (\S+)", out, flags=re.MULTILINE)
    return (proc.returncode == 0), failed[:20]


def validate_working_tree(
    cwd: str,
    modified_files: Optional[List[str]] = None,
    compare_to_main: bool = True,
    run_e2e: bool = True,
    run_companion: bool = True,
    budget_sec: Optional[float] = None,
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
        budget_sec: Wall-clock cap for the whole suite. Defaults to
            ``ICDEV_KANBAN_VERIFY_BUDGET_SEC``; the kanban runner passes a
            value derived from the task's dispatch budget so validation can
            never consume the time the build needs.

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

    budget = float(budget_sec) if budget_sec else _verify_budget_sec()
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

    # 1+2. CodeLens + Coherence concurrently — wall-clock is max(cl, co) rather
    #       than cl + co. CodeLens is the cheap gate and runs on this thread so
    #       that when it fails we CANCEL the coherence subprocess instead of
    #       blocking on it: a 4s CodeLens failure used to still wait out
    #       coherence's full timeout before returning.
    _cancel = threading.Event()
    _co_budget = max(30.0, min(float(_coherence_timeout_sec()), _remaining()))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
        _co_fut = _pool.submit(
            _run_coherence, cwd, compare_to_main, modified_files, _cancel, _co_budget,
        )
        cl_ok, cl_reason, cl_metrics = _run_codelens(cwd, modified_py, compare_to_main)
        if not cl_ok:
            _cancel.set()
        co_ok, co_reason = _co_fut.result()

    metrics.update(cl_metrics)
    metrics["codelens_passed"] = cl_ok
    # co_ok is tri-state: True / False / None ("not evaluated"). None is stored
    # as NULL so a skipped gate is never laundered into a recorded pass.
    metrics["coherence_passed"] = co_ok
    if co_ok is not True:
        metrics["coherence_violations"] = co_reason[:1000]

    if not cl_ok:
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        # Include coherence result in reason so the notification shows both
        suffix = "" if co_ok is True else f" | coherence: {co_reason}"
        return False, cl_reason + suffix, metrics

    if co_ok is False:
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        return False, co_reason, metrics

    if _over_budget():
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        return False, f"BUDGET EXHAUSTED after CodeLens+Coherence ({budget:.0f}s)", metrics

    # 2b. Unit tests (Governed Delivery Pipeline Phase 2) — pytest on the task's
    #     changed test files only. RECORD-ONLY by default: a failure blocks the
    #     task ONLY when KANBAN_PIPELINE_ENFORCE is on. not_run when no test
    #     files changed. Existing gates above are unaffected.
    pytest_passed, failed_tests = _run_pytest(cwd, modified_files, _remaining())
    metrics["pytest_ran"] = pytest_passed is not None
    metrics["pytest_passed"] = pytest_passed
    if failed_tests:
        metrics["failed_tests"] = ", ".join(failed_tests)
    if pytest_passed is False and _pipeline_enforce():
        metrics["elapsed_sec"] = round(time.monotonic() - t0, 2)
        return False, f"UNIT TESTS FAILED: {', '.join(failed_tests) or 'see pytest output'}", metrics

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
