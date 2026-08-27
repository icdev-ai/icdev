# CUI // SP-CTI
"""QA Agent Runner — Playwright E2E execution + coverage gap detection for ACE QA Agent.

Invoked by the qa_agent ACE co-worker via run_tool.  All public functions return
plain dicts/lists so they are trivially JSON-serialisable for the agent loop.

CLI usage:
    python tools/testing/qa_agent_runner.py --run [--canvas CANVAS_KEY] [--json]
    python tools/testing/qa_agent_runner.py --run --deadline-seconds 1800 --batch-size 4
    python tools/testing/qa_agent_runner.py --discover-gaps [--json]
    python tools/testing/qa_agent_runner.py --status RUN_ID [--json]

The suite is BATCHED by spec file rather than run as one invocation, because a
single `npx playwright test` killed at a wall-clock deadline emits no JSON
report at all — so a partial sweep returned nothing, and `--run` reported one
synthetic TestFailure(test_name="timeout") whatever the suite actually did.
Every batch that finishes has a real report, and the spec files that did not
run are NAMED (`spec_files_not_run` / `spec_files_no_report`) rather than
silently absent.

Note: Playwright shuts down a webServer it started, so with no dashboard
already listening each batch pays that startup again. Point the run at a
running dashboard, or set ICDEV_NO_SERVER=1, to avoid it.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

_QA_SCREENSHOT_DIR = "playwright/screenshots/qa-agent"
_E2E_SPEC_GLOB = "tests/e2e/*.spec.ts"
_COMPONENT_REGISTRY_PATH = "args/component_registry.yaml"

#: Whole-sweep wall-clock budget. playwright.config.ts records the measured
#: duration of the full suite in its own comment — "full suite on PostgreSQL
#: 732 passed, 55 failed, 25 skipped (41.5m)" — against 838 tests at
#: `workers: 1`, `fullyParallel: false`. The previous 1200s (20 min) was under
#: half of that, so --run ALWAYS hit subprocess.TimeoutExpired.
_DEADLINE_SECONDS = 3600

#: Spec files per `npx playwright test` invocation. Each batch writes its own
#: report, so this is the granularity at which a deadline-bounded sweep still
#: yields real results.
_BATCH_SIZE = 6

#: Do not start another batch with less than this much of the deadline left —
#: it would only be killed, producing no report for those spec files.
_MIN_BATCH_SECONDS = 90

#: Statuses a run can end in. `no_tests` and `incomplete` exist because the old
#: code called both of them `passed`.
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_NO_TESTS = "no_tests"
STATUS_INCOMPLETE = "incomplete"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class TestFailure:
    test_name: str = ""
    spec_file: str = ""
    error_message: str = ""
    screenshot_path: str = ""
    severity: str = "high"


@dataclass
class QARunResult:
    run_id: str = ""
    trigger: str = "manual"
    canvas_filter: str = ""
    status: str = "running"
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    screenshot_count: int = 0
    #: `failed` minus the failures the parser could name. Non-zero means the
    #: report held a shape the walker did not understand; it is never folded
    #: into `passed`.
    failures_unparsed: int = 0
    failures: List[TestFailure] = field(default_factory=list)
    report_path: str = ""

    #: Coverage bookkeeping. Three lists, never merged — each sends you to a
    #: different fix. `not_run` means the deadline stopped us before the batch
    #: (or mid-batch); `no_report` means the batch RAN and produced nothing
    #: parseable, which is an infrastructure fault, not missing coverage.
    spec_files_total: int = 0
    spec_files_run: List[str] = field(default_factory=list)
    spec_files_not_run: List[str] = field(default_factory=list)
    spec_files_no_report: List[str] = field(default_factory=list)
    batches: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["failures"] = [asdict(f) for f in self.failures]
        return d


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

def discover_coverage_gaps() -> List[Dict[str, Any]]:
    """Return canvases from component_registry.yaml lacking any tests/e2e/<key>*.spec.ts.

    Mirrors the pattern in icdev/tools/ace/canvas_role_gap.py::detect_gaps().
    """
    registry_path = PROJECT_ROOT / _COMPONENT_REGISTRY_PATH
    if not registry_path.exists():
        logger.warning("qa_agent_runner: component_registry.yaml not found at %s", registry_path)
        return []

    try:
        import yaml  # type: ignore[import-untyped]
        with open(registry_path, encoding="utf-8") as fh:
            registry = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.error("qa_agent_runner: cannot parse component_registry.yaml: %s", exc)
        return []

    canvases = registry.get("canvases", []) or []
    child_apps = registry.get("child_apps", []) or []
    all_components = canvases + child_apps

    existing_specs = set(glob.glob(str(PROJECT_ROOT / _E2E_SPEC_GLOB)))
    existing_keys: set[str] = set()
    for spec_path in existing_specs:
        basename = os.path.basename(spec_path).replace(".spec.ts", "")
        existing_keys.add(basename)

    gaps: List[Dict[str, Any]] = []
    for component in all_components:
        key = str(component.get("key") or "").strip()
        enabled = bool(component.get("enabled", True))
        if not key or not enabled:
            continue
        has_spec = any(k == key or k.startswith(key) or key in k for k in existing_keys)
        if not has_spec:
            gaps.append({
                "canvas_key": key,
                "display_name": component.get("display_name") or key,
                "route": component.get("route") or f"/{key}",
                "enabled": enabled,
            })

    return gaps


def generate_spec_stub(canvas_key: str, display_name: str, route: str) -> str:
    """Return a TypeScript Playwright spec for a previously-uncovered canvas.

    Covers: route HTTP status < 400, DOM load, CUI banner presence, IQE widget.
    Follows the pattern in tests/e2e/canvas_smoke.spec.ts.
    """
    safe_name = canvas_key.replace("-", "_")
    return f"""import {{ test, expect }} from '@playwright/test';

