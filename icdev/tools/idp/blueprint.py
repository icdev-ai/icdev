# CUI // SP-CTI
"""Internal Developer Portal blueprint (idp-ui-02).

Mounted at ``/idp`` by ``tools/dashboard/app.py`` from the ``url_prefix`` in
``args/component_registry.yaml`` — the blueprint deliberately declares no
prefix of its own, so the registry stays the single source of truth for where
this page lives.

All 8 components of the CLAUDE.md dashboard-page gate:
  1. template            tools/dashboard/templates/idp/page.html
  2. icdev mirror        icdev/tools/dashboard/templates/idp/page.html
  3. route               @bp.route("/") — this file
  4. backing module      tools/idp/portal.py (+ tools/idp/scorecard.py)
  5. constants           tools/idp/constants.py
  6. DB                  tools/idp/db/init_db.py — no new tables; reports which
                         optional backing tables exist so absent ones render as
                         "not measured" rather than as a passing zero
  7. nav link            registry nav.links → Canvases menu (base.html)
  8. IQE                 tools/iqe/adapters/idp.py + POST /idp/api/iqe-query +
                         includes/iqe_query_widget.html + registry-derived
                         _IQE_CANVAS_MAP and PATH_CANVAS entries + four seed
                         queries under context/iqe/queries/idp/

Routes:
  GET  /idp/                        Portal — overview, catalog, scorecard
  GET  /idp/catalog                 Portal focused on the catalog
  GET  /idp/scorecards              Portal focused on the scorecard
  GET  /idp/component/<key>         One component's facts, rules and 8 points
  GET  /idp/evidence                Why one rule landed as it did (idp-ui-01)
  GET  /idp/api/catalog             JSON catalog
  GET  /idp/api/scorecard           JSON scorecard report
  GET  /idp/api/component/<key>     JSON component detail
  GET  /idp/api/evidence            JSON rule evidence
  POST /idp/api/iqe-query           IQE natural-language query over idp.components
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request as flask_request

from tools.idp.constants import (
    DEFAULT_SCORECARD_KEY,
    IQE_API_ROUTE,
    IQE_CANVAS,
    IQE_COLLECTION,
    IQE_EXAMPLES,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

bp = Blueprint("idp", __name__)


def _requested_scorecard() -> str:
    return (flask_request.args.get("scorecard") or DEFAULT_SCORECARD_KEY).strip()


def _refresh_requested() -> bool:
    return (flask_request.args.get("refresh") or "").strip().lower() in ("1", "true", "yes")


def _render(focus: str):
    from tools.idp.portal import portal_overview

    data = portal_overview(
        scorecard_key=_requested_scorecard(),
        refresh=_refresh_requested(),
    )
    return render_template(
        "idp/page.html",
        focus=focus,
        iqe_canvas=IQE_CANVAS,
        iqe_api_route=IQE_API_ROUTE,
        iqe_examples=list(IQE_EXAMPLES),
        iqe_title="Ask the component catalog",
        **data,
    )


@bp.route("/")
def idp_portal():
    """Portal home — ladder, catalog and the portal's own grade."""
    return _render("overview")


@bp.route("/catalog")
def idp_catalog_page():
    """Same page, catalog section expanded."""
    return _render("catalog")


@bp.route("/scorecards")
def idp_scorecards_page():
    """Same page, scorecard section expanded."""
    return _render("scorecard")


@bp.route("/evidence")
def idp_evidence_page():
    """Why one rule landed the way it did for one component.

    Every per-dimension score on the catalog and the component page links
    here. The page re-runs the rule's own IQE query rather than replaying a
    stored verdict, and shows the fact fields, the probe rows and the 8-point
    breakdown behind it — a score whose source cannot be traced is an
    assertion, and this is where the trace lands.
    """
    from tools.idp.portal import rule_evidence

    component = (flask_request.args.get("component") or "").strip()
    rule = (flask_request.args.get("rule") or "").strip()
    widget = {
        "iqe_canvas": IQE_CANVAS,
        "iqe_api_route": IQE_API_ROUTE,
        "iqe_examples": list(IQE_EXAMPLES),
        "iqe_title": "Ask the component catalog",
    }
    if not component or not rule:
        return render_template(
            "idp/evidence.html",
            evidence={"found": False, "error": "component and rule are both required"},
            **widget,
        ), 400

    detail = rule_evidence(component, rule, scorecard_key=_requested_scorecard())
    return render_template("idp/evidence.html", evidence=detail, **widget), (
        200 if detail.get("found") else 404
    )


