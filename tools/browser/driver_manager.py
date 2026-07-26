#!/usr/bin/env python3
# CUI // SP-CTI
"""Browser driver manager — vendored msedgedriver + chromedriver fallback.

Provides a singleton factory for Selenium WebDriver instances.  Resolution
order (no runtime downloads):

1. Vendored msedgedriver matching installed Edge major version
   ``vendor/drivers/msedgedriver/{major}/msedgedriver[.exe]``
2. msedgedriver found on PATH (system-installed)
3. Vendored chromedriver (any major under ``vendor/drivers/chromedriver/``)
4. chromedriver on PATH

On Windows 11, Microsoft Edge is pre-installed, so route 1 or 2 should
always succeed once the vendor directory is populated via
``tools/airgap/driver_vendor.py``.

Usage::

    from tools.browser.driver_manager import get_driver, DriverManager

    # One-shot convenience
    driver = get_driver()
    driver.get("http://localhost:5050")
    driver.quit()

    # Access singleton for metadata
    mgr = DriverManager.instance()
    print(mgr.resolved_browser, mgr.resolved_driver_path)

CLI::

    python tools/browser/driver_manager.py --probe
    python tools/browser/driver_manager.py --json
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.browser.driver_manager")

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent.parent
VENDOR_DIR = BASE_DIR / "vendor" / "drivers"
MSEDGEDRIVER_VENDOR_DIR = VENDOR_DIR / "msedgedriver"
CHROMEDRIVER_VENDOR_DIR = VENDOR_DIR / "chromedriver"

_IS_WIN = platform.system() == "Windows"
_DRIVER_EXE_SUFFIX = ".exe" if _IS_WIN else ""


# ── Errors ────────────────────────────────────────────────────────────────────

class AirgapDriverMissingError(RuntimeError):
    """Raised when no driver binary is resolvable and air-gap mode is active.

    This is the fail-closed guarantee: in an air-gapped environment, driver
    resolution that falls through to Selenium Manager (which resolves drivers by
    *downloading* them) is a promise violation, not an acceptable last resort.
    Rather than hand control to a component that reaches the network, raise this
    with the exact admin refresh command so the operator gets an actionable next
    step instead of a confusing network timeout. Never triggers a CDN download.
    """


def _airgap_active() -> bool:
    """True when air-gap mode is in force (env override or detected environment).

    Kept as a thin wrapper so the network-download fail-closed decision has one
    seam and a missing airgap package degrades to "not air-gapped" (the
    commercial default) rather than crashing driver construction.
    """
    try:
        from tools.airgap.detector import is_airgap
        return bool(is_airgap())
    except Exception:  # noqa: BLE001 - absence of the airgap pkg means commercial
        import os
        return os.environ.get("ICDEV_AIRGAP", "").lower() in ("1", "true", "yes")


# ── Edge version detection ────────────────────────────────────────────────────

def _detect_edge_version() -> Optional[str]:
    """Return the installed Edge version string (e.g. '134.0.3124.57').

    Tries registry (Windows), common binary paths, then ``msedge --version``.
    Returns None if Edge is not found.
    """
    if _IS_WIN:
        version = _detect_edge_version_windows()
        if version:
            return version

    # PATH-based fallback (Linux/macOS)
    for exe in ("microsoft-edge", "microsoft-edge-stable", "msedge"):
        path = shutil.which(exe)
        if path:
            try:
                out = subprocess.check_output(
                    [path, "--version"], stderr=subprocess.DEVNULL, timeout=5
                ).decode("utf-8", errors="replace").strip()
                # "Microsoft Edge 134.0.3124.57"
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
                if m:
                    return m.group(1)
            except Exception:
                pass
    return None


def _detect_edge_version_windows() -> Optional[str]:
    """Read Edge version from Windows registry or file-system."""
    try:
        import winreg  # only available on Windows

        keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Edge\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Edge\BLBeacon"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
                r"\{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}",
            ),
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

    # Fallback: look for versioned subdirs under Edge's Application folder
    app_dirs = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application"),
        Path(r"C:\Program Files\Microsoft\Edge\Application"),
    ]
    for app_dir in app_dirs:
        if app_dir.exists():
            for subdir in sorted(app_dir.iterdir(), reverse=True):
                if subdir.is_dir() and re.match(r"\d+\.\d+\.\d+\.\d+", subdir.name):
                    return subdir.name
    return None


def _detect_chrome_version() -> Optional[str]:
    """Return the installed Google Chrome version string, or None.

    Symmetric with :func:`_detect_edge_version` — registry + Application dir on
    Windows, ``--version`` on Linux/macOS. Needed so staleness detection (D2) can
    compare the installed browser major against the vendored driver major for the
    chrome path, not only the edge path.
    """
    if _IS_WIN:
        try:
            import winreg  # only available on Windows

            keys = [
                (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon"),
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

        for app_dir in (
            Path(r"C:\Program Files\Google\Chrome\Application"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application"),
        ):
            if app_dir.exists():
                for subdir in sorted(app_dir.iterdir(), reverse=True):
                    if subdir.is_dir() and re.match(r"\d+\.\d+\.\d+\.\d+", subdir.name):
                        return subdir.name

    for exe in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        path = shutil.which(exe)
        if path:
            try:
                out = subprocess.check_output(
                    [path, "--version"], stderr=subprocess.DEVNULL, timeout=5
                ).decode("utf-8", errors="replace").strip()
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out)
                if m:
                    return m.group(1)
            except Exception:
                pass
    return None


def _major(version: str) -> str:
    """Return the major version component (e.g. '134' from '134.0.3124.57')."""
    return version.split(".")[0]


# ── Vendored driver lookup ────────────────────────────────────────────────────

def _find_vendored_msedgedriver(edge_major: Optional[str] = None) -> Optional[Path]:
    """Return path to a vendored msedgedriver binary, or None.

    Prefers the version matching *edge_major*; falls back to the newest
    available major if the exact match is absent.
    """
    if not MSEDGEDRIVER_VENDOR_DIR.exists():
        return None

    exe_name = f"msedgedriver{_DRIVER_EXE_SUFFIX}"

    # Collect all available vendored major versions
    available: List[Path] = []
    for subdir in MSEDGEDRIVER_VENDOR_DIR.iterdir():
        if subdir.is_dir() and re.match(r"\d+", subdir.name):
            candidate = subdir / exe_name
            if candidate.exists():
                available.append(candidate)

    if not available:
        return None

    if edge_major:
        for path in available:
            if path.parent.name == edge_major:
                logger.debug("Vendored msedgedriver matched major %s: %s", edge_major, path)
                return path

    # Best-effort: pick the numerically highest major
    available.sort(key=lambda p: int(p.parent.name), reverse=True)
    logger.debug(
        "Vendored msedgedriver: no exact match for major %s; using %s",
        edge_major,
        available[0],
    )
    return available[0]


def _find_vendored_chromedriver() -> Optional[Path]:
    """Return path to any vendored chromedriver binary, or None."""
    if not CHROMEDRIVER_VENDOR_DIR.exists():
        return None

    exe_name = f"chromedriver{_DRIVER_EXE_SUFFIX}"
    available: List[Path] = []
    for subdir in CHROMEDRIVER_VENDOR_DIR.iterdir():
        if subdir.is_dir() and re.match(r"\d+", subdir.name):
            candidate = subdir / exe_name
            if candidate.exists():
                available.append(candidate)

    if not available:
        return None

    available.sort(key=lambda p: int(p.parent.name), reverse=True)
    return available[0]


# ── Driver resolution ─────────────────────────────────────────────────────────

class DriverResolution:
    """Result of driver resolution — browser type + executable path.

    ``edge_version`` / ``chrome_version`` record the *installed* browser versions
    detected at resolve time, regardless of which browser was chosen. D6: these
    were previously discarded on every chrome-branch resolution, so ``--probe``
    reported ``resolved_edge_version: null`` even on a machine with Edge present.
    Threading them through is also the prerequisite for staleness detection (D2).
    """

    __slots__ = ("browser", "driver_path", "source", "edge_version", "chrome_version")

    def __init__(
        self,
        browser: str,
        driver_path: Optional[str],
        source: str,
        edge_version: Optional[str] = None,
        chrome_version: Optional[str] = None,
    ) -> None:
        self.browser = browser          # "edge" | "chrome"
        self.driver_path = driver_path  # absolute path or None (use PATH)
        self.source = source            # "vendored" | "path" | "selenium_manager"
        self.edge_version = edge_version
        self.chrome_version = chrome_version

    def to_dict(self) -> Dict[str, Any]:
        return {
            "browser": self.browser,
            "driver_path": self.driver_path,
            "source": self.source,
            "edge_version": self.edge_version,
            "chrome_version": self.chrome_version,
        }


def driver_staleness(resolution: "DriverResolution") -> Dict[str, Any]:
    """Compare a *vendored* driver's major against the installed browser major.

    D2: the browser auto-updates, the vendored driver does not — chromedriver /
    msedgedriver refuse a browser whose major differs, so a stale vendored store
    resolves at *resolve* time but fails at *launch*. This surfaces the mismatch
    up front. Only meaningful for ``source == "vendored"`` (the driver major is
    the vendor subdir name); returns ``checkable=False`` otherwise, since a PATH
    or Selenium-Manager driver's version is not known without launching it.
    """
    out: Dict[str, Any] = {
        "checkable": False,
        "stale": False,
        "browser": resolution.browser,
        "driver_major": None,
        "browser_major": None,
        "reason": None,
    }
    if resolution.source != "vendored" or not resolution.driver_path:
        out["reason"] = f"not a vendored driver (source={resolution.source}); staleness not determinable"
        return out

    driver_major = Path(resolution.driver_path).parent.name
    installed = resolution.edge_version if resolution.browser == "edge" else resolution.chrome_version
    out["checkable"] = True
    out["driver_major"] = driver_major
    if not installed:
        out["reason"] = f"{resolution.browser} not detected; cannot compare against vendored major {driver_major}"
        return out

    browser_major = _major(installed)
    out["browser_major"] = browser_major
    if driver_major != browser_major:
        out["stale"] = True
        out["reason"] = (
            f"vendored {resolution.browser}driver major {driver_major} != installed "
            f"{resolution.browser} major {browser_major}; the driver will refuse the browser at launch. "
            f"Refresh: python tools/airgap/driver_vendor.py "
            f"{'--fetch-edge' if resolution.browser == 'edge' else f'--fetch-chrome --major {browser_major}'}"
        )
    else:
        out["reason"] = f"vendored major {driver_major} matches installed {resolution.browser} major {browser_major}"
    return out


def resolve_driver() -> DriverResolution:
    """Determine which WebDriver binary to use.

    Resolution order (no network):
    1. Vendored msedgedriver matching installed Edge major
    2. msedgedriver on PATH
    3. Vendored chromedriver
    4. chromedriver on PATH
    5. Selenium Manager (bundled with selenium >= 4.6)
    """
    edge_version = _detect_edge_version()
    edge_major = _major(edge_version) if edge_version else None
    # Detected once and threaded through EVERY resolution (D6) so --probe reports
    # the installed versions regardless of which browser/driver was chosen, and
    # staleness (D2) can compare against them.
    chrome_version = _detect_chrome_version()

    def _res(browser: str, driver_path: Optional[str], source: str) -> DriverResolution:
        return DriverResolution(
            browser=browser,
            driver_path=driver_path,
            source=source,
            edge_version=edge_version,
            chrome_version=chrome_version,
        )

    # 1. Vendored msedgedriver
    vendored_edge = _find_vendored_msedgedriver(edge_major)
    if vendored_edge:
        return _res("edge", str(vendored_edge), "vendored")

    # 2. msedgedriver on PATH
    path_edge = shutil.which("msedgedriver")
    if path_edge:
        return _res("edge", path_edge, "path")

    # 3. Vendored chromedriver
    vendored_chrome = _find_vendored_chromedriver()
    if vendored_chrome:
        return _res("chrome", str(vendored_chrome), "vendored")

    # 4a. Well-known Linux/container Chrome paths (checked before PATH shutil.which
    #     to pick up headless-chromium in Alpine/Debian containers quickly)
    if platform.system() == "Linux":
        for _linux_chrome in (
            Path("/usr/bin/chromium"),
            Path("/usr/bin/chromium-browser"),
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/chromium-headless"),
        ):
            if _linux_chrome.exists():
                _linux_chromedriver = shutil.which("chromedriver")
                return _res("chrome", _linux_chromedriver, "path")

    # 4b. chromedriver on PATH
    path_chrome = shutil.which("chromedriver")
    if path_chrome:
        return _res("chrome", path_chrome, "path")

    # 5. Let Selenium Manager handle it (selenium >= 4.6 bundles selenium-manager)
    return _res("chrome", None, "selenium_manager")


# ── Singleton ─────────────────────────────────────────────────────────────────

class DriverManager:
    """Singleton that caches driver resolution and instantiates WebDrivers.

    Attributes:
        resolved_browser (str): "edge" or "chrome"
        resolved_driver_path (str | None): absolute path to driver binary or None
        resolved_source (str): "vendored" | "path" | "selenium_manager"
    """

    _instance: Optional["DriverManager"] = None

    def __init__(self) -> None:
        resolution = resolve_driver()
        self._resolution = resolution
        self.resolved_browser: str = resolution.browser
        self.resolved_driver_path: Optional[str] = resolution.driver_path
        self.resolved_source: str = resolution.source
        self.resolved_edge_version: Optional[str] = resolution.edge_version
        self.resolved_chrome_version: Optional[str] = resolution.chrome_version
        logger.info(
            "DriverManager: browser=%s source=%s path=%s edge_version=%s chrome_version=%s",
            self.resolved_browser,
            self.resolved_source,
            self.resolved_driver_path,
            self.resolved_edge_version,
            self.resolved_chrome_version,
        )
        # D2: a stale vendored driver resolves but fails at launch. Warn loudly so
        # the failure is not a cryptic chromedriver "browser N majors ahead" later.
        staleness = driver_staleness(resolution)
        if staleness.get("stale"):
            logger.warning("[driver_manager] STALE vendored driver: %s", staleness["reason"])

    @classmethod
    def instance(cls) -> "DriverManager":
        """Return (or create) the process-level singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Clear cached singleton (useful in tests)."""
        cls._instance = None

    # ── Public factory ──────────────────────────────────────────────────────

    def create_driver(
        self,
        headless: bool = True,
        window_size: tuple = (1920, 1080),
        extra_args: Optional[List[str]] = None,
        disable_gpu: bool = True,
        no_sandbox: bool = True,
    ) -> Any:
        """Instantiate and return a Selenium WebDriver.

        Parameters
        ----------
        headless:
            Run browser in headless mode (default True).
        window_size:
            ``(width, height)`` tuple.
        extra_args:
            Additional CLI arguments to pass to the browser.
        disable_gpu:
            Pass ``--disable-gpu`` (recommended for headless on Windows).
        no_sandbox:
            Pass ``--no-sandbox`` (required in some CI environments).

        Returns
        -------
        selenium.webdriver.Remote
            Fully initialised WebDriver instance.

        Raises
        ------
        AirgapDriverMissingError
            If no driver binary is resolvable (resolution fell through to
            Selenium Manager) *and* air-gap mode is active. Carries the admin
            refresh command; never triggers a CDN download.
        ImportError
            If selenium is not installed.
        RuntimeError
            If the driver binary cannot be located or started.
        """
        # Fail closed BEFORE importing/launching selenium: a None driver_path
        # means resolution fell through to Selenium Manager, which downloads.
        # In air-gap mode that is a guarantee violation — raise an actionable
        # error naming the search paths and the admin refresh command instead.
        if self.resolved_driver_path is None and _airgap_active():
            raise AirgapDriverMissingError(
                "No vendored WebDriver binary was resolvable and air-gap mode is "
                "active, so driver construction was refused rather than allowed to "
                "fall through to Selenium Manager (which downloads drivers from a "
                "CDN).\n"
                f"  searched (vendored): {MSEDGEDRIVER_VENDOR_DIR}\n"
                f"                       {CHROMEDRIVER_VENDOR_DIR}\n"
                "  searched (PATH):     msedgedriver, chromedriver\n"
                "Refresh the vendored driver on a connected admin host, then "
                "transfer vendor/drivers/ to this host:\n"
                "  python tools/airgap/driver_vendor.py --fetch-edge\n"
                "  python tools/airgap/driver_vendor.py --fetch-chrome --major <installed-major>"
            )
        if self.resolved_driver_path is None:
            logger.warning(
                "[driver_manager] no driver binary resolved; handing off to "
                "Selenium Manager, which may download a driver from the network "
                "(commercial mode — set ICDEV_AIRGAP=true to fail closed instead)."
            )
        try:
            from selenium import webdriver as wd
            from selenium.webdriver.edge.service import Service as EdgeService
            from selenium.webdriver.chrome.service import Service as ChromeService
        except ImportError as exc:
            raise ImportError(
                "selenium is not installed. Run: pip install selenium"
            ) from exc

        w, h = window_size
        if self.resolved_browser == "edge":
            from selenium.webdriver.edge.options import Options as EdgeOptions

            opts = EdgeOptions()
            if headless:
                opts.add_argument("--headless=new")
            opts.add_argument(f"--window-size={w},{h}")
            if disable_gpu:
                opts.add_argument("--disable-gpu")
            if no_sandbox:
                opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            for arg in (extra_args or []):
                opts.add_argument(arg)

            if self.resolved_driver_path:
                service = EdgeService(executable_path=self.resolved_driver_path)
                return wd.Edge(service=service, options=opts)
            return wd.Edge(options=opts)

        else:
            from selenium.webdriver.chrome.options import Options as ChromeOptions

            opts = ChromeOptions()
            if headless:
                opts.add_argument("--headless=new")
            opts.add_argument(f"--window-size={w},{h}")
            if disable_gpu:
                opts.add_argument("--disable-gpu")
            if no_sandbox:
                opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            for arg in (extra_args or []):
                opts.add_argument(arg)

            if self.resolved_driver_path:
                service = ChromeService(executable_path=self.resolved_driver_path)
                return wd.Chrome(service=service, options=opts)
            return wd.Chrome(options=opts)

    def staleness(self) -> Dict[str, Any]:
        """Staleness of the resolved (vendored) driver vs the installed browser."""
        return driver_staleness(self._resolution)

    def probe(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dict describing the resolved driver."""
        return {
            "resolved_browser": self.resolved_browser,
            "resolved_driver_path": self.resolved_driver_path,
            "resolved_source": self.resolved_source,
            "resolved_edge_version": self.resolved_edge_version,
            "resolved_chrome_version": self.resolved_chrome_version,
            "staleness": self.staleness(),
            "vendored_msedgedriver_dir": str(MSEDGEDRIVER_VENDOR_DIR),
            "vendored_chromedriver_dir": str(CHROMEDRIVER_VENDOR_DIR),
            "platform": platform.system(),
        }


