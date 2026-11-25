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

Engineering standards register (icx-coe-01, tools/idp/standards.py):
  GET  /idp/standards                            The register: browse, lifecycle, take a step
  GET  /idp/api/standards                        JSON register (every standard, its state)
  GET  /idp/api/standards/<standard_id>          One standard: versions, history, checks, sources
  POST /idp/api/standards/propose                A draft (anyone; decides nothing)
  POST /idp/api/standards/<standard_id>/review   A steward endorses or returns (declared step)
  POST /idp/api/standards/<standard_id>/adopt    A steward signs it into force (declared step)
  POST /idp/api/standards/<standard_id>/deprecate  A steward retires it (declared step)
  GET  /idp/api/standards-twin                   The register's twin observation, live, with threads
                                                 (also the exemptions and the ADRs: canvas idp)

Exemptions that expire (icx-coe-02, tools/idp/exemptions.py):
  GET  /idp/exemptions                           Every exemption, its scope and expiry state
  GET  /idp/api/exemptions                       JSON (``?as_of=`` looks ahead), with the gate
  GET  /idp/api/exemptions/<ref>                 One exemption: history, chain, digest, next step
  POST /idp/api/exemptions/request               A request (anyone; decides nothing)
  POST /idp/api/exemptions/<ref>/decide          A person grants or denies (declared step)
  POST /idp/api/exemptions/<ref>/renewal         Ask for a renewal (decides nothing)
  POST /idp/api/exemptions/<ref>/renew           A person renews or refuses (declared step)
  POST /idp/api/exemptions/<ref>/revoke          A stop: immediate, with a reason

ADRs as records (icx-coe-02, tools/idp/adrs.py):
  GET  /idp/adrs                                 Every ADR: status, links, lifecycle
  GET  /idp/api/adrs                             JSON (records merged with the ledger; ``?q=``)
  GET  /idp/api/adrs/<adr_id>                    One ADR: record, versions, history, next step
  POST /idp/api/adrs/propose                     A draft (anyone; decides nothing)
  POST /idp/api/adrs/<adr_id>/accept             A person accepts or returns (declared step)
  POST /idp/api/adrs/<adr_id>/supersede          A person supersedes or deprecates (declared step)

Scorecards against adopted standards and conformance bundles (icx-coe-03, tools/idp/conformance.py):
  GET  /idp/conformance                          Per repo: standards scored, delivery evidence, the bundle
  GET  /idp/api/conformance                      JSON: every declared repo and its ledger state
  GET  /idp/api/conformance/<repo>               JSON: the repo's bundle as it would be prepared now (?as_of=)
  POST /idp/api/conformance/<repo>/prepare       Build and record a bundle at its digest (decides nothing)
  POST /idp/api/conformance/<repo>/publish       A person publishes or refuses it (declared step)
  GET  /idp/api/conformance/<repo>/bundle        The newest published bundle, with its publication

Golden paths (icx-coe-04, tools/idp/golden_paths.py):
  GET  /idp/golden-paths                          Every path, the standards each meets, its drift check
  GET  /idp/api/golden-paths                      JSON: every path against the standards in force
  GET  /idp/api/golden-paths/<path_id>            One path: versions, drift checks, history, next step
  POST /idp/api/golden-paths/propose              A new path version (anyone; decides nothing)
  POST /idp/api/golden-paths/<path_id>/drift      Run and record a version's drift check (decides nothing)
  POST /idp/api/golden-paths/<path_id>/publish    A person publishes or refuses a version (declared step)
  POST /idp/api/golden-paths/<path_id>/retire     A person retires or keeps a version (declared step)
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


def _tenancy() -> dict:
    """Scoping facts for the current request (idp-mt-01).

    Attached to every read payload, not just the scoped ones. A response that
    is silent about scope invites the reader to mistake a tenant's 12
    components for the platform's 66, and the honest way to prevent that is to
    state the scope every time rather than only when it happens to be narrow.

    Never raises: a portal that cannot describe its own scoping should still
    serve the rows the adapter already scoped. The empty dict is falsy, so a
    template guarding on ``tenancy.scoped`` degrades to saying nothing rather
    than to claiming the surface is unscoped.
    """
    try:
        from tools.idp.tenancy import tenant_context  # noqa: PLC0415

        return tenant_context()
    except Exception:  # noqa: BLE001
        logger.warning("idp: tenant context unavailable", exc_info=True)
        return {}


