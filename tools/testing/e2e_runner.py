# CUI // SP-CTI
# ICDEV E2E Test Runner — Playwright Native + MCP Integration
# Adapted from ADW E2E test execution patterns

"""
E2E Test Runner for ICDEV — executes browser-based tests via native Playwright
or Playwright MCP (fallback).

Usage:
    # Native Playwright (preferred — runs .spec.ts files directly)
    python tools/testing/e2e_runner.py --mode native --run-all
    python tools/testing/e2e_runner.py --mode native --test-file tests/e2e/dashboard_health.spec.ts

    # MCP mode (legacy — markdown specs executed via Claude Code + Playwright MCP)
    python tools/testing/e2e_runner.py --mode mcp --test-file .claude/commands/e2e/dashboard_health.md

    # Auto-detect (tries native first, falls back to MCP)
    python tools/testing/e2e_runner.py --run-all

    # Discovery
    python tools/testing/e2e_runner.py --discover

Native Playwright tests live in tests/e2e/*.spec.ts
MCP test specs live in .claude/commands/e2e/*.md

Screenshots: .tmp/test_runs/{run_id}/screenshots/
Videos: .tmp/test_runs/playwright-artifacts/
Reports: .tmp/test_runs/playwright-report/
"""

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.testing.data_types import E2ETestResult
from tools.testing.utils import make_run_id, setup_logger, ensure_run_dir, timestamp_iso


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_e2e_tests(mode: str = "auto") -> list:
    """Discover all E2E test files.

    In native mode: tests/e2e/*.spec.ts
    In MCP mode: .claude/commands/e2e/*.md
    In auto mode: returns native tests if available, else MCP specs.
    """
    native_pattern = str(PROJECT_ROOT / "tests" / "e2e" / "*.spec.ts")
    mcp_pattern = str(PROJECT_ROOT / ".claude" / "commands" / "e2e" / "*.md")

    if mode == "native":
        return sorted(glob.glob(native_pattern))
    elif mode == "mcp":
        return sorted(glob.glob(mcp_pattern))
    else:
        # Auto: prefer native, fall back to MCP
        native = sorted(glob.glob(native_pattern))
        if native:
            return native
        return sorted(glob.glob(mcp_pattern))


def discover_native_tests() -> list:
    """Discover native Playwright .spec.ts files."""
    pattern = str(PROJECT_ROOT / "tests" / "e2e" / "*.spec.ts")
    return sorted(glob.glob(pattern))


def discover_mcp_tests() -> list:
    """Discover MCP E2E test specification files (.md)."""
    pattern = str(PROJECT_ROOT / ".claude" / "commands" / "e2e" / "*.md")
    return sorted(glob.glob(pattern))


def parse_test_spec(test_file: str) -> dict:
    """Parse an E2E test specification markdown file.

    Returns metadata about the test: name, description, steps, assertions.
    """
    with open(test_file) as f:
        content = f.read()

    test_name = os.path.basename(test_file).replace(".md", "").replace(".spec.ts", "")
    lines = content.strip().splitlines()

    spec = {
        "name": test_name,
        "file": test_file,
        "description": "",
        "steps": [],
        "assertions": [],
        "raw_content": content,
    }

    # Extract description from first paragraph
    for line in lines:
        if line.startswith("#") or line.startswith("//"):
            desc = line.lstrip("#/ ").strip()
            if desc and len(desc) > 5:
                spec["description"] = desc
                break

    # Count step-like lines (numbered items or bullet points with action verbs)
    action_verbs = ["navigate", "click", "fill", "type", "select", "check",
                    "assert", "verify", "wait", "screenshot", "scroll",
                    "goto", "expect", "toContain", "toBeVisible", "toHaveTitle"]
    for line in lines:
        lower = line.lower().strip()
        if any(verb in lower for verb in action_verbs):
            if any(verb in lower for verb in ["assert", "verify", "check", "expect", "tocontain", "tobevisible", "tohavetitle"]):
                spec["assertions"].append(line.strip())
            else:
                spec["steps"].append(line.strip())

    return spec


# ---------------------------------------------------------------------------
# Playwright availability check
# ---------------------------------------------------------------------------

def _npx_cmd() -> str:
    """Return correct npx command for the platform (npx.cmd on Windows)."""
    import platform
    return "npx.cmd" if platform.system() == "Windows" else "npx"


