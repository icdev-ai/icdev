# CUI // SP-CTI
"""View models for the Internal Developer Portal page (idp-ui-02).

Point 4 of the CLAUDE.md 8-point dashboard-page gate — the backing module the
blueprint imports.

This module owns no policy. It joins two things that already exist:

  * the facts in the ``idp.components`` IQE collection
    (``tools/iqe/adapters/idp.py``), and
  * the ladder + rules in ``args/scorecards/*.yaml``, evaluated by
    ``tools/idp/scorecard.py``,

into the shapes a Jinja template can render without logic in it. Adding a rule
or a level still means editing YAML and nothing here.

Failure posture: every entry point degrades rather than raising. A malformed
scorecard, an unreachable database or an unimportable adapter costs the page
its scores, not its catalog — a portal that 500s tells an on-call engineer
strictly less than one that renders the catalog and names what is dark.
"""
from __future__ import annotations

from typing import Any

from tools.idp.constants import (
    CATALOG_COLUMNS,
    DEFAULT_LEVEL_COLOR,
    DEFAULT_SCORECARD_KEY,
    KIND_LABELS,
    STATUS_BADGE,
)

#: Registry key of the portal itself — used by :func:`self_check` so the page
#: can show its own grade. "Eat the dog food" is a design constraint here, not
#: a slogan: the portal is the only component that can be *wrong about itself*
#: in a way nobody else would notice.
SELF_KEY = "idp"


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def component_facts(conn: Any = None, refresh: bool = False) -> list[dict]:
    """Return one fact row per registered component, or [] if unavailable."""
    try:
        from tools.iqe.adapters.idp import components_adapter, reset_cache  # noqa: PLC0415

        if refresh:
            reset_cache()
        return list(components_adapter(conn))
    except Exception:  # noqa: BLE001
        return []


def _fact_index(facts: list[dict]) -> dict[str, dict]:
    return {str(f.get("key")): f for f in facts}


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


def scorecard_report(
    key: str = DEFAULT_SCORECARD_KEY,
    conn: Any = None,
    directory: Any = None,
) -> dict[str, Any]:
    """Evaluate one scorecard, or return an ``error`` key describing why not.

    The error is returned rather than raised so the caller can render the rest
    of the page. A scorecard that cannot be parsed is an authoring bug worth
    showing on the page that depends on it.
    """
    try:
        from tools.idp.scorecard import evaluate, load_scorecard  # noqa: PLC0415

        card = load_scorecard(key, directory)
        return evaluate(card, conn=conn)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "scorecard": key, "results": [], "rules": [], "ladder": []}


def available_scorecards(directory: Any = None) -> list[dict[str, str]]:
    """``[{key, name}]`` for every scorecard on disk; [] when none load."""
    try:
        from tools.idp.scorecard import load_scorecards  # noqa: PLC0415

        return [{"key": c.key, "name": c.name} for c in load_scorecards(directory)]
    except Exception:  # noqa: BLE001
        return []


def _ladder_colors(report: dict[str, Any]) -> dict[str, str]:
    return {
        str(level.get("name")): str(level.get("color") or DEFAULT_LEVEL_COLOR)
        for level in report.get("ladder") or []
    }


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def build_catalog(facts: list[dict], report: dict[str, Any]) -> list[dict[str, Any]]:
    """Join facts with scorecard results into one row per component.

    Components with no scorecard result (an unparseable scorecard, or an entity
    the collection dropped) keep their catalog row with ``level=None`` and
    ``score=None``. Substituting 0 would be a lie shaped exactly like a real
    failing grade.
    """
    by_entity = {str(r.get("entity")): r for r in report.get("results") or []}
    colors = _ladder_colors(report)

    rows: list[dict[str, Any]] = []
    for fact in facts:
        key = str(fact.get("key"))
        result = by_entity.get(key)
        failures = (
            [o for o in (result.get("rules") or []) if o.get("status") == "fail"]
            if result
            else []
        )
        rows.append({
            "key": key,
            "display_name": fact.get("display_name") or key,
            "kind": fact.get("kind") or "",
            "kind_label": KIND_LABELS.get(str(fact.get("kind")), str(fact.get("kind") or "")),
            "route": fact.get("route") or "",
            "owner": fact.get("owner") or "",
            "has_owner": bool(fact.get("has_owner")),
            "enabled": bool(fact.get("enabled")),
            "level": (result or {}).get("level"),
            "level_rank": (result or {}).get("level_rank", 0),
            "level_color": colors.get(str((result or {}).get("level")), DEFAULT_LEVEL_COLOR),
            "score": (result or {}).get("score"),
            "graded": result is not None,
            "failure_count": len(failures),
            "failures": [str(o.get("identifier")) for o in failures],
            "completeness_declared": bool(fact.get("completeness_declared")),
            "completeness_passed": bool(fact.get("completeness_passed")),
            "completeness_points": int(fact.get("completeness_points") or 0),
            "facts": fact,
        })
    rows.sort(key=lambda r: (-(r["level_rank"] or 0), -(r["score"] or 0), r["key"]))
    return rows


