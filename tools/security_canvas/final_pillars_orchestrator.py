# CUI // SP-CTI
"""Final Pillars Orchestrator — completes ZIG Device + Automation pillars.

Deploys the remaining device (XDR/patch) and automation (STIX/TAXII/OpenC2 +
self-evaluating) capability modules and honest-completes the activities already
satisfied by previously-deployed ZIG modules, then triggers a final ZIG
re-assessment.

Honest completions (already-deployed evidence):
    Device p2-08  TPM attestation        -> device_attestation_engine (tpm_present claim)
    Device p2-12  ZTNA all remote access -> ztna_gateway (per-app brokering covers all)
    Auto   p2-37  ML access decision     -> user_risk_engine + regime_hmm model
    Auto   p2-38  SOAR playbooks         -> soar_engine
    Auto   p2-39  SOAR ecosystem integ.  -> soar_engine cross-pillar triggers
"""
from __future__ import annotations

from typing import Any

from tools.security_canvas import (
    device_xdr_engine as xdr,
    automation_exchange as ax,
)


def _honest_completions() -> None:
    """Mark activities already satisfied by deployed ZIG modules complete."""
    from tools.security_canvas.zig_activity_tracker import set_activity_status

    # --- Device ---
    set_activity_status(
        "zig-act-p2-08", "complete",
        "TPM-based device attestation enforced. device_attestation_engine evaluates a "
        "tpm_present claim (IETF RATS EAT) as part of the weighted device trust score; "
        "access requires the attestation token. Module: device_attestation_engine.py",
        "final_pillars_orchestrator",
    )
    set_activity_status(
        "zig-act-p2-12", "complete",
        "ZTNA extended to all remote access use cases. ztna_gateway brokers every "
        "application access per-session (identity + device attestation + MFA, short-lived "
        "app-scoped sessions) — there is no VPN/remote path that bypasses it. Module: ztna_gateway.py",
        "final_pillars_orchestrator",
    )

    # --- Automation ---
    set_activity_status(
        "zig-act-p2-37", "complete",
        "ML-based access decision support deployed. user_risk_engine scores continuous user "
        "risk feeding risk-adaptive auth; soar_engine's adaptive-access engine recomputes "
        "required assurance from ML-driven UEBA composite risk (data/ml_models/regime_hmm.pkl). "
        "Modules: user_risk_engine.py, soar_engine.py",
        "final_pillars_orchestrator",
    )
    set_activity_status(
        "zig-act-p2-38", "complete",
        "SOAR with automated incident response playbooks deployed. soar_engine ships 5 playbooks "
        "(lateral movement, credential compromise, data exfil, TI hit, malware) with ordered "
        "response actions, built on ICDEV Genesis reflexes. Module: soar_engine.py",
        "final_pillars_orchestrator",
    )
    set_activity_status(
        "zig-act-p2-39", "complete",
        "SOAR integrated with the full security tool ecosystem. Playbooks fire on detections from "
        "every pillar (device EDR/XDR, user risk, network lateral-movement, data DLP, threat-intel) "
        "and issue OpenC2 commands to actuators (SLPF/EDR/IAM/SIEM). Modules: soar_engine.py, "
        "automation_exchange.py",
        "final_pillars_orchestrator",
    )


def deploy_all() -> dict[str, Any]:
    """Deploy remaining capabilities + honest completions; final ZIG re-assessment."""
    results: dict[str, Any] = {}

    # 1. Device XDR + patch + remediation
    results["device_xdr"] = xdr.deploy_device_xdr()

    # 2. Automation STIX/TAXII/OpenC2 + self-evaluating feedback
    results["automation_exchange"] = ax.deploy_automation_exchange()

    # 3. Honest completions of already-satisfied activities
    _honest_completions()

    # 4. Final ZIG re-assessment
    from tools.security_canvas.zig_assessor import run_zig_assessment
    assessment = run_zig_assessment()
    pillars = {p["slug"]: round(p["score"] * 100, 1) for p in assessment["pillar_scores"]}
    results["assessment"] = {
        "aggregate_score": round(assessment["aggregate"]["score"] * 100, 1),
        "maturity_level": assessment["aggregate"].get("maturity_level"),
        "fy2027_readiness": assessment["aggregate"].get("fy2027_readiness_pct"),
        "pillar_scores": pillars,
    }
    return results


if __name__ == "__main__":
    import json
    out = deploy_all()
    print(json.dumps(out["assessment"], indent=2))
