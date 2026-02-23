# [TEMPLATE: CUI // SP-CTI]
# ICDEV Air-Gap Detector — network connectivity probe for webhook vs poll mode

"""
Detect network connectivity to determine webhook vs polling mode.

Uses only Python stdlib (socket) for air-gap safety. When the environment
is air-gapped, CI/CD falls back to polling triggers instead of webhooks.

Architecture Decision D44: Flag-based backward compatibility —
ICDEV_FORCE_POLLING=true overrides detection.

Usage:
    from tools.ci.core.air_gap_detector import detect_connectivity
    result = detect_connectivity()
    # {"mode": "webhook", "can_reach_github": True, "can_reach_gitlab": True}
"""

import os
import socket
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "args" / "cicd_config.yaml"


def _load_config() -> dict:
    """Load connectivity config from cicd_config.yaml."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                data = yaml.safe_load(f) or {}
            return data.get("cicd", {}).get("connectivity", {})
        except Exception:
            pass
    return {}


def _probe_host(host_port: str, timeout: int = 3) -> bool:
    """TCP connect probe to host:port. Returns True if reachable."""
    try:
        parts = host_port.rsplit(":", 1)
        host = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 443
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except (socket.timeout, socket.error, OSError, ValueError):
        return False


def detect_connectivity(gitlab_url: str = "") -> dict:
    """Detect network connectivity for webhook vs poll mode selection.

    Args:
        gitlab_url: Override GitLab instance URL (e.g., "gitlab.org.mil:443")

    Returns:
        {
            "mode": "webhook" | "polling",
            "can_reach_github": bool,
            "can_reach_gitlab": bool,
            "reason": str,
        }
    """
    # D44: Environment variable override
    if os.getenv("ICDEV_FORCE_POLLING", "").lower() in ("true", "1", "yes"):
        return {
            "mode": "polling",
            "can_reach_github": False,
            "can_reach_gitlab": False,
            "reason": "ICDEV_FORCE_POLLING is set",
        }

    config = _load_config()

    if config.get("force_polling", False):
        return {
            "mode": "polling",
            "can_reach_github": False,
            "can_reach_gitlab": False,
            "reason": "force_polling is true in cicd_config.yaml",
        }

    timeout = config.get("probe_timeout_seconds", 3)
    targets = config.get("probe_targets", {})

    github_target = targets.get("github", "github.com:443")
    gitlab_target = gitlab_url or targets.get("gitlab_default", "gitlab.com:443")

    can_reach_github = _probe_host(github_target, timeout)
    can_reach_gitlab = _probe_host(gitlab_target, timeout)

    if can_reach_github or can_reach_gitlab:
        reachable = []
        if can_reach_github:
            reachable.append("GitHub")
        if can_reach_gitlab:
            reachable.append("GitLab")
        return {
            "mode": "webhook",
            "can_reach_github": can_reach_github,
            "can_reach_gitlab": can_reach_gitlab,
            "reason": f"Can reach: {', '.join(reachable)}",
        }

    return {
        "mode": "polling",
        "can_reach_github": False,
        "can_reach_gitlab": False,
        "reason": "Cannot reach GitHub or GitLab — air-gapped environment detected",
    }
