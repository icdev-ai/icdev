#!/usr/bin/env python3
# [TEMPLATE: CUI // SP-CTI]
"""Air-gap environment detector.

Determines whether ICDEV™ is running in an air-gapped environment by
probing network connectivity, cloud provider availability, and local
LLM server presence.  All probes are best-effort with short timeouts.

Supports any local LLM server: Ollama, vLLM, llama.cpp, LM Studio,
LocalAI, TGI, or any OpenAI-compatible endpoint on localhost.

Usage::

    from tools.airgap.detector import is_airgap, detect_environment

    if is_airgap():
        print("Air-gapped — local models only")

    env = detect_environment()
    print(env)  # {"airgap": True, "local_llm": [...], "claude_code": False, ...}
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
import re
import shutil
import socket
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

logger = get_logger("icdev.airgap.detector")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Hostnames / IP patterns that indicate a local/private endpoint
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}  # nosec B104 -- intentional bind-all for containerized/dev deployment
_PRIVATE_IP_RE = re.compile(r"^(10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)$")

# ── Cache ──────────────────────────────────────────────────────────────
_cached_result: Dict[str, Any] | None = None


def is_local_url(url: str) -> bool:
    """Return True if url points to localhost or a private-network address.

    Covers: localhost, 127.x, 10.x, 172.16-31.x, 192.168.x, ::1.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return host in _LOCAL_HOSTS or bool(_PRIVATE_IP_RE.match(host))
    except Exception:
        return False


def _probe_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    """Test TCP connectivity to host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _probe_url(url: str, timeout: float = 1.5) -> bool:
    """Probe a URL's host:port via TCP."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return _probe_tcp(host, port, timeout=timeout)
    except Exception:
        return False


def _probe_ollama(base_url: str = "") -> bool:
    """Check if Ollama is running locally."""
    url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    return _probe_url(url, timeout=1.0)