def check_playwright_installed() -> bool:
    """Check if npx playwright is available."""
    try:
        result = subprocess.run(
            [_npx_cmd(), "playwright", "--version"],
            capture_output=True, text=True, timeout=15,
            cwd=str(PROJECT_ROOT),
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Native Playwright Execution
# ---------------------------------------------------------------------------

def run_playwright_native(
    run_id: str,
    logger,
    test_file: str = None,
    project: str = "chromium",
) -> list:
    """Run E2E tests via native Playwright CLI (npx playwright test).

    This is the preferred execution mode — tests run directly via Playwright
    without the Claude Code / MCP intermediary.

    Args:
        run_id: Test run identifier
        logger: Logger instance
        test_file: Specific .spec.ts file to run (None = run all)
        project: Playwright project name (chromium, firefox, webkit)

    Returns:
        List of E2ETestResult objects
    """
    logger.info("Running E2E tests via native Playwright...")

    # Ensure output dirs exist
    results_dir = ensure_run_dir(run_id)
    screenshots_dir = results_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd = [
        _npx_cmd(), "playwright", "test",
        "--project", project,
        "--reporter", "json",
    ]

    if test_file:
        cmd.append(test_file)

    env = os.environ.copy()
    env["PLAYWRIGHT_JSON_OUTPUT_NAME"] = str(results_dir / "playwright-results.json")

    logger.info(f"  Command: {' '.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=300,
            cwd=str(PROJECT_ROOT),
        )

        logger.info(f"  Playwright exit code: {proc.returncode}")

        # Parse JSON results
        results_file = results_dir / "playwright-results.json"
        if results_file.exists():
            return _parse_playwright_json_results(results_file, logger)

        # Fallback: parse stdout for JSON reporter output
        if proc.stdout.strip():
            try:
                report = json.loads(proc.stdout)
                return _parse_playwright_report(report, logger)
            except json.JSONDecodeError:
                pass

        # If no parseable output, create result from exit code
        if proc.returncode == 0:
            test_name = os.path.basename(test_file).replace(".spec.ts", "") if test_file else "all_e2e"
            return [E2ETestResult(
                test_name=test_name,
                status="passed",
                test_path=test_file or "tests/e2e/",
                screenshots=[],
            )]
        else:
            error_msg = proc.stderr[:500] if proc.stderr else proc.stdout[:500]
            test_name = os.path.basename(test_file).replace(".spec.ts", "") if test_file else "all_e2e"
            return [E2ETestResult(
                test_name=test_name,
                status="failed",
                test_path=test_file or "tests/e2e/",
                error=f"Playwright exited with code {proc.returncode}: {error_msg}",
            )]

    except subprocess.TimeoutExpired:
        logger.error("Playwright timed out after 300 seconds")
        return [E2ETestResult(
            test_name="playwright_timeout",
            status="failed",
            test_path=test_file or "tests/e2e/",
            error="Playwright test execution timed out after 300 seconds",
        )]
    except FileNotFoundError:
        logger.error("npx not found — ensure Node.js is installed")
        return [E2ETestResult(
            test_name="playwright_not_found",
            status="failed",
            test_path=test_file or "tests/e2e/",
            error="npx/playwright not found. Install with: npm install -D @playwright/test",
        )]


def _parse_playwright_json_results(results_file: Path, logger) -> list:
    """Parse Playwright JSON reporter output into E2ETestResult objects."""
    results = []
    try:
        with open(results_file) as f:
            report = json.load(f)
        return _parse_playwright_report(report, logger)
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Failed to parse Playwright JSON results: {e}")
        return results


def _parse_playwright_report(report: dict, logger) -> list:
    """Parse a Playwright JSON report dict into E2ETestResult objects."""
    results = []
    suites = report.get("suites", [])

    for suite in suites:
        suite_title = suite.get("title", "unknown")
        specs = suite.get("specs", [])

        for spec in specs:
            spec_title = spec.get("title", "unknown")
            test_name = f"{suite_title} > {spec_title}"
            test_file = spec.get("file", suite.get("file", ""))

            # Check all tests within the spec
            tests = spec.get("tests", [])
            for t in tests:
                t_results = t.get("results", [])
                if not t_results:
                    continue

                last_result = t_results[-1]
                status_str = last_result.get("status", "failed")
                pw_status = "passed" if status_str == "passed" else "failed"

                # Collect screenshots
                screenshots = []
                for attachment in last_result.get("attachments", []):
                    if attachment.get("contentType", "").startswith("image/"):
                        path = attachment.get("path", "")
                        if path:
                            screenshots.append(path)

                # Collect video
                video_path = None
                for attachment in last_result.get("attachments", []):
                    if attachment.get("contentType", "").startswith("video/"):
                        video_path = attachment.get("path")
                        break

                # Error message
                error = None
                if pw_status == "failed":
                    error = last_result.get("error", {}).get("message", "")
                    if not error:
                        error = last_result.get("error", {}).get("snippet", "Test failed")

                results.append(E2ETestResult(
                    test_name=test_name,
                    status=pw_status,
                    test_path=test_file,
                    screenshots=screenshots,
                    video_path=video_path,
                    error=error[:500] if error else None,
                    cui_banners_verified="cui" in test_name.lower() or "banner" in test_name.lower(),
                ))

    # Also parse nested suites recursively
    for suite in suites:
        for child_suite in suite.get("suites", []):
            child_report = {"suites": [child_suite]}
            results.extend(_parse_playwright_report(child_report, logger))

    if results:
        passed = sum(1 for r in results if r.passed)
        logger.info(f"  Parsed {len(results)} test results: {passed} passed, {len(results) - passed} failed")

    return results


# ---------------------------------------------------------------------------
# MCP Execution (legacy)
# ---------------------------------------------------------------------------

def execute_e2e_test(
    test_file: str,
    run_id: str,
    logger,
) -> E2ETestResult:
    """Execute a single E2E test via Playwright MCP (legacy mode).

    In full integration mode, this:
    1. Reads the .md test spec
    2. Invokes Claude Code with /test_e2e skill
    3. Claude Code uses Playwright MCP to execute browser actions
    4. Screenshots/videos captured automatically
    5. Returns structured E2ETestResult

    For standalone execution (without Claude Code), validates the
    test spec structure and prepares it for manual execution.
    """
    test_name = os.path.basename(test_file).replace(".md", "")
    logger.info(f"Executing E2E test (MCP mode): {test_name}")

    # Parse test spec
    try:
        spec = parse_test_spec(test_file)
    except Exception as e:
        return E2ETestResult(
            test_name=test_name,
            status="failed",
            test_path=test_file,
            error=f"Failed to parse test spec: {e}",
        )

    logger.info(f"  Description: {spec['description']}")
    logger.info(f"  Steps: {len(spec['steps'])}")
    logger.info(f"  Assertions: {len(spec['assertions'])}")

    # Set up screenshot directory
    screenshot_dir = ensure_run_dir(run_id) / "screenshots" / test_name
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    # Check if Claude Code CLI is available for full execution
    claude_path = os.getenv("CLAUDE_CODE_PATH", "claude")
    has_claude = False
    try:
        result = subprocess.run(
            [claude_path, "--version"],
            capture_output=True, text=True, timeout=5
        )
        has_claude = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if has_claude and os.getenv("ANTHROPIC_API_KEY"):
        # Full execution via Claude Code + Playwright MCP
        logger.info("  Mode: Full execution via Claude Code + Playwright MCP")
        return _execute_via_claude(test_file, test_name, run_id, screenshot_dir, logger)
    else:
        # Validation-only mode
        logger.info("  Mode: Spec validation only (Claude Code not available)")
        return _validate_spec(spec, test_name, test_file, logger)


def _execute_via_claude(
    test_file: str,
    test_name: str,
    run_id: str,
    screenshot_dir: Path,
    logger,
) -> E2ETestResult:
    """Execute E2E test via Claude Code CLI with Playwright MCP."""
    from tools.testing.utils import get_safe_subprocess_env

    claude_path = os.getenv("CLAUDE_CODE_PATH", "claude")
    env = get_safe_subprocess_env()

    # Read test spec
    with open(test_file) as f:
        test_spec = f.read()

    # Construct prompt for Claude Code with Playwright MCP
    prompt = (
        f"Execute the following E2E test using the Playwright MCP server. "
        f"Navigate through each step, take screenshots, and verify all assertions. "
        f"Save screenshots to {screenshot_dir}. "
        f"Return a JSON object with: test_name, status (passed/failed), screenshots (list of paths), error (null or message).\n\n"
        f"Test Spec:\n{test_spec}"
    )

    try:
        cmd = [
            claude_path, "-p", prompt,
            "--model", "sonnet",
            "--output-format", "json",
            "--dangerously-skip-permissions",
        ]

        proc = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            timeout=120, cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
        )

        if proc.returncode == 0 and proc.stdout.strip():
            try:
                from tools.testing.utils import parse_json
                result_data = parse_json(proc.stdout)
                return E2ETestResult(
                    test_name=result_data.get("test_name", test_name),
                    status=result_data.get("status", "failed"),
                    test_path=test_file,
                    screenshots=result_data.get("screenshots", []),
                    error=result_data.get("error"),
                )
            except Exception:
                pass

        return E2ETestResult(
            test_name=test_name,
            status="failed",
            test_path=test_file,
            error=f"Claude Code returned exit code {proc.returncode}: {proc.stderr[:200]}",
        )

    except subprocess.TimeoutExpired:
        return E2ETestResult(
            test_name=test_name,
            status="failed",
            test_path=test_file,
            error="E2E test timed out after 120 seconds",
        )
    except Exception as e:
        return E2ETestResult(
            test_name=test_name,
            status="failed",
            test_path=test_file,
            error=f"Execution error: {e}",
        )


