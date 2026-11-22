# CUI // SP-CTI
"""Read a file under a reviewed root, never through a symlink, a hard link or a FIFO (icx-ent-04).

A caller names a declared thing (a package, a target); which files are read comes only from reviewed configuration,
and each is read here: opened component by component with ``openat`` and ``O_NOFOLLOW`` (a symlink anywhere on the
path, or one swapped in after a check, is refused rather than followed), opened ``O_NONBLOCK`` so a FIFO or a device
is refused at ``fstat`` instead of hanging, a regular file with ONE link, within the size limit, and read once -- the
bytes hashed are the bytes used.

The same rule icx-ent-03 wrote inline for the factory's inputs (``tools/factory_builds/config.py``, unmerged when this
was written); the harmonisation pass moves that copy onto this one.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

REL = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]{0,299}", re.ASCII)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class ConfinedError(ValueError):
    pass


def rel_ok(rel: str) -> bool:
    """Relative, strict ASCII, no empty, ``.`` or ``..`` component."""
    return isinstance(rel, str) and bool(REL.fullmatch(rel)) and all(p not in ("", ".", "..") for p in rel.split("/"))


def _open_under(base: Path, rel: str, *, last_flags: int) -> int:
    if not rel_ok(rel):
        raise ConfinedError("a path is relative, ASCII and inside its root")
    parts = rel.split("/")
    if os.open in os.supports_dir_fd:
        try:
            fd = os.open(base, os.O_RDONLY | _DIRECTORY)
        except OSError as exc:
            raise ConfinedError("the root cannot be opened") from exc
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            return os.open(parts[-1], last_flags | _NOFOLLOW, dir_fd=fd)
        except OSError as exc:      # ELOOP / ENOTDIR: a symlink on the way; ENOENT: not there
            raise ConfinedError(f"{rel}: missing, or a symlink, which is never followed") from exc
        finally:
            os.close(fd)
    p = base
    for part in parts:
        p = p / part
        if p.is_symlink():
            raise ConfinedError(f"{rel}: a symlink is never followed")
    try:
        return os.open(p, last_flags | _NOFOLLOW)
    except OSError as exc:
        raise ConfinedError(f"{rel}: missing, or a symlink, which is never followed") from exc


def read_confined(base: Path, rel: str, *, max_bytes: int) -> bytes:
    """The bytes of ``base/rel``: a regular file with one link, no symlink on the way, at most ``max_bytes``."""
    fd = _open_under(Path(base), rel, last_flags=os.O_RDONLY | _NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise ConfinedError(f"{rel}: not a regular file with one link")
        if st.st_size > max_bytes:
            raise ConfinedError(f"{rel}: larger than {max_bytes} bytes")
        with os.fdopen(os.dup(fd), "rb") as fh:
            data = fh.read(max_bytes + 1)
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise ConfinedError(f"{rel}: larger than {max_bytes} bytes")
    return data


def exists_confined(base: Path, rel: str) -> bool:
    try:
        os.close(_open_under(Path(base), rel, last_flags=os.O_RDONLY | _NONBLOCK))
    except ConfinedError:
        return False
    return True


__all__ = ["ConfinedError", "exists_confined", "read_confined", "rel_ok"]
