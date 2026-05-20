# CUI // SP-CTI
DSOC_FEATURE_FLAG = "ICDEV_DSOC_ENABLED"

FLOWSPEC_ACTIONS = ["rate-limit", "drop", "redirect", "mark", "sample"]
RTBH_TRIGGER_REASONS = [
    "volumetric_attack", "syn_flood", "udp_flood", "icmp_flood",
    "amplification", "spoofed_traffic", "manual", "policy",
]
THREAT_TYPES = [
    "botnet_c2", "scanner", "amplifier", "spoofed_source",
    "known_attacker", "tor_exit", "vpn_provider", "other",
]
MITIGATION_TYPES = ["rtbh", "flowspec", "scrubbing", "acl", "hybrid"]

# RFC 5635 null-route nexthop for RTBH
RTBH_NEXTHOP_DEFAULT = "192.0.2.1"
# RFC 7999 BLACKHOLE community
RTBH_COMMUNITY_DEFAULT = "65535:666"

# BGP communities for flowspec signaling
FLOWSPEC_RATE_COMMUNITY = "65535:100"
FLOWSPEC_DROP_COMMUNITY = "65535:0"

SCRUBBING_UTILIZATION_WARN_PCT = 70.0
SCRUBBING_UTILIZATION_CRITICAL_PCT = 85.0

INTENT_RULES = [
    {"pattern": r"flowspec|flow spec|bgp flow", "collection": "dsoc.flowspec_rules"},
    {"pattern": r"rtbh|blackhole|black.?hole", "collection": "dsoc.rtbh_entries"},
    {"pattern": r"scrubbing|scrub center|mitigation center", "collection": "dsoc.scrubbing_centers"},
    {"pattern": r"threat|attack(er|ing)?|ddos|dos", "collection": "dsoc.threats"},
    {"pattern": r"mitigation|mitigat", "collection": "dsoc.mitigations"},
]
