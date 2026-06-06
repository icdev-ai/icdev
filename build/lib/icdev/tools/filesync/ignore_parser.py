#!/usr/bin/env python3
# CUI // SP-CTI
"""Ignore pattern parser — .syncignore file support (D-SYNC-4).

Supports gitignore-subset glob patterns via stdlib fnmatch.
Patterns are relative to sync job source root.
"""

from fnmatch import fnmatch
from pathlib import Path
from typing import List


def load_ignore_patterns(base_path: str, ignore_file: str = ".syncignore") -> List[str]:
    """Load ignore patterns from a .syncignore file.

    Args:
        base_path: Root directory of the sync job.
        ignore_file: Name of the ignore file (default: .syncignore).

    Returns:
        List of glob patterns. Empty list if file not found.
    """
    ignore_path = Path(base_path) / ignore_file
    if not ignore_path.is_file():
        return []
    patterns = []
    try:
        text = ignore_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    except OSError:
        return []
    return patterns


def should_ignore(relative_path: str, patterns: List[str]) -> bool:
    """Check if a relative path matches any ignore pattern.

    Args:
        relative_path: Forward-slash relative path (e.g., "src/temp/file.log").
        patterns: List of glob patterns from .syncignore.

    Returns:
        True if the path should be ignored.
    """
    if not patterns:
        return False

    # Normalize to forward slashes
    normalized = relative_path.replace("\\", "/")
    # Get the filename for basename-only patterns
    parts = normalized.split("/")
    filename = parts[-1] if parts else normalized

    for pattern in patterns:
        pat = pattern.replace("\\", "/")

        # Directory pattern (ends with /): match any path component
        if pat.endswith("/"):
            dir_pat = pat.rstrip("/")
            for part in parts[:-1]:
                if fnmatch(part, dir_pat):
                    return True
            continue

        # Pattern with slash: match against full relative path
        if "/" in pat:
            if fnmatch(normalized, pat):
                return True
            continue

        # Simple pattern: match against filename only
        if fnmatch(filename, pat):
            return True

        # Also try matching against each path component
        for part in parts:
            if fnmatch(part, pat):
                return True

    return False


def filter_files(file_list: List[dict], patterns: List[str]) -> List[dict]:
    """Filter a file list by ignore patterns.

    Args:
        file_list: List of dicts with 'relative_path' key.
        patterns: Ignore patterns from load_ignore_patterns().

    Returns:
        Filtered list with ignored files removed.
    """
    if not patterns:
        return file_list
    return [f for f in file_list if not should_ignore(f["relative_path"], patterns)]
