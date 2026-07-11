"""Resolve a (possibly constantly-changing) egress proxy for LLM provider calls.

Some environments route LLM traffic through an HTTP(S) proxy whose URL rotates
frequently. The current proxy is resolved FRESH at each call (cheap) so a
rotating value is always applied, from these sources in priority order:

  1. ``ICDEV_LLM_PROXY_CMD`` env / ``proxy.command`` config — a command whose
     stdout is the CURRENT proxy URL. Run per call, cached for
     ``command_ttl_seconds`` (``ICDEV_LLM_PROXY_CMD_TTL``) to bound subprocess
     cost. Best for a proxy supplied by an external rotator/agent.
  2. ``ICDEV_LLM_PROXY`` env — a static-but-updatable URL your rotator writes.
  3. ``proxy.url`` config — a fixed proxy URL.
  4. ``None`` — no ICDEV-specific proxy. The caller then leaves the OS
     ``HTTPS_PROXY``/``HTTP_PROXY`` env untouched (SDKs read it as usual).

:func:`apply_llm_proxy` pushes the resolved value into the standard proxy env
vars so every provider picks it up: ``requests``-based providers (Ollama) read
env per request automatically; SDK clients (Anthropic/OpenAI) read env at
construction, so callers must invalidate a cached client when this reports the
proxy changed — see ``LLMProvider.reset_client``.

Resolving ``None`` never clobbers an existing OS proxy: the feature is fully
opt-in and zero-overhead until one of the ICDEV sources above is set.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Optional

# Env vars we mirror the resolved proxy into (both cases, for maximum coverage).
_PROXY_ENV_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")

_CMD_LOCK = threading.Lock()
_cmd_cache_value: Optional[str] = None
_cmd_cache_at: float = 0.0
# Tracks what we last pushed into the env so apply_llm_proxy can report changes
# and skip redundant writes.
_last_applied: Optional[str] = None


def _run_proxy_command(command: str, ttl: float) -> Optional[str]:
    """Run *command*, returning its stdout (first line) as the proxy URL.

    Cached for *ttl* seconds. Any failure returns the last cached value if
    present, else ``None`` — a broken resolver must never crash an LLM call.
    """
    global _cmd_cache_value, _cmd_cache_at
    now = time.time()
    with _CMD_LOCK:
        if _cmd_cache_value is not None and (now - _cmd_cache_at) < ttl:
            return _cmd_cache_value
    try:
        # nosec B602 — `command` is operator-configured infrastructure (the
        # ICDEV_LLM_PROXY_CMD env / proxy.command config, same trust tier as
        # .env / llm_config.yaml), NOT end-user input. shell=True is intentional
        # so operators can pipe a proxy lookup, e.g. `aws ssm get-parameter … |
        # jq -r .Value`. Bounded by a 10s timeout; output is used only as a
        # proxy URL string, never executed. See sandbox-coverage.md Gap 28.
        out = subprocess.run(  # nosec B602
            command,
            shell=True,  # nosec B602
            capture_output=True,
            text=True,
            timeout=10,
        )
        value = (out.stdout or "").strip().splitlines()
        resolved = value[0].strip() if value else ""
        resolved = resolved or None
    except Exception:
        # Keep whatever we had; degrade to None on the very first failure.
        with _CMD_LOCK:
            return _cmd_cache_value
    with _CMD_LOCK:
        _cmd_cache_value = resolved
        _cmd_cache_at = now
    return resolved


def resolve_llm_proxy(config: dict | None) -> Optional[str]:
    """Return the current LLM egress proxy URL, or ``None`` if none configured.

    Resolved fresh each call (command path is TTL-cached) so a constantly
    changing proxy is always current. Env overrides win over *config*.
    """
    pcfg = {}
    if isinstance(config, dict):
        pcfg = config.get("proxy") or {}

    # 1) Resolver command (env > config).
    command = os.environ.get("ICDEV_LLM_PROXY_CMD", "").strip() or str(pcfg.get("command", "")).strip()
    if command:
        ttl_raw = os.environ.get("ICDEV_LLM_PROXY_CMD_TTL", "").strip()
        if ttl_raw:
            try:
                ttl = float(ttl_raw)
            except ValueError:
                ttl = 2.0
        else:
            try:
                ttl = float(pcfg.get("command_ttl_seconds", 2.0))
            except (TypeError, ValueError):
                ttl = 2.0
        resolved = _run_proxy_command(command, max(0.0, ttl))
        if resolved:
            return resolved

    # 2) Static-but-updatable env URL.
    env_url = os.environ.get("ICDEV_LLM_PROXY", "").strip()
    if env_url:
        return env_url

    # 3) Fixed config URL.
    cfg_url = str(pcfg.get("url", "")).strip()
    if cfg_url:
        return cfg_url

    # 4) Nothing ICDEV-specific — leave the OS proxy env as-is.
    return None


def apply_llm_proxy(proxy: Optional[str]) -> bool:
    """Push *proxy* into the standard proxy env vars. Returns True if it changed.

    ``None`` is a no-op that does NOT clear existing OS proxy env (the feature is
    opt-in). When the value changes, the caller should invalidate cached SDK
    clients so they rebuild against the new proxy.
    """
    global _last_applied
    if proxy is None:
        return False
    if proxy == _last_applied:
        return False
    for var in _PROXY_ENV_VARS:
        os.environ[var] = proxy
    _last_applied = proxy
    return True


def _reset_for_tests() -> None:
    global _cmd_cache_value, _cmd_cache_at, _last_applied
    with _CMD_LOCK:
        _cmd_cache_value = None
        _cmd_cache_at = 0.0
    _last_applied = None
