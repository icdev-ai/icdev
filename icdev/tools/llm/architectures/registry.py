"""Registry of named AGX reasoning architectures behind one uniform interface.

Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT,
Copyright (c) 2025 Fareed Khan). ICDEV adapts the *pattern* — a swappable
registry of ``.run(task) -> ArchitectureResult`` strategies — and vendors no
upstream code.

Every architecture is a callable with the signature::

    run(task, *, router=None, budget=None, function="architecture_run", **kwargs)
        -> ArchitectureResult

``task`` may be a plain ``str`` (used as the user prompt) or an ``LLMRequest``.
The uniform envelope (:class:`ArchitectureResult`) is what lets any canvas or
router function change reasoning strategy by *config, not code* (agx-core-03),
and lets the benchmark suite (agx-bench-01) grade strategies against each other.

LLM-agnostic by construction: this module performs no inference itself. All
adapters route through ``LLMRouter`` — there are zero vendor-SDK imports and no
hardcoded model IDs anywhere in this package.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from tools.llm.architectures.envelope import ArchitectureBudget, ArchitectureResult

# An architecture is any callable returning an ArchitectureResult. We keep the
# type intentionally loose (``Callable[..., ArchitectureResult]``) because
# adapters accept keyword-only router/budget/function plus pattern-specific
# kwargs.
ArchitectureFn = Callable[..., ArchitectureResult]

_REGISTRY: Dict[str, ArchitectureFn] = {}


class ArchitectureNotFound(KeyError):
    """Raised when a requested architecture name is not registered."""


def register(name: str, fn: ArchitectureFn, *, overwrite: bool = False) -> None:
    """Register ``fn`` under ``name``.

    Args:
        name: Unique architecture name (e.g. ``"chain_of_thought"``).
        fn:   Callable implementing the uniform run interface.
        overwrite: If False (default), re-registering an existing name raises
            ValueError — guards against two modules silently claiming one name.
    """
    if not name or not isinstance(name, str):
        raise ValueError("architecture name must be a non-empty string")
    if not callable(fn):
        raise ValueError(f"architecture '{name}' must be callable")
    if name in _REGISTRY and not overwrite:
        raise ValueError(
            f"architecture '{name}' already registered; pass overwrite=True to replace"
        )
    _REGISTRY[name] = fn


def unregister(name: str) -> None:
    """Remove ``name`` from the registry if present (idempotent)."""
    _REGISTRY.pop(name, None)


def get(name: str) -> ArchitectureFn:
    """Return the callable registered under ``name`` or raise ArchitectureNotFound."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise ArchitectureNotFound(
            f"architecture '{name}' not registered; known: {sorted(_REGISTRY)}"
        ) from exc


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def list_architectures() -> List[str]:
    """Return the sorted list of registered architecture names."""
    return sorted(_REGISTRY)


def run(
    name: str,
    task,
    *,
    router=None,
    budget: Optional[ArchitectureBudget] = None,
    function: str = "architecture_run",
    **kwargs,
) -> ArchitectureResult:
    """Resolve ``name`` and invoke it against ``task`` with the uniform interface.

    A thin dispatch helper so callers do not need to touch the registry dict.
    """
    fn = get(name)
    return fn(task, router=router, budget=budget, function=function, **kwargs)


def _ensure_builtin_adapters_registered() -> None:
    """Import the adapters module so built-in architectures self-register.

    Kept lazy and idempotent to avoid an import cycle at package import time
    (adapters import from this module).
    """
    from tools.llm.architectures import adapters  # noqa: F401  (registers on import)
    from tools.llm.architectures import cove  # noqa: F401  (registers chain_of_verification)
    from tools.llm.architectures import self_discover  # noqa: F401  (registers self_discover)
    from tools.llm.architectures import tree_of_thoughts  # noqa: F401  (registers tree_of_thoughts)


# Register the built-in wrapped architectures on first import of this module.
_ensure_builtin_adapters_registered()
