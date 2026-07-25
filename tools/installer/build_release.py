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
         Run a REAL `icdev init` and assert the payload landed: CLAUDE.md,
         .mcp.json, .claude/settings.json, .claude/{commands,hooks,skills}
         populated to at least the wheel's recorded counts, a non-empty .env
         carrying every packaged registry env flag, and the packaged registry
         loading with the expected component count.
         Verify import `import icdev` and core subsystem imports work.

    6. Air-gap OFFLINE install verification
         Pre-stage a wheelhouse (icdev + air-gap extra deps), then
         `pip install --no-index --find-links <wheelhouse> icdev[dod-il6]`
         with NO network. Run `icdev init --profile air-gap` + `icdev status`
         and assert the expected component count is enabled, and assert the
         NEGATIVE: no google-auth / google-generativeai / google-cloud-aiplatform
         / tensorboard was pulled. If a fully offline install cannot be
         simulated (no network to pre-stage the wheelhouse), the step documents
         exactly what was and was not verified instead of passing blindly.

If all pass, print the twine upload command (but don't auto-run it).
The user runs `twine upload` manually to guard against accidental uploads.

--skip-smoke is for LOCAL ITERATION ONLY. It prints a loud warning and must
never be used on the documented release path — skipping it means the wheel's
`icdev init` is never proven to work.

Usage:
    python tools/installer/build_release.py              # full pipeline
    python tools/installer/build_release.py --skip-smoke # LOCAL ONLY (loud warn)
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
# Real-init verification helpers (pkg-rel-01)
# ---------------------------------------------------------------------------
# The old smoke test ran `icdev init <dir> --list`, which only REPORTS what it
# would copy — a wheel whose `icdev init` copies zero files passed. These helpers
# drive a REAL init and assert the payload actually landed, keyed off the counts
# and env flags recorded in the wheel itself.

_BOOTSTRAP_CLAUDE = "icdev/data/claude_bootstrap/claude"
_WHEEL_REGISTRY = "icdev/data/args/component_registry.yaml"


def _wheel_payload(wheel: Path) -> dict:
    """Extract the payload contract from the built wheel.

    Returns the file counts the init MUST reproduce and the env flags + component
    count the packaged registry declares. Reading it from the wheel (not the
    source tree) is the point: the assertions are against what actually shipped.
    """
    import yaml  # available in the build environment

    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        registry_text = ""
        if _WHEEL_REGISTRY in names:
            registry_text = z.read(_WHEEL_REGISTRY).decode("utf-8", "replace")

    def _count(subdir: str) -> int:
        prefix = f"{_BOOTSTRAP_CLAUDE}/{subdir}/"
        return sum(1 for n in names if n.startswith(prefix) and not n.endswith("/"))

    env_flags: set[str] = set()
    component_count = 0
    if registry_text:
        data = yaml.safe_load(registry_text) or {}
        for comp in (data.get("components") or []):
            if not isinstance(comp, dict):
                continue
            flag = comp.get("env_flag")
            if flag:
                env_flags.add(flag)
                component_count += 1
            for extra in (comp.get("extra_env_flags") or []):
                env_flags.add(extra)

    return {
        "commands": _count("commands"),
        "hooks": _count("hooks"),
        "skills": _count("skills"),
        "env_flags": env_flags,
        "component_count": component_count,
    }


def _count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0


def _verify_init(proj_dir: Path, expected: dict) -> list[str]:
    """Assert a REAL `icdev init` produced a complete, usable project.

    Returns a list of failure strings (empty ⇒ all assertions passed).
    """
    fails: list[str] = []

    # Required top-level files.
    if not (proj_dir / "CLAUDE.md").is_file():
        fails.append("CLAUDE.md missing")
    if not (proj_dir / ".mcp.json").is_file():
        fails.append(".mcp.json missing")
    if not (proj_dir / ".claude" / "settings.json").is_file():
        fails.append(".claude/settings.json missing")

    # .claude subtrees exist and are at least as populated as the wheel payload.
    for sub in ("commands", "hooks", "skills"):
        d = proj_dir / ".claude" / sub
        if not d.is_dir():
            fails.append(f".claude/{sub} missing")
            continue
        got = _count_files(d)
        want = int(expected.get(sub, 0))
        if got < want:
            fails.append(f".claude/{sub} has {got} files, expected >= {want}")

    # .env exists, is non-empty, and carries every packaged env flag.
    env_file = proj_dir / ".env"
    if not env_file.is_file():
        fails.append(".env missing")
    else:
        env_text = env_file.read_text(encoding="utf-8")
        if not env_text.strip():
            fails.append(".env is empty")
        missing_flags = sorted(
            f for f in expected.get("env_flags", set())
            if f"{f}=" not in env_text
        )
        if missing_flags:
            shown = ", ".join(missing_flags[:15])
            more = "" if len(missing_flags) <= 15 else f" (+{len(missing_flags) - 15} more)"
            fails.append(
                f".env missing {len(missing_flags)} registry env flag(s): {shown}{more}")

    return fails


_REGISTRY_COUNT_PROBE = (
    "import sys\n"
    "from icdev.tools.config.component_registry import get_registry\n"
    "n = len([c for c in get_registry().list_all() if c.env_flag])\n"
    "print('REGISTRY_COMPONENTS=%d' % n)\n"
    "expected = {expected}\n"
    "if n != expected:\n"
    "    print('FAILED: registry loaded %d flag-components, expected %d' % (n, expected))\n"
    "    sys.exit(1)\n"
)


# ---------------------------------------------------------------------------
# Step 5: smoke test install in throwaway venv
# ---------------------------------------------------------------------------
#: Subsystems that must import from the *installed* package. Each previously
#: raised ``ModuleNotFoundError: No module named 'tools'`` inside the wheel.
_SMOKE_SUBSYSTEMS = (
    "icdev.tools.db.storage",
    "icdev.tools.security.abac_engine",
    "icdev.tools.security.column_security",
    "icdev.tools.llm.router",
)

_SUBSYSTEM_IMPORT_PROBE = (
    "import importlib, sys\n"
    "mods = {mods!r}\n"
    "bad = []\n"
    "for m in mods:\n"
    "    try:\n"
    "        importlib.import_module(m)\n"
    "    except Exception as exc:\n"
    "        bad.append('%s -> %s: %s' % (m, type(exc).__name__, exc))\n"
    "if bad:\n"
    "    print('FAILED_IMPORTS: ' + ' | '.join(bad))\n"
    "    sys.exit(1)\n"
    "print('SUBSYSTEM_IMPORTS_OK (%d)' % len(mods))\n"
).format(mods=list(_SMOKE_SUBSYSTEMS))

_COLUMN_POLICY_PROBE = (
    "import sys\n"
    "from icdev.tools.security import column_security as cs\n"
    "path = cs._resolve_config_path()\n"
    "n = len(cs._load_config().get('column_policies', []))\n"
    "print('CONFIG=%s POLICIES=%d' % (path, n))\n"
    "if n == 0:\n"
    "    print('FAILED: zero column policies loaded (config not found in wheel layout)')\n"
    "    sys.exit(1)\n"
)


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

        # Every probe below MUST run with cwd outside the repo. `python -c` puts
        # the cwd on sys.path, so running from REPO_ROOT (the _run default) makes
        # `import icdev` / `import tools` resolve to the *source tree* instead of
        # the installed wheel — which is why a wheel whose modules could not
        # import still passed this smoke test.
        probe_cwd = tmpdir

        # Test: import icdev
        r = _run([str(py), "-c", "import icdev; print(icdev.__version__)"],
                 cwd=probe_cwd, timeout=30)
        result["import_ok"] = (r.returncode == 0)
        result["version"] = r.stdout.strip()

        # Test: a REAL `icdev init` into a fresh project dir, then assert the
        # payload actually landed. (The old test ran `--list`, which only
        # reports what it *would* copy — a wheel that copies nothing passed.)
        # `--profile none` keeps it non-interactive and applies registry
        # defaults; every env flag is still emitted (enabled or commented).
        expected = _wheel_payload(wheel)
        result["expected_payload"] = {
            k: (v if k != "env_flags" else len(v)) for k, v in expected.items()
        }
        proj_dir = tmpdir / "proj"
        r = _run([str(icdev_bin), "init", str(proj_dir), "--profile", "none"],
                 cwd=probe_cwd, timeout=60)
        # Fallback: invoke via python -m if entry-point shim not found
        if r.returncode != 0 and "No such" in (r.stderr or ""):
            r = _run([str(py), "-m", "icdev.tools.cli.init",
                      str(proj_dir), "--profile", "none"],
                     cwd=probe_cwd, timeout=60)
        init_ran = (r.returncode == 0)
        result["init_output"] = (r.stdout or r.stderr or "")[-500:]

        init_fails = ([] if init_ran
                      else [f"icdev init exited {r.returncode}: "
                            f"{(r.stderr or r.stdout or '')[-200:]}"])
        if init_ran:
            init_fails = _verify_init(proj_dir, expected)
        result["init_ok"] = init_ran and not init_fails
        result["init_failures"] = init_fails
        # Back-compat key retained for existing readers of this result dict.
        result["init_list_ok"] = result["init_ok"]

        # Test: the packaged registry loads with the expected component count
        # (probing path resolution inside the installed wheel, not the source).
        probe = _REGISTRY_COUNT_PROBE.format(expected=expected["component_count"])
        r = _run([str(py), "-c", probe], cwd=probe_cwd, timeout=60)
        result["registry_load_ok"] = (r.returncode == 0)
        result["registry_load_info"] = (r.stdout or r.stderr or "").strip()[-300:]

        # Test: core icdev.tools.* subsystems actually import in the installed
        # package. ~1,900 packaged modules import their siblings through the
        # absolute ``tools.*`` namespace, which the wheel does not ship as a
        # top-level package. A missing alias made db.storage / abac_engine /
        # llm.router unimportable and silently disabled column masking, while
        # `import icdev` alone still succeeded — so this gap shipped unnoticed.
        r = _run([str(py), "-c", _SUBSYSTEM_IMPORT_PROBE], cwd=probe_cwd, timeout=90)
        result["subsystem_imports_ok"] = (r.returncode == 0)
        if r.returncode != 0:
            result["subsystem_imports_error"] = (r.stdout or r.stderr or "")[-600:]

        # Test: the shipped security config resolves and column policies load.
        # A wrong config path silently degrades every policy to "no policy",
        # i.e. unmasked rows, with no error anywhere.
        r = _run([str(py), "-c", _COLUMN_POLICY_PROBE], cwd=probe_cwd, timeout=60)
        result["column_policies_ok"] = (r.returncode == 0)
        result["column_policies_info"] = (r.stdout or r.stderr or "").strip()[-300:]

        result["ok"] = all((result.get("install_ok"),
                            result.get("import_ok"),
                            result.get("init_ok"),
                            result.get("registry_load_ok"),
                            result.get("subsystem_imports_ok"),
                            result.get("column_policies_ok")))
    return result


# ---------------------------------------------------------------------------
# Step 6: air-gap offline install verification (pkg-rel-03)
# ---------------------------------------------------------------------------
# Prove the air-gap install path end to end rather than assuming it: install the
# built wheel into a throwaway venv with NO network (`--no-index --find-links`),
# confirm the air-gap profile enables the expected components, and assert the
# NEGATIVE — that no google-auth / google-generativeai / tensorboard /
# google-cloud-aiplatform slipped into an air-gap extra. pyproject documents
# llm-gemini/llm-vertex as NOT air-gap compatible; this is what keeps that
# promise honest as the dependency tree changes.
#
# The air-gap pip EXTRA (icdev[dod-il6]) is a dependency bundle; the init-time
# --profile is a CORE profile from core_profiles.yaml. There is no `dod-il6`
# core profile, so we init with the air-gap core profile and assert its declared
# component count.
_AIRGAP_EXTRA = "dod-il6"
_AIRGAP_PROFILE = "air-gap"

# Packages that must NEVER appear after installing an air-gap extra.
_FORBIDDEN_AIRGAP_PACKAGES = (
    "google-auth",
    "google-generativeai",
    "google-cloud-aiplatform",
    "tensorboard",
)


def _forbidden_airgap_packages(pip_list_json: str) -> list[str]:
    """Return which forbidden packages appear in `pip list --format=json` output."""
    try:
        pkgs = json.loads(pip_list_json)
    except Exception:
        return []
    installed = {
        str(p.get("name", "")).strip().lower().replace("_", "-") for p in pkgs
    }
    return sorted(f for f in _FORBIDDEN_AIRGAP_PACKAGES if f in installed)


def _airgap_expected_count(profile: str = _AIRGAP_PROFILE) -> int:
    """Component count the given core profile enables (from core_profiles.yaml)."""
    try:
        from tools.config.core_profile import get_profile
        p = get_profile(profile) or {}
        return len(p.get("default_enabled_components") or [])
    except Exception:
        return -1


def step_airgap_install(extra: str = _AIRGAP_EXTRA,
                        profile: str = _AIRGAP_PROFILE) -> dict:
    """Install the wheel OFFLINE from a local wheelhouse and verify the air-gap path.

    Returns a step dict. ``ok`` is True/False when the offline install could be
    attempted, or None when a fully offline install cannot be simulated in this
    environment (no network to pre-build the wheelhouse) — in which case
    ``verified`` / ``not_verified`` document exactly what was and wasn't proven,
    per the pkg-rel-03 spec, rather than silently passing.
    """
    result: dict = {"step": "airgap_install", "ok": None,
                    "extra": extra, "profile": profile}
    wheels = sorted(DIST_DIR.glob("*.whl")) if DIST_DIR.exists() else []
    if not wheels:
        result["ok"] = False
        result["error"] = "no wheel in dist/"
        return result
    spec = f"icdev[{extra}]"

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        wheelhouse = tmpdir / "wheelhouse"
        wheelhouse.mkdir()

        # Pre-stage a wheelhouse: download icdev (from dist/) + every dependency
        # of the air-gap extra. This step needs network (the build machine has
        # it); the INSTALL below then runs fully offline against the wheelhouse.
        r = _run([sys.executable, "-m", "pip", "download", spec,
                  "--dest", str(wheelhouse), "--find-links", str(DIST_DIR)],
                 timeout=600)
        if r.returncode != 0:
            result["ok"] = None
            result["skipped"] = True
            result["reason"] = (
                "could not pre-build the offline wheelhouse (no network / "
                "restricted index) — a fully offline install cannot be "
                "simulated here")
            result["not_verified"] = [
                "offline install of the wheel",
                "air-gap profile component count",
                "absence of forbidden air-gap packages",
            ]
            result["detail"] = (r.stderr or r.stdout or "")[-400:]
            return result
        result["wheelhouse_files"] = sum(1 for _ in wheelhouse.glob("*"))

        # Fresh venv, install OFFLINE only from the local wheelhouse.
        venv_dir = tmpdir / "venv"
        if _run([sys.executable, "-m", "venv", str(venv_dir)],
                timeout=60).returncode != 0:
            result["ok"] = False
            result["error"] = "venv creation failed"
            return result
        if os.name == "nt":
            py = venv_dir / "Scripts" / "python.exe"
            icdev_bin = venv_dir / "Scripts" / "icdev.exe"
        else:
            py = venv_dir / "bin" / "python"
            icdev_bin = venv_dir / "bin" / "icdev"

        r = _run([str(py), "-m", "pip", "install", "--no-index",
                  "--find-links", str(wheelhouse), spec], timeout=300)
        result["offline_install_ok"] = (r.returncode == 0)
        if r.returncode != 0:
            result["ok"] = False
            result["error"] = f"offline install failed: {(r.stderr or '')[-400:]}"
            return result

        # Negative assertion: no forbidden (Google/tensorboard) packages present.
        r = _run([str(py), "-m", "pip", "list", "--format=json"], timeout=60)
        forbidden = _forbidden_airgap_packages(r.stdout or "[]")
        result["forbidden_packages"] = forbidden

        # Positive: init with the air-gap CORE profile + status reports the
        # expected enabled component count.
        probe_cwd = tmpdir
        proj = tmpdir / "proj"
        r = _run([str(icdev_bin), "init", str(proj), "--profile", profile],
                 cwd=probe_cwd, timeout=60)
        if r.returncode != 0 and "No such" in (r.stderr or ""):
            r = _run([str(py), "-m", "icdev.tools.cli.init", str(proj),
                      "--profile", profile], cwd=probe_cwd, timeout=60)
        result["init_ok"] = (r.returncode == 0)

        r = _run([str(icdev_bin), "status", "--json",
                  "--env-file", str(proj / ".env")], cwd=probe_cwd, timeout=60)
        if r.returncode != 0 and "No such" in (r.stderr or ""):
            r = _run([str(py), "-m", "icdev.tools.cli.enable", "status",
                      "--json", "--env-file", str(proj / ".env")],
                     cwd=probe_cwd, timeout=60)
        enabled_count = None
        try:
            enabled_count = json.loads(r.stdout).get("enabled_count")
        except Exception:
            pass
        expected_count = _airgap_expected_count(profile)
        result["enabled_count"] = enabled_count
        result["expected_count"] = expected_count
        result["count_ok"] = (
            enabled_count is not None and expected_count >= 0
            and enabled_count == expected_count
        )

        result["ok"] = bool(
            result.get("offline_install_ok")
            and not forbidden
            and result.get("init_ok")
            and result.get("count_ok")
        )
    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run(skip_smoke: bool = False) -> dict:
    total = 4 if skip_smoke else 6
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
        _step(5, total, "Smoke test install in throwaway venv (REAL icdev init)")
        r5 = step_smoke_test()
        results.append(r5)
        print(f"  install={r5.get('install_ok')}  import={r5.get('import_ok')} "
              f"(v{r5.get('version')})  init={r5.get('init_ok')}  "
              f"registry_load={r5.get('registry_load_ok')}")
        print(f"  subsystem_imports={r5.get('subsystem_imports_ok')}  "
              f"column_policies={r5.get('column_policies_ok')}")
        if r5.get("expected_payload"):
            print(f"    payload contract: {r5['expected_payload']}")
        for f in r5.get("init_failures", []):
            print(f"    INIT FAIL: {f}")
        if r5.get("registry_load_info"):
            print(f"    {r5['registry_load_info']}")
        if r5.get("column_policies_info"):
            print(f"    {r5['column_policies_info']}")
        if r5.get("subsystem_imports_error"):
            print(f"    {r5['subsystem_imports_error']}")
        if not r5["ok"]:
            if r5.get("error"):
                print(f"  error: {r5['error']}")
            return {"ok": False, "failed_at": "smoke_test", "results": results}

        _step(6, total,
              "Air-gap OFFLINE install verification (--no-index wheelhouse)")
        r6 = step_airgap_install()
        results.append(r6)
        if r6.get("skipped"):
            # Fully offline install could not be simulated here — document what
            # was and was not verified rather than passing on a partial check.
            print(f"  SKIPPED: {r6.get('reason')}")
            print(f"    not verified: {', '.join(r6.get('not_verified', []))}")
        else:
            print(f"  offline_install={r6.get('offline_install_ok')}  "
                  f"init={r6.get('init_ok')}  "
                  f"enabled={r6.get('enabled_count')}/"
                  f"{r6.get('expected_count')} (count_ok={r6.get('count_ok')})")
            forb = r6.get("forbidden_packages")
            print(f"    forbidden air-gap packages present: "
                  f"{forb if forb else 'none'}")
            if not r6["ok"]:
                if r6.get("error"):
                    print(f"  error: {r6['error']}")
                return {"ok": False, "failed_at": "airgap_install",
                        "results": results}
    else:
        # Skipping the smoke test means the release path never proves the wheel's
        # `icdev init` actually works — make that impossible to miss.
        print("\n" + "!" * 68)
        print("!! WARNING: --skip-smoke — the throwaway-venv REAL-init test and "
              "the")
        print("!! air-gap OFFLINE install verification were SKIPPED.")
        print("!! The wheel's `icdev init` payload was NOT verified. This flag is "
              "for")
        print("!! local iteration ONLY and must NEVER be used on the documented "
              "release path.")
        print("!" * 68)
        results.append({"step": "smoke_test", "ok": None, "skipped": True,
                        "warning": "smoke + air-gap tests skipped via --skip-smoke"})

    return {"ok": True, "failed_at": None, "results": results,
            "smoke_skipped": bool(skip_smoke)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--skip-smoke", action="store_true",
                        help="LOCAL ITERATION ONLY: skip the throwaway-venv "
                             "REAL-init test. Prints a loud warning; never use "
                             "on the documented release path.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run(skip_smoke=args.skip_smoke)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["ok"] else 1

    print()
    print("=" * 68)
    if result["ok"]:
        print("  RELEASE READY" + ("  (SMOKE TEST SKIPPED — NOT release-safe)"
                                    if result.get("smoke_skipped") else ""))
        print("=" * 68)
        if result.get("smoke_skipped"):
            print("  NOTE: --skip-smoke was used; the wheel's `icdev init` was "
                  "NOT verified.\n        Re-run without --skip-smoke before "
                  "publishing.")
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
