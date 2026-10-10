# CUI // SP-CTI
"""OPT-71: tools/agents/registry.py — adapter discovery + selection.

OPT-71 registry pattern inspired by jonwiggins/optio (MIT).
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import functools
import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass
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
            "fallback_order": ["opencode_cli", "claude_cli", "local_llm_router"],
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
        ("goose_cli", "tools.agents.adapters.goose_cli"),
        ("opencode_cli", "tools.agents.adapters.opencode_cli"),
        ("pi_cli", "tools.agents.adapters.pi_cli"),
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
    _omarchy_default_agent.cache_clear()
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


# omx-select-01 -- where a selection came from, for logs.
SOURCE_ENV = "env"
SOURCE_OMARCHY = "omarchy"
SOURCE_PREFERENCE = "preference"
SOURCE_FALLBACK = "fallback"


@dataclass(frozen=True)
class AdapterSelection:
    """The adapter ``select_adapter`` chose, and which rung chose it."""

    adapter: AgentAdapter
    source: str  # env | omarchy | preference | fallback


@functools.lru_cache(maxsize=1)
def _omarchy_default_agent() -> str:
    """The Omarchy desktop's default coding agent, or "" when there is none.

    Read through ``omarchy default agent`` with no argument, which prints the
    name stored in ``~/.config/omarchy/defaults/agent`` and prints NOTHING when
    no default is set (Omarchy picks none for you). Off Omarchy -- no
    ``omarchy`` on PATH, or any failure running it -- this is "" and never an
    error. Cached for the process lifetime; ``reset()`` clears it.
    """
    exe = shutil.which("omarchy")
    if not exe:
        return ""
    try:
        proc = subprocess.run(
            [exe, "default", "agent"],
            capture_output=True, text=True, encoding="utf-8",
            timeout=5, check=False,
        )
    except Exception as exc:  # noqa: BLE001 -- not on a working Omarchy
        logger.debug("omarchy default agent read failed: %s", exc)
        return ""
    if proc.returncode != 0:
        return ""
    lines = (proc.stdout or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def select_adapter(
    task_type: Optional[str] = None,
    config: Optional[dict] = None,
    require: Optional[Sequence[str]] = None,
) -> AdapterSelection:
    """Pick the best available adapter and say which rung picked it.

    Precedence:
        1. Env var ICDEV_AGENT_ADAPTER — explicit forced choice   (``env``)
        2. The Omarchy desktop default (``omarchy default agent``), mapped
           to an adapter through config['omarchy_agent_map'] — an unmapped,
           disabled or unavailable name falls through          (``omarchy``)
        3. config['per_task_type_preference'][task_type] if available
                                                              (``preference``)
        4. config['fallback_order'] walked in order — first that is
           available() + enabled                                (``fallback``)
        5. First adapter in `detect_available()` as a last resort
                                                                (``fallback``)

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

    A ``config`` without ``omarchy_agent_map`` skips rung 2 entirely — that is
    how the kanban runner keeps its executor chain authoritative.

    Raises NotInstalledError if nothing is available.
    """
    _ensure_loaded()
    cfg = config or _load_config()
    enabled = set(cfg.get("enabled_adapters") or list(_REGISTRY.keys()))
    meets = _capability_filter(require)

    def _usable(name: str) -> bool:
        if name not in _REGISTRY or name not in enabled or not meets(name):
            return False
        try:
            return bool(_REGISTRY[name].available())
        except Exception:  # noqa: BLE001 — a failing probe is "not available"
            return False

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
        return AdapterSelection(_REGISTRY[forced], SOURCE_ENV)

    omarchy_map = cfg.get("omarchy_agent_map") or {}
    if omarchy_map:
        desktop = _omarchy_default_agent()
        candidate = omarchy_map.get(desktop) if desktop else None
        if candidate and _usable(candidate):
            return AdapterSelection(_REGISTRY[candidate], SOURCE_OMARCHY)
        if desktop:
            logger.debug(
                "omarchy default agent %r -> %r is not usable here; falling "
                "through", desktop, candidate,
            )

    per_task = cfg.get("per_task_type_preference") or {}
    if task_type and task_type in per_task:
        candidate = per_task[task_type]
        if _usable(candidate):
            return AdapterSelection(_REGISTRY[candidate], SOURCE_PREFERENCE)

    for name in cfg.get("fallback_order") or []:
        if _usable(name):
            return AdapterSelection(_REGISTRY[name], SOURCE_FALLBACK)

    available_names = detect_available()
    for name in available_names:
        if name in enabled and meets(name):
            return AdapterSelection(_REGISTRY[name], SOURCE_FALLBACK)

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


def pick_default(
    task_type: Optional[str] = None,
    config: Optional[dict] = None,
    require: Optional[Sequence[str]] = None,
) -> AgentAdapter:
    """``select_adapter(...).adapter`` — see there for the precedence.

    Kept as the adapter-returning entry point every existing caller uses; the
    selection's ``source`` is logged here.
    """
    selection = select_adapter(task_type, config=config, require=require)
    logger.debug(
        "agent adapter selected: %s (source=%s, task_type=%s)",
        getattr(selection.adapter, "name", "?"), selection.source, task_type,
    )
    return selection.adapter
