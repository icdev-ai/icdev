#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev init` — Scaffold a new ICDEV™ project from the installed package.

Copies the FORGE orchestration layer from the installed icdev package into
the user's project directory so Claude Code (and humans) have everything
needed to drive ICDEV™:

    my-project/
    ├── CLAUDE.md            (master instructions for Claude Code)
    ├── .mcp.json            (MCP server configuration)
    ├── .env.template        (env var template — copy to .env and edit)
    ├── .claude/
    │   ├── commands/        (slash commands: /prime, /commit, /start, ...)
    │   ├── hooks/           (session hooks: stop, pre_tool_use, ...)
    │   ├── settings.json    (Claude Code settings)
    │   └── skills/          (ICDEV-specific skills)
    ├── args/                (deterministic behavior configs — editable)
    ├── goals/               (FORGE goal workflow definitions — editable)
    ├── hardprompts/         (reusable LLM instruction templates — editable)
    ├── context/             (reference material: tone, samples, case studies)
    ├── data/                (sqlite dbs, project-local)
    └── docs/                (project-local documentation)

After `icdev init`, users open the project in Claude Code and it "just works".

Usage:
    icdev init                        # scaffold in cwd (prompts before overwrite)
    icdev init my-project             # scaffold into ./my-project/
    icdev init --force                # overwrite existing files
    icdev init --minimal              # CLAUDE.md + .claude/ only (skip FORGE data)
    icdev init --list                 # just show what would be copied
    icdev init --profile local-dev    # non-interactive: apply an install profile
    icdev init --profile none         # skip the profile prompt (registry defaults)
    icdev init --json                 # JSON output

Install profiles come from args/core_profiles.yaml. With no --profile and a
real TTY, `icdev init` prompts for one (showing each profile's component
count); without a TTY (CI, piped installs) it falls back to a sensible default
(air-gap in an air-gap context, else local-dev) rather than blocking.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from importlib import resources
from pathlib import Path


def _package_resource_path(resource: str) -> Path | None:
    """Resolve a path inside the installed icdev package.

    Tries importlib.resources first (wheel install), falls back to direct
    filesystem resolution (development checkout).
    """
    try:
        # importlib.resources.files is Python 3.9+
        pkg_root = resources.files("icdev")
        target = pkg_root / resource
        if target.is_dir() or target.is_file():
            return Path(str(target))
    except Exception:
        pass
    # Dev fallback: relative to this file
    dev_root = Path(__file__).resolve().parent.parent.parent / "icdev"
    candidate = dev_root / resource
    if candidate.exists():
        return candidate
    return None


def _list_files(root: Path) -> list[Path]:
    """Recursively list files under root (excluding __pycache__ etc.)."""
    out: list = []
    if not root.exists():
        return out
    if root.is_file():
        return [root]
    for p in root.rglob("*"):
        if p.is_file():
            name = p.name
            if name == "__pycache__" or name.endswith(".pyc"):
                continue
            out.append(p)
    return out


# Mapping: bootstrap-source → project-target
BOOTSTRAP_MAP: list[tuple[str, str]] = [
    ("data/claude_bootstrap/CLAUDE.md", "CLAUDE.md"),
    ("data/claude_bootstrap/mcp.json", ".mcp.json"),
    ("data/claude_bootstrap/.env.template", ".env.template"),
    ("data/claude_bootstrap/claude/commands", ".claude/commands"),
    ("data/claude_bootstrap/claude/hooks", ".claude/hooks"),
    ("data/claude_bootstrap/claude/settings.json.template", ".claude/settings.json"),
    ("data/claude_bootstrap/claude/skills", ".claude/skills"),
    ("data/claude_bootstrap/claude/agents", ".claude/agents"),
]

# The non-Claude AI platform instruction files. ICDEV publishes the same
# guardrails to every major coding tool; without these, an installed project is
# Claude-only and the LLM-agnostic claim is false at the point it matters most
# — in the user's project. Restored from their flattened bootstrap names back
# to real paths (`platforms/cursor__rules__icdev.mdc` -> `.cursor/rules/icdev.mdc`).
try:
    from tools.dx.ai_platforms import AI_PLATFORM_FILES, bootstrap_name

    BOOTSTRAP_MAP.extend(
        (f"data/claude_bootstrap/{bootstrap_name(rel)}", rel)
        for _platform, rel in AI_PLATFORM_FILES
    )
except Exception:  # noqa: BLE001 - a missing platform list must not break init
    AI_PLATFORM_FILES = ()

# Bootstrap sources that may legitimately be absent from the package (e.g.
# `.claude/agents` ships zero files today but will the day agents are added).
# A missing OPTIONAL source is reported as "optional_missing" and does NOT
# count toward the `missing` total or flip the process exit code — a mapped
# entry with no source is a benign no-op, never an init failure.
OPTIONAL_SOURCES: set[str] = {
    "data/claude_bootstrap/claude/agents",
}

