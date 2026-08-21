"""ICDEV™ -- Intelligent Certified Development Platform.

A system that builds systems. AI-powered meta-builder for generating
complete, autonomous, ATO-ready applications with 42 compliance framework
mappings, 15 coordinating AI agents, and the FORGE framework.

FORGE layers:
  G - Goals     (workflow definitions)
  O - Orchestration (AI agent layer)
  T - Tools     (deterministic Python scripts)
  C - Context   (reference material)
  H - Hardprompts (reusable LLM instruction templates)
  A - Args      (YAML/JSON behavior settings)
"""

import importlib
import importlib.util
import sys

from icdev._version import __version__

__all__ = ["__version__"]


def _install_checkout_alias(tools_init: "str | None") -> None:
    """Source checkout: make ``icdev.tools.X`` the same object as ``tools.X``.

    xit-decl-02. ``tools/__init__.py`` installs the same finder, but whichever
    of the two packages is imported FIRST must install it, or a module imported
    as ``icdev.tools.X`` before ``tools`` is ever touched becomes a second
    object. ``install`` refuses unless ``tools/`` is this package's sibling, so
    the wheel and a project's own ``tools/`` package are unaffected.
    """
    if not tools_init:
        return
    try:
        from icdev.core import shim

        shim.install(tools_init)
    except Exception:  # pragma: no cover - importing icdev must never fail here
        pass


def _alias_tools_namespace() -> None:
    """Bind ``icdev.tools`` to the ``tools`` name when nothing else provides it.

    Roughly 1,900 of the modules under ``icdev/tools/`` import their siblings
    through the absolute ``tools.*`` namespace (``from tools.db.storage import
    get_connection``). A source checkout satisfies that with a top-level
    ``tools/`` shim package, but the published wheel ships only ``icdev`` — so
    in an installed environment those imports raised ``ModuleNotFoundError`` and
    whole subsystems (``db.storage``, ``security.abac_engine``, ``llm.router``)
    were unimportable. Where the failure was swallowed by a broad ``except``,
    the effect was a silently disabled control rather than a crash.

    A real top-level ``tools`` package always wins: the source checkout's shim,
    and a scaffolded project's own ``tools/`` directory, must not be shadowed.
    """
    if "tools" in sys.modules:
        _install_checkout_alias(getattr(sys.modules["tools"], "__file__", None))
        return
    try:
        spec = importlib.util.find_spec("tools")
    except (ImportError, ValueError, AttributeError):
        spec = None  # find_spec can raise on odd sys.path entries; fall through to alias
    if spec is not None:
        _install_checkout_alias(spec.origin)
        return
    try:
        sys.modules["tools"] = importlib.import_module("icdev.tools")
    except Exception:  # pragma: no cover — importing icdev must never fail here
        pass


_alias_tools_namespace()
