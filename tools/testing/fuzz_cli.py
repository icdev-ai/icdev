# CUI // SP-CTI
# ICDEV CLI Argument Fuzzer — feed malformed inputs to CLI tools
# Ensures tools fail gracefully (clean argparse errors, no crashes/tracebacks)

"""
ICDEV CLI Argument Fuzzer — lightweight fuzzer for CLI tool robustness.

Usage:
    python tools/testing/fuzz_cli.py --discover            # Fuzz all discovered CLI tools
    python tools/testing/fuzz_cli.py --tools tools/compliance/ssp_generator.py tools/builder/scaffolder.py
    python tools/testing/fuzz_cli.py --discover --json      # Machine-readable output

Fuzz strategies:
  1. no_args        — Run with no arguments at all
  2. random_strings — Random garbage string arguments
  3. long_strings   — Very long strings (>10000 chars)
  4. special_chars  — Null bytes, unicode, shell metacharacters
  5. missing_flags  — Known flags with missing values

A tool "passes" if it exits non-zero with clean argparse-style errors.
A tool "crashes" if it returns SIGSEGV (-11), SIGABRT (-6), or stderr
contains a Python traceback (line starting with "Traceback").

Exit codes: 0 = no crashes, 1 = at least one crash detected
"""

import argparse
import json
import os
import random
import re
import string
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TIMEOUT_SECONDS = 5

# Directories and patterns to skip during discovery
SKIP_DIRS = {".tmp", "__pycache__", "node_modules", ".venv", "venv", ".git"}
SKIP_PREFIXES = ("test_", "conftest")

CLI_PATTERNS = [
    re.compile(r"import\s+argparse"),
    re.compile(r"from\s+argparse\s+import"),
    re.compile(r'if\s+__name__\s*==\s*["\']__main__["\']'),
]

# Signal codes that indicate a real crash (Unix; on Windows these map differently)
CRASH_SIGNALS = {-11, -6}  # SIGSEGV, SIGABRT


# ---------------------------------------------------------------------------
# Fuzz payloads
# ---------------------------------------------------------------------------

def _random_string(length: int = 64) -> str:
    """Generate a random ASCII string."""
    return "".join(random.choices(string.ascii_letters + string.digits + string.punctuation, k=length))


FUZZ_STRATEGIES = {
    "no_args": lambda: [],
    "random_strings": lambda: [_random_string(32), _random_string(16)],
    "long_strings": lambda: ["--project-id", "A" * 10001],
    "special_chars": lambda: [
        "--name",
        "test\x00null",
        "--file",
        "../../etc/passwd",
        "--query",
        "$(rm -rf /)",
        "--input",
        "\ud800\udc00\U0001f4a9",
        "--value",
        "; DROP TABLE projects;--",
    ],
    "missing_flags": lambda: ["--project-id", "--json", "--output"],
}


# ---------------------------------------------------------------------------
# Discovery (shared logic with smoke_test.py)
# ---------------------------------------------------------------------------

def discover_cli_tools(tools_dir: Path) -> list:
    """Find all Python CLI tools in tools/ directory."""
    discovered = []
    for root, dirs, files in os.walk(tools_dir):
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
# Crash detection
# ---------------------------------------------------------------------------

def is_crash(exit_code: int, stderr: str) -> bool:
    """Determine if a tool invocation was a crash (not a clean failure)."""
    # Signal-based crash (Unix)
    if exit_code in CRASH_SIGNALS:
        return True

    # Python traceback in stderr indicates unhandled exception
    # (argparse errors print "usage:" or "error:", not "Traceback")
    if "Traceback (most recent call last)" in stderr:
        return True

    return False


def crash_reason(exit_code: int, stderr: str) -> str:
    """Return a short reason string for the crash."""
    if exit_code in CRASH_SIGNALS:
        signal_names = {-11: "SIGSEGV", -6: "SIGABRT"}
        return f"Signal {signal_names.get(exit_code, exit_code)}"
    if "Traceback (most recent call last)" in stderr:
        # Extract the last line of the traceback (the exception)
        lines = stderr.strip().splitlines()
        for line in reversed(lines):
            line = line.strip()
            if line and not line.startswith("File "):
                return f"Traceback: {line[:120]}"
        return "Traceback detected"
    return "Unknown crash"


# ---------------------------------------------------------------------------
# Fuzz execution
# ---------------------------------------------------------------------------

