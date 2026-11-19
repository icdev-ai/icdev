# CUI // SP-CTI
"""Evidence collectors console (icx-ent-02): /evidence-collectors/.

Who decides: anyone signed in (or an agent, as a draft) collects from a customer system's sources and prepares its
objective-evidence report, which records it at its digest and decides nothing. Publishing it to ICDEV[OS] is a
person's declared step (decl-wft-ec-evidence-publish): a person of the system's org, cleared for its marking, never
the preparer, bound to the digest read and the system's configuration digest. A system the reader may not see is
"no such system" everywhere, and a list counts what it holds back.

Routes:
  GET  /evidence-collectors/                          The page
  GET  /evidence-collectors/api/systems               JSON: every system the reader may see and its ledger state
  GET  /evidence-collectors/api/systems/<sid>         JSON: one system, its newest prepared report and observations
  POST /evidence-collectors/api/systems/<sid>/prepare Collect (read-only) and record the report (decides nothing)
  POST /evidence-collectors/api/systems/<sid>/publish A person publishes or refuses it (declared step)
  GET  /evidence-collectors/api/systems/<sid>/bundle  The newest published report, its publication embedded
  GET  /evidence-collectors/api/twin                  The governance twin, observed now (?thread= resolves one)
  POST /evidence-collectors/api/iqe-query             IQE query over the published objectives the reader may see
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, url_for
from flask import request as flask_request

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.evidence_collectors.blueprint")

ec_bp = Blueprint("evidence_collectors", __name__, url_prefix="/evidence-collectors")


def _body() -> dict:
    return flask_request.get_json(force=True, silent=True) or {}


def _refused(exc):
    return jsonify(exc.to_dict()), exc.status


@ec_bp.route("/")
def ec_page():
    """The systems the reader may see, each one's collectors and newest report, publishing, and the twin."""
    from tools.evidence_collectors import service
    from tools.evidence_collectors.constants import IQE_CANVAS, IQE_EXAMPLES, SCHEMA, SCHEMA_CONTRACT, TEMPLATE

    reader = service.current_reader()
    try:
        view, error = service.systems_view(reader), ""
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger or configuration is said, not hidden
        logger.warning("evidence collectors: unreadable: %s", type(exc).__name__)
        view, error = {"systems": [], "held_back": 0, "mapping": [], "chain": {}, "config": {}}, str(exc)[:300]
    sid = flask_request.args.get("system") or (view["systems"][0]["id"] if view["systems"] else "")
    detail = None
    if sid:
        try:
            detail = service.system_view(sid, reader)
        except service.Refused:
            detail = None
    return render_template("evidence_collectors/index.html", view=view, detail=detail, error=error,
                           template=TEMPLATE, schema=SCHEMA, contract=SCHEMA_CONTRACT,
                           me=(reader or {}).get("id", ""), iqe_canvas=IQE_CANVAS,
                           iqe_api_route=url_for("evidence_collectors.ec_api_iqe_query"),
                           iqe_examples=list(IQE_EXAMPLES), iqe_title="Query published objectives (IQE)")


@ec_bp.route("/api/systems")
def ec_api_systems():
    """JSON: every system the reader may see, with its ledger state; the others are only counted."""
    from tools.evidence_collectors import service

    try:
        return jsonify(service.systems_view(service.current_reader()))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the evidence ledger could not be read ({type(exc).__name__})"}), 503


@ec_bp.route("/api/systems/<sid>")
def ec_api_system(sid: str):
    """JSON: one system, its collectors, its newest prepared report with the observations, and its ledger."""
    from tools.evidence_collectors import service

    try:
        return jsonify(service.system_view(sid, service.current_reader()))
    except service.Refused as exc:
        return _refused(exc)


@ec_bp.route("/api/systems/<sid>/prepare", methods=["POST"])
def ec_api_prepare(sid: str):
    """Collect from the system's sources, read-only, and record its report at its digest. Decides nothing.

    Body: ``{note}``. A service key is recorded as an agent.
    """
    from tools.evidence_collectors import service
    from tools.workflow_hitl.declared import current_actor

    try:
        out = service.prepare(sid, current_actor(), service.current_reader(), note=str(_body().get("note") or ""))
    except service.Refused as exc:
        return _refused(exc)
    return jsonify(out), 201