def _render(focus: str):
    from tools.idp.portal import portal_overview

    data = portal_overview(
        scorecard_key=_requested_scorecard(),
        refresh=_refresh_requested(),
    )
    return render_template(
        "idp/page.html",
        focus=focus,
        tenancy=_tenancy(),
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
        "tenancy": _tenancy(),
        # `facts` is the whole upstream fact row and is already on each entry;
        # dropping it keeps the API payload to what a catalog consumer needs.
        "components": [{k: v for k, v in row.items() if k != "facts"} for row in rows],
    })


@bp.route("/api/scorecard")
def idp_api_scorecard():
    """JSON scorecard report for ``?scorecard=<key>`` (default component-readiness)."""
    from tools.idp.portal import scorecard_report

    report = scorecard_report(_requested_scorecard())
    return jsonify({**report, "tenancy": _tenancy()}), (
        500 if report.get("error") else 200
    )


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
            # Arbitrary IQE over idp.components. The rows are scoped in the
            # adapter; saying so here is what stops a scoped result being read
            # as an estate-wide answer.
            "tenancy": _tenancy(),
            "results": rows,
            "row_count": len(rows),
        }), 200
    except IQESyntaxError as exc:
        return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
    except Exception as exc:  # noqa: BLE001
        logger.warning("IDP IQE query error: %s", exc)
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500


# ---------------------------------------------------------------------------
# Engineering standards register (icx-coe-01)
# ---------------------------------------------------------------------------
#
# Who decides: a standard is PROPOSED by anyone (or an agent, as a draft) and
# decided only through the declared steps decl-wft-coe-standard-review / -adopt /
# -deprecate, each held by a steward and refused to the version's author. The
# routes below call tools/idp/standards.py, which takes the step through
# tools/workflow_hitl/declared.py before the ledger changes.


def _standards_refused(exc):
    return jsonify(exc.to_dict()), exc.status


def _step_body() -> dict:
    return flask_request.get_json(force=True, silent=True) or {}


@bp.route("/standards")
def idp_standards_page():
    """The engineering standards register: browse, see the lifecycle, take a step."""
    from tools.idp import standards

    try:
        view = standards.register_view()
        error = ""
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger is said, not hidden
        logger.warning("idp standards: register unreadable: %s", exc)
        view = {"standards": [], "counts": {}, "unseeded": [], "chain": {}, "register": {}}
        error = str(exc)
    return render_template(
        "idp/standards.html",
        view=view,
        error=error,
        templates=dict(standards.TEMPLATES),
    )


@bp.route("/api/standards")
def idp_api_standards():
    """JSON register: every standard with its lifecycle state, and seeds not yet recorded."""
    from tools.idp import standards

    try:
        return jsonify(standards.register_view())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the register could not be read: {exc}"}), 503


@bp.route("/api/standards/<standard_id>")
def idp_api_standard(standard_id: str):
    """One standard: every version, the full history, its check and its sources."""
    from tools.idp import standards

    try:
        return jsonify(standards.standard_view(standard_id))
    except standards.StandardRefused as exc:
        return _standards_refused(exc)


@bp.route("/api/standards/propose", methods=["POST"])
def idp_api_standard_propose():
    """Propose a standard as a draft. Anyone signed in; proposing decides nothing.

    Body: ``{id, title, version, owner, scope, sources, check_kind, scorecard,
    rule, evidence_name, evidence_procedure, produced_by, note}``. A service key
    is recorded as an agent. A draft without a check is accepted and shown;
    adoption refuses it.
    """
    from tools.idp import standards
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = standards.propose(standards.entry_from_form(data), current_actor(),
                                note=str(data.get("note") or ""))
    except standards.StandardRefused as exc:
        return _standards_refused(exc)
    return jsonify(out), 201


