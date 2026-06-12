# CUI // SP-CTI
# Classification: CUI — Controlled Unclassified Information
"""IDC IaC Twin Phase 1 — Pre-Apply Compliance Gate.

Runs IDC compliance checks against a `terraform plan -json` payload
before the plan is applied.  Blocks on CAT1 violations.

Usage:
    from tools.infra_canvas.pre_apply_gate import check_plan
    result = check_plan(plan_json_dict)
    if not result["passed"]:
        print(result["violations"])

NIST 800-53: CM-3 (Config Change Control), SI-3 (Malicious Code Protection),
             SA-11 (Developer Testing and Evaluation)
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.infra_canvas.terraform_show_importer import import_terraform_plan
from tools.infra_canvas.infra_engine import assess_infra_design


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_snapshot_id() -> str:
    """Generate a unique snapshot ID for this gate run."""
    return "gate-" + uuid.uuid4().hex[:12]


def _gate_passed(violations: list[dict]) -> bool:
    """Gate passes only if there are no CAT1 violations."""
    cat1 = [v for v in violations if v.get("severity") == "CAT1"]
    return len(cat1) == 0


def check_plan(plan_data: dict) -> dict:
    """Run pre-apply compliance check against a terraform plan.

    Args:
        plan_data: Parsed JSON from `terraform plan -json` or
                   `terraform show -json <planfile>`.

    Returns:
        {
            "snapshot_id": str,
            "assessed_at": str,          # ISO timestamp
            "graph": dict,               # IDC graph {"nodes": [...], "edges": [...]}
            "passed": bool,              # False if any CAT1 violation
            "violations": list[dict],    # Full finding list from infra_engine
            "score": float,              # 0-100 compliance score
        }
    """
    # 1. Convert plan to IDC graph
    graph = import_terraform_plan(plan_data)

    # 2. Assess against IDC compliance rules
    assessment = assess_infra_design(graph)

    violations: list[dict[str, Any]] = assessment.get("findings", [])
    score: float = assessment.get("score", 100.0)
    snapshot_id = _generate_snapshot_id()

    return {
        "snapshot_id": snapshot_id,
        "assessed_at": _utcnow_iso(),
        "graph": graph,
        "passed": _gate_passed(violations),
        "violations": violations,
        "score": score,
    }
