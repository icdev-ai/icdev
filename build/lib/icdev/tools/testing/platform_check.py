#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Platform Compatibility Checker.

Validates that the current OS environment can run ICDEV™ tools.
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
    """Check UTF-8 support in stdout and filesystem.

    On Windows, stdout may be cp1252 (normal for cmd/PowerShell).
    What matters is that filesystem encoding is UTF-8, which Python 3.7+
    ensures via UTF-8 mode. We pass if either stdout or fs is UTF-8.
    """
    stdout_enc = sys.stdout.encoding or "unknown"
    fs_enc = sys.getfilesystemencoding()
    ok = "utf" in stdout_enc.lower() or "utf" in fs_enc.lower()
    return {"check": "utf8_support", "ok": ok, "value": f"stdout={stdout_enc}, fs={fs_enc}"}


def check_platform() -> dict:
    """Report current platform."""
    from tools.compat.platform_utils import PLATFORM_NAME

    return {"check": "platform", "ok": True, "value": PLATFORM_NAME}


def check_gitattributes() -> dict:
    """Check .gitattributes exists and enforces LF line endings."""
    ga = PROJECT_ROOT / ".gitattributes"
    if ga.exists():
        content = ga.read_text(encoding="utf-8")
        has_lf = "eol=lf" in content
        return {"check": "gitattributes", "ok": has_lf, "value": "eol=lf enforced" if has_lf else "no eol=lf rule"}
    return {"check": "gitattributes", "ok": False, "value": "missing — create .gitattributes with '* text=auto eol=lf'"}


def check_ollama_http() -> dict:
    """Check Ollama is reachable via HTTP API (not subprocess)."""
    try:
        import urllib.request

        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        req = urllib.request.Request(f"{ollama_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:  # nosec B310
            return {"check": "ollama_http", "ok": resp.status == 200, "value": f"reachable at {ollama_url}"}
    except Exception:
        return {"check": "ollama_http", "ok": False, "value": "not reachable — install from https://ollama.com"}


def check_filesystem_encoding() -> dict:
    """Check filesystem encoding is UTF-8 (critical for cross-platform paths)."""
    fs_enc = sys.getfilesystemencoding()
    ok = "utf" in fs_enc.lower()
    return {"check": "fs_encoding", "ok": ok, "value": fs_enc + (" (UTF-8)" if ok else " — may cause issues")}


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
        check_filesystem_encoding(),
        check_gitattributes(),
        check_ollama_http(),
    ]
    return checks


def main():
    import argparse

    parser = argparse.ArgumentParser(description="ICDEV™ platform compatibility check")
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

        print(f"ICDEV™ Platform Check -- {PLATFORM_NAME}")
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
