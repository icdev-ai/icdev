#!/usr/bin/env python3
# CUI // SP-CTI
"""Find the egress proxy this machine already uses, so setup can adopt it.

Most enterprises route all outbound traffic through a proxy or LLM gateway, and
in that world **there is no API key to configure** — the gateway authenticates
upstream. ICDEV supports that already: an `openai_compatible` provider with an
empty key sends the placeholder ``not-needed``, and
``tools/llm/proxy_resolver.py`` pushes a resolved proxy into the standard env
vars on every invoke.

What was missing is the first step: noticing the proxy that is already
configured, instead of asking a user to retype something their OS already knows.

ROTATION

A rotating proxy is the normal case in these environments, and it is why this
returns a SOURCE rather than only a value. Baking today's URL into `.env`
produces a config that works until the pool rotates and then fails in a way
that looks like the LLM is down.

The three configurations, worst to best for a rotating proxy:

    static URL in .env      breaks on the next rotation
    follow the OS env       correct if the rotator updates the environment
    ICDEV_LLM_PROXY_CMD     correct always — a command that prints the CURRENT
                            proxy, re-run per call and TTL-cached

`proxy_resolver` already resolves fresh on every invoke and calls
``provider.reset_client()`` when the value changed, so an SDK client that
cached the old proxy at construction is rebuilt. The detection here just has to
pick the right source and say why.

CLI::

    python -m tools.cli.proxy_detect --json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass, field

#: Standard proxy variables, in the order most tooling honours them.
_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
               "HTTP_PROXY", "http_proxy")

#: ICDEV's own overrides, which take priority over anything OS-level.
_ICDEV_CMD = "ICDEV_LLM_PROXY_CMD"
_ICDEV_URL = "ICDEV_LLM_PROXY"


@dataclass
class ProxyInfo:
    """A detected proxy and, crucially, where it came from."""

    url: str = ""
    source: str = "none"        # icdev-command | icdev-url | env | windows-registry | macos-scutil | pac | none
    rotating: bool = False      # the source can change under us
    no_proxy: str = ""
    pac_url: str = ""
    detail: str = ""
    candidates: list = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.url or self.pac_url or self.source == "icdev-command")

    def to_dict(self) -> dict:
        return {
            "url": self.url, "source": self.source, "rotating": self.rotating,
            "no_proxy": self.no_proxy, "pac_url": self.pac_url,
            "found": self.found, "detail": self.detail,
            "candidates": self.candidates,
        }


def _from_env() -> ProxyInfo | None:
    for var in _PROXY_VARS:
        val = (os.environ.get(var) or "").strip()
        if val:
            return ProxyInfo(
                url=val, source="env", rotating=True,
                no_proxy=(os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""),
                detail=f"from ${var}",
            )
    return None


def _from_windows_registry() -> ProxyInfo | None:
    """WinINET settings — what Windows itself uses, and what most corporate
    machines are configured through rather than environment variables."""
    if platform.system() != "Windows":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
    except OSError:
        return None
    try:
        def _get(name):
            try:
                return winreg.QueryValueEx(key, name)[0]
            except OSError:
                return None

        pac = _get("AutoConfigURL")
        enabled = _get("ProxyEnable")
        server = _get("ProxyServer")
        override = _get("ProxyOverride") or ""

        if pac:
            # A PAC script chooses per-destination and can change at any time.
            # There is no single URL to record, so the honest configuration is
            # a command that evaluates it.
            return ProxyInfo(source="pac", rotating=True, pac_url=str(pac),
                             no_proxy=str(override).replace(";", ","),
                             detail="Windows auto-config (PAC) script")
        if enabled and server:
            url = str(server)
            if "=" in url:
                # "http=host:port;https=host:port" — prefer the https entry.
                parts = dict(p.split("=", 1) for p in url.split(";") if "=" in p)
                url = parts.get("https") or parts.get("http") or ""
            if url and not url.startswith("http"):
                url = "http://" + url
            if url:
                return ProxyInfo(url=url, source="windows-registry", rotating=False,
                                 no_proxy=str(override).replace(";", ","),
                                 detail="Windows Internet Settings")
    finally:
        key.Close()
    return None


def _from_macos() -> ProxyInfo | None:
    if platform.system() != "Darwin":
        return None
    try:
        out = subprocess.run(["scutil", "--proxy"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return None
    pac = re.search(r"ProxyAutoConfigURLString\s*:\s*(\S+)", out)
    if pac:
        return ProxyInfo(source="pac", rotating=True, pac_url=pac.group(1),
                         detail="macOS auto-config (PAC) script")
    host = re.search(r"HTTPSProxy\s*:\s*(\S+)", out)
    port = re.search(r"HTTPSPort\s*:\s*(\d+)", out)
    if host:
        url = f"http://{host.group(1)}" + (f":{port.group(1)}" if port else "")
        return ProxyInfo(url=url, source="macos-scutil", rotating=False,
                         detail="macOS network settings")
    return None


def detect_proxy() -> ProxyInfo:
    """Best available proxy configuration, highest-confidence source first.

    ICDEV's own overrides win: if someone already pointed us at a rotator, that
    is a deliberate decision and re-detecting the OS value would override it
    with something staler.
    """
    cmd = (os.environ.get(_ICDEV_CMD) or "").strip()
    if cmd:
        return ProxyInfo(source="icdev-command", rotating=True, url="",
                         detail=f"${_ICDEV_CMD} is set — proxy resolved per call")

    url = (os.environ.get(_ICDEV_URL) or "").strip()
    if url:
        return ProxyInfo(url=url, source="icdev-url", rotating=True,
                         detail=f"${_ICDEV_URL} is set")

    for probe in (_from_env, _from_windows_registry, _from_macos):
        got = probe()
        if got:
            return got
    return ProxyInfo(detail="no proxy configured on this machine")


def proxy_env_updates(info: ProxyInfo, *, command: str = "",
                      ttl_seconds: int = 0) -> dict:
    """`.env` keys for the chosen proxy strategy.

    A rotating proxy must NOT be written as a literal URL. When the source can
    change, the right configuration is either a command that prints the current
    value or nothing at all — letting the SDKs read the OS environment, which
    the rotator updates. Writing today's URL is what produces "it worked
    yesterday".
    """
    out: dict[str, str] = {}
    if command:
        out[_ICDEV_CMD] = command
        if ttl_seconds:
            out["ICDEV_LLM_PROXY_CMD_TTL"] = str(ttl_seconds)
        return out

    if info.source == "env":
        # Already in the environment and re-read on every invoke. Recording a
        # copy would go stale the moment the rotator moved it.
        return out

    if info.url and not info.rotating:
        out[_ICDEV_URL] = info.url
    return out


def guidance(info: ProxyInfo) -> list:
    """What to tell the user about this particular configuration."""
    if not info.found:
        return ["No proxy detected — ICDEV will connect directly."]
    tips = []
    if info.source == "icdev-command":
        tips.append("A proxy command is configured; the current proxy is resolved "
                    "per call, so rotation is handled.")
    elif info.source == "pac":
        tips.append(f"PAC script at {info.pac_url}. A PAC chooses per destination "
                    "and can change at any time, so there is no single URL to "
                    "record — set ICDEV_LLM_PROXY_CMD to a command that evaluates "
                    "it, or leave the OS environment to the SDKs.")
    elif info.source == "env":
        tips.append("Using the proxy from the OS environment. It is re-read on "
                    "every call, so a rotator that updates the environment is "
                    "picked up without reconfiguring ICDEV.")
    elif info.url:
        tips.append(f"Detected {info.url} ({info.detail}).")
    if info.no_proxy:
        tips.append(f"Bypass list: {info.no_proxy}")
    tips.append("With a gateway that authenticates upstream, leave the API key "
                "unset — the provider sends 'not-needed' and the gateway "
                "supplies real credentials.")
    return tips


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Detect the egress proxy in use.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    info = detect_proxy()
    if args.json:
        print(json.dumps(info.to_dict(), indent=2))
    else:
        print(f"proxy source : {info.source}")
        print(f"proxy url    : {info.url or '(none)'}")
        print(f"rotating     : {info.rotating}")
        if info.pac_url:
            print(f"pac url      : {info.pac_url}")
        for tip in guidance(info):
            print(f"  - {tip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
