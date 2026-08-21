# CUI // SP-CTI
"""One repository-root resolver for every ICDEV parent.

Three resolvers existed before this module and agreed only by accident:
``icdev/_paths.py`` (``ICDEV_PROJECT_ROOT`` -> walk up for ``pyproject.toml``
-> ``icdev/data``), ``tools/llm/config_path.py`` (``ICDEV_LLM_CONFIG`` -> walk
up -> packaged copy) and ``tools/db/storage.py::_resolve_repo_base``
(``pyproject.toml`` / ``.git`` marker, with a name-based fallback). Each public
name survives as a thin delegate onto this module, so no call site changes.

Resolution order, documented once::

    1. $ICDEV_PROJECT_ROOT              explicit override (must be a directory)
    2. the SOURCE checkout that holds the calling file — the nearest ancestor
       of ``anchor`` carrying icdev_domain.yaml, pyproject.toml or .git
    3. the nearest icdev_domain.yaml walking up from the CURRENT DIRECTORY —
       the pip-installed case, where the kernel lives in site-packages and is
       serving whichever parent the process was started in
    4. the packaged fallback: the ``icdev/`` package directory itself

Step 2 comes BEFORE step 3 on purpose, and it is a deliberate deviation from
the first draft of the design. A session's shell resets its working directory
to the main checkout after every command, and several worktrees of the same
repository are live at once on one machine; a source checkout that took its
root from CWD would silently bind a worktree's code to another checkout's
data — the exact cross-load the identity check exists to refuse. CWD is
consulted only when the code itself is not in a source checkout.

A "source checkout" is recognised by a MARKER FILE, never by a directory
NAME: ``_resolve_repo_base`` used to compare names and over-walked on GitHub
Actions, whose workspace is ``/home/runner/work/icdev/icdev``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

DOMAIN_FILE = "icdev_domain.yaml"
PROJECT_ROOT_ENV = "ICDEV_PROJECT_ROOT"

#: Markers that identify a source checkout, most specific first.
SOURCE_MARKERS: tuple[str, ...] = (DOMAIN_FILE, "pyproject.toml", ".git")

_ICDEV_PKG_DIR = Path(__file__).resolve().parent.parent  # icdev/
_PACKAGED_DATA_DIR = _ICDEV_PKG_DIR / "data"


def _is_installed(path: Path) -> bool:
    """True when ``path`` lives under site-packages or the interpreter prefix."""
    p = str(path).replace("\\", "/").lower()
    if "site-packages" in p or "dist-packages" in p:
        return True
    try:
        prefix = str(Path(sys.prefix).resolve()).replace("\\", "/").lower()
    except OSError:  # pragma: no cover — sys.prefix always resolves in practice
        return False
    return bool(prefix) and p.startswith(prefix + "/")


def _walk_up(start: Path, markers: Iterable[str]) -> Path | None:
    """Return the nearest directory at or above ``start`` holding any marker."""
    markers = tuple(markers)
    start = start if start.is_dir() else start.parent
    for candidate in (start, *start.parents):
        for m in markers:
            if (candidate / m).exists():
                return candidate
    return None


def find_domain_file(start: Path | None = None) -> Path | None:
    """Return the nearest ``icdev_domain.yaml`` at or above ``start`` (default CWD)."""
    base = _walk_up(Path(start or Path.cwd()).resolve(), (DOMAIN_FILE,))
    return None if base is None else base / DOMAIN_FILE


def repo_root(anchor: str | os.PathLike[str] | None = None) -> Path:
    """Return the repository root this process should treat as home.

    ``anchor`` is the calling module's ``__file__`` (or any path inside the
    checkout). When omitted, the ``icdev`` package directory is used, which is
    correct for the kernel's own modules and for the wheel.
    """
    env = os.environ.get(PROJECT_ROOT_ENV, "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()

    anchor_path = Path(anchor).resolve() if anchor is not None else _ICDEV_PKG_DIR
    if not _is_installed(anchor_path):
        found = _walk_up(anchor_path, SOURCE_MARKERS)
        if found is not None:
            return found

    cwd_root = _walk_up(Path.cwd().resolve(), (DOMAIN_FILE,))
    if cwd_root is not None:
        return cwd_root

    return _ICDEV_PKG_DIR


def data_path(name: str, anchor: str | os.PathLike[str] | None = None) -> Path:
    """Resolve a FORGE data directory (args, context, goals, hardprompts, ...).

    Falls back to the packaged ``icdev/data/<name>`` when the repository root
    has no such directory (the wheel case), then to a CWD-relative path so a
    caller that only wants to CREATE the directory still gets a sane answer.
    """
    root = repo_root(anchor)
    candidate = root / name
    if candidate.is_dir():
        return candidate
    packaged = _PACKAGED_DATA_DIR / name
    if packaged.is_dir():
        return packaged
    return Path(name)


def config_path(
    relpath: str | os.PathLike[str],
    *,
    env: str | None = None,
    anchor: str | os.PathLike[str] | None = None,
    packaged: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve one configuration file: ``$env`` -> ``<repo_root>/relpath`` -> packaged.

    ``relpath`` is relative to the repository root (``args/llm_config.yaml``).
    ``packaged`` is the last-resort copy shipped inside the package; when not
    given, ``icdev/data/<relpath>`` is tried and finally ``<repo_root>/relpath``
    is returned even if it does not exist, so the caller's error names the
    path that SHOULD have been there.
    """
    if env:
        override = os.environ.get(env, "").strip()
        if override:
            return Path(override).expanduser().resolve()
    rel = Path(relpath)
    candidate = repo_root(anchor) / rel
    if candidate.is_file():
        return candidate
    if packaged is not None:
        return Path(packaged)
    pkg = _PACKAGED_DATA_DIR / rel
    if pkg.is_file():
        return pkg
    return candidate


def describe(anchor: str | os.PathLike[str] | None = None) -> dict:
    """Diagnostics for ``icdev status``: which root won, and why."""
    env = os.environ.get(PROJECT_ROOT_ENV, "").strip()
    anchor_path = Path(anchor).resolve() if anchor is not None else _ICDEV_PKG_DIR
    installed = _is_installed(anchor_path)
    source = None if installed else _walk_up(anchor_path, SOURCE_MARKERS)
    cwd_root = _walk_up(Path.cwd().resolve(), (DOMAIN_FILE,))
    root = repo_root(anchor)
    if env and Path(env).expanduser().is_dir():
        how = "env"
    elif source is not None and source == root:
        how = "source_checkout"
    elif cwd_root is not None and cwd_root == root:
        how = "cwd_domain_file"
    else:
        how = "packaged"
    return {
        "root": str(root),
        "source": how,
        "env_var": PROJECT_ROOT_ENV,
        "env_value": env or None,
        "anchor": str(anchor_path),
        "anchor_installed": installed,
        "source_candidate": str(source) if source else None,
        "cwd_candidate": str(cwd_root) if cwd_root else None,
        "domain_file": str(root / DOMAIN_FILE) if (root / DOMAIN_FILE).is_file() else None,
    }
