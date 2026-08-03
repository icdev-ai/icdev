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
from urllib.parse import quote

from tools.idp.constants import (
    CATALOG_COLUMNS,
    DEFAULT_LEVEL_COLOR,
    DEFAULT_SCORECARD_KEY,
    GRADE_BADGE,
    KIND_LABELS,
    STATUS_BADGE,
    UNASSESSED_BADGE,
    UNASSESSED_LABEL,
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
    the collection dropped) keep their catalog row with ``level=None``,
    ``score=None`` and ``letter_grade=None``. Substituting 0 or "F" would be a
    lie shaped exactly like a real failing grade — see ``assessed``, which is
    the flag the template branches on.
    """
    by_entity = {str(r.get("entity")): r for r in report.get("results") or []}
    colors = _ladder_colors(report)
    dimension_keys = [str(d.get("key")) for d in report.get("dimensions") or []]

    rows: list[dict[str, Any]] = []
    for fact in facts:
        key = str(fact.get("key"))
        result = by_entity.get(key)
        failures = (
            [o for o in (result.get("rules") or []) if o.get("status") == "fail"]
            if result
            else []
        )
        # Per-dimension cells, in the scorecard's declared order, so every row
        # has the same columns whether or not that component was assessed on
        # each one. A dimension missing from the result renders as a blank
        # cell, never as a zero.
        by_dim = {str(d.get("key")): d for d in (result or {}).get("dimensions") or []}
        rows.append({
            "key": key,
            "display_name": fact.get("display_name") or key,
            "kind": fact.get("kind") or "",
            "kind_label": KIND_LABELS.get(str(fact.get("kind")), str(fact.get("kind") or "")),
            "route": fact.get("route") or "",
            "owner": fact.get("owner") or "",
            "owner_contact": fact.get("owner_contact") or "",
            "on_call": fact.get("on_call") or "",
            "has_owner": bool(fact.get("has_owner")),
            "enabled": bool(fact.get("enabled")),
            "level": (result or {}).get("level"),
            "level_rank": (result or {}).get("level_rank", 0),
            "level_color": colors.get(str((result or {}).get("level")), DEFAULT_LEVEL_COLOR),
            "score": (result or {}).get("score"),
            "letter_grade": (result or {}).get("letter_grade"),
            "grade_class": GRADE_BADGE.get(
                str((result or {}).get("letter_grade") or ""), UNASSESSED_BADGE
            ),
            # `graded`  — the scorecard produced a row for this component at all
            # `assessed` — that row had at least one applicable rule to score
            # They differ, and conflating them is how "nobody measured it"
            # becomes "it scored zero".
            "graded": result is not None,
            "assessed": bool((result or {}).get("assessed")),
            "dimensions": [
                _dimension_cell(by_dim.get(dk), dk) for dk in dimension_keys
            ],
            "failure_count": len(failures),
            "failures": [str(o.get("identifier")) for o in failures],
            "completeness_declared": bool(fact.get("completeness_declared")),
            "completeness_passed": bool(fact.get("completeness_passed")),
            "completeness_points": int(fact.get("completeness_points") or 0),
            "facts": fact,
        })
    # Unassessed sorts last rather than first: `score or -1` keeps a real 0%
    # (measured, failing) above a None (never measured), which is the order an
    # engineer triaging the catalog wants.
    rows.sort(key=lambda r: (-(r["level_rank"] or 0), -(r["score"] if r["score"] is not None else -1), r["key"]))
    return rows


def _dimension_cell(dim: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """One catalog cell for one dimension — always present, possibly unassessed."""
    if not dim:
        return {
            "key": key,
            "label": key.replace("_", " ").title(),
            "score": None,
            "letter_grade": None,
            "assessed": False,
            "failure_count": 0,
            "grade_class": UNASSESSED_BADGE,
        }
    return {
        "key": str(dim.get("key") or key),
        "label": str(dim.get("label") or key),
        "score": dim.get("score"),
        "letter_grade": dim.get("letter_grade"),
        "assessed": bool(dim.get("assessed")),
        "failure_count": int(dim.get("failure_count") or 0),
        "grade_class": GRADE_BADGE.get(
            str(dim.get("letter_grade") or ""), UNASSESSED_BADGE
        ),
    }


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
        "dimensions": report.get("dimensions") or [],
        "grade_bands": report.get("grade_bands") or [],
        "grade_distribution": report.get("grade_distribution") or {},
        "grade_badge": dict(GRADE_BADGE),
        "unassessed_badge": UNASSESSED_BADGE,
        "unassessed_label": UNASSESSED_LABEL,
        "adapter_module": report.get("adapter_module", ""),
        "scorecard_source": report.get("source_path", ""),
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
            # Counted separately from `graded` on purpose: the headline number
            # the acceptance criterion cares about is how many components the
            # scorecard actually had something to say about.
            "assessed": sum(1 for r in rows if r["assessed"]),
            "unassessed": sum(1 for r in rows if not r["assessed"]),
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

    def _decorate(outcome: dict[str, Any]) -> dict[str, Any]:
        """Attach the rule title and a runnable IQE link to one outcome."""
        evidence = dict(outcome.get("evidence") or {})
        identifier = str(outcome.get("identifier") or "")
        return {
            **outcome,
            "title": rule_titles.get(identifier, ""),
            "evidence": evidence,
            # Resolves to rule_evidence() via GET /idp/evidence — which re-runs
            # the rule's own query live. The reader can re-derive the verdict
            # instead of trusting the badge.
            "evidence_url": (
                f"/idp/evidence?component={quote(key)}&rule={quote(identifier)}"
                f"&scorecard={quote(str(report.get('scorecard') or scorecard_key))}"
                if identifier
                else ""
            ),
            "status_class": STATUS_BADGE.get(str(outcome.get("status")), "bg-secondary"),
        }

    outcomes = [_decorate(o) for o in (result or {}).get("rules", [])]
    by_identifier = {str(o.get("identifier")): o for o in outcomes}
    dimensions = [
        {
            **dim,
            "grade_class": GRADE_BADGE.get(
                str(dim.get("letter_grade") or ""), UNASSESSED_BADGE
            ),
            # Same decorated outcome objects, grouped — so each dimension on the
            # page carries the evidence for its own score rather than pointing at
            # a table somewhere else.
            "outcomes": [
                by_identifier[str(o.get("identifier"))]
                for o in dim.get("rules") or []
                if str(o.get("identifier")) in by_identifier
            ],
        }
        for dim in (result or {}).get("dimensions") or []
    ]

    return {
        "key": key,
        "found": True,
        "display_name": fact.get("display_name") or key,
        "facts": fact,
        "level": (result or {}).get("level"),
        "score": (result or {}).get("score"),
        "letter_grade": (result or {}).get("letter_grade"),
        "grade_class": GRADE_BADGE.get(
            str((result or {}).get("letter_grade") or ""), UNASSESSED_BADGE
        ),
        "assessed": bool((result or {}).get("assessed")),
        "graded": result is not None,
        "unassessed_label": UNASSESSED_LABEL,
        "dimensions": dimensions,
        "outcomes": outcomes,
        "adapter_module": report.get("adapter_module", ""),
        "scorecard_source": report.get("source_path", ""),
        "scorecard_key": report.get("scorecard", scorecard_key),
        "completeness": completeness_points(key),
        "scorecard_error": report.get("error", ""),
    }


#: Fact fields that are derived from ``awareness_component_health`` probe rows.
#: A rule reading one of these gets the underlying rows attached as evidence.
PROBE_DERIVED_FIELDS = frozenset({"failing_probes", "health_probed"})


def rule_evidence(
    component: str,
    identifier: str,
    scorecard_key: str = DEFAULT_SCORECARD_KEY,
    conn: Any = None,
) -> dict[str, Any]:
    """Re-derive one rule's verdict for one component, from source.

    This is what the per-dimension evidence links resolve to. It does not read
    a cached verdict and hand it back — it re-runs the rule's own IQE query
    live and reports the passing set, so the caller can see the check execute
    rather than take the badge's word for it.

    Includes, where they exist:
      * the exact IQE expression and filter, runnable by hand
      * the component's observed value for every fact field the rule reads
      * the probe rows behind a probe-derived fact
      * the per-point breakdown behind the 8-point completeness fact
    """
    report = scorecard_report(scorecard_key, conn=conn)
    if report.get("error"):
        return {"found": False, "error": report["error"], "component": component, "rule": identifier}

    rule = next(
        (r for r in report.get("rules") or [] if str(r.get("identifier")) == identifier), None
    )
    if rule is None:
        return {
            "found": False,
            "error": f"no rule {identifier!r} in scorecard {scorecard_key!r}",
            "component": component,
            "rule": identifier,
        }

    result = next(
        (r for r in report.get("results") or [] if str(r.get("entity")) == component), None
    )
    outcome = next(
        (o for o in (result or {}).get("rules") or [] if str(o.get("identifier")) == identifier),
        None,
    )
    if outcome is None:
        return {
            "found": False,
            "error": f"component {component!r} is not in this scorecard",
            "component": component,
            "rule": identifier,
        }

    evidence = dict(outcome.get("evidence") or {})
    fact = _fact_index(component_facts(conn=conn)).get(component) or {}
    fields = list(evidence.get("fields") or [])

    sources: list[dict[str, Any]] = []
    if PROBE_DERIVED_FIELDS.intersection(fields):
        rows = _probe_rows(str(fact.get("route") or ""), conn=conn)
        sources.append({
            "kind": "probe_rows",
            "table": "awareness_component_health",
            "label": "HTTP probe rows for this component's routes",
            "rows": rows,
            # An empty list here is "never probed", and the template says so.
            # Rendering it as "0 failures" would turn silence into a pass.
            "measured": bool(rows),
        })
    if "completeness_passed" in fields or "completeness_points" in fields:
        points = completeness_points(component)
        sources.append({
            "kind": "completeness_gate",
            "table": "",
            "label": "8-point dashboard-page completeness gate",
            "rows": points.get("items") or [],
            "measured": bool(points.get("declared")),
        })

    # Re-run the rule live rather than trusting the stored verdict.
    passing = _run_rule(scorecard_key, str(rule.get("expression") or ""), conn=conn)

    return {
        "found": True,
        "component": component,
        "rule": identifier,
        "title": rule.get("title", ""),
        "dimension": rule.get("dimension", ""),
        "weight": rule.get("weight", 0),
        "level": rule.get("level"),
        "status": outcome.get("status"),
        "message": outcome.get("message", ""),
        "evidence": evidence,
        "scorecard": scorecard_key,
        "scorecard_source": report.get("source_path", ""),
        "adapter_module": report.get("adapter_module", ""),
        "passing_count": len(passing),
        "component_passes": component in passing,
        "sources": sources,
    }


def _run_rule(scorecard_key: str, expression: str, conn: Any = None) -> set[str]:
    """Execute one rule expression live and return the entity keys it matched."""
    if not expression:
        return set()
    try:
        from tools.idp.scorecard import _run_query, load_scorecard  # noqa: PLC0415

        card = load_scorecard(scorecard_key)
        return set(_run_query(expression, card, conn, where="evidence"))
    except Exception:  # noqa: BLE001
        return set()


def _probe_rows(route: str, conn: Any = None) -> list[dict[str, Any]]:
    try:
        from tools.iqe.adapters.idp import probe_evidence  # noqa: PLC0415

        return probe_evidence(route, conn=conn)
    except Exception:  # noqa: BLE001
        return []


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
