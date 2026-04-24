"""Canvas-agnostic color/style constants for Digital Program Twin diagrams."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Zone fill colors — infrastructure provider zones
# ---------------------------------------------------------------------------

AWS_ZONE = {"fill": "#FF9900", "fill_opacity": 0.08, "stroke": "#FF9900", "stroke_width": 2}
AZURE_ZONE = {"fill": "#0078D4", "fill_opacity": 0.08, "stroke": "#0078D4", "stroke_width": 2}
BCAP_ZONE = {"fill": "#5C4EE5", "fill_opacity": 0.10, "stroke": "#5C4EE5", "stroke_width": 2}
IDPS_ZONE = {"fill": "#D83B01", "fill_opacity": 0.10, "stroke": "#D83B01", "stroke_width": 2}
BGP_ZONE = {"fill": "#107C10", "fill_opacity": 0.08, "stroke": "#107C10", "stroke_width": 2}
MEGAPORT_ZONE = {"fill": "#E4002B", "fill_opacity": 0.08, "stroke": "#E4002B", "stroke_width": 2}
PRIVATE_LINK_ZONE = {"fill": "#773ADC", "fill_opacity": 0.10, "stroke": "#773ADC", "stroke_width": 2}

# ---------------------------------------------------------------------------
# Zone fill colors — microservice canvas
# ---------------------------------------------------------------------------

MESH_ZONE = {"fill": "#0F6CBD", "fill_opacity": 0.08, "stroke": "#0F6CBD", "stroke_width": 1}
GATEWAY_ZONE = {"fill": "#CA5010", "fill_opacity": 0.08, "stroke": "#CA5010", "stroke_width": 1}
BROKER_ZONE = {"fill": "#8764B8", "fill_opacity": 0.08, "stroke": "#8764B8", "stroke_width": 1}
STORE_ZONE = {"fill": "#038387", "fill_opacity": 0.08, "stroke": "#038387", "stroke_width": 1}

# ---------------------------------------------------------------------------
# Zone fill colors — EDA (Event-Driven Architecture) canvas
# ---------------------------------------------------------------------------

EDA_PRODUCER_ZONE = {"fill": "#498205", "fill_opacity": 0.08, "stroke": "#498205", "stroke_width": 1}
EDA_CONSUMER_ZONE = {"fill": "#0078D4", "fill_opacity": 0.08, "stroke": "#0078D4", "stroke_width": 1}
EDA_BROKER_ZONE = {"fill": "#8764B8", "fill_opacity": 0.10, "stroke": "#8764B8", "stroke_width": 2}
EDA_STREAM_PROCESSOR_ZONE = {"fill": "#CA5010", "fill_opacity": 0.08, "stroke": "#CA5010", "stroke_width": 1}

# ---------------------------------------------------------------------------
# Flow palette — F1 through F6 plus overflow
# ---------------------------------------------------------------------------

FLOW_PALETTE = {
    "F1": "#2196F3",  # primary data path
    "F2": "#4CAF50",  # success / healthy
    "F3": "#FF9800",  # warning / degraded
    "F4": "#F44336",  # error / critical
    "F5": "#9C27B0",  # control plane
    "F6": "#00BCD4",  # management / out-of-band
    "F7": "#795548",  # legacy / deprecated path
    "F8": "#607D8B",  # telemetry / observability
}

# ---------------------------------------------------------------------------
# Edge semantic colors
# ---------------------------------------------------------------------------

EDGE_STYLES: dict[str, dict] = {
    "data": {"stroke": "#2196F3", "stroke_width": 2, "dashed": False},
    "control": {"stroke": "#9C27B0", "stroke_width": 2, "dashed": True},
    "auth": {"stroke": "#F44336", "stroke_width": 2, "dashed": False},
    "health": {"stroke": "#4CAF50", "stroke_width": 1, "dashed": True},
    "telemetry": {"stroke": "#607D8B", "stroke_width": 1, "dashed": True},
    "async": {"stroke": "#FF9800", "stroke_width": 2, "dashed": True},
    "sync": {"stroke": "#2196F3", "stroke_width": 2, "dashed": False},
    "replication": {"stroke": "#00BCD4", "stroke_width": 2, "dashed": False},
    "failover": {"stroke": "#F44336", "stroke_width": 2, "dashed": True},
    "peering": {"stroke": "#107C10", "stroke_width": 2, "dashed": False},
}

# ---------------------------------------------------------------------------
# Zone registry — maps (zone_key, canvas_type) → style dict
# ---------------------------------------------------------------------------

_INFRA_ZONES: dict[str, dict] = {
    "aws": AWS_ZONE,
    "azure": AZURE_ZONE,
    "bcap": BCAP_ZONE,
    "idps": IDPS_ZONE,
    "bgp": BGP_ZONE,
    "megaport": MEGAPORT_ZONE,
    "private_link": PRIVATE_LINK_ZONE,
}

_MICROSERVICE_ZONES: dict[str, dict] = {
    "mesh": MESH_ZONE,
    "gateway": GATEWAY_ZONE,
    "broker": BROKER_ZONE,
    "store": STORE_ZONE,
}

_EDA_ZONES: dict[str, dict] = {
    "producer": EDA_PRODUCER_ZONE,
    "consumer": EDA_CONSUMER_ZONE,
    "broker": EDA_BROKER_ZONE,
    "stream_processor": EDA_STREAM_PROCESSOR_ZONE,
}

_CANVAS_ZONE_MAP: dict[str, dict[str, dict]] = {
    "network": _INFRA_ZONES,
    "microservice": _MICROSERVICE_ZONES,
    "eda": _EDA_ZONES,
    # NDC/SDC/PDC share infra zones by default
    "ndc": _INFRA_ZONES,
    "sdc": _INFRA_ZONES,
    "pdc": _INFRA_ZONES,
}

_DEFAULT_NODE_STYLE: dict = {
    "fill": "#F5F5F5",
    "fill_opacity": 0.12,
    "stroke": "#9E9E9E",
    "stroke_width": 1,
}


def get_node_style(zone: str, canvas_type: str = "network") -> dict:
    """Return fill/stroke style dict for *zone* in *canvas_type*.

    Falls back to _DEFAULT_NODE_STYLE when zone or canvas_type is unknown.
    Returns a copy so callers can mutate without affecting constants.
    """
    canvas_zones = _CANVAS_ZONE_MAP.get(canvas_type.lower(), {})
    style = canvas_zones.get(zone.lower(), _DEFAULT_NODE_STYLE)
    return dict(style)


def get_edge_style(semantic: str, canvas_type: str = "network") -> dict:  # noqa: ARG001
    """Return stroke style dict for an edge with *semantic* meaning.

    *canvas_type* is accepted for API symmetry but edge semantics are
    canvas-agnostic; the parameter is reserved for future per-canvas overrides.
    Falls back to the 'data' edge style when *semantic* is unknown.
    Returns a copy so callers can mutate without affecting constants.
    """
    style = EDGE_STYLES.get(semantic.lower(), EDGE_STYLES["data"])
    return dict(style)


def get_flow_color(flow_id: str) -> str:
    """Return hex color for flow label *flow_id* (e.g. 'F1', 'f3').

    Falls back to F1 color when the ID is not found.
    """
    return FLOW_PALETTE.get(flow_id.upper(), FLOW_PALETTE["F1"])