// QA Agent generated spec — {display_name}
// Canvas key: {canvas_key}  Route: {route}

test.describe('{display_name} QA Smoke', () => {{
  test('{canvas_key} loads without error', async ({{ page }}) => {{
    const response = await page.goto('{route}');
    expect(response?.status()).toBeLessThan(400);
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('body')).not.toContainText('Traceback');
    await expect(page.locator('body')).not.toContainText('Internal Server Error');
    await page.screenshot({{
      path: 'playwright/screenshots/qa-agent/{canvas_key}_smoke.png',
      fullPage: true,
    }});
  }});

  test('{canvas_key} has CUI classification banner', async ({{ page }}) => {{
    await page.goto('{route}');
    await page.waitForLoadState('domcontentloaded');
    const body = await page.locator('body').textContent();
    expect(body).toContain('CUI');
  }});

  test('{canvas_key} IQE widget present', async ({{ page }}) => {{
    await page.goto('{route}');
    await page.waitForLoadState('domcontentloaded');
    const iqe = page.locator('[id*="iqe"], [class*="iqe-query"], [data-iqe]');
    await expect(iqe.first()).toBeVisible({{ timeout: 5000 }}).catch(() => {{
      // IQE widget optional — log absence but do not fail
      console.warn('{safe_name}: IQE widget not found on {route}');
    }});
  }});
}});
"""


# ---------------------------------------------------------------------------
# E2E execution
# ---------------------------------------------------------------------------

def _npx_cmd() -> str:
    try:
        from tools.compat.platform_utils import get_npx_cmd
        return get_npx_cmd()
    except Exception:
        return "npx.cmd" if sys.platform == "win32" else "npx"


def _make_run_id() -> str:
    import time
    return f"qa-{int(time.time())}"


def _ensure_screenshot_dir(run_id: str) -> Path:
    d = PROJECT_ROOT / _QA_SCREENSHOT_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_spec_files(canvas_filter: Optional[str] = None) -> List[str]:
    """Return spec paths RELATIVE to the repo root, forward-slashed.

    A bare Playwright argument is a REGEX matched against the test file path,
    not a path. An absolute Windows path (backslashes, a drive colon) matches
    nothing: Playwright exits "No tests found" and still writes a 0/0/0 report,
    which reads exactly like a clean run.
    """
    if canvas_filter:
        pattern = str(PROJECT_ROOT / "tests" / "e2e" / f"*{canvas_filter}*.spec.ts")
    else:
        pattern = str(PROJECT_ROOT / _E2E_SPEC_GLOB)
    return sorted(
        os.path.relpath(p, str(PROJECT_ROOT)).replace(os.sep, "/")
        for p in glob.glob(pattern)
    )


def build_playwright_cmd(npx: str, rel_specs: List[str]) -> List[str]:
    """Build one `npx playwright test` argv for a batch of spec files.

    `--project=chromium` is ONE token on purpose. Split as `--project chromium`,
    the parser reads every following bare argument as a further PROJECT name,
    and the run dies with `Project(s) "<spec path>" not found`.

    No `--reporter` override: playwright.config.ts already declares the json
    reporter (whose output path honours ICDEV_PW_RUN_TAG), and a CLI
    `--reporter` REPLACES that list rather than adding to it.
    """
    return [npx, "playwright", "test", "--project=chromium", *rel_specs]


def batch_specs(rel_specs: List[str], batch_size: int) -> List[List[str]]:
    """Split spec files into fixed-size batches, preserving order."""
    size = max(1, int(batch_size))
    return [rel_specs[i:i + size] for i in range(0, len(rel_specs), size)]


def derive_status(result: QARunResult) -> str:
    """Classify a finished run. A run that measured nothing is never `passed`.

    `no_tests` (Playwright matched no test) and `incomplete` (the deadline or a
    missing report left spec files unmeasured) were both previously reported as
    `passed`, so a sweep that ran zero tests was indistinguishable from a green
    one.
    """
    if result.failed > 0:
        return STATUS_FAILED
    if result.spec_files_not_run or result.spec_files_no_report:
        return STATUS_INCOMPLETE
    if result.total == 0:
        return STATUS_NO_TESTS
    return STATUS_PASSED


def _batch_report_path(run_tag: str) -> Path:
    """Where playwright.config.ts's json reporter writes for this run tag."""
    return PROJECT_ROOT / ".tmp" / "test_runs" / f"playwright-results-{run_tag}.json"


