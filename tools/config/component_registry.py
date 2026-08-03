#!/usr/bin/env python3
# CUI // SP-CTI
"""Unified component registry loader for ICDEV™.

Single source of truth for canvases, child apps, and core extensions declared
in `args/component_registry.yaml`. Replaces the hardcoded `_CANVAS_DEFS`,
`_APP_DEFS`, `_CANVAS_ROUTES`, and `_CANVAS_MAP` scattered across
`tools/dashboard/app.py` and `tools/cli/enable.py`.

Usage:
    from tools.config.component_registry import ComponentRegistry
    registry = ComponentRegistry()

    # Iterate enabled canvases
    for comp in registry.iter_enabled(kind="canvas"):
        bp = comp.get_blueprint()
        app.register_blueprint(bp, url_prefix=comp.url_prefix)

    # IQE dispatch map
    canvas_map = registry.get_iqe_mapping()

    # CLI toggles
    toggles = registry.get_toggles()

    # Ownership ("who owns this?" — the IDP catalog question)
    registry.get_owner("ndc")            # -> str | None
    registry.list_unowned()              # -> components nobody is accountable for
    registry.get_ownership_summary()     # -> coverage counts for the scorecard

The loader is deterministic and read-only: it only inspects the registry file
and environment variables. It does not write files or mutate global state.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import os
from pathlib import Path
from typing import Any, Iterator

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.component_registry")

def _find_repo_root() -> tuple[Path, Path]:
    """Locate ``component_registry.yaml`` and the base dir that contains it.

    Works whether this module is imported from the canonical ``icdev.tools.*
    package (nested under ``icdev/``), the legacy root ``tools.*`` shim, or a
    ``pip install``ed wheel. In a wheel the registry is installed as package
    data under ``icdev/data/args/`` rather than ``<root>/args/``, so both
    layouts are probed at every level. Returns ``(base_dir, registry_path)``.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        for rel in (("args",), ("data", "args")):
            candidate = parent.joinpath(*rel, "component_registry.yaml")
            if candidate.is_file():
                return parent, candidate
    # Fallback to the naive parent-of-parent heuristic.
    base = here.parent.parent.parent
    return base, base / "args" / "component_registry.yaml"


BASE_DIR, DEFAULT_REGISTRY_PATH = _find_repo_root()


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

#: Values that look like an owner but assert nothing. A component carrying one
#: of these is reported as UNOWNED, not as owned-by-"TBD". A wrong owner is
#: worse than a blank one: it routes an incident to nobody while reading as
#: answered.
UNOWNED_SENTINELS = frozenset(
    {
        "",
        "-",
        "n/a",
        "na",
        "none",
        "null",
        "tbd",
        "todo",
        "unassigned",
        "unknown",
        "unowned",
    }
)


