#!/usr/bin/env python3
# CUI // SP-CTI
"""One-command PyPI release for ICDEV(TM).

Runs the complete release pipeline in strict order. Any step that fails
aborts the whole thing so you never upload a broken wheel:

    1. sync_package_tree.py --clean
         Mirror tools/ into icdev/tools/ (excluding parent-only dirs).
         Copy FORGE data (goals, args, context, hardprompts) into icdev/data/.
         Run prebuild_bootstrap.py to populate claude_bootstrap/.

    2. validate_package_config.py --gate
         Verify PARENT_ONLY_DIRS are in sync across 3 config files.
         Verify all required framework subsystems are present.
         Verify claude_bootstrap has CLAUDE.md + commands + hooks.
         Verify FORGE data dirs are populated.
         Verify entry points resolve to real modules.

    3. Clean dist/ and rebuild
         python -m build

    4. Inspect the wheel
         Verify CLAUDE.md, all 9 canvases, genesis, writing, etc. are inside.

    5. Smoke test in a throwaway venv
         pip install dist/*.whl into a temp venv.
         Verify `icdev init --list` runs cleanly.
         Verify import `import icdev` works.

If all 5 pass, print the twine upload command (but don't auto-run it).
The user runs `twine upload` manually to guard against accidental uploads.

Usage:
    python tools/installer/build_release.py              # full pipeline
    python tools/installer/build_release.py --skip-smoke # skip venv test
    python tools/installer/build_release.py --json       # machine output
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIST_DIR = REPO_ROOT / "dist"


def _step(num: int, total: int, name: str) -> None:
    print(f"\n[{num}/{total}] {name}", flush=True)
    print("-" * 68)


def _run(cmd: list[str], cwd: Path = REPO_ROOT, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Step 1: sync
# ---------------------------------------------------------------------------
def step_sync() -> dict:
    r = _run([sys.executable, "tools/installer/sync_package_tree.py", "--clean"])
    ok = r.returncode == 0
    return {
        "step": "sync",
        "ok": ok,
        "stdout": r.stdout[-1500:] if r.stdout else "",
        "stderr": r.stderr[-500:] if r.stderr else "",
    }


# ---------------------------------------------------------------------------
# Step 2: validate
# ---------------------------------------------------------------------------
def step_validate() -> dict:
    r = _run([sys.executable, "tools/installer/validate_package_config.py",
              "--gate", "--json"])
    try:
        data = json.loads(r.stdout)
    except Exception:
        data = {"raw": r.stdout[-500:]}
    return {
        "step": "validate",
        "ok": r.returncode == 0,
        "result": data,
    }


# ---------------------------------------------------------------------------
# Step 3: build
# ---------------------------------------------------------------------------
def step_build() -> dict:
    # Clean dist first
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    r = _run([sys.executable, "-m", "build"], timeout=600)
    ok = r.returncode == 0
    artifacts: list = []
    if DIST_DIR.exists():
        for p in sorted(DIST_DIR.iterdir()):
            artifacts.append({"name": p.name, "size": p.stat().st_size})
    return {
        "step": "build",
        "ok": ok and bool(artifacts),
        "artifacts": artifacts,
        "stdout": r.stdout[-800:] if r.stdout else "",
        "stderr": r.stderr[-800:] if r.stderr else "",
    }


# ---------------------------------------------------------------------------
# Step 4: inspect wheel
# ---------------------------------------------------------------------------
# Paths that MUST be inside the wheel for a usable ICDEV install
WHEEL_REQUIRED_PATHS = [
    "icdev/data/claude_bootstrap/CLAUDE.md",
    "icdev/data/claude_bootstrap/mcp.json",
    "icdev/data/claude_bootstrap/claude/commands/",
    "icdev/data/claude_bootstrap/claude/hooks/",
    "icdev/data/claude_bootstrap/claude/skills/",
    "icdev/data/args/",
    "icdev/data/goals/",
    "icdev/data/hardprompts/",
    "icdev/data/context/",
    "icdev/tools/boundary_canvas/",
    "icdev/tools/security_canvas/",
    "icdev/tools/data_canvas/",
    "icdev/tools/infra_canvas/",
    "icdev/tools/migration_canvas/",
    "icdev/tools/observability_canvas/",
    "icdev/tools/qdc_canvas/",
    "icdev/tools/network/",
    "icdev/tools/pipeline/",
    "icdev/tools/canvas/",
    "icdev/tools/genesis/",
    "icdev/tools/oracle/",
    "icdev/tools/awareness/",
    "icdev/tools/writing/",
    "icdev/tools/rag/",
    "icdev/tools/kanban/",
    "icdev/tools/anvil/",
    "icdev/tools/cli/init.py",
]

# Paths that MUST NOT be in the wheel (owner child apps, parent services)
WHEEL_FORBIDDEN_PATHS = [
    "icdev/tools/pulse/",
    "icdev/tools/proposal_genesis/",
    "icdev/tools/govcon/",
    "icdev/tools/saas/",
    "icdev/tools/marketplace/",
    "icdev/tools/trading/",
    "icdev/tools/gateway/",
    "icdev/tools/creative/",
    "icdev/tools/playground/",
]


def step_inspect_wheel() -> dict:
    wheels = sorted(DIST_DIR.glob("*.whl")) if DIST_DIR.exists() else []
    if not wheels:
        return {"step": "inspect_wheel", "ok": False, "error": "no wheel in dist/"}
    wheel = wheels[-1]

    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()

    missing_required: list = []
    for required in WHEEL_REQUIRED_PATHS:
        # Either exact file or any file under the dir prefix
        if any(n == required or n.startswith(required) for n in names):
            continue
        missing_required.append(required)

    found_forbidden: list = []
    for forbidden in WHEEL_FORBIDDEN_PATHS:
        hits = [n for n in names if n.startswith(forbidden)]
        if hits:
            found_forbidden.append({"path": forbidden, "file_count": len(hits)})

    return {
        "step": "inspect_wheel",
        "ok": not missing_required and not found_forbidden,
        "wheel": wheel.name,
        "total_files": len(names),
        "missing_required": missing_required,
        "found_forbidden": found_forbidden,
    }


# ---------------------------------------------------------------------------
# Step 5: smoke test install in throwaway venv
# ---------------------------------------------------------------------------
def step_smoke_test() -> dict:
    wheels = sorted(DIST_DIR.glob("*.whl")) if DIST_DIR.exists() else []
    if not wheels:
        return {"step": "smoke_test", "ok": False, "error": "no wheel in dist/"}
    wheel = wheels[-1]

    result: dict = {"step": "smoke_test", "ok": False, "wheel": wheel.name}

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        venv_dir = tmpdir / "venv"

        # Create venv
        r = _run([sys.executable, "-m", "venv", str(venv_dir)], timeout=60)
        if r.returncode != 0:
            result["error"] = f"venv creation failed: {r.stderr[:200]}"
            return result

        if os.name == "nt":
            py = venv_dir / "Scripts" / "python.exe"
            icdev_bin = venv_dir / "Scripts" / "icdev.exe"
        else:
            py = venv_dir / "bin" / "python"
            icdev_bin = venv_dir / "bin" / "icdev"

        # Install wheel
        r = _run([str(py), "-m", "pip", "install", "--quiet", str(wheel)], timeout=300)
        if r.returncode != 0:
            result["error"] = f"pip install failed: {r.stderr[:500]}"
            return result
        result["install_ok"] = True

        # Test: import icdev
        r = _run([str(py), "-c", "import icdev; print(icdev.__version__)"], timeout=30)
        result["import_ok"] = (r.returncode == 0)
        result["version"] = r.stdout.strip()

        # Test: icdev init --list in a fresh project dir
        proj_dir = tmpdir / "proj"
        proj_dir.mkdir()
        r = _run([str(icdev_bin), "init", str(proj_dir), "--list"], timeout=30)
        # Fallback: invoke via python -m if entry-point shim not found
        if r.returncode != 0 and "No such" in (r.stderr or ""):
            r = _run([str(py), "-m", "icdev.tools.cli.init",
                      str(proj_dir), "--list"], timeout=30)
        result["init_list_ok"] = (r.returncode == 0)
        result["init_list_output"] = (r.stdout or "")[-500:]

        result["ok"] = all((result.get("install_ok"),
                            result.get("import_ok"),
                            result.get("init_list_ok")))
    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run(skip_smoke: bool = False) -> dict:
    total = 4 if skip_smoke else 5
    results: list = []

    _step(1, total, "Sync tools/ + FORGE data + Claude bootstrap into icdev/")
    r1 = step_sync()
    results.append(r1)
    print(r1["stdout"][-800:] if r1["stdout"] else "")
    if not r1["ok"]:
        return {"ok": False, "failed_at": "sync", "results": results}

    _step(2, total, "Validate package config (3-file sync, subsystems, entry points)")
    r2 = step_validate()
    results.append(r2)
    if not r2["ok"]:
        print(json.dumps(r2["result"], indent=2))
        return {"ok": False, "failed_at": "validate", "results": results}
    print("  All validation gates passed.")

    _step(3, total, "Build wheel + sdist (python -m build)")
    r3 = step_build()
    results.append(r3)
    for a in r3.get("artifacts", []):
        print(f"  artifact: {a['name']} ({a['size']:,} bytes)")
    if not r3["ok"]:
        print(r3.get("stderr", "")[-500:])
        return {"ok": False, "failed_at": "build", "results": results}

    _step(4, total, "Inspect wheel contents (required + forbidden paths)")
    r4 = step_inspect_wheel()
    results.append(r4)
    print(f"  wheel: {r4.get('wheel')} ({r4.get('total_files')} files)")
    if r4.get("missing_required"):
        print(f"  MISSING required paths: {r4['missing_required']}")
    if r4.get("found_forbidden"):
        print(f"  FOUND forbidden paths: {r4['found_forbidden']}")
    if not r4["ok"]:
        return {"ok": False, "failed_at": "inspect_wheel", "results": results}

    if not skip_smoke:
        _step(5, total, "Smoke test install in throwaway venv")
        r5 = step_smoke_test()
        results.append(r5)
        print(f"  install={r5.get('install_ok')}  import={r5.get('import_ok')} "
              f"(v{r5.get('version')})  init_list={r5.get('init_list_ok')}")
        if not r5["ok"]:
            if r5.get("error"):
                print(f"  error: {r5['error']}")
            return {"ok": False, "failed_at": "smoke_test", "results": results}

    return {"ok": True, "failed_at": None, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--skip-smoke", action="store_true",
                        help="Skip the throwaway-venv install test (faster, less safe)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run(skip_smoke=args.skip_smoke)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["ok"] else 1

    print()
    print("=" * 68)
    if result["ok"]:
        print("  RELEASE READY")
        print("=" * 68)
        wheels = sorted(DIST_DIR.glob("*.whl")) if DIST_DIR.exists() else []
        sdists = sorted(DIST_DIR.glob("*.tar.gz")) if DIST_DIR.exists() else []
        print()
        print("Artifacts:")
        for w in wheels + sdists:
            print(f"  {w.name}")
        print()
        print("To upload to PyPI (run manually):")
        print("  python -m twine upload dist/*")
    else:
        print(f"  RELEASE BLOCKED AT: {result['failed_at']}")
        print("=" * 68)
        print("Fix the failure above and re-run.")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