def run_e2e_suite(
    canvas_filter: Optional[str] = None,
    trigger: str = "manual",
    deadline_seconds: int = _DEADLINE_SECONDS,
    batch_size: int = _BATCH_SIZE,
) -> QARunResult:
    """Execute the Playwright E2E suite in deadline-bounded batches.

    Returns a QARunResult with a structured failure list, the spec files that
    were measured, and the spec files that were not.
    """
    import time

    run_id = _make_run_id()
    screenshot_dir = _ensure_screenshot_dir(run_id)

    result = QARunResult(
        run_id=run_id,
        trigger=trigger,
        canvas_filter=canvas_filter or "",
    )

    rel_specs = resolve_spec_files(canvas_filter)
    result.spec_files_total = len(rel_specs)
    if not rel_specs:
        logger.warning(
            "qa_agent_runner: no spec files matching canvas '%s'", canvas_filter or "*"
        )
        result.status = STATUS_NO_TESTS
        return result

    env = os.environ.copy()
    root_str = str(PROJECT_ROOT)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root_str if not existing_pp else root_str + os.pathsep + existing_pp
    env["PLAYWRIGHT_SCREENSHOT_DIR"] = str(screenshot_dir)

    npx = _npx_cmd()
    batches = batch_specs(rel_specs, batch_size)
    started = time.time()
    deadline = started + max(1, int(deadline_seconds))

    logger.info(
        "qa_agent_runner: starting run_id=%s canvas_filter=%s specs=%d batches=%d deadline=%ds",
        run_id, canvas_filter, len(rel_specs), len(batches), deadline_seconds,
    )

    for idx, batch in enumerate(batches):
        remaining = deadline - time.time()
        if remaining < _MIN_BATCH_SECONDS:
            for pending in batches[idx:]:
                result.spec_files_not_run.extend(pending)
            result.batches.append({
                "batch": idx, "status": "deadline_skipped", "files": list(batch),
            })
            break

        run_tag = f"{run_id}-b{idx}"
        benv = dict(env)
        benv["ICDEV_PW_RUN_TAG"] = run_tag
        report_path = _batch_report_path(run_tag)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        t0 = time.time()
        try:
            proc = subprocess.run(
                build_playwright_cmd(npx, batch),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=benv,
                timeout=int(remaining),
                cwd=str(PROJECT_ROOT),
            )
        except subprocess.TimeoutExpired:
            # This batch and everything after it is unmeasured, and each one is
            # NAMED — a deadline-truncated sweep that reported only what it got
            # through would read as full coverage.
            result.spec_files_not_run.extend(batch)
            for pending in batches[idx + 1:]:
                result.spec_files_not_run.extend(pending)
            result.batches.append({
                "batch": idx, "status": "deadline_killed",
                "seconds": round(time.time() - t0, 1), "files": list(batch),
            })
            break
        except FileNotFoundError:
            for pending in batches[idx:]:
                result.spec_files_not_run.extend(pending)
            result.failures.append(TestFailure(
                test_name="setup",
                error_message="npx/playwright not found — install Node.js and @playwright/test",
                severity="critical",
            ))
            break

        elapsed = round(time.time() - t0, 1)
        raw_json = _read_batch_report(report_path, proc)
        if not raw_json:
            result.spec_files_no_report.extend(batch)
            result.batches.append({
                "batch": idx, "status": "no_report", "seconds": elapsed,
                "returncode": proc.returncode, "files": list(batch),
                "error": (proc.stderr or proc.stdout or "")[:500],
            })
            continue

        try:
            report = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            result.spec_files_no_report.extend(batch)
            result.batches.append({
                "batch": idx, "status": "unparseable_report", "seconds": elapsed,
                "returncode": proc.returncode, "files": list(batch),
            })
            continue

        result.failures.extend(parse_playwright_json(raw_json))
        _tally(report, result)
        result.spec_files_run.extend(batch)
        stats = report.get("stats") or {}
        batch_record = {
            "batch": idx, "status": "ok", "seconds": elapsed,
            "returncode": proc.returncode, "files": list(batch),
            "report_path": str(report_path),
            "stats": {k: stats.get(k) for k in ("expected", "unexpected", "skipped", "flaky")},
        }
        # Report-level errors say WHY a batch ran zero tests — a webServer that
        # never came up, a config that failed to load. `no_tests` on its own is
        # a shrug, and these are different fixes from "the suite is empty".
        errors = [
            str(e.get("message") or e) for e in (report.get("errors") or [])
        ]
        if errors:
            batch_record["errors"] = errors[:5]
        result.batches.append(batch_record)

    result.screenshot_count = len(list(screenshot_dir.glob("*.png")))
    # `failed` was tallied from Playwright's `stats.unexpected`; `failures` is
    # what the parser could NAME. If the two disagree the gap is reported, never
    # resolved in favour of the parser -- a parser blind spot must not turn a
    # red sweep green.
    result.failures_unparsed = max(0, result.failed - len(result.failures))
    if result.failures_unparsed:
        logger.warning(
            "qa_agent_runner: run_id=%s Playwright reports %d unexpected but only %d "
            "were parsed into failures (%d unnamed)",
            run_id, result.failed, len(result.failures), result.failures_unparsed,
        )
    result.report_path = str(write_run_report(result))
    result.status = derive_status(result)

    logger.info(
        "qa_agent_runner: run_id=%s status=%s total=%d passed=%d failed=%d "
        "specs_run=%d/%d not_run=%d no_report=%d",
        run_id, result.status, result.total, result.passed, result.failed,
        len(result.spec_files_run), result.spec_files_total,
        len(result.spec_files_not_run), len(result.spec_files_no_report),
    )
    return result