@bp.route("/component/<key>")
def idp_component_page(key: str):
    """One component: its facts, its per-rule outcomes, its 8 points."""
    from tools.idp.portal import component_detail

    detail = component_detail(key, scorecard_key=_requested_scorecard())
    return render_template(
        "idp/component.html",
        detail=detail,
        iqe_canvas=IQE_CANVAS,
        iqe_api_route=IQE_API_ROUTE,
        iqe_examples=list(IQE_EXAMPLES),
        iqe_title="Ask the component catalog",
    ), (200 if detail.get("found") else 404)


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@bp.route("/api/catalog")
def idp_api_catalog():
    """JSON catalog — one row per registered component."""
    from tools.idp.portal import build_catalog, component_facts, scorecard_report

    facts = component_facts(refresh=_refresh_requested())
    report = scorecard_report(_requested_scorecard())
    rows = build_catalog(facts, report)
    return jsonify({
        "count": len(rows),
        "scorecard": report.get("scorecard"),
        "scorecard_error": report.get("error", ""),
        # `facts` is the whole upstream fact row and is already on each entry;
        # dropping it keeps the API payload to what a catalog consumer needs.
        "components": [{k: v for k, v in row.items() if k != "facts"} for row in rows],
    })


@bp.route("/api/scorecard")
def idp_api_scorecard():
    """JSON scorecard report for ``?scorecard=<key>`` (default component-readiness)."""
    from tools.idp.portal import scorecard_report

    report = scorecard_report(_requested_scorecard())
    return jsonify(report), (500 if report.get("error") else 200)


@bp.route("/api/component/<key>")
def idp_api_component(key: str):
    """JSON detail for one component, including its 8-point breakdown."""
    from tools.idp.portal import component_detail

    detail = component_detail(key, scorecard_key=_requested_scorecard())
    return jsonify(detail), (200 if detail.get("found") else 404)


@bp.route("/api/evidence")
def idp_api_evidence():
    """JSON form of /idp/evidence — ``?component=<key>&rule=<identifier>``."""
    from tools.idp.portal import rule_evidence

    component = (flask_request.args.get("component") or "").strip()
    rule = (flask_request.args.get("rule") or "").strip()
    if not component or not rule:
        return jsonify({"error": "component and rule are both required"}), 400

    detail = rule_evidence(component, rule, scorecard_key=_requested_scorecard())
    return jsonify(detail), (200 if detail.get("found") else 404)


@bp.route("/api/iqe-query", methods=["POST"])
def idp_api_iqe_query():
    """IQE natural-language query over ``idp.components`` (gate point 8)."""
    from tools.iqe.executor import execute_query
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import IQESyntaxError, parse
    import tools.iqe.adapters.idp  # noqa: F401  (registers the collection)

    data = flask_request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    translation = nl_to_iqe(question, [IQE_COLLECTION])
    iqe_str = translation.get("iqe", "")
    explanation = translation.get("explanation", "")

    if not data.get("execute", True):
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation}), 200

    try:
        ast = parse(iqe_str)
        rows = execute_query(ast, None)
        return jsonify({
            "ok": True,
            "iqe": iqe_str,
            "explanation": explanation,
            "results": rows,
            "row_count": len(rows),
        }), 200
    except IQESyntaxError as exc:
        return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
    except Exception as exc:  # noqa: BLE001
        logger.warning("IDP IQE query error: %s", exc)
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500
