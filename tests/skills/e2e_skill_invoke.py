"""E2E test for OPT-41 — skill invoker must be runnable headlessly.

Lifecycle:
  1. registry.py --list --json returns >= 20 skills
  2. registry.py --get icdev-init returns a valid entry
  3. invoke.py --list --json returns all skills
  4. invoke.py --show icdev-status prints skill card
  5. invoke.py --dry-run icdev-status previews commands without running
  6. invoke.py --exec icdev-status actually executes the documented command
     (must exit 0 with real stdout)
  7. invoke.py --exec nonexistent-skill returns error JSON (not crash)

Run: python tests/skills/e2e_skill_invoke.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
PY = sys.executable
REG = [PY, str(BASE_DIR / "tools/skills/registry.py")]
INV = [PY, str(BASE_DIR / "tools/skills/invoke.py")]


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


def _run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=timeout, cwd=str(BASE_DIR))


def main() -> int:
    res = R()

    try:
        out = _run(REG + ["--list", "--json"])
        assert out.returncode == 0
        data = json.loads(out.stdout)
        assert data["count"] >= 20, f"expected >=20, got {data['count']}"
        res.ok("registry_list", f"{data['count']} skills")
    except Exception as e:
        res.fail("registry_list", e)

    try:
        out = _run(REG + ["--get", "icdev-init", "--json"])
        assert out.returncode == 0
        entry = json.loads(out.stdout)
        assert entry["name"] == "icdev-init"
        assert entry["description"]
        res.ok("registry_get", f"cmds={len(entry['commands'])}")
    except Exception as e:
        res.fail("registry_get", e)

    try:
        out = _run(INV + ["--list", "--json"])
        assert out.returncode == 0
        data = json.loads(out.stdout)
        assert "icdev-status" in data["skills"]
        res.ok("invoke_list", f"{data['count']} skills")
    except Exception as e:
        res.fail("invoke_list", e)

    try:
        out = _run(INV + ["--show", "icdev-status"])
        assert out.returncode == 0
        assert "icdev-status" in out.stdout
        assert "Commands" in out.stdout
        res.ok("invoke_show")
    except Exception as e:
        res.fail("invoke_show", e)

    try:
        out = _run(INV + ["--dry-run", "icdev-status", "--json"])
        assert out.returncode == 0
        data = json.loads(out.stdout)
        assert data["dry_run"] is True
        assert data["executed_count"] == 0
        assert len(data["steps"]) >= 1
        res.ok("invoke_dry_run", f"{len(data['steps'])} steps previewed")
    except Exception as e:
        res.fail("invoke_dry_run", e)

    try:
        out = _run(INV + ["--exec", "icdev-status", "--json"], timeout=180)
        assert out.returncode == 0, f"rc={out.returncode} stderr={out.stderr[:200]}"
        data = json.loads(out.stdout)
        assert data["executed_count"] >= 1
        assert data["failed_count"] == 0
        # At least one step produced real stdout
        has_output = any(s.get("stdout", "").strip() for s in data["steps"]
                         if "returncode" in s)
        assert has_output, "no step produced stdout"
        res.ok("invoke_exec_real", f"executed={data['executed_count']}")
    except Exception as e:
        res.fail("invoke_exec_real", e)

    try:
        out = _run(INV + ["--exec", "nonexistent-skill-xyz", "--json"])
        assert out.returncode == 1  # error exit
        data = json.loads(out.stdout)
        assert "error" in data
        res.ok("invoke_unknown_graceful")
    except Exception as e:
        res.fail("invoke_unknown_graceful", e)

    print("\n" + "=" * 60)
    print(f"PASSED: {len(res.p)}  FAILED: {len(res.f)}  TOTAL: {len(res.p)+len(res.f)}")
    for n, e in res.f:
        print(f"  - {n}: {e}")
    return 0 if not res.f else 1


if __name__ == "__main__":
    sys.exit(main())
