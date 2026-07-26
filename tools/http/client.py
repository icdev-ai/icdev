# CUI // SP-CTI
"""Central HTTP client with mTLS, CA bundle, and proxy support.

All outbound HTTPS calls (LLM providers, SAM.gov, Ollama, marketplace, etc.)
should go through `get_session()` or `request()` so operators can harden the
outbound posture with a single set of env vars.

Thresholds (connect/read timeout, retries, backoff, max_redirects) are read
from ``args/http_client.yaml``.  All numeric values can be overridden there
without touching code — no hardcoded thresholds here.

Env vars (all optional, override yaml where noted):

    ICDEV_MTLS_CLIENT_CERT   path to client certificate (PEM)
    ICDEV_MTLS_CLIENT_KEY    path to client private key (PEM)
    ICDEV_MTLS_CA_BUNDLE     path to CA bundle used to verify the server
    ICDEV_MTLS_VERIFY        "true" (default) | "false" — disable TLS
                             verification entirely (NOT for production)
    ICDEV_HTTP_TIMEOUT       overrides yaml timeout.read (seconds)
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
import pathlib
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]


# ── YAML config loading ───────────────────────────────────────────────────────

_HTTP_CONFIG: dict | None = None
_ARGS_FILE = pathlib.Path(__file__).resolve().parents[2] / "args" / "http_client.yaml"


def _load_config() -> dict:
    global _HTTP_CONFIG
    if _HTTP_CONFIG is None:
        if _yaml is not None and _ARGS_FILE.exists():
            with _ARGS_FILE.open(encoding="utf-8") as fh:
                _HTTP_CONFIG = _yaml.safe_load(fh) or {}
        else:
            _HTTP_CONFIG = {}
    return _HTTP_CONFIG


def _cfg(section: str, key: str, default: float) -> float:
    """Read ``key`` from ``section``; an empty *section* means the yaml top level.

    ``max_retries``/``backoff_factor``/``max_redirects``/``*_chunk_size`` live at
    the top level of ``args/http_client.yaml``, so an empty section must resolve
    against the root mapping — looking up a literal ``""`` key would silently
    discard every operator edit to those five settings.
    """
    cfg = _load_config()
    scope = cfg if not section else cfg.get(section, {})
    if not isinstance(scope, dict):
        return default
    try:
        return float(scope.get(key, default))
    except (TypeError, ValueError):
        return default


# ── Derived thresholds (all sourced from yaml, no inline magic numbers) ───────

def _connect_timeout() -> float:
    return _cfg("timeout", "connect", 10.0)


def _read_timeout() -> float:
    # ICDEV_HTTP_TIMEOUT env var overrides yaml for backward compat.
    env = os.environ.get("ICDEV_HTTP_TIMEOUT")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    return _cfg("timeout", "read", 30.0)


def _max_retries() -> int:
    return int(_cfg("", "max_retries", 3))


def _backoff_factor() -> float:
    return _cfg("", "backoff_factor", 0.5)


def _max_redirects() -> int:
    return int(_cfg("", "max_redirects", 10))


def _content_chunk_size() -> int:
    return int(_cfg("", "content_chunk_size", 10240))


def _iter_chunk_size() -> int:
    return int(_cfg("", "iter_chunk_size", 512))


# ── Public API ─────────────────────────────────────────────────────────────────

def content_chunk_size() -> int:
    """Bytes per chunk when iterating response content (mirrors requests CONTENT_CHUNK_SIZE)."""
    return _content_chunk_size()


def iter_chunk_size() -> int:
    """Default chunk size for Response.iter_content(); sourced from args/http_client.yaml."""
    return _iter_chunk_size()


def default_timeout() -> tuple[float, float]:
    """Return (connect_timeout, read_timeout) sourced from args/http_client.yaml."""
    return (_connect_timeout(), _read_timeout())


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


def get_session() -> requests.Session:
    """Return a configured requests.Session with mTLS + CA + proxy + retry applied."""
    session = requests.Session()

    # Retry adapter — thresholds sourced from args/http_client.yaml.
    retry = Retry(
        total=_max_retries(),
        backoff_factor=_backoff_factor(),
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    session.max_redirects = _max_redirects()

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
