#!/usr/bin/env python3
# CUI // SP-CTI
"""Admin tool — download and vendor browser driver binaries.

Downloads msedgedriver (and optionally chromedriver) into the project's
``vendor/drivers/`` tree with SHA256 verification.  Intended for one-time
admin use so that runtime E2E tests never need a network connection.

Vendor tree layout::

    vendor/drivers/
      msedgedriver/
        {major}/
          msedgedriver.exe        (Windows)
          msedgedriver             (Linux/macOS)
          SHA256SUM                (hex digest of the binary)
      chromedriver/
        {major}/
          chromedriver.exe
          chromedriver
          SHA256SUM

Usage::

    # Download msedgedriver matching installed Edge version
    python tools/airgap/driver_vendor.py --fetch-edge

    # Download a specific Edge driver version
    python tools/airgap/driver_vendor.py --fetch-edge --version 134.0.3124.57

    # Download chromedriver for a specific major
    python tools/airgap/driver_vendor.py --fetch-chrome --major 134

    # Verify all vendored drivers
    python tools/airgap/driver_vendor.py --verify

    # List vendored drivers
    python tools/airgap/driver_vendor.py --list --json

Note
----
This tool REQUIRES network access and is intended for admin/CI use only.
Runtime E2E tests must use ``tools/browser/driver_manager.py`` which never
touches the network.
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.airgap.driver_vendor")

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent.parent
VENDOR_DIR = BASE_DIR / "vendor" / "drivers"
MSEDGEDRIVER_VENDOR_DIR = VENDOR_DIR / "msedgedriver"
CHROMEDRIVER_VENDOR_DIR = VENDOR_DIR / "chromedriver"

_IS_WIN = platform.system() == "Windows"
_SYSTEM = platform.system().lower()   # "windows" | "linux" | "darwin"
_MACHINE = platform.machine().lower() # "amd64" | "x86_64" | "arm64" etc.

# ── Download URL templates ────────────────────────────────────────────────────

# Microsoft publishes msedgedriver at a well-known URL keyed by full version.
# https://msedgewebdriverstorage.blob.core.windows.net/edgewebdriver/{version}/edgedriver_{platform}.zip
_EDGE_CDN = "https://msedgewebdriverstorage.blob.core.windows.net/edgewebdriver"

# Chrome for Testing (CfT) JSON API
_CHROME_CFT_KNOWN = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"

_EDGE_PLATFORM_MAP: Dict[str, str] = {
    "windows-amd64": "win64",
    "windows-x86":   "win32",
    "linux-x86_64":  "linux64",
    "linux-amd64":   "linux64",
    "darwin-arm64":  "mac-arm64",
    "darwin-x86_64": "mac64",
}


def _edge_platform_key() -> str:
    sys_key = f"{_SYSTEM}-{_MACHINE}"
    if sys_key in _EDGE_PLATFORM_MAP:
        return _EDGE_PLATFORM_MAP[sys_key]
    # Reasonable defaults
    if _IS_WIN:
        return "win64"
    if _SYSTEM == "linux":
        return "linux64"
    return "mac64"


# ── SHA256 helpers ────────────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA256 of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_checksum(binary_path: Path) -> str:
    digest = _sha256_file(binary_path)
    checksum_path = binary_path.parent / "SHA256SUM"
    checksum_path.write_text(f"{digest}  {binary_path.name}\n", encoding="utf-8", newline="")
    return digest


def _verify_checksum(binary_path: Path) -> bool:
    """Return True if the stored SHA256SUM matches the binary on disk."""
    checksum_path = binary_path.parent / "SHA256SUM"
    if not checksum_path.exists():
        logger.warning("No SHA256SUM for %s", binary_path)
        return False
    stored_line = checksum_path.read_text(encoding="utf-8").strip()
    stored_hex = stored_line.split()[0]
    actual = _sha256_file(binary_path)
    ok = actual == stored_hex
    if not ok:
        logger.error("SHA256 MISMATCH for %s: stored=%s actual=%s", binary_path, stored_hex, actual)
    return ok


# ── Edge version detection (mirrors driver_manager logic) ────────────────────

def _detect_installed_edge_version() -> Optional[str]:
    """Return the installed Edge full version string or None."""
    if _IS_WIN:
        try:
            import winreg
            keys = [
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Edge\BLBeacon"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Edge\BLBeacon"),
            ]
            for hive, subkey in keys:
                try:
                    with winreg.OpenKey(hive, subkey) as k:
                        val, _ = winreg.QueryValueEx(k, "version")
                        if val:
                            return str(val)
                except OSError:
                    continue
        except ImportError:
            pass

        # File-system fallback
        for app_dir in [
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application"),
            Path(r"C:\Program Files\Microsoft\Edge\Application"),
        ]:
            if app_dir.exists():
                for subdir in sorted(app_dir.iterdir(), reverse=True):
                    if subdir.is_dir() and re.match(r"\d+\.\d+\.\d+\.\d+", subdir.name):
                        return subdir.name

    for exe in ("microsoft-edge", "microsoft-edge-stable", "msedge"):
        path = shutil.which(exe)
        if path:
            try:
                out = subprocess.check_output(
                    [path, "--version"], stderr=subprocess.DEVNULL, timeout=5
                ).decode("utf-8", errors="replace")
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
                if m:
                    return m.group(1)
            except Exception:
                pass
    return None


# ── Download helpers ──────────────────────────────────────────────────────────

def _download(url: str, dest: Path, label: str = "") -> None:
    """Download *url* to *dest*, showing progress."""
    label = label or url.split("/")[-1]
    logger.info("Downloading %s → %s", url, dest)
    print(f"  Downloading {label} …", flush=True)

    req = urllib.request.Request(url, headers={"User-Agent": "icdev-driver-vendor/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        received = 0
        with open(dest, "wb") as out_f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out_f.write(chunk)
                received += len(chunk)
                if total:
                    pct = received * 100 // total
                    print(f"\r    {pct:3d}% ({received:,} / {total:,} bytes)", end="", flush=True)
    print()


def _extract_driver_from_zip(
    zip_path: Path, dest_dir: Path, driver_name: str
) -> Optional[Path]:
    """Extract *driver_name* (e.g. ``msedgedriver.exe``) from *zip_path* to *dest_dir*."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            # e.g. "edgedriver_win64/msedgedriver.exe"
            if member.endswith(driver_name) and not member.endswith("/"):
                data = zf.read(member)
                out_path = dest_dir / driver_name
                out_path.write_bytes(data)
                # Make executable on Unix
                if not _IS_WIN:
                    out_path.chmod(0o755)
                logger.info("Extracted %s → %s", member, out_path)
                return out_path
    logger.error("'%s' not found in zip %s", driver_name, zip_path)
    return None


