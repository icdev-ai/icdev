"""E2E test for OPT-53 — tracer-bullet planner CLI.

Lifecycle:
  1. --prd <sample> --no-llm --json emits meta with mode=deterministic-skeleton
  2. Plan file is written to disk and passes its own --lint
  3. --lint on a clean plan returns ok
  4. --lint on a dirty plan detects file + function names
  5. --validate-only without writing returns appropriate exit code
  6. --prd nonexistent errors gracefully
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = [PY, str(BASE_DIR / "tools/planning/prd_to_plan.py")]


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


PRD_BODY = """\
# Feature — Score Intake Readiness

## Durable Decisions
- Route: POST /api/intake/score
- Schema: readiness_score with (session_id, score, dimensions[])
- UI: /intake/<session>/score page

## Goal
Compute a 0–100 readiness score per intake session.
"""


DIRTY_PLAN_BODY = """\
# Bad Plan

### Phase 1
- Modify src/router.py to add the handler.
- Call score_intake() from the scheduler.
"""


def main() -> int:
    res = R()
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        prd_path = td_path / "sample_prd.md"
        prd_path.write_text(PRD_BODY, encoding="utf-8")
        out_path = td_path / "plan.md"

        # 1. Build plan, no-LLM, JSON
        try:
            out = _run(["--prd", str(prd_path), "--no-llm", "--out",
                       str(out_path), "--json"])
            assert out.returncode == 0, f"rc={out.returncode} stderr={out.stderr[:200]}"
            data = json.loads(out.stdout)
            assert data["meta"]["mode"] == "deterministic-skeleton"
            assert data["meta"]["decision_count"] == 3
            assert data["meta"]["phase_count"] >= 3
            assert data["lint"]["ok"] is True
            res.ok("build_skeleton",
                   f"phases={data['meta']['phase_count']} decisions={data['meta']['decision_count']}")
        except Exception as e:
            res.fail("build_skeleton", e)

        # 2. Plan file written + self-lints clean
        try:
            assert out_path.exists()
            out = _run(["--lint", str(out_path), "--json"])
            assert out.returncode == 0
            data = json.loads(out.stdout)
            assert data["ok"] is True
            res.ok("plan_self_lints_clean", f"{out_path.stat().st_size} bytes")
        except Exception as e:
            res.fail("plan_self_lints_clean", e)

        # 3. Build decisions + 'POST /api/intake/score' appears in plan
        try:
            content = out_path.read_text(encoding="utf-8")
            assert "POST /api/intake/score" in content
            assert "Phase 1" in content
            res.ok("plan_contains_durable_decisions")
        except Exception as e:
            res.fail("plan_contains_durable_decisions", e)

        # 4. Lint catches dirty plan
        try:
            dirty = td_path / "bad_plan.md"
            dirty.write_text(DIRTY_PLAN_BODY, encoding="utf-8")
            out = _run(["--lint", str(dirty), "--json"])
            assert out.returncode == 1, f"lint should fail, rc={out.returncode}"
            data = json.loads(out.stdout)
            assert data["ok"] is False
            kinds = {f["kind"] for f in data["findings"]}
            assert "file_name" in kinds
            assert "function_call" in kinds
            res.ok("lint_catches_dirty_plan", f"{len(data['findings'])} findings")
        except Exception as e:
            res.fail("lint_catches_dirty_plan", e)

        # 5. --validate-only doesn't write
        try:
            ghost = td_path / "ghost.md"
            out = _run(["--prd", str(prd_path), "--no-llm",
                       "--out", str(ghost), "--validate-only", "--json"])
            assert out.returncode == 0
            assert not ghost.exists(), "validate-only should not write output"
            res.ok("validate_only_no_write")
        except Exception as e:
            res.fail("validate_only_no_write", e)

        # 6. Missing PRD
        try:
            out = _run(["--prd", str(td_path / "nonexistent.md")])
            assert out.returncode == 1
            assert "not found" in (out.stdout + out.stderr).lower()
            res.ok("missing_prd_graceful")
        except Exception as e:
            res.fail("missing_prd_graceful", e)

    print("\n" + "=" * 60)
    print(f"PASSED: {len(res.p)}  FAILED: {len(res.f)}  TOTAL: {len(res.p)+len(res.f)}")
    for n, e in res.f:
        print(f"  - {n}: {e}")
    return 0 if not res.f else 1


if __name__ == "__main__":
    sys.exit(main())
