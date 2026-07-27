# CUI // SP-CTI
"""Shared, network-free Chromium-family browser locator (cdp-port-02).

The CDP transport launches the browser itself (``--remote-debugging-port``), so it
needs the browser **executable path** — not a driver binary, and not just a version
string. That discovery logic was stranded across two modules: Edge version detection
in ``tools/browser/driver_manager.py`` and Chrome executable/filesystem detection in
``tools/airgap/driver_vendor.py`` (an admin-only, network-capable module). This
consolidates *executable* discovery into one place, covering **Edge -> Chrome ->
Chromium on both Windows and Linux** — the estate the spike (cdp-00 §4.4) says varies
by site.

Strictly network-free: registry reads, filesystem probes, ``shutil.which``, and a
local ``--version`` call on an already-located binary. Version metadata reuses
driver_manager's existing detectors (registry ``BLBeacon`` on Windows) rather than
duplicating them — the locator's own contribution is the executable path.

Discovery is preference-ordered (Edge first: it is preinstalled on every Windows
workstation and implements CDP with APIs identical to Chrome's), and every result is
verified to exist on disk before being returned.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.browser_locator")

_IS_WIN = platform.system() == "Windows"
_IS_LINUX = platform.system() == "Linux"

# Preference order — Edge leads (§4.4: preinstalled on Windows, CDP-identical).
FAMILIES = ("edge", "chrome", "chromium")


@dataclass
class BrowserLocation:
    """A located Chromium-family browser executable."""

    family: str            # "edge" | "chrome" | "chromium"
    executable: str        # absolute path to the browser binary
    version: Optional[str]  # full version string if determinable, else None

    @property
    def major(self) -> Optional[str]:
        return self.version.split(".")[0] if self.version else None

    def to_dict(self) -> Dict[str, object]:
        d = asdict(self)
        d["major"] = self.major
        return d


# ── Per-family executable discovery (network-free) ────────────────────────────


def _win_app_paths(exe_name: str) -> Optional[Path]:
    """Look up an executable via the Windows 'App Paths' registry key — the most
    reliable, install-location-independent way to find Edge/Chrome on Windows."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return None
    subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, subkey) as k:
                val, _ = winreg.QueryValueEx(k, "")  # default value = full path
                if val and Path(val).exists():
                    return Path(val)
        except OSError:
            continue
    return None


def _first_existing(candidates: List[Path]) -> Optional[Path]:
    for c in candidates:
        try:
            if c and c.exists():
                return c
        except OSError:
            continue
    return None


def _locate_edge() -> Optional[Path]:
    if _IS_WIN:
        found = _win_app_paths("msedge.exe")
        if found:
            return found
        return _first_existing([
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ])
    which = shutil.which("microsoft-edge") or shutil.which("microsoft-edge-stable") or shutil.which("msedge")
    if which:
        return Path(which)
    return _first_existing([
        Path("/usr/bin/microsoft-edge"),
        Path("/usr/bin/microsoft-edge-stable"),
        Path("/opt/microsoft/msedge/msedge"),
    ])


def _locate_chrome() -> Optional[Path]:
    if _IS_WIN:
        found = _win_app_paths("chrome.exe")
        if found:
            return found
        local = os.environ.get("LOCALAPPDATA", "")
        return _first_existing([
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(local) / r"Google\Chrome\Application\chrome.exe" if local else Path(),
        ])
    which = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if which:
        return Path(which)
    return _first_existing([
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/opt/google/chrome/chrome"),
    ])


def _locate_chromium() -> Optional[Path]:
    if _IS_WIN:
        local = os.environ.get("LOCALAPPDATA", "")
        return _first_existing([
            Path(local) / r"Chromium\Application\chrome.exe" if local else Path(),
            Path(r"C:\Program Files\Chromium\Application\chrome.exe"),
        ])
    which = shutil.which("chromium") or shutil.which("chromium-browser")
    if which:
        return Path(which)
    return _first_existing([
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/snap/bin/chromium"),
    ])


_LOCATORS: Dict[str, Callable[[], Optional[Path]]] = {
    "edge": _locate_edge,
    "chrome": _locate_chrome,
    "chromium": _locate_chromium,
}


# ── Version resolution (reuses existing detectors; never downloads) ───────────


def _version_via_cli(executable: Path) -> Optional[str]:
    """Local ``--version`` call. Reliable on Linux; on Windows Chrome/Edge do not
    print a version to stdout, so this is a best-effort fallback only."""
    try:
        out = subprocess.check_output(
            [str(executable), "--version"], stderr=subprocess.DEVNULL, timeout=5
        ).decode("utf-8", errors="replace")
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:  # noqa: BLE001 - version is optional metadata
        pass
    return None


def _resolve_version(family: str, executable: Path) -> Optional[str]:
    # Prefer driver_manager's platform-correct detectors (registry BLBeacon on Win).
    try:
        from tools.browser.driver_manager import _detect_chrome_version, _detect_edge_version
        if family == "edge":
            v = _detect_edge_version()
            if v:
                return v
        elif family in ("chrome", "chromium"):
            v = _detect_chrome_version()
            if v:
                return v
    except Exception:  # noqa: BLE001 - detectors absent, fall back to CLI
        pass
    return _version_via_cli(executable)


# ── Public API ────────────────────────────────────────────────────────────────


def locate_all(prefer=FAMILIES) -> List[BrowserLocation]:
    """Return every located browser, in preference order."""
    results: List[BrowserLocation] = []
    for family in prefer:
        locator = _LOCATORS.get(family)
        if not locator:
            continue
        exe = locator()
        if exe:
            results.append(BrowserLocation(
                family=family,
                executable=str(exe),
                version=_resolve_version(family, exe),
            ))
    return results


def locate_browser(prefer=FAMILIES) -> Optional[BrowserLocation]:
    """Return the highest-preference located browser, or None.

    ``None`` is the loud-degradation signal (spike §4.6): the caller must raise a
    specific error naming the search families, not stall on a launch timeout.
    """
    for family in prefer:
        locator = _LOCATORS.get(family)
        if not locator:
            continue
        exe = locator()
        if exe:
            loc = BrowserLocation(family=family, executable=str(exe), version=_resolve_version(family, exe))
            logger.info("[browser_locator] located %s at %s (v%s)", family, exe, loc.version)
            return loc
    logger.warning("[browser_locator] no Chromium-family browser found; searched %s", ", ".join(prefer))
    return None


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Chromium-family browser locator (cdp-port-02)")
    parser.add_argument("--all", action="store_true", help="List every located browser")
    parser.add_argument("--prefer", default=",".join(FAMILIES),
                        help="Comma-separated preference order (default: edge,chrome,chromium)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    ns = parser.parse_args()

    prefer = tuple(f.strip() for f in ns.prefer.split(",") if f.strip())
    if ns.all:
        out = [loc.to_dict() for loc in locate_all(prefer)]
    else:
        loc = locate_browser(prefer)
        out = loc.to_dict() if loc else None

    print(json.dumps(out, indent=2))
    if out is None or (isinstance(out, list) and not out):
        sys.exit(1)
