# CUI // SP-CTI
"""GovLift DoD IL4 Cloud Migration Tool — Constants.

All SQL CHECK constraint values reference these constants.
Never hardcode status/type strings in SQL; always reference these lists.
"""

from __future__ import annotations

# Workload lifecycle states
WORKLOAD_STATUSES = [
    "discovered",
    "assessed",
    "wave_assigned",
    "in_migration",
    "migrated",
    "failed",
    "rollback",
]

# Workload categories
WORKLOAD_TYPES = [
    "web_app",
    "database",
    "api_service",
    "batch_job",
    "message_queue",
    "storage",
    "network_appliance",
    "legacy_app",
]

# Migration job lifecycle
MIGRATION_STATUSES = [
    "pending",
    "pre_check",
    "running",
    "post_check",
    "completed",
    "failed",
    "rolled_back",
]

# Rollback event lifecycle
ROLLBACK_STATUSES = [
    "initiated",
    "in_progress",
    "completed",
    "failed",
    "cancelled",
]

# STIG finding severity categories (DoD CAT I/II/III)
STIG_SEVERITIES = ["cat1", "cat2", "cat3"]

# STIG check disposition values
STIG_CHECK_STATUSES = [
    "open",
    "not_a_finding",
    "not_applicable",
    "not_reviewed",
]

# Migration wave lifecycle
WAVE_STATUSES = ["planned", "ready", "in_progress", "completed", "paused"]

# Risk tiers
RISK_LEVELS = ["low", "medium", "high", "critical"]

# External integration targets
INTEGRATION_SYSTEMS = [
    "servicenow",
    "splunk",
    "aws_control_tower",
    "tenable",
    "emass",
]

# Rollback SLA
ROLLBACK_SLA_HOURS = 4

# Classification and compliance metadata
CLASSIFICATION = "CUI // SP-CTI"
IMPACT_LEVEL = "IL4"
AUDIT_RETENTION_YEARS = 7

# SQL CHECK fragments — derived from the lists above so SQL and Python stay in sync
_ws = "', '".join(WORKLOAD_STATUSES)
_wt = "', '".join(WORKLOAD_TYPES)
_ms = "', '".join(MIGRATION_STATUSES)
_ss = "', '".join(STIG_SEVERITIES)
_sc = "', '".join(STIG_CHECK_STATUSES)
_wv = "', '".join(WAVE_STATUSES)
_rl = "', '".join(RISK_LEVELS)
_is = "', '".join(INTEGRATION_SYSTEMS)
_rs = "', '".join(ROLLBACK_STATUSES)

CHECK_WORKLOAD_STATUS   = f"migration_status IN ('{_ws}')"
CHECK_WORKLOAD_TYPE     = f"workload_type IN ('{_wt}')"
CHECK_MIGRATION_STATUS  = f"status IN ('{_ms}')"
CHECK_STIG_SEVERITY     = f"severity IN ('{_ss}')"
CHECK_STIG_STATUS       = f"status IN ('{_sc}')"
CHECK_WAVE_STATUS       = f"status IN ('{_wv}')"
CHECK_RISK_LEVEL        = f"risk_level IN ('{_rl}')"
CHECK_INTEGRATION_SYS   = f"system_name IN ('{_is}')"
CHECK_ROLLBACK_STATUS   = f"status IN ('{_rs}')"