@bp.route("/api/standards/<standard_id>/review", methods=["POST"])
def idp_api_standard_review(standard_id: str):
    """A steward, never the author, endorses a proposed standard or returns it.

    Body: ``{version, decision: endorse|return, reason, digest}``. The person's
    step of decl-wft-coe-standard-review, bound to the entry digest read.
    """
    from tools.idp import standards
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = standards.review(standard_id, str(data.get("version") or ""),
                               decision=str(data.get("decision") or ""),
                               reason=str(data.get("reason") or ""),
                               read_digest=str(data.get("digest") or ""), actor=current_actor())
    except standards.StandardRefused as exc:
        return _standards_refused(exc)
    return jsonify(out)


@bp.route("/api/standards/<standard_id>/adopt", methods=["POST"])
def idp_api_standard_adopt(standard_id: str):
    """A steward, never the author, signs a reviewed standard into force, or refuses it.

    Body: ``{version, decision: sign|refuse, reason, digest}``. The person's step
    of decl-wft-coe-standard-adopt, bound to the entry digest read. A standard
    without a check is a wish: an entry naming no scorecard rule and no manual
    evidence step is refused before any step is taken.
    """
    from tools.idp import standards
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = standards.adopt(standard_id, str(data.get("version") or ""),
                              decision=str(data.get("decision") or ""),
                              reason=str(data.get("reason") or ""),
                              read_digest=str(data.get("digest") or ""), actor=current_actor())
    except standards.StandardRefused as exc:
        return _standards_refused(exc)
    return jsonify(out)


@bp.route("/api/standards/<standard_id>/deprecate", methods=["POST"])
def idp_api_standard_deprecate(standard_id: str):
    """A steward, never the author, retires the standard in force, with a reason.

    Body: ``{version, reason, digest, superseded_by}``. The person's step of
    decl-wft-coe-standard-deprecate.
    """
    from tools.idp import standards
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = standards.deprecate(standard_id, str(data.get("version") or ""),
                                  reason=str(data.get("reason") or ""),
                                  read_digest=str(data.get("digest") or ""),
                                  superseded_by=str(data.get("superseded_by") or ""),
                                  actor=current_actor())
    except standards.StandardRefused as exc:
        return _standards_refused(exc)
    return jsonify(out)


@bp.route("/api/standards-twin")
def idp_api_standards_twin():
    """The register's governance twin, observed now (not recorded), with each finding's thread.

    ``?thread=<id>`` resolves one finding's thread to the ledger row it rests on.
    """
    from tools.twin_core import governance

    thread = (flask_request.args.get("thread") or "").strip()
    try:
        if thread:
            return jsonify(governance.resolve_thread(thread, reader=governance.thread_reader()))
        import tools.twin_core.adapters.coe_standards  # noqa: F401  (registers the twin)

        return jsonify(governance.observe(governance.adapter_for("idp"), record=False))
    except Exception as exc:  # noqa: BLE001 — a twin that cannot observe says so
        return jsonify({"error": f"the standards twin could not observe: {exc}"}), 503


# ---------------------------------------------------------------------------
# Exemptions that expire (icx-coe-02)
# ---------------------------------------------------------------------------
#
# Who decides: anyone (or an agent, as a draft) requests an exemption or asks
# for a renewal; neither decides anything. Granting and renewing are a person's
# declared steps (decl-wft-coe-exemption-grant / -renew), refused to the
# requester and to whoever asked for the renewal, bound to the exemption digest.
# Revoking is a stop: immediate, with a stated reason.


def _exemption_refused(exc):
    return jsonify(exc.to_dict()), exc.status


@bp.route("/exemptions")
def idp_exemptions_page():
    """Scorecard exemptions: each with its scope, standards and expiry state; request, grant, renew."""
    from tools.idp import exemptions

    as_of = (flask_request.args.get("as_of") or "").strip()
    try:
        view = exemptions.exemptions_view(as_of=as_of)
        error = ""
    except Exception as exc:  # noqa: BLE001 — an unreadable log is said, not hidden
        logger.warning("idp exemptions: log unreadable: %s", exc)
        view = {"exemptions": [], "counts": {}, "gate": "unknown", "scopes": [], "repos": []}
        error = str(exc)
    try:
        from tools.idp.scorecard import load_scorecards

        cards = [{"key": c.key, "rules": [r.identifier for r in c.rules]} for c in load_scorecards()]
    except Exception:  # noqa: BLE001
        cards = []
    return render_template("idp/exemptions.html", view=view, error=error, scorecards=cards,
                           templates=dict(exemptions.TEMPLATES))


