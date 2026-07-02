# CUI // SP-CTI
"""Info Operations Canvas — shared constants."""
from __future__ import annotations

CANVAS_KEY = "iop"
DISPLAY_NAME = "Information Operations"
FEATURE_FLAG = "ICDEV_IOP_ENABLED"
URL_PREFIX = "/info-ops"
MIN_IL = "IL4"

ALLOWED_ROLES = {"developer", "analyst", "admin", "superadmin"}

OBJECT_TYPES = (
    "narrative",
    "influence_op",
    "media_campaign",
    "attribution",
    "network",
    "incident",
)

SEVERITY_LEVELS = ("low", "medium", "high", "critical")
