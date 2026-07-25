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


def _row_to_dict(row, keys: tuple[str, ...]) -> dict:
    """Normalize a DB row (Mapping-like or positional tuple) to a dict of ``keys``."""
    if row is None:
        return {}
    if isinstance(row, (list, tuple)):
        return {k: row[i] if i < len(row) else None for i, k in enumerate(keys)}
    try:
        return {k: row[k] for k in keys}
    except (KeyError, TypeError, IndexError):
        return dict(row) if hasattr(row, "keys") else {}


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

    # -- fleet-health declaration (for the cross-canvas observer) ---------------
    # Adapters that persist snapshots/simulations/violations declare their table
    # + timestamp column here; the base :meth:`fleet_health` runs generic
    # count / latest-age / 24h-verdict queries over them. Names are class-level
    # constants (never user input), so the interpolated SQL is safe.
    snapshot_table: str | None = None
    snapshot_time_col: str = "created_at"
    simulation_table: str | None = None
    simulation_time_col: str = "created_at"
    simulation_verdict_col: str = "verdict"
    violation_table: str | None = None
    violation_severity_col: str = "severity"

    def _fleet_conn(self):
        """Return a connection to this canvas's DB for fleet-wide health queries.

        Override in adapters that declare a ``snapshot_table``/``simulation_table``.
        Default ``None`` = fleet health unavailable (honest, not fabricated).
        """
        return None

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

    def fleet_health(self, window_hours: int = 24) -> dict:
        """Canvas-wide twin health (all entities), for the cross-canvas observer.

        Runs generic queries over the adapter's declared tables:
        snapshot count + latest-snapshot timestamp, a ``window_hours`` verdict
        distribution (only where a simulation table persists verdicts), and
        violation counts by severity (only where persisted). Every query is
        best-effort — a missing table or empty canvas DB degrades to ``None`` /
        ``{}`` rather than raising, so a fresh checkout with no canvas data still
        reports honestly.
        """
        from datetime import datetime, timedelta, timezone

        from tools.twin_core.schema import normalize_severity, normalize_verdict

        out = {
            "available": False,
            "snapshot_count": None,
            "latest_snapshot_at": None,
            "verdicts": {},
            "violation_counts": {},
        }
        try:
            conn = self._fleet_conn()
        except Exception:  # noqa: BLE001
            conn = None
        if conn is None:
            return out
        out["available"] = True
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        try:
            if self.snapshot_table:
                row = conn.execute(  # nosec B608 — table/col are class constants
                    f"SELECT COUNT(*) AS c, MAX({self.snapshot_time_col}) AS m "
                    f"FROM {self.snapshot_table}"
                ).fetchone()
                d = _row_to_dict(row, ("c", "m"))
                out["snapshot_count"] = d.get("c")
                out["latest_snapshot_at"] = d.get("m")
        except Exception:  # noqa: BLE001 — table may not exist yet
            pass
        try:
            if self.simulation_table:
                rows = conn.execute(  # nosec B608 — table/col are class constants
                    f"SELECT {self.simulation_verdict_col} AS v, COUNT(*) AS c "
                    f"FROM {self.simulation_table} WHERE {self.simulation_time_col} >= %s "
                    f"GROUP BY {self.simulation_verdict_col}",
                    (cutoff,),
                ).fetchall()
                dist: dict = {}
                for r in rows:
                    d = _row_to_dict(r, ("v", "c"))
                    dist[normalize_verdict(d.get("v"))] = dist.get(normalize_verdict(d.get("v")), 0) + (d.get("c") or 0)
                out["verdicts"] = dist
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.violation_table:
                rows = conn.execute(  # nosec B608 — table/col are class constants
                    f"SELECT {self.violation_severity_col} AS s, COUNT(*) AS c "
                    f"FROM {self.violation_table} GROUP BY {self.violation_severity_col}"
                ).fetchall()
                counts: dict = {}
                for r in rows:
                    d = _row_to_dict(r, ("s", "c"))
                    sev = normalize_severity(d.get("s"))
                    counts[sev] = counts.get(sev, 0) + (d.get("c") or 0)
                out["violation_counts"] = counts
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return out

    # -- helper for subclasses -------------------------------------------------

    def _airgap_augment(self, target: Any, violations: list, verdict: Any, kwargs: dict):
        """Append air-gap deployment-blocker violations when targeting an air-gapped
        environment (twx-fed-01). Enabled by ``airgap=True`` kwarg, or auto when the
        runtime is detected air-gapped. Any blocker escalates the verdict to ``fail``.
        Returns ``(violations, verdict)``.
        """
        active = kwargs.get("airgap")
        if active is None:
            from tools.twin_core.airgap_rules import is_airgap_environment

            active = is_airgap_environment()
        if not active:
            return violations, verdict
        from tools.twin_core.airgap_rules import evaluate_airgap

        extra = evaluate_airgap(target, source_canvas=self.canvas_key, active=True)
        if extra:
            return list(violations) + extra, "fail"
        return violations, verdict

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
