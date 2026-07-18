# CUI // SP-CTI
"""NOC Operations Canvas (NOCC) constants."""

from __future__ import annotations

NOCC_FEATURE_FLAG = "ICDEV_NOCC_ENABLED"

ALARM_SEVERITIES = ["critical", "major", "minor", "warning", "info"]

ALARM_TYPES = [
    "interface", "bgp", "circuit", "power", "optical",
    "cpu", "memory", "temperature", "security", "other",
]

ALARM_SOURCES = [
    "solarwinds", "librenms", "snmp-trap", "syslog",
    "nagios", "zabbix", "prtg", "custom",
]

# MOP provenance — source of truth for the noc_mops.generated_by CHECK enum
# (see tools/db/schema/pg_consolidated.sql + migration 278). 'ai' = LLM-authored
# steps; 'ai_template' = LLM unavailable, deterministic template fallback (the
# common CI path); 'manual' = human-authored.
MOP_GENERATED_BY = ["manual", "ai", "ai_template"]

INCIDENT_SEVERITIES = {
    "p1": {
        "label": "P1 — Critical",
        "sla_response_min": 15,
        "sla_resolve_min": 240,
        "color": "#ef4444",
    },
    "p2": {
        "label": "P2 — Major",
        "sla_response_min": 30,
        "sla_resolve_min": 480,
        "color": "#f97316",
    },
    "p3": {
        "label": "P3 — Minor",
        "sla_response_min": 120,
        "sla_resolve_min": 1440,
        "color": "#eab308",
    },
    "p4": {
        "label": "P4 — Info",
        "sla_response_min": 480,
        "sla_resolve_min": 4320,
        "color": "#3b82f6",
    },
}

INCIDENT_STATUSES = ["open", "acknowledged", "investigating", "resolved", "closed"]
RFC_CHANGE_TYPES = ["emergency", "standard", "normal"]
RFC_STATUSES = ["draft", "submitted", "approved", "executing", "completed", "rejected"]
RISK_LEVELS = ["low", "medium", "high"]
MAINTENANCE_STATUSES = ["scheduled", "in-progress", "completed", "cancelled"]
MAINTENANCE_IMPACT_SCOPES = ["single-circuit", "multi-circuit", "site", "region", "global"]
SLA_TYPES = ["uptime", "latency_ms", "jitter_ms", "packet_loss_pct", "mttr_min"]

ALARM_STORM_THRESHOLD = 5      # alarms on same device within window = storm
ALARM_STORM_WINDOW_MIN = 15    # sliding window in minutes

NOCC_ROUTES = {
    "overview":    "/noc",
    "alarms":      "/noc/alarms",
    "incidents":   "/noc/incidents",
    "rfcs":        "/noc/rfcs",
    "mops":        "/noc/mops",
    "maintenance": "/noc/maintenance",
    "sla":         "/noc/sla",
}

# IQE intent rules — maps keywords to collection names
INTENT_RULES = [
    {"keywords": ["alarm", "alert", "fault", "trap"],         "collection": "noc.alarms"},
    {"keywords": ["incident", "outage", "p1", "p2", "breach"],"collection": "noc.incidents"},
    {"keywords": ["rfc", "change", "request", "change request"],"collection": "noc.rfcs"},
    {"keywords": ["mop", "procedure", "steps", "runbook"],    "collection": "noc.mops"},
    {"keywords": ["maintenance", "window", "scheduled", "downtime"], "collection": "noc.maintenance_windows"},
    {"keywords": ["sla", "uptime", "availability", "credit"], "collection": "noc.sla_records"},
]
