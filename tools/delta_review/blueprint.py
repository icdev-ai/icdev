# CUI // SP-CTI
"""Delta Review — side-by-side HITL delta panel — Flask Blueprint (trust-hitl-02).

:func:`create_delta_review_blueprint` returns the canvas blueprint, or ``None``
when ``ICDEV_DELTA_REVIEW_ENABLED`` is off, so ``app.py`` registers-or-skips with
one guard and the canvas is fully dark when toggled off.

Pages::

  GET  /delta-review                      review queue
  GET  /delta-review?delta_id=<id>        queue + the side-by-side panel

JSON API::

  GET  /api/delta-review/deltas           pending queue (?limit=)
  GET  /api/delta-review/delta/<id>       one delta's full panel payload
  GET  /api/delta-review/artifact/<id>    the delta chain for one artifact
  POST /api/delta-review/delta/<id>/settle  {approved: bool, rationale: str}
  POST /delta-review/api/iqe-query        plain-English → IQE → rows

Pages and API mount on two different roots, so — like ``integrity`` and
``supply_chain`` — each route carries its explicit path and the registry entry
declares ``url_prefix: ''``.

THE SETTLE ROUTE IS THE POINT OF THE CANVAS, and it has three properties that
are not decoration:

1. **A rationale is mandatory** and is checked HERE as well as in
   ``hitl_delta.settle_delta``. Not redundancy for its own sake: this is the
   layer that can return 400 and tell a human why, whereas the store can only
   return ``None``. An approval with no stated reason is unauditable after the
   fact and indistinguishable from a bug (``trust_gate`` invariant 4).
2. **The actor is the authenticated user, never the request body.** A caller
   must not be able to attribute a disposition to someone else — the same rule
   ``integrity.blueprint._reviewer`` enforces on promote/reject (nav-comp-06).
   Any body-supplied ``actor`` is deliberately ignored.
3. **Settling is not idempotent-by-overwrite.** A second settle on the same
   delta returns 409, because the store refuses it — the successor row already
   exists and a second one would give the panel two contradictory answers.

RLS: reads run through ``tools.quality.hitl_delta``, which uses
``get_connection()`` — ``trust_deltas`` carries ``tenant_id`` + ``classification``
so the global predicate filters every row to the caller's tenant and clearance.
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
from tools.delta_review.review import artifact_history, delta_payload, panel_context
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.delta_review.blueprint")

_IQE_ROUTE = "/delta-review/api/iqe-query"


def _is_enabled() -> bool:
    """True when this canvas is toggled on.

    Asks the REGISTRY, which is the single source of truth for enablement and is
    the only thing that knows this component declares ``default_enabled: true``.
    Re-deciding it here from ``os.environ`` alone would mean the flag being
    UNSET reads as off, so a canvas the registry says ships on would mount
    nowhere and report nothing — the registry entry and the factory would
    disagree, silently, with the factory winning.

    Falls back to reading the env flag directly if the registry cannot be
    loaded, and in that fallback an unset flag means ENABLED, matching the
    ``default_enabled: true`` this component declares.
    """
    try:
        from tools.config.component_registry import ComponentRegistry

        component = ComponentRegistry().get(CANVAS_KEY)
        if component is not None:
            return component.is_enabled()
    except Exception as exc:  # noqa: BLE001 — never let registry load failure 500 startup
        logger.warning("delta_review: registry unavailable (%s); reading %s directly",
                       exc, FEATURE_FLAG)
    return str(os.environ.get(FEATURE_FLAG, "true")).strip().strip('"').strip("'").lower() in (
        "1", "true", "yes", "on", "enabled",
    )


def _reviewer() -> str:
    """The HITL reviewer recorded on a settlement.

    Bound to the authenticated user; a body-supplied actor is ignored. Falls
    back to a generic ``dashboard`` actor rather than to an empty string, so a
    settlement is never attributed to nobody.
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

    # ── Page ──────────────────────────────────────────────────────────────── #
    @bp.route("/delta-review")
    @bp.route("/delta-review/")
    def delta_review_page():
        """Review queue, plus the side-by-side panel when ``?delta_id=`` is set.

        One template rather than a list page and a detail page: a reviewer
        working a queue wants the next item visible without a round trip, and a
        second template is a second icdev/ mirror to keep in step for no gain.
        """
        delta_id = (request.args.get("delta_id") or "").strip()
        try:
            context = panel_context(delta_id or None, limit=_limit_arg())
        except Exception as exc:  # noqa: BLE001 — an empty board must not 500
            logger.warning("delta_review page read failed: %s", exc)
            context = {
                "summary": {"telemetry_available": False}, "queue": [],
                "selected": None, "not_found": "", "telemetry_available": False,
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

    # ── JSON API — reads ──────────────────────────────────────────────────── #
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
        from tools.quality.hitl_delta import get_delta

        try:
            delta = get_delta(delta_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_delta read failed for %s: %s", delta_id, exc)
            delta = None
        if delta is None:
            return jsonify({"error": f"delta not found: {delta_id}"}), 404
        payload = delta_payload(delta)
        payload["history"] = artifact_history(delta.artifact_id, limit=DEFAULT_LIMIT)
        return jsonify(payload)

    @bp.route("/api/delta-review/artifact/<path:artifact_id>", methods=["GET"])
    def api_artifact(artifact_id: str):
        try:
            history = artifact_history(artifact_id, limit=_limit_arg())
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_artifact read failed for %s: %s", artifact_id, exc)
            history = []
        return jsonify({"artifact_id": artifact_id, "history": history,
                        "count": len(history)})

    # ── JSON API — the settle write ───────────────────────────────────────── #
    @bp.route("/api/delta-review/delta/<delta_id>/settle", methods=["POST"])
    def api_settle(delta_id: str):
        """Record a human's disposition of one delta.

        Body: ``{approved: bool, rationale: str}``. ``rationale`` is mandatory —
        see the module docstring. The actor is the authenticated user; a
        body-supplied ``actor`` is ignored.
        """
        data = request.get_json(force=True, silent=True) or {}

        if "approved" not in data:
            # Neither implied nor defaulted. A disposition must be an explicit,
            # unambiguous act — the same call approval_inbox's CLI makes when it
            # refuses --resolve without exactly one of --approve/--deny.
            return jsonify({"error": "approved (true|false) is required"}), 400
        approved = bool(data.get("approved"))

        rationale = str(data.get("rationale") or "").strip()
        if len(rationale) < MIN_RATIONALE_CHARS:
            return jsonify({
                "error": (
                    f"rationale is mandatory and must be at least "
                    f"{MIN_RATIONALE_CHARS} characters — an unexplained "
                    "disposition is unauditable after the fact"
                ),
                "min_chars": MIN_RATIONALE_CHARS,
            }), 400

        from tools.quality.hitl_delta import (
            DeltaStoreUnavailable, get_delta, settle_delta,
        )

        if get_delta(delta_id) is None:
            return jsonify({"error": f"delta not found: {delta_id}"}), 404

        try:
            settlement = settle_delta(
                delta_id,
                approved=approved,
                actor=_reviewer(),
                rationale=rationale,
            )
        except DeltaStoreUnavailable as exc:
            logger.error("delta_review settle failed for %s: %s", delta_id, exc)
            return jsonify({"error": f"could not record the settlement: {exc}"}), 500
        except Exception as exc:  # noqa: BLE001
            logger.exception("delta_review settle errored for %s", delta_id)
            return jsonify({"error": f"settle failed: {exc}"}), 500

        if settlement is None:
            # The store refused: already settled, or gone between the lookup
            # above and the write. Not a 500 — the caller's request was
            # well-formed, the delta's state simply does not permit it.
            return jsonify({
                "error": f"{delta_id} is already settled or is no longer pending",
                "delta_id": delta_id,
            }), 409

        return jsonify({
            "ok": True,
            "delta_id": delta_id,
            "settlement_id": settlement.delta_id,
            "disposition": settlement.disposition,
            "actor": settlement.actor,
            "rationale": settlement.rationale,
            "approval_item_id": settlement.approval_item_id,
        }), 201

    # ── JSON API — IQE natural-language query ─────────────────────────────── #
    @bp.route(_IQE_ROUTE, methods=["POST"])
    def delta_review_iqe_query():
        """Plain-English → IQE → rows over the ``delta_review.*`` collections.

        Mirrors the canvas-aware dispatcher in ``app.py`` (``nl_to_iqe`` →
        ``parse`` → ``execute_query``) so the shared
        ``includes/iqe_query_widget.html`` renders the generated IQE plus the
        matching rows. Reads run RLS-aware via the registered adapters.
        """
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        iqe_str = ""
        try:
            from tools.iqe import adapters as _adapters  # noqa: F401
            from tools.iqe.adapters import delta_review as _  # noqa: F401  registers collections
            from tools.iqe.executor import execute_query
            from tools.iqe.nl_to_iqe import nl_to_iqe
            from tools.iqe.parser import IQESyntaxError, parse as iqe_parse

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