# A wheel built before the platform files were packaged simply will not have
# them; init should degrade to a Claude-only project, not fail.
try:
    from tools.dx.ai_platforms import bootstrap_name as _bn

    OPTIONAL_SOURCES.update(
        f"data/claude_bootstrap/{_bn(rel)}" for _p, rel in AI_PLATFORM_FILES
    )
except Exception:  # noqa: BLE001
    pass

# FORGE data (editable project config, copied from package defaults)
FORGE_MAP: list[tuple[str, str]] = [
    ("data/args", "args"),
    ("data/goals", "goals"),
    ("data/hardprompts", "hardprompts"),
    ("data/context", "context"),
]

#: FORGE layers that are NOT optional, even under `--minimal`.
#:
#: `goals/` is the Goals layer of FORGE and the entry point of ANVIL: CLAUDE.md
#: opens with "Check `goals/manifest.md` before starting any task" and repeats it
#: under How to Operate. `--minimal` used to skip the whole FORGE_MAP, so it
#: produced a project whose own instructions pointed at a file that was not
#: there — the agent's first action failed, silently, on a fresh install.
#:
#: args/, hardprompts/ and context/ stay optional under --minimal: they are
#: configuration and reference material, and a project can start without them.
REQUIRED_FORGE: tuple[str, ...] = ("data/goals",)

# Install-profile defaults. Profiles themselves are read dynamically from
# args/core_profiles.yaml (never hard-coded here) so this list never drifts.
# `_NONE_PROFILE` is the escape hatch for CI/scripts that want the registry's
# own default enablement with no profile applied.
_DEFAULT_PROFILE = "local-dev"
_AIRGAP_DEFAULT_PROFILE = "air-gap"
_NONE_PROFILE = "none"


def _available_profiles() -> "dict[str, dict]":
    """Load install profiles from args/core_profiles.yaml (dynamic, never hard-coded)."""
    try:
        from tools.config.core_profile import load_profiles

        return load_profiles()
    except Exception:
        return {}


def _profile_component_keys(profile: dict) -> set[str]:
    """The set of component keys a profile enables (default_enabled_components)."""
    comps = profile.get("default_enabled_components") or []
    return {str(k) for k in comps}


def _profile_env_overrides(profile: dict) -> dict[str, str]:
    """Non-component env defaults a profile implies (storage/LLM/air-gap).

    Delegates to core_profile.profile_env_overrides but strips the env-var-wins
    guard: at `icdev init` time we are authoring a fresh .env file, so we want
    the profile's declared defaults written even if they happen to be set in
    the current shell.
    """
    try:
        import os

        from tools.config.core_profile import profile_env_overrides

        # profile_env_overrides only emits keys not already in os.environ; for
        # authoring a file we want them all, so compute against a clean env.
        saved = dict(os.environ)
        try:
            for k in list(os.environ):
                if k.startswith("ICDEV_"):
                    del os.environ[k]
            return profile_env_overrides(profile)
        finally:
            os.environ.clear()
            os.environ.update(saved)
    except Exception:
        return {}


def _default_profile_name(profiles: dict) -> str:
    """Pick the non-interactive default: air-gap in an air-gap context, else local-dev."""
    airgap = False
    try:
        from tools.airgap import is_airgap

        airgap = bool(is_airgap())
    except Exception:
        airgap = False
    preferred = _AIRGAP_DEFAULT_PROFILE if airgap else _DEFAULT_PROFILE
    if preferred in profiles:
        return preferred
    # Fall back to any profile that exists so we never return a bogus name.
    return next(iter(profiles), _NONE_PROFILE)


