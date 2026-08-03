# CUI // SP-CTI
"""Tenant scoping for the Internal Developer Portal (idp-mt-01).

The IDP ships in two directions at once: inward, as ICDEV governing its own 66
registered components, and outward, as the same catalog-plus-scorecard surface
offered to a customer over their services. Those two readings need different
answers to the same question — *which components does this scorecard cover?* —
and until this module existed there was only one answer: all of them.

MEASURED 2026-08-02. None of the scoring or catalog tables carried a
``tenant_id`` at all: ``readiness_scores``, ``production_audits``,
``developer_scorecards``, ``kg_nodes``, ``awareness_component_health`` and
``kanban_tasks`` were checked against ``information_schema`` and none had the
column. Component-level scoring was globally scoped by construction, not by
oversight.

What this module owns
---------------------
**The scope.** :func:`enabled_component_keys` answers "which components has
THIS tenant got enabled", using the ``tenant_component_overrides`` table
(migration 207) that already exists and works. A tenant's scorecard must cover
the components that tenant has enabled, not the global set — otherwise the
scorecard grades them on canvases they cannot reach, and the grade is a
statement about somebody else's estate.

**One choke point.** Scoping is applied inside the ``idp.components`` IQE
adapter, which is the single fact source behind *every* IDP read path: the
scorecard universe, the per-rule queries, the fact rows behind evidence, the
catalog, and the free-text IQE widget on the page. Filtering there means a new
read path is scoped the day it is written rather than the day someone
remembers. Filtering in :func:`~tools.idp.scorecard.evaluate` alone would have
left ``POST /idp/api/iqe-query`` — arbitrary IQE over the collection — serving
the whole estate to any tenant that asked.

**Resolution order**, most explicit first, in :func:`active_tenant_id`:

  1. an explicit :func:`tenant_scope` binding (what ``evaluate(tenant_id=…)``
     and the recorder use, so a caller's argument always beats ambient state)
  2. the Flask request's tenant, via ``tools.saas.auth.middleware``
  3. ``ICDEV_TENANT_ID`` in the environment, for CLI and reflex runs
  4. ``None`` — the platform's own view, covering every registered component

``None`` is not "no tenant found, fail open". It is the internal/inward
reading, and it is the *only* reading that sees the whole estate. A request
carrying a tenant is scoped; the CLI is scoped when told to be. Isolation of
the request itself — is this caller really that tenant — is the auth
middleware's job, not this module's.

Isolation posture
-----------------
This module scopes rows in application code. That is the platform's ONLY
isolation mechanism today: PostgreSQL RLS is enabled on 0 of 1,737 tables and
``pg_policies`` is empty. Whether app-layer-only isolation is acceptable for
this surface when it is offered externally is a judgement call for a human, and
:func:`posture` reads that decision from ``args/idp_tenancy.yaml`` — backed by
``docs/security/idp-tenant-isolation-posture.md`` — rather than assuming it.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

BASE_DIR = Path(__file__).resolve().parent.parent.parent

#: Config carrying the machine-readable half of the isolation-posture decision.
POSTURE_CONFIG = BASE_DIR / "args" / "idp_tenancy.yaml"

#: Environment variable naming the tenant for a CLI or reflex run.
TENANT_ENV = "ICDEV_TENANT_ID"

#: Posture values used when the config is missing or unreadable. Deliberately
#: the *unapproved* state: a surface whose approval record cannot be read has
#: not been approved.
POSTURE_FALLBACK: dict[str, Any] = {
    "isolation_posture": "app_layer_only",
    "external_offering_approved": False,
    "approved_by": "",
    "approved_on": "",
    "decision_record": "docs/security/idp-tenant-isolation-posture.md",
    "unapproved_notice": (
        "IDP scoring is tenant-scoped in application code only. PostgreSQL-native "
        "row-level security is not in force on these tables. Serving this surface "
        "to an external tenant is not yet approved — see the decision record."
    ),
}

# An explicit binding beats ambient request/env state. The sentinel matters:
# ``(True, None)`` is "scoped to the platform view on purpose" and
# ``(False, None)`` is "nothing bound, go look at the request".
_SCOPE: ContextVar[tuple[bool, str | None]] = ContextVar(
    "idp_tenant_scope", default=(False, None)
)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _request_tenant_id() -> str | None:
    """The current Flask request's tenant, or None outside a request."""
    try:
        from tools.saas.auth.middleware import get_current_tenant_id  # noqa: PLC0415

        return get_current_tenant_id()
    except Exception:  # noqa: BLE001 — no Flask, no request context, no SaaS layer
        return None


def active_tenant_id() -> str | None:
    """Resolve the tenant this read is scoped to. See the module docstring."""
    bound, value = _SCOPE.get()
    if bound:
        return value or None
    return _request_tenant_id() or (os.environ.get(TENANT_ENV) or "").strip() or None


