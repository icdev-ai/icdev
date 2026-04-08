# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Setup Wizard — interactive first-run configuration.

Guides users through dependency installation, database backend selection,
canvas enablement, LLM provider selection, and FileSync configuration
after ``pip install icdev``.

Works in both connected and air-gapped environments (pip resolves packages
from whatever index is configured — PyPI, local mirror, or Artifactory).

Usage::

    icdev-setup                        # interactive wizard
    pip install 'icdev[govcloud]'      # profile-based (skip wizard)
    python -m icdev.tools.installer.setup_wizard

Dependencies: Python stdlib only (pip is invoked via subprocess).
"""

from __future__ import annotations

import argparse
import importlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


def _prompt_choice(prompt: str, options: List[str], default: int = 0) -> int:
    """Display a numbered list and return the zero-based index chosen."""
    print("\n" + prompt)
    print("-" * 60)
    for i, opt in enumerate(options):
        marker = " *" if i == default else ""
        print("  %d. %s%s" % (i + 1, opt, marker))
    print()
    while True:
        raw = input("Enter choice [1-%d] (default=%d): " % (len(options), default + 1)).strip()
        if not raw:
            return default
        try:
            choice = int(raw) - 1
            if 0 <= choice < len(options):
                return choice
            print("  Please enter a number between 1 and %d." % len(options))
        except ValueError:
            print("  Please enter a valid number.")


def _prompt_multi_choice(
    prompt: str,
    options: List[str],
    defaults: Optional[List[int]] = None,
) -> List[int]:
    """Display a numbered list and return a list of zero-based indices."""
    if defaults is None:
        defaults = []
    print("\n" + prompt)
    print("-" * 60)
    for i, opt in enumerate(options):
        check = "[x]" if i in defaults else "[ ]"
        print("  %d. %s %s" % (i + 1, check, opt))
    print()
    print("  Enter numbers separated by commas (e.g., 1,3,5)")
    print("  Press Enter to accept defaults, or 'none' to clear all.")
    print()
    while True:
        raw = input("Your selection: ").strip()
        if not raw:
            return list(defaults)
        if raw.lower() in ("none", "0"):
            return []
        try:
            indices = []  # type: List[int]
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                if "-" in part:
                    start_s, end_s = part.split("-", 1)
                    for n in range(int(start_s), int(end_s) + 1):
                        idx = n - 1
                        if 0 <= idx < len(options) and idx not in indices:
                            indices.append(idx)
                else:
                    idx = int(part) - 1
                    if 0 <= idx < len(options) and idx not in indices:
                        indices.append(idx)
            return indices
        except ValueError:
            print("  Please enter valid numbers separated by commas.")


def _prompt_confirm(prompt: str, default: bool = True) -> bool:
    """Yes/no confirmation prompt."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input("\n%s %s: " % (prompt, suffix)).strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please enter 'y' or 'n'.")


def _prompt_input(prompt: str, default: str = "") -> str:
    """Prompt for free-text input with an optional default."""
    if default:
        raw = input("%s [%s]: " % (prompt, default)).strip()
        return raw if raw else default
    while True:
        raw = input("%s: " % prompt).strip()
        if raw:
            return raw
        print("  A value is required.")


# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------

# Maps pip extra names to the Python packages they install
_EXTRA_PROBE = {
    "llm-local": [("openai", "openai"), ("ollama", "ollama")],
    "llm": [("openai", "openai"), ("anthropic", "anthropic"), ("ollama", "ollama")],
    "llm-bedrock": [("boto3", "boto3")],
    "llm-azure": [("openai", "openai")],
    "llm-gemini": [("google.generativeai", "google-generativeai")],
    "llm-airgap": [("openai", "openai"), ("anthropic", "anthropic"), ("boto3", "boto3")],
    "search": [("numpy", "numpy"), ("rank_bm25", "rank_bm25")],
    "testing": [("pytest", "pytest"), ("ruff", "ruff")],
    "security": [("bandit", "bandit")],
    "network": [("defusedxml", "defusedxml"), ("networkx", "networkx")],
    "saas": [("psycopg2", "psycopg2-binary")],
    "postgresql": [("psycopg2", "psycopg2-binary")],
    "image-gen": [("torch", "torch"), ("diffusers", "diffusers")],
}