@ec_bp.route("/api/systems/<sid>/publish", methods=["POST"])
def ec_api_publish(sid: str):
    """A person, never the preparer, publishes a system's prepared objective-evidence report to ICDEV[OS], or refuses it.

    Body: ``{decision: publish|refuse, reason, digest}``. The person's step of decl-wft-ec-evidence-publish, bound
    to the digest of the prepared report read and to the system's configuration digest.
    """
    from tools.evidence_collectors import service
    from tools.workflow_hitl.declared import current_actor

    data = _body()
    try:
        out = service.publish(sid, decision=str(data.get("decision") or ""), reason=str(data.get("reason") or ""),
                              read_digest=str(data.get("digest") or ""), actor=current_actor(),
                              reader=service.current_reader())
    except service.Refused as exc:
        return _refused(exc)
    return jsonify(out)


@ec_bp.route("/api/systems/<sid>/bundle")
def ec_api_bundle(sid: str):
    """The system's newest PUBLISHED report (icos-objective-evidence/v1), its publication embedded."""
    from tools.evidence_collectors import service

    try:
        b = service.published_bundle(sid, service.current_reader())
    except service.Refused as exc:
        return _refused(exc)
    if b is None:
        return jsonify({"error": "no report of this system has been published", "code": "unpublished"}), 404
    return jsonify(b)


@ec_bp.route("/api/twin")
def ec_api_twin():
    """The governance twin over the collectors, observed now; ``?thread=evidence_collectors:<event>`` resolves one."""
    import tools.twin_core.adapters.evidence_collectors  # noqa: F401 — registers the adapter
    from tools.twin_core import governance

    thread = flask_request.args.get("thread") or ""
    if thread:
        if not thread.startswith("evidence_collectors:"):
            return jsonify({"error": "not an evidence_collectors thread", "code": "thread"}), 400
        from tools.evidence_collectors import config as cmod
        from tools.evidence_collectors import service

        res = governance.resolve_thread(thread)
        s = cmod.system(cmod.load(), str((res.get("record") or {}).get("system_id") or ""))
        if not res.get("resolved") or s is None or not service.visible(s, service.current_reader()):
            # One answer for a thread that does not exist and one about a system the reader may not see.
            return jsonify({"thread_id": thread, "resolved": False, "reason": "no such thread"}), 404
        return jsonify(res)
    try:
        env = governance.observe(governance.adapter_for("evidence_collectors"), record=False)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the twin could not observe ({type(exc).__name__})"}), 503
    return jsonify(env)


@ec_bp.route("/api/iqe-query", methods=["POST"])
def ec_api_iqe_query():
    """IQE natural-language query over evidence_collectors.objectives: the published objectives of the systems the
    signed-in reader may see. Reads; observations, notes and credentials are no column."""
    import tools.iqe.adapters.evidence_collectors  # noqa: F401  (registers the collection)
    from tools.evidence_collectors.constants import IQE_COLLECTIONS
    from tools.iqe.executor import execute_query
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import IQESyntaxError, parse

    data = _body()
    question = str(data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400
    translation = nl_to_iqe(question, list(IQE_COLLECTIONS))
    iqe_str = translation.get("iqe", "")
    if not data.get("execute", True):
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": translation.get("explanation", "")})
    try:
        rows = execute_query(parse(iqe_str), None)
    except IQESyntaxError as exc:
        return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
    except Exception as exc:  # noqa: BLE001
        logger.warning("evidence collectors IQE query error: %s", type(exc).__name__)
        return jsonify({"error": f"the query could not run ({type(exc).__name__})", "iqe": iqe_str}), 500
    return jsonify({"ok": True, "iqe": iqe_str, "explanation": translation.get("explanation", ""),
                    "results": rows, "row_count": len(rows)})