@contextmanager
def tenant_scope(tenant_id: str | None) -> Iterator[str | None]:
    """Bind *tenant_id* for the duration of the block.

    A context variable rather than a module global because the dashboard serves
    concurrent requests: a global would let one tenant's scope leak into
    another's render. Binding ``None`` is meaningful — it pins the platform
    view even inside a tenant's request, which is what the internal reflex
    needs when it runs under a web worker.
    """
    token = _SCOPE.set((True, (tenant_id or None)))
    try:
        yield tenant_id or None
    finally:
        _SCOPE.reset(token)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def _override_map(tenant_id: str, conn: Any = None) -> dict[str, bool]:
    """``{component_key: enabled}`` overrides for one tenant.

    Returns ``{}`` when the table is absent or unreadable, which reproduces
    ``ComponentRegistry.is_enabled_for_tenant``'s fallback exactly: resolution
    then lands on the env/default set. That is the same set the canvas access
    guard falls through to, so a degraded lookup can never make the scorecard
    broader than what the guard would admit.
    """
    if not tenant_id:
        return {}

    sql = (
        "SELECT component_key, enabled FROM tenant_component_overrides "
        "WHERE tenant_id = %s"
    )
    own = conn is None
    try:
        if own:
            from tools.db.storage import get_connection  # noqa: PLC0415

            conn = get_connection()
        rows = conn.execute(sql, (tenant_id,)).fetchall() or []
    except Exception:  # noqa: BLE001
        # A failed statement leaves a PostgreSQL transaction aborted, and every
        # later query on the caller's connection then reports "relation does not
        # exist" whether or not it does. Roll back so the caller's connection
        # survives a missing overrides table.
        if not own and conn is not None:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001, S110
                pass
        return {}
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass

    overrides: dict[str, bool] = {}
    for row in rows:
        try:
            if isinstance(row, (tuple, list)):
                key, enabled = row[0], row[1]
            else:
                mapping = dict(row)
                key, enabled = mapping["component_key"], mapping["enabled"]
            overrides[str(key)] = bool(int(enabled))
        except Exception:  # noqa: BLE001, S112
            continue
    return overrides


def enabled_component_keys(
    tenant_id: str | None, conn: Any = None, registry: Any = None
) -> frozenset[str] | None:
    """Component keys visible to *tenant_id*, or ``None`` for the platform view.

    ``None`` means "no scope" — every registered component — and is what an
    unauthenticated/internal read gets. A real tenant gets the resolution the
    registry already implements: an explicit ``tenant_component_overrides`` row
    wins, otherwise the environment flag plus ``default_enabled``.

    Batched into ONE query on purpose. ``is_enabled_for_tenant`` opens its own
    connection per key, and the catalog asks about ~66 keys on every rule of
    every scorecard evaluation.
    """
    if not tenant_id:
        return None
    try:
        if registry is None:
            from tools.config.component_registry import get_registry  # noqa: PLC0415

            registry = get_registry()
        components = list(registry.list_all())
    except Exception:  # noqa: BLE001
        # No registry means no way to know what the tenant has. Returning the
        # empty scope is the fail-closed answer; returning None would hand the
        # whole estate to a tenant because a YAML failed to load.
        return frozenset()

    overrides = _override_map(tenant_id, conn)
    return frozenset(
        comp.key
        for comp in components
        if overrides.get(comp.key, _env_enabled(registry, comp))
    )


def _env_enabled(registry: Any, comp: Any) -> bool:
    """Env/default enablement for one component, degrading to its default."""
    try:
        return bool(registry.is_enabled(comp.key))
    except Exception:  # noqa: BLE001
        return bool(getattr(comp, "default_enabled", False))


def scope_component_rows(
    rows: list[dict[str, Any]],
    tenant_id: str | None,
    conn: Any = None,
    key_field: str = "key",
) -> list[dict[str, Any]]:
    """Filter component fact rows to those *tenant_id* has enabled."""
    scope = enabled_component_keys(tenant_id, conn)
    if scope is None:
        return rows
    return [row for row in rows if str(row.get(key_field)) in scope]


# ---------------------------------------------------------------------------
# Posture
# ---------------------------------------------------------------------------


def posture() -> dict[str, Any]:
    """The recorded isolation-posture decision, as data.

    Reads ``args/idp_tenancy.yaml`` — the machine-readable half of
    ``docs/security/idp-tenant-isolation-posture.md``. Never raises: an
    unreadable config yields :data:`POSTURE_FALLBACK`, which reports the
    surface as *unapproved*, because a decision nobody can read is not one that
    has been made.
    """
    values = dict(POSTURE_FALLBACK)
    try:
        import yaml  # noqa: PLC0415

        with open(POSTURE_CONFIG, "r", encoding="utf-8") as handle:
            data = (yaml.safe_load(handle) or {}).get("tenancy") or {}
        for field in values:
            if field in data and data[field] is not None:
                values[field] = data[field]
        values["external_offering_approved"] = bool(
            values["external_offering_approved"]
        ) and bool(str(values["approved_by"]).strip())
    except Exception:  # noqa: BLE001
        values["config_error"] = f"unreadable: {POSTURE_CONFIG.name}"
    values["config_path"] = "args/idp_tenancy.yaml"
    return values


def tenant_context(tenant_id: str | None = None, conn: Any = None) -> dict[str, Any]:
    """The scoping facts a caller should render or return alongside scores.

    A scoped payload that does not say it is scoped invites the reader to
    mistake a tenant's 12 components for the platform's 66.
    """
    resolved = tenant_id if tenant_id is not None else active_tenant_id()
    scope = enabled_component_keys(resolved, conn)
    stance = posture()
    scoped = scope is not None
    return {
        "tenant_id": resolved,
        "scoped": scoped,
        "scope_size": (len(scope) if scope is not None else None),
        "isolation_posture": stance.get("isolation_posture"),
        "external_offering_approved": bool(stance.get("external_offering_approved")),
        "decision_record": stance.get("decision_record"),
        # Only when it applies: an internal render is not an external offering,
        # so it must not carry an external-offering warning.
        "notice": (
            str(stance.get("unapproved_notice") or "")
            if scoped and not stance.get("external_offering_approved")
            else ""
        ),
    }