def _is_extra_installed(extra: str) -> bool:
    """Check if all packages for a pip extra are importable."""
    probes = _EXTRA_PROBE.get(extra, [])
    if not probes:
        return False
    for module_name, _pkg_name in probes:
        try:
            importlib.import_module(module_name)
        except ImportError:
            return False
    return True


def _detect_installed_extras() -> List[str]:
    """Return list of extras whose packages are already installed."""
    installed = []  # type: List[str]
    for extra in _EXTRA_PROBE:
        if _is_extra_installed(extra):
            installed.append(extra)
    return installed


def _compute_missing_extras(needed: List[str]) -> List[str]:
    """Return extras from needed that are not yet installed."""
    return [e for e in needed if not _is_extra_installed(e)]


# ---------------------------------------------------------------------------
# Canvas definitions
# ---------------------------------------------------------------------------

# (short_key, display_name, dashboard_env_var, storage_backend_env_var, pip_extra)
CANVAS_DEFS = [
    ("idc", "Infrastructure Design Canvas  (IDC)", "ICDEV_IDC_ENABLED", "IDC_STORAGE_BACKEND", None),
    ("ndc", "Network Design Canvas         (NDC)", "ICDEV_NDC_ENABLED", "NC_STORAGE_BACKEND", "network"),
    ("sdc", "Security Design Canvas        (SDC)", "ICDEV_SDC_ENABLED", "SC_STORAGE_BACKEND", None),
    ("bdc", "Boundary Design Canvas        (BDC)", "ICDEV_BDC_ENABLED", "BDC_STORAGE_BACKEND", None),
    ("pdc", "Pipeline Design Canvas        (PDC)", "ICDEV_PDC_ENABLED", "PC_STORAGE_BACKEND", None),
    ("odc", "Observability Design Canvas   (ODC)", "ICDEV_ODC_ENABLED", "OC_STORAGE_BACKEND", None),
    ("ddc", "Data Design Canvas            (DDC)", "ICDEV_DDC_ENABLED", "DDC_STORAGE_BACKEND", None),
    ("qdc", "Quality Design Canvas         (QDC)", "ICDEV_QDC_ENABLED", "QDC_STORAGE_BACKEND", None),
    ("mdc", "Migration Design Canvas       (MDC)", "ICDEV_MIGRATION_CANVAS_ENABLED", "MC_STORAGE_BACKEND", None),
]

# LLM provider choices -> pip extras
LLM_CHOICES = [
    ("Local only (Ollama, vLLM, llama.cpp — air-gap safe)", ["llm-local"]),
    ("Anthropic + local", ["llm"]),
    ("AWS Bedrock + local (GovCloud compatible)", ["llm-local", "llm-bedrock"]),
    ("Azure OpenAI + local", ["llm-local", "llm-azure"]),
    ("Air-gap safe bundle (all except Google)", ["llm-airgap"]),
    ("All cloud providers (includes Google — NOT air-gap safe)", ["llm-all"]),
    ("None (I'll configure LLM later)", []),
]

# Additional feature extras
FEATURE_CHOICES = [
    ("Semantic search (RAG, knowledge base)", "search"),
    ("Security scanning (bandit, pip-audit, SBOM)", "security"),
    ("Test suite (pytest, behave, ruff)", "testing"),
    ("Image generation (SDXL Turbo — requires GPU)", "image-gen"),
]

# Pre-built install profiles for reference
INSTALL_PROFILES = {
    "developer": "llm-local,search,testing,security",
    "govcloud": "llm-airgap,search,security,network",
    "dod-il6": "llm-local,search,security,network",
    "full-airgap": "llm-airgap,search,testing,security,network",
    "full": "llm-all,search,testing,security,network,saas",
    "minimal-airgap": "llm-local,search",
}


# ---------------------------------------------------------------------------
# pip install helper
# ---------------------------------------------------------------------------


