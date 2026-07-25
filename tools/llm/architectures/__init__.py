"""AGX reasoning-architecture registry — swappable strategies behind one envelope.

Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT,
Copyright (c) 2025 Fareed Khan). ICDEV adapts the uniform
``.run(task) -> ArchitectureResult`` pattern and vendors no upstream code.

Public surface::

    from tools.llm.architectures import (
        run, get, register, list_architectures,
        ArchitectureResult, ArchitectureStep, ArchitectureBudget,
    )

Built-in architectures wrap existing production implementations (nothing is
rebuilt): ``chain_of_thought``, ``chain_of_debate``, ``council`` (from
ChainOrchestrator) and ``react`` (from agent_loop). agx-verify-*, agx-rag-*,
agx-search-* and agx-bench-* register additional architectures here.
"""
from __future__ import annotations

from tools.llm.architectures.envelope import (
    ENVELOPE_SCHEMA_VERSION,
    ArchitectureBudget,
    ArchitectureResult,
    ArchitectureStep,
)
from tools.llm.architectures.registry import (
    ArchitectureNotFound,
    get,
    is_registered,
    list_architectures,
    register,
    run,
    unregister,
)

__all__ = [
    "ENVELOPE_SCHEMA_VERSION",
    "ArchitectureBudget",
    "ArchitectureResult",
    "ArchitectureStep",
    "ArchitectureNotFound",
    "get",
    "is_registered",
    "list_architectures",
    "register",
    "run",
    "unregister",
]