def probe_local_llm_servers() -> List[Dict[str, Any]]:
    """Discover all reachable local LLM servers.

    Checks well-known ports/env vars for:
    - Ollama (11434)
    - vLLM (8000)
    - llama.cpp server (8080)
    - LM Studio (1234)
    - LocalAI (8080)
    - TGI (8080)
    - Custom endpoints from env vars

    Returns list of {"name": str, "url": str, "reachable": bool}.
    """
    # Well-known local LLM server endpoints
    candidates = [
        ("ollama", os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")),
        ("vllm", os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1")),
        ("llama_cpp", os.environ.get("LLAMA_CPP_BASE_URL", "http://localhost:8080/v1")),
        ("lm_studio", os.environ.get("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")),
        # D1: LocalAI's upstream default is 8080 (same as llama.cpp — only one runs
        # there at a time). The previous 8081 dodged that collision but meant a
        # stock LocalAI install was never detected. Probe its real default; users on
        # a non-default port set LOCALAI_BASE_URL.
        ("localai", os.environ.get("LOCALAI_BASE_URL", "http://localhost:8080/v1")),
        ("tgi", os.environ.get("TGI_BASE_URL", "http://localhost:8082")),
    ]

    # Also check any openai_compatible providers from llm_config.yaml
    # that have local base_urls
    try:
        import yaml

        cfg_path = BASE_DIR / "args" / "llm_config.yaml"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            for pname, pcfg in cfg.get("providers", {}).items():
                base = pcfg.get("base_url", "")
                # Expand env vars
                if "${" in base:
                    import re as _re

                    for m in _re.finditer(r"\$\{([^}]+)\}", base):
                        expr = m.group(1)
                        var, _, default = expr.partition(":-")
                        base = base.replace(m.group(0), os.environ.get(var, default))
                if base and is_local_url(base):
                    # Avoid duplicates
                    if not any(c[1].rstrip("/") == base.rstrip("/") for c in candidates):
                        candidates.append((f"config:{pname}", base))
    except Exception:
        pass

    results = []
    seen_ports = set()
    for name, url in candidates:
        try:
            parsed = urlparse(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            # Skip duplicate probes to same host:port
            key = f"{parsed.hostname}:{port}"
            if key in seen_ports:
                continue
            seen_ports.add(key)
        except Exception:
            pass

        reachable = _probe_url(url, timeout=1.0)
        results.append({"name": name, "url": url, "reachable": reachable})

    return results


def _probe_claude_code_cli() -> bool:
    """Check if Claude Code CLI is available on PATH."""
    return shutil.which("claude") is not None


def _probe_claude_code_env() -> bool:
    """Check if running inside a Claude Code session."""
    return bool(
        os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CLAUDE_CODE")
    )


def _probe_anthropic_api() -> bool:
    """Check if Anthropic API is reachable (TCP only, no auth)."""
    return _probe_tcp("api.anthropic.com", 443, timeout=3.0)


def _probe_bedrock() -> bool:
    """Check if AWS Bedrock endpoint is reachable (TCP only)."""
    region = os.environ.get("AWS_DEFAULT_REGION", "us-gov-west-1")
    host = f"bedrock-runtime.{region}.amazonaws.com"
    return _probe_tcp(host, 443, timeout=3.0)


def _check_cloud_config() -> bool:
    """Check if cloud_config.yaml explicitly declares air-gapped mode."""
    try:
        import yaml
    except ImportError:
        return False
    cfg_path = BASE_DIR / "args" / "cloud_config.yaml"
    if not cfg_path.exists():
        return False
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        env = cfg.get("environment", {})
        return env.get("air_gapped", False) is True or str(env.get("cloud_mode", "")).lower() == "air_gapped"
    except Exception:
        return False


def detect_environment(use_cache: bool = True) -> Dict[str, Any]:
    """Probe all connectivity vectors and return detailed environment map.

    Returns:
        Dict with keys: airgap, local_llm_servers, ollama,
        claude_code_cli, claude_code_env, anthropic_api, bedrock,
        cloud_config_airgap, env_var_forced.
    """
    global _cached_result
    if use_cache and _cached_result is not None:
        return _cached_result

    # Env var override: ICDEV_AIRGAP=true forces air-gap mode
    env_forced = os.environ.get("ICDEV_AIRGAP", "").lower() in ("true", "1", "yes")

    # Discover all local LLM servers (Ollama, vLLM, llama.cpp, LM Studio, etc.)
    local_servers = probe_local_llm_servers()
    reachable_servers = [s for s in local_servers if s["reachable"]]

    ollama = _probe_ollama()
    claude_cli = _probe_claude_code_cli()
    claude_env = _probe_claude_code_env()
    cloud_cfg = _check_cloud_config()

    # Only probe cloud if not already forced
    if env_forced or cloud_cfg:
        anthropic = False
        bedrock = False
    else:
        anthropic = _probe_anthropic_api()
        bedrock = _probe_bedrock()

    # Air-gap determination:
    # 1. Explicit env var or cloud config → air-gapped
    # 2. No cloud APIs reachable AND no Claude Code → air-gapped
    # 3. At least one cloud API reachable → not air-gapped
    if env_forced or cloud_cfg:
        is_ag = True
    elif not anthropic and not bedrock:
        is_ag = True
    else:
        is_ag = False

    result = {
        "airgap": is_ag,
        "local_llm_servers": reachable_servers,
        "local_llm_count": len(reachable_servers),
        "ollama": ollama,
        "claude_code_cli": claude_cli,
        "claude_code_env": claude_env,
        "anthropic_api": anthropic,
        "bedrock": bedrock,
        "cloud_config_airgap": cloud_cfg,
        "env_var_forced": env_forced,
    }

    _cached_result = result
    logger.info("Environment detection: %s", result)
    return result


def is_airgap(use_cache: bool = True) -> bool:
    """Quick check: is ICDEV™ running in air-gapped mode?

    Priority:
    1. ICDEV_AIRGAP env var (explicit override)
    2. cloud_config.yaml air_gapped flag
    3. Network probe (Anthropic + Bedrock unreachable)
    """
    return detect_environment(use_cache=use_cache)["airgap"]


def clear_cache() -> None:
    """Clear the cached detection result (e.g. after network change)."""
    global _cached_result
    _cached_result = None


# ── Vendored driver detection ──────────────────────────────────────────

def has_vendored_driver(driver_type: str = "any") -> bool:
    """Return True if at least one vendored browser driver binary exists.

    Parameters
    ----------
    driver_type:
        ``"edge"``      — check only msedgedriver vendor tree
        ``"chrome"``    — check only chromedriver vendor tree
        ``"any"``       — True if either tree contains a binary (default)

    The vendor tree lives at ``vendor/drivers/{msedgedriver,chromedriver}/{major}/``
    relative to the project root.  A directory is considered populated when
    the platform-appropriate executable is present inside a versioned sub-dir.
    """
    import platform as _platform

    exe_suffix = ".exe" if _platform.system() == "Windows" else ""
    vendor_root = BASE_DIR / "vendor" / "drivers"

    def _has_binary(driver_dir_name: str, exe_name: str) -> bool:
        driver_dir = vendor_root / driver_dir_name
        if not driver_dir.exists():
            return False
        for sub in driver_dir.iterdir():
            if sub.is_dir():
                if (sub / exe_name).exists():
                    return True
        return False

    check_edge = driver_type in ("edge", "any")
    check_chrome = driver_type in ("chrome", "any")

    if check_edge and _has_binary("msedgedriver", f"msedgedriver{exe_suffix}"):
        return True
    if check_chrome and _has_binary("chromedriver", f"chromedriver{exe_suffix}"):
        return True
    return False


# ── CLI ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys

    env = detect_environment(use_cache=False)
    print(json.dumps(env, indent=2))
    sys.exit(0 if not env["airgap"] else 2)
