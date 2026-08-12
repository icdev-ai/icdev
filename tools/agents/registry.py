# CUI // SP-CTI
"""OPT-71: tools/agents/registry.py — adapter discovery + selection.

OPT-71 registry pattern inspired by jonwiggins/optio (MIT).
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
import pathlib
from typing import Dict, List, Optional, Sequence

import yaml

from tools.agents.adapter_base import AgentAdapter, NotInstalledError


logger = get_logger(__name__)
ROOT = pathlib.Path(__file__).resolve().parents[2]
_CONFIG_PATH = ROOT / "args" / "agent_adapters.yaml"


def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {
            "default_adapter": "auto",
            "enabled_adapters": ["claude_cli", "local_llm_router"],
            "per_task_type_preference": {},
            "fallback_order": ["claude_cli", "local_llm_router"],
        }
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("agent adapters config parse failed: %s", exc)
        return {}


_REGISTRY: Dict[str, AgentAdapter] = {}


def _ensure_loaded() -> None:
    if _REGISTRY:
        return
    # Lazy import so a broken adapter in the tree doesn't kill the whole
    # registry. Import failures log a warning and continue.
    for name, module in (
        ("claude_cli", "tools.agents.adapters.claude_cli"),
        ("local_llm_router", "tools.agents.adapters.local_llm_router"),
        ("local_agent", "tools.agents.adapters.local_agent"),
        ("codex_cli", "tools.agents.adapters.codex_cli"),
        ("copilot_cli", "tools.agents.adapters.copilot_cli"),
    ):
        try:
            mod = __import__(module, fromlist=["ADAPTER"])
            adapter = getattr(mod, "ADAPTER", None)
            if adapter is not None:
                _REGISTRY[name] = adapter
        except Exception as exc:
            logger.warning("agent adapter %s failed to load: %s", name, exc)


def reset() -> None:
    """Clear the registry — tests use this to force re-loading.

    The capability matrix is memoised per adapter object, so it has to go with
    it: a matrix describing adapters that are no longer registered is exactly
    the kind of stale-but-plausible answer this seam keeps producing.
    """
    _REGISTRY.clear()
    try:
        from tools.agents import capability_matrix  # noqa: PLC0415 — cycle

        capability_matrix.reset_cache()
    except Exception:  # noqa: BLE001 — reset must never raise
        pass


def list_adapters() -> List[str]:
    """Return all registered adapter names (available or not)."""
    _ensure_loaded()
    return sorted(_REGISTRY.keys())


def get_adapter(name: str) -> AgentAdapter:
    """Return the adapter for `name`. Raises KeyError if unknown."""
    _ensure_loaded()
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown agent adapter: {name!r}. "
            f"Registered: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]


def detect_available() -> List[str]:
    """Return the names of all adapters whose `available()` is True."""
    _ensure_loaded()
    out: List[str] = []
    for name, adapter in _REGISTRY.items():
        try:
            if adapter.available():
                out.append(name)
        except Exception as exc:
            logger.debug("adapter %s availability check failed: %s", name, exc)
    return out


def _capability_filter(require: Optional[Sequence[str]]):
    """Return a predicate over adapter names for the ``require`` set.

    With nothing required the predicate is a constant True and selection is
    byte-identical to what it was before exa-bench-03 — no probe runs, nothing
    is imported, no existing caller changes behaviour.

    The import is local because ``capability_matrix`` imports this module.
    """
    caps = [c for c in (require or []) if c]
    if not caps:
        return lambda _name: True

    from tools.agents import capability_matrix  # noqa: PLC0415 — cycle

    def _ok(name: str) -> bool:
        try:
            return capability_matrix.supports(name, caps)
        except Exception as exc:  # noqa: BLE001 — a failed probe never promotes
            logger.warning(
                "capability probe failed for %s (%s) — treating the "
                "requirement as unmet", name, exc,
            )
            return False

    return _ok


def pick_default(
    task_type: Optional[str] = None,
    config: Optional[dict] = None,
    require: Optional[Sequence[str]] = None,
) -> AgentAdapter:
    """Pick the best available adapter for the given task type.

    Precedence:
        1. Env var ICDEV_AGENT_ADAPTER — explicit forced choice
        2. config['per_task_type_preference'][task_type] if available
        3. config['fallback_order'] walked in order — first that is
           available() + enabled
        4. First adapter in `detect_available()` as a last resort

    ``require`` is an optional list of capability names from
    ``tools.agents.capability_matrix.CAPABILITIES``. When given, a candidate is
    skipped unless the probe MEASURED every one of them as ``present`` — a
    capability that is merely declared, or that the probe could not confirm,
    does not qualify. This is what makes adapter selection consult a
    measurement instead of an assumption; leaving it unset preserves the old
    behaviour exactly.

    ``ICDEV_AGENT_ADAPTER`` still wins over ``require``. An operator forcing an
    adapter has said something more specific than a capability filter, and
    silently overriding them is the "control that looks like it worked" failure
    this codebase keeps producing. The mismatch is logged at WARNING instead.

    Raises NotInstalledError if nothing is available.
    """
    _ensure_loaded()
    cfg = config or _load_config()
    enabled = set(cfg.get("enabled_adapters") or list(_REGISTRY.keys()))
    meets = _capability_filter(require)

    forced = os.environ.get("ICDEV_AGENT_ADAPTER", "").strip()
    if forced:
        if forced not in _REGISTRY:
            raise KeyError(
                f"ICDEV_AGENT_ADAPTER={forced!r} is not a registered adapter"
            )
        if require and not meets(forced):
            logger.warning(
                "ICDEV_AGENT_ADAPTER=%r does not measure present for every "
                "required capability %s — honouring the override anyway",
                forced, list(require),
            )
        return _REGISTRY[forced]

    per_task = cfg.get("per_task_type_preference") or {}
    if task_type and task_type in per_task:
        candidate = per_task[task_type]
        if (candidate in _REGISTRY
                and candidate in enabled
                and meets(candidate)
                and _REGISTRY[candidate].available()):
            return _REGISTRY[candidate]

    for name in cfg.get("fallback_order") or []:
        if name not in _REGISTRY or name not in enabled or not meets(name):
            continue
        try:
            if _REGISTRY[name].available():
                return _REGISTRY[name]
        except Exception:
            continue

    available_names = detect_available()
    for name in available_names:
        if name in enabled and meets(name):
            return _REGISTRY[name]

    if require:
        raise NotInstalledError(
            "No agent adapter on this host is both available and measured "
            f"present for every required capability {list(require)}. Run "
            "`python tools/agents/capability_matrix.py` to see what each "
            "adapter actually supports."
        )
    raise NotInstalledError(
        "No agent adapter is available on this host. Install Claude "
        "Code CLI or configure LLMRouter."
    )
