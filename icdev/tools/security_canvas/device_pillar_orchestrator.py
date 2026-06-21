# CUI // SP-CTI
"""Device Pillar Orchestrator — ZIG Device Pillar deployment driver.

Deploys all six device-pillar capabilities in dependency order, seeds a
representative managed fleet, and triggers a ZIG re-assessment so the
device-pillar maturity score reflects the newly-deployed controls.

Capabilities deployed:
    p1-09  device compliance scanning   (device_compliance_scanner)
    p1-10  network access control        (nac_enforcer)
    p1-11  device health attestation     (device_attestation_engine)
    p1-12  ZTNA (VPN replacement)        (ztna_gateway)
    p1-13  MDM/UEM enrollment            (mdm_enrollment_manager)
    p1-15  EDR deployment                (edr_deployment_controller)
"""
from __future__ import annotations

from typing import Any

from tools.security_canvas import (
    device_compliance_scanner as dcs,
    nac_enforcer as nac,
    device_attestation_engine as att,
    ztna_gateway as ztna,
    mdm_enrollment_manager as mdm,
    edr_deployment_controller as edr,
)

# A representative managed fleet (mix of OS platforms)
DEFAULT_FLEET = [
    {"hostname": "icdev-wks-01", "os_platform": "windows"},
    {"hostname": "icdev-wks-02", "os_platform": "windows"},
    {"hostname": "icdev-srv-01", "os_platform": "linux"},
    {"hostname": "icdev-srv-02", "os_platform": "linux"},
    {"hostname": "icdev-mac-01", "os_platform": "macos"},
    {"hostname": "icdev-vdi-01", "os_platform": "windows"},
]


def deploy_all(fleet: list[dict] | None = None) -> dict[str, Any]:
    """Deploy all six device-pillar capabilities and re-assess ZIG.

    Order matters: MDM enrollment + EDR + compliance scan populate the
    device registry, then attestation/NAC/ZTNA consume that posture data.
    """
    devices = fleet or DEFAULT_FLEET
    hostnames = [d["hostname"] for d in devices]
    results: dict[str, Any] = {}

    # 1. MDM/UEM enrollment — establishes the managed-device baseline
    results["mdm"] = mdm.enroll_fleet(devices)

    # 2. EDR deployment — sensors on every endpoint
    results["edr"] = edr.deploy_fleet_edr(hostnames)

    # 3. Compliance scanning — CIS + STIG per device
    results["compliance"] = dcs.run_fleet_scan(hostnames)

    # 4. Device health attestation — generate tokens from posture
    attestations = [att.generate_attestation(h) for h in hostnames]
    att.deploy_attestation_engine()
    results["attestation"] = {
        "issued": len(attestations),
        "trusted": sum(1 for a in attestations if a["verdict"] == "trusted"),
    }

    # 5. NAC enforcement — quarantine unknown, allow compliant
    for h in hostnames:
        nac.evaluate_access(
            mac_address=f"02:00:{hash(h) & 0xff:02x}:00:00:01",
            hostname=h,
            device_id=__import__("hashlib").sha256(h.encode()).hexdigest()[:16],
        )
    results["nac"] = nac.deploy_nac_policy()

    # 6. ZTNA gateway — replace VPN with per-app brokering
    results["ztna"] = ztna.deploy_ztna_gateway()

    # 7. Re-assess ZIG to recompute pillar scores
    from tools.security_canvas.zig_assessor import run_zig_assessment
    assessment = run_zig_assessment()
    device_pillar = next(
        (p for p in assessment["pillar_scores"] if p["slug"] == "device"), None
    )
    results["assessment"] = {
        "aggregate_score": round(assessment["aggregate"]["score"] * 100, 1),
        "device_pillar_score": round(device_pillar["score"] * 100, 1) if device_pillar else None,
        "device_complete_activities": device_pillar["complete_activities"] if device_pillar else None,
        "fy2027_readiness": assessment["aggregate"].get("fy2027_readiness_pct"),
    }

    return results


if __name__ == "__main__":
    import json
    out = deploy_all()
    print(json.dumps(out["assessment"], indent=2))