# ── Module-level convenience ──────────────────────────────────────────────────

def get_driver(
    headless: bool = True,
    window_size: tuple = (1920, 1080),
    extra_args: Optional[List[str]] = None,
) -> Any:
    """Return a ready-to-use Selenium WebDriver (the public Phase-D3.1 entry point).

    Convenience wrapper around ``DriverManager.instance().create_driver()``.

    Parameters
    ----------
    headless:
        Run browser in headless mode. Default ``True``. Add ``'--headless=new'``
        (Chromium family) and ``'--window-size=WxH'`` to the launch flags.
    window_size:
        ``(width, height)`` tuple. Default ``(1920, 1080)``. Applied via
        ``--window-size`` launch flag regardless of headless mode.
    extra_args:
        Additional browser CLI flags forwarded verbatim. Merged after the
        air-gap-safe defaults (``--no-sandbox``, ``--disable-gpu``,
        ``--disable-dev-shm-usage``) so caller flags can override them.

    Return type
    -----------
    Declared as ``Any`` to keep import-time coupling to the ``selenium``
    package optional (air-gap environments without selenium installed can
    still import this module for resolution probing). At runtime the value
    is always one of ``selenium.webdriver.Edge`` or ``selenium.webdriver.Chrome``
    \u2014 determined by ``resolve_driver()`` at first call, then reused
    for the lifetime of the Python process. A future ``browser='edge'|'chrome'|'auto'``
    keyword will expose explicit override; for now auto-selection matches
    whatever is present under ``vendor/drivers/``.

    Caching / side effects
    ----------------------
    - **Driver-binary resolution is cached** at the process level via
      ``DriverManager.instance()`` (a classmethod-backed singleton). First
      call walks ``vendor/drivers/``, picks a binary, and records the
      decision; subsequent calls are O(1).
    - **WebDriver instances are NOT cached**. Each ``get_driver()`` call
      returns a *fresh* ``WebDriver`` session. This intentionally differs
      from a connection-pool design because Selenium sessions are stateful
      (cookies, URL, window handle) and sharing them across tests produces
      flaky results.

    Cleanup contract
    ----------------
    **The caller owns the returned driver and MUST call ``driver.quit()``
    before the process exits.** ``get_driver()`` does not register any
    ``atexit`` hook, and there is no context-manager helper at this layer.
    The canonical pattern is ``try/finally``::

        driver = get_driver(headless=True)
        try:
            driver.get(url)
            ...
        finally:
            driver.quit()

    A context-manager helper (``with managed_driver(...) as d:``) is a
    candidate for D7's pilot refactor if the first three migrated tests
    find the try/finally boilerplate noisy.

    Thread safety
    -------------
    ``DriverManager.instance()`` is not thread-locked; concurrent first-call
    threads may race on the resolution cache. That's acceptable because
    resolution is idempotent (same ``vendor/drivers/`` scan, same result).
    Within a test, the returned ``WebDriver`` is single-threaded per
    selenium's own contract \u2014 do not share across threads.

    Air-gap behaviour
    -----------------
    If no driver binary is resolvable (no vendored binary, none on PATH),
    ``create_driver()`` raises ``AirgapDriverMissingError`` with the admin
    refresh command. **Never triggers a CDN download.**
    """
    return DriverManager.instance().create_driver(
        headless=headless,
        window_size=window_size,
        extra_args=extra_args,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    parser = argparse.ArgumentParser(description="ICDEV™ Browser driver manager probe")
    parser.add_argument("--probe", action="store_true", help="Probe driver resolution")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--smoke", action="store_true", help="Instantiate driver and quit")
    parser.add_argument(
        "--check-staleness",
        action="store_true",
        help="Exit non-zero if the resolved vendored driver's major does not match "
        "the installed browser (D2). For admin/CI monitoring on the vendoring host.",
    )
    args = parser.parse_args()

    mgr = DriverManager.instance()
    info = mgr.probe()

    if args.check_staleness:
        st = mgr.staleness()
        print(json.dumps(st, indent=2))
        sys.exit(1 if st.get("stale") else 0)

    if args.json or args.probe:
        print(json.dumps(info, indent=2))

    if args.smoke:
        print("Launching WebDriver smoke test …", file=sys.stderr)
        try:
            drv = mgr.create_driver(headless=True)
            drv.get("about:blank")
            title = drv.title
            drv.quit()
            print(json.dumps({"smoke": "ok", "title": title}))
        except Exception as exc:
            print(json.dumps({"smoke": "fail", "error": str(exc)}))
            sys.exit(1)

    if not (args.probe or args.json or args.smoke):
        print(json.dumps(info, indent=2))
