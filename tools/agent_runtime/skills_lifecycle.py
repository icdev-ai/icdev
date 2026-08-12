# CUI // SP-CTI
"""Skills lifecycle for the standalone agent (sag-skl-01).

Wires ICDEV's *existing* NOVA skill-generation machinery into the SAG runtime —
it does not rebuild a generator. The flow:

1. **Propose** — a post-session hook (:func:`maybe_propose_from_session`, or the
   ``/skill propose`` command) asks NOVA's
   :func:`tools.nova.skill_generator.generate_skill_spec` to draft a skill when a
   session solved a *novel* task. The draft is queued in the existing
   ``agent_improvement_artifacts`` table (``status='pending'``) — NOVA's queue,
   reused. Provenance (session id, model, timestamp) is stamped onto the queue row
   because an LLM-generated skill is a TRUST surface.
2. **HITL** — proposals stay quarantined until a human runs
   :func:`approve_proposal` / :func:`reject_proposal` / :func:`edit_proposal`.
   Nothing is written to ``.agents/skills`` without explicit human approval — the
   approval IS the gate.
3. **Promote** — an approved proposal is written to
   ``.agents/skills/icdev-auto-<slug>/SKILL.md`` (parseable by
   ``tools/skills/registry.py``) with a provenance frontmatter block, and recorded
   in ``sag_skill_registry`` (migration 291).
4. **Curate** — the ``sag_skill_curator`` Genesis reflex tracks
   ``use_count`` / ``last_activity_at`` and **archives (never deletes)** idle,
   unpinned auto-skills after N days. Pinned skills are retained indefinitely.

Everything degrades gracefully — a missing NOVA generator, DB, or skills dir yields
an empty/annotated result rather than raising.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.skills_lifecycle")

_TABLE = "sag_skill_registry"
_ARTIFACTS = "agent_improvement_artifacts"
_AUTO_PREFIX = "icdev-auto-"
_PROPOSALS_ENV = "ICDEV_SAG_SKILL_PROPOSALS"  # gate the automatic post-session hook
_DEFAULT_ARCHIVE_DAYS = 30
_MIN_NOVEL_TURNS = 2


def proposals_enabled() -> bool:
    """Whether the post-session skill-proposal hook runs.

    ``ICDEV_SAG_SKILL_PROPOSALS`` → ``args/agent_runtime.yaml`` → off. The env
    var is read first and still wins (hgx-cfg-01); the default stays OFF at every
    layer, because the hook queues a NOVA proposal on every session close.
    """
    import os

    try:
        from tools.agent_runtime.config import load_config

        return load_config().subsystem_enabled(
            "skill_proposals", env=_PROPOSALS_ENV, default=False
        )
    except Exception as exc:  # noqa: BLE001 — config is a layer, not a dependency
        logger.debug("skills_lifecycle: config layer unavailable: %s", exc)
        return os.environ.get(_PROPOSALS_ENV, "").lower() in ("1", "true", "yes")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Paths (repo root via upward sentinel walk — never parents[N] / cwd)
# ---------------------------------------------------------------------------
def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() or (parent / "CLAUDE.md").exists():
            return parent
    return here.parents[2]


def _skills_dir() -> Path:
    return _repo_root() / ".agents" / "skills"


def _archive_dir() -> Path:
    return _skills_dir() / "_archive"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "skill"


def auto_skill_name(base: str) -> str:
    """Normalise any name/pattern to a canonical ``icdev-auto-<slug>`` name."""
    base = base or "skill"
    if base.startswith(_AUTO_PREFIX):
        return base
    if base.startswith("icdev-"):
        base = base[len("icdev-"):]
    return _AUTO_PREFIX + _slug(base)


# ---------------------------------------------------------------------------
# Registry store
# ---------------------------------------------------------------------------
_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    name             TEXT PRIMARY KEY,
    artifact_id      TEXT,
    skill_dir        TEXT,
    session_id       TEXT DEFAULT '',
    model            TEXT DEFAULT '',
    approved_by      TEXT DEFAULT '',
    status           TEXT DEFAULT 'active',
    pinned           INTEGER DEFAULT 0,
    use_count        INTEGER DEFAULT 0,
    last_activity_at TEXT,
    classification   TEXT DEFAULT 'CUI',
    created_at       TEXT,
    updated_at       TEXT
)
"""

_REG_COLS = [
    "name", "artifact_id", "skill_dir", "session_id", "model", "approved_by",
    "status", "pinned", "use_count", "last_activity_at", "created_at", "updated_at",
]


def _connect(conn=None):
    if conn is not None:
        return conn
    from tools.db.storage import get_connection

    return get_connection()