@bp.route("/api/exemptions")
def idp_api_exemptions():
    """JSON: every exemption with its state (``?as_of=YYYY-MM-DD`` looks ahead), and the gate."""
    from tools.idp import exemptions

    try:
        return jsonify(exemptions.exemptions_view(
            as_of=(flask_request.args.get("as_of") or "").strip()))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the exemption log could not be read: {exc}"}), 503


@bp.route("/api/exemptions/<ref>")
def idp_api_exemption(ref: str):
    """One exemption: its state, history, chain, digest and the step it waits for."""
    from tools.idp import exemptions

    try:
        return jsonify(exemptions.exemption_view(ref))
    except exemptions.ExemptionError as exc:
        return _exemption_refused(exc)


@bp.route("/api/exemptions/request", methods=["POST"])
def idp_api_exemption_request():
    """Request an exemption. Anyone signed in; requesting decides nothing.

    Body: ``{scorecard, rule, component, scope, scope_ref, standards, reason,
    expires}``. Refused with no expiry, an expiry not after today or too far
    out, an undeclared scope, or an unknown rule or standard.
    """
    from tools.idp import exemptions
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    actor = current_actor() or {}
    standards = data.get("standards") or []
    if isinstance(standards, str):
        standards = [s for s in standards.replace(",", " ").split() if s]
    try:
        out = exemptions.request_exemption(
            str(data.get("scorecard") or "component-readiness"), str(data.get("rule") or ""),
            str(data.get("component") or ""), reason=str(data.get("reason") or ""),
            requested_by=str(actor.get("id") or ""), expires=str(data.get("expires") or ""),
            scope=str(data.get("scope") or "rule"), scope_ref=str(data.get("scope_ref") or ""),
            standards=standards,
            actor_kind="agent" if actor.get("role") == "service" else "person")
    except exemptions.ExemptionError as exc:
        return _exemption_refused(exc)
    return jsonify(out), 201


@bp.route("/api/exemptions/<ref>/decide", methods=["POST"])
def idp_api_exemption_decide(ref: str):
    """A person, never the requester, grants an exemption request or denies it.

    Body: ``{decision: grant|deny, reason, digest}``. The person's step of
    decl-wft-coe-exemption-grant, bound to the digest of the request read.
    """
    from tools.idp import exemptions
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = exemptions.decide_exemption(
            exemptions.find_key(ref), decision=str(data.get("decision") or ""),
            reason=str(data.get("reason") or ""), read_digest=str(data.get("digest") or ""),
            actor=current_actor())
    except exemptions.ExemptionError as exc:
        return _exemption_refused(exc)
    return jsonify(out)


@bp.route("/api/exemptions/<ref>/renewal", methods=["POST"])
def idp_api_exemption_renewal(ref: str):
    """Ask for a granted exemption to run to a new expiry. Decides nothing.

    Body: ``{expires, reason}``. The exemption keeps its expiry, and lapses on it,
    until a person renews it.
    """
    from tools.idp import exemptions
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = exemptions.request_renewal(
            exemptions.find_key(ref), expires=str(data.get("expires") or ""),
            reason=str(data.get("reason") or ""), requested_by=current_actor() or {})
    except exemptions.ExemptionError as exc:
        return _exemption_refused(exc)
    return jsonify(out), 201


@bp.route("/api/exemptions/<ref>/renew", methods=["POST"])
def idp_api_exemption_renew(ref: str):
    """A person, never the requester or the asker, renews an exemption or refuses.

    Body: ``{decision: renew|refuse, reason, digest}``. The person's step of
    decl-wft-coe-exemption-renew, bound to the digest of the renewal read.
    """
    from tools.idp import exemptions
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = exemptions.renew_exemption(
            exemptions.find_key(ref), decision=str(data.get("decision") or ""),
            reason=str(data.get("reason") or ""), read_digest=str(data.get("digest") or ""),
            actor=current_actor())
    except exemptions.ExemptionError as exc:
        return _exemption_refused(exc)
    return jsonify(out)