def _prompt_for_profile(profiles: dict) -> str:
    """Interactively choose an install profile. Returns a profile name or 'none'."""
    names = list(profiles)
    print("\nChoose an install profile (which canvases/features to enable):\n")
    for i, name in enumerate(names, start=1):
        p = profiles[name]
        n = len(_profile_component_keys(p))
        desc = (p.get("description") or "").strip()
        print(f"  {i}. {name}  ({n} components)")
        if desc:
            print(f"       {desc}")
    print(f"  {len(names) + 1}. none  (registry defaults — nothing profile-forced)\n")

    default = _default_profile_name(profiles)
    try:
        raw = input(f"Profile [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not raw:
        return default
    if raw.isdigit():
        idx = int(raw)
        if idx == len(names) + 1:
            return _NONE_PROFILE
        if 1 <= idx <= len(names):
            return names[idx - 1]
        print(f"  (out of range — using default '{default}')")
        return default
    if raw == _NONE_PROFILE or raw in profiles:
        return raw
    print(f"  (unknown profile '{raw}' — using default '{default}')")
    return default


def resolve_profile(
    requested: str | None,
    interactive: bool | None = None,
) -> tuple[str | None, str]:
    """Resolve which install profile to apply.

    Returns ``(profile_name_or_None, source)`` where source is one of
    ``requested`` / ``prompt`` / ``non_tty_default`` / ``none``.
    A returned name of None means "no profile — use registry defaults".
    """
    profiles = _available_profiles()
    if interactive is None:
        interactive = sys.stdin.isatty()

    # Explicit --profile wins and stays non-interactive.
    if requested is not None:
        if requested == _NONE_PROFILE:
            return None, "requested"
        if requested in profiles:
            return requested, "requested"
        valid = ", ".join([*profiles, _NONE_PROFILE]) or _NONE_PROFILE
        raise ValueError(
            f"unknown profile '{requested}'. Valid profiles: {valid}"
        )

    if not profiles:
        return None, "none"

    # No TTY (CI, piped installs): fall back to the default rather than block.
    if not interactive:
        return _default_profile_name(profiles), "non_tty_default"

    chosen = _prompt_for_profile(profiles)
    if chosen == _NONE_PROFILE:
        return None, "prompt"
    return chosen, "prompt"


def _copy_one(src: Path, dst: Path, force: bool = False) -> tuple[bool, str]:
    """Copy one file or directory. Returns (copied, message)."""
    if not src.exists():
        return False, f"source missing: {src}"
    if dst.exists() and not force:
        return False, f"exists (use --force to overwrite): {dst}"

    try:
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return True, f"copied file: {dst}"
        if src.is_dir():
            if dst.exists() and force:
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.log", "*.pid"
            ))
            return True, f"copied tree: {dst}"
    except Exception as exc:
        return False, f"copy failed ({src} -> {dst}): {exc}"
    return False, "unknown error"


