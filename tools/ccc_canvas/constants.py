# CUI // SP-CTI
"""Circuit & Capacity Canvas — constants."""
from __future__ import annotations

CCC_FEATURE_FLAG = "ICDEV_CCC_ENABLED"

CIRCUIT_TYPES = [
    "ethernet", "wavelength", "dark_fiber",
    "oc3", "oc12", "oc48", "oc192",
    "100g", "400g", "mpls", "ip_vpn", "other",
]

CIRCUIT_STATUSES = ["active", "pending", "maintenance", "decommissioned", "test"]

CROSS_CONNECT_FACILITIES = [
    "equinix", "megaport", "zayo", "lumen", "cogent",
    "coresite", "digital_realty", "switch", "other",
]

XC_STATUSES = ["active", "pending_loa", "installing", "decommissioned", "suspended"]

LOA_STATUSES = ["draft", "submitted", "approved", "expired", "completed", "rejected"]

DWDM_STATUSES = ["operational", "degraded", "dark", "maintenance"]

# Utilization thresholds (%)
UTIL_WARN_PCT  = 70.0
UTIL_CRIT_PCT  = 85.0
UTIL_PLAN_PCT  = 60.0   # trigger capacity planning at 60%

# DWDM thresholds
OSNR_WARN_DB   = 15.0   # OSNR below this = degraded

INTENT_RULES = {
    "show active circuits":         "SELECT * FROM ccc_circuits WHERE status='active' ORDER BY carrier",
    "circuits near capacity":       f"SELECT * FROM ccc_circuits WHERE utilization_pct >= {UTIL_WARN_PCT} ORDER BY utilization_pct DESC",
    "pending cross connects":       "SELECT * FROM ccc_cross_connects WHERE status IN ('pending_loa','installing') ORDER BY created_at DESC",
    "open loa requests":            "SELECT * FROM ccc_loa_requests WHERE status IN ('draft','submitted','approved') ORDER BY created_at DESC",
    "dwdm spans":                   "SELECT * FROM ccc_dwdm_spans ORDER BY span_id",
    "circuits expiring soon":       "SELECT * FROM ccc_circuits WHERE contract_end != '' AND contract_end <= date('now','+90 days') ORDER BY contract_end",
    "capacity plans":               "SELECT cp.*, c.circuit_id, c.carrier FROM ccc_capacity_plans cp JOIN ccc_circuits c ON c.id=cp.circuit_id ORDER BY cp.plan_date DESC",
}