@bp.route("/api/exemptions/<ref>/revoke", methods=["POST"])
def idp_api_exemption_revoke(ref: str):
    """Withdraw an exemption or a request: a stop, immediate, with a stated reason.

    Body: ``{reason}``. It only narrows (the rule applies again), so it takes no
    declared step.
    """
    from tools.idp import exemptions
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = exemptions.revoke_by_key(exemptions.find_key(ref), revoked_by=current_actor() or {},
                                       reason=str(data.get("reason") or ""))
    except exemptions.ExemptionError as exc:
        return _exemption_refused(exc)
    return jsonify(out)


# ---------------------------------------------------------------------------
# ADRs as records (icx-coe-02)
# ---------------------------------------------------------------------------
#
# Who decides: anyone (or an agent, as a draft) proposes an ADR. Accepting or
# returning it, and superseding or deprecating it, are a person's declared steps
# (decl-wft-coe-adr-accept / -supersede), never the author's.


def _adr_refused(exc):
    return jsonify(exc.to_dict()), exc.status


@bp.route("/adrs")
def idp_adrs_page():
    """Architecture decision records: browse, see each one's lifecycle, propose, accept, supersede."""
    from tools.idp import adrs

    try:
        view = adrs.adrs_view()
        error = ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("idp adrs: records unreadable: %s", exc)
        view = {"adrs": [], "counts": {}, "chain": {}, "doc": {}, "total": 0}
        error = str(exc)
    return render_template("idp/adrs.html", view=view, error=error,
                           templates=dict(adrs.TEMPLATES))


@bp.route("/api/adrs")
def idp_api_adrs():
    """JSON: every ADR (records merged with the ledger); ``?q=`` filters."""
    from tools.idp import adrs

    try:
        return jsonify(adrs.adrs_view(query=(flask_request.args.get("q") or "")))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the ADR records could not be read: {exc}"}), 503


@bp.route("/api/adrs/<adr_id>")
def idp_api_adr(adr_id: str):
    """One ADR: its record, versions, history and the step it waits for."""
    from tools.idp import adrs

    try:
        return jsonify(adrs.adr_view(adr_id))
    except adrs.AdrRefused as exc:
        return _adr_refused(exc)


@bp.route("/api/adrs/propose", methods=["POST"])
def idp_api_adr_propose():
    """Propose an ADR as a draft. Anyone signed in; proposing decides nothing.

    Body: ``{id, title, version, section, context, decision, consequences,
    supersedes, requirements, standards, note}``.
    """
    from tools.idp import adrs
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = adrs.propose(adrs.entry_from_form(data), current_actor(),
                           note=str(data.get("note") or ""))
    except adrs.AdrRefused as exc:
        return _adr_refused(exc)
    return jsonify(out), 201


@bp.route("/api/adrs/<adr_id>/accept", methods=["POST"])
def idp_api_adr_accept(adr_id: str):
    """A person, never the author, accepts a proposed ADR or returns it.

    Body: ``{version, decision: accept|return, reason, digest}``. The person's
    step of decl-wft-coe-adr-accept, bound to the record digest read.
    """
    from tools.idp import adrs
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = adrs.accept(adr_id, str(data.get("version") or ""),
                          decision=str(data.get("decision") or ""),
                          reason=str(data.get("reason") or ""),
                          read_digest=str(data.get("digest") or ""), actor=current_actor())
    except adrs.AdrRefused as exc:
        return _adr_refused(exc)
    return jsonify(out)


@bp.route("/api/adrs/<adr_id>/supersede", methods=["POST"])
def idp_api_adr_supersede(adr_id: str):
    """A person who wrote neither ADR supersedes an accepted ADR by its successor, or deprecates it.

    Body: ``{version, decision: supersede|deprecate, superseded_by, reason,
    digest}``. The person's step of decl-wft-coe-adr-supersede.
    """
    from tools.idp import adrs
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = adrs.supersede(adr_id, str(data.get("version") or ""),
                             decision=str(data.get("decision") or ""),
                             reason=str(data.get("reason") or ""),
                             read_digest=str(data.get("digest") or ""),
                             superseded_by=str(data.get("superseded_by") or ""),
                             actor=current_actor())
    except adrs.AdrRefused as exc:
        return _adr_refused(exc)
    return jsonify(out)