def _ensure_schema(conn) -> None:
    try:
        conn.execute(_DDL)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("skills_lifecycle: ensure_schema failed: %s", exc)


# ---------------------------------------------------------------------------
# Proposal side (reuses NOVA's queue in agent_improvement_artifacts)
# ---------------------------------------------------------------------------
def _existing_skill_names() -> set[str]:
    try:
        from tools.skills.registry import load_registry

        return set((load_registry().get("skills") or {}).keys())
    except Exception:  # noqa: BLE001
        return set()


def is_novel(pattern: str, *, existing: Optional[set[str]] = None) -> bool:
    """A pattern is novel when it maps to an auto-skill name not already present."""
    if not (pattern or "").strip():
        return False
    names = existing if existing is not None else _existing_skill_names()
    candidate = auto_skill_name(pattern)
    base = candidate[len(_AUTO_PREFIX):]
    # collide with either the icdev-auto-<slug> or a hand-authored icdev-<slug>
    return candidate not in names and f"icdev-{base}" not in names


def propose_skill(
    pattern: str,
    *,
    session_id: str = "",
    model: str = "",
    category: str = "general",
    conn=None,
) -> dict[str, Any]:
    """Draft + queue a skill proposal via NOVA (quarantined as 'pending').

    Stamps provenance (session id, model) onto the queue row. Returns the NOVA
    result enriched with ``novel`` / ``proposed``. No SKILL.md is written here —
    promotion is HITL-gated (:func:`approve_proposal`).
    """
    if not is_novel(pattern):
        return {"proposed": False, "novel": False, "reason": "not novel", "pattern": pattern}
    try:
        from tools.nova.skill_generator import generate_skill_spec

        result = generate_skill_spec(pattern, category=category)
    except Exception as exc:  # noqa: BLE001 — generator best-effort
        logger.debug("skills_lifecycle: generate failed: %s", exc)
        return {"proposed": False, "novel": True, "error": str(exc), "pattern": pattern}

    artifact_id = result.get("skill_id")
    result["proposed"] = bool(result.get("queued"))
    result["novel"] = True
    # Stamp provenance onto the queued artifact (TRUST surface).
    if artifact_id and result.get("queued"):
        _stamp_provenance(artifact_id, session_id=session_id, model=model, conn=conn)
    return result


def _stamp_provenance(artifact_id: str, *, session_id: str, model: str, conn=None) -> None:
    try:
        c = _connect(conn)
        cur = c.execute(
            f"SELECT evidence_traces FROM {_ARTIFACTS} WHERE artifact_id = %s",
            (artifact_id,),
        )
        row = cur.fetchone()
        try:
            evidence = json.loads(row[0]) if row and row[0] else {}
        except Exception:  # noqa: BLE001
            evidence = {}
        if not isinstance(evidence, dict):
            evidence = {"raw": evidence}
        evidence.update({"session_id": session_id, "model": model, "provenance_at": _utcnow()})
        c.execute(
            f"UPDATE {_ARTIFACTS} SET evidence_traces = %s WHERE artifact_id = %s",
            (json.dumps(evidence), artifact_id),
        )
        c.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("skills_lifecycle: stamp provenance failed: %s", exc)


