# CUI // SP-CTI
"""AISG constants — action types and ROI time-savings rates."""
from __future__ import annotations

# Canonical list of ROI action types (mirrors DB CHECK constraint in migration 098).
ACTION_TYPES: list[str] = [
    "self_heal",
    "compliance_check",
    "security_scan",
    "test_run",
    "evidence_collect",
    "pattern_deploy",
    "fine_tune_eval",
    "genesis_reflex",
]

# Estimated minutes saved per automated action vs. manual equivalent.
ROI_RATES_MINUTES: dict[str, float] = {
    "self_heal":        30.0,   # diagnose + patch an issue manually
    "compliance_check": 45.0,   # manual NIST/FedRAMP control verification
    "security_scan":    60.0,   # manual SAST/dependency audit pass
    "test_run":         20.0,   # write + run a manual regression test
    "evidence_collect": 90.0,   # gather and package ATO evidence artifacts
    "pattern_deploy":  120.0,   # stand up and validate a reusable pattern
    "fine_tune_eval":   60.0,   # evaluate fine-tune results and select checkpoint
    "genesis_reflex":   45.0,   # autonomous research + knowledge-graph update cycle
}
