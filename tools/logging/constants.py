# CUI // SP-CTI
"""Constants for the EQO centralized-logging query surface (eqo-log-04).

Shared by ``tools.logging.log_query`` (CLI + ``query_logs``), the ``/logs``
dashboard blueprint, and the IQE ``logs.entries`` adapter so the level
vocabulary and query bounds are defined in exactly one place.
"""
from __future__ import annotations

# Canonical log levels, ordered low → high severity. The query surface accepts
# any string for ``level`` (logs are free-form evidence), but the dashboard and
# CLI present this ordered set.
LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# Severity rank for stable ordering / colour mapping in the UI.
LEVEL_RANK = {lvl: i for i, lvl in enumerate(LOG_LEVELS)}

# Default and hard-cap row counts for a single query_logs / /api/logs call.
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000

# The append-only sink these queries read from (created by migration 181).
LOGS_TABLE = "centralized_logs"

# Feature flag mounting the /logs canvas blueprint (defaults on).
FEATURE_FLAG = "ICDEV_LOGS_ENABLED"
