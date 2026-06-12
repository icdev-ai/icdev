#!/usr/bin/env python3
# CUI // SP-CTI
# ruff: noqa: E501
"""Cross-platform compatibility utilities for ICDEV™.

Centralizes OS detection and platform-specific behavior (D145).
Uses only Python stdlib (air-gap safe).

Usage:
    from tools.compat.platform_utils import (
        IS_WINDOWS, IS_MACOS, IS_LINUX, PLATFORM_NAME,
        get_temp_dir, get_home_dir, get_npx_cmd,
        normalize_path, get_data_dir, get_config_dir,
    )
"""

from __future__ import annotations

import ctypes
import os
import platform
import signal
import sys
import tempfile
from pathlib import Path
from typing import List


# ---------------------------------------------------------------------------
# Platform detection constants
# ---------------------------------------------------------------------------
PLATFORM_NAME: str = platform.system()  # "Windows", "Darwin", "Linux"
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
    """Return ICDEV™ project root."""
    return Path(__file__).resolve().parent.parent.parent


def get_data_dir() -> Path:
    """Return ICDEV™ data directory (relative to project root)."""
    return get_project_root() / "data"


def get_config_dir() -> Path:
    """Return platform-appropriate config directory for user-level config.

    Windows: %APPDATA%/icdev
    macOS:   ~/Library/Application Support/icdev
    Linux:   ~/.config/icdev (XDG_CONFIG_HOME respected)
    """
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", str(get_home_dir() / "AppData" / "Roaming")))
    elif IS_MACOS:
        base = get_home_dir() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(get_home_dir() / ".config")))
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
# Process utilities (cross-platform — psutil preferred, stdlib fallback)
# ---------------------------------------------------------------------------
def pid_exists(pid: int) -> bool:
    """Return True if a process with *pid* is alive — cross-platform.

    Uses psutil when available; falls back to os.kill on POSIX and
    ctypes.OpenProcess on Windows.
    """
    try:
        import psutil as _ps  # optional dep
        return _ps.pid_exists(pid)
    except ImportError:
        pass
    if IS_WINDOWS:
        SYNCHRONIZE = 0x00100000
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)  # type: ignore[attr-defined]
        if h:
            ctypes.windll.kernel32.CloseHandle(h)  # type: ignore[attr-defined]
            return True
        return False
    # POSIX: signal 0 = existence check (no signal sent)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it (different user)
        return True
    except OSError:
        return False


def kill_process(pid: int, force: bool = True) -> bool:
    """Terminate a process by PID — cross-platform.

    Returns True if the signal was sent, False if the process was not found.
    Uses psutil when available; falls back to os.kill on POSIX and
    ctypes.TerminateProcess on Windows.
    """
    try:
        import psutil as _ps  # optional dep
        try:
            p = _ps.Process(pid)
            if force:
                p.kill()
            else:
                p.terminate()
            return True
        except _ps.NoSuchProcess:
            return False
    except ImportError:
        pass
    if IS_WINDOWS:
        PROCESS_TERMINATE = 0x0001
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)  # type: ignore[attr-defined]
        if not h:
            return False
        ctypes.windll.kernel32.TerminateProcess(h, 1)  # type: ignore[attr-defined]
        ctypes.windll.kernel32.CloseHandle(h)  # type: ignore[attr-defined]
        return True
    # POSIX
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def find_pids_by_cmdline(fragment: str) -> List[int]:
    """Return PIDs whose command-line contains *fragment* — cross-platform.

    Uses psutil when available; falls back to pgrep on POSIX or wmic on Windows.
    """
    try:
        import psutil as _ps  # optional dep
        result = []
        for proc in _ps.process_iter(["pid", "cmdline"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if fragment in cmdline:
                    result.append(proc.info["pid"])
            except (_ps.NoSuchProcess, _ps.AccessDenied):
                pass
        return result
    except ImportError:
        pass
    if IS_WINDOWS:
        import subprocess
        try:
            out = subprocess.run(
                ["wmic", "process", "get", "ProcessId,CommandLine", "/format:csv"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            pids = []
            for line in out.splitlines():
                if fragment in line:
                    parts = line.rsplit(",", 1)
                    if len(parts) == 2 and parts[-1].strip().isdigit():
                        pids.append(int(parts[-1].strip()))
            return pids
        except Exception:
            return []
    # POSIX: pgrep -f
    import subprocess
    try:
        out = subprocess.run(
            ["pgrep", "-f", fragment],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return [int(p) for p in out.split() if p.isdigit()]
    except FileNotFoundError:
        return []


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

        sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