def _pip_install_extras(extras: List[str]) -> bool:
    """Install icdev with the specified extras via pip.

    Works with any pip index (PyPI, local mirror, Artifactory, etc.).
    pip resolves packages from whatever source is configured.

    Args:
        extras: List of extra names (e.g. ["saas", "llm-local", "network"]).

    Returns:
        True on success.
    """
    if not extras:
        return True

    extra_str = ",".join(sorted(set(extras)))
    spec = "icdev[%s]" % extra_str
    print("\n  Running: pip install '%s'" % spec)
    print("  (resolves from your configured pip index)")
    print("  " + "-" * 56)

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", spec],
        timeout=600,
    )
    if result.returncode == 0:
        print("\n  -> Installed successfully.")
        return True
    else:
        print("\n  WARNING: pip install returned code %d." % result.returncode)
        print("  You can retry manually: pip install '%s'" % spec)
        return False


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------


def step_database() -> tuple:
    """Step 1: Choose database backend.

    Returns:
        (env_vars dict, list of pip extras needed)
    """
    print("\n" + "=" * 60)
    print("  Step 1: Database Backend")
    print("=" * 60)
    idx = _prompt_choice(
        "Select the database backend for ICDEV:",
        [
            "SQLite  (default, zero-config, air-gap safe)",
            "PostgreSQL  (production, multi-user, scalable)",
        ],
        default=0,
    )
    env = {}  # type: Dict[str, str]
    extras = []  # type: List[str]
    if idx == 0:
        env["ICDEV_STORAGE_BACKEND"] = "sqlite"
        print("\n  -> SQLite selected. No additional packages needed.")
    else:
        env["ICDEV_STORAGE_BACKEND"] = "postgresql"
        extras.append("postgresql")
        print("\n  Configure PostgreSQL connection:")
        env["ICDEV_PG_HOST"] = _prompt_input("    Host", "localhost")
        env["ICDEV_PG_PORT"] = _prompt_input("    Port", "5432")
        env["ICDEV_PG_USER"] = _prompt_input("    User", "icdev")
        env["ICDEV_PG_PASSWORD"] = _prompt_input("    Password")
        env["ICDEV_PG_DATABASE"] = _prompt_input("    Database name", "icdev")
        print("\n  -> PostgreSQL configured.")
    return env, extras


def step_canvases() -> tuple:
    """Step 2: Select which design canvases to enable.

    Returns:
        (env_vars dict, list of pip extras needed)
    """
    print("\n" + "=" * 60)
    print("  Step 2: Design Canvases")
    print("=" * 60)
    print("\n  ICDEV ships 9 design canvases. None are enabled by default.")
    print("  Select the ones you need (you can change this later in .env).\n")

    display_names = [c[1] for c in CANVAS_DEFS]
    selected = _prompt_multi_choice(
        "Which canvases do you want to enable?",
        display_names,
        defaults=[],
    )

    env = {}  # type: Dict[str, str]
    extras = []  # type: List[str]
    for i, (key, name, env_var, _storage_var, pip_extra) in enumerate(CANVAS_DEFS):
        env[env_var] = "true" if i in selected else "false"
        if i in selected and pip_extra:
            extras.append(pip_extra)

    env["ICDEV_CANVAS_KG_ENABLED"] = "true" if selected else "false"

    enabled_count = len(selected)
    if enabled_count == 0:
        print("\n  -> No canvases enabled.")
    else:
        names = [CANVAS_DEFS[i][0].upper() for i in selected]
        print("\n  -> Enabled %d canvas(es): %s" % (enabled_count, ", ".join(names)))
    return env, extras


def step_llm() -> tuple:
    """Step 3: Select LLM provider(s).

    Returns:
        (env_vars dict, list of pip extras needed)
    """
    print("\n" + "=" * 60)
    print("  Step 3: LLM Provider")
    print("=" * 60)
    print("\n  Choose which LLM backends to install.")
    print("  The base install has zero LLM dependencies.\n")

    options = [c[0] for c in LLM_CHOICES]
    idx = _prompt_choice(
        "Select LLM provider configuration:",
        options,
        default=0,
    )
    extras = list(LLM_CHOICES[idx][1])
    env = {}  # type: Dict[str, str]

    if extras:
        print("\n  -> Selected: %s" % ", ".join(extras))
    else:
        print("\n  -> No LLM packages selected.")
    return env, extras


