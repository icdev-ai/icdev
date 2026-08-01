"""Config-driven architecture selection (agx-core-03).

Lets a canvas or router function choose its reasoning architecture in
``args/llm_config.yaml`` rather than in Python, using the agx-core-01 registry.
This is what makes the agx-bench-02 leaderboard actionable: a measured winner
becomes a routing default by editing config, without touching any call site.

Resolution precedence (highest first):

    explicit call arg  >  functions.<function>  >  roles.<role>  >  default

A resolved value of ``None`` means "no architecture indirection" — the caller
keeps its current behavior unchanged. The shipped config sets everything to
``null``, so omitting the key changes nothing (safe default = current behavior).

Config source respects the single-source rule: the ``architectures:`` section
lives in the one file ``resolve_llm_config_path()`` returns, not a sibling file.

LLM-agnostic: this module selects a *strategy name*; it performs no inference and
imports no provider. Model selection still resolves through ``LLMRouter`` /
``args/llm_config.yaml`` inside whichever architecture runs.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Sentinel distinguishing "caller passed no explicit architecture" from
# "caller explicitly passed None to force current behavior". Only an explicit
# non-_UNSET value wins the precedence chain.
_UNSET = object()


def _architectures_config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract the ``architectures:`` block from an llm_config mapping.

    Accepts either a full llm_config dict (with a top-level ``architectures``
    key) or an already-narrowed block. Returns an empty dict when absent, so a
    config without the section behaves as "all defaults / current behavior".
    """
    if not isinstance(config, dict):
        return {}
    if "architectures" in config and isinstance(config["architectures"], dict):
        return config["architectures"]
    # Already-narrowed block (has the expected shape but no wrapper key).
    if any(k in config for k in ("default", "functions", "roles", "log_selections")):
        return config
    return {}


def resolve_architecture(
    *,
    function: Optional[str] = None,
    role: Optional[str] = None,
    explicit: Any = _UNSET,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Resolve the architecture name for a request, or ``None`` for current behavior.

    Args:
        function: ICDEV function name (e.g. ``"code_review"``).
        role:     Routing role key (e.g. ``"cot_reasoner"``).
        explicit: An explicit architecture the caller passed at the call site.
                  Pass a string to force it, or leave unset. Passing ``None``
                  explicitly forces "current behavior" and short-circuits config.
        config:   An llm_config mapping (or its ``architectures`` block). When
                  omitted, resolution falls back to ``default`` only via config
                  being empty — callers normally pass ``router._config``.

    Returns:
        A registered architecture name, or ``None`` meaning the caller should
        keep its existing behavior.
    """
    # Explicit call arg wins — including an explicit None (force current behavior).
    if explicit is not _UNSET:
        return explicit

    block = _architectures_config(config)
    if not block:
        return None

    functions = block.get("functions") or {}
    roles = block.get("roles") or {}

    if function and isinstance(functions, dict) and function in functions:
        return functions.get(function)
    if role and isinstance(roles, dict) and role in roles:
        return roles.get(role)
    return block.get("default")


def log_selection(
    architecture: Optional[str],
    *,
    function: Optional[str] = None,
    role: Optional[str] = None,
    source: str = "",
    config: Optional[Dict[str, Any]] = None,
    trace_id: str = "",
) -> None:
    """Emit a structured record of which architecture served a request.

    The bench (agx-bench-01) consumes these to attribute results to strategies.
    No-op when ``architectures.log_selections`` is false. Never raises — logging
    must not break inference.
    """
    try:
        block = _architectures_config(config)
        if block and block.get("log_selections") is False:
            return
        logger.info(
            "agx_architecture_selected",
            extra={
                "extra": {
                    "event": "agx_architecture_selected",
                    "architecture": architecture or "current_behavior",
                    "function": function or "",
                    "role": role or "",
                    "source": source,
                    "trace_id": trace_id,
                }
            },
        )
    except Exception:  # logging must never break the request path
        pass


def resolve_and_log(
    *,
    function: Optional[str] = None,
    role: Optional[str] = None,
    explicit: Any = _UNSET,
    config: Optional[Dict[str, Any]] = None,
    trace_id: str = "",
) -> Optional[str]:
    """Convenience: resolve the architecture and emit the selection log in one call."""
    arch = resolve_architecture(function=function, role=role, explicit=explicit, config=config)
    # Determine which precedence tier won, for the bench's attribution.
    if explicit is not _UNSET:
        source = "explicit"
    else:
        block = _architectures_config(config)
        functions = (block.get("functions") or {}) if block else {}
        roles = (block.get("roles") or {}) if block else {}
        if function and function in functions:
            source = "function"
        elif role and role in roles:
            source = "role"
        else:
            source = "default"
    log_selection(arch, function=function, role=role, source=source, config=config, trace_id=trace_id)
    return arch
