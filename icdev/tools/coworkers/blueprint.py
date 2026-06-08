# CUI // SP-CTI
"""Co-Workers canvas — Flask blueprint (key ``cwk``, prefix ``/coworkers``).

Pages / routes (``coworkers_bp``):

  GET  /coworkers              roster of AI co-workers (cards)
  GET  /coworkers/c/<id>       open the shared chat seeded with that co-worker
  GET  /coworkers/status       JSON status — roster counts by kind

The roster is config-driven (``registry.list_coworkers``); there is no
canvas-specific table, so this blueprint never touches the database and cannot
500 on a missing migration.  Selecting a co-worker 302-redirects into the
shared ``/chat`` via ``context_factory.build_chat_link``.

``create_coworkers_blueprint()`` is the registration factory; a module-level
``coworkers_bp`` instance is also exported so ``app.py``'s ``_CANVAS_DEFS`` loop
can pick it up directly.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, redirect, render_template

from icdev.tools.coworkers import constants as _const
from icdev.tools.coworkers.context_factory import build_chat_link
from icdev.tools.coworkers.registry import get_coworker, list_coworkers

logger = logging.getLogger("icdev.coworkers.blueprint")


def create_coworkers_blueprint() -> Blueprint:
    """Build and return the ``/coworkers`` blueprint."""
    bp = Blueprint(
        "coworkers",
        __name__,
        url_prefix=_const.URL_PREFIX,
    )

    @bp.route("")
    @bp.route("/")
    def index():
        """Co-worker roster — one card per co-worker, links to the shared chat."""
        try:
            coworkers = [cw.to_dict() for cw in list_coworkers()]
        except Exception as exc:  # noqa: BLE001 — empty roster beats a 500
            logger.warning("coworkers index roster load failed: %s", exc)
            coworkers = []
        # Pre-compute chat links so the template stays logic-free.
        for cw in coworkers:
            obj = get_coworker(cw["id"])
            cw["chat_link"] = build_chat_link(obj) if obj else "/chat"
        counts = {
            "total": len(coworkers),
            "persona": sum(1 for c in coworkers if c["kind"] == "persona"),
            "ace": sum(1 for c in coworkers if c["kind"] == "ace"),
            "ref": sum(1 for c in coworkers if c["kind"] == "ref"),
        }
        try:
            return render_template(
                "coworkers/index.html", coworkers=coworkers, counts=counts
            )
        except Exception as exc:  # noqa: BLE001 — JSON fallback if template absent
            logger.info("coworkers/index.html unavailable (%s); JSON fallback", exc)
            return jsonify({"coworkers": coworkers, "counts": counts})

    @bp.route("/c/<path:coworker_id>")
    def open_chat(coworker_id: str):
        """Open the shared chat seeded with the selected co-worker's persona."""
        cw = get_coworker(coworker_id)
        if cw is None:
            return jsonify({"error": f"unknown co-worker: {coworker_id}"}), 404
        return redirect(build_chat_link(cw))

    @bp.route("/status")
    def status():
        """JSON status — roster counts by kind (canvas health probe)."""
        try:
            roster = list_coworkers()
            return jsonify(
                {
                    "ok": True,
                    "canvas": _const.CANVAS_KEY,
                    "total": len(roster),
                    "persona": sum(1 for c in roster if c.kind == "persona"),
                    "ace": sum(1 for c in roster if c.kind == "ace"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("coworkers status failed: %s", exc)
            return jsonify({"ok": False, "canvas": _const.CANVAS_KEY, "error": str(exc)}), 500

    return bp


# Module-level instance for the _CANVAS_DEFS registration loop in app.py.
coworkers_bp = create_coworkers_blueprint()