def _read_batch_report(report_path: Path, proc: "subprocess.CompletedProcess[str]") -> Optional[str]:
    """Return a batch's raw JSON report, or None if it produced none."""
    if report_path.exists():
        return report_path.read_text(encoding="utf-8", errors="replace")
    stdout = (proc.stdout or "").strip()
    return stdout if stdout.startswith("{") else None


def write_run_report(result: QARunResult) -> Path:
    """Persist the aggregated run to .tmp/ace/qa/<run_id>-results.json."""
    out = PROJECT_ROOT / ".tmp" / "ace" / "qa" / f"{result.run_id}-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    except OSError as exc:
        logger.error("qa_agent_runner: cannot write run report %s: %s", out, exc)
    return out


def _tally(report: dict, result: QARunResult) -> None:
    """Accumulate one batch report's stats onto the run total."""
    stats = report.get("stats") or {}
    expected = int(stats.get("expected") or 0)
    unexpected = int(stats.get("unexpected") or 0)
    skipped = int(stats.get("skipped") or 0)
    result.total += expected + unexpected + skipped
    result.passed += expected
    result.skipped += skipped
    # `failed` is Playwright's OWN count. It used to be `len(result.failures)`
    # assigned after the loop, and on the 2026-08-22 sweep (task-qa-sweep-3c7b8b3d)
    # that read 0 against 31 `unexpected` across 11 batch reports -- the parser
    # walked one suite level and every spec sits under a `test.describe`, so
    # `status` was `passed` with thirty-one red tests inside it.
    result.failed += unexpected


