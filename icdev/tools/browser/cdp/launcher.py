# CUI // SP-CTI
"""CDP launch lifecycle — start a browser with remote debugging, find its port,
tear it down (cdp-port-03).

The browser serves CDP itself over a loopback WebSocket when launched with
``--remote-debugging-port`` — no driver binary. The launch has to be done right,
and the spike (cdp-00 §4.4/§4.5) enumerates the constraints, all encoded here:

* **A non-default profile is mandatory.** Chrome/Edge >=136 refuse
  ``--remote-debugging-port`` on the default profile (a deliberate hardening
  against cookie theft). A fresh temp ``--user-data-dir`` is required — and it is
  also the security control that makes an unauthenticated local debug port
  acceptable (no cookies, no saved credentials, nothing to steal through it).
* **Ephemeral port.** ``--remote-debugging-port=0`` lets the OS pick; the real
  port is read from the ``DevToolsActivePort`` file the browser writes into the
  user-data-dir, rather than assuming 9222 is free.
* **Loopback only.** The listener binds to ``127.0.0.1`` (the CDP default); never
  ``0.0.0.0``.
* **Air-gap hygiene flags**, or the browser stalls on calls that cannot complete
  (component update, background networking, first-run).

Security posture (§4.5): CDP is an INTERNAL transport detail beneath the scope
guard, never a caller-reachable escape hatch. The temp profile + ephemeral port +
deterministic teardown are the controls that keep an unauthenticated local port
acceptable; ``--remote-debugging-pipe`` (no TCP listener at all) is the documented
future hardening.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from tools.browser.browser_locator import BrowserLocation, locate_browser
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.cdp.launcher")

# Flags that keep an air-gapped browser from stalling on calls it cannot complete,
# plus the headless/sandbox defaults. Kept as a named constant so the launch
# surface is auditable in one place.
AIRGAP_HYGIENE_FLAGS: Tuple[str, ...] = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-dev-shm-usage",
)


class CDPLaunchError(RuntimeError):
    """The browser could not be launched or never advertised a debug port."""


def build_launch_args(
    executable: str,
    user_data_dir: str,
    *,
    headless: bool = True,
    window_size: Tuple[int, int] = (1920, 1080),
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    """Construct the browser argv. Ephemeral loopback debug port + mandatory
    non-default profile + air-gap hygiene flags. Caller ``extra_args`` come last
    so they can override defaults."""
    w, h = window_size
    args = [
        executable,
        "--remote-debugging-port=0",       # OS-assigned; real port via DevToolsActivePort
        f"--user-data-dir={user_data_dir}",  # mandatory: Chrome/Edge >=136 refuse debug on default profile
        f"--window-size={w},{h}",
    ]
    if headless:
        args.append("--headless=new")
    args.extend(AIRGAP_HYGIENE_FLAGS)
    args.extend(extra_args or [])
    return args


def read_devtools_active_port(user_data_dir: str) -> Tuple[int, str]:
    """Parse the ``DevToolsActivePort`` file the browser writes into its
    user-data-dir. Line 1 is the chosen port; line 2 (if present) is the
    browser-level WebSocket path, e.g. ``/devtools/browser/<uuid>``.

    Raises :class:`CDPLaunchError` if the file is absent or malformed.
    """
    path = Path(user_data_dir) / "DevToolsActivePort"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CDPLaunchError(f"DevToolsActivePort not found in {user_data_dir}: {exc}") from exc
    lines = text.splitlines()
    if not lines or not lines[0].strip().isdigit():
        raise CDPLaunchError(f"malformed DevToolsActivePort file: {text!r}")
    port = int(lines[0].strip())
    ws_path = lines[1].strip() if len(lines) > 1 and lines[1].strip() else ""
    return port, ws_path


def browser_ws_url(port: int, ws_path: str) -> str:
    """Build the loopback browser-level CDP WebSocket URL."""
    path = ws_path if ws_path.startswith("/") else f"/{ws_path}" if ws_path else "/devtools/browser"
    return f"ws://127.0.0.1:{port}{path}"


@dataclass
class LaunchedBrowser:
    """A running browser and how to reach its CDP endpoint."""

    process: "subprocess.Popen"
    browser: BrowserLocation
    user_data_dir: str
    port: int
    browser_ws_url: str
    _owns_profile: bool = True

    def terminate(self) -> None:
        """Deterministic teardown: stop the process, then remove the temp profile."""
        proc = self.process
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        except Exception as exc:  # noqa: BLE001 - teardown must not raise
            logger.debug("[cdp launcher] terminate: %s", exc)
        if self._owns_profile:
            shutil.rmtree(self.user_data_dir, ignore_errors=True)


def launch(
    *,
    browser: Optional[BrowserLocation] = None,
    headless: bool = True,
    window_size: Tuple[int, int] = (1920, 1080),
    extra_args: Optional[List[str]] = None,
    port_wait_timeout: float = 20.0,
    user_data_dir: Optional[str] = None,
) -> LaunchedBrowser:
    """Locate a browser, launch it with remote debugging, and wait for its port.

    Loudly refuses (spike §4.6) when no Chromium-family browser is present, naming
    what was searched — the opposite of stalling on a launch timeout.
    """
    browser = browser or locate_browser()
    if browser is None:
        raise CDPLaunchError(
            "no Chromium-family browser found (searched Edge, Chrome, Chromium on "
            "PATH, the App Paths registry, and known install dirs); cannot start a "
            "CDP session. Install a Chromium-family browser or use Tier 3 "
            "(browser-free HTTP verification)."
        )

    owns_profile = user_data_dir is None
    profile = user_data_dir or tempfile.mkdtemp(prefix="icdev-cdp-")
    args = build_launch_args(
        browser.executable, profile, headless=headless, window_size=window_size, extra_args=extra_args
    )
    logger.info("[cdp launcher] launching %s (headless=%s) profile=%s", browser.family, headless, profile)

    # Detach the browser's own stdio; we speak to it over the debug socket only.
    proc = subprocess.Popen(  # noqa: S603 - executable path comes from the trusted locator, not user input
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )

    port, ws_path = _await_port(proc, profile, port_wait_timeout)
    return LaunchedBrowser(
        process=proc,
        browser=browser,
        user_data_dir=profile,
        port=port,
        browser_ws_url=browser_ws_url(port, ws_path),
        _owns_profile=owns_profile,
    )


def _await_port(proc: "subprocess.Popen", user_data_dir: str, timeout: float) -> Tuple[int, str]:
    """Poll for the DevToolsActivePort file; fail loudly if the browser dies or
    never writes it."""
    deadline = time.monotonic() + timeout
    port_file = Path(user_data_dir) / "DevToolsActivePort"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _cleanup_profile(user_data_dir)
            raise CDPLaunchError(f"browser exited (code {proc.returncode}) before advertising a debug port")
        if port_file.exists():
            try:
                return read_devtools_active_port(user_data_dir)
            except CDPLaunchError:
                pass  # file may be mid-write; retry until deadline
        time.sleep(0.1)
    _terminate(proc)
    _cleanup_profile(user_data_dir)
    raise CDPLaunchError(
        f"browser did not advertise a debug port within {timeout}s "
        "(RemoteDebuggingAllowed policy may forbid it — run the preflight to check)"
    )


def _terminate(proc: "subprocess.Popen") -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _cleanup_profile(user_data_dir: str) -> None:
    if os.path.basename(user_data_dir).startswith("icdev-cdp-"):
        shutil.rmtree(user_data_dir, ignore_errors=True)
