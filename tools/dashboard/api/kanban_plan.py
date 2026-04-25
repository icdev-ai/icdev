# CUI // SP-CTI
"""Kanban Plan API — task decomposition and scheduling endpoints."""

import pathlib
import time as _t

from flask import Blueprint, jsonify

kanban_plan_api = Blueprint("kanban_plan_api", __name__)


@kanban_plan_api.route("/api/kanban/plans", methods=["GET"])
def list_plans():
    """List kanban plans."""
    return jsonify({"plans": [], "total": 0})


@kanban_plan_api.route("/api/kanban/scheduler-status", methods=["GET"])
def scheduler_status():
    """Kanban scheduler heartbeat from log mtime.

    scheduler_last_seen_secs > 600 → scheduler likely dead (zombie tasks possible).
    """
    log_path = pathlib.Path(".tmp/kanban_scheduler.log")
    scheduler_last_seen_secs = None
    if log_path.exists():
        scheduler_last_seen_secs = int(_t.time() - log_path.stat().st_mtime)

    if scheduler_last_seen_secs is None:
        staleness = "unknown"
    elif scheduler_last_seen_secs > 600:
        staleness = "stale"
    elif scheduler_last_seen_secs > 180:
        staleness = "warning"
    else:
        staleness = "active"

    return jsonify({
        "scheduler_last_seen_secs": scheduler_last_seen_secs,
        "staleness": staleness,
    })