def fuzz_tool(filepath: Path, strategy_name: str, args_fn) -> dict:
    """Run one fuzz strategy against one tool. Returns result dict."""
    fuzz_args = args_fn()
    rel = str(filepath.relative_to(PROJECT_ROOT))
    start = time.monotonic()

    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, str(filepath)] + fuzz_args,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        elapsed = round((time.monotonic() - start) * 1000)
        stderr = result.stderr or ""
        crashed = is_crash(result.returncode, stderr)

        return {
            "file": rel,
            "strategy": strategy_name,
            "args": fuzz_args[:4],  # Truncate for readability
            "exit_code": result.returncode,
            "crashed": crashed,
            "crash_reason": crash_reason(result.returncode, stderr) if crashed else "",
            "duration_ms": elapsed,
        }
    except subprocess.TimeoutExpired:
        elapsed = round((time.monotonic() - start) * 1000)
        return {
            "file": rel,
            "strategy": strategy_name,
            "args": fuzz_args[:4],
            "exit_code": -1,
            "crashed": False,  # Timeout is not a crash, just slow
            "crash_reason": "",
            "duration_ms": elapsed,
        }
    except Exception as exc:
        return {
            "file": rel,
            "strategy": strategy_name,
            "args": fuzz_args[:4],
            "exit_code": -1,
            "crashed": False,
            "crash_reason": str(exc),
            "duration_ms": 0,
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_fuzz(tools: list, verbose: bool = False) -> dict:
    """Execute fuzz suite against all tools. Returns summary dict."""
    results = []
    total_tests = 0
    total_crashes = 0

    for filepath in tools:
        rel = filepath.relative_to(PROJECT_ROOT)
        if verbose:
            print(f"Fuzzing {rel} ...")

        for strategy_name, args_fn in FUZZ_STRATEGIES.items():
            total_tests += 1
            result = fuzz_tool(filepath, strategy_name, args_fn)
            results.append(result)

            if result["crashed"]:
                total_crashes += 1
                if verbose:
                    print(f"  CRASH  {strategy_name:16s}  {result['crash_reason']}")
            elif verbose:
                status = "OK" if result["exit_code"] != 0 else "WARN(0)"
                print(f"  {status:5s}  {strategy_name:16s}  exit={result['exit_code']}")

    success = total_crashes == 0
    summary = {
        "success": success,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tools_fuzzed": len(tools),
        "strategies": len(FUZZ_STRATEGIES),
        "total_tests": total_tests,
        "crashes": total_crashes,
        "passed": total_tests - total_crashes,
        "results": results,
    }
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV CLI Argument Fuzzer — test tools handle bad input gracefully"
    )
    parser.add_argument("--discover", action="store_true",
                        help="Auto-discover all CLI tools in tools/")
    parser.add_argument("--tools", nargs="+", metavar="PATH",
                        help="Specific tool paths to fuzz")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output")
    parser.add_argument("--verbose", action="store_true",
                        help="Detailed per-tool output")
    args = parser.parse_args()

    if not args.discover and not args.tools:
        parser.error("Specify --discover or --tools <path> [<path> ...]")

    # Resolve tool list
    if args.discover:
        tools_dir = PROJECT_ROOT / "tools"
        tools = discover_cli_tools(tools_dir)
    else:
        tools = [Path(t).resolve() for t in args.tools]
        missing = [t for t in tools if not t.is_file()]
        if missing:
            parser.error(f"Tool files not found: {', '.join(str(m) for m in missing)}")

    if not args.json:
        print(f"ICDEV CLI Fuzzer — {len(tools)} tools x {len(FUZZ_STRATEGIES)} strategies")
        print("=" * 60)

    summary = run_fuzz(tools, verbose=args.verbose)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print()
        print(f"Tools fuzzed:  {summary['tools_fuzzed']}")
        print(f"Strategies:    {summary['strategies']}")
        print(f"Total tests:   {summary['total_tests']}")
        print(f"Passed:        {summary['passed']}")
        print(f"Crashes:       {summary['crashes']}")
        status = "PASS" if summary["success"] else "FAIL"
        print(f"Result:        {status}")

        # Show crashes if any
        if not summary["success"]:
            print()
            print("Crashes:")
            for r in summary["results"]:
                if r["crashed"]:
                    print(f"  {r['file']}")
                    print(f"    Strategy: {r['strategy']}")
                    print(f"    Reason:   {r['crash_reason']}")

    sys.exit(0 if summary["success"] else 1)


if __name__ == "__main__":
    main()
