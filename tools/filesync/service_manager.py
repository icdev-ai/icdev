#!/usr/bin/env python3
# CUI // SP-CTI
"""FileSync Service Manager -- persist daemon across Windows reboots.

Uses Windows Task Scheduler (schtasks) to register a startup task that
launches the FileSync daemon automatically when the user logs in.

On non-Windows platforms, generates a systemd unit file or launchd plist.

Architecture: D-SYNC-9 (daemon mode persistence).
"""

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TASK_NAME = "ICDEV™FileSyncDaemon"
SYSTEMD_UNIT = "icdev-filesync.service"


def _find_python() -> str:
    """Find the Python executable path."""
    return sys.executable


def _sync_engine_path() -> str:
    """Path to sync_engine.py."""
    return str(BASE_DIR / "tools" / "filesync" / "sync_engine.py")


def _get_startup_folder() -> Path:
    """Get the Windows user Startup folder path."""
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


SHORTCUT_NAME = "ICDEV™FileSyncDaemon.vbs"


# =========================================================================
# WINDOWS — Startup folder VBS launcher (no admin required)
# =========================================================================


def _install_windows() -> Dict:
    """Place a VBS launcher in the Windows Startup folder.

    Uses a .vbs script that launches pythonw.exe hidden (no console window).
    The Startup folder runs scripts automatically on user logon -- no admin needed.
    """
    python_exe = _find_python()
    engine_path = _sync_engine_path()

    # Use pythonw.exe if available (no console window), else python.exe
    pythonw = python_exe.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = python_exe

    startup_dir = _get_startup_folder()
    if not startup_dir.exists():
        return {"status": "error", "error": f"Startup folder not found: {startup_dir}"}

    vbs_path = startup_dir / SHORTCUT_NAME

    # VBS script that launches the daemon hidden (no console flash)
    # WScript.Shell.Run with 0 = hidden window, False = don't wait
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{BASE_DIR}"
WshShell.Run """{pythonw}"" ""{engine_path}"" --daemon", 0, False
'''

    vbs_path.write_text(vbs_content, encoding="utf-8", newline="")

    return {
        "status": "installed",
        "method": "startup_folder",
        "vbs_path": str(vbs_path),
        "python": pythonw,
        "script": engine_path,
        "working_directory": str(BASE_DIR),
        "message": f"Startup script installed at: {vbs_path}\nDaemon will auto-start on next logon (no admin required).",
    }


def _uninstall_windows() -> Dict:
    """Remove the VBS launcher from the Startup folder."""
    startup_dir = _get_startup_folder()
    vbs_path = startup_dir / SHORTCUT_NAME

    if not vbs_path.exists():
        return {"status": "not_found", "message": "FileSync startup script not found. Nothing to remove."}

    vbs_path.unlink()
    return {
        "status": "uninstalled",
        "vbs_path": str(vbs_path),
        "message": "Startup script removed. Daemon will no longer auto-start on logon.",
    }


def _status_windows() -> Dict:
    """Check if the startup VBS script exists."""
    startup_dir = _get_startup_folder()
    vbs_path = startup_dir / SHORTCUT_NAME

    if not vbs_path.exists():
        return {
            "status": "not_installed",
            "message": "FileSync daemon is NOT registered for auto-start.",
            "startup_folder": str(startup_dir),
        }

    # Check if daemon process is currently running
    daemon_running = False
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter "
                "\"Name='pythonw.exe' OR Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match 'sync_engine.*daemon' } | "
                "Select-Object -ExpandProperty ProcessId",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        pids = [ln.strip() for ln in result.stdout.strip().split("\n") if ln.strip().isdigit()]
        daemon_running = len(pids) > 0
    except Exception:
        pass

    return {
        "status": "installed",
        "vbs_path": str(vbs_path),
        "daemon_running": daemon_running,
        "message": f"FileSync daemon IS registered for auto-start on logon. "
        f"Daemon currently {'RUNNING' if daemon_running else 'NOT running'}.",
    }


# =========================================================================
# LINUX — systemd user unit
# =========================================================================


def _install_linux() -> Dict:
    """Generate and install a systemd user service."""
    python_exe = _find_python()
    engine_path = _sync_engine_path()

    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / SYSTEMD_UNIT

    unit_content = f"""[Unit]
Description=ICDEV™ FileSync Daemon
After=default.target

[Service]
Type=simple
ExecStart={python_exe} {engine_path} --daemon
WorkingDirectory={BASE_DIR}
Restart=on-failure
RestartSec=30
Environment=PYTHONIOENCODING=utf-8