def init_project(
    target_dir: Path,
    force: bool = False,
    minimal: bool = False,
    only: list | None = None,
    list_only: bool = False,
    profile: str | None = None,
) -> dict:
    """Scaffold a new ICDEV project.

    Args:
        target_dir: Where to create the project (must exist or will be created).
        force: Overwrite existing files.
        minimal: Skip the optional FORGE layers (args/, hardprompts/,
            context/). goals/ is always copied — CLAUDE.md depends on it.
        only: Restrict to these project-relative targets, e.g.
            ``["CLAUDE.md"]`` or ``["goals"]``. Lets a user pull one artifact
            into an existing project without scaffolding around it.
        list_only: Don't copy — just report what would happen.
        profile: Install-profile name (from args/core_profiles.yaml) whose
            ``default_enabled_components`` and env defaults shape the generated
            .env. None ⇒ registry-default enablement (no profile applied).

    Returns a dict with success/failure per item.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    actions: list = []

    sources = list(BOOTSTRAP_MAP)
    if minimal:
        # Even a minimal scaffold needs the Goals layer — CLAUDE.md instructs
        # the agent to read goals/manifest.md before doing anything.
        sources.extend((pkg, proj) for pkg, proj in FORGE_MAP
                       if pkg in REQUIRED_FORGE)
    else:
        sources.extend(FORGE_MAP)

    # `--only` narrows to specific project-relative targets, so a user can pull
    # exactly CLAUDE.md (or goals/) into an existing project without scaffolding
    # everything around it.
    if only:
        wanted = {o.strip().rstrip("/\\") for o in only}
        sources = [(pkg, proj) for pkg, proj in sources
                   if str(proj).rstrip("/\\") in wanted]

    for pkg_rel, proj_rel in sources:
        src = _package_resource_path(pkg_rel)
        dst = target_dir / proj_rel

        if src is None:
            status = ("optional_missing" if pkg_rel in OPTIONAL_SOURCES
                      else "source_missing")
            actions.append({"src": pkg_rel, "dst": str(proj_rel),
                            "status": status})
            continue

        if list_only:
            n_files = len(_list_files(src))
            actions.append({"src": str(src), "dst": str(dst),
                            "status": "would_copy", "files": n_files})
            continue

        ok, msg = _copy_one(src, dst, force=force)
        actions.append({"src": str(src), "dst": str(dst),
                        "status": "copied" if ok else "skipped", "message": msg})

    # Write a complete .env: template's non-component keys + a generated
    # section covering EVERY component env flag from the registry, so no
    # canvas/feature is ever silently undiscoverable (the reported bug).
    profile_applied: str | None = None
    if not list_only:
        env_template = target_dir / ".env.template"
        env_file = target_dir / ".env"
        if env_template.exists() and not env_file.exists():
            msg = "created .env from template"
            try:
                from tools.cli.env_generator import compose_env
                from tools.config.component_registry import get_registry

                enabled_keys: set[str] | None = None
                env_overrides: dict[str, str] | None = None
                if profile:
                    prof = _available_profiles().get(profile)
                    if prof is not None:
                        enabled_keys = _profile_component_keys(prof)
                        env_overrides = _profile_env_overrides(prof)
                        env_overrides["ICDEV_CORE_PROFILE"] = profile
                        profile_applied = profile

                template_text = env_template.read_text(encoding="utf-8")
                composed = compose_env(
                    template_text, get_registry(),
                    enabled_keys=enabled_keys, env_overrides=env_overrides,
                )
                env_file.write_text(composed, encoding="utf-8")
                if profile_applied:
                    n = len(enabled_keys or set())
                    msg = (f"created .env from template + '{profile_applied}' "
                           f"profile ({n} components enabled)")
                else:
                    msg = "created .env from template + registry component flags"
            except Exception as exc:
                # Never let registry generation block init — fall back to the
                # static template so `icdev init` always produces a usable .env.
                shutil.copy2(env_template, env_file)
                msg = f"created .env from template (registry augmentation skipped: {exc})"
            actions.append({"src": ".env.template", "dst": str(env_file),
                            "status": "copied", "message": msg})

    summary = {
        "target": str(target_dir.resolve()),
        "minimal": minimal,
        "only": list(only) if only else [],
        "force": force,
        "list_only": list_only,
        "profile": profile_applied,
        "actions": actions,
        "copied": sum(1 for a in actions if a["status"] == "copied"),
        "skipped": sum(1 for a in actions if a["status"] == "skipped"),
        "missing": sum(1 for a in actions if a["status"] == "source_missing"),
        "optional_missing": sum(
            1 for a in actions if a["status"] == "optional_missing"
        ),
    }
    return summary


def _next_steps(target: Path) -> str:
    return (
        f"\nNext steps:\n"
        f"  1. cd {target}\n"
        f"  2. Edit .env to add your API keys (ANTHROPIC_API_KEY, etc.)\n"
        f"  3. icdev-init-db                 # initialize databases\n"
        f"  4. icdev-dashboard               # start dashboard on :5050\n"
        f"  5. Open the project in Claude Code — CLAUDE.md will guide the agent.\n"
        f"\n"
        f"Canvases (Tech Writer, Notebook, BI Dashboard, etc.) are opt-in —\n"
        f"run 'icdev list' to see them, 'icdev enable <name>' to turn one on.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="icdev init",
        description=__doc__.split("\n\n")[0],
    )
    parser.add_argument("target", nargs="?", default=".",
                        help="Target directory (default: current directory)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing files")
    parser.add_argument("--minimal", action="store_true",
                        help="Skip optional FORGE layers (args/, hardprompts/, "
                             "context/). goals/ is always included — CLAUDE.md "
                             "instructs the agent to read goals/manifest.md.")
    parser.add_argument("--only", nargs="+", metavar="TARGET", default=None,
                        help="Copy ONLY these targets, e.g. --only CLAUDE.md "
                             "goals. Use --list to see available names.")
    parser.add_argument("--list", dest="list_only", action="store_true",
                        help="Show what would be copied without copying")
    parser.add_argument(
        "--profile", metavar="NAME", default=None,
        help="Install profile from args/core_profiles.yaml (e.g. local-dev, "
             "air-gap, government). 'none' applies no profile (registry "
             "defaults). Omit for an interactive prompt; auto-falls back to a "
             "default when stdin is not a TTY.",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    target = Path(args.target).resolve()

    # Resolve the install profile. A prompt only fires on a real TTY and never
    # for --list (dry-run) or --json (machine) invocations.
    interactive = None
    if args.list_only or args.json:
        interactive = False
    try:
        profile_name, profile_source = resolve_profile(
            args.profile, interactive=interactive
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = init_project(
        target,
        force=args.force,
        minimal=args.minimal,
        only=args.only,
        list_only=args.list_only,
        profile=profile_name,
    )
    result["profile_source"] = profile_source

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Target: {result['target']}")
        if not args.list_only:
            shown = result.get("profile") or "none (registry defaults)"
            print(f"Profile: {shown}  [{profile_source}]")
        for a in result["actions"]:
            icon = {"copied": "[+]", "skipped": "[=]",
                    "source_missing": "[!]",
                    "optional_missing": "[~]",
                    "would_copy": "[?]"}.get(a["status"], "[?]")
            print(f"  {icon} {a['dst']}  ({a.get('message', a['status'])})")
        print(f"\nCopied: {result['copied']}   Skipped: {result['skipped']}   "
              f"Missing: {result['missing']}")
        if not args.list_only and result["copied"] > 0:
            print(_next_steps(target))

    return 0 if result["missing"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