def group_by_kind(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group catalog rows by registry ``kind``, preserving KIND_LABELS order.

    A kind absent from KIND_LABELS still renders, under its raw name — a new
    registry kind must appear in the catalog the day it is added, not the day
    someone remembers to update this module.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["kind"], []).append(row)

    ordered = [k for k in KIND_LABELS if k in groups]
    ordered += sorted(k for k in groups if k not in KIND_LABELS)
    return [
        {"kind": k, "label": KIND_LABELS.get(k, k or "(unspecified)"), "rows": groups[k]}
        for k in ordered
    ]


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------


def portal_overview(
    scorecard_key: str = DEFAULT_SCORECARD_KEY,
    conn: Any = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Everything /idp renders, in one call."""
    facts = component_facts(conn=conn, refresh=refresh)
    report = scorecard_report(scorecard_key, conn=conn)
    rows = build_catalog(facts, report)

    canvases = [r for r in rows if r["completeness_declared"]]
    return {
        "scorecard_key": scorecard_key,
        "scorecard_name": report.get("name") or scorecard_key,
        "scorecard_error": report.get("error", ""),
        "evaluated_on": report.get("evaluated_on", ""),
        "window": report.get("window", ""),
        "ladder": report.get("ladder") or [],
        "level_distribution": report.get("level_distribution") or {},
        "rules": report.get("rules") or [],
        "rows": rows,
        "groups": group_by_kind(rows),
        "columns": list(CATALOG_COLUMNS),
        "status_badge": dict(STATUS_BADGE),
        "scorecards": available_scorecards(),
        "schema_status": schema_status(conn=conn),
        "totals": {
            "components": len(rows),
            "graded": sum(1 for r in rows if r["graded"]),
            "unowned": sum(1 for r in rows if not r["has_owner"]),
            "canvases": len(canvases),
            "completeness_passing": sum(1 for r in canvases if r["completeness_passed"]),
        },
        # Not "self": Jinja binds `self` to the template's own block namespace,
        # so a context variable of that name is silently shadowed and every
        # `self.x` renders as Undefined.
        "self_report": self_check(rows=rows, report=report),
    }


def schema_status(conn: Any = None) -> list[dict[str, Any]]:
    """Presence of the optional backing tables (see tools/idp/db/init_db.py)."""
    try:
        from tools.idp.db.init_db import schema_status as _status  # noqa: PLC0415

        return _status(conn)
    except Exception:  # noqa: BLE001
        return []


def component_detail(
    key: str,
    scorecard_key: str = DEFAULT_SCORECARD_KEY,
    conn: Any = None,
) -> dict[str, Any]:
    """Facts, per-rule outcomes and the 8-point breakdown for one component."""
    facts = component_facts(conn=conn)
    fact = _fact_index(facts).get(key)
    if fact is None:
        return {"key": key, "found": False}

    report = scorecard_report(scorecard_key, conn=conn)
    result = next(
        (r for r in report.get("results") or [] if str(r.get("entity")) == key), None
    )
    rule_titles = {str(r["identifier"]): str(r.get("title") or "") for r in report.get("rules") or []}
    outcomes = [
        {**o, "title": rule_titles.get(str(o.get("identifier")), "")}
        for o in (result or {}).get("rules", [])
    ]
    return {
        "key": key,
        "found": True,
        "display_name": fact.get("display_name") or key,
        "facts": fact,
        "level": (result or {}).get("level"),
        "score": (result or {}).get("score"),
        "outcomes": outcomes,
        "completeness": completeness_points(key),
        "scorecard_error": report.get("error", ""),
    }


def completeness_points(key: str) -> dict[str, Any]:
    """Per-point 8-point gate breakdown for one canvas.

    ``declared`` is False for a non-canvas component: the gate is a
    dashboard-page gate, and grading a headless feature against it would
    manufacture failures nobody can fix.
    """
    try:
        from tools.config.component_registry import (  # noqa: PLC0415
            get_registry,
            validate_canvas_completeness,
        )

        registry = get_registry()
        comp = registry.get(key)
        if comp is None or comp.kind != "canvas":
            return {"declared": False, "passed": False, "items": []}
        report = validate_canvas_completeness(key, registry=registry)
        return {
            "declared": True,
            "passed": bool(report.passed),
            "items": [
                {
                    "point": item.point,
                    "required": item.required,
                    "present": item.present,
                    "path": item.path,
                    "message": item.message,
                }
                for item in report.items
            ],
        }
    except Exception as exc:  # noqa: BLE001
        return {"declared": False, "passed": False, "items": [], "error": str(exc)}


def self_check(
    rows: list[dict[str, Any]] | None = None,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The portal's own catalog row and 8-point breakdown.

    The dogfood check. A component catalog that cannot find itself is not a
    catalog, and a scorecard its own surface is exempt from is a scorecard
    nobody has tested. Both conditions are visible on the page and asserted by
    ``tests/test_idp_portal.py``.
    """
    if rows is None:
        report = report if report is not None else scorecard_report()
        rows = build_catalog(component_facts(), report)
    row = next((r for r in rows if r["key"] == SELF_KEY), None)
    completeness = completeness_points(SELF_KEY)
    return {
        "key": SELF_KEY,
        "in_catalog": row is not None,
        "row": row,
        "level": (row or {}).get("level"),
        "score": (row or {}).get("score"),
        "completeness_passed": bool(completeness.get("passed")),
        "completeness": completeness,
    }
