# CUI // SP-CTI
"""AI-assisted vulnerability discovery console (icx-ent-01): /vulnresearch/.

Who decides: anyone (or an agent) registers a target, runs discovery on an AUTHORISED one, proposes a patch and
prepares a findings bundle; none of those decides anything. A person who did not register a target authorises it
(decl-wft-vr-target-authorise); a person who did not propose a patch reviews it (decl-wft-vr-patch-review); a
person who did not prepare a bundle publishes it to ICDEV[OS] (decl-wft-vr-findings-publish). Each is a declared
step of tools/workflow_hitl, taken in one action on the page and bound to the digest the person read. Revoking a
target is a stop: immediate, with a reason.

Routes:
  GET  /vulnresearch/                                    The page
  GET  /vulnresearch/api/targets                         JSON: every target and its state
  POST /vulnresearch/api/targets                         Register a target (decides nothing)
  GET  /vulnresearch/api/targets/<tid>                   JSON: one target, its runs, patches and bundle ledger
  POST /vulnresearch/api/targets/<tid>/authorise         A person authorises or refuses (declared step)
  POST /vulnresearch/api/targets/<tid>/revoke            A person revokes it (a stop)
  POST /vulnresearch/api/targets/<tid>/runs              Run discovery on an authorised target
  POST /vulnresearch/api/targets/<tid>/measure           Measure FP/FN on the labelled benchmark target
  POST /vulnresearch/api/findings/<fid>/patches          Propose a patch (validated in the sandbox)
  POST /vulnresearch/api/findings/<fid>/review           A person accepts or rejects it (declared step)
  POST /vulnresearch/api/targets/<tid>/bundle/prepare    Prepare the findings bundle (decides nothing)
  POST /vulnresearch/api/targets/<tid>/bundle/publish    A person publishes or refuses it (declared step)
  GET  /vulnresearch/api/targets/<tid>/bundle            The newest published bundle and its publication
  GET  /vulnresearch/api/twin                            The governance twin, observed now (?thread= resolves)
  POST /vulnresearch/api/iqe-query                       IQE natural-language query over the findings and targets
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template
from flask import request as flask_request

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.vulnresearch.blueprint")

vr_bp = Blueprint("vulnresearch", __name__, url_prefix="/vulnresearch")


def _body() -> dict:
    return flask_request.get_json(force=True, silent=True) or {}


def _refused(exc):
    body = exc.to_dict()
    if getattr(exc, "validation", None):
        body["validation"] = exc.validation
    return jsonify(body), exc.status


def _actor():
    from tools.workflow_hitl.declared import current_actor

    return current_actor()


@vr_bp.route("/")
def vr_page():
    """Targets, discovery runs with their findings and dropped leads, patches, the bundle and the twin."""
    from tools.security.sandbox_executor import isolated_profile
    from tools.vulnresearch import benchmark, constants, targets

    try:
        view = targets.targets_view()
        error = ""
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger is said, not hidden
        logger.warning("vulnresearch: ledger unreadable: %s", exc)
        view, error = {"targets": []}, str(exc)
    try:
        measurement = benchmark.latest()
    except Exception:  # noqa: BLE001
        measurement = None
    return render_template("vulnresearch/index.html", view=view, error=error, measurement=measurement,
                           iqe_canvas=constants.IQE_CANVAS, iqe_api_route=constants.IQE_API_ROUTE,
                           iqe_examples=list(constants.IQE_EXAMPLES), iqe_title="Query findings (IQE)",
                           isolation=isolated_profile(), templates={
                               "authorise": constants.TEMPLATE_AUTHORISE, "patch": constants.TEMPLATE_PATCH,
                               "publish": constants.TEMPLATE_PUBLISH})


@vr_bp.route("/api/targets", methods=["GET"])
def vr_api_targets():
    from tools.vulnresearch import targets

    try:
        return jsonify(targets.targets_view())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"the vulnerability research ledger could not be read: {exc}"}), 503


@vr_bp.route("/api/targets", methods=["POST"])
def vr_api_target_register():
    """Register a target at its tree digest. Anyone signed in (a service key as an agent); it decides nothing.

    Body: ``{name, root, reason, src_root, test_dir, fuzz_targets: [module:function], expires_on, system_id}``.
    """
    from tools.vulnresearch import targets

    d = _body()
    try:
        out = targets.register(_actor(), name=str(d.get("name") or ""), root=str(d.get("root") or ""),
                               reason=str(d.get("reason") or ""), src_root=str(d.get("src_root") or "."),
                               test_dir=str(d.get("test_dir") or ""),
                               fuzz_targets=[str(x) for x in d.get("fuzz_targets") or [] if str(x).strip()],
                               expires_on=str(d.get("expires_on") or ""),
                               system_id=str(d.get("system_id") or ""))
    except targets.TargetRefused as exc:
        return _refused(exc)
    return jsonify(out), 201


@vr_bp.route("/api/targets/<tid>")
def vr_api_target(tid: str):
    """One target: its registration and decision, its runs (sealed PoVs withheld), its patches, its bundle ledger."""
    from tools.vulnresearch import bundle, discovery, patches, targets

    try:
        st = targets.state(tid)
        return jsonify({"target": st, "runs": discovery.runs(tid), "patches": patches.patches_for(tid),
                        "bundle": bundle.ledger_view(tid)})
    except targets.TargetRefused as exc:
        return _refused(exc)


@vr_bp.route("/api/targets/<tid>/authorise", methods=["POST"])
def vr_api_target_authorise(tid: str):
    """A person, never the registrant, authorises a registered target for testing, or refuses it.

    Body: ``{decision: authorise|refuse, reason, digest}``. The person's step of decl-wft-vr-target-authorise,
    bound to the digest of the registration read.
    """
    from tools.vulnresearch import targets

    d = _body()
    try:
        return jsonify(targets.authorise(tid, decision=str(d.get("decision") or ""), reason=str(d.get("reason") or ""),
                                         read_digest=str(d.get("digest") or ""), actor=_actor()))
    except targets.TargetRefused as exc:
        return _refused(exc)


@vr_bp.route("/api/targets/<tid>/revoke", methods=["POST"])
def vr_api_target_revoke(tid: str):
    """A stop: a signed-in person revokes a target at once, with a reason. Nothing scans it again."""
    from tools.vulnresearch import targets

    try:
        return jsonify(targets.revoke(tid, reason=str(_body().get("reason") or ""), actor=_actor()))
    except targets.TargetRefused as exc:
        return _refused(exc)


@vr_bp.route("/api/targets/<tid>/runs", methods=["POST"])
def vr_api_run(tid: str):
    """Run discovery on an authorised target (scope-locked; target code only in the sandbox). Body: ``{use_model}``."""
    from tools.vulnresearch import discovery

    try:
        out = discovery.run(tid, _actor(), use_model=bool(_body().get("use_model", True)))
    except discovery.RunRefused as exc:
        return _refused(exc)
    return jsonify(out), 201


@vr_bp.route("/api/targets/<tid>/measure", methods=["POST"])
def vr_api_measure(tid: str):
    """Run discovery on the registered benchmark target and record FP/FN against its labels (no model judges)."""
    from tools.vulnresearch import benchmark, ledger

    try:
        out = benchmark.measure(tid, _actor(), use_model=bool(_body().get("use_model", True)))
    except ledger.LedgerRefused as exc:
        return _refused(exc)
    return jsonify(out), 201


@vr_bp.route("/api/findings/<fid>/patches", methods=["POST"])
def vr_api_patch_propose(fid: str):
    """Propose a patch for a confirmed finding: ``{diff}``, or ``{draft: true}`` for a model draft. It is applied to
    a throwaway copy and proposed only when the PoV now fails and the tests pass; nothing is applied to the target."""
    from tools.vulnresearch import patches

    d = _body()
    try:
        if d.get("draft"):
            out = patches.draft(fid, _actor())
        else:
            out = patches.propose(fid, str(d.get("diff") or ""), _actor(), note=str(d.get("note") or ""))
    except patches.PatchRefused as exc:
        return _refused(exc)
    return jsonify(out), 201


@vr_bp.route("/api/findings/<fid>/review", methods=["POST"])
def vr_api_patch_review(fid: str):
    """A person, never the proposer, accepts or rejects a proposed patch. Nothing is applied.

    Body: ``{decision: accept|reject, reason, digest}``. The person's step of decl-wft-vr-patch-review.
    """
    from tools.vulnresearch import patches

    d = _body()
    try:
        return jsonify(patches.review(fid, decision=str(d.get("decision") or ""), reason=str(d.get("reason") or ""),
                                      read_digest=str(d.get("digest") or ""), actor=_actor()))
    except patches.PatchRefused as exc:
        return _refused(exc)


@vr_bp.route("/api/targets/<tid>/bundle/prepare", methods=["POST"])
def vr_api_bundle_prepare(tid: str):
    """Prepare the target's icos-findings/v1 bundle at its digest. Anyone signed in; it decides nothing."""
    from tools.vulnresearch import bundle

    try:
        out = bundle.prepare(tid, _actor(), note=str(_body().get("note") or ""))
    except bundle.BundleRefused as exc:
        return _refused(exc)
    return jsonify(out), 201