# ── Public fetch functions ────────────────────────────────────────────────────

def fetch_msedgedriver(version: Optional[str] = None) -> Dict[str, Any]:
    """Download and vendor msedgedriver.

    Parameters
    ----------
    version:
        Full version string like ``"134.0.3124.57"``.  If None, detects the
        installed Edge version automatically.

    Returns
    -------
    dict with keys: version, major, driver_path, sha256, status
    """
    if version is None:
        version = _detect_installed_edge_version()
        if version is None:
            return {"status": "error", "error": "Could not detect installed Edge version. Use --version."}

    major = version.split(".")[0]
    platform_key = _edge_platform_key()
    driver_name = f"msedgedriver{'.exe' if _IS_WIN else ''}"
    zip_name = f"edgedriver_{platform_key}.zip"
    url = f"{_EDGE_CDN}/{version}/{zip_name}"

    dest_dir = MSEDGEDRIVER_VENDOR_DIR / major
    dest_dir.mkdir(parents=True, exist_ok=True)
    driver_dest = dest_dir / driver_name

    if driver_dest.exists():
        print(f"  Already vendored: {driver_dest}")
        digest = _write_checksum(driver_dest)
        return {
            "status": "already_vendored",
            "version": version,
            "major": major,
            "driver_path": str(driver_dest),
            "sha256": digest,
        }

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / zip_name
        try:
            _download(url, zip_path, label=zip_name)
        except Exception as exc:
            return {"status": "error", "error": f"Download failed: {exc}", "url": url}

        extracted = _extract_driver_from_zip(zip_path, dest_dir, driver_name)
        if extracted is None:
            return {"status": "error", "error": "Driver binary not found in zip", "url": url}

    digest = _write_checksum(extracted)
    print(f"  Vendored: {extracted}")
    print(f"  SHA256:   {digest}")
    return {
        "status": "ok",
        "version": version,
        "major": major,
        "driver_path": str(extracted),
        "sha256": digest,
    }