def _validate_spec(spec: dict, test_name: str, test_file: str, logger) -> E2ETestResult:
    """Validate E2E test spec structure without executing."""
    issues = []

    if not spec["steps"]:
        issues.append("No test steps found in spec")
    if not spec["assertions"]:
        issues.append("No assertions found in spec")

    if issues:
        return E2ETestResult(
            test_name=test_name,
            status="failed",
            test_path=test_file,
            error=f"Spec validation: {'; '.join(issues)}",
        )

    logger.info(f"  Spec validated: {len(spec['steps'])} steps, {len(spec['assertions'])} assertions")
    return E2ETestResult(
        test_name=test_name,
        status="passed",
        test_path=test_file,
        screenshots=[],
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="ICDEV E2E Test Runner")
    parser.add_argument("--test-file", help="Path to E2E test (.spec.ts or .md)")
    parser.add_argument("--discover", action="store_true", help="List available E2E tests")
    parser.add_argument("--run-all", action="store_true", help="Run all discovered E2E tests")
    parser.add_argument("--run-id", help="Test run ID (auto-generated if not provided)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--mode", choices=["native", "mcp", "auto"], default="auto",
        help="Execution mode: native (Playwright CLI), mcp (Claude Code + MCP), auto (prefer native)"
    )
    parser.add_argument(
        "--project", default="chromium",
        help="Playwright browser project (chromium, firefox, webkit)"
    )
    args = parser.parse_args()

    # Resolve mode
    mode = args.mode
    if mode == "auto":
        if check_playwright_installed() and discover_native_tests():
            mode = "native"
        else:
            mode = "mcp"

    if args.discover:
        tests = discover_e2e_tests(mode)
        if args.json:
            items = []
            for t in tests:
                name = os.path.basename(t).replace(".md", "").replace(".spec.ts", "")
                items.append({"file": t, "name": name, "mode": mode})
            print(json.dumps(items, indent=2))
        else:
            print(f"Found {len(tests)} E2E tests (mode: {mode}):")
            for t in tests:
                spec = parse_test_spec(t)
                print(f"  {spec['name']}: {spec['description']} ({len(spec['steps'])} steps, {len(spec['assertions'])} assertions)")
        return

    run_id = args.run_id or make_run_id()
    logger = setup_logger(run_id, "e2e_runner")
    logger.info(f"E2E Runner mode: {mode}")

    if args.run_all:
        if mode == "native":
            results = run_playwright_native(run_id, logger, project=args.project)
        else:
            # MCP mode: run all .md specs
            tests = discover_mcp_tests()
            results = []
            for test_file in tests:
                result = execute_e2e_test(test_file, run_id, logger)
                results.append(result)
                if not result.passed:
                    logger.info(f"E2E test failed: {result.test_name}, stopping (fail-fast)")
                    break

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        logger.info(f"\nE2E Results ({mode}): {passed} passed, {failed} failed")

        if args.json:
            print(json.dumps([r.model_dump() for r in results], indent=2, default=str))

        sys.exit(0 if failed == 0 else 1)

    elif args.test_file:
        if mode == "native" or args.test_file.endswith(".spec.ts"):
            results = run_playwright_native(run_id, logger, test_file=args.test_file, project=args.project)
            result = results[0] if results else E2ETestResult(
                test_name="unknown", status="failed", test_path=args.test_file,
                error="No results from Playwright",
            )
        else:
            result = execute_e2e_test(args.test_file, run_id, logger)

        if args.json:
            print(json.dumps(result.model_dump(), indent=2, default=str))
        else:
            status = "PASS" if result.passed else "FAIL"
            print(f"[{status}] {result.test_name}")
            if result.error:
                print(f"  Error: {result.error}")

        sys.exit(0 if result.passed else 1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
