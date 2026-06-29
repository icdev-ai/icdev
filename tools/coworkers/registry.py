# CUI // SP-CTI
"""Co-Worker registry — the roster the ``/coworkers`` hub lists.

Co-workers are read from two existing, config-driven sources (no DB):

  * ``args/chat_personas.yaml`` — the chat-agent persona registry (Atlas,
    Guardian, Scout, ...).  These are the primary conversational co-workers.
  * ``args/ace/roles/*.yaml`` — the ACE autonomous role catalog (AI Developer,
    QA Manager, ...).  Included best-effort as additional co-workers.

Each entry is normalized into a :class:`CoWorker`.  Loading is fully tolerant:
a missing or malformed source yields an empty contribution rather than raising,
so the blueprint can render an empty roster instead of 500-ing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from icdev.tools.coworkers import constants as _const


@dataclass
class CoWorker:
    """A single co-worker the user can collaborate with via the shared chat."""

    id: str
    name: str
    role: str
    description: str
    avatar_initial: str
    accent: str
    kind: str  # "persona" | "ace"
    rag_namespace: str
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "avatar_initial": self.avatar_initial,
            "accent": self.accent,
            "kind": self.kind,
            "rag_namespace": self.rag_namespace,
            "tags": list(self.tags),
        }


def _read_yaml(path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — missing/garbage source -> no contribution
        return {}


def _personas() -> list[CoWorker]:
    data = _read_yaml(_const.PERSONAS_YAML)
    personas = data.get("personas") or {}
    out: list[CoWorker] = []
    for key, spec in personas.items():
        if key in _const.HIDDEN_PERSONA_KEYS or not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or key.title())
        out.append(
            CoWorker(
                id=f"persona:{key}",
                name=name,
                role=str(spec.get("role") or "Co-Worker"),
                description=str(spec.get("description") or ""),
                avatar_initial=str(spec.get("avatar_initial") or name[:1].upper()),
                accent=str(spec.get("accent") or _const.DEFAULT_ACCENT),
                kind="persona",
                rag_namespace=f"coworker:{key}",
                tags=list(spec.get("advisory_types") or []),
            )
        )
    return out


def _ace_roles() -> list[CoWorker]:
    roles_dir = _const.ACE_ROLES_DIR
    if not roles_dir.exists():
        return []
    out: list[CoWorker] = []
    for path in sorted(roles_dir.glob("*.yaml")):
        spec = _read_yaml(path)
        role_id = spec.get("role_id")
        if not role_id:
            continue
        name = str(spec.get("display_name") or role_id)
        out.append(
            CoWorker(
                id=f"ace:{role_id}",
                name=name,
                role=str(spec.get("trust_tier", "")).title() + " Tier" if spec.get("trust_tier") else "Autonomous",
                description=str(spec.get("description") or ""),
                avatar_initial=name[:1].upper(),
                accent="#7c4dff",
                kind="ace",
                rag_namespace=f"coworker:ace:{role_id}",
                tags=list(spec.get("steps") or [])[:4],
            )
        )
    return out


def list_coworkers() -> list[CoWorker]:
    """Return the full co-worker roster, personas first then ACE roles."""
    return _personas() + _ace_roles()


def get_coworker(coworker_id: str) -> Optional[CoWorker]:
    """Return a single co-worker by id, or ``None`` if unknown."""
    for cw in list_coworkers():
        if cw.id == coworker_id:
            return cw
    return None
