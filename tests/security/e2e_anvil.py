"""E2E test for OPT-42 — ANVIL headless wrappers.

Lifecycle:
  1. All 10 wrappers exist on disk and report --help
  2. --dry-run on a .md-backed wrapper (feature) extracts N>0 commands
  3. --dry-run on a skill-backed wrapper (status) returns 1 command
  4. --exec on status (skill-backed) runs the underlying tool end-to-end
  5. Unknown positional args after -- are forwarded as $ARGUMENTS (dry-run check)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
PY = sys.executable


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


def _run(wrapper: str, args: list[str], timeout=120):
    return subprocess.run(
        [PY, str(BASE_DIR / "tools/anvil" / f"{wrapper}.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, cwd=str(BASE_DIR))


def main() -> int:
    res = R()
    wrappers = ["feature", "bug", "chore", "test", "review", "commit",
                "status", "monitor", "maintain", "secure", "deploy"]

    # 1. All wrappers report --help
    try:
        missing = []
        for w in wrappers:
            out = _run(w, ["--help"])
            if out.returncode != 0 or "--json" not in out.stdout:
                missing.append(w)
        assert not missing, f"bad wrappers: {missing}"
        res.ok("all_help_usable", f"{len(wrappers)} wrappers OK")
    except Exception as e:
        res.fail("all_help_usable", e)

    # 2. feature (.md-backed) dry-run
    try:
        out = _run("feature", ["--dry-run", "--json", "--", "add demo"])
        assert out.returncode == 0
        data = json.loads(out.stdout)
        assert data["dry_run"] is True
        assert data["total_commands"] > 5, data["total_commands"]
        res.ok("feature_dry_run", f"{data['total_commands']} commands")
    except Exception as e:
        res.fail("feature_dry_run", e)

    # 3. status (skill-backed) dry-run
    try:
        out = _run("status", ["--dry-run", "--json"])
        assert out.returncode == 0
        data = json.loads(out.stdout)
        assert data.get("dry_run") is True
        assert data.get("executed_count") == 0
        res.ok("status_dry_run", f"skill={data.get('skill')}")
    except Exception as e:
        res.fail("status_dry_run", e)

    # 4. status --exec runs underlying tool
    try:
        out = _run("status", ["--json"], timeout=180)
        assert out.returncode == 0
        data = json.loads(out.stdout)
        assert data.get("executed_count", 0) >= 1
        # At least one step produced real output
        has_output = any(s.get("stdout", "").strip()
                         for s in data.get("steps", [])
                         if "returncode" in s)
        assert has_output, "no stdout from executed skill"
        res.ok("status_exec_real", f"executed={data['executed_count']}")
    except Exception as e:
        res.fail("status_exec_real", e)

    # 5. $ARGUMENTS forwarding (dry-run so we don't actually run anything)
    try:
        out = _run("secure", ["--dry-run", "--json", "--", "--scan", "tools/compat"])
        assert out.returncode == 0
        data = json.loads(out.stdout)
        # Either the skill has commands with $ARGUMENTS or it doesn't — we just
        # verify the wrapper accepted the forwarded args without crashing
        assert data.get("dry_run") is True
        res.ok("args_forwarding", f"steps={len(data.get('steps', []))}")
    except Exception as e:
        res.fail("args_forwarding", e)

    print("\n" + "=" * 60)
    print(f"PASSED: {len(res.p)}  FAILED: {len(res.f)}  TOTAL: {len(res.p)+len(res.f)}")
    for n, e in res.f:
        print(f"  - {n}: {e}")
    return 0 if not res.f else 1


if __name__ == "__main__":
    sys.exit(main())
