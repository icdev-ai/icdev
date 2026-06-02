# CUI // SP-CTI
"""User Pillar Orchestrator — ZIG User Pillar deployment driver.

Deploys the user-pillar capabilities (MFA, PAM, continuous risk scoring,
identity governance + federation) and triggers a ZIG re-assessment so the
user-pillar maturity score reflects the deployed identity controls.

Capabilities deployed:
    p1-01  MFA — privileged/admin           (mfa_manager)
    p1-02  MFA — standard users             (mfa_manager)
    p1-03  Privileged Access Management     (pam_manager)
    p2-02  Continuous user risk scoring     (user_risk_engine)
    p2-05  Behavioral analytics auth        (user_risk_engine)
    p2-01  Federated identity               (identity_governance)
    p2-04  Access certification campaigns   (identity_governance)
    p2-06  Entitlement analytics + drift    (identity_governance)
"""
from __future__ import annotations

from typing import Any

from tools.security_canvas import (
    mfa_manager as mfa,
    pam_manager as pam,
    user_risk_engine as risk,
    identity_governance as iga,
)

# Representative account roster (mix of classes)
DEFAULT_ACCOUNTS = [
    {"username": "admin-icdev", "account_class": "admin", "authenticator": "fido2"},
    {"username": "domain-admin", "account_class": "privileged", "authenticator": "piv_cac"},
    {"username": "analyst-01", "account_class": "standard", "authenticator": "totp"},
    {"username": "operator-01", "account_class": "standard", "authenticator": "platform_bio"},
    {"username": "svc-icdev", "account_class": "service", "authenticator": "fido2"},
]


def deploy_all(accounts: list[dict] | None = None) -> dict[str, Any]:
    """Deploy all user-pillar capabilities and re-assess ZIG."""
    roster = accounts or DEFAULT_ACCOUNTS
    usernames = [a["username"] for a in roster]
    results: dict[str, Any] = {}

    # 1. MFA enrollment (privileged + standard)
    results["mfa"] = mfa.enroll_fleet(roster)

    # 2. Privileged Access Management
    results["pam"] = pam.deploy_pam()

    # 3. Continuous user risk scoring + behavioral analytics
    results["risk"] = risk.deploy_risk_engine(usernames)

    # 4. Identity governance: federation + certification + entitlement analytics
    results["governance"] = iga.deploy_identity_governance()

    # 5. Re-assess ZIG
    from tools.security_canvas.zig_assessor import run_zig_assessment
    assessment = run_zig_assessment()
    user_pillar = next(
        (p for p in assessment["pillar_scores"] if p["slug"] == "user"), None
    )
    results["assessment"] = {
        "aggregate_score": round(assessment["aggregate"]["score"] * 100, 1),
        "user_pillar_score": round(user_pillar["score"] * 100, 1) if user_pillar else None,
        "user_complete_activities": user_pillar["complete_activities"] if user_pillar else None,
        "fy2027_readiness": assessment["aggregate"].get("fy2027_readiness_pct"),
    }
    return results


if __name__ == "__main__":
    import json
    out = deploy_all()
    print(json.dumps(out["assessment"], indent=2))