def _parse_evidence(raw: Any) -> dict[str, Any]:
    """Lesson-backed evidence bundle for a proposal (exa-refine-04)."""
    try:
        from tools.workflow.refinement_evidence import parse_evidence

        return parse_evidence(raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("skills_lifecycle: evidence parse failed: %s", exc)
        return {}


def _evidence_summary(raw: Any) -> str:
    try:
        from tools.workflow.refinement_evidence import evidence_summary

        return evidence_summary(raw)
    except Exception:  # noqa: BLE001
        return ""


def list_proposals(status: str = "pending", *, limit: int = 50, conn=None) -> list[dict[str, Any]]:
    """List skill proposals in the NOVA queue (``task_type='skill_generation'``)."""
    try:
        c = _connect(conn)
        cur = c.execute(
            f"SELECT artifact_id, skill_used, improvement_text, evidence_traces, status, created_at "
            f"FROM {_ARTIFACTS} WHERE task_type = %s "
            + ("AND status = %s " if status and status != "all" else "")
            + f"ORDER BY created_at DESC LIMIT {int(limit)}",
            (("skill_generation", status) if status and status != "all" else ("skill_generation",)),
        )
        out = []
        for r in cur.fetchall():
            try:
                ev = json.loads(r[3]) if r[3] else {}
            except Exception:  # noqa: BLE001
                ev = {}
            out.append({
                "artifact_id": r[0], "skill_name": r[1],
                "spec": r[2], "provenance": ev, "status": r[4], "created_at": str(r[5]),
                # exa-refine-04: a reviewer sees the lesson rows and recurrence
                # score behind the proposal, not just its provenance.
                "evidence": _parse_evidence(r[3]),
                "evidence_summary": _evidence_summary(r[3]),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("skills_lifecycle: list_proposals failed: %s", exc)
        return []


def _get_proposal(artifact_id: str, *, conn=None) -> Optional[dict[str, Any]]:
    c = _connect(conn)
    cur = c.execute(
        f"SELECT artifact_id, skill_used, improvement_text, evidence_traces, status "
        f"FROM {_ARTIFACTS} WHERE artifact_id = %s",
        (artifact_id,),
    )
    r = cur.fetchone()
    if not r:
        return None
    try:
        ev = json.loads(r[3]) if r[3] else {}
    except Exception:  # noqa: BLE001
        ev = {}
    return {
        "artifact_id": r[0], "skill_name": r[1], "spec": r[2], "provenance": ev,
        "status": r[4], "evidence": _parse_evidence(r[3]),
        "evidence_summary": _evidence_summary(r[3]),
    }


def _set_proposal_status(artifact_id: str, status: str, *, conn=None) -> None:
    c = _connect(conn)
    c.execute(
        f"UPDATE {_ARTIFACTS} SET status = %s WHERE artifact_id = %s",
        (status, artifact_id),
    )
    c.commit()


def edit_proposal(artifact_id: str, new_spec: str, *, conn=None) -> bool:
    """Human edit of a quarantined proposal's spec text before approval."""
    prop = _get_proposal(artifact_id, conn=conn)
    if prop is None:
        return False
    c = _connect(conn)
    c.execute(
        f"UPDATE {_ARTIFACTS} SET improvement_text = %s WHERE artifact_id = %s",
        (new_spec, artifact_id),
    )
    c.commit()
    return True


def reject_proposal(artifact_id: str, *, approver: str = "human", reason: str = "", conn=None) -> bool:
    prop = _get_proposal(artifact_id, conn=conn)
    if prop is None:
        return False
    _set_proposal_status(artifact_id, "rejected", conn=conn)
    logger.info("skills_lifecycle: proposal %s rejected by %s (%s)", artifact_id, approver, reason)
    return True


# ---------------------------------------------------------------------------
# Promotion (HITL-approved write to .agents/skills — the ONLY writer)
# ---------------------------------------------------------------------------
def _provenance_frontmatter(name: str, prov: dict[str, Any], approver: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: Auto-generated skill (HITL-approved). Review before relying on it.\n"
        "allowed-tools: \n"
        "generated-by: nova_skill_generator\n"
        f"source-session: {prov.get('session_id', '') or ''}\n"
        f"source-model: {prov.get('model', '') or ''}\n"
        f"source-pattern: {prov.get('source_pattern', '') or ''}\n"
        f"generated-at: {prov.get('provenance_at', '') or _utcnow()}\n"
        f"approved-by: {approver}\n"
        f"approved-at: {_utcnow()}\n"
        "trust: unverified-llm-generated\n"
        "---\n\n"
    )


def approve_proposal(
    artifact_id: str, *, approver: str = "human", conn=None
) -> dict[str, Any]:
    """Promote an approved proposal to ``.agents/skills/icdev-auto-<slug>/SKILL.md``.

    Writes the spec with a provenance frontmatter block and records the skill in
    ``sag_skill_registry``. This is the sole path that writes an auto-skill to
    disk, and only on explicit human approval (HITL gate).
    """
    prop = _get_proposal(artifact_id, conn=conn)
    if prop is None:
        return {"approved": False, "error": "proposal not found", "artifact_id": artifact_id}

    name = auto_skill_name(prop.get("skill_name") or artifact_id)
    spec = prop.get("spec") or ""
    prov = prop.get("provenance") or {}
    frontmatter = _provenance_frontmatter(name, prov, approver)

    skill_dir = _skills_dir() / name
    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        # If the generated spec already carries a frontmatter block, keep the body.
        body = spec
        (skill_dir / "SKILL.md").write_text(frontmatter + body + "\n", encoding="utf-8", newline="")
    except Exception as exc:  # noqa: BLE001
        return {"approved": False, "error": f"write failed: {exc}", "artifact_id": artifact_id}

    _register_promoted(
        name, artifact_id=artifact_id, skill_dir=str(skill_dir),
        session_id=str(prov.get("session_id", "")), model=str(prov.get("model", "")),
        approved_by=approver, conn=conn,
    )
    _set_proposal_status(artifact_id, "approved", conn=conn)
    logger.info("skills_lifecycle: promoted %s (approved by %s)", name, approver)
    return {"approved": True, "name": name, "skill_dir": str(skill_dir)}


def _register_promoted(
    name: str, *, artifact_id: str, skill_dir: str, session_id: str, model: str,
    approved_by: str, conn=None,
) -> None:
    c = _connect(conn)
    _ensure_schema(c)
    now = _utcnow()
    cur = c.execute(f"SELECT 1 FROM {_TABLE} WHERE name = %s", (name,))
    if cur.fetchone():
        c.execute(
            f"UPDATE {_TABLE} SET artifact_id = %s, skill_dir = %s, session_id = %s, "
            f"model = %s, approved_by = %s, status = 'active', last_activity_at = %s, "
            f"updated_at = %s WHERE name = %s",
            (artifact_id, skill_dir, session_id, model, approved_by, now, now, name),
        )
    else:
        c.execute(
            f"INSERT INTO {_TABLE} (name, artifact_id, skill_dir, session_id, model, "
            f"approved_by, status, pinned, use_count, last_activity_at, created_at, updated_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s, 'active', 0, 0, %s, %s, %s)",
            (name, artifact_id, skill_dir, session_id, model, approved_by, now, now, now),
        )
    c.commit()


# ---------------------------------------------------------------------------
# Curator (use tracking + archive-never-delete + pin)
# ---------------------------------------------------------------------------
def record_use(name: str, *, conn=None) -> None:
    """Bump ``use_count`` / ``last_activity_at`` for a promoted auto-skill."""
    try:
        c = _connect(conn)
        _ensure_schema(c)
        c.execute(
            f"UPDATE {_TABLE} SET use_count = use_count + 1, last_activity_at = %s, "
            f"updated_at = %s WHERE name = %s",
            (_utcnow(), _utcnow(), name),
        )
        c.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("skills_lifecycle: record_use failed: %s", exc)


def set_pinned(name: str, pinned: bool, *, conn=None) -> bool:
    c = _connect(conn)
    _ensure_schema(c)
    c.execute(
        f"UPDATE {_TABLE} SET pinned = %s, updated_at = %s WHERE name = %s",
        (1 if pinned else 0, _utcnow(), name),
    )
    c.commit()
    return _get_registry_row(name, conn=c) is not None


def _get_registry_row(name: str, *, conn=None) -> Optional[dict[str, Any]]:
    c = _connect(conn)
    _ensure_schema(c)
    cur = c.execute(f"SELECT {', '.join(_REG_COLS)} FROM {_TABLE} WHERE name = %s", (name,))
    r = cur.fetchone()
    return {_REG_COLS[i]: r[i] for i in range(len(_REG_COLS))} if r else None


def list_promoted(*, status: Optional[str] = None, conn=None) -> list[dict[str, Any]]:
    c = _connect(conn)
    _ensure_schema(c)
    where = " WHERE status = %s" if status else ""
    cur = c.execute(
        f"SELECT {', '.join(_REG_COLS)} FROM {_TABLE}{where} ORDER BY name",
        ((status,) if status else ()),
    )
    return [{_REG_COLS[i]: r[i] for i in range(len(_REG_COLS))} for r in cur.fetchall()]


def curate(*, archive_after_days: int = _DEFAULT_ARCHIVE_DAYS, dry_run: bool = True, conn=None) -> dict[str, Any]:
    """Archive (never delete) idle, unpinned auto-skills.

    A skill is idle when ``last_activity_at`` is older than ``archive_after_days``
    (falling back to ``created_at``). Pinned skills are always retained. Archiving
    moves the skill directory to ``.agents/skills/_archive/`` and flips status to
    ``archived`` — the SKILL.md is preserved, never deleted.
    """
    c = _connect(conn)
    _ensure_schema(c)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=archive_after_days)
    report: dict[str, Any] = {"dry_run": dry_run, "archived": [], "retained_pinned": [], "checked": 0}

    for row in list_promoted(status="active", conn=c):
        report["checked"] += 1
        if int(row.get("pinned", 0) or 0):
            report["retained_pinned"].append(row["name"])
            continue
        ts = row.get("last_activity_at") or row.get("created_at")
        idle = True
        if ts:
            try:
                dt = datetime.fromisoformat(str(ts))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                idle = dt < cutoff
            except Exception:  # noqa: BLE001
                idle = True
        if not idle:
            continue
        report["archived"].append(row["name"])
        if not dry_run:
            _archive_skill(row["name"], row.get("skill_dir"), conn=c)
    return report


def _archive_skill(name: str, skill_dir: Optional[str], *, conn=None) -> None:
    try:
        src = Path(skill_dir) if skill_dir else (_skills_dir() / name)
        if src.exists():
            dest_root = _archive_dir()
            dest_root.mkdir(parents=True, exist_ok=True)
            dest = dest_root / name
            if dest.exists():
                shutil.rmtree(dest)  # replace a stale archive copy, not the live one
            shutil.move(str(src), str(dest))
        c = _connect(conn)
        c.execute(
            f"UPDATE {_TABLE} SET status = 'archived', updated_at = %s WHERE name = %s",
            (_utcnow(), name),
        )
        c.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("skills_lifecycle: archive %s failed: %s", name, exc)


# ---------------------------------------------------------------------------
# Post-session hook
# ---------------------------------------------------------------------------
def maybe_propose_from_session(runtime: Any, *, force: bool = False) -> dict[str, Any]:
    """Best-effort post-session skill proposal (env-gated unless ``force``).

    Called when a SAG session ends (``/new`` swap or ``/exit``). Gated behind
    ``ICDEV_SAG_SKILL_PROPOSALS`` (falling back to
    ``subsystems.skill_proposals.enabled`` in ``args/agent_runtime.yaml``) so it
    is silent by default. Derives a candidate pattern from the session title (a
    novel, tool-solved task), novelty-gates it, and queues a NOVA proposal.
    Never raises.
    """
    if not force and not proposals_enabled():
        return {"proposed": False, "reason": "disabled"}
    try:
        session = getattr(runtime, "session", None)
        if session is None:
            return {"proposed": False, "reason": "no session"}
        if int(getattr(session, "turn_count", 0) or 0) < _MIN_NOVEL_TURNS:
            return {"proposed": False, "reason": "too few turns"}
        title = (getattr(session, "title", "") or "").strip()
        if not title or title.lower() in ("untitled session", "untitled"):
            return {"proposed": False, "reason": "no descriptive title"}
        return propose_skill(
            title,
            session_id=getattr(session, "context_id", ""),
            model=getattr(runtime, "llm_function", ""),
        )
    except Exception as exc:  # noqa: BLE001 — hook is best-effort
        logger.debug("skills_lifecycle: post-session hook failed: %s", exc)
        return {"proposed": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Headless HITL CLI
# ---------------------------------------------------------------------------
def cli(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m tools.agent_runtime.skills_lifecycle",
        description="HITL lifecycle for auto-generated SAG skills.",
    )
    sub = p.add_subparsers(dest="cmd")
    prop = sub.add_parser("propose", help="Propose a skill from a pattern.")
    prop.add_argument("pattern")
    lst = sub.add_parser("list", help="List proposals.")
    lst.add_argument("--status", default="pending")
    for name in ("approve", "reject"):
        sp = sub.add_parser(name, help=f"{name} a proposal by artifact id.")
        sp.add_argument("artifact_id")
        sp.add_argument("--by", default="human")
        if name == "reject":
            sp.add_argument("--reason", default="")
    promoted = sub.add_parser("promoted", help="List promoted auto-skills.")
    promoted.add_argument("--status", default=None)
    pin = sub.add_parser("pin", help="Pin a promoted skill (never archived).")
    pin.add_argument("name")
    unpin = sub.add_parser("unpin", help="Unpin a promoted skill.")
    unpin.add_argument("name")
    cur = sub.add_parser("curate", help="Run the archival sweep.")
    cur.add_argument("--days", type=int, default=_DEFAULT_ARCHIVE_DAYS)
    cur.add_argument("--apply", action="store_true", help="Actually archive (default: dry-run).")

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 1
    if args.cmd == "propose":
        print(json.dumps(propose_skill(args.pattern), indent=2, default=str))
        return 0
    if args.cmd == "list":
        print(json.dumps(list_proposals(args.status), indent=2, default=str))
        return 0
    if args.cmd == "approve":
        print(json.dumps(approve_proposal(args.artifact_id, approver=args.by), indent=2, default=str))
        return 0
    if args.cmd == "reject":
        ok = reject_proposal(args.artifact_id, approver=args.by, reason=args.reason)
        print(json.dumps({"rejected": ok}, indent=2))
        return 0 if ok else 1
    if args.cmd == "promoted":
        print(json.dumps(list_promoted(status=args.status), indent=2, default=str))
        return 0
    if args.cmd in ("pin", "unpin"):
        print(json.dumps({"ok": set_pinned(args.name, args.cmd == "pin")}, indent=2))
        return 0
    if args.cmd == "curate":
        print(json.dumps(curate(archive_after_days=args.days, dry_run=not args.apply), indent=2, default=str))
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(cli(sys.argv[1:]))