def parse_playwright_json(raw_json: str) -> List[TestFailure]:
    """Parse Playwright JSON reporter output into TestFailure objects."""
    try:
        report = json.loads(raw_json)
    except json.JSONDecodeError:
        return []

    failures: List[TestFailure] = []
    _walk_suites(report.get("suites") or [], "", "", failures)
    return failures


def _walk_suites(
    suites: List[dict], parent_title: str, parent_file: str, failures: List[TestFailure],
) -> None:
    """Collect failed specs from a Playwright suite tree, at ANY depth.

    Playwright's JSON reporter nests: a FILE suite (title = the file name, no
    specs of its own) holds one suite per `test.describe`, which holds the
    specs. Every spec under tests/e2e/ is inside a describe block, so a walk of
    the top level alone finds zero specs in every real report -- measured on
    the 2026-08-22 sweep: 6 file suites per batch, 0 specs each, 1 child each.
    The innermost suite names the test (`Auth flow > login page loads`); the
    file is inherited from whichever ancestor carries one.
    """
    for suite in suites or []:
        suite_title = suite.get("title") or parent_title or "unknown"
        spec_file = suite.get("file") or parent_file or ""
        for spec in suite.get("specs") or []:
            spec_title = spec.get("title", "unknown")
            test_name = f"{suite_title} > {spec_title}"
            for t in spec.get("tests") or []:
                t_results = t.get("results") or []
                if not t_results:
                    continue
                last = t_results[-1]
                status = last.get("status") or ""
                if status in ("passed", "expected", "skipped"):
                    continue
                err = last.get("error") or {}
                error_msg = err.get("message") or err.get("snippet") or "Test failed"
                screenshot_path = ""
                for att in last.get("attachments") or []:
                    ctype = (att.get("contentType") or "").lower()
                    if ctype.startswith("image/") and att.get("path"):
                        screenshot_path = att["path"]
                        break

                severity = "critical" if any(k in test_name.lower() for k in ("auth", "rls", "login", "permission")) else "high"
                failures.append(TestFailure(
                    test_name=test_name,
                    spec_file=spec_file or spec.get("file") or "",
                    error_message=error_msg[:1000],
                    screenshot_path=screenshot_path,
                    severity=severity,
                ))
        _walk_suites(suite.get("suites") or [], suite_title, spec_file, failures)


# ---------------------------------------------------------------------------
# Kanban task filing
# ---------------------------------------------------------------------------

