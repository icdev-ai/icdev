# CUI // SP-CTI
"""Canvas registry — single source of truth for canvas-level metadata.

Provides flow_noun and display_name per canvas type so downstream modules
never hardcode canvas-specific strings.
"""

from __future__ import annotations

CANVAS_FLOW_NOUNS: dict[str, str] = {
    "ndc": "traffic flow",
    "idc": "data flow",
    "sdc": "threat path",
    "bdc": "boundary crossing",
    "pdc": "pipeline run",
    "odc": "trace",
    "ddc": "data lineage",
    "qdc": "test run",
    "mdc": "migration wave",
}

CANVAS_DISPLAY_NAMES: dict[str, str] = {
    "ndc": "Network Design Canvas",
    "idc": "Infrastructure Design Canvas",
    "sdc": "Security Design Canvas",
    "bdc": "Boundary Design Canvas",
    "pdc": "Pipeline Design Canvas",
    "odc": "Observability Design Canvas",
    "ddc": "Data Design Canvas",
    "qdc": "Quality Design Canvas",
    "mdc": "Migration Design Canvas",
}

_FALLBACK_FLOW_NOUN = "flow"


def get_flow_noun(canvas_type: str) -> str:
    """Return the domain flow noun for a canvas type (e.g. 'ndc' -> 'traffic flow')."""
    return CANVAS_FLOW_NOUNS.get(canvas_type.lower(), _FALLBACK_FLOW_NOUN)


def get_display_name(canvas_type: str) -> str:
    """Return the human-readable canvas name."""
    return CANVAS_DISPLAY_NAMES.get(canvas_type.lower(), canvas_type.upper())


def valid_canvas_types() -> list[str]:
    """Return all registered canvas type keys."""
    return list(CANVAS_FLOW_NOUNS.keys())