[Install]
WantedBy=default.target
"""
    unit_path.write_text(unit_content, encoding="utf-8", newline="")

    # Reload and enable
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    result = subprocess.run(
        ["systemctl", "--user", "enable", SYSTEMD_UNIT],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {"status": "error", "error": result.stderr.strip()}

    # Start it now
    subprocess.run(
        ["systemctl", "--user", "start", SYSTEMD_UNIT],
        capture_output=True,
        text=True,
    )

    return {
        "status": "installed",
        "unit": str(unit_path),
        "message": f"Systemd user service '{SYSTEMD_UNIT}' installed and enabled.",
    }


def _uninstall_linux() -> Dict:
    """Remove the systemd user service."""
    subprocess.run(["systemctl", "--user", "stop", SYSTEMD_UNIT], capture_output=True)
    subprocess.run(["systemctl", "--user", "disable", SYSTEMD_UNIT], capture_output=True)

    unit_path = Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT
    if unit_path.exists():
        unit_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)

    return {"status": "uninstalled", "message": f"Systemd service '{SYSTEMD_UNIT}' removed."}


def _status_linux() -> Dict:
    """Check systemd user service status."""
    result = subprocess.run(
        ["systemctl", "--user", "is-active", SYSTEMD_UNIT],
        capture_output=True,
        text=True,
    )
    active = result.stdout.strip()

    result2 = subprocess.run(
        ["systemctl", "--user", "is-enabled", SYSTEMD_UNIT],
        capture_output=True,
        text=True,
    )
    enabled = result2.stdout.strip()

    installed = enabled != "not-found"
    return {
        "status": "installed" if installed else "not_installed",
        "active": active,
        "enabled": enabled,
        "message": f"Service is {active}, {enabled}."
        if installed
        else "FileSync daemon is NOT registered for auto-start.",
    }


# =========================================================================
# MACOS — launchd plist
# =========================================================================


def _install_macos() -> Dict:
    """Generate and install a launchd user agent plist."""
    python_exe = _find_python()
    engine_path = _sync_engine_path()

    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.icdev.filesync.plist"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.icdev.filesync</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_exe}</string>
        <string>{engine_path}</string>
        <string>--daemon</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{BASE_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{Path.home() / ".icdev" / "filesync-daemon.log"}</string>
    <key>StandardErrorPath</key>
    <string>{Path.home() / ".icdev" / "filesync-daemon.err"}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONIOENCODING</key>
        <string>utf-8</string>
    </dict>
</dict>
</plist>
"""
    plist_path.write_text(plist_content, encoding="utf-8", newline="")
    subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)

    return {
        "status": "installed",
        "plist": str(plist_path),
        "message": "Launchd agent 'com.icdev.filesync' installed. Daemon will auto-start on login.",
    }


def _uninstall_macos() -> Dict:
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.icdev.filesync.plist"
    if plist_path.exists():
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        plist_path.unlink()
    return {"status": "uninstalled", "message": "Launchd agent removed."}


def _status_macos() -> Dict:
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.icdev.filesync.plist"
    if not plist_path.exists():
        return {"status": "not_installed", "message": "FileSync daemon is NOT registered for auto-start."}
    result = subprocess.run(
        ["launchctl", "list", "com.icdev.filesync"],
        capture_output=True,
        text=True,
    )
    running = result.returncode == 0
    return {
        "status": "installed",
        "running": running,
        "message": f"Launchd agent installed, {'running' if running else 'not running'}.",
    }


# =========================================================================
# DISPATCH
# =========================================================================


def install_service() -> Dict:
    """Install FileSync daemon as a persistent OS service."""
    system = platform.system()
    if system == "Windows":
        return _install_windows()
    elif system == "Linux":
        return _install_linux()
    elif system == "Darwin":
        return _install_macos()
    return {"status": "error", "error": f"Unsupported platform: {system}"}


def uninstall_service() -> Dict:
    """Remove FileSync daemon from OS startup."""
    system = platform.system()
    if system == "Windows":
        return _uninstall_windows()
    elif system == "Linux":
        return _uninstall_linux()
    elif system == "Darwin":
        return _uninstall_macos()
    return {"status": "error", "error": f"Unsupported platform: {system}"}


def service_status() -> Dict:
    """Check if daemon is registered as an OS service."""
    system = platform.system()
    if system == "Windows":
        return _status_windows()
    elif system == "Linux":
        return _status_linux()
    elif system == "Darwin":
        return _status_macos()
    return {"status": "error", "error": f"Unsupported platform: {system}"}