def file_failure_tasks(
    failures: List[TestFailure],
    run_id: str,
    instance_id: str = "",
) -> List[str]:
    """Create kanban tasks for each failure. Returns list of inserted task IDs."""
    if not failures:
        return []

    try:
        from tools.kanban.task_factory import create_tasks
    except ImportError:
        logger.error("qa_agent_runner: cannot import task_factory — skipping kanban filing")
        return []

    specs = []
    for f in failures:
        idem_key = hashlib.sha256(f"{run_id}:{f.test_name}".encode()).hexdigest()[:16]
        task_id = f"qa-fail-{idem_key}"
        desc_lines = [
            f"**Test**: {f.test_name}",
            f"**Spec file**: {f.spec_file}",
            f"**Error**: {f.error_message}",
            f"**Screenshot**: {f.screenshot_path or 'none captured'}",
            f"**Run ID**: {run_id}",
        ]
        if instance_id:
            desc_lines.append(f"**ACE Instance**: {instance_id}")
        specs.append({
            "id": task_id,
            "title": f"[QA] {f.test_name[:80]}",
            "description": "\n".join(desc_lines),
            # NOT "bug". create_tasks refuses it outright — VALID_TASK_TYPES is
            # {build, run, fix, research, deploy, test, chore} and the raise
            # happens before any insert, so every call filed nothing at all.
            "task_type": "fix",
            "priority": "critical" if f.severity == "critical" else "high",
            "status": "backlog",
            "idempotency_key": idem_key,
        })

    created = create_tasks(specs)
    logger.info("qa_agent_runner: filed %d kanban tasks for run %s", len(created), run_id)
    return created


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def record_run(result: QARunResult) -> str:
    """Persist QA run to ace_qa_runs (append-only). Returns run_id."""
    try:
        from icdev.tools.db.storage import get_canvas_connection
    except ImportError:
        from tools.db.storage import get_canvas_connection  # type: ignore[no-reattr]

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        conn.execute(
            """INSERT INTO ace_qa_runs
               (id, trigger, canvas_filter, status,
                total_tests, passed, failed, screenshot_count, report_path)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (
                result.run_id, result.trigger, result.canvas_filter, result.status,
                result.total, result.passed, result.failed,
                result.screenshot_count, result.report_path,
            ),
        )
        conn.commit()
    except Exception:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO ace_qa_runs
                   (id, trigger, canvas_filter, status,
                    total_tests, passed, failed, screenshot_count, report_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.run_id, result.trigger, result.canvas_filter, result.status,
                    result.total, result.passed, result.failed,
                    result.screenshot_count, result.report_path,
                ),
            )
            conn.commit()
        except Exception as exc2:
            logger.error("qa_agent_runner: record_run failed: %s", exc2)
    finally:
        conn.close()
    return result.run_id


def record_failure(
    failure: TestFailure,
    run_id: str,
    kanban_task_id: str = "",
) -> str:
    """Persist one failure to ace_qa_failures (append-only). Returns failure_id."""
    failure_id = hashlib.sha256(f"{run_id}:{failure.test_name}".encode()).hexdigest()[:24]

    try:
        from icdev.tools.db.storage import get_canvas_connection
    except ImportError:
        from tools.db.storage import get_canvas_connection  # type: ignore[no-reattr]

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        conn.execute(
            """INSERT INTO ace_qa_failures
               (id, run_id, test_name, spec_file, error_message,
                screenshot_path, severity, kanban_task_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (
                failure_id, run_id, failure.test_name, failure.spec_file,
                failure.error_message, failure.screenshot_path,
                failure.severity, kanban_task_id,
            ),
        )
        conn.commit()
    except Exception:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO ace_qa_failures
                   (id, run_id, test_name, spec_file, error_message,
                    screenshot_path, severity, kanban_task_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    failure_id, run_id, failure.test_name, failure.spec_file,
                    failure.error_message, failure.screenshot_path,
                    failure.severity, kanban_task_id,
                ),
            )
            conn.commit()
        except Exception as exc2:
            logger.error("qa_agent_runner: record_failure failed: %s", exc2)
    finally:
        conn.close()
    return failure_id