# ---------------------------------------------------------------------------
# Scorecards against adopted standards, delivery evidence, conformance bundles (icx-coe-03)
# ---------------------------------------------------------------------------
#
# Who decides: anyone (or an agent, as a draft) prepares a repo's bundle, which records
# it at its digest and decides nothing. Publishing it to ICDEV[OS] is a person's declared
# step (decl-wft-coe-conformance-publish), never the preparer's, bound to that digest.


def _conformance_refused(exc):
    return jsonify(exc.to_dict()), exc.status


@bp.route("/conformance")
def idp_conformance_page():
    """Scorecards per repo against the adopted standards, delivery evidence, and each repo's bundle."""
    from tools.idp import conformance

    try:
        view = conformance.repos_view()
        error = ""
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger is said, not hidden
        logger.warning("idp conformance: ledger unreadable: %s", exc)
        view = {"repos": [], "chain": {}, "config": {}}
        error = str(exc)
    return render_template("idp/conformance.html", view=view, error=error,
                           template=conformance.TEMPLATE)


@bp.route("/api/conformance")
def idp_api_conformance():
    """JSON: every declared repo and its ledger state (the newest prepared and published bundle)."""
    from tools.idp import conformance

    try:
        return jsonify(conformance.repos_view())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the conformance ledger could not be read: {exc}"}), 503


@bp.route("/api/conformance/<repo>")
def idp_api_conformance_repo(repo: str):
    """One repo's scorecard and delivery evidence as a bundle would carry them now (``?as_of=``), and its ledger."""
    from tools.idp import conformance

    try:
        return jsonify(conformance.repo_view(repo, as_of=(flask_request.args.get("as_of") or None)))
    except conformance.ConformanceRefused as exc:
        return _conformance_refused(exc)


@bp.route("/api/conformance/<repo>/prepare", methods=["POST"])
def idp_api_conformance_prepare(repo: str):
    """Prepare a repo's bundle: build it and record it at its digest. Anyone signed in; it decides nothing.

    Body: ``{as_of, note}``. A service key is recorded as an agent.
    """
    from tools.idp import conformance
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = conformance.prepare(repo, current_actor(), as_of=(str(data.get("as_of") or "") or None),
                                  note=str(data.get("note") or ""))
    except conformance.ConformanceRefused as exc:
        return _conformance_refused(exc)
    return jsonify(out), 201


@bp.route("/api/conformance/<repo>/publish", methods=["POST"])
def idp_api_conformance_publish(repo: str):
    """A person, never the preparer, publishes a repo's prepared bundle to ICDEV[OS], or refuses it.

    Body: ``{decision: publish|refuse, reason, digest}``. The person's step of
    decl-wft-coe-conformance-publish, bound to the digest of the prepared bundle read.
    """
    from tools.idp import conformance
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = conformance.publish(repo, decision=str(data.get("decision") or ""),
                                  reason=str(data.get("reason") or ""),
                                  read_digest=str(data.get("digest") or ""), actor=current_actor())
    except conformance.ConformanceRefused as exc:
        return _conformance_refused(exc)
    return jsonify(out)


@bp.route("/api/conformance/<repo>/bundle")
def idp_api_conformance_bundle(repo: str):
    """The repo's newest PUBLISHED bundle (icos-conformance/v1), with its publication, as ICDEV[OS] imports it."""
    from tools.idp import conformance

    try:
        b = conformance.published_bundle(repo)
    except conformance.ConformanceRefused as exc:
        return _conformance_refused(exc)
    if b is None:
        return jsonify({"error": f"no bundle of {repo} has been published", "code": "unpublished"}), 404
    return jsonify(b)


# ---------------------------------------------------------------------------
# Golden paths: a new service conformant by construction (icx-coe-04)
# ---------------------------------------------------------------------------
#
# Who decides: anyone (or an agent, as a draft) proposes a path version and runs its drift
# check; neither decides anything. Publishing a version, or retiring one, is a person's declared
# step (decl-wft-coe-golden-path-publish / -retire), never the version's author's, and publishing
# only when its gate holds (a passing drift check on the template as it is now; not behind).