def fetch_chromedriver(major: Optional[str] = None) -> Dict[str, Any]:
    """Download and vendor chromedriver via Chrome for Testing JSON API.

    Parameters
    ----------
    major:
        Major version string e.g. ``"134"``.  If None, attempts to detect the
        installed Chrome version.
    """
    if major is None:
        major = _detect_chrome_major()
        if major is None:
            return {
                "status": "error",
                "error": "Could not detect Chrome version. Use --major.",
            }

    driver_name = f"chromedriver{'.exe' if _IS_WIN else ''}"

    # Determine CfT platform key
    if _IS_WIN:
        cft_platform = "win64"
    elif _SYSTEM == "linux":
        cft_platform = "linux64"
    elif _SYSTEM == "darwin":
        cft_platform = "mac-arm64" if _MACHINE == "arm64" else "mac-x64"
    else:
        cft_platform = "linux64"

    # Fetch JSON index
    print("  Fetching Chrome for Testing version index …", flush=True)
    try:
        with urllib.request.urlopen(_CHROME_CFT_KNOWN, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"status": "error", "error": f"Failed to fetch CfT index: {exc}"}

    # Find best matching version for major
    candidates = [
        v for v in data.get("versions", [])
        if v.get("version", "").split(".")[0] == major
    ]
    if not candidates:
        return {"status": "error", "error": f"No CfT versions found for Chrome major {major}"}

    # Pick the latest patch
    best = sorted(
        candidates,
        key=lambda v: [int(x) for x in v["version"].split(".")],
        reverse=True,
    )[0]
    version = best["version"]

    # Find download URL for chromedriver
    url: Optional[str] = None
    for dl in best.get("downloads", {}).get("chromedriver", []):
        if dl.get("platform") == cft_platform:
            url = dl["url"]
            break
    if url is None:
        return {
            "status": "error",
            "error": f"No CfT download URL for platform '{cft_platform}' at version {version}",
        }

    dest_dir = CHROMEDRIVER_VENDOR_DIR / major
    dest_dir.mkdir(parents=True, exist_ok=True)
    driver_dest = dest_dir / driver_name

    if driver_dest.exists():
        print(f"  Already vendored: {driver_dest}")
        digest = _write_checksum(driver_dest)
        return {
            "status": "already_vendored",
            "version": version,
            "major": major,
            "driver_path": str(driver_dest),
            "sha256": digest,
        }

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "chromedriver.zip"
        try:
            _download(url, zip_path, label=f"chromedriver {version} ({cft_platform})")
        except Exception as exc:
            return {"status": "error", "error": f"Download failed: {exc}"}

        extracted = _extract_driver_from_zip(zip_path, dest_dir, driver_name)
        if extracted is None:
            # Some CfT zips nest the driver deeper; try any chromedriver entry
            with zipfile.ZipFile(zip_path) as zf:
                for member in zf.namelist():
                    if "chromedriver" in member.lower() and not member.endswith("/"):
                        data_bytes = zf.read(member)
                        extracted = dest_dir / driver_name
                        extracted.write_bytes(data_bytes)
                        if not _IS_WIN:
                            extracted.chmod(0o755)
                        break
        if extracted is None or not extracted.exists():
            return {"status": "error", "error": "chromedriver binary not found in zip"}

    digest = _write_checksum(extracted)
    print(f"  Vendored: {extracted}")
    print(f"  SHA256:   {digest}")
    return {
        "status": "ok",
        "version": version,
        "major": major,
        "driver_path": str(extracted),
        "sha256": digest,
    }


def _detect_chrome_major() -> Optional[str]:
    """Detect installed Google Chrome major version."""
    for exe in ("google-chrome", "google-chrome-stable", "chrome", "chromium-browser", "chromium"):
        path = shutil.which(exe)
        if path:
            try:
                out = subprocess.check_output(
                    [path, "--version"], stderr=subprocess.DEVNULL, timeout=5
                ).decode("utf-8", errors="replace")
                m = re.search(r"(\d+)\.", out)
                if m:
                    return m.group(1)
            except Exception:
                pass

    # Linux: explicit filesystem paths for distros that don't put Chrome in PATH
    if _SYSTEM == "linux":
        for chrome_path in [
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
            Path("/snap/bin/chromium"),
        ]:
            if chrome_path.exists():
                try:
                    out = subprocess.check_output(
                        [str(chrome_path), "--version"], stderr=subprocess.DEVNULL, timeout=5
                    ).decode("utf-8", errors="replace")
                    m = re.search(r"(\d+)\.", out)
                    if m:
                        return m.group(1)
                except Exception:
                    pass

    if _IS_WIN:
        for chrome_path in [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(os.environ.get("LOCALAPPDATA", "")) / r"Google\Chrome\Application\chrome.exe",
        ]:
            if chrome_path.exists():
                try:
                    import subprocess as sp
                    result = sp.check_output(
                        ["powershell", "-Command",
                         f"(Get-Item '{chrome_path}').VersionInfo.ProductVersion"],
                        stderr=sp.DEVNULL, timeout=10
                    ).decode("utf-8", errors="replace").strip()
                    if result:
                        return result.split(".")[0]
                except Exception:
                    pass
    return None


