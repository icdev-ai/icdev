# CUI // SP-CTI
"""Info Ops Platform — Flask Blueprint.

Routes:
  GET  /info-ops/           landing page
  GET  /info-ops/dashboard  main dashboard
  GET  /info-ops/analysis   analysis input + results
  GET  /info-ops/reports    report library
  GET  /info-ops/osint      OSINT data browser
  GET  /info-ops/settings   app configuration
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import os

from flask import Blueprint, jsonify, render_template, request

log = get_logger(__name__)

_INFO_OPS_ENABLED = os.environ.get("ICDEV_INFO_OPS_ENABLED", "true").lower() not in ("0", "false", "no")


def create_info_ops_blueprint() -> Blueprint | None:
    if not _INFO_OPS_ENABLED:
        log.info("Info Ops disabled via ICDEV_INFO_OPS_ENABLED=false")
        return None

    bp = Blueprint("info_ops", __name__, template_folder="../../tools/dashboard/templates")

    # ── Pages ─────────────────────────────────────────────────────────────────

    @bp.route("/")
    def index():
        return render_template("info_ops/index.html")

    @bp.route("/dashboard")
    def dashboard():
        return render_template("info_ops/dashboard.html")

    @bp.route("/analysis")
    def analysis():
        return render_template("info_ops/analysis.html")

    @bp.route("/reports")
    def reports():
        return render_template("info_ops/reports.html")

    @bp.route("/osint")
    def osint():
        return render_template("info_ops/osint.html")

    @bp.route("/settings")
    def settings():
        return render_template("info_ops/settings.html")

    # ── API ───────────────────────────────────────────────────────────────────

    @bp.route("/api/stats", methods=["GET"])
    def api_stats():
        return jsonify({
            "campaigns": 12,
            "active_operations": 4,
            "pending_reports": 7,
            "osint_feeds": 23,
            "alerts_today": 3,
            "classification": "CUI // SP-CTI",
        })

    @bp.route("/api/analysis", methods=["POST"])
    def api_analysis():
        data = request.get_json(silent=True) or {}
        query = data.get("query", "")
        return jsonify({
            "query": query,
            "status": "completed",
            "results": [
                {"type": "sentiment", "value": "negative", "confidence": 0.87},
                {"type": "narrative", "value": "disinformation campaign detected", "confidence": 0.92},
            ],
            "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        })

    @bp.route("/api/reports", methods=["GET"])
    def api_reports():
        return jsonify({
            "reports": [
                {"id": "rpt-001", "title": "Q2 Information Environment Assessment", "date": "2026-05-10", "status": "final"},
                {"id": "rpt-002", "title": "Social Media Narrative Analysis", "date": "2026-05-12", "status": "draft"},
                {"id": "rpt-003", "title": "OSINT Feed Quality Review", "date": "2026-05-14", "status": "final"},
            ]
        })

    @bp.route("/api/osint", methods=["GET"])
    def api_osint():
        return jsonify({
            "feeds": [
                {"id": "osint-001", "source": "Twitter/X", "category": "social", "status": "active", "last_update": "2026-05-15T14:30:00Z"},
                {"id": "osint-002", "source": "NewsAPI", "category": "news", "status": "active", "last_update": "2026-05-15T13:15:00Z"},
                {"id": "osint-003", "source": "RSS Mil-Intel", "category": "defense", "status": "active", "last_update": "2026-05-15T12:00:00Z"},
                {"id": "osint-004", "source": "Dark Web Monitor", "category": "cyber", "status": "warning", "last_update": "2026-05-14T22:00:00Z"},
            ]
        })

    return bp
