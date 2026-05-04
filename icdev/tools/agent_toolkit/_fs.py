# CUI // SP-CTI
"""Filesystem primitives for the agent toolkit (OPT-67).

All functions are deterministic, utf-8 safe, and do not invoke any LLM.
They can be called directly by Python code OR composed into an agent
loop via tools.agent_toolkit.create_agent().

Design rules:
  - All text I/O uses encoding='utf-8', errors='replace' per ICDEV
    mandatory testing workflow feedback.
  - All paths are accepted as str or pathlib.Path; internally
    normalized to Path.
  - write_file uses atomic replace (write to .tmp, rename) so
    readers never see a half-written file.
  - edit_file does exact-match replace with optional count guard.
  - ls/glob/grep return lists of structured dicts, not bare strings,
    so the agent can reason about file metadata.
  - All functions raise FileNotFoundError / PermissionError as
    stdlib does — no silent exception swallowing. The composer
    wraps errors when invoking tools from an LLM context.

No network calls. No subprocess (see _shell.py for that). Air-gap safe.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional, Union

PathLike = Union[str, Path]


def _to_path(p: PathLike) -> Path:
    return p if isinstance(p, Path) else Path(p)


def read_file(
    path: PathLike,
    encoding: str = "utf-8",
    max_bytes: Optional[int] = None,
    offset: int = 0,
) -> str:
    """Read a text file and return its contents.

    Args:
        path: File path to read.
        encoding: Text encoding. Default utf-8 with 'replace' error mode.
        max_bytes: Optional size cap. Raises ValueError if file is larger.
            Use None for no cap.
        offset: Byte offset to start reading from. Default 0 (whole file).

    Returns:
        File contents as a string.

    Raises:
        FileNotFoundError: If path does not exist.
        IsADirectoryError: If path is a directory.
        ValueError: If max_bytes is set and the file exceeds it.
    """
    p = _to_path(path)
    if not p.exists():
        raise FileNotFoundError(f"read_file: {path} does not exist")
    if p.is_dir():
        raise IsADirectoryError(f"read_file: {path} is a directory")
    size = p.stat().st_size
    if max_bytes is not None and size > max_bytes:
        raise ValueError(
            f"read_file: {path} is {size} bytes, exceeds max_bytes={max_bytes}"
        )
    with open(p, "r", encoding=encoding, errors="replace") as f:
        if offset:
            f.seek(offset)
        return f.read()


def write_file(
    path: PathLike,
    content: str,
    encoding: str = "utf-8",
    create_dirs: bool = True,
) -> dict:
    """Write a string to a file atomically.

    Writes to a sibling temp file first, then renames. Readers never
    see a half-written file.

    Args:
        path: Destination path.
        content: Text to write.
        encoding: Text encoding. Default utf-8.
        create_dirs: If True (default), create parent directories as needed.

    Returns:
        Dict with keys: path (str), bytes_written (int), created (bool).

    Raises:
        FileExistsError: If the target is a directory.
        PermissionError: If the destination is not writable.
    """
    p = _to_path(path)
    if p.exists() and p.is_dir():
        raise FileExistsError(f"write_file: {path} is a directory")
    created = not p.exists()
    if create_dirs:
        p.parent.mkdir(parents=True, exist_ok=True)

    tmp = p.with_suffix(p.suffix + ".tmp")
    data = content.encode(encoding)
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, p)

    return {
        "path": str(p),
        "bytes_written": len(data),
        "created": created,
    }


def edit_file(
    path: PathLike,
    old_text: str,
    new_text: str,
    expected_count: Optional[int] = None,
) -> dict:
    """Exact-string replace in a file.

    Reads the file, replaces every occurrence of old_text with new_text,
    writes atomically. If expected_count is provided, raises if the
    actual replacement count differs — protects against silent
    over/under-matching.

    Args:
        path: File to edit.
        old_text: Exact substring to replace.
        new_text: Replacement text.
        expected_count: If set, raise ValueError when actual != expected.
            Use 1 for "replace the single occurrence"; use None for "any".

    Returns:
        Dict with path, replacements_made, original_bytes, new_bytes.

    Raises:
        FileNotFoundError: If path does not exist.
        ValueError: If old_text is empty OR expected_count mismatch.
    """
    if not old_text:
        raise ValueError("edit_file: old_text must be non-empty")

    p = _to_path(path)
    if not p.exists():
        raise FileNotFoundError(f"edit_file: {path} does not exist")

    original = read_file(p)
    count = original.count(old_text)
    if expected_count is not None and count != expected_count:
        raise ValueError(
            f"edit_file: expected {expected_count} replacement(s), "
            f"found {count} occurrence(s) of old_text in {path}"
        )
    if count == 0:
        return {
            "path": str(p),
            "replacements_made": 0,
            "original_bytes": len(original.encode("utf-8")),
            "new_bytes": len(original.encode("utf-8")),
        }

    updated = original.replace(old_text, new_text)
    write_file(p, updated)
    return {
        "path": str(p),
        "replacements_made": count,
        "original_bytes": len(original.encode("utf-8")),
        "new_bytes": len(updated.encode("utf-8")),
    }


def ls(
    path: PathLike = ".",
    recursive: bool = False,
    pattern: Optional[str] = None,
) -> List[dict]:
    """List directory contents.

    Args:
        path: Directory to list. Default current directory.
        recursive: If True, walk subdirectories.
        pattern: Optional glob pattern to filter (e.g., '*.py').

    Returns:
        List of dicts with: name, path (absolute), type ('file'|'dir'),
        size (bytes; 0 for dirs).

    Raises:
        NotADirectoryError: If path is not a directory.
        FileNotFoundError: If path does not exist.
    """
    p = _to_path(path)
    if not p.exists():
        raise FileNotFoundError(f"ls: {path} does not exist")
    if not p.is_dir():
        raise NotADirectoryError(f"ls: {path} is not a directory")

    out: List[dict] = []
    if recursive:
        iterator = p.rglob(pattern) if pattern else p.rglob("*")
    else:
        iterator = p.glob(pattern) if pattern else p.iterdir()

    for entry in iterator:
        try:
            is_dir = entry.is_dir()
            out.append({
                "name": entry.name,
                "path": str(entry),
                "type": "dir" if is_dir else "file",
                "size": 0 if is_dir else entry.stat().st_size,
            })
        except OSError:
            continue  # symlink loops, permission errors — skip
    return sorted(out, key=lambda e: (e["type"] == "file", e["name"]))


def glob(pattern: str, root: Optional[PathLike] = None) -> List[str]:
    """Glob files matching a pattern under root.

    Args:
        pattern: Glob pattern (e.g., '**/*.py', 'tools/*/blueprint.py').
        root: Root directory. Default current directory.

    Returns:
        Sorted list of matching path strings.
    """
    r = _to_path(root) if root else Path(".")
    return sorted(str(p) for p in r.glob(pattern))


def grep(
    pattern: str,
    paths: Optional[List[PathLike]] = None,
    regex: bool = True,
    case_insensitive: bool = False,
    max_matches: int = 250,
    include_line_numbers: bool = True,
) -> List[dict]:
    """Search for a pattern across one or more files.

    Uses Python re — no subprocess, no ripgrep dependency. Suitable for
    air-gap + deterministic behavior.

    Args:
        pattern: Regex (if regex=True) or literal substring.
        paths: List of file or directory paths to search. If a directory
            is passed, searches all files recursively. Default: current
            directory.
        regex: If True (default), treat pattern as regex. If False, literal
            substring match.
        case_insensitive: Case-insensitive match.
        max_matches: Cap on total results. Default 250.
        include_line_numbers: Include line_number in output dicts.

    Returns:
        List of dicts: {path, line_number, line_text (stripped)}.

    Raises:
        re.error: If regex=True and pattern is malformed.
    """
    if paths is None:
        paths = [Path(".")]
    paths = [_to_path(p) for p in paths]

    flags = re.IGNORECASE if case_insensitive else 0
    if regex:
        rx = re.compile(pattern, flags)
    else:
        rx = re.compile(re.escape(pattern), flags)

    out: List[dict] = []
    files: List[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(p.rglob("*") if p.is_dir() else [])
        elif p.is_file():
            files.append(p)

    for f in files:
        if not f.is_file():
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if rx.search(line):
                        entry: dict = {
                            "path": str(f),
                            "line_text": line.rstrip("\n"),
                        }
                        if include_line_numbers:
                            entry["line_number"] = i
                        out.append(entry)
                        if len(out) >= max_matches:
                            return out
        except (OSError, UnicodeDecodeError):
            continue  # skip binary files + permission errors
    return out
