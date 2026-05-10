# CUI // SP-CTI
"""MITRE ATT&CK Enterprise technique catalog loader for the Observability Design Canvas."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / "context" / "mitre" / "enterprise.json"


@dataclass(frozen=True, order=False)
class MitreTechnique:
    id: str
    name: str
    description: str
    tactic_ids: tuple[str, ...]
    is_sub_technique: bool
    parent_id: Optional[str] = field(default=None)

    def __lt__(self, other: "MitreTechnique") -> bool:
        return self.id < other.id


def load_techniques(
    catalog_path: Path = _DEFAULT_CATALOG,
    tactic_filter: Optional[str] = None,
) -> list[MitreTechnique]:
    """Return sorted list of MitreTechnique, optionally filtered by tactic ID or short_name.

    Iterates both top-level techniques and their sub_techniques.
    Order is deterministic: ascending by technique ID (lexicographic).
    """
    with open(catalog_path, encoding="utf-8") as fh:
        data = json.load(fh)

    results: list[MitreTechnique] = []

    for tactic in data.get("tactics", []):
        tactic_id: str = tactic["id"]
        tactic_short: str = tactic.get("short_name", "")

        if tactic_filter is not None:
            if tactic_filter not in (tactic_id, tactic_short):
                continue

        for tech in tactic.get("techniques", []):
            results.append(
                MitreTechnique(
                    id=tech["id"],
                    name=tech["name"],
                    description=tech.get("description", ""),
                    tactic_ids=(tactic_id,),
                    is_sub_technique=False,
                    parent_id=None,
                )
            )
            for sub in tech.get("sub_techniques", []):
                results.append(
                    MitreTechnique(
                        id=sub["id"],
                        name=sub["name"],
                        description=sub.get("description", ""),
                        tactic_ids=(tactic_id,),
                        is_sub_technique=True,
                        parent_id=tech["id"],
                    )
                )

    results.sort()
    return results
