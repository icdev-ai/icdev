# CUI // SP-CTI
"""STRATEGOS — Theater Abstraction Layer.

Conflict-agnostic base class for all 14 STRATEGOS epics.  Loads a theater YAML
from args/theaters/<id>.yaml and exposes typed properties used by importers,
agents, and the blueprint.

Supported theaters: pacific, ukraine (stub), and any custom YAML following the
same schema.

Usage::

    from tools.strategos.theater import Theater, THEATER_REGISTRY

    theater = Theater.load("pacific")
    print(theater.id, theater.bounds)

    # List all registered theaters
    for t in THEATER_REGISTRY.values():
        print(t.id, t.name)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

_THEATERS_DIR = Path(__file__).resolve().parents[2] / "args" / "theaters"

# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class TheaterBounds:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def contains(self, lon: float, lat: float) -> bool:
        return (self.min_lon <= lon <= self.max_lon and
                self.min_lat <= lat <= self.max_lat)

    def as_list(self) -> list[float]:
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    def center(self) -> tuple[float, float]:
        return (
            (self.min_lon + self.max_lon) / 2,
            (self.min_lat + self.max_lat) / 2,
        )


@dataclass
class TheaterActor:
    id: str
    name: str
    role: str  # adversary | friendly | contested | neutral
    color: str = "#888888"
    force_designator: str = ""


@dataclass
class OrbatUnit:
    id: str
    name: str
    actor: str
    unit_type: str  # surface | submarine | aircraft | ground | cyber | space
    count: int = 1
    lat: float = 0.0
    lon: float = 0.0
    status: str = "active"  # active | degraded | destroyed
    properties: dict = field(default_factory=dict)


@dataclass
class EscalationLevel:
    level: int  # 1–10
    label: str
    description: str
    color: str = "#888888"


@dataclass
class Theater:
    id: str
    name: str
    bounds: TheaterBounds
    pmtiles_file: str = ""
    active_domains: list[str] = field(default_factory=list)
    actors: list[TheaterActor] = field(default_factory=list)
    orbat: list[OrbatUnit] = field(default_factory=list)
    escalation_ladder: list[EscalationLevel] = field(default_factory=list)
    _raw: dict = field(default_factory=dict, repr=False)

    # ── Factories ─────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, theater_id: str) -> "Theater":
        """Load a theater from args/theaters/<theater_id>.yaml."""
        path = _THEATERS_DIR / f"{theater_id}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Theater config not found: {path}")
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, raw: dict) -> "Theater":
        t = raw.get("theater", raw)

        bounds_d = t.get("bounds", {})
        bounds = TheaterBounds(
            min_lon=float(bounds_d.get("min_lon", 0)),
            min_lat=float(bounds_d.get("min_lat", 0)),
            max_lon=float(bounds_d.get("max_lon", 0)),
            max_lat=float(bounds_d.get("max_lat", 0)),
        )

        actors = [
            TheaterActor(
                id=a["id"],
                name=a.get("name", a["id"]),
                role=a.get("role", "neutral"),
                color=a.get("color", "#888888"),
                force_designator=a.get("force_designator", ""),
            )
            for a in t.get("actors", [])
        ]

        orbat: list[OrbatUnit] = []
        for grp in t.get("orbat", []):
            actor_id = grp.get("actor", "")
            for unit in grp.get("units", []):
                orbat.append(
                    OrbatUnit(
                        id=unit.get("id", ""),
                        name=unit.get("name", ""),
                        actor=actor_id,
                        unit_type=unit.get("type", "surface"),
                        count=int(unit.get("count", 1)),
                        lat=float(unit.get("lat", 0)),
                        lon=float(unit.get("lon", 0)),
                        status=unit.get("status", "active"),
                        properties={
                            k: v
                            for k, v in unit.items()
                            if k not in {"id", "name", "type", "count", "lat", "lon", "status"}
                        },
                    )
                )

        escalation = [
            EscalationLevel(
                level=int(e.get("level", i + 1)),
                label=e.get("label", f"Level {i+1}"),
                description=e.get("description", ""),
                color=e.get("color", "#888888"),
            )
            for i, e in enumerate(t.get("escalation_ladder", []))
        ]
        if not escalation:
            escalation = _default_escalation_ladder()

        active_domains = t.get("active_domains", ["land", "sea", "air", "cyber", "space"])

        return cls(
            id=t.get("id", "unknown"),
            name=t.get("name", "Unknown Theater"),
            bounds=bounds,
            pmtiles_file=t.get("pmtiles_file", ""),
            active_domains=active_domains,
            actors=actors,
            orbat=orbat,
            escalation_ladder=escalation,
            _raw=raw,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def get_actor(self, actor_id: str) -> TheaterActor | None:
        for a in self.actors:
            if a.id == actor_id:
                return a
        return None

    def actors_by_role(self, role: str) -> list[TheaterActor]:
        return [a for a in self.actors if a.role == role]

    def orbat_for_actor(self, actor_id: str) -> list[OrbatUnit]:
        return [u for u in self.orbat if u.actor == actor_id]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "bounds": {
                "min_lon": self.bounds.min_lon,
                "min_lat": self.bounds.min_lat,
                "max_lon": self.bounds.max_lon,
                "max_lat": self.bounds.max_lat,
            },
            "pmtiles_file": self.pmtiles_file,
            "active_domains": self.active_domains,
            "actors": [
                {
                    "id": a.id,
                    "name": a.name,
                    "role": a.role,
                    "color": a.color,
                    "force_designator": a.force_designator,
                }
                for a in self.actors
            ],
            "orbat_count": len(self.orbat),
            "escalation_levels": len(self.escalation_ladder),
        }


# ── Default escalation ladder ─────────────────────────────────────────────────

def _default_escalation_ladder() -> list[EscalationLevel]:
    return [
        EscalationLevel(1,  "Tension",          "Diplomatic friction, military posturing",              "#27ae60"),
        EscalationLevel(2,  "Crisis",            "Incident at sea/air, warnings issued",                "#2ecc71"),
        EscalationLevel(3,  "Limited Action",    "Quarantine, blockade, covert operations",             "#f39c12"),
        EscalationLevel(4,  "Overt Hostility",   "Military confrontation, limited strikes",             "#e67e22"),
        EscalationLevel(5,  "Regional War",      "Large-scale conventional conflict",                   "#e74c3c"),
        EscalationLevel(6,  "Expanded War",      "Allied intervention, widening front",                 "#c0392b"),
        EscalationLevel(7,  "Nuclear Signaling", "Nuclear rhetoric, dispersal of assets",               "#8e44ad"),
        EscalationLevel(8,  "Nuclear Warning",   "Tactical nuclear detonation (low-yield)",             "#6c3483"),
        EscalationLevel(9,  "Strategic Nuclear", "Strategic nuclear exchange threatened",               "#4a235a"),
        EscalationLevel(10, "Existential",       "Full strategic nuclear exchange",                     "#1a1a2e"),
    ]


# ── Domain registry ───────────────────────────────────────────────────────────

DOMAIN_REGISTRY: dict[str, dict] = {
    "land": {
        "label": "Land",
        "icon": "land",
        "color": "#8B6914",
        "importers": ["acled_importer", "osm_importer", "oryx_importer"],
        "agents": ["intel_agent", "land_agent"],
    },
    "sea": {
        "label": "Maritime",
        "icon": "anchor",
        "color": "#1565C0",
        "importers": ["ais_importer", "ais_sdr_receiver"],
        "agents": ["maritime_agent"],
    },
    "air": {
        "label": "Air",
        "icon": "plane",
        "color": "#1976D2",
        "importers": ["adsb_importer"],
        "agents": ["intel_agent"],
    },
    "cyber": {
        "label": "Cyber",
        "icon": "shield",
        "color": "#7B1FA2",
        "importers": ["stix_importer", "cisa_kev_importer"],
        "agents": ["cyber_agent"],
    },
    "space": {
        "label": "Space",
        "icon": "satellite",
        "color": "#0D47A1",
        "importers": ["tle_importer"],
        "agents": ["space_agent"],
    },
    "info": {
        "label": "Info Warfare",
        "icon": "broadcast",
        "color": "#E65100",
        "importers": ["gdelt_importer"],
        "agents": ["info_warfare_agent"],
    },
    "ew": {
        "label": "Electronic Warfare",
        "icon": "wifi",
        "color": "#558B2F",
        "importers": [],
        "agents": ["ew_agent"],
    },
    "supply": {
        "label": "Logistics / Supply",
        "icon": "truck",
        "color": "#4E342E",
        "importers": ["osm_importer"],
        "agents": ["intel_agent"],
    },
}


# ── Theater registry (auto-loads all YAML in args/theaters/) ──────────────────

def _build_registry() -> dict[str, Theater]:
    reg: dict[str, Theater] = {}
    if not _THEATERS_DIR.exists():
        return reg
    for path in sorted(_THEATERS_DIR.glob("*.yaml")):
        try:
            t = Theater.load(path.stem)
            reg[t.id] = t
        except Exception:
            pass
    return reg


THEATER_REGISTRY: dict[str, Theater] = _build_registry()


def get_theater(theater_id: str) -> Theater:
    """Return a Theater by ID, reloading from disk if not cached."""
    if theater_id not in THEATER_REGISTRY:
        THEATER_REGISTRY[theater_id] = Theater.load(theater_id)
    return THEATER_REGISTRY[theater_id]


def list_theaters() -> list[dict]:
    """Return a list of all registered theater summary dicts."""
    return [t.as_dict() for t in THEATER_REGISTRY.values()]
