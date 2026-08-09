# CUI // SP-CTI
"""NOVA SOUL — Product Manager persistent state."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_SOULS_DIR = BASE_DIR / ".tmp" / "nova_souls"

_DEFAULT_STATE: dict[str, Any] = {
    "active_roadmap_priorities": [],
    "current_sprint_state": {},
    "stakeholder_preferences": {},
    "accumulated_premortem_patterns": [],
    "wwas_history": [],
}


def _soul_path(role_id: str, tenant_id: str) -> Path:
    return _SOULS_DIR / f"{role_id}_{tenant_id}.json"


def load(role_id: str = "product_manager", tenant_id: str = "default") -> dict:
    path = _soul_path(role_id, tenant_id)
    if not path.exists():
        return {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in _DEFAULT_STATE.items()}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in _DEFAULT_STATE.items()}


def save(state: dict, role_id: str = "product_manager", tenant_id: str = "default") -> None:
    _SOULS_DIR.mkdir(parents=True, exist_ok=True)
    _soul_path(role_id, tenant_id).write_text(json.dumps(state, indent=2, default=str), encoding="utf-8", newline="")


def update_roadmap(priorities: list, role_id: str = "product_manager", tenant_id: str = "default") -> None:
    state = load(role_id, tenant_id)
    state["active_roadmap_priorities"] = priorities
    save(state, role_id, tenant_id)


def update_sprint_state(sprint: dict, role_id: str = "product_manager", tenant_id: str = "default") -> None:
    state = load(role_id, tenant_id)
    state["current_sprint_state"] = sprint
    save(state, role_id, tenant_id)


def add_stakeholder_pref(name: str, pref: dict, role_id: str = "product_manager", tenant_id: str = "default") -> None:
    state = load(role_id, tenant_id)
    state["stakeholder_preferences"][name] = pref
    save(state, role_id, tenant_id)


def add_premortem_pattern(pattern: str, role_id: str = "product_manager", tenant_id: str = "default") -> None:
    state = load(role_id, tenant_id)
    if pattern not in state["accumulated_premortem_patterns"]:
        state["accumulated_premortem_patterns"].append(pattern)
    save(state, role_id, tenant_id)


def append_wwas(wwas_entry: dict, role_id: str = "product_manager", tenant_id: str = "default") -> None:
    state = load(role_id, tenant_id)
    entry = dict(wwas_entry)
    entry.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
    state["wwas_history"].append(entry)
    save(state, role_id, tenant_id)
