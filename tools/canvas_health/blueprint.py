# CUI // SP-CTI
"""Canvas Health Dashboard blueprint — /health/canvases."""
from flask import Blueprint, render_template

bp = Blueprint("canvas_health", __name__, url_prefix="")


@bp.route("/health/canvases")
def canvas_health_page():
    from tools.canvas_health.health_data import get_canvas_health
    canvases = get_canvas_health()
    green = sum(1 for c in canvases if c["status"] == "green")
    amber = sum(1 for c in canvases if c["status"] == "amber")
    red = sum(1 for c in canvases if c["status"] == "red")
    return render_template(
        "canvas_health/page.html",
        canvases=canvases,
        green=green,
        amber=amber,
        red=red,
        total=len(canvases),
    )


@bp.route("/api/canvas-health")
def canvas_health_api():
    """JSON API — consumed by IQE and external monitors."""
    import json
    from flask import Response
    from tools.canvas_health.health_data import get_canvas_health
    return Response(json.dumps({"canvases": get_canvas_health()}), mimetype="application/json")
