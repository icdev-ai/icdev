# CUI // SP-CTI
"""NOVA SOUL — Software Craftsperson persistent state."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_SOULS_DIR = BASE_DIR / ".tmp" / "nova_souls"

_DEFAULT_STATE: dict = {
    "adr_store": {},
    "rationalization_log": [],
    "current_spec_context": None,
    "test_debt_register": [],
}


def _soul_path(role_id: str, tenant_id: str) -> Path:
    return _SOULS_DIR / f"{role_id}_{tenant_id}.json"


def load(role_id: str = "software_craftsperson", tenant_id: str = "default") -> dict:
    path = _soul_path(role_id, tenant_id)
    if not path.exists():
        return {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in _DEFAULT_STATE.items()}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in _DEFAULT_STATE.items()}


def save(state: dict, role_id: str = "software_craftsperson", tenant_id: str = "default") -> None:
    _SOULS_DIR.mkdir(parents=True, exist_ok=True)
    _soul_path(role_id, tenant_id).write_text(json.dumps(state, indent=2, default=str), encoding="utf-8", newline="")


def add_adr(project: str, adr: dict, role_id: str = "software_craftsperson", tenant_id: str = "default") -> None:
    state = load(role_id, tenant_id)
    if project not in state["adr_store"]:
        state["adr_store"][project] = []
    entry = dict(adr)
    entry.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
    state["adr_store"][project].append(entry)
    save(state, role_id, tenant_id)


def log_rationalization(
    excuse: str,
    rebuttal: str,
    task: str,
    role_id: str = "software_craftsperson",
    tenant_id: str = "default",
) -> None:
    state = load(role_id, tenant_id)
    state["rationalization_log"].append({
        "task": task,
        "excuse": excuse,
        "rebuttal": rebuttal,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    })
    save(state, role_id, tenant_id)


def set_spec_context(spec_path: str, role_id: str = "software_craftsperson", tenant_id: str = "default") -> None:
    state = load(role_id, tenant_id)
    state["current_spec_context"] = spec_path
    save(state, role_id, tenant_id)


def add_test_debt(
    func_name: str,
    file_path: str,
    role_id: str = "software_craftsperson",
    tenant_id: str = "default",
) -> None:
    state = load(role_id, tenant_id)
    entry = {
        "func_name": func_name,
        "file_path": file_path,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    existing = [e for e in state["test_debt_register"] if e.get("func_name") == func_name and e.get("file_path") == file_path]
    if not existing:
        state["test_debt_register"].append(entry)
    save(state, role_id, tenant_id)
