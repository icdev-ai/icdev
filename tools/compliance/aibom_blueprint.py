# CUI // SP-CTI
"""ICDEV[IT]'s AI-BOM export to ICDEV[OS] (osx-cop-02, tools/compliance/aibom_bundle.py).

  GET  /ai-bom/                          The AI components, the two standards, the AI-BOM, the ledger, the twin
  GET  /ai-bom/api/view                  JSON: the components (and what was left out), the AI-BOM as it would be
                                         prepared now (?as_of=), the standards and the ledger
  POST /ai-bom/api/prepare               Build and record the AI-BOM at its digest (decides nothing)
  POST /ai-bom/api/publish               A person publishes or refuses it (declared step)
  GET  /ai-bom/api/bundle                The newest published icos-aibom/v1 bundle, with its publication
  GET  /ai-bom/api/document/<kind>       The newest published bundle's SPDX 3.0.1 document or CycloneDX 1.7 BOM
  GET  /ai-bom/api/twin                  The AI-BOM twin, observed now (?thread= resolves one)

Who decides: anyone (or an agent, as a draft) prepares the AI-BOM, which records it at its digest and decides nothing.
Publishing it to ICDEV[OS] is a person's declared step (decl-wft-aibom-publish), never the preparer's, bound to that
digest: a person, never the preparer, publishes ICDEV[IT]'s prepared AI-BOM to ICDEV[OS], or refuses it.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request as flask_request

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

aibom_bp = Blueprint("aibom", __name__, url_prefix="/ai-bom")


def _refused(exc):
    return jsonify(exc.to_dict()), exc.status


def _body() -> dict:
    return flask_request.get_json(force=True, silent=True) or {}


def _arg(name: str):
    return (flask_request.args.get(name) or "").strip() or None


@aibom_bp.route("/")
def aibom_page():
    """The AI components, the two standards, the AI-BOM as it would be prepared, the ledger and the twin."""
    from tools.compliance import aibom_bundle as ab
    from tools.workflow_hitl.declared import current_actor

    me = (current_actor() or {}).get("id") or ""
    return render_template("compliance/aibom_export.html", template=ab.TEMPLATE, schema=ab.SCHEMA,
                           contract=ab.SCHEMA_CONTRACT, config_path=ab.CONFIG_RELPATH, me=me)


@aibom_bp.route("/api/view")
def aibom_api_view():
    """JSON: the components, what was left out and why, the AI-BOM as it would be prepared now, the ledger."""
    from tools.compliance import aibom_bundle as ab
    from tools.workflow_hitl.declared import current_actor

    try:
        out = ab.view(as_of=_arg("as_of"))
    except ab.AIBOMRefused as exc:
        return _refused(exc)
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger is said, not hidden
        logger.warning("aibom: view failed: %s", exc)
        return jsonify({"error": f"the AI-BOM export ledger could not be read: {exc}"}), 503
    out["me"] = (current_actor() or {}).get("id")
    return jsonify(out)


@aibom_bp.route("/api/prepare", methods=["POST"])
def aibom_api_prepare():
    """Prepare the AI-BOM: build it from the declared components and record it at its digest. It decides nothing.

    Body: ``{as_of, note}``. A service key is recorded as an agent.
    """
    from tools.compliance import aibom_bundle as ab
    from tools.workflow_hitl.declared import current_actor

    data = _body()
    try:
        out = ab.prepare(current_actor(), as_of=(str(data.get("as_of") or "") or None),
                         note=str(data.get("note") or ""))
    except ab.AIBOMRefused as exc:
        return _refused(exc)
    return jsonify(out), 201


@aibom_bp.route("/api/publish", methods=["POST"])
def aibom_api_publish():
    """A person, never the preparer, publishes ICDEV[IT]'s prepared AI-BOM to ICDEV[OS], or refuses it.

    Body: ``{decision: publish|refuse, reason, digest}``. The person's step of decl-wft-aibom-publish, bound to the
    digest of the prepared AI-BOM read.
    """
    from tools.compliance import aibom_bundle as ab
    from tools.workflow_hitl.declared import current_actor

    data = _body()
    try:
        out = ab.publish(decision=str(data.get("decision") or ""), reason=str(data.get("reason") or ""),
                         read_digest=str(data.get("digest") or ""), actor=current_actor())
    except ab.AIBOMRefused as exc:
        return _refused(exc)
    return jsonify(out)


@aibom_bp.route("/api/bundle")
def aibom_api_bundle():
    """The newest PUBLISHED AI-BOM (icos-aibom/v1), with its publication, as ICDEV[OS] imports it."""
    from tools.compliance import aibom_bundle as ab

    b = ab.published_bundle()
    if b is None:
        return jsonify({"error": "no AI-BOM has been published", "code": "unpublished"}), 404
    resp = jsonify(b)
    if _arg("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="{ab.SYSTEM}.json"'
    return resp


@aibom_bp.route("/api/document/<kind>")
def aibom_api_document(kind: str):
    """The newest published AI-BOM's SPDX 3.0.1 document (``spdx``) or CycloneDX 1.7 BOM (``cyclonedx``)."""
    from tools.compliance import aibom_bundle as ab

    if kind not in ("spdx", "cyclonedx"):
        return jsonify({"error": f"{kind!r} is spdx or cyclonedx", "code": "kind"}), 404
    b = ab.published_bundle()
    if b is None:
        return jsonify({"error": "no AI-BOM has been published", "code": "unpublished"}), 404
    resp = jsonify(b[kind])
    if _arg("download"):
        name = f"{ab.SYSTEM}.spdx3.json" if kind == "spdx" else f"{ab.SYSTEM}.cdx.json"
        resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


@aibom_bp.route("/api/twin")
def aibom_api_twin():
    """The AI-BOM twin, observed now (not recorded), with each finding's thread.

    ``?thread=<id>`` resolves one finding's thread to the ledger row it rests on.
    """
    from tools.twin_core import governance

    thread = _arg("thread")
    try:
        if thread:
            return jsonify(governance.resolve_thread(thread))
        import tools.twin_core.adapters.aibom  # noqa: F401  (registers the twin)

        return jsonify(governance.observe(governance.adapter_for("aibom"), record=False))
    except Exception as exc:  # noqa: BLE001 — a twin that cannot observe says so
        return jsonify({"error": f"the AI-BOM twin could not observe: {exc}"}), 503