def _golden_refused(exc):
    return jsonify(exc.to_dict()), exc.status


@bp.route("/golden-paths")
def idp_golden_paths_page():
    """Every golden path, the standards each meets, and its last drift check."""
    from tools.idp import golden_paths

    try:
        view = golden_paths.paths_view()
        error = ""
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger is said, not hidden
        logger.warning("idp golden paths: ledger unreadable: %s", exc)
        view = {"paths": [], "in_force": [], "chain": {}, "config": {}}
        error = str(exc)
    return render_template("idp/golden_paths.html", view=view, error=error,
                           templates=golden_paths.TEMPLATES)


@bp.route("/api/golden-paths")
def idp_api_golden_paths():
    """JSON: every golden path against the standards in force, with its last drift check."""
    from tools.idp import golden_paths

    try:
        return jsonify(golden_paths.paths_view())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the golden path ledger could not be read: {exc}"}), 503


@bp.route("/api/golden-paths/<path_id>")
def idp_api_golden_path(path_id: str):
    """One golden path: every version, its drift checks and gate, the history, the step it waits for."""
    from tools.idp import golden_paths

    try:
        return jsonify(golden_paths.path_view(path_id))
    except golden_paths.GoldenPathRefused as exc:
        return _golden_refused(exc)


@bp.route("/api/golden-paths/propose", methods=["POST"])
def idp_api_golden_path_propose():
    """A new path version: anyone signed in, or an agent. It decides nothing.

    Body: ``{id, version, satisfies: ["ID@version", ...], note}``; the other fields come from the
    path's newest version.
    """
    from tools.idp import golden_paths
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        base = None
        try:
            view = golden_paths.path_view(str(data.get("id") or ""))
            base = (view["versions"][-1]["entry"] if view["versions"] else view["declared"])
        except golden_paths.GoldenPathRefused:
            base = None
        out = golden_paths.propose(golden_paths.entry_from_form(data, base), current_actor(),
                                   note=str(data.get("note") or ""))
    except golden_paths.GoldenPathRefused as exc:
        return _golden_refused(exc)
    return jsonify(out), 201


@bp.route("/api/golden-paths/<path_id>/drift", methods=["POST"])
def idp_api_golden_path_drift(path_id: str):
    """Run a version's drift check and record its result. Anyone signed in; it decides nothing.

    Body: ``{version}`` (the newest version when blank).
    """
    from tools.idp import golden_paths
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = golden_paths.record_drift(path_id, str(data.get("version") or "") or None, current_actor())
    except golden_paths.GoldenPathRefused as exc:
        return _golden_refused(exc)
    return jsonify(out), 201


@bp.route("/api/golden-paths/<path_id>/publish", methods=["POST"])
def idp_api_golden_path_publish(path_id: str):
    """A person, never the version's author, publishes a proposed golden path version, or refuses it.

    Body: ``{version, decision: publish|refuse, reason, digest}``. The person's step of
    decl-wft-coe-golden-path-publish, bound to the digest read, and refused when the gate does not hold.
    """
    from tools.idp import golden_paths
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = golden_paths.publish(path_id, str(data.get("version") or ""), decision=str(data.get("decision") or ""),
                                   reason=str(data.get("reason") or ""), read_digest=str(data.get("digest") or ""),
                                   actor=current_actor())
    except golden_paths.GoldenPathRefused as exc:
        return _golden_refused(exc)
    return jsonify(out)


@bp.route("/api/golden-paths/<path_id>/retire", methods=["POST"])
def idp_api_golden_path_retire(path_id: str):
    """A person, never the version's author, retires a published golden path version, or keeps it.

    Body: ``{version, decision: retire|keep, reason, digest}`` (decl-wft-coe-golden-path-retire).
    """
    from tools.idp import golden_paths
    from tools.workflow_hitl.declared import current_actor

    data = _step_body()
    try:
        out = golden_paths.retire(path_id, str(data.get("version") or ""), decision=str(data.get("decision") or ""),
                                  reason=str(data.get("reason") or ""), read_digest=str(data.get("digest") or ""),
                                  actor=current_actor())
    except golden_paths.GoldenPathRefused as exc:
        return _golden_refused(exc)
    return jsonify(out)
