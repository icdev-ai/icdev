# CUI // SP-CTI
"""Central HTTP client with mTLS, CA bundle, and proxy support.

All outbound HTTPS calls (LLM providers, SAM.gov, Ollama, marketplace, etc.)
should go through `get_session()` or `request()` so operators can harden the
outbound posture with a single set of env vars.

Env vars (all optional):

    ICDEV_MTLS_CLIENT_CERT   path to client certificate (PEM)
    ICDEV_MTLS_CLIENT_KEY    path to client private key (PEM)
    ICDEV_MTLS_CA_BUNDLE     path to CA bundle used to verify the server
    ICDEV_MTLS_VERIFY        "true" (default) | "false" — disable TLS
                             verification entirely (NOT for production)
    ICDEV_HTTP_TIMEOUT       default timeout in seconds (connect + read),
                             default: 30
    ICDEV_HTTP_PROXY         proxy URL for http:// requests (also reads
                             HTTP_PROXY)
    ICDEV_HTTPS_PROXY        proxy URL for https:// requests (also reads
                             HTTPS_PROXY)

Usage:

    from tools.http.client import get_session, request

    # Option 1 — one-shot
    r = request("GET", "https://api.example.gov/v1/opps", timeout=10)

    # Option 2 — reusable session (preferred for many calls)
    s = get_session()
    r = s.get("https://api.example.gov/v1/opps")

Notes:
    - `session.cert` is set only when BOTH cert and key env vars are present.
    - `session.verify` defaults to the CA bundle if provided, else True
      (requests' built-in certifi bundle), else False if
      ICDEV_MTLS_VERIFY=false.
    - Timeouts are applied as a default on `request()`; direct `session.get`
      callers must still pass their own timeout.
"""

from __future__ import annotations

import os
from typing import Any

import requests


_DEFAULT_TIMEOUT = None  # populated lazily


def _env_flag(name: str, default: bool = True) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _build_cert() -> tuple[str, str] | None:
    cert = os.environ.get("ICDEV_MTLS_CLIENT_CERT")
    key = os.environ.get("ICDEV_MTLS_CLIENT_KEY")
    if cert and key:
        return (cert, key)
    return None


def _build_verify() -> Any:
    # ICDEV_MTLS_VERIFY=false overrides everything (testing only).
    if not _env_flag("ICDEV_MTLS_VERIFY", default=True):
        return False
    ca = os.environ.get("ICDEV_MTLS_CA_BUNDLE")
    if ca:
        return ca
    return True  # requests uses certifi by default


def _build_proxies() -> dict[str, str]:
    proxies: dict[str, str] = {}
    http_proxy = os.environ.get("ICDEV_HTTP_PROXY") or os.environ.get("HTTP_PROXY")
    https_proxy = os.environ.get("ICDEV_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY")
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy
    return proxies


def default_timeout() -> float:
    global _DEFAULT_TIMEOUT
    if _DEFAULT_TIMEOUT is None:
        try:
            _DEFAULT_TIMEOUT = float(os.environ.get("ICDEV_HTTP_TIMEOUT", "30"))
        except ValueError:
            _DEFAULT_TIMEOUT = 30.0
    return _DEFAULT_TIMEOUT


def get_session() -> requests.Session:
    """Return a configured requests.Session with mTLS + CA + proxy applied."""
    session = requests.Session()
    cert = _build_cert()
    if cert:
        session.cert = cert
    session.verify = _build_verify()
    proxies = _build_proxies()
    if proxies:
        session.proxies.update(proxies)
    return session


def request(method: str, url: str, **kwargs) -> requests.Response:
    """One-shot request via a fresh session with the default timeout applied."""
    kwargs.setdefault("timeout", default_timeout())
    with get_session() as s:
        return s.request(method, url, **kwargs)