def step_features() -> tuple:
    """Step 4: Select additional features.

    Returns:
        (env_vars dict, list of pip extras needed)
    """
    print("\n" + "=" * 60)
    print("  Step 4: Additional Features")
    print("=" * 60)
    print("\n  Select optional features to install.\n")

    display_names = [f[0] for f in FEATURE_CHOICES]
    selected = _prompt_multi_choice(
        "Which features do you want?",
        display_names,
        defaults=[],
    )

    extras = [FEATURE_CHOICES[i][1] for i in selected]
    env = {}  # type: Dict[str, str]

    if extras:
        print("\n  -> Selected: %s" % ", ".join(extras))
    else:
        print("\n  -> No additional features selected.")
    return env, extras


def step_filesync() -> Dict[str, str]:
    """Step 5: Enable/disable FileSync."""
    print("\n" + "=" * 60)
    print("  Step 5: FileSync")
    print("=" * 60)
    print("\n  FileSync provides Syncthing-inspired file synchronization.")
    print("  It is disabled by default.\n")

    enabled = _prompt_confirm("Enable FileSync?", default=False)
    val = "true" if enabled else "false"
    print("\n  -> FileSync %s." % ("enabled" if enabled else "disabled"))
    return {"ICDEV_FILESYNC_ENABLED": val}


def step_canvas_storage(
    db_backend: str, canvas_env: Dict[str, str]
) -> Dict[str, str]:
    """Set canvas storage backend based on main DB choice."""
    env = {}  # type: Dict[str, str]
    if db_backend == "postgresql":
        env["ICDEV_CANVAS_STORAGE_BACKEND"] = "postgresql"
        for _key, _name, _dash_env, storage_env, _extra in CANVAS_DEFS:
            if canvas_env.get(_dash_env) == "true":
                env[storage_env] = "postgresql"
    else:
        env["ICDEV_CANVAS_STORAGE_BACKEND"] = "sqlite"
    return env


def step_install_deps(all_extras: List[str], plan_only: bool = False) -> List[str]:
    """Step 6: Detect installed packages, install missing ones.

    Args:
        all_extras: List of extras needed based on wizard selections.
        plan_only: If True, print the pip command but do not run it.
                   Use this to generate the install command for a connected
                   staging machine before transferring to air-gapped env.

    Returns:
        List of all extras (installed or planned).
    """
    if not all_extras:
        print("\n  No additional packages needed — base install is sufficient.")
        return []

    deduped = sorted(set(all_extras))
    already = [e for e in deduped if _is_extra_installed(e)]
    missing = [e for e in deduped if e not in already]

    print("\n" + "=" * 60)
    print("  Step 6: Install Dependencies")
    print("=" * 60)

    if already:
        print("\n  Already installed: %s" % ", ".join(already))

    if not missing:
        print("  All required packages are already installed.")
        return deduped

    full_str = ",".join(deduped)
    missing_str = ",".join(missing)

    if plan_only:
        print("\n  Missing packages: %s" % ", ".join(missing))
        print("\n  ── Run this on a machine with PyPI access ──")
        print()
        print("  # Download wheels for transfer to air-gapped environment:")
        print("  pip download 'icdev[%s]' -d ./icdev-wheels" % full_str)
        print()
        print("  # Then on the air-gapped machine, install from local wheels:")
        print("  pip install --no-index --find-links ./icdev-wheels 'icdev[%s]'" % full_str)
        print()
        print("  # Or if your local PyPI mirror has the packages:")
        print("  pip install 'icdev[%s]'" % full_str)
        return deduped

    print("  Missing packages:  %s" % ", ".join(missing))
    print("\n  Command: pip install 'icdev[%s]'" % missing_str)
    print("  (resolves from your configured pip index / local mirror)")

    if _prompt_confirm("Install missing packages now?", default=True):
        _pip_install_extras(missing)
    else:
        print("\n  Skipped. Install manually before starting the dashboard:")
        print("    pip install 'icdev[%s]'" % missing_str)

    return deduped


# ---------------------------------------------------------------------------
# .env generation and DB init
# ---------------------------------------------------------------------------


def _find_project_root() -> Path:
    """Find project root by walking up from this file or CWD."""
    d = Path(__file__).resolve().parent
    for _ in range(8):
        if (d / "pyproject.toml").exists():
            return d
        parent = d.parent
        if parent == d:
            break
        d = parent
    return Path.cwd()


