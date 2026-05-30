# CUI // SP-CTI
"""PMC constants — peer types, RPKI statuses, IQE routing rules."""

from __future__ import annotations

PMC_FEATURE_FLAG = "ICDEV_PMC_ENABLED"

PEER_TYPES = {
    "public": {"label": "Public", "color": "#22c55e"},
    "private": {"label": "Private", "color": "#3b82f6"},
    "transit": {"label": "Transit", "color": "#f59e0b"},
    "customer": {"label": "Customer", "color": "#8b5cf6"},
    "route-server": {"label": "Route Server", "color": "#6b7280"},
}

PEER_POLICIES = {
    "open": {"label": "Open", "color": "#22c55e", "description": "Peer with anyone at shared IXs"},
    "selective": {"label": "Selective", "color": "#f59e0b", "description": "Peer on case-by-case basis"},
    "no": {"label": "No Peering", "color": "#ef4444", "description": "Do not peer"},
}

PEER_STATUSES = {
    "evaluation": {"label": "Evaluation", "color": "#6b7280"},
    "requested": {"label": "Requested", "color": "#f59e0b"},
    "active": {"label": "Active", "color": "#22c55e"},
    "suspended": {"label": "Suspended", "color": "#ef4444"},
    "closed": {"label": "Closed", "color": "#374151"},
}

RPKI_STATUSES = {
    "valid": {"label": "Valid", "color": "#22c55e", "icon": "✓"},
    "invalid": {"label": "Invalid", "color": "#ef4444", "icon": "✗"},
    "not-found": {"label": "Not Found", "color": "#f59e0b", "icon": "?"},
    "unknown": {"label": "Unknown", "color": "#6b7280", "icon": "–"},
}

RIRS = ["ARIN", "RIPE", "APNIC", "LACNIC", "AFRINIC", "UNKNOWN"]

# Decision engine dimension weights (must sum to 1.0)
PEERING_SCORE_WEIGHTS = {
    "traffic_ratio": 0.25,     # Balanced traffic exchange
    "prefix_count": 0.15,      # Number of advertised prefixes
    "rpki_validity": 0.20,     # RPKI ROA coverage
    "irr_registration": 0.15,  # IRR route object hygiene
    "noc_responsiveness": 0.10,  # NOC email / 24x7 contact
    "ix_presence": 0.15,       # Shared IX memberships
}

PEERING_THRESHOLDS = {
    "open_score": 0.75,        # Score >= this → recommend "open"
    "selective_score": 0.45,   # Score >= this → recommend "selective"
    # below selective_score → recommend "no"
    "max_prefixes_v4_warn": 500_000,  # Warn if peer advertises > this
    "max_prefixes_v6_warn": 100_000,
    "rpki_invalid_block": True,  # Block sessions with RPKI-invalid prefixes
}

# PeeringDB cache TTL
PEERINGDB_CACHE_TTL_HOURS = 24

# BGP OS types supported by config generator
BGP_OS_TYPES = ["ios_xr", "junos", "eos", "nokia_sros", "frr", "bird2"]

INTENT_RULES = [
    {"pattern": r"(peer|asn|bgp|ix|internet exchange)", "canvas": "pmc", "collection": "pmc.peers"},
    {"pattern": r"(rpki|roa|route origin)", "canvas": "pmc", "collection": "pmc.prefixes"},
    {"pattern": r"(prefix|route object|irr|rpsl)", "canvas": "pmc", "collection": "pmc.prefixes"},
    {"pattern": r"(peering request|peer request)", "canvas": "pmc", "collection": "pmc.peering_requests"},
    {"pattern": r"(route policy|import policy|export policy|bgp policy)", "canvas": "pmc", "collection": "pmc.route_policies"},
    {"pattern": r"(internet exchange|ix member)", "canvas": "pmc", "collection": "pmc.ix_memberships"},
]

PMC_ROUTES = {
    "index": "/pmc",
    "peers": "/pmc/peers",
    "peer_detail": "/pmc/peers/<peer_id>",
    "ix": "/pmc/ix",
    "rpki": "/pmc/rpki",
    "policies": "/pmc/policies",
    "requests": "/pmc/requests",
}
