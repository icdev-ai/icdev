#!/usr/bin/env python3
# CUI // SP-CTI
"""Print the interpreter this checkout's commands should run under (omx-linux-02).

WHY THIS EXISTS. Every ``/start`` and ``/stop`` step ran bare ``python``. On a
PEP 668 host (Arch, measured 2026-10-11 on the Omarchy laptop) that is the
SYSTEM interpreter, which has none of the project's packages -- ``dotenv`` and
``icdev.core.paths`` failed to import and the agent had to substitute
``.venv/bin/python`` by hand. On Windows the same bare ``python`` happened to
work only because the packages were installed into it.

Resolution order, first hit wins:

1. the ACTIVE virtualenv (``$VIRTUAL_ENV``),
2. ``<root>/.venv`` -- ``bin/python`` on POSIX, ``Scripts\\python.exe`` on Windows,
3. the interpreter running this script (``sys.executable``), i.e. what ``python``
   already meant.

STDLIB ONLY, and it imports nothing from ``tools.*``: it is run BY the system
interpreter precisely when that interpreter cannot import the project. The root
is the current directory (the commands run from the checkout root) unless
``--root`` says otherwise -- it is deliberately NOT derived from ``__file__``.

Usage::

    PY="$(python3 tools/dx/project_python.py 2>/dev/null || python tools/dx/project_python.py)"
    "$PY" tools/genesis/supervisor_status.py

    $PY = python tools/dx/project_python.py          # PowerShell
    & $PY tools/genesis/supervisor_status.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable, List, Mapping, Optional


def venv_python(venv: Path, os_name: str) -> Path:
    """The interpreter inside a virtualenv directory, for the given ``os.name``."""
    if os_name == "nt":
        return Path(venv) / "Scripts" / "python.exe"
    return Path(venv) / "bin" / "python"


def candidates(root: Path, env: Mapping[str, str], os_name: str) -> List[Path]:
    """Venv interpreters to try, in priority order. The fallback is not listed."""
    out: List[Path] = []
    active = (env.get("VIRTUAL_ENV") or "").strip()
    if active:
        out.append(venv_python(Path(active), os_name))
    out.append(venv_python(Path(root) / ".venv", os_name))
    return out


def resolve(
    root: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    os_name: Optional[str] = None,
    exists: Callable[[Path], bool] = os.path.isfile,
    fallback: Optional[str] = None,
) -> str:
    """The first existing venv interpreter, else ``fallback`` (``sys.executable``)."""
    root = Path.cwd() if root is None else Path(root)
    env = os.environ if env is None else env
    os_name = os.name if os_name is None else os_name
    for path in candidates(root, env, os_name):
        if exists(path):
            return str(path)
    return fallback or sys.executable or "python"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Print this checkout's Python interpreter")
    ap.add_argument("--root", type=Path, default=None,
                    help="checkout root (default: the current directory)")
    args = ap.parse_args(argv)
    print(resolve(args.root.resolve() if args.root else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