def get_run_status(run_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a run record by ID. Returns None if not found."""
    try:
        from icdev.tools.db.storage import get_canvas_connection
    except ImportError:
        from tools.db.storage import get_canvas_connection  # type: ignore[no-reattr]

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        row = conn.execute(
            "SELECT * FROM ace_qa_runs WHERE id = %s", (run_id,)
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM ace_qa_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        keys = [d[0] for d in (row.cursor.description if hasattr(row, "cursor") else [])]
        if not keys:
            return {"id": run_id, "raw": str(row)}
        return dict(zip(keys, row))
    except Exception as exc:
        logger.error("qa_agent_runner: get_run_status failed: %s", exc)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="QA Agent Runner — Playwright E2E execution for ACE qa_agent role"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="Execute Playwright E2E suite")
    group.add_argument("--discover-gaps", action="store_true", help="Find canvases without E2E specs")
    group.add_argument("--status", metavar="RUN_ID", help="Fetch status of a previous run")
    parser.add_argument("--canvas", metavar="CANVAS_KEY", help="Limit --run to specs matching this canvas key")
    parser.add_argument("--trigger", default="manual", help="Trigger label (default: manual)")
    parser.add_argument(
        "--deadline-seconds", type=int, default=_DEADLINE_SECONDS,
        help=f"Whole-sweep wall-clock budget (default: {_DEADLINE_SECONDS}; "
             "the full suite measures ~41.5m)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=_BATCH_SIZE,
        help=f"Spec files per Playwright invocation (default: {_BATCH_SIZE})",
    )
    parser.add_argument(
        "--record", dest="record", action="store_true", default=True,
        help="Persist the run to ace_qa_runs (default: on)",
    )
    parser.add_argument(
        "--no-record", dest="record", action="store_false",
        help="Run the suite without persisting it",
    )
    parser.add_argument(
        "--file-failures", action="store_true",
        help="File one kanban `fix` card per failure (default: off — one shared "
             "cause becomes N cards and N duplicate PRs, so filing is opt-in)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    if args.run:
        result = run_e2e_suite(
            canvas_filter=args.canvas,
            trigger=args.trigger,
            deadline_seconds=args.deadline_seconds,
            batch_size=args.batch_size,
        )
        # A sweep the CLI does not persist is a sweep nobody can cite an hour
        # later: `--run` measured 770 tests and wrote NOTHING to ace_qa_runs,
        # so every caller that needed step 3/4 of the QA card had to hand-write
        # a driver around these same two seams. Both outcomes are REPORTED —
        # `recorded` is None when recording was not attempted, which is never
        # the same as an attempt that failed.
        persistence: Dict[str, Any] = {
            "recorded": None, "record_error": None,
            "filed_tasks": None, "file_failures_error": None,
        }
        if args.record:
            try:
                persistence["recorded"] = record_run(result)
            except Exception as exc:
                persistence["record_error"] = repr(exc)
                logger.error("qa_agent_runner: record_run failed: %s", exc)
        if result.failures and args.file_failures:
            try:
                persistence["filed_tasks"] = file_failure_tasks(
                    result.failures, run_id=result.run_id
                )
            except Exception as exc:
                persistence["file_failures_error"] = repr(exc)
                logger.error("qa_agent_runner: file_failure_tasks failed: %s", exc)

        if args.json:
            payload = result.to_dict()
            payload["persistence"] = persistence
            print(json.dumps(payload, indent=2))
        else:
            print(f"Run {result.run_id}: {result.status} ({result.passed}/{result.total} passed)")
            print(f"  recorded: {persistence['recorded'] or persistence['record_error'] or 'not attempted'}")
            if result.failures:
                print(f"  filed tasks: {persistence['filed_tasks'] if persistence['filed_tasks'] is not None else persistence['file_failures_error'] or 'not attempted (--file-failures is off)'}")
            print(f"  spec files: {len(result.spec_files_run)}/{result.spec_files_total} measured")
            for f in result.failures:
                print(f"  FAIL: {f.test_name} — {f.error_message[:120]}")
            # Name the unmeasured spec files. A truncated sweep that printed
            # only what it got through would read as full coverage.
            for label, files in (
                ("NOT RUN (deadline)", result.spec_files_not_run),
                ("NO REPORT", result.spec_files_no_report),
            ):
                for path in files:
                    print(f"  {label}: {path}")
            for b in result.batches:
                for err in b.get("errors") or []:
                    print(f"  BATCH {b['batch']} ERROR: {err[:160]}")
        return 0 if result.status == STATUS_PASSED else 1

    if args.discover_gaps:
        gaps = discover_coverage_gaps()
        if args.json:
            print(json.dumps(gaps, indent=2))
        else:
            if gaps:
                print(f"Coverage gaps found: {len(gaps)}")
                for g in gaps:
                    print(f"  {g['canvas_key']} ({g['display_name']}) — {g['route']}")
            else:
                print("No coverage gaps detected.")
        return 0

    if args.status:
        row = get_run_status(args.status)
        if row is None:
            print(json.dumps({"error": f"run_id not found: {args.status}"}) if args.json else f"Not found: {args.status}")
            return 1
        if args.json:
            print(json.dumps(row, indent=2, default=str))
        else:
            print(f"Run {row.get('id')}: status={row.get('status')} "
                  f"total={row.get('total_tests')} passed={row.get('passed')} failed={row.get('failed')}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