# ── Verify and list ───────────────────────────────────────────────────────────

def verify_all() -> List[Dict[str, Any]]:
    """Verify SHA256 checksums for all vendored drivers."""
    results = []
    for driver_type_dir in [MSEDGEDRIVER_VENDOR_DIR, CHROMEDRIVER_VENDOR_DIR]:
        if not driver_type_dir.exists():
            continue
        driver_type = driver_type_dir.name
        for major_dir in sorted(driver_type_dir.iterdir()):
            if not major_dir.is_dir():
                continue
            for ext in ("", ".exe"):
                exe_name = f"{driver_type.rstrip('driver')}{ext}" if False else None
            # Try both executable names
            for exe_name in (
                f"msedgedriver{'.exe' if _IS_WIN else ''}",
                f"chromedriver{'.exe' if _IS_WIN else ''}",
            ):
                candidate = major_dir / exe_name
                if candidate.exists():
                    ok = _verify_checksum(candidate)
                    results.append({
                        "driver": driver_type,
                        "major": major_dir.name,
                        "path": str(candidate),
                        "sha256_ok": ok,
                    })
    return results


def list_vendored() -> List[Dict[str, Any]]:
    """Return list of all vendored driver entries."""
    entries = []
    for driver_type_dir in [MSEDGEDRIVER_VENDOR_DIR, CHROMEDRIVER_VENDOR_DIR]:
        if not driver_type_dir.exists():
            continue
        driver_type = driver_type_dir.name
        for major_dir in sorted(driver_type_dir.iterdir()):
            if not major_dir.is_dir():
                continue
            for exe_name in (
                f"msedgedriver{'.exe' if _IS_WIN else ''}",
                "msedgedriver",
                f"chromedriver{'.exe' if _IS_WIN else ''}",
                "chromedriver",
            ):
                candidate = major_dir / exe_name
                if candidate.exists():
                    checksum_path = major_dir / "SHA256SUM"
                    sha256 = ""
                    if checksum_path.exists():
                        sha256 = checksum_path.read_text(encoding="utf-8").split()[0]
                    entries.append({
                        "driver": driver_type,
                        "major": major_dir.name,
                        "path": str(candidate),
                        "size_bytes": candidate.stat().st_size,
                        "sha256": sha256,
                    })
                    break  # one entry per major dir
    return entries


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(name)s  %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        description="ICDEV™ driver vendor — fetch and verify browser drivers"
    )
    parser.add_argument("--fetch-edge", action="store_true", help="Fetch msedgedriver")
    parser.add_argument("--fetch-chrome", action="store_true", help="Fetch chromedriver")
    parser.add_argument("--version", help="Full driver version (e.g. 134.0.3124.57)")
    parser.add_argument("--major", help="Browser major version for chromedriver")
    parser.add_argument("--verify", action="store_true", help="Verify all vendored drivers")
    parser.add_argument("--list", action="store_true", dest="list_drivers", help="List vendored drivers")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result: Any = {}

    if args.fetch_edge:
        result = fetch_msedgedriver(version=args.version)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = result.get("status", "?")
            if status in ("ok", "already_vendored"):
                print(f"OK  {result.get('driver_path')}")
            else:
                print(f"ERROR: {result.get('error')}", file=sys.stderr)
                sys.exit(1)

    elif args.fetch_chrome:
        result = fetch_chromedriver(major=args.major)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = result.get("status", "?")
            if status in ("ok", "already_vendored"):
                print(f"OK  {result.get('driver_path')}")
            else:
                print(f"ERROR: {result.get('error')}", file=sys.stderr)
                sys.exit(1)

    elif args.verify:
        results = verify_all()
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            for r in results:
                status_str = "OK " if r["sha256_ok"] else "FAIL"
                print(f"  {status_str}  {r['driver']} major={r['major']}  {r['path']}")
            failures = [r for r in results if not r["sha256_ok"]]
            if failures:
                print(f"\n{len(failures)} verification failure(s)", file=sys.stderr)
                sys.exit(1)
            print(f"\nAll {len(results)} driver(s) verified OK")

    elif args.list_drivers:
        entries = list_vendored()
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            if not entries:
                print("  (no vendored drivers found)")
            for e in entries:
                print(f"  {e['driver']}  major={e['major']}  {e['path']}  ({e['size_bytes']:,} bytes)")

    else:
        parser.print_help()