def generate_env_file(env_vars: Dict[str, str], project_root: Path) -> Path:
    """Generate .env file. Backs up existing .env if present."""
    env_path = project_root / ".env"
    example_path = project_root / ".env.example"

    if env_path.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup = env_path.with_suffix(".env.backup.%s" % ts)
        shutil.copy2(str(env_path), str(backup))
        print("\n  Backed up existing .env to %s" % backup.name)

    lines = []  # type: List[str]
    if example_path.exists():
        with open(str(example_path), "r", encoding="utf-8") as f:
            lines = f.readlines()

    existing_keys = set()  # type: set
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            existing_keys.add(key)

    new_lines = []  # type: List[str]
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in env_vars:
                new_lines.append("%s=%s\n" % (key, env_vars[key]))
                continue
        new_lines.append(line)

    appended = []  # type: List[str]
    for key, val in env_vars.items():
        if key not in existing_keys:
            appended.append("%s=%s\n" % (key, val))
    if appended:
        new_lines.append("\n# ── Wizard additions ──\n")
        new_lines.extend(appended)

    with open(str(env_path), "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print("  Generated .env at %s" % env_path)
    return env_path


def run_init_db(project_root: Path) -> bool:
    """Run icdev-init-db to initialize databases."""
    print("\n  Initializing databases...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "icdev.tools.db.init_icdev_db"],
            cwd=str(project_root),
            timeout=120,
        )
        if result.returncode == 0:
            print("  Database initialization complete.")
            return True
        else:
            print("  WARNING: Database init returned code %d." % result.returncode)
            return False
    except FileNotFoundError:
        init_script = project_root / "tools" / "db" / "init_icdev_db.py"
        if init_script.exists():
            result = subprocess.run(
                [sys.executable, str(init_script)],
                cwd=str(project_root),
                timeout=120,
            )
            return result.returncode == 0
        print("  WARNING: Could not find init_icdev_db. Run 'icdev-init-db' manually.")
        return False
    except Exception as exc:
        print("  WARNING: Database init failed: %s" % exc)
        return False


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(env_vars: Dict[str, str], installed_extras: List[str]) -> None:
    """Print configuration summary and next steps."""
    print("\n" + "=" * 60)
    print("  Setup Complete!")
    print("=" * 60)

    db = env_vars.get("ICDEV_STORAGE_BACKEND", "sqlite")
    print("\n  Database:   %s" % db.upper())
    if db == "postgresql":
        host = env_vars.get("ICDEV_PG_HOST", "localhost")
        port = env_vars.get("ICDEV_PG_PORT", "5432")
        dbname = env_vars.get("ICDEV_PG_DATABASE", "icdev")
        print("              %s:%s/%s" % (host, port, dbname))

    canvases = []  # type: List[str]
    for key, name, env_var, _, _extra in CANVAS_DEFS:
        if env_vars.get(env_var) == "true":
            canvases.append(key.upper())
    if canvases:
        print("  Canvases:   %s" % ", ".join(canvases))
    else:
        print("  Canvases:   None (enable later in .env)")

    fs = env_vars.get("ICDEV_FILESYNC_ENABLED", "false")
    print("  FileSync:   %s" % ("Enabled" if fs == "true" else "Disabled"))

    if installed_extras:
        print("  Extras:     icdev[%s]" % ",".join(sorted(set(installed_extras))))
    else:
        print("  Extras:     base only")

    print("\n  Next steps:")
    print("    1. Review/edit .env for LLM keys and classification settings")
    print("    2. Run: icdev-dashboard")
    print("    3. Visit: http://localhost:5000")
    print("    4. Run: icdev-setup   (to reconfigure at any time)")

    # Show equivalent profile for future installs
    extra_set = set(installed_extras) if installed_extras else set()
    for profile, extras_csv in INSTALL_PROFILES.items():
        profile_set = set(extras_csv.split(","))
        if extra_set == profile_set:
            print("\n  Tip: Next time you can skip the wizard with:")
            print("    pip install 'icdev[%s]'" % profile)
            break

    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for ``icdev-setup`` console_scripts."""
    parser = argparse.ArgumentParser(
        prog="icdev-setup",
        description="ICDEV Setup Wizard — interactive first-run configuration.",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help=(
            "Don't install packages — just print the pip commands. "
            "Use on air-gapped machines to generate the install command "
            "for a connected staging machine."
        ),
    )
    parser.add_argument(
        "--show-profiles",
        action="store_true",
        help="Show available install profiles and exit.",
    )
    args = parser.parse_args()

    if args.show_profiles:
        print("\n  ICDEV Install Profiles")
        print("  " + "=" * 56)
        print()
        print("  Usage: pip install 'icdev[PROFILE]'")
        print("         pip install 'icdev[PROFILE,postgresql]'  # add PG")
        print()
        for name, extras in sorted(INSTALL_PROFILES.items()):
            print("  %-20s icdev[%s]" % (name, extras))
        print()
        print("  Combine with postgresql for PG backend:")
        print("    pip install 'icdev[govcloud,postgresql]'")
        print()
        print("  Air-gap wheel download:")
        print("    pip download 'icdev[govcloud]' -d ./wheels")
        print("    # transfer ./wheels to air-gapped machine, then:")
        print("    pip install --no-index --find-links ./wheels 'icdev[govcloud]'")
        print()
        return

    plan_only = args.plan_only

    print()
    print("=" * 60)
    print("  ICDEV Setup Wizard")
    if plan_only:
        print("  (plan-only mode — no packages will be installed)")
    print("=" * 60)
    print()
    print("  This wizard configures your ICDEV installation.")
    if not plan_only:
        print("  It installs only the dependencies you select.")
        print("  Works with PyPI, local mirrors, and air-gapped repos.")
    else:
        print("  It will generate pip commands for your staging machine.")
    print()
    print("  Or skip the wizard with a profile:")
    print("    pip install 'icdev[developer]'     # local LLM + dev tools")
    print("    pip install 'icdev[govcloud]'       # IL4/IL5 GovCloud")
    print("    pip install 'icdev[dod-il6]'        # IL6 air-gap")
    print("    pip install 'icdev[full-airgap]'    # everything air-gap safe")
    print()
    print("  Run: icdev-setup --show-profiles      # see all profiles")
    print()
    print("  Press Ctrl+C at any time to cancel.")

    try:
        project_root = _find_project_root()
        all_extras = []  # type: List[str]

        # Detect what's already installed
        pre_installed = _detect_installed_extras()
        if pre_installed:
            print("\n  Detected installed extras: %s" % ", ".join(pre_installed))

        # Step 1: Database
        db_vars, db_extras = step_database()
        db_backend = db_vars.get("ICDEV_STORAGE_BACKEND", "sqlite")
        all_extras.extend(db_extras)

        # Step 2: Canvases
        canvas_vars, canvas_extras = step_canvases()
        all_extras.extend(canvas_extras)

        # Step 3: LLM providers
        llm_vars, llm_extras = step_llm()
        all_extras.extend(llm_extras)

        # Step 4: Additional features
        feature_vars, feature_extras = step_features()
        all_extras.extend(feature_extras)

        # Step 5: FileSync
        filesync_vars = step_filesync()

        # Canvas storage (inherits main DB choice)
        canvas_storage_vars = step_canvas_storage(db_backend, canvas_vars)

        # Merge all env vars
        all_vars = {}  # type: Dict[str, str]
        all_vars.update(db_vars)
        all_vars.update(canvas_vars)
        all_vars.update(llm_vars)
        all_vars.update(feature_vars)
        all_vars.update(filesync_vars)
        all_vars.update(canvas_storage_vars)

        # Step 6: Install missing dependencies (or plan)
        installed = step_install_deps(all_extras, plan_only=plan_only)

        # Step 7: Generate .env
        print("\n" + "=" * 60)
        print("  Step 7: Generate Configuration")
        print("=" * 60)
        generate_env_file(all_vars, project_root)

        # Step 8: Init databases
        if not plan_only:
            print("\n" + "=" * 60)
            print("  Step 8: Initialize Databases")
            print("=" * 60)
            if _prompt_confirm("Initialize databases now?", default=True):
                run_init_db(project_root)
            else:
                print("  Skipped. Run 'icdev-init-db' when ready.")
        else:
            print("\n  Skipping DB init in plan-only mode.")
            print("  Run 'icdev-init-db' after installing packages.")

        # Summary
        print_summary(all_vars, installed)

    except KeyboardInterrupt:
        print("\n\n  Setup cancelled.")
        sys.exit(1)


if __name__ == "__main__":
    main()
