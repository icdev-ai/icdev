# CUI // SP-CTI
"""FathomDesk per-agent LLM provider override factory.

Wraps the ICDEV LLMRouter so each FathomDesk analyst agent can declare a
preferred provider without touching router internals.

Usage::

    from tools.fathomdesk.llm_factory import get_llm

    llm = get_llm("news_analyst")
    response = llm(request)  # LLMRequest → LLMResponse

Config (args/fathomdesk_config.yaml)::

    agent_llm_overrides:
      news_analyst: "ollama_function_key"   # route via this LLM function key
      quant_analyst: "code_generation"

If a key is absent the agent name itself is passed as the router function key,
which LLMRouter resolves via its normal fallback chain.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Callable

import yaml

from tools.llm.router import LLMRouter

_CONFIG_PATH = Path(__file__).parent.parent.parent / "args" / "fathomdesk_config.yaml"
_router: LLMRouter | None = None


def _router_instance() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


def _load_overrides() -> dict[str, str]:
    """Return agent_llm_overrides from fathomdesk_config.yaml, or {} if absent."""
    if not _CONFIG_PATH.exists():
        return {}
    with _CONFIG_PATH.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return cfg.get("agent_llm_overrides", {})


def get_llm(agent_name: str) -> Callable:
    """Return a callable ``(LLMRequest) -> LLMResponse`` for *agent_name*.

    Resolution order:
    1. ``args/fathomdesk_config.yaml`` → ``agent_llm_overrides[agent_name]``
       (value is a router function key, e.g. ``"code_generation"``)
    2. LLMRouter default — *agent_name* used directly as the function key.
    """
    overrides = _load_overrides()
    fn_key = overrides.get(agent_name, agent_name)
    return functools.partial(_router_instance().invoke, fn_key)
