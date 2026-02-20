#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Platform Compatibility Checker.

Validates that the current OS environment can run ICDEV tools.
Run on first setup to catch compatibility issues early.

Usage:
    python tools/testing/platform_check.py          # Human output
    python tools/testing/platform_check.py --json   # JSON output
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def check_python_version() -> dict:
    """Python 3.9+ required for graphlib.TopologicalSorter."""
    v = sys.version_info
    ok = v >= (3, 9)
    return {
        "check": "python_version",
        "ok": ok,
        "value": f"{v.major}.{v.minor}.{v.micro}",
        "required": ">=3.9",
    }


def check_temp_dir() -> dict:
    """Verify writable temp directory."""
    tmp = tempfile.gettempdir()
    writable = os.access(tmp, os.W_OK)
    return {"check": "temp_directory", "ok": writable, "value": tmp}


def check_home_dir() -> dict:
    """Verify home directory resolution."""
    home = str(Path.home())
    exists = Path(home).is_dir()
    return {"check": "home_directory", "ok": exists, "value": home}


def check_data_dir() -> dict:
    """Verify data directory exists or is creatable."""
    data = PROJECT_ROOT / "data"
    exists = data.is_dir()
    return {
        "check": "data_directory",
        "ok": exists,
        "value": str(data),
        "note": "Run /initialize to create" if not exists else "",
    }


def check_git() -> dict:
    """Git must be available."""
    git = shutil.which("git")
    return {"check": "git", "ok": git is not None, "value": git or "not found"}


def check_npx() -> dict:
    """npx for Playwright E2E tests."""
    from tools.compat.platform_utils import get_npx_cmd

    cmd = get_npx_cmd()
    found = shutil.which(cmd)
    return {
        "check": "npx",
        "ok": found is not None,
        "value": found or "not found",
        "note": "Optional -- needed for E2E tests only",
    }


def check_utf8_locale() -> dict:
    """Check UTF-8 support."""
    encoding = sys.stdout.encoding or "unknown"
    ok = "utf" in encoding.lower()
    return {"check": "utf8_support", "ok": ok, "value": encoding}


def check_platform() -> dict:
    """Report current platform."""
    from tools.compat.platform_utils import PLATFORM_NAME

    return {"check": "platform", "ok": True, "value": PLATFORM_NAME}


def run_all_checks() -> list:
    checks = [
        check_platform(),
        check_python_version(),
        check_temp_dir(),
        check_home_dir(),
        check_data_dir(),
        check_git(),
        check_npx(),
        check_utf8_locale(),
    ]
    return checks


def main():
    import argparse

    parser = argparse.ArgumentParser(description="ICDEV platform compatibility check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    results = run_all_checks()

    if args.json:
        print(
            json.dumps(
                {"checks": results, "all_ok": all(r["ok"] for r in results)},
                indent=2,
            )
        )
    else:
        from tools.compat.platform_utils import PLATFORM_NAME

        print(f"ICDEV Platform Check -- {PLATFORM_NAME}")
        print("=" * 50)
        for r in results:
            status = "PASS" if r["ok"] else "FAIL"
            print(f"  [{status}] {r['check']}: {r['value']}")
            if r.get("note"):
                print(f"         Note: {r['note']}")
        ok = all(r["ok"] for r in results)
        print(f"\nOverall: {'PASS' if ok else 'FAIL'}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
