# CUI // SP-CTI
"""OPT-71: tools.agents — unified agent adapter registry.

Adapted from jonwiggins/optio packages/agent-adapters (MIT).
See https://github.com/jonwiggins/optio

This package provides a formal AgentAdapter Protocol and a registry
that lets any orchestrator (kanban reflex, pr_watcher, CI hook) pick
an agent backend without hard-coding which one.
"""
from tools.agents.adapter_base import (  # noqa: F401
    AgentAdapter,
    AgentSession,
    AgentResult,
    NotInstalledError,
)
from tools.agents.registry import (  # noqa: F401
    get_adapter,
    list_adapters,
    pick_default,
    detect_available,
)
from tools.agents.capability_matrix import (  # noqa: F401
    CAPABILITIES,
    adapters_with,
    build_matrix,
    capability_status,
    supports,
)
