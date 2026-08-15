# CUI // SP-CTI
"""Delta Review — side-by-side HITL delta panel — Flask Blueprint (trust-hitl-02).

:func:`create_delta_review_blueprint` returns the canvas blueprint, or ``None``
when ``ICDEV_DELTA_REVIEW_ENABLED`` is off, so ``app.py`` registers-or-skips with
one guard and the canvas is fully dark when toggled off.

Pages::

  GET  /delta-review                        review queue
  GET  /delta-review?delta_id=<id>          queue + the side-by-side panel

JSON API::

  GET  /api/delta-review/deltas             pending queue (?limit=)
  GET  /api/delta-review/delta/<id>         one delta's full panel payload
  GET  /api/delta-review/artifact/<id>      that artifact's delta timeline
  POST /api/delta-review/delta/<id>/settle  {approved: bool, rationale: str}
  POST /delta-review/api/iqe-query          plain-English -> IQE -> rows

Pages and API mount on two different roots, so — like ``integrity`` and
``supply_chain`` — each route carries its explicit path and the registry entry
declares ``url_prefix: ''``.

THE SETTLE ROUTE IS THE POINT OF THE CANVAS, and it has three properties that
are not decoration:

1. **A rationale is mandatory, and this is the layer that can insist.**
   ``hitl_delta.settle_delta`` accepts an empty ``reason`` and substitutes
   ``"delta <id> approved"`` so a CLI or a sweep still writes a well-formed
   ``agent_approval_log`` row. That default restates the action and says nothing
   about the evidence. A human looking at the diff can be asked for better, and
   an unexplained disposition is unauditable after the fact and
   indistinguishable from a bug (``trust_gate`` invariant 4).

2. **The actor is the authenticated user, never the request body.** A caller
   must not be able to attribute a decision to someone else — the rule
   ``integrity.blueprint._reviewer`` enforces on promote/reject (nav-comp-06).
   Any body-supplied ``actor`` is deliberately ignored.

3. **A second settle is a 409, never an overwrite.** ``approval_inbox._settle``
   UPDATEs conditionally on ``state = 'pending'`` and returns ``None`` when it
   matched nothing, so a repeat is refused at the store. This route surfaces
   that refusal rather than reporting success on a decision it did not make.

WHERE THE DECISION IS WRITTEN. Not here and not in ``trust_deltas``, which is
append-only evidence with no disposition column. ``settle_delta`` moves the
``approval_items`` row through ``approval_inbox.resolve``, which writes the
permanent ``agent_approval_log`` entry carrying the reviewer's rationale.

RLS: reads run through ``tools.quality.hitl_delta``, which uses
``get_connection()`` — ``trust_deltas`` carries ``classification`` so the global
predicate filters every row to the caller's tenant and clearance.
"""
from __future__ import annotations

import os
from typing import Optional

from flask import Blueprint, g, jsonify, render_template, request

