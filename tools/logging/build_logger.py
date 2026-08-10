#!/usr/bin/env python3
# CUI // SP-CTI
"""Build Logger — captures pytest + Playwright results as NDJSON events (LOG-04).

Writes structured build events to .logs/build.ndjson so the Genesis
log_triage reflex can autonomously detect and create remediation tasks.

Event types:
  pytest_run       — one event per pytest invocation
  playwright_run   — one event per Playwright run

Usage (from build scripts):
    from tools.logging.build_logger import capture_pytest, capture_playwright

    result = subprocess.run(["pytest", ...], capture_output=True, text=True)
    capture_pytest(result.returncode, result.stdout, result.stderr, duration_s=12.4)

CLI self-test:
    python tools/logging/build_logger.py --self-test
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger, _load_config

_log = get_logger("build_logger")

# Build event log is centralised in one file for triage
_BUILD_LOG_NAME = "build"


def _build_log_path() -> Path:
    cfg = _load_config()
    log_dir = Path(cfg.get("log_dir", ".logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "build.ndjson"


def _write_build_event(event_type: str, data: Dict[str, Any]) -> None:
    """Append one NDJSON line to .logs/build.ndjson."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": "ERROR" if data.get("returncode", 0) != 0 else "INFO",
        "component": "build_logger",
        "event_type": event_type,
        **data,
    }
    line = json.dumps(record, default=str)
    _log.info("build_event", extra={"extra": {"event_type": event_type, **data}})
    with open(_build_log_path(), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _parse_pytest_failures(stdout: str) -> List[Dict[str, str]]:
    """Extract failing test names and short error from pytest stdout."""
    failures: List[Dict[str, str]] = []
    # Match lines like: FAILED tests/test_foo.py::test_bar - AssertionError: ...
    for m in re.finditer(r"FAILED\s+([\w/\\.\-:]+)\s+-\s+(.+)", stdout):
        failures.append({"test": m.group(1).strip(), "error": m.group(2).strip()})
    return failures


def _parse_playwright_failures(stdout: str) -> List[Dict[str, str]]:
    """Extract failing Playwright test names from stdout.

    Two shapes are matched, because the marker depends on the reporter:
      ✘  [chromium] › tests/e2e/foo.spec.ts:42:7 › Suite › test name   (list)
      1) [chromium] › tests/e2e/foo.spec.ts:42:7 › Suite › test name   (all)

    Only `list` prints the ✘ marker. `--reporter=line` — what the E2E runbook
    actually invokes — prints progress on a single rewritten line and reports
    failures ONLY as the numbered detail blocks, so matching ✘ alone recorded
    `failures: []` next to `failed: 32` and left log_triage with a count it
    could not turn into remediation tasks. The numbered block is emitted by
    every reporter, hence both patterns plus a dedupe: `list` prints both
    forms for the same test and must not double-count it.
    """
    # Strip ANSI before matching. Playwright colourises unconditionally when
    # FORCE_COLOR is set, and `line` prefixes each rewritten line with a cursor
    # move, so the raw text reads "\x1b[1A\x1b[2K  1) [chromium] ..." — a `^\s*`
    # anchor never reaches the digit, and the error text is shot through with
    # colour codes.
    stdout = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", stdout)
    failures: List[Dict[str, str]] = []
    seen = set()
    for pattern in (
        r"[✘×]\s+\[(\w+)\]\s+›\s+([\w/\\.:\-]+)\s+›\s+([^\n]+)",
        r"(?m)^\s*\d+\)\s+\[(\w+)\]\s+›\s+([\w/\\.:\-]+)\s+›\s+([^\n]+)",
    ):
        for m in re.finditer(pattern, stdout):
            # `list` appends a duration — "› does a thing (1.2s)" — and the
            # numbered block does not. Drop it so the same test dedupes.
            title = re.sub(r"\s*\(\d+(?:\.\d+)?[a-z]{1,2}\)\s*$", "", m.group(3).strip())
            test = f"{m.group(2)} › {title}"
            key = (m.group(1), test)
            if key in seen:
                continue
            seen.add(key)
            # The first `Error:` line after the header is the assertion itself.
            err = re.search(r"\n\s*(Error:[^\n]+)", stdout[m.end():m.end() + 600])
            failures.append({
                "browser": m.group(1),
                "test": test,
                "error": err.group(1).strip() if err else "",
            })
    return failures


def capture_pytest(
    returncode: int,
    stdout: str,
    stderr: str = "",
    duration_s: float = 0.0,
    passed: Optional[int] = None,
    failed: Optional[int] = None,
    skipped: Optional[int] = None,
) -> None:
    """Record one pytest run to build.ndjson."""
    # Try to parse counts from stdout if not provided
    if passed is None or failed is None:
        m = re.search(r"(\d+) passed", stdout)
        passed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) failed", stdout)
        failed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) skipped", stdout)
        skipped = int(m.group(1)) if m else 0

    _write_build_event("pytest_run", {
        "returncode": returncode,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration_s": round(duration_s, 2),
        "failures": _parse_pytest_failures(stdout),
        "stderr_tail": stderr[-500:] if stderr else "",
    })


def capture_playwright(
    returncode: int,
    stdout: str,
    passed: Optional[int] = None,
    failed: Optional[int] = None,
    skipped: Optional[int] = None,
    duration_s: float = 0.0,
) -> None:
    """Record one Playwright run to build.ndjson."""
    if passed is None or failed is None:
        m = re.search(r"(\d+) passed", stdout)
        passed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) failed", stdout)
        failed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) skipped", stdout)
        skipped = int(m.group(1)) if m else 0

    _write_build_event("playwright_run", {
        "returncode": returncode,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration_s": round(duration_s, 2),
        "failures": _parse_playwright_failures(stdout),
    })


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        print("Running build_logger self-test...")
        capture_pytest(
            returncode=1,
            stdout="FAILED tests/test_foo.py::test_bar - AssertionError: expected True\n3 passed, 1 failed",
            stderr="",
            duration_s=2.1,
        )
        capture_playwright(
            returncode=0,
            stdout="551 passed (25.5m)",
            duration_s=1530.0,
        )
        path = _build_log_path()
        print(f"Written to: {path}")
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        for line in lines[-2:]:
            print(line)
        print("Self-test OK")
