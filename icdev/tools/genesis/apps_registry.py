# CUI // SP-CTI
"""Genesis-enabled sibling systems, read from args/genesis_apps.yaml (xit-gen-01).

``tools/dashboard/app.py`` used to carry this table as a dict literal that put
every sibling at ``Path(BASE_DIR).parent / <name>`` and ran its daemon with that
directory as ``cwd`` and no existence check. This module produces the SAME
shape the dashboard always consumed::

    {"<key>": {"name", "root", "daemon", "promoter", "env_var", "db"}}

plus ``key``, ``root_env`` and ``available`` (``root`` is a directory), and it
resolves each root the way ``args/kanban_external_repos.yaml`` does: the
declared env var wins, the sibling-directory fallback applies otherwise.

It NEVER raises at import: an unreadable or absent YAML degrades to the one
entry that is always true -- this repository itself -- with a warning, because
a dashboard that cannot render /genesis over a config typo is a worse failure
than a dashboard that renders one app.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from tools.logging.icdev_logger import get_logger

log = get_logger("icdev.genesis.apps_registry")

CONFIG_RELPATH = Path("args") / "genesis_apps.yaml"


def _builtin_self(base_dir: Path) -> dict[str, dict[str, Any]]:
    base = Path(base_dir).resolve()
    return {
        "icdev": {
            "key": "icdev",
            "name": "ICDEV™",
            "root": str(base),
            "root_env": "ICDEV_PROJECT_ROOT",
            "daemon": "tools/genesis/daemon.py",
            "promoter": "tools/genesis/promoter.py",
            "env_var": "ICDEV_GENESIS_ENABLED",
            "db": str(base / "data" / "icdev.db"),
            "available": True,
        }
    }


def resolve_root(entry: Mapping[str, Any], base_dir: Path, environ: Mapping[str, str] | None = None) -> Path:
    """``root_env`` wins when set to an existing directory; else the sibling fallback."""
    env = os.environ if environ is None else environ
    base = Path(base_dir).resolve()
    root_env = str(entry.get("root_env") or "").strip()
    if root_env:
        value = (env.get(root_env) or "").strip()
        if value:
            candidate = Path(value).expanduser()
            if candidate.is_dir():
                return candidate.resolve()
    fallback = str(entry.get("root_fallback") or "").strip() or "."
    if fallback == ".":
        return base
    return (base.parent / fallback).resolve()


def load_genesis_apps(
    base_dir: Path,
    config_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the app registry. Same keys the dashboard has always read."""
    base = Path(base_dir).resolve()
    path = Path(config_path) if config_path is not None else base / CONFIG_RELPATH
    try:
        import yaml  # noqa: PLC0415 -- pyyaml is declared

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        apps = raw.get("apps") or {}
        if not isinstance(apps, Mapping) or "icdev" not in apps:
            raise ValueError("args/genesis_apps.yaml must declare an `apps:` mapping containing `icdev`")
    except Exception as exc:  # noqa: BLE001 -- degrade, never break /genesis
        log.warning("genesis_apps: %s unreadable (%s) -- serving this repository only", path, exc)
        return _builtin_self(base)

    out: dict[str, dict[str, Any]] = {}
    for key, entry in apps.items():
        entry = entry or {}
        root = resolve_root(entry, base, environ)
        db = str(entry.get("db") or "").strip()
        out[str(key)] = {
            "key": str(key),
            "name": str(entry.get("name") or key),
            "root": str(root),
            "root_env": str(entry.get("root_env") or ""),
            "daemon": str(entry.get("daemon") or "tools/genesis/daemon.py"),
            "promoter": (str(entry["promoter"]) if entry.get("promoter") else None),
            "env_var": str(entry.get("env_var") or f"{str(key).upper().replace('-', '_')}_GENESIS_ENABLED"),
            "db": str(root / db) if db else str(root / "data" / f"{key}.db"),
            "available": root.is_dir(),
        }
    return out


def root_missing(cfg: Mapping[str, Any]) -> dict[str, Any] | None:
    """The JSON answer for an app whose root is not on this machine, else None."""
    root = str(cfg.get("root") or "")
    if root and Path(root).is_dir():
        return None
    return {
        "error": "root_missing",
        "app": cfg.get("key"),
        "root": root,
        "root_env": cfg.get("root_env"),
        "hint": f"set {cfg.get('root_env') or 'the app root env var'} to the checkout, or clone it beside this repository",
    }
