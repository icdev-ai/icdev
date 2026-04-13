"""E2E test for OPT-54 — architecture audit CLI.

Lifecycle:
  1. --path tools/compat --format markdown returns Markdown with all sections
  2. --path tools/compat --format json parses as JSON with expected keys
  3. --path tools/security --top 5 runs cleanly on ~24 modules
  4. --path tools --top 10 completes on full tools/ tree (>=1000 modules)
  5. --out writes a file to disk
  6. --path nonexistent errors gracefully with exit 1
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
AUDIT = [PY, str(BASE_DIR / "tools/analysis/architecture_audit.py")]


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


def _run(args, timeout=300):
    return subprocess.run(
        AUDIT + args, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, cwd=str(BASE_DIR))


def main() -> int:
    res = R()

    # 1. Markdown output on a small module
    try:
        out = _run(["--path", "tools/compat", "--format", "markdown", "--top", "5"])
        assert out.returncode == 0, f"rc={out.returncode} stderr={out.stderr[:200]}"
        for section in ("# Architecture Audit", "## Summary",
                        "## Shallow-module candidates", "## Deep modules",
                        "## Coupling clusters"):
            assert section in out.stdout, f"missing: {section}"
        res.ok("markdown_sections", f"{len(out.stdout)} bytes")
    except Exception as e:
        res.fail("markdown_sections", e)

    # 2. JSON output parses
    try:
        out = _run(["--path", "tools/compat", "--format", "json", "--top", "5"])
        assert out.returncode == 0
        data = json.loads(out.stdout)
        for k in ("root", "summary", "shallow_top", "deep_top", "clusters_top"):
            assert k in data, f"json missing {k}"
        s = data["summary"]
        assert s["module_count"] >= 1
        res.ok("json_output", f"{s['module_count']} modules in tools/compat")
    except Exception as e:
        res.fail("json_output", e)

    # 3. Medium dir
    try:
        out = _run(["--path", "tools/security", "--top", "5", "--json"])
        assert out.returncode == 0
        data = json.loads(out.stdout)
        assert data["summary"]["module_count"] >= 10, data["summary"]
        res.ok("medium_scan", f"tools/security: {data['summary']['module_count']} modules")
    except Exception as e:
        res.fail("medium_scan", e)

    # 4. Full tools/ scan
    try:
        out = _run(["--path", "tools", "--top", "10", "--json"], timeout=600)
        assert out.returncode == 0
        data = json.loads(out.stdout)
        s = data["summary"]
        assert s["module_count"] >= 1000, f"expected >=1000 modules, got {s['module_count']}"
        # With 1000+ modules, we expect some clusters and shallow candidates
        res.ok("full_tools_scan", f"{s['module_count']} mods, "
                                  f"{s['shallow_count']} shallow, {s['cluster_count']} clusters")
    except Exception as e:
        res.fail("full_tools_scan", e)

    # 5. --out writes to disk
    try:
        with tempfile.TemporaryDirectory() as td:
            out_file = Path(td) / "arch.md"
            out = _run(["--path", "tools/compat", "--format", "markdown",
                       "--out", str(out_file)])
            assert out.returncode == 0
            assert out_file.exists(), "output file not written"
            content = out_file.read_text(encoding="utf-8")
            assert "# Architecture Audit" in content
            res.ok("out_file", f"{len(content)} bytes written")
    except Exception as e:
        res.fail("out_file", e)

    # 6. Error path
    try:
        out = _run(["--path", "nonexistent_dir_xyz_123"])
        assert out.returncode == 1
        assert "not found" in out.stderr.lower() or "not found" in out.stdout.lower()
        res.ok("missing_path_graceful")
    except Exception as e:
        res.fail("missing_path_graceful", e)

    print("\n" + "=" * 60)
    print(f"PASSED: {len(res.p)}  FAILED: {len(res.f)}  TOTAL: {len(res.p)+len(res.f)}")
    for n, e in res.f:
        print(f"  - {n}: {e}")
    return 0 if not res.f else 1


if __name__ == "__main__":
    sys.exit(main())
