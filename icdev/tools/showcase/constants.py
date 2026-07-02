# CUI // SP-CTI
"""Demo Runner Canvas — shared constants."""
from __future__ import annotations

CANVAS_KEY = "demo_runner"
DISPLAY_NAME = "Demo Runner"
FEATURE_FLAG = "ICDEV_DEMO_RUNNER_ENABLED"
URL_PREFIX = "/demo"
MIN_IL = "IL4"

ALLOWED_ROLES = {"developer", "analyst", "admin", "superadmin"}

DEMO_CATEGORIES = (
    "cybersecurity",
    "govtech",
    "modernization",
    "ml_ai",
    "compliance",
    "analytics",
)

DEMO_STATUS = ("draft", "active", "archived")