from tools.delta_review.constants import (
    CANVAS_KEY,
    DEFAULT_LIMIT,
    FEATURE_FLAG,
    IQE_COLLECTIONS,
    IQE_EXAMPLES,
    MAX_LIMIT,
    MIN_RATIONALE_CHARS,
)
from tools.delta_review.review import (
    artifact_timeline,
    correction_chain,
    delta_payload,
    panel_context,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.delta_review.blueprint")

_IQE_ROUTE = "/delta-review/api/iqe-query"

#: ``settle_delta`` reports a refusal as ``{settled: False, reason: str}``.
#: These map its three refusals onto status codes. Anything unrecognised is a
#: 409 rather than a 500: the store declined a well-formed request, which is a
#: conflict with the delta's state, not a server fault.
_REFUSAL_STATUS = {
    "no such delta": 404,
    "delta was recorded with no approval item": 409,
    "approval item is absent or already settled": 409,
}


def _is_enabled() -> bool:
    """True when this canvas is toggled on.

    Asks the REGISTRY, which is the single source of truth for enablement and is
    the only thing that knows this component declares ``default_enabled: true``.
    Re-deciding it from ``os.environ`` alone would mean an UNSET flag reads as
    off, so a canvas the registry says ships on would mount nowhere and report
    nothing — the registry entry and the factory disagreeing silently, with the
    factory winning.

    Falls back to the env flag if the registry cannot be loaded, and in that
    fallback an unset flag means ENABLED, matching ``default_enabled: true``.
    """
    try:
        from tools.config.component_registry import ComponentRegistry

        component = ComponentRegistry().get(CANVAS_KEY)
        if component is not None:
            return component.is_enabled()
    except Exception as exc:  # noqa: BLE001 — never let a registry failure 500 startup
        logger.warning("delta_review: registry unavailable (%s); reading %s directly",
                       exc, FEATURE_FLAG)
    return str(os.environ.get(FEATURE_FLAG, "true")).strip().strip('"').strip("'").lower() in (
        "1", "true", "yes", "on", "enabled",
    )


def _reviewer() -> str:
    """The reviewer recorded against a settlement.

    Bound to the authenticated user; a body-supplied actor is ignored. Falls
    back to a generic ``dashboard`` actor rather than an empty string, so a
    decision is never attributed to nobody. ``approval_inbox.resolve`` runs this
    through ``resolve_actor`` again, which is harmless and keeps the store's own
    guarantee independent of its callers.
    """
    try:
        user = getattr(g, "current_user", None)
        if isinstance(user, dict):
            return user.get("username") or user.get("email") or "dashboard"
        if user:
            return str(getattr(user, "username", None) or user)
    except RuntimeError:
        pass
    return "dashboard"


def _limit_arg() -> int:
    try:
        n = int(request.args.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def create_delta_review_blueprint() -> Optional[Blueprint]:
    """Build the Delta Review blueprint, or ``None`` when the canvas is off."""
    if not _is_enabled():
        logger.info("delta_review blueprint: %s is off — canvas not mounted", FEATURE_FLAG)
        return None

    bp = Blueprint(CANVAS_KEY, __name__)

    # -- Page ------------------------------------------------------------- #
    @bp.route("/delta-review")
    @bp.route("/delta-review/")
    def delta_review_page():
        """Review queue, plus the side-by-side panel when ``?delta_id=`` is set.

        One template rather than a list page and a detail page: a reviewer
        working a queue wants the next item visible without a round trip, and a
        second template is a second ``icdev/`` mirror to keep in step for no
        gain.
        """
        delta_id = (request.args.get("delta_id") or "").strip()
        try:
            context = panel_context(delta_id or None, limit=_limit_arg())
        except Exception as exc:  # noqa: BLE001 — an unreadable board must not 500
            logger.warning("delta_review page read failed: %s", exc)
            context = {
                "summary": {}, "queue": [], "selected": None,
                "not_found": "", "telemetry_available": False,
            }

        try:
            return render_template(
                "delta_review/page.html",
                iqe_canvas=CANVAS_KEY,
                iqe_api_route=_IQE_ROUTE,
                iqe_examples=list(IQE_EXAMPLES),
                iqe_title="Ask about deltas",
                min_rationale_chars=MIN_RATIONALE_CHARS,
                **context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("delta_review/page.html unavailable (%s); JSON fallback", exc)
            return jsonify(context)

    # -- JSON API: reads --------------------------------------------------- #
    @bp.route("/api/delta-review/deltas", methods=["GET"])
    def api_deltas():
        try:
            context = panel_context(None, limit=_limit_arg())
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_deltas read failed: %s", exc)
            return jsonify({"deltas": [], "count": 0, "telemetry_available": False})
        return jsonify({
            "deltas": context["queue"],
            "count": len(context["queue"]),
            "summary": context["summary"],
            "telemetry_available": context["telemetry_available"],
        })

    @bp.route("/api/delta-review/delta/<delta_id>", methods=["GET"])
    def api_delta(delta_id: str):
        from tools.quality.hitl_delta import get as get_delta

        try:
            delta = get_delta(delta_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_delta read failed for %s: %s", delta_id, exc)
            delta = None
        if delta is None:
            return jsonify({"error": f"delta not found: {delta_id}"}), 404
        payload = delta_payload(delta)
        payload["timeline"] = artifact_timeline(delta.artifact_id, limit=DEFAULT_LIMIT)
        payload["chain"] = correction_chain(delta.delta_id)
        return jsonify(payload)

    @bp.route("/api/delta-review/artifact/<path:artifact_id>", methods=["GET"])
    def api_artifact(artifact_id: str):
        try:
            timeline = artifact_timeline(artifact_id, limit=_limit_arg())
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_artifact read failed for %s: %s", artifact_id, exc)
            timeline = []
        return jsonify({"artifact_id": artifact_id, "timeline": timeline,
                        "count": len(timeline)})

    # -- JSON API: the settle write ---------------------------------------- #
    @bp.route("/api/delta-review/delta/<delta_id>/settle", methods=["POST"])
    def api_settle(delta_id: str):
        """Record a human's decision on one delta.

        Body: ``{approved: bool, rationale: str}``. The actor is the
        authenticated user; a body-supplied ``actor`` is ignored.
        """
        data = request.get_json(force=True, silent=True) or {}

        if "approved" not in data:
            # Neither implied nor defaulted. A decision must be an explicit,
            # unambiguous act — the call approval_inbox's own CLI makes when it
            # refuses --resolve without exactly one of --approve/--deny.
            return jsonify({"error": "approved (true|false) is required"}), 400
        approved = bool(data.get("approved"))

        rationale = str(data.get("rationale") or "").strip()
        if len(rationale) < MIN_RATIONALE_CHARS:
            return jsonify({
                "error": (
                    f"rationale is mandatory and must be at least "
                    f"{MIN_RATIONALE_CHARS} characters — an unexplained "
                    "decision is unauditable after the fact"
                ),
                "min_chars": MIN_RATIONALE_CHARS,
            }), 400

        from tools.quality.hitl_delta import get as get_delta
        from tools.quality.hitl_delta import settle_delta

        if get_delta(delta_id) is None:
            return jsonify({"error": f"delta not found: {delta_id}"}), 404

        try:
            result = settle_delta(
                delta_id,
                approved=approved,
                actor=_reviewer(),
                reason=rationale,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("delta_review settle errored for %s", delta_id)
            return jsonify({"error": f"settle failed: {exc}"}), 500

        if not result.get("settled"):
            reason = str(result.get("reason") or "the delta's state does not permit it")
            status = _REFUSAL_STATUS.get(reason, 409)
            if reason.startswith("inbox unavailable"):
                # Not the caller's fault and not a state conflict: the inbox
                # module could not be imported at all.
                status = 503
            return jsonify({"error": reason, "delta_id": delta_id}), status

        return jsonify({
            "ok": True,
            "delta_id": delta_id,
            "approval_item_id": result.get("approval_item_id", ""),
            "state": result.get("state", ""),
            "resolution": result.get("resolution", ""),
            "resolved_by": result.get("resolved_by", ""),
            "resolved_at": result.get("resolved_at", ""),
            "rationale": rationale,
        }), 200

    # -- JSON API: IQE natural-language query ------------------------------ #
    @bp.route(_IQE_ROUTE, methods=["POST"])
    def delta_review_iqe_query():
        """Plain-English -> IQE -> rows over the ``delta_review.*`` collections.

        Mirrors the canvas-aware dispatcher in ``app.py`` (``nl_to_iqe`` ->
        ``parse`` -> ``execute_query``) so the shared
        ``includes/iqe_query_widget.html`` renders the generated IQE plus the
        matching rows. Reads run RLS-aware via the registered adapters.
        """
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        iqe_str = ""
        try:
            from tools.iqe.adapters import delta_review as _  # noqa: F401  registers collections
            from tools.iqe.executor import execute_query
            from tools.iqe.nl_to_iqe import nl_to_iqe
            from tools.iqe.parser import IQESyntaxError
            from tools.iqe.parser import parse as iqe_parse

            translated = nl_to_iqe(question, list(IQE_COLLECTIONS))
            iqe_str = translated.get("iqe", "")
            explanation = translated.get("explanation", "")
            try:
                ast = iqe_parse(iqe_str)
                rows = execute_query(ast, conn=None)
            except IQESyntaxError:
                rows = []
            return jsonify({
                "ok": True, "canvas": CANVAS_KEY, "iqe": iqe_str,
                "explanation": explanation, "results": rows, "row_count": len(rows),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("delta_review iqe-query error: %s", exc)
            return jsonify({"error": str(exc), "canvas": CANVAS_KEY, "iqe": iqe_str}), 500

    logger.info("delta_review blueprint mounted (page + JSON API + IQE)")
    return bp


__all__ = ["create_delta_review_blueprint"]
