# CUI // SP-CTI
"""Team Composition Validator — validates workforce team structure.

Validates squad-based team compositions, role distributions, and totals.
Used by workflow teams, deployment profiles, and proposal planning.

Usage:
    from tools.workforce.team_composition import TeamComposition, validate_composition
    comp = TeamComposition(squads=[Squad(role="engineer", count=8)],
                           specialists=[Role(name="DevSecOps", count=1),
                                        Role(name="ISSO", count=1)])
    result = validate_composition(comp)
    assert result.valid
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Role:
    name: str
    count: int


@dataclass
class Squad:
    role: str
    count: int
    name: str | None = None


@dataclass
class TeamComposition:
    squads: list[Squad] = field(default_factory=list)
    specialists: list[Role] = field(default_factory=list)

    @property
    def total_members(self) -> int:
        squad_total = sum(s.count for s in self.squads)
        specialist_total = sum(r.count for r in self.specialists)
        return squad_total + specialist_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "squads": [
                {"role": s.role, "count": s.count, "name": s.name}
                for s in self.squads
            ],
            "specialists": [
                {"name": r.name, "count": r.count}
                for r in self.specialists
            ],
            "total_members": self.total_members,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class ValidationResult:
    valid: bool
    expected_total: int | None = None
    actual_total: int | None = None
    errors: list[str] = field(default_factory=list)


def validate_composition(
    comp: TeamComposition, *, expected_total: int | None = None
) -> ValidationResult:
    """Validate a team composition.

    Checks:
      - All counts are positive
      - Total matches expected_total if provided
      - At least one squad or specialist is defined
    """
    errors: list[str] = []

    for idx, squad in enumerate(comp.squads):
        if squad.count <= 0:
            errors.append(f"Squad {idx + 1} count must be positive, got {squad.count}")

    for idx, role in enumerate(comp.specialists):
        if role.count <= 0:
            errors.append(
                f"Specialist role '{role.name}' count must be positive, got {role.count}"
            )

    if not comp.squads and not comp.specialists:
        errors.append("Team composition must include at least one squad or specialist")

    actual_total = comp.total_members
    if expected_total is not None and actual_total != expected_total:
        errors.append(
            f"Total team size mismatch: expected {expected_total}, got {actual_total}"
        )

    return ValidationResult(
        valid=len(errors) == 0,
        expected_total=expected_total,
        actual_total=actual_total,
        errors=errors,
    )


def default_engineering_team() -> TeamComposition:
    """Return the standard ICDEV engineering team composition.

    2 squads of 8 engineers each (16 total),
    1 DevSecOps engineer,
    1 ISSO.
    Total team size 18 members.
    """
    return TeamComposition(
        squads=[
            Squad(role="engineer", count=8, name="Squad Alpha"),
            Squad(role="engineer", count=8, name="Squad Bravo"),
        ],
        specialists=[
            Role(name="DevSecOps", count=1),
            Role(name="ISSO", count=1),
        ],
    )


def data_mesh_conflict_monitoring_team() -> TeamComposition:
    """Return the dedicated data-mesh conflict-monitoring team composition.

    2 data engineers,
    2 ML specialists,
    1 security analyst.
    Total team size 5 experts.
    """
    return TeamComposition(
        squads=[
            Squad(role="data_engineer", count=2, name="Data Mesh Ops"),
            Squad(role="ml_specialist", count=2, name="ML Conflict Monitoring"),
        ],
        specialists=[
            Role(name="security_analyst", count=1),
        ],
    )
