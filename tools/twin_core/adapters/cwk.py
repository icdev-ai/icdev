# CUI // SP-CTI — Co-Workers canvas governance twin adapter (icx-cnf-03)
"""Governance twin over the Co-Workers canvas (``tools/coworkers``), canvas key ``cwk``.

The canvas has no table and decides nothing: it lists a roster read from two
config sources (``args/chat_personas.yaml`` and ``args/ace/roles/*.yaml``) and
links into the shared chat. ``registry.list_coworkers`` is tolerant by design —
a file it cannot parse, or a role with no ``role_id``, simply drops out of the
roster with no error anywhere.

The smallest meaningful observation is therefore the one that tolerance hides:
every source file read and hashed, every file that silently contributes nothing,
and any two entries that collide on an id (``get_coworker`` returns the first).

Threads: ``config:<path>@<sha256 prefix>``; resolving one re-hashes the file and
says whether it changed since the observation.
"""
from __future__ import annotations

import hashlib

from tools.twin_core.governance import (
    GovernanceTwinAdapter,
    check,
    finding,
    load_policy,
    now_iso,
)
from tools.twin_core.governance import (
    replay as replay_observation,
)
from tools.twin_core.registry import register_twin


def _read(path, base) -> dict:
    """One source file: its digest and what it parses to (never raises)."""
    import yaml

    rel = path.relative_to(base).as_posix()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {"path": rel, "sha256": "", "error": str(exc)}
    out = {"path": rel, "sha256": hashlib.sha256(raw).hexdigest(), "error": ""}
    try:
        out["data"] = yaml.safe_load(raw.decode("utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — a bad file is the finding, not a crash
        out["error"] = f"unparseable: {exc}"
    return out


@register_twin
class CoWorkersGovernanceTwin(GovernanceTwinAdapter):
    canvas_key = "cwk"

    def replay(self, observation_id: str) -> dict:
        """Re-judge a recorded observation from its evidence (see governance.replay)."""
        return replay_observation(self, observation_id)

    def collect(self) -> dict:
        from tools.coworkers import constants as const
        from tools.twin_core.governance import BASE_DIR

        ev: dict = {"canvas": self.canvas_key, "target_id": "fleet", "observed_at": now_iso(),
                    "policy": load_policy("cwk"), "personas": None, "roles": None,
                    "errors": {}}
        personas_path = BASE_DIR / "args" / const.PERSONAS_YAML.name
        src = _read(personas_path, BASE_DIR)
        data = src.pop("data", None)
        if src["error"]:
            ev["errors"]["personas"] = src["error"]
        else:
            personas = (data or {}).get("personas") if isinstance(data, dict) else None
            src["keys"] = sorted(k for k, v in (personas or {}).items()
                                 if isinstance(v, dict) and k not in const.HIDDEN_PERSONA_KEYS)
            src["shape_ok"] = isinstance(personas, dict)
            ev["personas"] = src
        roles_dir = BASE_DIR / "args" / "ace" / "roles"
        if not roles_dir.is_dir():
            ev["errors"]["roles"] = f"{roles_dir.relative_to(BASE_DIR).as_posix()} missing"
        else:
            roles = []
            for path in sorted(roles_dir.glob("*.yaml")):
                item = _read(path, BASE_DIR)
                data = item.pop("data", None)
                item["role_id"] = (str(data.get("role_id") or "")
                                   if isinstance(data, dict) else "")
                roles.append(item)
            ev["roles"] = roles
        return ev

    def judge(self, evidence: dict) -> dict:
        errors = evidence.get("errors") or {}
        checks, findings = [], []

        personas = evidence.get("personas")
        checks.append(check("cwk.personas", "Persona roster source", personas is not None,
                            errors.get("personas") or "args/chat_personas.yaml not read"))
        if personas is not None:
            thread = f"config:{personas['path']}@{personas['sha256'][:12]}"
            if not personas["shape_ok"]:
                findings.append(finding(
                    "cwk.personas.shape", "high", "chat_personas.yaml has no 'personas' mapping",
                    thread_id=thread,
                    recommendation="The roster shows no persona at all until this parses."))
            elif not personas["keys"]:
                findings.append(finding(
                    "cwk.personas.empty", "medium", "No visible persona in the roster",
                    thread_id=thread))

        roles = evidence.get("roles")
        checks.append(check("cwk.roles", "ACE role roster sources", roles is not None,
                            errors.get("roles") or "args/ace/roles not read"))
        ids: dict = {}
        for k in (personas or {}).get("keys") or []:
            ids.setdefault(f"persona:{k}", []).append((personas or {})["path"])
        for r in roles or []:
            thread = f"config:{r['path']}@{r['sha256'][:12]}"
            if r["error"] or not r["role_id"]:
                findings.append(finding(
                    "cwk.role.dropped", "medium",
                    f"{r['path']} contributes no co-worker (silently dropped)",
                    thread_id=thread, detail=r["error"] or "no role_id",
                    recommendation="Fix the file or remove it; the roster hides the failure."))
            else:
                ids.setdefault(f"ace:{r['role_id']}", []).append(r["path"])
        for cid, paths in sorted(ids.items()):
            if len(paths) > 1:
                src = next(r for r in roles or [] if r["path"] == paths[1])
                findings.append(finding(
                    "cwk.id.collision", "medium", f"Co-worker id '{cid}' is defined twice",
                    thread_id=f"config:{src['path']}@{src['sha256'][:12]}",
                    detail=", ".join(paths),
                    recommendation="get_coworker returns the first; the second is unreachable."))

        summary = {
            "personas": len((personas or {}).get("keys") or []),
            "role_files": len(roles or []),
            "roster": len(ids),
        }
        return {"checks": checks, "findings": findings, "summary": summary}
