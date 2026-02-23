# [TEMPLATE: CUI // SP-CTI]
# ICDEV Smoke Test — verify all CLI tools are importable and --help works
# Catches import errors after bulk refactors (renames, ruff cleanup, etc.)

"""
ICDEV Smoke Test — validates all CLI tools compile and respond to --help.

Usage:
    python tools/testing/smoke_test.py                # Full smoke test
    python tools/testing/smoke_test.py --quick         # py_compile only (fast)
    python tools/testing/smoke_test.py --json          # Machine-readable output
    python tools/testing/smoke_test.py --verbose       # Detailed per-tool output

Discovers all Python files in tools/ that contain argparse or __main__ patterns,
then runs py_compile and --help on each. Reports pass/fail summary.

Exit codes: 0 = all pass, 1 = any fail
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TIMEOUT_SECONDS = 10

# Directories and patterns to skip
SKIP_DIRS = {".tmp", "__pycache__", "node_modules", ".venv", "venv", ".git"}
SKIP_PREFIXES = ("test_", "conftest")

# Patterns that indicate a file is a CLI tool
CLI_PATTERNS = [
    re.compile(r"import\s+argparse"),
    re.compile(r"from\s+argparse\s+import"),
    re.compile(r'if\s+__name__\s*==\s*["\']__main__["\']'),
]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_cli_tools(tools_dir: Path) -> list:
    """Find all Python CLI tools in tools/ directory."""
    discovered = []
    for root, dirs, files in os.walk(tools_dir):
        # Prune skipped directories in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for filename in sorted(files):
            if not filename.endswith(".py"):
                continue
            if filename.startswith(SKIP_PREFIXES):
                continue

            filepath = Path(root) / filename
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            for pattern in CLI_PATTERNS:
                if pattern.search(content):
                    discovered.append(filepath)
                    break

    return discovered


# ---------------------------------------------------------------------------
# Test execution
# ---------------------------------------------------------------------------

def run_py_compile(filepath: Path) -> dict:
    """Run py_compile on a single file. Returns result dict."""
    start = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(filepath)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
        elapsed = round((time.monotonic() - start) * 1000)
        return {
            "check": "py_compile",
            "file": str(filepath.relative_to(PROJECT_ROOT)),
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "stderr": result.stderr.strip() if result.returncode != 0 else "",
            "duration_ms": elapsed,
        }
    except subprocess.TimeoutExpired:
        elapsed = round((time.monotonic() - start) * 1000)
        return {
            "check": "py_compile",
            "file": str(filepath.relative_to(PROJECT_ROOT)),
            "passed": False,
            "exit_code": -1,
            "stderr": f"Timeout after {TIMEOUT_SECONDS}s",
            "duration_ms": elapsed,
        }
    except Exception as exc:
        return {
            "check": "py_compile",
            "file": str(filepath.relative_to(PROJECT_ROOT)),
            "passed": False,
            "exit_code": -1,
            "stderr": str(exc),
            "duration_ms": 0,
        }


def run_help(filepath: Path) -> dict:
    """Run --help on a single file. Returns result dict."""
    start = time.monotonic()
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, str(filepath), "--help"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        elapsed = round((time.monotonic() - start) * 1000)
        return {
            "check": "help",
            "file": str(filepath.relative_to(PROJECT_ROOT)),
            "passed": result.returncode == 0,
            "exit_code": result.returncode,
            "stderr": result.stderr.strip() if result.returncode != 0 else "",
            "duration_ms": elapsed,
        }
    except subprocess.TimeoutExpired:
        elapsed = round((time.monotonic() - start) * 1000)
        return {
            "check": "help",
            "file": str(filepath.relative_to(PROJECT_ROOT)),
            "passed": False,
            "exit_code": -1,
            "stderr": f"Timeout after {TIMEOUT_SECONDS}s",
            "duration_ms": elapsed,
        }
    except Exception as exc:
        return {
            "check": "help",
            "file": str(filepath.relative_to(PROJECT_ROOT)),
            "passed": False,
            "exit_code": -1,
            "stderr": str(exc),
            "duration_ms": 0,
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_smoke_test(quick: bool = False, verbose: bool = False) -> dict:
    """Execute the smoke test suite. Returns summary dict."""
    tools_dir = PROJECT_ROOT / "tools"
    if not tools_dir.is_dir():
        return {
            "success": False,
            "error": f"tools/ directory not found at {tools_dir}",
            "tools_tested": 0,
            "passed": 0,
            "failed": 0,
            "results": [],
        }

    tools = discover_cli_tools(tools_dir)
    if verbose:
        print(f"Discovered {len(tools)} CLI tools in tools/")

    results = []
    total_passed = 0
    total_failed = 0

    for filepath in tools:
        rel = filepath.relative_to(PROJECT_ROOT)

        # Step 1: py_compile
        compile_result = run_py_compile(filepath)
        results.append(compile_result)

        if compile_result["passed"]:
            if verbose:
                print(f"  PASS  py_compile  {rel}")
        else:
            total_failed += 1
            if verbose:
                print(f"  FAIL  py_compile  {rel}")
                if compile_result["stderr"]:
                    print(f"        {compile_result['stderr'][:200]}")
            continue  # Skip --help if compile fails

        # Step 2: --help (unless --quick)
        if not quick:
            help_result = run_help(filepath)
            results.append(help_result)

            if help_result["passed"]:
                if verbose:
                    print(f"  PASS  --help      {rel}")
                total_passed += 1
            else:
                total_failed += 1
                if verbose:
                    print(f"  FAIL  --help      {rel}")
                    if help_result["stderr"]:
                        print(f"        {help_result['stderr'][:200]}")
        else:
            total_passed += 1

    success = total_failed == 0
    summary = {
        "success": success,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "quick" if quick else "full",
        "tools_tested": len(tools),
        "passed": total_passed,
        "failed": total_failed,
        "results": results,
    }
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Smoke Test — verify all CLI tools compile and --help works"
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--quick", action="store_true", help="py_compile only (skip --help)")
    parser.add_argument("--verbose", action="store_true", help="Detailed per-tool output")
    args = parser.parse_args()

    if not args.json:
        mode_label = "quick (py_compile only)" if args.quick else "full (py_compile + --help)"
        print(f"ICDEV Smoke Test — {mode_label}")
        print("=" * 60)

    summary = run_smoke_test(quick=args.quick, verbose=args.verbose)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print()
        print(f"Tools tested: {summary['tools_tested']}")
        print(f"Passed:       {summary['passed']}")
        print(f"Failed:       {summary['failed']}")
        status = "PASS" if summary["success"] else "FAIL"
        print(f"Result:       {status}")

        # Show failures if any
        if not summary["success"]:
            print()
            print("Failures:")
            for r in summary["results"]:
                if not r["passed"]:
                    print(f"  {r['check']:12s}  {r['file']}")
                    if r.get("stderr"):
                        for line in r["stderr"].splitlines()[:3]:
                            print(f"               {line}")

    sys.exit(0 if summary["success"] else 1)


if __name__ == "__main__":
    main()
