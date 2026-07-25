# CUI // SP-CTI — Twin Core registry (additive cross-canvas twin layer)
"""TwinRegistry — a data-driven registry of thin per-canvas twin adapters.

Each canvas already ships a working digital twin (``tools/network/twin.py``,
``tools/pipeline/twin.py``, ...). This registry does NOT replace them. It holds
a *thin adapter* per canvas that exposes a uniform surface —
``take_snapshot`` / ``simulate_delta`` / ``list_snapshots`` / ``latest_status`` —
over the canvas's existing module, translating native output into the canonical
:mod:`tools.twin_core.schema`.

Registration is **data-driven, not a hardcoded list**:

1. Adapter modules live in ``tools/twin_core/adapters/`` and register themselves
   at import time via the :func:`register_twin` decorator.
2. :meth:`TwinRegistry.discover` imports every module in that package (driven by
   the filesystem), so adding a canvas twin = dropping in one adapter file — no
   edit to this registry.
3. Canonical canvas keys and display names are cross-checked against
   ``args/component_registry.yaml`` (the same authoritative registry that drives
   blueprints/nav/IQE), so an adapter cannot register under a bogus canvas key.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from tools.logging.icdev_logger import get_logger

from tools.twin_core.schema import twin_verdict

logger = get_logger("icdev.twin_core.registry")

_ADAPTERS_PACKAGE = "tools.twin_core.adapters"


class TwinAdapter:
    """Base class for a thin per-canvas twin adapter.

    Subclasses set :attr:`canvas_key` and override the methods they support.
    Every method degrades gracefully: canvases whose native twin lacks a given
    capability (e.g. NDC has no ``list_snapshots``) return an honest empty /
    ``unknown`` result rather than raising.
    """

    #: Canonical canvas key (must match a ``kind: canvas`` key in component_registry.yaml).
    canvas_key: str = ""
    #: Human-readable name (falls back to the registry display_name).
    display_name: str = ""
    #: Provenance label carried onto every canonical violation for this canvas.
    method: str = "heuristic"
    #: Capability flags surfaced to the observer / dashboard.
    supports_snapshots: bool = True
    supports_simulation: bool = True

    # -- capability surface (override as needed) -------------------------------

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        raise NotImplementedError(f"{self.canvas_key}: take_snapshot not implemented")

    def list_snapshots(self, target_id: str, **kwargs) -> list[dict]:
        """Return snapshots newest-first. Default: empty (native twin has no list)."""
        return []

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        raise NotImplementedError(f"{self.canvas_key}: simulate_delta not implemented")

    def latest_status(self, target_id: str, **kwargs) -> dict:
        """Return a lightweight status envelope for ``target_id``.

        Default implementation reports snapshot count + latest snapshot with an
        ``unknown`` verdict (no simulation persisted). Adapters that persist
        verdicts should override to surface the real latest verdict.
        """
        snaps = self.list_snapshots(target_id, **kwargs)
        return {
            "canvas": self.canvas_key,
            "target_id": target_id,
            "verdict": "unknown",
            "snapshot_count": len(snaps),
            "latest_snapshot": snaps[0] if snaps else None,
            "method": self.method,
        }

    # -- helper for subclasses -------------------------------------------------

    def _wrap(self, target_id: str, verdict: Any, violations: list[dict] | None = None, **kw) -> dict:
        """Wrap a native simulate result in the canonical envelope (schema.twin_verdict)."""
        return twin_verdict(self.canvas_key, target_id, verdict, violations, method=self.method, **kw)

    def describe(self) -> dict:
        return {
            "canvas": self.canvas_key,
            "display_name": self.display_name or self.canvas_key,
            "method": self.method,
            "supports_snapshots": self.supports_snapshots,
            "supports_simulation": self.supports_simulation,
        }


class TwinRegistry:
    """Process-wide registry of :class:`TwinAdapter` instances, keyed by canvas."""

    _adapters: dict[str, TwinAdapter] = {}
    _discovered = False

    @classmethod
    def register(cls, adapter: TwinAdapter) -> TwinAdapter:
        key = adapter.canvas_key
        if not key:
            raise ValueError("TwinAdapter.canvas_key must be set before registration")
        cls._adapters[key] = adapter
        logger.debug("Registered twin adapter for canvas %s", key)
        return adapter

    @classmethod
    def get(cls, canvas_key: str) -> TwinAdapter | None:
        cls.discover()
        return cls._adapters.get(canvas_key)

    @classmethod
    def is_registered(cls, canvas_key: str) -> bool:
        cls.discover()
        return canvas_key in cls._adapters

    @classmethod
    def keys(cls) -> list[str]:
        cls.discover()
        return sorted(cls._adapters.keys())

    @classmethod
    def all(cls) -> dict[str, TwinAdapter]:
        cls.discover()
        return dict(cls._adapters)

    @classmethod
    def describe_all(cls) -> list[dict]:
        cls.discover()
        return [a.describe() for a in cls._adapters.values()]

    @classmethod
    def discover(cls, force: bool = False) -> list[str]:
        """Import every adapter module in ``tools/twin_core/adapters`` (idempotent).

        Filesystem-driven: each module self-registers via :func:`register_twin`,
        so this never enumerates a hardcoded canvas list.
        """
        if cls._discovered and not force:
            return sorted(cls._adapters.keys())
        try:
            pkg = importlib.import_module(_ADAPTERS_PACKAGE)
        except ModuleNotFoundError:
            cls._discovered = True
            return sorted(cls._adapters.keys())
        for mod in pkgutil.iter_modules(pkg.__path__):
            if mod.name.startswith("_"):
                continue
            try:
                importlib.import_module(f"{_ADAPTERS_PACKAGE}.{mod.name}")
            except Exception as exc:  # noqa: BLE001 — one bad adapter must not kill the rest
                logger.warning("Twin adapter %s failed to import: %s", mod.name, exc)
        cls._discovered = True
        return sorted(cls._adapters.keys())

    @classmethod
    def reset(cls) -> None:
        """Clear the registry (test hook)."""
        cls._adapters = {}
        cls._discovered = False


def register_twin(adapter_cls: type[TwinAdapter]) -> type[TwinAdapter]:
    """Class decorator: instantiate ``adapter_cls`` and register it.

    Enriches ``display_name`` from ``args/component_registry.yaml`` when the
    adapter didn't set one, keeping the registry data-driven off the same
    authoritative source as the rest of the platform.
    """
    instance = adapter_cls()
    if not instance.display_name:
        instance.display_name = _registry_display_name(instance.canvas_key) or instance.canvas_key
    TwinRegistry.register(instance)
    return adapter_cls


# ── component_registry.yaml cross-reference (data-driven display names) ────────

_DISPLAY_CACHE: dict[str, str] | None = None


def _load_canvas_display_names() -> dict[str, str]:
    """Best-effort map of ``canvas_key -> display_name`` from component_registry.yaml."""
    global _DISPLAY_CACHE
    if _DISPLAY_CACHE is not None:
        return _DISPLAY_CACHE
    names: dict[str, str] = {}
    try:
        from tools.config.component_registry import get_registry

        for comp in get_registry().iter_canvases():
            key = getattr(comp, "key", None)
            if key:
                names[key] = getattr(comp, "display_name", None) or key
    except Exception as exc:  # noqa: BLE001 — YAML is advisory only
        logger.debug("component_registry display-name lookup unavailable: %s", exc)
    _DISPLAY_CACHE = names
    return names


def _registry_display_name(canvas_key: str) -> str | None:
    return _load_canvas_display_names().get(canvas_key)


def known_canvas_keys() -> set[str]:
    """Canonical ``kind: canvas`` keys from component_registry.yaml (advisory)."""
    return set(_load_canvas_display_names().keys())
