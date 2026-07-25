# CUI // SP-CTI — Twin Observatory constants (twx-obs-01)
"""Display constants for the Twin Observatory page."""
from __future__ import annotations

CANVAS_KEY = "twin_observatory"
DISPLAY_NAME = "Twin Observatory"
URL_PREFIX = "/twin-observatory"

# Canvas keys → the dashboard route each twin's canvas page lives at, for the
# grid's click-through column. Best-effort; unknown keys fall back to "#".
TWIN_CANVAS_ROUTES: dict[str, str] = {
    "ndc": "/network",
    "pdc": "/pipeline",
    "bdc": "/boundary",
    "sdc": "/security",
    "ddc": "/data-canvas",
    "odc": "/observability",
    "idc": "/infrastructure",
    "qdc": "/quality",
    "aadc": "/agentic-ai",
    "aimc": "/aiml",
    "mission_canvas": "/mission",
}

# Verdict → badge colour (light/dark friendly hex).
VERDICT_COLORS: dict[str, str] = {
    "pass": "#2e7d32",
    "warn": "#b26a00",
    "fail": "#c62828",
    "unknown": "#5a6b8c",
}

# IQE collections exposed by this page (registry-driven).
IQE_COLLECTIONS = ["twin_observatory.twins", "twin_observatory.events"]
