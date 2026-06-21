# CUI // SP-CTI
"""Network Pillar Orchestrator — ZIG Network Pillar deployment driver.

Deploys the network-pillar capabilities (macro + micro segmentation, lateral
movement detection/containment, SDN policy enforcement + dynamic adjustment)
then triggers a ZIG re-assessment.

Capabilities deployed:
    p1-16  macro-segmentation               (network_segmentation)
    p2-13  workload micro-segmentation       (network_segmentation)
    p2-14  identity-based micro-segmentation (network_segmentation)
    p2-17  lateral movement detection        (lateral_movement_detector)
    p2-16  SDN policy enforcement            (sdn_policy_engine)
    p2-18  dynamic risk-based net policy     (sdn_policy_engine)
"""
from __future__ import annotations

from typing import Any

from tools.security_canvas import (
    network_segmentation as seg,
    lateral_movement_detector as lmd,
    sdn_policy_engine as sdn,
)


def deploy_all() -> dict[str, Any]:
    """Deploy all network-pillar capabilities and re-assess ZIG."""
    results: dict[str, Any] = {}

    # 1. Segmentation (macro + workload-micro + identity-micro) — establishes the fabric
    results["segmentation"] = seg.deploy_segmentation()

    # 2. Lateral movement detection + containment (consumes segmentation identities)
    results["lateral_movement"] = lmd.deploy_lateral_detection()

    # 3. SDN policy enforcement + dynamic risk-based adjustment (reads quarantine state)
    results["sdn"] = sdn.deploy_sdn()

    # 4. Re-assess ZIG
    from tools.security_canvas.zig_assessor import run_zig_assessment
    assessment = run_zig_assessment()
    net_pillar = next(
        (p for p in assessment["pillar_scores"] if p["slug"] == "network"), None
    )
    results["assessment"] = {
        "aggregate_score": round(assessment["aggregate"]["score"] * 100, 1),
        "network_pillar_score": round(net_pillar["score"] * 100, 1) if net_pillar else None,
        "network_complete_activities": net_pillar["complete_activities"] if net_pillar else None,
        "maturity_level": assessment["aggregate"].get("maturity_level"),
        "fy2027_readiness": assessment["aggregate"].get("fy2027_readiness_pct"),
    }
    return results


if __name__ == "__main__":
    import json
    out = deploy_all()
    print(json.dumps(out["assessment"], indent=2))
