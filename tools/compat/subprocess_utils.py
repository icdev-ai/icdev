# CUI // SP-CTI
"""Module names that survive being handed to a *child* Python process.

``icdev/__init__.py`` binds ``icdev.tools`` to the bare ``tools`` name so the
~1,900 packaged modules that import their siblings as ``tools.*`` keep working
in an installed wheel. That alias lives in ``sys.modules`` and therefore only
exists **after** ``icdev`` has been imported — inside the current interpreter.

A subprocess launched as ``python -m tools.db.init_icdev_db`` resolves the name
before any ICDEV code runs, so the alias is not there yet. In a pip-installed
environment, where the wheel ships only ``icdev``, the child dies with::

    ModuleNotFoundError: No module named 'tools'

The parent usually captures that into a generic "provisioning failed" string,
which is why this class of bug reaches users rather than CI: the source
checkout has a real top-level ``tools/`` shim, so every developer machine works.

Use :func:`runnable_module` for any ``-m`` target under ``tools.``.
"""
from __future__ import annotations

import importlib.util
import sys

__all__ = ["runnable_module"]


def runnable_module(dotted: str) -> str:
    """Return the name a ``python -m`` child can actually import.

    ``tools.x`` is returned unchanged when a genuine top-level ``tools``
    package exists (source checkout, or a scaffolded project shipping its own).
    Otherwise the packaged name ``icdev.tools.x`` is returned.

    The real shim is preferred over the ``icdev.tools`` mirror deliberately: in
    a source checkout the mirror can lag the tree it mirrors, and running a
    stale copy of a schema initialiser is worse than not running one.

    The discriminator is ``__name__`` on an already-imported ``tools``, not its
    spec: the repo shim sets ``__spec__ = None``, and ``find_spec`` raises
    ``ValueError`` for an imported module with no spec — which would misreport
    a real checkout as an installed wheel.
    """
    if not dotted.startswith("tools."):
        return dotted

    existing = sys.modules.get("tools")
    if existing is not None:
        # Under the alias, sys.modules["tools"] IS icdev.tools and says so.
        return dotted if getattr(existing, "__name__", "") == "tools" else f"icdev.{dotted}"

    try:
        spec = importlib.util.find_spec("tools")
    except (ImportError, ValueError, AttributeError):
        # find_spec raises on odd sys.path entries; treat as "no real package".
        spec = None
    if spec is not None and spec.name == "tools":
        return dotted
    return f"icdev.{dotted}"
