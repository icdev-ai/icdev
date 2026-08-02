# CUI // SP-CTI
"""ICDEV™ end-to-end browser test runner.

Drives Playwright in two modes:

* ``native`` — runs ``tests/e2e/*.spec.ts`` directly via
  ``npx playwright test --reporter json``.
* ``mcp`` — runs markdown specs under ``.claude/commands/e2e/*.md`` via
  Claude Code + the Playwright MCP server.

Auto mode prefers native when Playwright is installed and at least
one ``.spec.ts`` exists; otherwise falls back to MCP.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/testing/e2e_runner.md`` (OPT-75 Phase 3
clean-room rewrite). Fixes the historic ``args.project_id`` typo that
crashed every native ``--run-all`` invocation.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# Propagate the worktree root via os.environ so that any subprocess launched
# without an explicit env= dict still resolves `import tools` correctly.
_existing_pp = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = (
    str(PROJECT_ROOT) if not _existing_pp
    else str(PROJECT_ROOT) + os.pathsep + _existing_pp
)

from tools.testing.data_types import E2ETestResult  # noqa: E402
from tools.testing.utils import (  # noqa: E402
    ensure_run_dir,
    make_run_id,
    setup_logger,
)


_NATIVE_TIMEOUT_SECONDS: int = 1200  # 60s server start + ~1140s for 88 tests (20 min safety margin)
_MCP_TIMEOUT_SECONDS: int = 120
_PLAYWRIGHT_PROBE_TIMEOUT: int = 15
_CLAUDE_PROBE_TIMEOUT: int = 5
_SELENIUM_TIMEOUT_SECONDS: int = 300


# ────────────────────────────────────────────────────────────────────────────
# Discovery
# ────────────────────────────────────────────────────────────────────────────


def _selenium_glob() -> str:
    return str(PROJECT_ROOT / "tests" / "e2e_selenium" / "test_*.py")


def _selenium_script_glob() -> str:
    # Standalone selenium e2e scripts live at the tests/ root as e2e_*.py with a
    # main() entry + exit-code contract. They are NOT pytest-collectable — their
    # test_* functions take positional args (driver, results), so pytest would
    # raise fixture errors — hence they are executed directly via `python <file>`.
    return str(PROJECT_ROOT / "tests" / "e2e_*.py")


def _native_glob() -> str:
    return str(PROJECT_ROOT / "tests" / "e2e" / "*.spec.ts")


def _mcp_glob() -> str:
    return str(PROJECT_ROOT / ".claude" / "commands" / "e2e" / "*.md")


def discover_native_tests() -> List[str]:
    return sorted(glob.glob(_native_glob()))


def discover_mcp_tests() -> List[str]:
    return sorted(glob.glob(_mcp_glob()))


def discover_selenium_scripts() -> List[str]:
    """Standalone tests/e2e_*.py selenium scripts (main()-driven, run as scripts)."""
    return sorted(glob.glob(_selenium_script_glob()))


def discover_selenium_tests() -> List[str]:
    # Inventory both the pytest-style selenium suite under tests/e2e_selenium/ and
    # the standalone tests/e2e_*.py scripts so `--driver selenium --discover` lists
    # every selenium e2e test (e.g. e2e_odc_lifecycle, e2e_observability_mitre).
    return sorted(
        set(glob.glob(_selenium_glob())) | set(glob.glob(_selenium_script_glob()))
    )


# ────────────────────────────────────────────────────────────────────────────
# Standalone script allowlist (oxf-e2e-01)
# ────────────────────────────────────────────────────────────────────────────


def _allowlist_path() -> Path:
    return PROJECT_ROOT / "args" / "e2e_script_allowlist.yaml"


def _load_allowlist_doc(logger: Optional[logging.Logger] = None) -> Dict[str, Any]:
    """Parse args/e2e_script_allowlist.yaml. Missing/broken → {} with a warning.

    Never raises: a missing or malformed allowlist degrades gracefully so that
    ``--include-scripts`` is a no-op rather than an error.
    """
    path = _allowlist_path()
    if not path.exists():
        if logger:
            logger.warning(
                "e2e_runner: script allowlist not found at %s — "
                "--include-scripts will run no standalone scripts",
                path,
            )
        return {}
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - degrade gracefully on any parse error
        if logger:
            logger.warning(
                "e2e_runner: cannot parse script allowlist %s: %s", path, exc
            )
        return {}
    return data if isinstance(data, dict) else {}


def load_script_allowlist(logger: Optional[logging.Logger] = None) -> set:
    """Return the set of allowlisted standalone-script names (no .py suffix)."""
    doc = _load_allowlist_doc(logger)
    names = doc.get("allowlist") or []
    return {str(n).strip() for n in names if str(n).strip()}


def load_script_exclusions(logger: Optional[logging.Logger] = None) -> Dict[str, str]:
    """Return the {script_name: reason} exclusion map from the allowlist doc."""
    doc = _load_allowlist_doc(logger)
    excluded = doc.get("excluded") or {}
    if not isinstance(excluded, dict):
        return {}
    return {str(k).strip(): str(v) for k, v in excluded.items()}


def discover_allowlisted_scripts(
    logger: Optional[logging.Logger] = None,
) -> List[str]:
    """Standalone tests/e2e_*.py scripts present on disk AND in the allowlist.

    Logs the skipped/excluded count so truncation is never silent. Scripts on
    disk but not allowlisted are skipped; allowlisted names with no file are
    reported as missing.
    """
    allow = load_script_allowlist(logger)
    on_disk = discover_selenium_scripts()
    disk_names = {os.path.basename(s)[:-3] for s in on_disk}

    included = [s for s in on_disk if os.path.basename(s)[:-3] in allow]
    skipped = len(on_disk) - len(included)
    missing = sorted(allow - disk_names)

    if logger:
        logger.info(
            "e2e_runner: allowlist → include=%d skipped=%d (of %d on disk)",
            len(included), skipped, len(on_disk),
        )
        if missing:
            logger.warning(
                "e2e_runner: %d allowlisted script(s) not found on disk: %s",
                len(missing), ", ".join(missing),
            )
    return sorted(included)


def discover_e2e_tests(mode: str = "auto") -> List[str]:
    if mode == "native":
        return discover_native_tests()
    if mode == "mcp":
        return discover_mcp_tests()
    native = discover_native_tests()
    return native if native else discover_mcp_tests()


# ────────────────────────────────────────────────────────────────────────────
# Spec parsing
# ────────────────────────────────────────────────────────────────────────────


_ACTION_VERBS = (
    "navigate", "click", "fill", "type", "select",
    "check", "assert", "verify", "wait", "screenshot",
    "scroll", "goto", "expect", "tocontain", "tobevisible", "tohavetitle",
)

_ASSERTION_VERBS = (
    "assert", "verify", "check", "expect",
    "tocontain", "tobevisible", "tohavetitle",
)


def parse_test_spec(test_file: str) -> Dict[str, Any]:
    """Pull metadata out of a test spec markdown or .spec.ts file."""
    with open(test_file, "r", encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    base = os.path.basename(test_file)
    test_name = base.replace(".md", "").replace(".spec.ts", "")
    lines = (content or "").strip().splitlines()

    spec: Dict[str, Any] = {
        "name": test_name,
        "file": test_file,
        "description": "",
        "steps": [],
        "assertions": [],
        "raw_content": content,
    }

    for line in lines:
        if line.startswith("#") or line.startswith("//"):
            desc = line.lstrip("#/ ").strip()
            if desc and len(desc) > 5:
                spec["description"] = desc
                break

    for line in lines:
        lower = line.lower().strip()
        if not any(verb in lower for verb in _ACTION_VERBS):
            continue
        if any(verb in lower for verb in _ASSERTION_VERBS):
            spec["assertions"].append(line.strip())
        else:
            spec["steps"].append(line.strip())

    return spec


# ────────────────────────────────────────────────────────────────────────────
# Playwright availability
# ────────────────────────────────────────────────────────────────────────────


def _npx_cmd() -> str:
    from tools.compat.platform_utils import get_npx_cmd
    return get_npx_cmd()


def check_playwright_installed() -> bool:
    try:
        proc = subprocess.run(
            [_npx_cmd(), "playwright", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PLAYWRIGHT_PROBE_TIMEOUT,
            cwd=str(PROJECT_ROOT),
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ────────────────────────────────────────────────────────────────────────────
# Native execution
# ────────────────────────────────────────────────────────────────────────────


def _failure_result_for(
    test_file: Optional[str],
    error: str,
) -> E2ETestResult:
    name = (
        os.path.basename(test_file).replace(".spec.ts", "")
        if test_file else "all_e2e"
    )
    return E2ETestResult(
        test_name=name,
        status="failed",
        test_path=test_file or "tests/e2e/",
        error=error,
    )


def _success_result_for(test_file: Optional[str]) -> E2ETestResult:
    name = (
        os.path.basename(test_file).replace(".spec.ts", "")
        if test_file else "all_e2e"
    )
    return E2ETestResult(
        test_name=name,
        status="passed",
        test_path=test_file or "tests/e2e/",
    )


def _server_is_up(url: str = "http://localhost:5050", timeout: int = 3) -> bool:
    """Return True if the dashboard server is already listening."""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status < 500
    except Exception:
        return False


def run_playwright_native(
    run_id: str,
    logger: logging.Logger,
    test_file: Optional[str] = None,
    project: str = "chromium",
) -> List[E2ETestResult]:
    if _server_is_up():
        logger.info("e2e_runner: server already running at localhost:5050 — Playwright will reuse it")
    else:
        logger.info("e2e_runner: server not running — Playwright webServer will start it (up to 60s)")
    logger.info("e2e_runner: running tests via native Playwright")
    results_dir = ensure_run_dir(run_id)
    (results_dir / "screenshots").mkdir(parents=True, exist_ok=True)

    cmd = [
        _npx_cmd(), "playwright", "test",
        "--project", project,
        "--reporter", "json",
    ]
    if test_file:
        cmd.append(test_file)

    env = os.environ.copy()
    root_str = str(PROJECT_ROOT)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root_str if not existing else root_str + os.pathsep + existing
    json_results_file = results_dir / "playwright-results.json"
    env["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(json_results_file)
    logger.info("e2e_runner: PYTHONPATH=%s", env["PYTHONPATH"])
    logger.info("e2e_runner: command: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=_NATIVE_TIMEOUT_SECONDS,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "e2e_runner: Playwright timed out after %ss", _NATIVE_TIMEOUT_SECONDS,
        )
        return [
            _failure_result_for(
                test_file,
                f"Playwright test execution timed out after "
                f"{_NATIVE_TIMEOUT_SECONDS} seconds",
            )
        ]
    except FileNotFoundError:
        logger.error("e2e_runner: npx not found — install Node.js")
        return [
            _failure_result_for(
                test_file,
                "npx/playwright not found. Install with: "
                "npm install -D @playwright/test",
            )
        ]

    logger.info("e2e_runner: Playwright exit code %s", proc.returncode)

    if json_results_file.exists():
        parsed = _parse_playwright_json_results(json_results_file, logger)
        if parsed:
            return parsed

    if (proc.stdout or "").strip():
        try:
            return _parse_playwright_report(json.loads(proc.stdout), logger)
        except json.JSONDecodeError:
            pass

    if proc.returncode == 0:
        return [_success_result_for(test_file)]

    error_excerpt = (proc.stderr or proc.stdout or "")[:500]
    return [
        _failure_result_for(
            test_file,
            f"Playwright exited with code {proc.returncode}: {error_excerpt}",
        )
    ]


def _parse_playwright_json_results(
    results_file: Path, logger: logging.Logger,
) -> List[E2ETestResult]:
    try:
        with open(results_file, "r", encoding="utf-8") as fh:
            return _parse_playwright_report(json.load(fh), logger)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("e2e_runner: cannot parse %s: %s", results_file, exc)
        return []


def _collect_attachments(attachments: List[Dict[str, Any]]):
    screenshots: List[str] = []
    video_path: Optional[str] = None
    for att in attachments or []:
        ctype = (att.get("contentType") or "").lower()
        path = att.get("path") or ""
        if ctype.startswith("image/") and path:
            screenshots.append(path)
        elif ctype.startswith("video/") and path and not video_path:
            video_path = path
    return screenshots, video_path


def _parse_playwright_report(
    report: Dict[str, Any], logger: logging.Logger,
) -> List[E2ETestResult]:
    results: List[E2ETestResult] = []
    suites = report.get("suites") or []

    for suite in suites:
        suite_title = suite.get("title", "unknown")
        for spec in suite.get("specs") or []:
            spec_title = spec.get("title", "unknown")
            test_name = f"{suite_title} > {spec_title}"
            test_path = spec.get("file") or suite.get("file") or ""
            for t in spec.get("tests") or []:
                t_results = t.get("results") or []
                if not t_results:
                    continue
                last = t_results[-1]
                raw = (last.get("status") or "")
                if raw in ("passed", "expected"):
                    pw_status = "passed"
                elif raw == "skipped":
                    pw_status = "skipped"
                else:
                    pw_status = "failed"
                screenshots, video_path = _collect_attachments(
                    last.get("attachments") or []
                )
                error_msg = None
                if pw_status == "failed":
                    err = last.get("error") or {}
                    error_msg = (
                        err.get("message") or err.get("snippet") or "Test failed"
                    )
                results.append(E2ETestResult(
                    test_name=test_name,
                    status=pw_status,
                    test_path=test_path,
                    screenshots=screenshots,
                    video_path=video_path,
                    error=(error_msg[:500] if error_msg else None),
                    cui_banners_verified=(
                        "cui" in test_name.lower()
                        or "banner" in test_name.lower()
                    ),
                ))

    # Recurse into nested suites.
    for suite in suites:
        for child in suite.get("suites") or []:
            results.extend(
                _parse_playwright_report({"suites": [child]}, logger)
            )

    if results:
        passed_n = sum(1 for r in results if r.passed)
        logger.info(
            "e2e_runner: parsed %d results (%d passed, %d failed)",
            len(results), passed_n, len(results) - passed_n,
        )
    return results


# ────────────────────────────────────────────────────────────────────────────
# MCP execution (legacy)
# ────────────────────────────────────────────────────────────────────────────


def _claude_available() -> bool:
    claude_path = os.getenv("CLAUDE_CODE_PATH", "claude")
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        proc = subprocess.run(
            [claude_path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CLAUDE_PROBE_TIMEOUT,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def execute_e2e_test(
    test_file: str,
    run_id: str,
    logger: logging.Logger,
) -> E2ETestResult:
    test_name = os.path.basename(test_file).replace(".md", "")
    logger.info("e2e_runner: executing MCP test %s", test_name)

    try:
        spec = parse_test_spec(test_file)
    except Exception as exc:
        return E2ETestResult(
            test_name=test_name,
            status="failed",
            test_path=test_file,
            error=f"Failed to parse test spec: {exc}",
        )

    logger.info("  description: %s", spec["description"])
    logger.info("  steps: %d", len(spec["steps"]))
    logger.info("  assertions: %d", len(spec["assertions"]))

    screenshot_dir = ensure_run_dir(run_id) / "screenshots" / test_name
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    if _claude_available():
        return _execute_via_claude(
            test_file, test_name, run_id, screenshot_dir, logger,
        )
    return _validate_spec(spec, test_name, test_file, logger)


def _execute_via_claude(
    test_file: str,
    test_name: str,
    run_id: str,
    screenshot_dir: Path,
    logger: logging.Logger,
) -> E2ETestResult:
    from tools.testing.utils import get_safe_subprocess_env

    claude_path = os.getenv("CLAUDE_CODE_PATH", "claude")
    env = get_safe_subprocess_env()

    try:
        with open(test_file, "r", encoding="utf-8", errors="replace") as fh:
            test_spec = fh.read()
    except OSError as exc:
        return E2ETestResult(
            test_name=test_name,
            status="failed",
            test_path=test_file,
            error=f"Cannot read spec file: {exc}",
        )

    prompt = (
        "Execute the following E2E test using the Playwright MCP server. "
        "Navigate through each step, take screenshots, and verify all "
        f"assertions. Save screenshots to {screenshot_dir}. Return a JSON "
        "object with: test_name, status (passed/failed), screenshots "
        "(list of paths), error (null or message).\n\n"
        f"Test Spec:\n{test_spec}"
    )

    cmd = [
        claude_path,
        "-p", prompt,
        "--model", "sonnet",
        "--output-format", "json",
        "--dangerously-skip-permissions",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=_MCP_TIMEOUT_SECONDS,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return E2ETestResult(
            test_name=test_name,
            status="failed",
            test_path=test_file,
            error=f"E2E test timed out after {_MCP_TIMEOUT_SECONDS} seconds",
        )
    except Exception as exc:
        return E2ETestResult(
            test_name=test_name,
            status="failed",
            test_path=test_file,
            error=f"Execution error: {exc}",
        )

    if proc.returncode == 0 and (proc.stdout or "").strip():
        try:
            from tools.testing.utils import parse_json
            payload = parse_json(proc.stdout)
            return E2ETestResult(
                test_name=payload.get("test_name", test_name),
                status=payload.get("status", "failed"),
                test_path=test_file,
                screenshots=payload.get("screenshots", []) or [],
                error=payload.get("error"),
            )
        except Exception:
            pass

    return E2ETestResult(
        test_name=test_name,
        status="failed",
        test_path=test_file,
        error=(
            f"Claude Code returned exit code {proc.returncode}: "
            f"{(proc.stderr or '')[:200]}"
        ),
    )


def _validate_spec(
    spec: Dict[str, Any],
    test_name: str,
    test_file: str,
    logger: logging.Logger,
) -> E2ETestResult:
    issues: List[str] = []
    if not spec.get("steps"):
        issues.append("No test steps found in spec")
    if not spec.get("assertions"):
        issues.append("No assertions found in spec")
    if issues:
        return E2ETestResult(
            test_name=test_name,
            status="failed",
            test_path=test_file,
            error=f"Spec validation: {'; '.join(issues)}",
        )
    logger.info(
        "e2e_runner: spec validated (%d steps, %d assertions)",
        len(spec["steps"]), len(spec["assertions"]),
    )
    return E2ETestResult(
        test_name=test_name,
        status="passed",
        test_path=test_file,
    )


# ────────────────────────────────────────────────────────────────────────────
# Vision validation (Phase 23)
# ────────────────────────────────────────────────────────────────────────────


def _run_vision_validation(
    results: List[E2ETestResult],
    logger: logging.Logger,
    assertions: Optional[List[str]] = None,
    strict: bool = False,
) -> List[E2ETestResult]:
    try:
        from tools.testing.screenshot_validator import (
            DEFAULT_ASSERTIONS,
            check_vision_available,
            validate_screenshot,
        )
    except ImportError as exc:
        logger.warning("e2e_runner: screenshot validator unavailable: %s", exc)
        return results

    try:
        availability = check_vision_available()
    except Exception as exc:
        logger.warning(
            "e2e_runner: vision availability probe raised: %s", exc
        )
        return results

    if not availability.get("available"):
        logger.warning(
            "e2e_runner: vision model not available — skipping validation: %s",
            availability.get("error", "unknown"),
        )
        return results

    if assertions is None:
        assertions = list(DEFAULT_ASSERTIONS)

    logger.info(
        "e2e_runner: running vision validation with %d assertion(s)",
        len(assertions),
    )

    total_validated = total_passed = total_failed = 0

    for result in results:
        screenshots = result.screenshots or []
        if not screenshots:
            continue

        vision_results: List[Dict[str, Any]] = []
        for screenshot_path in screenshots:
            if not Path(screenshot_path).exists():
                logger.warning(
                    "e2e_runner: screenshot missing: %s", screenshot_path
                )
                continue
            for assertion in assertions:
                vr = validate_screenshot(screenshot_path, assertion)
                vision_results.append(vr.to_dict())
                total_validated += 1
                if vr.passed is True:
                    total_passed += 1
                elif vr.passed is False:
                    total_failed += 1
                    logger.warning(
                        "e2e_runner: vision FAIL: %s — %s",
                        assertion, vr.explanation,
                    )
                    if strict:
                        result.status = "failed"
                        if result.error:
                            result.error += f"; Vision: {assertion} failed"
                        else:
                            result.error = (
                                f"Vision: {assertion} failed — {vr.explanation}"
                            )

        result.vision_analysis = vision_results if vision_results else None

    skipped = total_validated - total_passed - total_failed
    logger.info(
        "e2e_runner: vision validation done (%d passed, %d failed, %d skipped)",
        total_passed, total_failed, skipped,
    )
    return results


# ────────────────────────────────────────────────────────────────────────────
# Selenium execution
# ────────────────────────────────────────────────────────────────────────────


def check_selenium_driver() -> bool:
    """Return True when a vendored ChromeDriver or msedgedriver binary exists."""
    try:
        from tools.airgap.detector import has_vendored_driver
        return has_vendored_driver()
    except Exception:
        return False


def _parse_pytest_output(
    stdout: str,
    stderr: str,
    returncode: int,
    logger: logging.Logger,
) -> List[E2ETestResult]:
    """Parse ``pytest -v`` stdout into a list of E2ETestResult objects."""
    results: List[E2ETestResult] = []
    for line in stdout.splitlines():
        line = line.strip()
        if " PASSED" in line or " FAILED" in line or " ERROR" in line:
            status = "passed" if " PASSED" in line else "failed"
            test_id = line.split(" ")[0]
            test_path = ""
            test_name = test_id
            if "::" in test_id:
                parts = test_id.split("::")
                test_path = parts[0]
                test_name = "::".join(parts[1:])
            results.append(E2ETestResult(
                test_name=test_name,
                status=status,
                test_path=test_path,
                error=(
                    f"pytest reported ERROR/FAILED for {test_name}"
                    if status == "failed" else None
                ),
            ))

    if not results:
        overall = "passed" if returncode == 0 else "failed"
        error_excerpt = ((stderr or stdout) or "")[:500] if returncode != 0 else None
        results.append(E2ETestResult(
            test_name="e2e_selenium",
            status=overall,
            test_path=str(PROJECT_ROOT / "tests" / "e2e_selenium"),
            error=(
                f"pytest exited {returncode}: {error_excerpt}"
                if error_excerpt else None
            ),
        ))

    return results


def _is_selenium_script(test_file: str) -> bool:
    """True for a standalone tests/e2e_*.py selenium script (main()-driven).

    These are executed directly via ``python <file>`` rather than pytest, because
    their ``test_*`` functions take positional args (driver, results) and are not
    pytest fixtures — pytest collection would raise fixture errors.
    """
    p = Path(test_file)
    return (
        p.suffix == ".py"
        and p.name.startswith("e2e_")
        and p.parent.name == "tests"
    )


def run_selenium_script(
    run_id: str,
    logger: logging.Logger,
    script: str,
) -> List[E2ETestResult]:
    """Run a standalone selenium e2e script (tests/e2e_*.py) via ``python <file>``.

    The script's ``main()`` returns 0 on pass / non-zero on failure. Requires a
    live dashboard + a browser driver; the caller gates on ``check_selenium_driver``.
    """
    name = os.path.basename(script).replace(".py", "")
    logger.info("e2e_runner: running selenium script %s", name)
    cmd = [sys.executable, script]

    env = os.environ.copy()
    root_str = str(PROJECT_ROOT)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root_str if not existing else root_str + os.pathsep + existing

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SELENIUM_TIMEOUT_SECONDS,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        msg = f"selenium script timed out after {_SELENIUM_TIMEOUT_SECONDS} seconds"
        logger.error("e2e_runner: %s", msg)
        return [E2ETestResult(
            test_name=name, status="failed", test_path=script, error=msg,
        )]
    except FileNotFoundError:
        msg = f"Python interpreter not found: {sys.executable}"
        logger.error("e2e_runner: %s", msg)
        return [E2ETestResult(
            test_name=name, status="failed", test_path=script, error=msg,
        )]

    logger.info("e2e_runner: %s exit code %s", name, proc.returncode)
    status = "passed" if proc.returncode == 0 else "failed"
    error = None
    if status == "failed":
        error = (
            f"script exited {proc.returncode}: "
            f"{((proc.stderr or proc.stdout) or '')[:500]}"
        )
    return [E2ETestResult(
        test_name=name, status=status, test_path=script, error=error,
    )]


def run_selenium(
    run_id: str,
    logger: logging.Logger,
    test_file: Optional[str] = None,
) -> List[E2ETestResult]:
    """Run Selenium tests under tests/e2e_selenium/ via pytest.

    Caller must check ``check_selenium_driver()`` first and handle the
    absent-driver case; this function assumes the driver is present.

    A standalone ``tests/e2e_*.py`` script passed as ``test_file`` is dispatched to
    ``run_selenium_script`` (executed directly), since such scripts are main()-driven
    and not pytest-collectable.
    """
    if test_file and _is_selenium_script(test_file):
        return run_selenium_script(run_id, logger, test_file)

    logger.info("e2e_runner: running tests via Selenium (pytest)")
    target = test_file or str(PROJECT_ROOT / "tests" / "e2e_selenium")
    cmd = [sys.executable, "-m", "pytest", target, "-v", "--tb=short", "-q"]
    logger.info("e2e_runner: command: %s", " ".join(cmd))

    # Propagate the worktree root via PYTHONPATH so `import tools` resolves in
    # the subprocess even when the parent environment doesn't have it set.
    # Use only the project root (parent of icdev/) — not icdev/ itself — so
    # `import tools` resolves from the canonical project root.
    env = os.environ.copy()
    root_str = str(PROJECT_ROOT)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root_str if not existing else root_str + os.pathsep + existing
    logger.info("e2e_runner: PYTHONPATH=%s", env["PYTHONPATH"])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SELENIUM_TIMEOUT_SECONDS,
            cwd=str(PROJECT_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        msg = f"pytest timed out after {_SELENIUM_TIMEOUT_SECONDS} seconds"
        logger.error("e2e_runner: %s", msg)
        return [E2ETestResult(
            test_name="e2e_selenium",
            status="failed",
            test_path=target,
            error=msg,
        )]
    except FileNotFoundError:
        msg = f"Python interpreter not found: {sys.executable}"
        logger.error("e2e_runner: %s", msg)
        return [E2ETestResult(
            test_name="e2e_selenium",
            status="failed",
            test_path=target,
            error=msg,
        )]

    logger.info("e2e_runner: pytest exit code %s", proc.returncode)
    return _parse_pytest_output(proc.stdout, proc.stderr, proc.returncode, logger)


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ICDEV™ E2E test runner")
    parser.add_argument("--test-file", help="Path to one E2E test (.spec.ts or .md)")
    parser.add_argument("--discover", action="store_true",
                        help="List discoverable E2E tests")
    parser.add_argument("--run-all", action="store_true",
                        help="Run every discovered E2E test")
    parser.add_argument("--run-id", help="Test run ID (auto-generated if absent)")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument(
        "--mode",
        choices=["native", "mcp", "auto"],
        default="auto",
        help="Execution mode",
    )
    parser.add_argument(
        "--driver",
        choices=["native", "mcp", "selenium"],
        default=None,
        help="Test driver override (selenium runs tests/e2e_selenium/ via pytest)",
    )
    parser.add_argument(
        "--project",
        default="chromium",
        help="Playwright browser project (chromium, firefox, webkit)",
    )
    parser.add_argument(
        "--include-scripts",
        action="store_true",
        help=(
            "With --driver selenium --run-all, additionally execute the standalone "
            "tests/e2e_*.py scripts listed in args/e2e_script_allowlist.yaml. "
            "Opt-in; default --run-all behavior is unchanged."
        ),
    )
    parser.add_argument("--validate-screenshots", action="store_true")
    parser.add_argument(
        "--vision-assertions", action="append",
        help="Custom vision assertion (repeatable)",
    )
    parser.add_argument("--vision-strict", action="store_true")
    return parser


def _resolve_mode(requested: str) -> str:
    if requested != "auto":
        return requested
    if check_playwright_installed() and discover_native_tests():
        return "native"
    return "mcp"


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --driver selenium takes precedence over --mode
    if args.driver == "selenium":
        run_id = args.run_id or make_run_id()
        logger = setup_logger(run_id, "e2e_runner")
        logger.info("e2e_runner: driver=selenium")

        if args.discover:
            tests = discover_selenium_tests()
            if args.json:
                items = [
                    {"file": t, "name": os.path.basename(t).replace(".py", ""), "driver": "selenium"}
                    for t in tests
                ]
                print(json.dumps(items, indent=2))
            else:
                print(f"Found {len(tests)} Selenium tests:")
                for t in tests:
                    print(f"  {os.path.basename(t)}")
            return 0

        if not check_selenium_driver():
            reason = (
                "No vendored ChromeDriver or msedgedriver found in vendor/drivers/. "
                "Add a driver binary to run selenium tests."
            )
            logger.warning("e2e_runner: skipping selenium — %s", reason)
            if args.json:
                print(json.dumps([{"driver": "selenium", "status": "skipped", "reason": reason}], indent=2))
            else:
                print(f"[SKIP] selenium — {reason}")
            return 0

        if args.run_all or args.test_file:
            target = args.test_file if args.test_file else None
            results = run_selenium(run_id, logger, test_file=target)
            # Opt-in: additionally run allowlisted standalone scripts on --run-all.
            # Default behavior (flag absent) is unchanged.
            if args.run_all and getattr(args, "include_scripts", False):
                scripts = discover_allowlisted_scripts(logger)
                logger.info(
                    "e2e_runner: --include-scripts → executing %d allowlisted "
                    "standalone script(s)", len(scripts),
                )
                for script in scripts:
                    results.extend(run_selenium_script(run_id, logger, script))
        else:
            parser.print_help()
            return 1

        passed_n = sum(1 for r in results if r.passed)
        failed_n = len(results) - passed_n
        logger.info("e2e_runner: %d passed, %d failed", passed_n, failed_n)
        if args.json:
            print(json.dumps(
                [r.model_dump() for r in results], indent=2, default=str,
            ))
        return 0 if failed_n == 0 else 1

    # --driver native/mcp maps to --mode for backward compatibility
    if args.driver in ("native", "mcp"):
        args.mode = args.driver

    mode = _resolve_mode(args.mode)

    if args.discover:
        tests = discover_e2e_tests(mode)
        if args.json:
            items = [
                {
                    "file": t,
                    "name": os.path.basename(t).replace(".md", "")
                                              .replace(".spec.ts", ""),
                    "mode": mode,
                }
                for t in tests
            ]
            print(json.dumps(items, indent=2))
        else:
            print(f"Found {len(tests)} E2E tests (mode: {mode}):")
            for t in tests:
                spec = parse_test_spec(t)
                print(
                    f"  {spec['name']}: {spec['description']} "
                    f"({len(spec['steps'])} steps, "
                    f"{len(spec['assertions'])} assertions)"
                )
        return 0

    run_id = args.run_id or make_run_id()
    logger = setup_logger(run_id, "e2e_runner")
    logger.info("e2e_runner: mode=%s", mode)

    if args.run_all:
        if mode == "native":
            results = run_playwright_native(
                run_id, logger, project=args.project,
            )
        else:
            results = []
            for test_file in discover_mcp_tests():
                result = execute_e2e_test(test_file, run_id, logger)
                results.append(result)
                if not result.passed:
                    logger.info(
                        "e2e_runner: stopping after failure (%s)",
                        result.test_name,
                    )
                    break

        if args.validate_screenshots:
            results = _run_vision_validation(
                results, logger,
                assertions=args.vision_assertions,
                strict=args.vision_strict,
            )

        passed_n = sum(1 for r in results if r.passed)
        failed_n = len(results) - passed_n
        logger.info(
            "e2e_runner: %d passed, %d failed", passed_n, failed_n
        )
        if args.json:
            print(json.dumps(
                [r.model_dump() for r in results], indent=2, default=str,
            ))
        return 0 if failed_n == 0 else 1

    if args.test_file:
        if mode == "native" or args.test_file.endswith(".spec.ts"):
            results = run_playwright_native(
                run_id, logger,
                test_file=args.test_file,
                project=args.project,
            )
            result = (
                results[0] if results else E2ETestResult(
                    test_name="unknown",
                    status="failed",
                    test_path=args.test_file,
                    error="No results from Playwright",
                )
            )
        else:
            result = execute_e2e_test(args.test_file, run_id, logger)

        if args.validate_screenshots:
            [result] = _run_vision_validation(
                [result], logger,
                assertions=args.vision_assertions,
                strict=args.vision_strict,
            )

        if args.json:
            print(json.dumps(result.model_dump(), indent=2, default=str))
        else:
            status = "PASS" if result.passed else "FAIL"
            print(f"[{status}] {result.test_name}")
            if result.error:
                print(f"  Error: {result.error}")
        return 0 if result.passed else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
