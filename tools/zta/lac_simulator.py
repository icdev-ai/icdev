"""ZTA LAC (Location-based Access Control) Simulator — OPA/Python ABAC policy evaluator."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from datetime import datetime, timezone

from tools.zta.constants import CLEARANCE_ORDER, ECI_RESTRICTIONS, LAC_DECISION


@dataclass
class PrincipalContext:
    citizenship: str
    clearance_level: str
    cois: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)


@dataclass
class ResourceMetadata:
    resource_id: str
    is_eci: bool = False
    cois: list[str] = field(default_factory=list)
    classification: str = "CUI"
    ownership: str = "US"


def _clearance_sufficient(principal_level: str, resource_level: str) -> bool:
    try:
        return CLEARANCE_ORDER.index(principal_level) >= CLEARANCE_ORDER.index(resource_level)
    except ValueError:
        return False


def _has_matching_coi(principal_cois: list[str], resource_cois: list[str]) -> bool:
    return bool(set(principal_cois) & set(resource_cois))


def _is_break_glass(principal: PrincipalContext) -> bool:
    return ECI_RESTRICTIONS["break_glass"]["role_required"] in principal.roles


def simulate_access(
    principal: PrincipalContext,
    resource: ResourceMetadata,
    action: str = "read",
    environment: dict[str, Any] | None = None,
    scenario_name: str = "ad_hoc",
) -> dict[str, Any]:
    """Evaluate access policy. Returns {decision, deny_reasons, audit_trail, scenario_name}."""
    deny_reasons: list[str] = []

    # COI compartmentalization check
    if resource.cois and not _has_matching_coi(principal.cois, resource.cois):
        deny_reasons.append(
            f"Principal lacks required COI membership. "
            f"Resource requires one of {resource.cois}; principal holds {principal.cois}."
        )

    # ECI export control check
    if resource.is_eci:
        if principal.citizenship not in ECI_RESTRICTIONS["citizenship_allowed"]:
            if not _is_break_glass(principal):
                deny_reasons.append(
                    f"ECI export control violation: citizenship '{principal.citizenship}' "
                    f"is not permitted to access ECI resources. US citizenship required."
                )
            # break-glass overrides deny but adds mandatory audit
        elif _is_break_glass(principal):
            # US + break-glass = permit but with audit flag
            pass

    # Clearance sufficiency
    if not _clearance_sufficient(principal.clearance_level, resource.classification):
        deny_reasons.append(
            f"Insufficient clearance: principal holds '{principal.clearance_level}', "
            f"resource requires '{resource.classification}'."
        )

    decision = LAC_DECISION["DENY"] if deny_reasons else LAC_DECISION["PERMIT"]
    break_glass_activated = _is_break_glass(principal) and resource.is_eci and not deny_reasons

    audit_trail = {
        "scenario_name": scenario_name,
        "decision": decision,
        "deny_reasons": deny_reasons,
        "principal": {
            "citizenship": principal.citizenship,
            "clearance_level": principal.clearance_level,
            "cois": principal.cois,
            "roles": principal.roles,
        },
        "resource": {
            "resource_id": resource.resource_id,
            "is_eci": resource.is_eci,
            "cois": resource.cois,
            "classification": resource.classification,
        },
        "action": action,
        "environment": environment or {},
        "break_glass_activated": break_glass_activated,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "decision": decision,
        "deny_reasons": deny_reasons,
        "audit_trail": audit_trail,
        "scenario_name": scenario_name,
        "break_glass_activated": break_glass_activated,
    }
