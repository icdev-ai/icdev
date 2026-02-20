#!/usr/bin/env python3
# CUI // SP-CTI
"""Cross-platform compatibility utilities for ICDEV.

Centralizes OS detection and platform-specific behavior (D145).
Uses only Python stdlib (air-gap safe).

Usage:
    from tools.compat.platform_utils import (
        IS_WINDOWS, IS_MACOS, IS_LINUX, PLATFORM_NAME,
        get_temp_dir, get_home_dir, get_npx_cmd,
        normalize_path, get_data_dir, get_config_dir,
    )
"""

import os
import platform
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Platform detection constants
# ---------------------------------------------------------------------------
PLATFORM_NAME: str = platform.system()   # "Windows", "Darwin", "Linux"
IS_WINDOWS: bool = PLATFORM_NAME == "Windows"
IS_MACOS: bool = PLATFORM_NAME == "Darwin"
IS_LINUX: bool = PLATFORM_NAME == "Linux"


# ---------------------------------------------------------------------------
# Directory utilities
# ---------------------------------------------------------------------------
def get_temp_dir() -> Path:
    """Return the platform temp directory (never hardcoded /tmp)."""
    return Path(tempfile.gettempdir())


def get_home_dir() -> Path:
    """Return user home directory cross-platform."""
    return Path.home()


def get_project_root() -> Path:
    """Return ICDEV project root."""
    return Path(__file__).resolve().parent.parent.parent


def get_data_dir() -> Path:
    """Return ICDEV data directory (relative to project root)."""
    return get_project_root() / "data"


def get_config_dir() -> Path:
    """Return platform-appropriate config directory for user-level config.

    Windows: %APPDATA%/icdev
    macOS:   ~/Library/Application Support/icdev
    Linux:   ~/.config/icdev (XDG_CONFIG_HOME respected)
    """
    if IS_WINDOWS:
        base = Path(os.environ.get(
            "APPDATA", str(get_home_dir() / "AppData" / "Roaming")
        ))
    elif IS_MACOS:
        base = get_home_dir() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get(
            "XDG_CONFIG_HOME", str(get_home_dir() / ".config")
        ))
    return base / "icdev"


# ---------------------------------------------------------------------------
# Command utilities
# ---------------------------------------------------------------------------
def get_npx_cmd() -> str:
    """Return the correct npx command for the current platform.

    Windows requires npx.cmd; Unix uses npx directly.
    """
    return "npx.cmd" if IS_WINDOWS else "npx"


def get_python_cmd() -> str:
    """Return the Python executable for subprocess invocations."""
    return sys.executable


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------
def normalize_path(path_str: str) -> Path:
    """Normalize a path string to a pathlib.Path.

    Handles Windows backslash paths, Unix paths, and mixed inputs.
    Resolves to absolute path if possible.
    """
    p = Path(path_str)
    try:
        return p.resolve()
    except OSError:
        return p


# ---------------------------------------------------------------------------
# Console utilities
# ---------------------------------------------------------------------------
def ensure_utf8_console():
    """Ensure stdout supports UTF-8 on Windows.

    Safe to call on any platform (no-op on Unix).
    """
    if not IS_WINDOWS:
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        import io as _io
        sys.stdout = _io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace"
        )
