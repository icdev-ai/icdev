# CUI // SP-CTI
"""Curated MCP toolset profiles for external-agent consumption (SAG sag-mcp-01).

``tools/mcp/unified_server.py`` exposes 447+ ICDEV tools over stdio MCP. Small
local models (kimi/ollama) with limited context cannot handle that surface, so
this module loads named, bounded profiles from ``args/mcp_toolset_profiles.yaml``
and resolves a profile name to a validated set of tool names the server should
register.

It also encodes the CUI-egress decision (mirroring the CLI-bridge rules): a
``local_only`` profile may surface tools that emit CUI / classification-bearing
artifacts and must not be exposed to an external agent running on a cloud LLM
unless explicitly overridden. :func:`enforce_cui_egress` performs that gate.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("mcp.toolset_profiles")

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_PROFILE_PATH = _BASE_DIR / "args" / "mcp_toolset_profiles.yaml"

# Providers whose api_key_env implies CUI leaves the local trust boundary.
# Mirrors the CLI-bridge distinction (ollama = local, everything else = cloud).
_LOCAL_PROVIDER_MARKERS = ("ollama",)


class ToolsetProfileError(ValueError):
    """Raised when a requested profile is unknown or egress policy blocks it."""


def _profile_path() -> Path:
    override = os.environ.get("ICDEV_MCP_TOOLSET_PROFILES")
    return Path(override) if override else _PROFILE_PATH


def load_profiles() -> dict[str, dict[str, Any]]:
    """Load and return the ``{name: profile_dict}`` mapping (empty on error)."""
    path = _profile_path()
    if not path.exists():
        logger.warning("toolset_profiles: %s not found", path)
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("toolset_profiles: failed to parse %s: %s", path, exc)
        return {}
    profiles = data.get("profiles") or {}
    if not isinstance(profiles, dict):
        logger.warning("toolset_profiles: 'profiles' is not a mapping")
        return {}
    return profiles


def list_profiles() -> list[dict[str, Any]]:
    """Return a summary list of profiles for ``--list-toolsets``."""
    out = []
    for name, prof in sorted(load_profiles().items()):
        tools = prof.get("tools") or []
        out.append(
            {
                "name": name,
                "description": (prof.get("description") or "").strip(),
                "cui_egress": prof.get("cui_egress", "cloud_safe"),
                "tool_count": len(tools),
                "tools": list(tools),
            }
        )
    return out


def resolve_toolset(
    name: str, *, registry_names: set[str] | None = None
) -> set[str]:
    """Resolve ``name`` to the set of tool names to register.

    Args:
        name: Profile name from ``args/mcp_toolset_profiles.yaml``.
        registry_names: Optional set of valid tool names (from ``TOOL_REGISTRY``).
            When provided, profile entries not present are dropped with a warning
            so a registry rename never crashes the server.

    Raises:
        ToolsetProfileError: if the profile name is unknown.
    """
    profiles = load_profiles()
    prof = profiles.get(name)
    if prof is None:
        raise ToolsetProfileError(
            f"Unknown toolset profile {name!r}. Available: "
            f"{', '.join(sorted(profiles)) or '(none)'}"
        )
    requested = set(prof.get("tools") or [])
    if registry_names is not None:
        missing = requested - registry_names
        if missing:
            logger.warning(
                "toolset_profiles: profile %r references %d unknown tool(s): %s",
                name,
                len(missing),
                ", ".join(sorted(missing)),
            )
        requested &= registry_names
    return requested


def _cloud_provider_active() -> bool:
    """Best-effort detection of whether the active LLM provider is cloud-based.

    Mirrors the CLI-bridge heuristic: if the resolved provider / api_key_env for
    the standalone agent is a local marker (ollama) it is local; otherwise treat
    it as cloud (fail-safe toward "cloud" when unknown).
    """
    provider = (os.environ.get("ICDEV_LLM_PROVIDER") or "").strip().lower()
    if provider:
        return not any(m in provider for m in _LOCAL_PROVIDER_MARKERS)
    key_env = (os.environ.get("ICDEV_AGENT_API_KEY_ENV") or "").strip().lower()
    if key_env:
        return not any(m in key_env for m in _LOCAL_PROVIDER_MARKERS)
    # Unknown provider: fail safe — assume cloud so local_only profiles are gated.
    return True


def enforce_cui_egress(name: str) -> None:
    """Gate a ``local_only`` profile against a cloud LLM.

    Raises:
        ToolsetProfileError: if the profile is ``local_only`` and a cloud
            provider is active without the ``ICDEV_MCP_ALLOW_CLOUD_CUI`` override.
    """
    prof = load_profiles().get(name) or {}
    egress = str(prof.get("cui_egress", "cloud_safe")).lower()
    if egress != "local_only":
        return
    if os.environ.get("ICDEV_MCP_ALLOW_CLOUD_CUI", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        logger.warning(
            "toolset_profiles: local_only profile %r exposed on cloud provider "
            "via ICDEV_MCP_ALLOW_CLOUD_CUI override",
            name,
        )
        return
    if _cloud_provider_active():
        raise ToolsetProfileError(
            f"Toolset profile {name!r} is CUI local-only and a cloud LLM provider "
            "is active. Refusing to expose CUI-capable tools to a cloud agent. "
            "Set ICDEV_LLM_PROVIDER=ollama (local) or, if you accept the risk, "
            "ICDEV_MCP_ALLOW_CLOUD_CUI=1."
        )
