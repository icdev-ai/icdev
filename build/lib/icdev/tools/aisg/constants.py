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


# ── Ontology Mapping (AISG -> ICDEV AI-ethics ontology) ────────────────────
AISG_ONTOLOGY_MAP: dict[str, str] = {
    "self_heal": "https://icdev.dev/ontology/ai-ethics#Action.Self.Heal",
    "compliance_check": "https://icdev.dev/ontology/ai-ethics#Action.Compliance.Check",
    "security_scan": "https://icdev.dev/ontology/ai-ethics#Action.Security.Scan",
    "test_run": "https://icdev.dev/ontology/ai-ethics#Action.Test.Run",
    "evidence_collect": "https://icdev.dev/ontology/ai-ethics#Action.Evidence.Collect",
    "pattern_deploy": "https://icdev.dev/ontology/ai-ethics#Action.Pattern.Deploy",
    "fine_tune_eval": "https://icdev.dev/ontology/ai-ethics#Action.Fine.Tune.Eval",
    "genesis_reflex": "https://icdev.dev/ontology/ai-ethics#Action.Genesis.Reflex",
    "nist_ai_rmf_measure": "https://icdev.dev/ontology/ai-ethics#NISTAIRMF.Measure",
    "nist_ai_rmf_manage": "https://icdev.dev/ontology/ai-ethics#NISTAIRMF.Manage",
    "nist_ai_rmf_map": "https://icdev.dev/ontology/ai-ethics#NISTAIRMF.Map",
    "nist_ai_rmf_govern": "https://icdev.dev/ontology/ai-ethics#NISTAIRMF.Govern",
    "ai_ethics_fairness": "https://icdev.dev/ontology/ai-ethics#Ethics.Fairness",
    "ai_ethics_transparency": "https://icdev.dev/ontology/ai-ethics#Ethics.Transparency",
    "ai_ethics_accountability": "https://icdev.dev/ontology/ai-ethics#Ethics.Accountability",
    "ai_ethics_privacy": "https://icdev.dev/ontology/ai-ethics#Ethics.Privacy",
    "ai_ethics_safety": "https://icdev.dev/ontology/ai-ethics#Ethics.Safety",
    "ai_ethics_reliability": "https://icdev.dev/ontology/ai-ethics#Ethics.Reliability",
}
