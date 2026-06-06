# [CUI // SP-CTI]
"""ICDEV™ NDC — Auto-layout for imported topology graphs.

Imported diagrams (PDF/VSDX/config-derived) often arrive with raw source
coordinates that don't survive the canvas transform — nodes pile on top
of each other or fly off the visible area. This module re-flows a graph
into a clean professional layout with three steps:

1. **Classify** each node into a hierarchy tier (edge / core / dist /
   access / endpoint / cloud / external) based on label keywords + type.
2. **Lane assignment** — one horizontal lane per tier, top-to-bottom.
3. **Within-lane spacing** — even horizontal distribution; collisions
   broken by inserting gaps; finally snap to a 20px grid.

Pure Python, no external deps. Idempotent (re-running on already-laid-out
graph yields the same result within ±1px).
"""
from __future__ import annotations

import re

# Tier order top → bottom on the canvas
_TIERS = [
    "external",   # ISP, internet, partner WAN
    "edge",       # edge-router, edge-firewall, perimeter
    "cloud",      # AWS/Azure/GCP gateways, VPC, transit-gw
    "core",       # core-router, core-switch, backbone
    "dist",       # distribution / aggregation
    "access",     # access switches, ToR
    "endpoint",   # servers, hosts, AP, IoT
    "unknown",
]

# Heuristic patterns — case-insensitive substring/regex hits
_TIER_PATTERNS: list[tuple[str, list[str]]] = [
    ("external", [
        r"\bisp\b", r"\binternet\b", r"\bpartner\b", r"\bwan-handoff\b",
        r"\bcarrier\b", r"\bmpls\b",
    ]),
    ("edge", [
        r"edge[-_ ]?(router|firewall|fw|gw)", r"perimeter", r"dmz[-_ ]?(switch|sw|fw)",
        r"\bedge\b", r"^fw-", r"-fw-", r"-fw$", r"firewall",
    ]),
    ("cloud", [
        r"\baws[-_ ]?(tgw|igw|vgw|vpc)\b", r"\bazure\b", r"\bvpc[-_ ]?\w+",
        r"\bvpn[-_ ]?(gw|gateway)\b", r"\btransit[-_ ]?gw\b", r"\bcloud\b",
    ]),
    ("core", [
        r"core[-_ ]?(router|switch|sw|rtr)", r"backbone", r"^core-", r"-core-",
    ]),
    ("dist", [
        r"dist(ribution)?[-_ ]?(switch|sw|router|rtr)", r"\bagg(regation)?\b",
        r"^dist-", r"-dist-",
    ]),
    ("access", [
        r"access[-_ ]?(switch|sw|ap|point)", r"\btor[-_ ]?(switch|sw)\b",
        r"^acc-", r"-acc-", r"\btop[- ]of[- ]rack\b",
    ]),
    ("endpoint", [
        r"\bserver\b", r"\bhost\b", r"\bvm\b", r"\bworkstation\b",
        r"\bdesktop\b", r"\bprinter\b", r"\biot\b", r"\bcamera\b",
        r"^srv-", r"-srv-", r"^web", r"^db", r"^app",
    ]),
]

# Node `type` field hints (override label-based classification when set)
_TYPE_TO_TIER = {
    "router": "core",
    "switch": "dist",
    "firewall": "edge",
    "load_balancer": "core",
    "server": "endpoint",
    "access_point": "access",
    "wan_link": "external",
    "vpn_gateway": "cloud",
    "cloud_service": "cloud",
}


def classify_tier(label: str, node_type: str = "") -> str:
    """Return one of ``_TIERS`` for a given node label + type."""
    nt = (node_type or "").lower().strip()
    if nt in _TYPE_TO_TIER:
        return _TYPE_TO_TIER[nt]

    lbl = (label or "").lower()
    for tier, patterns in _TIER_PATTERNS:
        for pat in patterns:
            if re.search(pat, lbl):
                return tier
    return "unknown"


def auto_layout(
    graph: dict,
    *,
    width: int = 1600,
    margin: int = 80,
    lane_height: int = 140,
    min_node_spacing: int = 160,
    grid: int = 20,
) -> dict:
    """Re-flow ``graph`` into a hierarchical layout.

    Mutates and returns the graph (also returns it for chaining).

    Args:
        graph: ``{nodes: [...], edges: [...]}`` — node ``x``/``y`` are
            replaced with new coordinates.
        width: Total canvas width to fit lanes into.
        margin: Outer padding from the canvas edge.
        lane_height: Vertical pixels per tier lane.
        min_node_spacing: Minimum horizontal pixels between sibling
            nodes in the same lane.
        grid: Snap final coordinates to this pixel grid (set 1 to skip).
    """
    nodes = graph.get("nodes") or []
    if not nodes:
        return graph

    # 1. Classify
    by_tier: dict[str, list[dict]] = {t: [] for t in _TIERS}
    for n in nodes:
        tier = classify_tier(n.get("label", ""), n.get("type", ""))
        by_tier[tier].append(n)

    # Drop empty tiers from vertical stacking so we don't waste rows
    active_tiers = [t for t in _TIERS if by_tier[t]]
    if not active_tiers:
        return graph

    # 2. Lane Y assignment (top → bottom in tier order)
    tier_y: dict[str, int] = {
        t: margin + i * lane_height
        for i, t in enumerate(active_tiers)
    }

    # 3. Within-lane X spread + collision break
    usable_width = max(min_node_spacing, width - 2 * margin)
    for tier in active_tiers:
        siblings = by_tier[tier]
        # Stable order: by existing label so re-runs are deterministic
        siblings.sort(key=lambda s: (s.get("label") or "").lower())
        n = len(siblings)
        if n == 1:
            siblings[0]["x"] = _snap(margin + usable_width // 2, grid)
            siblings[0]["y"] = _snap(tier_y[tier], grid)
            continue
        # Even spacing across the usable width
        step = max(min_node_spacing, usable_width // max(1, n - 1))
        for i, s in enumerate(siblings):
            s["x"] = _snap(margin + i * step, grid)
            s["y"] = _snap(tier_y[tier], grid)

    # 4. De-overlap: in case node widths exceed step, push right
    _de_overlap(nodes, min_spacing=min_node_spacing, grid=grid)

    return graph


def _snap(v: float, grid: int) -> int:
    if grid <= 1:
        return int(v)
    return int(round(v / grid) * grid)


def _de_overlap(nodes: list[dict], *, min_spacing: int, grid: int) -> None:
    """For each lane (same Y), ensure adjacent nodes are min_spacing apart."""
    by_y: dict[int, list[dict]] = {}
    for n in nodes:
        by_y.setdefault(int(n.get("y", 0)), []).append(n)
    for _y, lane in by_y.items():
        lane.sort(key=lambda s: int(s.get("x", 0)))
        for i in range(1, len(lane)):
            prev_x = int(lane[i - 1].get("x", 0))
            cur_x = int(lane[i].get("x", 0))
            if cur_x - prev_x < min_spacing:
                lane[i]["x"] = _snap(prev_x + min_spacing, grid)