@vr_bp.route("/api/targets/<tid>/bundle/publish", methods=["POST"])
def vr_api_bundle_publish(tid: str):
    """A person, never the preparer, publishes a target's prepared findings bundle to ICDEV[OS], or refuses it.

    Body: ``{decision: publish|refuse, reason, digest}``. The person's step of decl-wft-vr-findings-publish.
    """
    from tools.vulnresearch import bundle

    d = _body()
    try:
        return jsonify(bundle.publish(tid, decision=str(d.get("decision") or ""), reason=str(d.get("reason") or ""),
                                      read_digest=str(d.get("digest") or ""), actor=_actor()))
    except bundle.BundleRefused as exc:
        return _refused(exc)


@vr_bp.route("/api/targets/<tid>/bundle")
def vr_api_bundle(tid: str):
    """The newest PUBLISHED bundle exactly as prepared, and its publication beside it, as ICDEV[OS] imports them."""
    from tools.vulnresearch import bundle

    b = bundle.published(tid)
    if b is None:
        return jsonify({"error": f"no findings bundle of {tid} has been published", "code": "unpublished"}), 404
    return jsonify(b)


@vr_bp.route("/api/twin")
def vr_api_twin():
    """The vulnerability discovery governance twin, observed now (not recorded). ``?thread=`` resolves one thread."""
    from tools.twin_core import governance

    thread = (flask_request.args.get("thread") or "").strip()
    try:
        if thread:
            return jsonify(governance.resolve_thread(thread))
        import tools.twin_core.adapters.vulnresearch  # noqa: F401  (registers the twin)

        return jsonify(governance.observe(governance.adapter_for("vulnresearch"), record=False))
    except Exception as exc:  # noqa: BLE001 — a twin that cannot observe says so
        return jsonify({"error": f"the vulnerability discovery twin could not observe: {exc}"}), 503


@vr_bp.route("/api/iqe-query", methods=["POST"])
def vr_api_iqe_query():
    """IQE natural-language query over vulnresearch.findings and vulnresearch.targets. Reads; sealed PoVs are no column."""
    from tools.iqe.executor import execute_query
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import IQESyntaxError, parse
    import tools.iqe.adapters.vulnresearch  # noqa: F401  (registers the collections)
    from tools.vulnresearch.constants import IQE_COLLECTIONS

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
        logger.warning("vulnresearch IQE query error: %s", exc)
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500
    return jsonify({"ok": True, "iqe": iqe_str, "explanation": translation.get("explanation", ""),
                    "results": rows, "row_count": len(rows)})