def _clean_ownership_field(value: Any) -> str | None:
    """Normalize one ownership field to a real value or ``None``.

    Anything blank, non-scalar, or in :data:`UNOWNED_SENTINELS` becomes
    ``None`` so that "declared but meaningless" and "not declared" collapse to
    the single reportable state: unowned.
    """
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value).strip()
    if text.lower() in UNOWNED_SENTINELS:
        return None
    return text


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def log_component_audit(
    event_type: str,
    actor: str = "system",
    tenant_id: str | None = None,
    component_key: str | None = None,
    profile_name: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Append a component-configuration event to ``component_audit_log``.

    Failures are logged at debug level and swallowed; audit logging must not
    break the action it is observing.
    """
    try:
        import json
        import uuid
        from datetime import datetime, timezone

        from tools.db.storage import get_connection

        now = datetime.now(timezone.utc).isoformat()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO component_audit_log
                  (id, event_type, actor, tenant_id, component_key, profile_name, details, recorded_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    event_type,
                    actor,
                    tenant_id or None,
                    component_key or None,
                    profile_name or None,
                    json.dumps(details or {}),
                    now,
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.debug("component_audit_log write failed for %s: %s", event_type, exc)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Component:
    """One registered ICDEV component (canvas, child_app, feature, core_extension)."""

    key: str
    kind: str  # canvas | child_app | feature | core_extension
    cli_name: str
    display_name: str
    description: str
    env_flag: str
    extra_env_flags: list[str]
    default_enabled: bool
    module: str | None
    blueprint_attr: str | None
    url_prefix: str
    min_il: str
    min_tier: str  # community | professional | enterprise
    default_roles: list[str]
    nav: dict[str, Any]
    iqe: dict[str, Any]
    completeness: dict[str, Any]
    raw: dict[str, Any]
    # --- Ownership (all optional; declared last so existing positional
    # construction of Component keeps working) --------------------------------
    #: Team or individual accountable for this component. NOT the same thing as
    #: ``default_roles``, which is an RBAC access list (who may *use* the
    #: canvas), not who answers for it.
    owner: str | None = None
    #: How to reach the owner — email, chat handle, distribution list, URL.
    owner_contact: str | None = None
    #: On-call rotation or escalation handle for production issues.
    on_call: str | None = None

    @property
    def is_owned(self) -> bool:
        """True when a real owner is declared (sentinels do not count)."""
        return self.owner is not None

    def ownership(self) -> dict[str, Any]:
        """Return this component's ownership record.

        ``owned`` is the authoritative answer to "does anyone own this?" —
        callers should branch on it rather than truth-testing ``owner``.
        """
        return {
            "key": self.key,
            "kind": self.kind,
            "display_name": self.display_name,
            "owned": self.is_owned,
            "owner": self.owner,
            "owner_contact": self.owner_contact,
            "on_call": self.on_call,
        }

    def is_enabled(self, env: dict[str, str] | None = None) -> bool:
        """Return True if the component's primary env flag is enabled.

        Args:
            env: Optional mapping of environment variables. Defaults to
                 `os.environ`.
        """
        env = env if env is not None else os.environ
        value = env.get(self.env_flag, "true" if self.default_enabled else "false")
        return str(value).strip().strip('"').strip("'").lower() in (
            "true",
            "1",
            "yes",
            "on",
        )

    def get_blueprint(self) -> Any | None:
        """Dynamically import the module and resolve the blueprint attribute.

        Mirrors the logic in `tools/dashboard/app.py`:
          - If the attribute is callable and lacks a `.name`, treat it as a
            factory and call it once.
          - Otherwise return the attribute itself.

        Returns None for feature toggles or if import/resolution fails.
        """
        if self.module is None or self.blueprint_attr is None:
            return None

        try:
            mod = importlib.import_module(self.module)
        except Exception as exc:
            logger.warning(
                "Component %s import failed (%s): %s", self.key, self.module, exc
            )
            return None

        bp = getattr(mod, self.blueprint_attr, None)
        if bp is None:
            logger.warning(
                "Component %s missing blueprint attribute %s in %s",
                self.key,
                self.blueprint_attr,
                self.module,
            )
            return None

        try:
            if callable(bp) and not hasattr(bp, "name"):
                bp = bp()
        except Exception as exc:
            logger.warning(
                "Component %s blueprint factory failed (%s.%s): %s",
                self.key,
                self.module,
                self.blueprint_attr,
                exc,
            )
            return None

        return bp


# ---------------------------------------------------------------------------
# Registry loader
# ---------------------------------------------------------------------------


class ComponentRegistry:
    """Load and query `args/component_registry.yaml`."""

    def __init__(
        self,
        registry_path: Path | str | None = None,
        env: dict[str, str] | None = None,
    ):
        """Initialize the registry.

        Args:
            registry_path: Path to `component_registry.yaml`. Defaults to
                `args/component_registry.yaml` under the repo root.
            env: Optional environment-variable mapping. Defaults to `os.environ`.
        """
        self.registry_path = Path(registry_path or DEFAULT_REGISTRY_PATH)
        self._env = env if env is not None else os.environ
        self._components: tuple[Component, ...] = tuple(self._load_components())
        self._by_key: dict[str, Component] = {c.key: c for c in self._components}

    # ------------------------------------------------------------------
    # YAML loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        """Load YAML. PyYAML preferred; minimal fallback for air-gap."""
        if not path.exists():
            logger.warning("Component registry not found: %s", path)
            return {}
        try:
            import yaml

            with open(path, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except ImportError:
            logger.warning(
                "PyYAML not installed; component registry cannot be loaded air-gap. "
                "Install PyYAML or provide an alternative loader."
            )
            return {}
        except Exception as exc:
            logger.error("Failed to load component registry %s: %s", path, exc)
            return {}

    def _load_components(self) -> Iterator[Component]:
        data = self._load_yaml(self.registry_path)
        entries = data.get("components", [])
        if not isinstance(entries, list):
            logger.error("component_registry.yaml 'components' must be a list")
            return

        for idx, raw in enumerate(entries):
            if not isinstance(raw, dict):
                logger.warning("Skipping non-dict registry entry #%s", idx)
                continue
            try:
                yield self._make_component(raw)
            except Exception as exc:
                logger.warning(
                    "Skipping invalid registry entry #%s (%s): %s", idx, raw.get("key"), exc
                )

    @staticmethod
    def _make_component(raw: dict[str, Any]) -> Component:
        """Construct a Component from a raw registry dict."""
        key = str(raw.get("key", "")).strip()
        if not key:
            raise ValueError("component missing required 'key'")

        kind = str(raw.get("kind", "canvas")).strip()
        cli_name = str(raw.get("cli_name", key)).strip() or key
        display_name = str(raw.get("display_name", key)).strip()
        description = str(raw.get("description", "")).strip()
        env_flag = str(raw.get("env_flag", f"ICDEV_{key.upper()}_ENABLED")).strip()
        extra_env_flags = list(raw.get("extra_env_flags", []) or [])
        default_enabled = bool(raw.get("default_enabled", False))
        module = raw.get("module")
        module = str(module).strip() if module else ""
        blueprint_attr = raw.get("blueprint_attr")
        blueprint_attr = str(blueprint_attr).strip() if blueprint_attr else ""
        url_prefix = str(raw.get("url_prefix", f"/{key}")).strip()
        min_il = str(raw.get("min_il", "IL2")).strip()
        _valid_tiers = ("community", "professional", "enterprise")
        min_tier = str(raw.get("min_tier", "community")).strip().lower()
        if min_tier not in _valid_tiers:
            min_tier = "community"
        default_roles = list(raw.get("default_roles", []) or [])
        nav = dict(raw.get("nav", {}) or {})
        iqe = dict(raw.get("iqe", {}) or {})
        completeness = dict(raw.get("completeness", {}) or {})
        # Ownership is optional by design: a required field would fail the whole
        # registry load for the entries that legitimately have no owner yet.
        owner = _clean_ownership_field(raw.get("owner"))
        owner_contact = _clean_ownership_field(raw.get("owner_contact"))
        on_call = _clean_ownership_field(raw.get("on_call"))

        if kind not in ("feature", "core_extension"):
            if not module:
                raise ValueError(f"component {key!r} missing required 'module'")
            if not blueprint_attr:
                raise ValueError(f"component {key!r} missing required 'blueprint_attr'")

        return Component(
            key=key,
            kind=kind,
            cli_name=cli_name,
            display_name=display_name,
            description=description,
            env_flag=env_flag,
            extra_env_flags=extra_env_flags,
            default_enabled=default_enabled,
            module=module or None,
            blueprint_attr=blueprint_attr or None,
            url_prefix=url_prefix,
            min_il=min_il,
            min_tier=min_tier,
            default_roles=default_roles,
            nav=nav,
            iqe=iqe,
            completeness=completeness,
            raw=raw,
            owner=owner,
            owner_contact=owner_contact,
            on_call=on_call,
        )

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._components)

    def __iter__(self) -> Iterator[Component]:
        return iter(self._components)

    def __contains__(self, key: str) -> bool:
        return key in self._by_key

    def get(self, key: str) -> Component | None:
        """Return component by key, or None if not registered."""
        return self._by_key.get(key)

    def list_all(self, kind: str | None = None) -> list[Component]:
        """Return all components, optionally filtered by kind."""
        if kind is None:
            return list(self._components)
        return [c for c in self._components if c.kind == kind]

    def list_enabled(self, kind: str | None = None) -> list[Component]:
        """Return enabled components, optionally filtered by kind."""
        return [c for c in self.list_all(kind) if c.is_enabled(self._env)]

    def iter_canvases(self) -> Iterator[Component]:
        """Yield all canvas components."""
        return iter(self.list_all(kind="canvas"))

    def iter_child_apps(self) -> Iterator[Component]:
        """Yield all child_app components."""
        return iter(self.list_all(kind="child_app"))

    def iter_enabled(self, kind: str | None = None) -> Iterator[Component]:
        """Yield enabled components."""
        return iter(self.list_enabled(kind))

    # ------------------------------------------------------------------
    # Ownership
    # ------------------------------------------------------------------

    def get_owner(self, key: str) -> str | None:
        """Return the declared owner of a component, or None if unowned.

        Returns None both for an unowned component and for an unregistered
        key. Use ``key in registry`` first when the distinction matters.
        """
        comp = self._by_key.get(key)
        return comp.owner if comp is not None else None

    def list_owned(self, kind: str | None = None) -> list[Component]:
        """Return components that declare a real owner."""
        return [c for c in self.list_all(kind) if c.is_owned]

    def list_unowned(self, kind: str | None = None) -> list[Component]:
        """Return components with no accountable owner.

        This is the input to the IDP scorecard's ownership rule: an unowned
        component is a finding, never a component silently attributed to a
        default team.
        """
        return [c for c in self.list_all(kind) if not c.is_owned]

    def get_ownership_map(self) -> dict[str, dict[str, Any]]:
        """Return ``{component_key: ownership record}`` for every component."""
        return {c.key: c.ownership() for c in self._components}

    def get_ownership_summary(self, kind: str | None = None) -> dict[str, Any]:
        """Return ownership coverage counts for scorecard reporting."""
        components = self.list_all(kind)
        unowned = [c.key for c in components if not c.is_owned]
        total = len(components)
        owned = total - len(unowned)
        return {
            "kind": kind,
            "total": total,
            "owned": owned,
            "unowned": len(unowned),
            "unowned_keys": sorted(unowned),
            "coverage_pct": round(owned / total * 100, 1) if total else 0.0,
            "with_contact": sum(
                1 for c in components if c.owner_contact is not None
            ),
            "with_on_call": sum(1 for c in components if c.on_call is not None),
        }

    def is_enabled(self, key: str) -> bool:
        """Return True if the named component is enabled by env/default."""
        comp = self._by_key.get(key)
        if comp is None:
            return False
        return comp.is_enabled(self._env)

    def is_enabled_for_tenant(
        self,
        key: str,
        tenant_id: str,
        conn_factory: Any | None = None,
    ) -> bool:
        """Return True if the component is enabled for the given tenant.

        Resolution order:
          1. Explicit tenant override from ``tenant_component_overrides``
             (if the table exists). A value of ``1`` enables, ``0`` disables,
             and a missing row falls through.
          2. Environment flag + ``default_enabled``.
        """
        env_enabled = self.is_enabled(key)
        if not tenant_id or key not in self._by_key:
            return env_enabled
        if conn_factory is None:
            try:
                from tools.db.storage import get_connection

                conn_factory = get_connection
            except Exception:
                return env_enabled
        try:
            with conn_factory() as conn:
                row = conn.execute(
                    "SELECT enabled FROM tenant_component_overrides "
                    "WHERE tenant_id = %s AND component_key = %s",
                    (tenant_id, key),
                ).fetchone()
                if row:
                    if isinstance(row, (tuple, list)):
                        value = row[0]
                    else:
                        value = row["enabled"]
                    return bool(int(value))
        except Exception as exc:
            logger.debug(
                "Tenant override lookup failed for %s/%s: %s", tenant_id, key, exc
            )
        return env_enabled

    def set_tenant_component_override(
        self,
        tenant_id: str,
        component_key: str,
        enabled: bool,
        updated_by: str = "system",
    ) -> bool:
        """Set a tenant-level enablement override for a component.

        Returns True if the write succeeded. Falls back to False if the table is
        missing or the DB is unavailable.
        """
        if not tenant_id or not component_key:
            return False
        try:
            import uuid
            from datetime import datetime, timezone

            from tools.db.storage import get_connection

            override_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO tenant_component_overrides
                      (id, tenant_id, component_key, enabled, updated_by, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, component_key)
                    DO UPDATE SET
                      enabled = excluded.enabled,
                      updated_by = excluded.updated_by,
                      updated_at = excluded.updated_at
                    """,
                    (
                        override_id,
                        tenant_id,
                        component_key,
                        1 if enabled else 0,
                        updated_by,
                        now,
                    ),
                )
                conn.commit()
            log_component_audit(
                event_type="tenant_override_set",
                actor=updated_by,
                tenant_id=tenant_id,
                component_key=component_key,
                details={"enabled": enabled},
            )
            return True
        except Exception as exc:
            logger.warning(
                "Tenant override write failed for %s/%s: %s",
                tenant_id,
                component_key,
                exc,
            )
            return False

    def clear_tenant_component_override(
        self, tenant_id: str, component_key: str
    ) -> bool:
        """Remove a tenant-level override so env/default settings apply again."""
        if not tenant_id or not component_key:
            return False
        try:
            from tools.db.storage import get_connection

            with get_connection() as conn:
                conn.execute(
                    "DELETE FROM tenant_component_overrides "
                    "WHERE tenant_id = %s AND component_key = %s",
                    (tenant_id, component_key),
                )
                conn.commit()
            log_component_audit(
                event_type="tenant_override_clear",
                actor="system",
                tenant_id=tenant_id,
                component_key=component_key,
            )
            return True
        except Exception as exc:
            logger.warning(
                "Tenant override clear failed for %s/%s: %s",
                tenant_id,
                component_key,
                exc,
            )
            return False

    def list_tenant_overrides(self, tenant_id: str) -> list[dict[str, Any]]:
        """Return all tenant-level component overrides for a tenant."""
        if not tenant_id:
            return []
        try:
            from tools.db.storage import get_connection

            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT component_key, enabled, updated_by, updated_at "
                    "FROM tenant_component_overrides WHERE tenant_id = %s",
                    (tenant_id,),
                ).fetchall()
            return [
                {
                    "component_key": r[0],
                    "enabled": bool(int(r[1])),
                    "updated_by": r[2],
                    "updated_at": r[3],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("Tenant override list failed for %s: %s", tenant_id, exc)
            return []

    def get_blueprint(self, key: str) -> Any | None:
        """Resolve and return the Flask blueprint for a component."""
        comp = self._by_key.get(key)
        if comp is None:
            logger.warning("Requested blueprint for unknown component %s", key)
            return None
        return comp.get_blueprint()

    def get_toggles(self) -> dict[str, list[str]]:
        """Return canonical CLI toggle names → env flag list.

        Toggle name is `cli_name` when present, otherwise `key`. The value
        includes the primary `env_flag` plus any `extra_env_flags` (legacy
        aliases that must flip together, e.g., ICDEV_BDC_ENABLED +
        ICDEV_BOUNDARY_ENABLED).

        Includes every component that has an env_flag. For the user-facing
        `icdev enable/disable` surface, use `get_cli_toggles()` instead.
        """
        toggles: dict[str, list[str]] = {}
        for c in self._components:
            if not c.env_flag:
                continue
            name = c.cli_name or c.key
            flags = [c.env_flag] + list(c.extra_env_flags)
            toggles[name] = flags
        return toggles

    def get_cli_toggles(self) -> dict[str, list[str]]:
        """Return user-facing CLI toggles — every component with an env_flag.

        This is the single source of truth for `icdev enable/disable`. It
        covers **all** toggleable kinds (canvas, feature, core_extension,
        child_app), so any component that declares an ``env_flag`` in
        ``component_registry.yaml`` is reachable from the CLI. The value is the
        primary ``env_flag`` plus any ``extra_env_flags`` (legacy aliases that
        flip together, e.g. ICDEV_NDC_ENABLED + ICDEV_NETWORK_ENABLED).

        Historically this filtered to canvas + feature only, which left 21
        core-extension / child-app env flags (STRATEGOS, CPMP, INNOVATION, the
        *_IQE_* flags, …) with no CLI path at all — the exact registry/CLI
        drift CLAUDE.md forbids. Broadening the filter closes that gap; the
        drift-guard test in tests/test_component_registry.py keeps it closed.
        """
        toggles: dict[str, list[str]] = {}
        for c in self._components:
            if not c.env_flag:
                continue
            name = c.cli_name or c.key
            flags = [c.env_flag] + list(c.extra_env_flags)
            toggles[name] = flags
        return toggles

    def get_descriptions(self) -> dict[str, str]:
        """Return canonical toggle name → description for CLI display."""
        return {(c.cli_name or c.key): c.display_name for c in self._components}

    def get_cli_descriptions(self) -> dict[str, str]:
        """Return CLI toggle descriptions for every component with an env_flag.

        Mirrors the coverage of ``get_cli_toggles()`` so every reachable toggle
        has a display name in `icdev list` / `icdev status`.
        """
        return {
            (c.cli_name or c.key): c.display_name
            for c in self._components
            if c.env_flag
        }

    def get_iqe_mapping(self) -> dict[str, tuple[str, list[str]]]:
        """Return component key → (adapter_module, collections) for IQE dispatch.

        Includes any component (canvas, child_app, core_extension, feature) that
        declares an IQE adapter. This lets the registry act as the single
        source of truth for /api/iqe/dispatch routing.
        """
        mapping: dict[str, tuple[str, list[str]]] = {}
        for c in self._components:
            adapter = c.iqe.get("adapter_module")
            collections = c.iqe.get("collections")
            if adapter and isinstance(collections, list):
                mapping[c.key] = (str(adapter), list(collections))
        return mapping

    def get_iqe_path_canvas(self) -> list[tuple[str, str]]:
        """Return an ordered ``(regex_source, canvas_key)`` list for the
        client-side canvas detector (IQE mini-bar + AI-brief banner).

        Single source of truth replacing the two divergent hardcoded
        ``PATH_CANVAS`` copies in ``base.html`` and
        ``includes/ai_brief_banner.html``. The result is injected once into
        ``base.html`` as ``window.__ICDEV_PATH_CANVAS__`` and consumed by both.

        Derivation (priority order — first match wins in JS, loose ``^prefix``
        semantics preserved from the legacy array):

          1. The explicit, ordered ``iqe_path_canvas`` list from
             ``component_registry.yaml``. Each entry is either
             ``{path: <prefix>, canvas: <key>}`` (loose prefix → ``^<prefix>``)
             or ``{regex: <src>, canvas: <key>}`` (verbatim pattern, e.g.
             ``^/network|^/twin``). This preserves legacy ordering, regex
             specials, and path→canvas aliases (e.g. ``/proposals`` → ``govcon``).
             More specific paths are listed before shorter ones, and the
             ``/coworkers`` → ``cwk`` entry precedes ``/coworker`` → ``ace``
             (the documented longest-path-first collision resolution).

          2. Auto-derived entries for every ``canvas`` component that declares a
             ``url_prefix`` AND an IQE adapter and is not already covered by (1).
             Appended longest-prefix-first so a longer path is always tested
             before a shorter one. This future-proofs the detector: a new canvas
             with a ``url_prefix`` + IQE adapter appears automatically, without
             editing either template.

        Each returned regex source is anchored with ``^`` and is compatible with
        both Python ``re`` and JavaScript ``RegExp``. The registry is load-once
        (YAML parsed at construction), so callers may cache the result at module
        scope.
        """
        import re as _re

        raw = self._load_yaml(self.registry_path)
        explicit = raw.get("iqe_path_canvas") or []

        result: list[tuple[str, str]] = []
        covered_prefixes: set[str] = set()
        covered_keys: set[str] = set()

        def _prefix_to_src(prefix: str) -> str:
            return "^" + _re.escape(prefix)

        if isinstance(explicit, list):
            for entry in explicit:
                if not isinstance(entry, dict):
                    continue
                canvas = str(entry.get("canvas", "")).strip()
                if not canvas:
                    continue
                if entry.get("regex"):
                    src = str(entry["regex"])
                elif entry.get("path"):
                    prefix = str(entry["path"]).rstrip("/") or "/"
                    src = _prefix_to_src(prefix)
                    covered_prefixes.add(prefix)
                else:
                    continue
                result.append((src, canvas))
                covered_keys.add(canvas)

        # Auto-derive: enabled-or-not canvases with a url_prefix + IQE adapter
        # not already covered, longest-prefix-first for deterministic matching.
        auto: list[tuple[int, str, str]] = []
        for c in self._components:
            if c.kind != "canvas":
                continue
            prefix = (c.url_prefix or "").rstrip("/")
            if not prefix or not c.iqe.get("adapter_module"):
                continue
            if c.key in covered_keys or prefix in covered_prefixes:
                continue
            auto.append((len(prefix), _prefix_to_src(prefix), c.key))
            covered_keys.add(c.key)
            covered_prefixes.add(prefix)
        auto.sort(key=lambda t: -t[0])
        for _length, src, key in auto:
            result.append((src, key))

        return result

    @staticmethod
    def _normalize_nav_links(
        nav: dict[str, Any], url_prefix: str
    ) -> list[dict[str, Any]]:
        """Return sanitized nav links for a component.

        If the registry entry does not declare explicit links, generate a single
        "Dashboard" link pointing at the component's ``url_prefix``.

        cnr-nav-01: when a component declares no explicit links **and** has no
        ``url_prefix`` (empty string), emit **no** link rather than fabricating a
        link to ``/``. The old ``href = url_prefix or "/"`` fallback dumped users
        back on the home dashboard for every ``url_prefix: ''`` component whose
        real routes live in its blueprint — such components must declare explicit
        ``nav.links`` (or none, if they are page-less feature toggles).
        """
        links = nav.get("links")
        if not links:
            prefix = (url_prefix or "").strip()
            if not prefix:
                return []
            href = prefix if prefix.endswith("/") else f"{prefix}/"
            return [
                {
                    "label": "Dashboard",
                    "href": href,
                    "style": "",
                    "icon": "",
                    "enabled": True,
                }
            ]
        result: list[dict[str, Any]] = []
        for link in links:
            if not isinstance(link, dict):
                continue
            label = str(link.get("label", "")).strip()
            href = str(link.get("href", "")).strip()
            if not label or not href:
                continue
            result.append(
                {
                    "label": label,
                    "href": href,
                    "style": str(link.get("style", "")).strip(),
                    "icon": str(link.get("icon", "")).strip(),
                    "enabled": bool(link.get("enabled", True)),
                }
            )
        return result

    def get_nav_context(self) -> dict[str, Any]:
        """Return navigation tree derived from registry entries.

        Structure::

            {"sections": {"Section Name": {"groups": [
                {"label": ..., "items": [
                    {
                        "key": ...,
                        "display_name": ...,
                        "kind": ...,
                        "url_prefix": ...,
                        "min_il": ...,
                        "default_roles": [...],
                        "enabled": ...,
                        "links": [
                            {"label": ..., "href": ..., "style": ..., "icon": ..., "enabled": ...}
                        ],
                    }
                ]}
            ]}}}

        Components that share the same section and label are grouped so templates
        can render a single section header with links from multiple components
        (e.g., ``Agentic AI`` containing both ``agentic_ai_canvas`` and
        ``demo_runner``).
        """
        sections: dict[str, dict[str, Any]] = {}
        for c in self._components:
            section = c.nav.get("section")
            label = c.nav.get("label")
            if not section:
                continue
            sec = sections.setdefault(section, {"groups": {}})
            groups: dict[str, Any] = sec["groups"]
            group_key = label or c.display_name
            if group_key not in groups:
                groups[group_key] = {"label": group_key, "items": []}
            groups[group_key]["items"].append(
                {
                    "key": c.key,
                    "display_name": c.display_name,
                    "kind": c.kind,
                    "url_prefix": c.url_prefix,
                    "min_il": c.min_il,
                    "min_tier": c.min_tier,
                    "default_roles": list(c.default_roles),
                    "enabled": c.is_enabled(self._env),
                    "links": self._normalize_nav_links(c.nav, c.url_prefix),
                }
            )
        return {
            "sections": {
                section: {"groups": list(groups["groups"].values())}
                for section, groups in sections.items()
            }
        }

    def get_url_prefixes(self) -> dict[str, str]:
        """Return component key → url_prefix (legacy _CANVAS_ROUTES replacement)."""
        return {c.key: c.url_prefix for c in self._components}

    def get_default_enabled_flags(self) -> set[str]:
        """Return the set of keys that are enabled by default."""
        return {c.key for c in self._components if c.default_enabled}

    def validate_canvas_completeness(
        self,
        key: str,
        repo_root: Path | str | None = None,
    ) -> CanvasCompletenessReport:
        """Validate a canvas against the 8-point completeness gate.

        Convenience wrapper around :func:`validate_canvas_completeness` that
        passes this registry instance.
        """
        return validate_canvas_completeness(key, registry=self, repo_root=repo_root)


# ---------------------------------------------------------------------------
# 8-point canvas completeness validator (CLAUDE.md dashboard-page gate)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CanvasCompletenessItem:
    """One of the 8 required completeness points for a canvas."""

    point: str
    required: bool
    present: bool
    path: str | None
    message: str


@dataclasses.dataclass(frozen=True)
class CanvasCompletenessReport:
    """Result of validating a canvas against the 8-point gate."""

    key: str
    passed: bool
    items: tuple[CanvasCompletenessItem, ...]
    min_tier: str = "community"


def _file_exists(path: Path | str | None) -> bool:
    if path is None:
        return False
    return Path(path).exists()


def _ast_has_route_decorator(text: str) -> bool:
    """Return True if the Python source declares a Flask ``@*.route(...)`` decorator."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                    if deco.func.attr == "route":
                        return True
                elif isinstance(deco, ast.Attribute) and deco.attr == "route":
                    return True
    return False


def _has_route_decorator(blueprint_path: Path) -> bool:
    """Return True if the blueprint declares a Flask route — inline or split.

    Classic blueprints carry ``@bp.route(...)`` decorators directly in the file.
    After a route-group split (cvx-net-01) the blueprint becomes a thin assembler
    whose ``create_*_blueprint()`` delegates to ``register_<group>_routes(bp)``
    helpers imported from a sibling ``routes/`` subpackage; the actual ``@route``
    decorators live in those modules. When the file itself carries no route but
    (a) references ``register_*_routes`` / imports from a ``routes`` subpackage,
    OR (b) has a ``routes/`` directory beside it, scan ``routes/**/*.py`` and pass
    if any declares a route. Still returns False when nothing declares a route.
    """
    if not blueprint_path.exists():
        return False
    try:
        text = blueprint_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if _ast_has_route_decorator(text):
        return True

    import re

    references_split = bool(
        re.search(r"register_\w+_routes", text)
        or re.search(r"from\s+[\w.]*routes(?:\.\w+)?\s+import", text)
        or re.search(r"import\s+[\w.]*\.routes\b", text)
    )
    routes_dir = blueprint_path.parent / "routes"
    if references_split or routes_dir.is_dir():
        if routes_dir.is_dir():
            for py in sorted(routes_dir.rglob("*.py")):
                try:
                    if _ast_has_route_decorator(py.read_text(encoding="utf-8")):
                        return True
                except OSError:
                    continue
    return False


def _module_dir_from_module(module: str, root: Path | None = None) -> str:
    """Return the package directory for a registry `module` entry.

    `module` may name a package's blueprint submodule ('tools.xxx.blueprint') or a
    standalone module file ('tools.govcon.rfi_canvas_blueprint'). Only the first
    form was handled, so a canvas whose blueprint is a plain module — rather than
    a `blueprint.py` inside its own package — resolved to a directory that does not
    exist, and was reported as missing both its blueprint and its backing module.

    When `root` is supplied, decide by looking at the filesystem: strip the final
    segment whenever it denotes a .py file rather than a directory. Without a root,
    fall back to the historical 'blueprint'-suffix rule.
    """
    parts = module.split(".")
    if root is not None and len(parts) > 1:
        candidate = root.joinpath(*parts)
        if not candidate.is_dir() and candidate.with_suffix(".py").is_file():
            parts = parts[:-1]
        return "/".join(parts)
    if len(parts) > 1 and parts[-1] == "blueprint":
        parts = parts[:-1]
    return "/".join(parts)


def _blueprint_path_from_module(module: str, root: Path) -> Path:
    """Resolve the file that should carry the @route decorators."""
    module_file = root.joinpath(*module.split(".")).with_suffix(".py")
    if module_file.is_file():
        return module_file
    return root / _module_dir_from_module(module, root) / "blueprint.py"


def validate_canvas_completeness(
    key: str,
    registry: ComponentRegistry | None = None,
    repo_root: Path | str | None = None,
) -> CanvasCompletenessReport:
    """Validate a registered canvas against the 8-point completeness gate.

    The gate is defined in CLAUDE.md for new dashboard pages:
      1. tools/dashboard/templates/<canvas>/page.html exists
      2. icdev/tools/dashboard/templates/<canvas>/page.html mirrored
      3. tools/<canvas>/blueprint.py contains @bp.route decorators
      4. tools/<canvas>/ has a backing module beyond blueprint.py
      5. tools/<canvas>/constants.py exists
      6. DB migration directory exists (when declared in registry)
      7. Nav/parent link declared in registry
      8. IQE integration: adapter + seed queries + registry IQE data

    Args:
        key: Canvas registry key.
        registry: Optional registry instance. Defaults to global singleton.
        repo_root: Optional repo root. Defaults to parent of this module.

    Returns:
        CanvasCompletenessReport with per-point findings.
    """
    if registry is None:
        registry = get_registry()
    comp = registry.get(key)
    if comp is None:
        item = CanvasCompletenessItem(
            point="registered",
            required=True,
            present=False,
            path=None,
            message=f"Component '{key}' not found in registry",
        )
        return CanvasCompletenessReport(key=key, passed=False, items=(item,))

    if comp.kind != "canvas":
        item = CanvasCompletenessItem(
            point="registered",
            required=True,
            present=False,
            path=None,
            message=f"Component '{key}' is kind={comp.kind}, not canvas",
        )
        return CanvasCompletenessReport(key=key, passed=False, items=(item,))

    root = Path(repo_root or BASE_DIR).resolve()
    completeness = comp.completeness

    # Point 1: main page template — check declared path, then common legacy fallbacks.
    # Legacy canvases may use index.html, dashboard.html, or live in a differently named dir.
    module_path_tmp = comp.module or ""
    module_dir_tmp = _module_dir_from_module(module_path_tmp, root)
    _module_leaf = module_dir_tmp.split("/")[-1] if "/" in module_dir_tmp else module_dir_tmp

    template_path_str = completeness.get("template") or f"tools/dashboard/templates/{key}/page.html"
    template_path = root / template_path_str
    page_present = template_path.exists()

    if not page_present:
        # Try common alternative names and directory conventions
        _tmpl_candidates = [
            f"tools/dashboard/templates/{_module_leaf}/page.html",
            f"tools/dashboard/templates/{_module_leaf}/index.html",
            f"tools/dashboard/templates/{key}/index.html",
            f"tools/dashboard/templates/{key}/dashboard.html",
        ]
        for _cand in _tmpl_candidates:
            if (root / _cand).exists():
                template_path_str = _cand
                template_path = root / _cand
                page_present = True
                break
        # Last resort: any .html in the template dir (e.g. trust.html for ace)
        if not page_present:
            for _leaf in (_module_leaf, key):
                _tmpl_dir = root / "tools" / "dashboard" / "templates" / _leaf
                if _tmpl_dir.is_dir():
                    _html_files = sorted(
                        f for f in _tmpl_dir.glob("*.html")
                        if f.name not in ("base.html", "login.html", "error.html")
                    )
                    if _html_files:
                        template_path = _html_files[0]
                        template_path_str = str(template_path.relative_to(root)).replace("\\", "/")
                        page_present = True
                        break

    # Point 2: icdev mirror — only required if a main template was found.
    # Existing canvases without a web template don't need the icdev mirror.
    icdev_template_path = root / "icdev" / template_path_str
    mirror_present = icdev_template_path.exists()

    # Point 3: blueprint with route decorators
    module_path = comp.module or ""
    module_dir = _module_dir_from_module(module_path, root)
    blueprint_path = _blueprint_path_from_module(module_path, root)
    route_present = _has_route_decorator(blueprint_path)

    # Point 4: backing module (a non-blueprint, non-init Python file in the package)
    module_pkg = root / module_dir
    backing_present = False
    if module_pkg.is_dir():
        for py_file in module_pkg.glob("*.py"):
            if py_file.name not in ("__init__.py", blueprint_path.name):
                backing_present = True
                break

    # Point 5: constants
    constants_path_str = completeness.get("constants") or f"{module_dir}/constants.py"
    constants_path = root / constants_path_str
    constants_present = constants_path.exists()

    # Point 6: DB migration directory
    # Check declared path and common legacy fallback (db/ without /migrations sub-dir).
    migration_path_str = completeness.get("db_migration")
    migration_path = root / migration_path_str if migration_path_str else None
    migration_present: bool | None = None
    if migration_path_str:
        migration_present = _file_exists(migration_path)
        if not migration_present:
            # Fallback: check parent db/ directory (older canvases don't have /migrations)
            _db_parent = Path(migration_path_str).parent
            if _db_parent.name == "migrations":
                _db_parent = _db_parent.parent
            if (root / _db_parent).exists():
                migration_present = True
                migration_path_str = str(_db_parent)

    # Point 7: nav link
    #
    # The `completeness:` block declares APPLICABILITY, not presence — the
    # registry's own convention comment reads "7. nav_link — applies ->
    # `nav_link: true`". An explicit `nav_link: false` therefore means the point
    # does not apply to this component, exactly like an absent `db_migration`
    # (point 6) or an absent `iqe.adapter_module` (point 8), both of which
    # already drive `required` rather than `present`. Reading that `false` as
    # "declared and missing" left the one canvas that uses it permanently
    # unfixable: `aiify_compat` is a 301-redirect alias (/ai-augmentation →
    # /ai-ify/) whose sidebar link is owned by the real `aiify` canvas, and
    # giving it a nav section re-emits the duplicate "AI-ify" entry cnr-nav-01
    # removed. A MISSING key still means "required" — only an explicit falsy
    # value opts out, so this cannot silently excuse a canvas that forgot to
    # declare its nav.
    nav_link_declared_na = "nav_link" in completeness and not completeness.get("nav_link")
    nav_link_present = bool(completeness.get("nav_link")) or bool(comp.nav.get("section"))

    # Point 8: IQE integration
    iqe_adapter_module = comp.iqe.get("adapter_module")
    seed_queries_str = completeness.get("seed_queries")
    iqe_adapter_path = None
    if iqe_adapter_module:
        iqe_adapter_path = root / (iqe_adapter_module.replace(".", "/") + ".py")
    seed_queries_path = root / seed_queries_str if seed_queries_str else None
    iqe_adapter_present = _file_exists(iqe_adapter_path) if iqe_adapter_module else None
    seed_queries_present = _file_exists(seed_queries_path) if seed_queries_str else None

    # Fallback: search common IQE seed-query directory name variants when not found.
    if not seed_queries_present and iqe_adapter_present:
        _iqe_query_root = root / "context" / "iqe" / "queries"
        if _iqe_query_root.exists():
            _name_variants = [key, key + "_canvas", key.replace("_canvas", ""), _module_leaf]
            for _var in _name_variants:
                _cand = _iqe_query_root / _var
                if _cand.exists() and any(_cand.iterdir()):
                    seed_queries_present = True
                    seed_queries_path = _cand
                    break

    iqe_present = iqe_adapter_present is True and seed_queries_present is True

    items: list[CanvasCompletenessItem] = [
        CanvasCompletenessItem(
            point="page_template",
            required=True,
            present=page_present,
            path=str(template_path.relative_to(root)) if page_present else template_path_str,
            message="page template exists" if page_present else f"missing {template_path_str}",
        ),
        CanvasCompletenessItem(
            point="icdev_mirror",
            # Only required when a main template exists; legacy canvases without
            # a template don't need the icdev/ mirror (companion sync would create it).
            required=page_present,
            present=mirror_present,
            path=str(icdev_template_path.relative_to(root)) if mirror_present else f"icdev/{template_path_str}",
            message="icdev mirror exists" if mirror_present else f"missing icdev/{template_path_str}",
        ),
        CanvasCompletenessItem(
            point="blueprint_route",
            required=True,
            present=route_present,
            path=str(blueprint_path.relative_to(root)) if blueprint_path.exists() else str(blueprint_path.relative_to(root)),
            message="blueprint contains @bp.route" if route_present else f"no @bp.route found in {module_dir}/blueprint.py",
        ),
        CanvasCompletenessItem(
            point="backing_module",
            required=True,
            present=backing_present,
            path=str(module_pkg.relative_to(root)) if module_pkg.exists() else module_dir,
            message="backing module .py present" if backing_present else f"no backing module .py in {module_dir}",
        ),
        CanvasCompletenessItem(
            point="constants",
            required=True,
            present=constants_present,
            path=str(constants_path.relative_to(root)) if constants_present else constants_path_str,
            message="constants.py exists" if constants_present else f"missing {constants_path_str}",
        ),
        CanvasCompletenessItem(
            point="db_migration",
            required=migration_path_str is not None,
            present=bool(migration_present),
            path=migration_path_str,
            message=f"DB migration dir exists: {migration_path_str}" if migration_present else f"missing {migration_path_str}",
        ),
        CanvasCompletenessItem(
            point="nav_link",
            required=not nav_link_declared_na,
            present=nav_link_present,
            path=None,
            message=(
                "nav link declared in registry"
                if nav_link_present
                else "nav link declared not applicable (completeness.nav_link: false)"
                if nav_link_declared_na
                else "missing nav_link or nav.section"
            ),
        ),
        CanvasCompletenessItem(
            point="iqe_integration",
            required=bool(iqe_adapter_module),
            present=iqe_present,
            path=str(iqe_adapter_path.relative_to(root)) if iqe_adapter_present else str(iqe_adapter_path) if iqe_adapter_path else None,
            message="IQE adapter + seed queries exist" if iqe_present else "missing IQE adapter or seed queries",
        ),
    ]

    passed = all(item.present or not item.required for item in items)
    return CanvasCompletenessReport(
        key=key,
        passed=passed,
        items=tuple(items),
        min_tier=comp.min_tier,
    )


_REGISTRY_SINGLETON: ComponentRegistry | None = None


def get_registry(
    registry_path: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> ComponentRegistry:
    """Return a registry instance.

    Uses a module-level singleton when called with default arguments to avoid
    repeated YAML parsing.
    """
    global _REGISTRY_SINGLETON
    if registry_path is None and env is None:
        if _REGISTRY_SINGLETON is None:
            _REGISTRY_SINGLETON = ComponentRegistry()
        return _REGISTRY_SINGLETON
    return ComponentRegistry(registry_path=registry_path, env=env)


def reset_registry_singleton() -> None:
    """Clear the cached singleton (useful in tests)."""
    global _REGISTRY_SINGLETON
    _REGISTRY_SINGLETON = None
