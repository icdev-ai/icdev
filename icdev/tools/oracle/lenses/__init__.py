# CUI // SP-CTI
"""Oracle Lenses — Prediction modules for the anticipatory intelligence engine.

Lenses were historically imported ad-hoc at each call site (dashboard API,
migration/qdc blueprints).  This module adds a small, lazy registry so lenses
can be discovered and listed by name — used by ``oracle_lens_status`` style
tooling and by the Oracle → kanban bridge to know which prediction sources
exist.

The registry is intentionally lazy: entries map a lens name to
``(module_path, class_name)`` and the class is imported only when
``get_lens()`` is called, so importing this package stays cheap and free of
import cycles.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from tools.oracle.base_lens import BaseLens

# name -> (module_path, class_name)
_LENS_CLASSES: dict[str, tuple[str, str]] = {
    "network": ("tools.oracle.lenses.lens_network", "NetworkLens"),
    "quality": ("tools.oracle.lenses.lens_quality", "QualityLens"),
    "migration": ("tools.oracle.lenses.lens_migration", "MigrationLens"),
    "workflow_patterns": ("tools.oracle.lenses.lens_workflow_patterns", "WorkflowPatternLens"),
    "trajectory": ("tools.oracle.lens_trajectory", "TrajectoryLens"),
}


def list_lenses() -> list[str]:
    """Return the sorted list of registered oracle lens names."""
    return sorted(_LENS_CLASSES)


def get_lens_registry() -> dict[str, tuple[str, str]]:
    """Return a copy of the raw name -> (module, class) registry."""
    return dict(_LENS_CLASSES)


def get_lens(name: str) -> "type[BaseLens]":
    """Import and return the lens class registered under ``name``.

    Raises KeyError if the name is unknown, ImportError/AttributeError if the
    target module/class cannot be loaded.
    """
    try:
        module_path, class_name = _LENS_CLASSES[name]
    except KeyError as exc:
        raise KeyError(f"unknown oracle lens: {name!r}") from exc
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


__all__ = ["list_lenses", "get_lens_registry", "get_lens"]
