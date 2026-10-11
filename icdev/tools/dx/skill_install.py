#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev skill install|uninstall` -- one cross-harness ICDEV skill (omx-dx-02).

Omarchy's pattern: ONE source skill, linked into every harness's skill dir.

- The source is ``icdev/data/skills/icdev/SKILL.md`` (Agent Skills frontmatter).
- ``install`` copies it to ``~/.agents/skills/icdev`` (the shared Agent Skills
  home), then links that copy into each harness skill dir that ALREADY EXISTS
  (``--dirs auto``) or into the harnesses named by ``--dirs claude,codex,...``.
  Where a symlink cannot be made (Windows without the symlink privilege) it
  falls back to a copy and says so per target.
- Every path it creates is recorded in a manifest
  (``~/.icdev/skill-install-manifest.json``); ``--uninstall`` removes exactly
  those paths and nothing else. A pre-existing ``icdev`` entry it did not
  create is left alone and reported as ``skipped``.

All targeted harnesses read the same Agent Skills format (``name`` +
``description`` frontmatter), so there is no per-harness translation today;
the source is validated with ``skill_translator.parse_claude_skill`` -- the
one frontmatter parser -- before anything is written.

Usage:
    icdev skill install [--dirs auto|claude,codex,...] [--json]
    icdev skill uninstall [--json]       (alias: icdev skill install --uninstall)
    icdev skill status [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_NAME = "icdev"

# Harness -> skill dir, relative to the user's home. Omarchy v4.0.4 seams plus
# opencode's global skills dir.
HARNESS_SKILL_DIRS: dict[str, tuple[str, ...]] = {
    "claude": (".claude", "skills"),
    "codex": (".codex", "skills"),
    "pi": (".pi", "agent", "skills"),
    "gemini": (".gemini", "config", "skills"),
    "hermes": (".hermes", "skills"),
    "opencode": (".config", "opencode", "skills"),
}

AGENTS_SKILLS = (".agents", "skills")
MANIFEST_PARTS = (".icdev", "skill-install-manifest.json")


def _home(home: Path | str | None) -> Path:
    return Path(home) if home else Path.home()


def source_dir() -> Path:
    """The packaged source skill directory (source checkout or wheel)."""
    from importlib.resources import files

    return Path(str(files("icdev.data") / "skills" / SKILL_NAME))


def manifest_path(home: Path | str | None = None) -> Path:
    return _home(home).joinpath(*MANIFEST_PARTS)


def _load_manifest(home: Path) -> dict:
    p = manifest_path(home)
    if not p.exists():
        return {"entries": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_manifest(home: Path, manifest: dict) -> None:
    p = manifest_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _validate_source(src: Path) -> None:
    from icdev.tools.dx.skill_translator import parse_claude_skill

    data = parse_claude_skill(src)
    has_frontmatter = data is not None and (src / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    if not has_frontmatter or data.get("name") != SKILL_NAME or not data.get("description"):
        raise ValueError(f"{src / 'SKILL.md'}: missing Agent Skills frontmatter (name: {SKILL_NAME}, description)")


def _remove(path: Path) -> None:
    """Remove a symlink (file or directory flavour) or a copied directory."""
    if path.is_symlink():
        try:
            path.unlink()
        except OSError:
            os.rmdir(path)  # a Windows directory symlink is removed with rmdir
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _link_or_copy(src: Path, dest: Path) -> str:
    """Symlink ``dest`` -> ``src``; fall back to a copy. Returns the mode used."""
    try:
        os.symlink(src, dest, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        shutil.copytree(src, dest)
        return "copy"


def resolve_targets(dirs: str, home: Path) -> list[tuple[str, Path, bool]]:
    """Return ``(harness, skills_dir, explicit)`` for ``--dirs``.

    ``auto`` keeps only harness skill dirs that already exist; a named list
    targets those harnesses whether or not their dir exists yet.
    """
    if dirs == "auto":
        return [
            (name, home.joinpath(*parts), False)
            for name, parts in HARNESS_SKILL_DIRS.items()
            if home.joinpath(*parts).is_dir()
        ]
    out = []
    for name in (n.strip() for n in dirs.split(",") if n.strip()):
        if name not in HARNESS_SKILL_DIRS:
            raise ValueError(f"unknown harness '{name}' (known: {', '.join(HARNESS_SKILL_DIRS)})")
        out.append((name, home.joinpath(*HARNESS_SKILL_DIRS[name]), True))
    return out


def install(dirs: str = "auto", home: Path | str | None = None, src: Path | None = None) -> dict:
    home = _home(home)
    src = Path(src) if src else source_dir()
    _validate_source(src)
    manifest = _load_manifest(home)
    owned = {e["path"] for e in manifest["entries"]}
    results: list[dict] = []

    def _record(path: Path, mode: str, harness: str) -> None:
        if str(path) not in owned:
            manifest["entries"].append({"path": str(path), "mode": mode, "harness": harness})
            owned.add(str(path))
        else:
            for e in manifest["entries"]:
                if e["path"] == str(path):
                    e["mode"] = mode

    # 1. The shared copy in ~/.agents/skills/icdev.
    shared = home.joinpath(*AGENTS_SKILLS, SKILL_NAME)
    if (shared.exists() or shared.is_symlink()) and str(shared) not in owned:
        raise FileExistsError(f"{shared} exists and was not installed by icdev; remove it first")
    if shared.exists() or shared.is_symlink():
        _remove(shared)
    shared.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, shared)
    _record(shared, "copy", "agents")
    results.append({"harness": "agents", "path": str(shared), "status": "installed", "mode": "copy"})

    # 2. One link per harness skill dir.
    for harness, skills_dir, explicit in resolve_targets(dirs, home):
        dest = skills_dir / SKILL_NAME
        if (dest.exists() or dest.is_symlink()) and str(dest) not in owned:
            results.append({"harness": harness, "path": str(dest), "status": "skipped",
                            "reason": "an icdev skill already exists here and was not installed by icdev"})
            continue
        if dest.exists() or dest.is_symlink():
            _remove(dest)
        if explicit:
            skills_dir.mkdir(parents=True, exist_ok=True)
        mode = _link_or_copy(shared, dest)
        _record(dest, mode, harness)
        entry = {"harness": harness, "path": str(dest), "status": "installed", "mode": mode}
        if mode == "copy":
            entry["note"] = "symlink not permitted here; installed a copy (re-run install to refresh it)"
        results.append(entry)

    manifest["installed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["source"] = str(src)
    _save_manifest(home, manifest)
    return {"action": "install", "manifest": str(manifest_path(home)), "results": results}


def uninstall(home: Path | str | None = None) -> dict:
    home = _home(home)
    manifest = _load_manifest(home)
    removed, missing = [], []
    # Harness links first, the shared copy they point at last.
    for e in sorted(manifest["entries"], key=lambda e: e["harness"] == "agents"):
        p = Path(e["path"])
        if p.exists() or p.is_symlink():
            _remove(p)
            removed.append(e["path"])
        else:
            missing.append(e["path"])
    mp = manifest_path(home)
    if mp.exists():
        mp.unlink()
    return {"action": "uninstall", "removed": removed, "already_gone": missing}


def status(home: Path | str | None = None) -> dict:
    home = _home(home)
    entries = _load_manifest(home)["entries"]
    for e in entries:
        p = Path(e["path"])
        e["present"] = p.exists() or p.is_symlink()
    return {"action": "status", "manifest": str(manifest_path(home)), "entries": entries,
            "harness_dirs": {n: home.joinpath(*parts).is_dir() for n, parts in HARNESS_SKILL_DIRS.items()}}


def _print(result: dict) -> None:
    if result["action"] == "install":
        for r in result["results"]:
            extra = f" ({r['mode']})" if "mode" in r else f" -- {r.get('reason', '')}"
            print(f"  {r['status']:<9} {r['harness']:<9} {r['path']}{extra}")
            if r.get("note"):
                print(f"            note: {r['note']}")
        print(f"Manifest: {result['manifest']}")
    elif result["action"] == "uninstall":
        for p in result["removed"]:
            print(f"  removed   {p}")
        for p in result["already_gone"]:
            print(f"  gone      {p}")
        print(f"Removed {len(result['removed'])} path(s).")
    else:
        for e in result["entries"]:
            print(f"  {'present' if e['present'] else 'MISSING':<8} {e['harness']:<9} {e['path']} ({e['mode']})")
        if not result["entries"]:
            print("icdev skill is not installed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="icdev skill", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest="cmd")
    ip = subs.add_parser("install", help="Install the ICDEV skill into every harness skill dir.")
    ip.add_argument("--dirs", default="auto",
                    help=f"'auto' (existing dirs only) or a comma list of: {', '.join(HARNESS_SKILL_DIRS)}")
    ip.add_argument("--uninstall", action="store_true", help="Remove exactly what install created.")
    ip.add_argument("--home", default=None, help=argparse.SUPPRESS)
    ip.add_argument("--json", action="store_true")
    for name, helptext in (("uninstall", "Remove exactly what install created."),
                           ("status", "Show what install created and whether it is still there.")):
        p = subs.add_parser(name, help=helptext)
        p.add_argument("--home", default=None, help=argparse.SUPPRESS)
        p.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1
    try:
        if args.cmd == "status":
            result = status(args.home)
        elif args.cmd == "uninstall" or args.uninstall:
            result = uninstall(args.home)
        else:
            result = install(args.dirs, args.home)
    except (ValueError, FileExistsError) as exc:
        print(f"icdev skill: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print(result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
