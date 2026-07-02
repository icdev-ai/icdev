"""tools/updates/blueprint.py — Platform Updates canvas blueprint.

The /updates route is also registered directly in app.py via
tools.dashboard.changelog. This module provides the canonical blueprint
for the coherence gate and route completeness checks.
"""
from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("updates", __name__)


@bp.route("/updates")
def updates_page():
    """Render platform updates / changelog page."""
    from tools.updates.engine import get_releases
    releases = get_releases()
    return render_template("updates/page.html", releases=releases)
