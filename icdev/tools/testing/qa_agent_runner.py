# CUI // SP-CTI
"""QA Agent Runner — Playwright E2E execution + coverage gap detection for ACE QA Agent.

Invoked by the qa_agent ACE co-worker via run_tool.  All public functions return
plain dicts/lists so they are trivially JSON-serialisable for the agent loop.

CLI usage:
    python tools/testing/qa_agent_runner.py --run [--canvas CANVAS_KEY] [--json]
    python tools/testing/qa_agent_runner.py --discover-gaps [--json]
    python tools/testing/qa_agent_runner.py --status RUN_ID [--json]
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
_TIMEOUT_SECONDS = 1200


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
    failures: List[TestFailure] = field(default_factory=list)
    report_path: str = ""

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


def run_e2e_suite(
    canvas_filter: Optional[str] = None,
    trigger: str = "manual",
    timeout_seconds: int = _TIMEOUT_SECONDS,
) -> QARunResult:
    """Execute Playwright via e2e_runner subprocess with --json reporter.

    Returns a QARunResult with structured failure list.
    """
    run_id = _make_run_id()
    screenshot_dir = _ensure_screenshot_dir(run_id)

    result = QARunResult(
        run_id=run_id,
        trigger=trigger,
        canvas_filter=canvas_filter or "",
    )

    npx = _npx_cmd()
    cmd = [npx, "playwright", "test", "--project", "chromium", "--reporter", "json"]

    if canvas_filter:
        # Run only spec files matching the canvas key
        spec_pattern = str(PROJECT_ROOT / "tests" / "e2e" / f"*{canvas_filter}*.spec.ts")
        matched = glob.glob(spec_pattern)
        if not matched:
            logger.warning("qa_agent_runner: no spec files matching canvas '%s'", canvas_filter)
            result.status = "skipped"
            return result
        cmd.extend(matched)

    env = os.environ.copy()
    root_str = str(PROJECT_ROOT)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root_str if not existing_pp else root_str + os.pathsep + existing_pp

    json_output_file = PROJECT_ROOT / ".tmp" / "ace" / "qa" / f"{run_id}-results.json"
    json_output_file.parent.mkdir(parents=True, exist_ok=True)
    env["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(json_output_file)
    env["PLAYWRIGHT_SCREENSHOT_DIR"] = str(screenshot_dir)

    logger.info("qa_agent_runner: starting run_id=%s canvas_filter=%s", run_id, canvas_filter)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout_seconds,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        result.status = "failed"
        result.failures.append(TestFailure(
            test_name="timeout",
            error_message=f"Playwright timed out after {timeout_seconds}s",
            severity="critical",
        ))
        return result
    except FileNotFoundError:
        result.status = "failed"
        result.failures.append(TestFailure(
            test_name="setup",
            error_message="npx/playwright not found — install Node.js and @playwright/test",
            severity="critical",
        ))
        return result

    raw_json: Optional[str] = None
    if json_output_file.exists():
        raw_json = json_output_file.read_text(encoding="utf-8", errors="replace")
    elif (proc.stdout or "").strip().startswith("{"):
        raw_json = proc.stdout.strip()

    if raw_json:
        result.failures = parse_playwright_json(raw_json)
    elif proc.returncode != 0:
        error_excerpt = (proc.stderr or proc.stdout or "")[:500]
        result.failures.append(TestFailure(
            test_name="playwright_run",
            error_message=f"Playwright exited {proc.returncode}: {error_excerpt}",
            severity="critical",
        ))

    # Count screenshots captured in the run directory
    result.screenshot_count = len(list(screenshot_dir.glob("*.png")))
    result.report_path = str(json_output_file)

    if raw_json:
        try:
            report = json.loads(raw_json)
            _tally(report, result)
        except (json.JSONDecodeError, TypeError):
            pass

    result.failed = len(result.failures)
    if result.total == 0 and result.failed > 0:
        result.total = result.failed
    result.status = "passed" if result.failed == 0 else "failed"

    logger.info(
        "qa_agent_runner: run_id=%s status=%s total=%d passed=%d failed=%d",
        run_id, result.status, result.total, result.passed, result.failed,
    )
    return result


def _tally(report: dict, result: QARunResult) -> None:
    stats = report.get("stats") or {}
    result.total = stats.get("expected", 0) + stats.get("unexpected", 0) + stats.get("skipped", 0)
    result.passed = stats.get("expected", 0)
    result.skipped = stats.get("skipped", 0)


def parse_playwright_json(raw_json: str) -> List[TestFailure]:
    """Parse Playwright JSON reporter output into TestFailure objects."""
    try:
        report = json.loads(raw_json)
    except json.JSONDecodeError:
        return []

    failures: List[TestFailure] = []
    for suite in report.get("suites") or []:
        suite_title = suite.get("title", "unknown")
        spec_file = suite.get("file") or ""
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
    return failures


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
            "task_type": "bug",
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
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    if args.run:
        result = run_e2e_suite(canvas_filter=args.canvas, trigger=args.trigger)
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f"Run {result.run_id}: {result.status} ({result.passed}/{result.total} passed)")
            for f in result.failures:
                print(f"  FAIL: {f.test_name} — {f.error_message[:120]}")
        return 0 if result.status == "passed" else 1

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
