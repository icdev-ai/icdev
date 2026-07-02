# CUI // SP-CTI
"""Constants for the BI Dashboard Canvas."""
from __future__ import annotations

CANVAS_KEY = "bi_dashboard"
DISPLAY_NAME = "BI Studio"
ENV_FLAG = "ICDEV_BI_DASHBOARD_ENABLED"
URL_PREFIX = "/bi_dashboard"

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_UPLOAD_EXTENSIONS = (".csv", ".json", ".xlsx")
REJECTED_UPLOAD_EXTENSIONS = (".xlsm",)  # macro-enabled — never parsed

DEFAULT_TILE_WIDTH = 6  # of 12 grid columns
