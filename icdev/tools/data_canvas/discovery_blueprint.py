# CUI // SP-CTI
"""Data discovery export to ICDEV[OS] (osx-cap-02, tools/data_canvas/discovery_bundle.py).

  GET  /data-discovery/                                  Per source: profiles, the bundle, the ledger, the twin
  GET  /data-discovery/api/sources                       JSON: every declared source and its ledger state
  GET  /data-discovery/api/sources/<source>              JSON: the source's bundle as it would be prepared now
                                                         (?profile=, ?as_of=), its profiles and its ledger
  POST /data-discovery/api/sources/<source>/prepare      Build and record a bundle at its digest (decides nothing)
  POST /data-discovery/api/sources/<source>/publish      A person publishes or refuses it (declared step)
  GET  /data-discovery/api/sources/<source>/bundle       The newest published bundle, with its publication
  GET  /data-discovery/api/twin                          The data discovery twin, observed now (?thread= resolves one)

Who decides: anyone (or an agent, as a draft) prepares a source's bundle, which records it at its digest and decides
nothing. Publishing it to ICDEV[OS] is a person's declared step (decl-wft-data-discovery-publish), never the
preparer's, bound to that digest.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request as flask_request

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

discovery_bp = Blueprint("data_discovery", __name__, url_prefix="/data-discovery")


def _refused(exc):
    return jsonify(exc.to_dict()), exc.status


def _body() -> dict:
    return flask_request.get_json(force=True, silent=True) or {}


def _arg(name: str):
    return (flask_request.args.get(name) or "").strip() or None


@discovery_bp.route("/")
def dd_export_page():
    """Each declared source: its stored profiles, the bundle that would be prepared, the ledger, the twin."""
    from tools.data_canvas import discovery_bundle as db

    try:
        view = db.sources_view()
        error = ""
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger is said, not hidden
        logger.warning("data discovery: ledger unreadable: %s", exc)
        view = {"sources": [], "chain": {}, "config": {}}
        error = str(exc)
    return render_template("data_canvas/discovery_export.html", view=view, error=error, template=db.TEMPLATE,
                           schema=db.SCHEMA, contract=db.SCHEMA_CONTRACT)


@discovery_bp.route("/api/sources")
def dd_api_sources():
    """JSON: every declared source and its ledger state (the newest prepared and published bundle)."""
    from tools.data_canvas import discovery_bundle as db

    try:
        return jsonify(db.sources_view())
    except db.DiscoveryRefused as exc:
        return _refused(exc)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the discovery export ledger could not be read: {exc}"}), 503


@discovery_bp.route("/api/sources/<source>")
def dd_api_source(source: str):
    """One source's bundle as it would be prepared now (``?profile=``, ``?as_of=``), its profiles and its ledger."""
    from tools.data_canvas import discovery_bundle as db

    try:
        return jsonify(db.source_view(source, profile_id=_arg("profile"), as_of=_arg("as_of")))
    except db.DiscoveryRefused as exc:
        return _refused(exc)


@discovery_bp.route("/api/sources/<source>/prepare", methods=["POST"])
def dd_api_prepare(source: str):
    """Prepare a source's bundle: build it from a stored profile and record it at its digest. It decides nothing.

    Body: ``{profile, as_of, note}``. A service key is recorded as an agent.
    """
    from tools.data_canvas import discovery_bundle as db
    from tools.workflow_hitl.declared import current_actor

    data = _body()
    try:
        out = db.prepare(source, current_actor(), profile_id=(str(data.get("profile") or "") or None),
                         as_of=(str(data.get("as_of") or "") or None), note=str(data.get("note") or ""))
    except db.DiscoveryRefused as exc:
        return _refused(exc)
    return jsonify(out), 201


@discovery_bp.route("/api/sources/<source>/publish", methods=["POST"])
def dd_api_publish(source: str):
    """A person, never the preparer, publishes a source's prepared discovery bundle to ICDEV[OS], or refuses it.

    Body: ``{decision: publish|refuse, reason, digest}``. The person's step of decl-wft-data-discovery-publish,
    bound to the digest of the prepared bundle read.
    """
    from tools.data_canvas import discovery_bundle as db
    from tools.workflow_hitl.declared import current_actor

    data = _body()
    try:
        out = db.publish(source, decision=str(data.get("decision") or ""), reason=str(data.get("reason") or ""),
                         read_digest=str(data.get("digest") or ""), actor=current_actor())
    except db.DiscoveryRefused as exc:
        return _refused(exc)
    return jsonify(out)


@discovery_bp.route("/api/sources/<source>/bundle")
def dd_api_bundle(source: str):
    """The source's newest PUBLISHED bundle (icos-discovery/v1), with its publication, as ICDEV[OS] imports it."""
    from tools.data_canvas import discovery_bundle as db

    try:
        b = db.published_bundle(source)
    except db.DiscoveryRefused as exc:
        return _refused(exc)
    if b is None:
        return jsonify({"error": f"no bundle of {source} has been published", "code": "unpublished"}), 404
    resp = jsonify(b)
    if _arg("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="{b["source"]["key"]}.json"'
    return resp


@discovery_bp.route("/api/twin")
def dd_api_twin():
    """The data discovery twin, observed now (not recorded), with each finding's thread.

    ``?thread=<id>`` resolves one finding's thread to the ledger row it rests on.
    """
    from tools.twin_core import governance

    thread = _arg("thread")
    try:
        if thread:
            return jsonify(governance.resolve_thread(thread))
        import tools.twin_core.adapters.data_discovery  # noqa: F401  (registers the twin)

        return jsonify(governance.observe(governance.adapter_for("data_discovery"), record=False))
    except Exception as exc:  # noqa: BLE001 — a twin that cannot observe says so
        return jsonify({"error": f"the data discovery twin could not observe: {exc}"}), 503
