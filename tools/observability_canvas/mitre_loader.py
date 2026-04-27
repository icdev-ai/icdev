# CUI // SP-CTI
"""MITRE ATT&CK Enterprise technique catalog loader for the Observability Design Canvas.

Extends: war-domain actor profiles for Sandworm Team (G0034) and APT28 (G0007).
Use ``load_actor_techniques(actor_key)`` to retrieve catalog techniques attributed
to either actor.  Use ``WAR_DOMAIN_ACTORS`` to inspect known T-code sets and aliases.
"""

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


@dataclass(frozen=True)
class ActorProfile:
    """Static profile for a war-domain threat actor.

    Attributes
    ----------
    id            : MITRE ATT&CK Group ID (e.g. 'G0034')
    name          : Canonical name
    aliases       : Known aliases / industry names
    technique_ids : MITRE T-codes attributed to this actor
    """

    id: str
    name: str
    aliases: tuple[str, ...]
    technique_ids: tuple[str, ...]


# ---------------------------------------------------------------------------
# War-domain actor profiles
# Technique lists sourced from MITRE ATT&CK Groups page (Enterprise v15).
# ---------------------------------------------------------------------------

WAR_DOMAIN_ACTORS: dict[str, ActorProfile] = {
    "sandworm": ActorProfile(
        id="G0034",
        name="Sandworm Team",
        aliases=(
            "BlackEnergy", "ELECTRUM", "IRIDIUM", "Quedagh",
            "TeleBots", "TEMP.Noble", "Voodoo Bear",
        ),
        technique_ids=(
            # Initial Access
            "T1078", "T1190", "T1133", "T1195.002",
            # Execution
            "T1059.001", "T1059.003", "T1059.005", "T1059.006",
            "T1203", "T1204.002",
            # Persistence
            "T1547.001", "T1053.005", "T1543.003", "T1197",
            # Defense Evasion
            "T1027", "T1027.002", "T1140", "T1055", "T1036",
            "T1112", "T1562.001",
            # Credential Access
            "T1003.001", "T1003.002", "T1110", "T1557",
            # Discovery
            "T1083", "T1057", "T1012", "T1016", "T1049",
            "T1082", "T1087", "T1033",
            # Lateral Movement
            "T1021.002", "T1021.004", "T1021.006", "T1534",
            # Collection
            "T1039", "T1113",
            # C2
            "T1071.001", "T1071.002", "T1573", "T1219",
            # Exfiltration
            "T1048", "T1041",
            # Impact
            "T1486", "T1490", "T1529", "T1561.002", "T1485",
            # Resource Development
            "T1584.001", "T1583.001",
        ),
    ),
    "apt28": ActorProfile(
        id="G0007",
        name="APT28",
        aliases=(
            "Fancy Bear", "STRONTIUM", "Sofacy", "Pawn Storm",
            "Sednit", "CyberCaliphate", "IRON TWILIGHT", "Forest Blizzard",
        ),
        technique_ids=(
            # Initial Access
            "T1078", "T1190", "T1133", "T1566.001", "T1566.002",
            # Execution
            "T1059.001", "T1059.003", "T1059.005", "T1203", "T1204.002",
            # Persistence
            "T1547.001", "T1053.005", "T1543.003",
            # Defense Evasion
            "T1027", "T1140", "T1055", "T1036", "T1112",
            "T1562.001", "T1070.004",
            # Credential Access
            "T1003.001", "T1003.003", "T1110", "T1187", "T1528",
            # Discovery
            "T1083", "T1057", "T1012", "T1016", "T1049",
            "T1082", "T1087", "T1033",
            # Lateral Movement
            "T1021.002", "T1021.003", "T1021.006",
            # Collection
            "T1039", "T1056.001", "T1114.002", "T1560.001",
            "T1113", "T1123", "T1125",
            # C2
            "T1071.001", "T1071.004", "T1573",
            # Exfiltration
            "T1048", "T1041", "T1567",
            # Resource Development
            "T1584.001", "T1583.001", "T1586.002",
        ),
    ),
}


def load_actor_techniques(
    actor_key: str,
    catalog_path: Path = _DEFAULT_CATALOG,
) -> list[MitreTechnique]:
    """Return catalog ``MitreTechnique`` objects attributed to a war-domain actor.

    Parameters
    ----------
    actor_key   : ``'sandworm'`` or ``'apt28'`` — key in ``WAR_DOMAIN_ACTORS``
    catalog_path: path to MITRE ATT&CK Enterprise catalog JSON

    Returns
    -------
    Sorted list of ``MitreTechnique`` for every T-code in the actor profile
    that is present in the catalog.  T-codes absent from the catalog (version
    drift) are silently skipped.

    Raises
    ------
    ValueError  : unknown actor_key
    """
    profile = WAR_DOMAIN_ACTORS.get(actor_key.lower())
    if profile is None:
        raise ValueError(
            f"Unknown actor '{actor_key}'. "
            f"Valid keys: {sorted(WAR_DOMAIN_ACTORS)}"
        )
    all_techniques = load_techniques(catalog_path)
    known_ids = frozenset(profile.technique_ids)
    return sorted(t for t in all_techniques if t.id in known_ids)


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
