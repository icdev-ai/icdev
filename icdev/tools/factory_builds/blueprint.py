# CUI // SP-CTI
"""Signed factory builds console (icx-ent-03): /factory-builds/.

Who decides: a person prepares a target's build (the factory's key signs it; an agent or a service key never does),
which records it at its artefact digest and decides nothing. Publishing it to ICDEV[OS] is another person's declared
step (decl-wft-fb-build-publish): a person of the target's org, cleared for its marking, never the preparer, bound to
the digest read. A target the reader may not see is "no such target" everywhere, and a list counts what it holds back.

Routes:
  GET  /factory-builds/                           The page
  GET  /factory-builds/api/targets                JSON: every target the reader may see and its ledger state
  GET  /factory-builds/api/targets/<tid>          JSON: one target, its newest build (record, SBOM, VEX), its history
  POST /factory-builds/api/targets/<tid>/prepare  Build and sign the target (a person; decides nothing)
  POST /factory-builds/api/targets/<tid>/publish  A person publishes or refuses it (declared step)
  GET  /factory-builds/api/targets/<tid>/export   The newest published build, as ICDEV[OS] imports it
  GET  /factory-builds/api/twin                   The twin as this reader may see it (?thread= resolves one)
  POST /factory-builds/api/iqe-query              IQE query over the published builds the reader may see
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, url_for
from flask import request as flask_request

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.factory_builds.blueprint")

fb_bp = Blueprint("factory_builds", __name__, url_prefix="/factory-builds")


def _body() -> dict:
    return flask_request.get_json(force=True, silent=True) or {}


def _refused(exc):
    return jsonify(exc.to_dict()), exc.status


@fb_bp.route("/")
def fb_page():
    """The targets the reader may see, each one's newest signed build, publishing, and the twin."""
    from tools.factory_builds import service
    from tools.factory_builds.constants import IQE_CANVAS, IQE_EXAMPLES, SCHEMA, SCHEMA_CONTRACT, TEMPLATE

    reader = service.current_reader()
    try:
        view, error = service.targets_view(reader), ""
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger or configuration is said, not hidden
        logger.warning("factory builds: unreadable: %s", type(exc).__name__)
        view, error = {"targets": [], "held_back": 0, "signing": {}, "transparency_log": {}}, \
            f"the build ledger or configuration could not be read ({type(exc).__name__})"
    tid = flask_request.args.get("target") or (view["targets"][0]["id"] if view["targets"] else "")
    detail = None
    if tid:
        try:
            detail = service.target_view(tid, reader)
        except service.Refused:
            detail = None
    return render_template("factory_builds/index.html", view=view, detail=detail, error=error, template=TEMPLATE,
                           schema=SCHEMA, contract=SCHEMA_CONTRACT, me=(reader or {}).get("id", ""),
                           iqe_canvas=IQE_CANVAS, iqe_api_route=url_for("factory_builds.fb_api_iqe_query"),
                           iqe_examples=list(IQE_EXAMPLES), iqe_title="Query published builds (IQE)")


@fb_bp.route("/api/targets")
def fb_api_targets():
    """JSON: every target the reader may see, with its ledger state; the others are only counted."""
    from tools.factory_builds import service

    try:
        return jsonify(service.targets_view(service.current_reader()))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the build ledger could not be read ({type(exc).__name__})"}), 503


@fb_bp.route("/api/targets/<tid>")
def fb_api_target(tid: str):
    """JSON: one target, its newest build's record, SBOM and VEX summaries, and its ledger."""
    from tools.factory_builds import service

    try:
        return jsonify(service.target_view(tid, service.current_reader()))
    except service.Refused as exc:
        return _refused(exc)


@fb_bp.route("/api/targets/<tid>/prepare", methods=["POST"])
def fb_api_prepare(tid: str):
    """A person builds the target and the factory's key signs it; recorded at its artefact digest. Decides nothing.

    Body: ``{version, note}``. A service key or an agent is refused: it never makes the factory's key sign.
    """
    from tools.factory_builds import service
    from tools.workflow_hitl.declared import current_actor

    data = _body()
    try:
        out = service.prepare(tid, current_actor(), service.current_reader(), version=str(data.get("version") or ""),
                              note=str(data.get("note") or ""))
    except service.Refused as exc:
        return _refused(exc)
    return jsonify(out), 201


@fb_bp.route("/api/targets/<tid>/publish", methods=["POST"])
def fb_api_publish(tid: str):
    """A person, never the preparer, publishes a target's signed build to ICDEV[OS], or refuses it.

    Body: ``{decision: publish|refuse, reason, digest}``. The person's step of decl-wft-fb-build-publish, bound to the
    artefact digest read.
    """
    from tools.factory_builds import service
    from tools.workflow_hitl.declared import current_actor

    data = _body()
    try:
        out = service.publish(tid, decision=str(data.get("decision") or ""), reason=str(data.get("reason") or ""),
                              read_digest=str(data.get("digest") or ""), actor=current_actor(),
                              reader=service.current_reader())
    except service.Refused as exc:
        return _refused(exc)
    return jsonify(out)


@fb_bp.route("/api/targets/<tid>/export")
def fb_api_export(tid: str):
    """The target's newest PUBLISHED build: {"bundle": icos-release-transfer/v1, "publication": ...}."""
    from tools.factory_builds import service

    try:
        return jsonify(service.export(tid, service.current_reader()))
    except service.Refused as exc:
        return _refused(exc)


@fb_bp.route("/api/twin")
def fb_api_twin():
    """The governance twin over the factory builds as the signed-in reader may see it, judged now and never recorded.
    ``?thread=factory_builds:<event>`` resolves one ledger row for a reader who may see its target."""
    import tools.twin_core.adapters.factory_builds  # noqa: F401 — registers the adapter
    from tools.factory_builds import service
    from tools.twin_core import governance

    reader = service.current_reader()
    thread = flask_request.args.get("thread") or ""
    if thread:
        kind, _, ref = thread.partition(":")
        rec = service.thread_record(ref, reader) if kind == "factory_builds" else None
        if rec is None:
            return jsonify({"thread_id": thread, "resolved": False, "reason": "no such thread"}), 404
        return jsonify({"thread_id": thread, "kind": kind, "resolved": True, "record": rec, "source": "fb_events"})
    if reader is None:
        return jsonify({"error": "sign in to see the twin", "code": "no_reader"}), 401
    try:
        return jsonify(governance.adapter_for("factory_builds").judge_for(reader))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the twin could not observe ({type(exc).__name__})"}), 503


@fb_bp.route("/api/iqe-query", methods=["POST"])
def fb_api_iqe_query():
    """IQE natural-language query over factory_builds.builds: the published builds of the targets the signed-in reader
    may see. Reads only."""
    import tools.iqe.adapters.factory_builds  # noqa: F401  (registers the collection)
    from tools.factory_builds.constants import IQE_COLLECTIONS
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
        node = parse(iqe_str)
        # review L9: this route answers over its own collection only, whatever the translation produced
        if str(node.collection) not in IQE_COLLECTIONS:
            return jsonify({"error": "this page answers over factory_builds.builds only", "iqe": iqe_str}), 400
        rows = execute_query(node, None)
    except IQESyntaxError as exc:
        return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
    except Exception as exc:  # noqa: BLE001
        logger.warning("factory builds IQE query error: %s", type(exc).__name__)
        return jsonify({"error": f"the query could not run ({type(exc).__name__})", "iqe": iqe_str}), 500
    return jsonify({"ok": True, "iqe": iqe_str, "explanation": translation.get("explanation", ""),
                    "results": rows, "row_count": len(rows)})
