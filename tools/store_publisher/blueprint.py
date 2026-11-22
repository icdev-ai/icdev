# CUI // SP-CTI
"""Store publisher console (icx-ent-04): /store-publisher/.

Who decides: a person prepares a package (the publisher's key signs its Agent Card; an agent or a service key never
does), which records it at its content digest and decides nothing. Publishing it for ICDEV[OS]'s agent store is
another person's declared step (icos-store-publish): a person of the package's org, cleared for its marking, never
the preparer, bound to the digest read. Importing it into ICDEV[OS] is a third person's act there. A package the reader
may not see is "no such package" everywhere, and a list counts what it holds back.

Routes:
  GET  /store-publisher/                            The page
  GET  /store-publisher/api/packages                JSON: every package the reader may see and its ledger state
  GET  /store-publisher/api/packages/<pid>          JSON: one package, its newest prepared version, its history
  POST /store-publisher/api/packages/<pid>/prepare  Build and sign the package (a person; decides nothing)
  POST /store-publisher/api/packages/<pid>/publish  A person publishes or refuses it (declared step)
  GET  /store-publisher/api/packages/<pid>/export   The newest published version, as ICDEV[OS] imports it
  GET  /store-publisher/api/twin                    The twin as this reader may see it (?thread= resolves one)
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template
from flask import request as flask_request

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.store_publisher.blueprint")

sp_bp = Blueprint("store_publisher", __name__, url_prefix="/store-publisher")


def _body() -> dict:
    return flask_request.get_json(force=True, silent=True) or {}


def _refused(exc):
    return jsonify(exc.to_dict()), exc.status


@sp_bp.route("/")
def sp_page():
    """The packages the reader may see, each one's newest signed version, its gates, publishing, and the twin."""
    from tools.store_publisher import service
    from tools.store_publisher.constants import SCANNER, SCHEMA, SCHEMA_CONTRACT, TEMPLATE

    reader = service.current_reader()
    try:
        view, error = service.packages_view(reader), ""
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger or configuration is said, not hidden
        logger.warning("store publisher: unreadable: %s", type(exc).__name__)
        view, error = {"packages": [], "held_back": 0, "publisher": {}, "signing": {}}, \
            f"the store publisher ledger or configuration could not be read ({type(exc).__name__})"
    pid = flask_request.args.get("package") or (view["packages"][0]["id"] if view["packages"] else "")
    detail = None
    if pid:
        try:
            detail = service.package_view(pid, reader)
        except service.Refused:
            detail = None
    return render_template("store_publisher/index.html", view=view, detail=detail, error=error, template=TEMPLATE,
                           schema=SCHEMA, contract=SCHEMA_CONTRACT, gate_names=SCANNER,
                           me=(reader or {}).get("id", ""))


@sp_bp.route("/api/packages")
def sp_api_packages():
    """JSON: every package the reader may see, with its ledger state; the others are only counted."""
    from tools.store_publisher import service

    try:
        return jsonify(service.packages_view(service.current_reader()))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the store publisher ledger could not be read ({type(exc).__name__})"}), 503


@sp_bp.route("/api/packages/<pid>")
def sp_api_package(pid: str):
    """JSON: one package, its newest prepared version's card, gates and red-team summary, and its ledger."""
    from tools.store_publisher import service

    try:
        return jsonify(service.package_view(pid, service.current_reader()))
    except service.Refused as exc:
        return _refused(exc)


@sp_bp.route("/api/packages/<pid>/prepare", methods=["POST"])
def sp_api_prepare(pid: str):
    """A person builds the package and the publisher's key signs its card; recorded at its digest. Decides nothing.

    Body: ``{version, note}``. A service key or an agent is refused: it never makes the publisher's key sign.
    """
    from tools.store_publisher import service
    from tools.workflow_hitl.declared import current_actor

    data = _body()
    try:
        out = service.prepare(pid, current_actor(), service.current_reader(), version=str(data.get("version") or ""),
                              note=str(data.get("note") or ""))
    except service.Refused as exc:
        return _refused(exc)
    return jsonify(out), 201


@sp_bp.route("/api/packages/<pid>/publish", methods=["POST"])
def sp_api_publish(pid: str):
    """A person, never the preparer, publishes a prepared store package for ICDEV[OS], or refuses it.

    Body: ``{decision: publish|refuse, reason, digest}``. The person's step of icos-store-publish, bound to the
    content digest read.
    """
    from tools.store_publisher import service
    from tools.workflow_hitl.declared import current_actor

    data = _body()
    try:
        out = service.publish(pid, decision=str(data.get("decision") or ""), reason=str(data.get("reason") or ""),
                              read_digest=str(data.get("digest") or ""), actor=current_actor(),
                              reader=service.current_reader())
    except service.Refused as exc:
        return _refused(exc)
    return jsonify(out)


@sp_bp.route("/api/packages/<pid>/export")
def sp_api_export(pid: str):
    """The package's newest PUBLISHED version: {"bundle": icos-store-package/v1, "publication": ...}."""
    from tools.store_publisher import service

    try:
        return jsonify(service.export(pid, service.current_reader()))
    except service.Refused as exc:
        return _refused(exc)


@sp_bp.route("/api/twin")
def sp_api_twin():
    """The governance twin over the store packages as the signed-in reader may see it, judged now and never recorded.
    ``?thread=store_publisher:<event>`` resolves one ledger row for a reader who may see its package."""
    import tools.twin_core.adapters.store_publisher  # noqa: F401 — registers the adapter
    from tools.store_publisher import service
    from tools.twin_core import governance

    reader = service.current_reader()
    thread = flask_request.args.get("thread") or ""
    if thread:
        kind, _, ref = thread.partition(":")
        rec = service.thread_record(ref, reader) if kind == "store_publisher" else None
        if rec is None:
            return jsonify({"thread_id": thread, "resolved": False, "reason": "no such thread"}), 404
        return jsonify({"thread_id": thread, "kind": kind, "resolved": True, "record": rec, "source": "sp_events"})
    if reader is None:
        return jsonify({"error": "sign in to see the twin", "code": "no_reader"}), 401
    try:
        return jsonify(governance.adapter_for("store_publisher").judge_for(reader))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the twin could not observe ({type(exc).__name__})"}), 503
