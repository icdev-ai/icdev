# CUI // SP-CTI
"""IQE adapter — Internal Developer Portal (IDP).

Collections:
    idp.components — one fact row per component in args/component_registry.yaml

This is the collection scorecard rules query.  Registering the component
catalog as an ordinary IQE collection is the whole point of idp-score-02: a
scorecard rule is an IQE expression, so ICDEV does not need a scorecard DSL of
its own — the rule language is already implemented, sandboxed and reachable
from the existing query surface.

Example rules (see args/scorecards/*.yaml):

    foreach c in idp.components where c.owned == true select c.key
    foreach c in idp.components where c.kind == 'canvas' select c.key
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def components_adapter(conn: Any = None, window_days: Any = None) -> list[dict]:
    """Return the component fact rows.

    Args:
        conn: Open DB connection supplied by the executor. Facts that need no
            DB are computed regardless of whether it is usable.
        window_days: Optional evidence window for time-series facts, passed
            through from a parameterised collection call such as
            ``idp.components(30)``.
    """
    from tools.idp.component_facts import DEFAULT_WINDOW_DAYS, build_component_facts, parse_window

    days = parse_window(window_days, DEFAULT_WINDOW_DAYS) if window_days is not None else DEFAULT_WINDOW_DAYS
    try:
        return build_component_facts(window_days=days, conn=conn)
    except Exception:  # noqa: BLE001 — an unqueryable catalog is empty, not fatal
        return []


register_collection("idp.components", components_adapter)
