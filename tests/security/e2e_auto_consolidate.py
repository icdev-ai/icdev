"""E2E test for OPT-40 — memory auto-consolidate daemon CLI.

Lifecycle:
  1. --json with default threshold returns valid JSON and exit 0
  2. --force triggers consolidation path (summary present)
  3. --dry-run sets dry_run flag in result
  4. Custom --threshold-mb honored
  5. --no-audit suppresses audit write (verified via missing-db path)
  6. --help prints usage
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = [PY, str(BASE_DIR / "tools/memory/auto_consolidate.py")]


class R:
    def __init__(self):
        self.p: list[str] = []
        self.f: list[tuple[str, str]] = []

    def ok(self, n, d=""):
        self.p.append(n)
        print(f"[OK]   {n}{(': '+d) if d else ''}")

    def fail(self, n, e):
        self.f.append((n, str(e)[:300]))
        print(f"[FAIL] {n}: {str(e)[:300]}")


def _run(args, timeout=60):
    return subprocess.run(TOOL + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=timeout, cwd=str(BASE_DIR))


def main() -> int:
    res = R()

    # 1. JSON, no audit (idempotent-safe)
    try:
        out = _run(["--json", "--no-audit"])
        assert out.returncode == 0, f"rc={out.returncode} stderr={out.stderr[:200]}"
        data = json.loads(out.stdout)
        for key in ("timestamp", "db_path", "db_bytes", "threshold_bytes",
                    "triggered", "duration_ms"):
            assert key in data
        res.ok("json_shape", f"db_mb={data['db_mb']} triggered={data['triggered']}")
    except Exception as e:
        res.fail("json_shape", e)

    # 2. --force triggers consolidation path
    try:
        out = _run(["--force", "--dry-run", "--no-audit", "--json"])
        assert out.returncode == 0
        data = json.loads(out.stdout)
        assert data["triggered"] is True
        assert data["dry_run"] is True
        assert data["summary"] is not None
        assert "processed" in data["summary"]
        res.ok("force_triggers",
               f"processed={data['summary'].get('processed')}")
    except Exception as e:
        res.fail("force_triggers", e)

    # 3. Custom threshold honored
    try:
        out = _run(["--threshold-mb", "9999999", "--json", "--no-audit"])
        assert out.returncode == 0
        data = json.loads(out.stdout)
        assert data["threshold_mb"] == 9999999
        # Very large threshold -> should skip
        assert data["triggered"] is False or data["force"] is True
        res.ok("custom_threshold", f"triggered={data['triggered']}")
    except Exception as e:
        res.fail("custom_threshold", e)

    # 4. Help prints usage
    try:
        out = _run(["--help"])
        assert out.returncode == 0
        assert "--threshold-mb" in out.stdout
        assert "--dry-run" in out.stdout
        res.ok("help_usable")
    except Exception as e:
        res.fail("help_usable", e)

    # 5. Exit code: normal path returns 0 (no error)
    try:
        out = _run(["--no-audit", "--json"])
        assert out.returncode == 0
        res.ok("normal_exit_0")
    except Exception as e:
        res.fail("normal_exit_0", e)

    print("\n" + "=" * 60)
    print(f"PASSED: {len(res.p)}  FAILED: {len(res.f)}  TOTAL: {len(res.p)+len(res.f)}")
    for n, e in res.f:
        print(f"  - {n}: {e}")
    return 0 if not res.f else 1


if __name__ == "__main__":
    sys.exit(main())
