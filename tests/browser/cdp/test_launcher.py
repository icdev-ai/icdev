# CUI // SP-CTI
"""cdp-port-03 — CDP launch lifecycle helpers.

The pure, testable half of the launcher: argv construction (mandatory non-default
profile + ephemeral loopback port + air-gap hygiene flags), DevToolsActivePort
parsing, and the loud refusal when no browser is present. Actually spawning a
browser is integration (a browser must be installed and the sandbox must permit
it), so that is not asserted here.
"""
from __future__ import annotations

import importlib

import pytest

launcher = importlib.import_module("tools.browser.cdp.launcher")


def test_launch_args_encode_the_security_and_airgap_posture():
    args = launcher.build_launch_args("/x/msedge", "/tmp/prof", headless=True, window_size=(1280, 720))
    assert args[0] == "/x/msedge"
    # ephemeral port (not a fixed 9222), mandatory non-default profile
    assert "--remote-debugging-port=0" in args
    assert "--user-data-dir=/tmp/prof" in args
    assert "--headless=new" in args
    assert "--window-size=1280,720" in args
    # air-gap hygiene — the browser would otherwise stall on these
    for flag in ("--no-first-run", "--disable-background-networking", "--disable-component-update"):
        assert flag in args


def test_launch_args_headless_false_omits_headless():
    args = launcher.build_launch_args("/x/chrome", "/tmp/p", headless=False)
    assert "--headless=new" not in args


def test_extra_args_come_last():
    args = launcher.build_launch_args("/x/chrome", "/tmp/p", extra_args=["--proxy-server=x"])
    assert args[-1] == "--proxy-server=x"


def test_read_devtools_active_port_parses_port_and_ws_path(tmp_path):
    (tmp_path / "DevToolsActivePort").write_text("54231\n/devtools/browser/abc-123\n", encoding="utf-8")
    port, ws_path = launcher.read_devtools_active_port(str(tmp_path))
    assert port == 54231
    assert ws_path == "/devtools/browser/abc-123"


def test_read_devtools_active_port_missing_raises(tmp_path):
    with pytest.raises(launcher.CDPLaunchError):
        launcher.read_devtools_active_port(str(tmp_path))


def test_read_devtools_active_port_malformed_raises(tmp_path):
    (tmp_path / "DevToolsActivePort").write_text("not-a-port\n", encoding="utf-8")
    with pytest.raises(launcher.CDPLaunchError):
        launcher.read_devtools_active_port(str(tmp_path))


def test_browser_ws_url_builds_loopback_url():
    assert launcher.browser_ws_url(9333, "/devtools/browser/x") == "ws://127.0.0.1:9333/devtools/browser/x"
    # tolerates a missing leading slash and an empty path
    assert launcher.browser_ws_url(9333, "devtools/browser/x") == "ws://127.0.0.1:9333/devtools/browser/x"
    assert launcher.browser_ws_url(9333, "") == "ws://127.0.0.1:9333/devtools/browser"


def test_launch_refuses_loudly_when_no_browser(monkeypatch):
    monkeypatch.setattr(launcher, "locate_browser", lambda: None)
    with pytest.raises(launcher.CDPLaunchError) as exc:
        launcher.launch()
    msg = str(exc.value)
    assert "no Chromium-family browser" in msg
    assert "Tier 3" in msg  # points at the honest fallback
