#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Cross-platform ICDEV™ services entry point.

Replaces start_daemon.bat (Windows) and start_daemon.ps1 (PowerShell).
Works identically on Windows, Linux, and macOS / inside containers.

Usage:
    python tools/genesis/launch.py [--no-ollama] [--dry-run]

    # Windows thin wrappers delegate here:
    start_daemon.bat
    start_daemon.ps1
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
os.chdir(ROOT)

# Ensure project is importable
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.compat.platform_utils import IS_WINDOWS


def _ollama_alive(timeout: float = 3.0) -> bool:
    """Return True if the local Ollama API is reachable."""
    import urllib.request
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):  # nosec B310
            return True
    except Exception:
        return False


def _start_ollama() -> None:
    """Best-effort: try to launch Ollama if not already running."""
    if _ollama_alive():
        return

    ollama_bin: str | None = shutil.which("ollama")

    if not ollama_bin and IS_WINDOWS:
        # Common Windows install location
        candidate = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama app.exe"
        if candidate.exists():
            ollama_bin = str(candidate)

    if not ollama_bin:
        print("[launch] Ollama not found on PATH — skipping auto-start (Genesis will run with degraded LLM)")
        return

    print(f"[launch] Starting Ollama: {ollama_bin}")
    creationflags = 0x00000008 if IS_WINDOWS else 0  # DETACHED_PROCESS on Windows
    try:
        subprocess.Popen([ollama_bin], creationflags=creationflags)
    except Exception as exc:
        print(f"[launch] Ollama auto-start failed: {exc}")
        return

    # Wait up to 30s for Ollama to become ready
    for _ in range(6):
        time.sleep(5)
        if _ollama_alive():
            print("[launch] Ollama is ready")
            return
    print("[launch] WARNING: Ollama did not respond within 30s — continuing anyway")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ICDEV™ cross-platform services launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--no-ollama", action="store_true",
        help="Skip Ollama detection and auto-start",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would run without starting anything",
    )
    args = parser.parse_args()

    if args.dry_run:
        print(f"[launch] Project root : {ROOT}")
        print(f"[launch] Python       : {sys.executable}")
        print(f"[launch] Platform     : {sys.platform}")
        print("[launch] Would start  : Dashboard, Genesis Daemon, Kanban Scheduler")
        print("[launch] Ollama check : skipped (--dry-run)")
        return

    if not args.no_ollama:
        _start_ollama()

    # Delegate to the existing launcher.main() which manages all subprocesses
    from tools.genesis import launcher
    launcher.main()


if __name__ == "__main__":
    main()
