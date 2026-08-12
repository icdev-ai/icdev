#!/usr/bin/env python3
# CUI // SP-CTI
"""Point-in-time snapshot + as-a-unit rollback of the supplemental harness state.

ICDEV can already undo *individual* self-applied changes: ``prompt_registry``
has per-prompt version rollback, ``skills_lifecycle`` archives (never deletes)
and gates promotion behind HITL, ``goal_learner`` keeps suffixed prior versions.
What none of them gives you is "undo the last refinement cycle" — because a
cycle spans all three, and there was no snapshot of the supplemental state *as a
whole* to return to. This module is that snapshot, and the rollback that uses it.

**What "supplemental state" means here.** The harness state ICDEV layers on top
of a vendor agent and can rewrite about itself: the active prompt layers
(``prompt_versions``), the auto-generated skills (``sag_skill_registry`` plus
``.agents/skills/icdev-auto-*``), and the learned goals
(``genesis_generated_goals`` plus ``data/genesis/suggested_goals``). Each is a
:class:`Provider`; the set is open, so a fourth supplemental store registers
itself rather than being special-cased here.

**Two halves, one unit.** Supplemental state is half database rows and half
files on disk, so a snapshot is half of each:

* The **file** half is delegated to :mod:`tools.agent_runtime.checkpoints`
  verbatim — no second checkpoint system. It already does exactly what is needed
  (untracked files copied, tracked files captured as one git object and restored
  with explicit pathspecs, absent files recorded so rollback deletes them) and it
  is already the thing ``/snapshot`` and ``/rollback`` drive. A cycle stores its
  ``checkpoint_id`` and calls back into it.
* The **row** half is what checkpoints.py has no model for — it captures paths,
  not tuples — so each provider serialises the fields that make its rows
  restorable, and the JSON lands in ``supplemental_state_snapshots``.

**Rollback is itself a cycle.** Before restoring anything, the current state is
captured as a new *undo* cycle, so ``rollback_cycle(undo_cycle_id)`` puts you
back. That is also what makes deleting a file safe: a file that appeared during
the cycle is only removed once the undo cycle's checkpoint is confirmed to hold
a recoverable copy of that exact path (see :func:`_recoverable_from`).

**Nothing is UPDATEd to record history.** Both tables are append-only. A cycle
being rolled back is a new ``('cycle', 'rolled_back')`` row, not a status flip,
and :func:`cycle_status` derives the verdict at read time — the same
successor-not-edit convention ``sbom_revision`` uses.

**Every snapshot and every applied refinement writes a chained audit row.**
``log_event`` (exa-audit-03) populates ``hash`` / ``previous_hash`` /
``signature``, so a self-modification is *verifiable*, not merely logged: the
returned ``audit_entry_id`` is stored on the row, and :func:`verify_cycle` feeds
each one back through ``provenance_verifier.verify_audit_integrity``. Audit
first, row second — the audit id is a column, and an append-only table has no
UPDATE coming later to fill it in.

Usage:
    python tools/agent_runtime/refinement_cycle.py open --label "gepa pass"
    python tools/agent_runtime/refinement_cycle.py list
    python tools/agent_runtime/refinement_cycle.py show <cycle-id>
    python tools/agent_runtime/refinement_cycle.py rollback <cycle-id> [--yes]
    python tools/agent_runtime/refinement_cycle.py verify <cycle-id>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.refinement_cycle")

SNAPSHOT_TABLE = "supplemental_state_snapshots"
REFINEMENT_TABLE = "supplemental_refinements"

#: The audit event type every row here is written under. Actions are namespaced
#: inside it (``snapshot_created``, ``refinement_applied``, ``cycle_rolled_back``)
#: rather than each becoming its own event type — the convention
#: ``migration_canvas`` set in ``VALID_EVENT_TYPES``.
AUDIT_EVENT_TYPE = "supplemental_state"

#: Snapshot kinds.
KIND_OPEN = "open"
KIND_PRE_ROLLBACK = "pre_rollback"

#: The provider name used for cycle-level bookkeeping rows.
CYCLE_PROVIDER = "cycle"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _connect(conn=None):
    if conn is not None:
        return conn, False
    from tools.db.storage import get_connection

    return get_connection(), True


def _has_table(conn, name: str) -> bool:
    try:
        from tools.db.storage import table_exists

        return bool(table_exists(conn, name))
    except Exception as exc:  # noqa: BLE001
        logger.debug("refinement_cycle: table_exists(%s) failed: %s", name, exc)
        return False


def _rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Fetch rows as plain dicts, tolerating sqlite3.Row / psycopg row shapes."""
    cur = conn.execute(sql, params)
    fetched = cur.fetchall() or []
    out: list[dict[str, Any]] = []
    for row in fetched:
        if hasattr(row, "keys"):
            out.append({k: row[k] for k in row.keys()})
        else:
            cols = [d[0] for d in (cur.description or [])]
            out.append(dict(zip(cols, row)))
    return out


def repo_root() -> Path:
    """The repo root, resolved the same way checkpoints.py resolves it.

    Deliberately not ``os.getcwd()``: this module runs from worktrees and from
    the Genesis daemon, where cwd is not the checkout root (CLAUDE.md).
    """
    from tools.agent_runtime.checkpoints import repo_root as _root

    return _root()


# ---------------------------------------------------------------------------
# Providers — one per supplemental store
# ---------------------------------------------------------------------------
@dataclass
class Provider:
    """One supplemental store that can be captured and restored.

    ``capture`` returns a JSON-serialisable dict. ``restore`` receives exactly
    what ``capture`` returned and reports what it did, as human-readable lines.
    ``paths`` lists the repo-relative *files* backing the store, which is what
    gets handed to checkpoints.py.
    """

    name: str
    capture: Callable[[], dict[str, Any]]
    restore: Callable[[dict[str, Any]], list[str]]
    paths: Callable[[], list[str]] = field(default=lambda: [])


_PROVIDERS: "dict[str, Provider]" = {}


def register_provider(provider: Provider) -> None:
    """Add a supplemental store to the set a cycle snapshots."""
    _PROVIDERS[provider.name] = provider


def providers() -> "dict[str, Provider]":
    return dict(_PROVIDERS)


def _unavailable(reason: str) -> dict[str, Any]:
    """A capture result for a store this database does not have.

    Recorded rather than omitted: "the prompt registry had no table" and "the
    prompt registry had no rows" are different facts, and a rollback must not
    silently skip a store whose absence it never noticed.
    """
    return {"available": False, "reason": reason}


def _files_under(*relative_dirs: str, patterns: tuple = ("*",)) -> list[str]:
    """Repo-relative posix paths of every file under the given directories."""
    root = repo_root()
    found: list[str] = []
    for rel in relative_dirs:
        base = root / rel
        if not base.is_dir():
            continue
        for pattern in patterns:
            for path in sorted(base.rglob(pattern)):
                if path.is_file():
                    found.append(path.relative_to(root).as_posix())
    return sorted(set(found))


# --- prompts ---------------------------------------------------------------
def _capture_prompts() -> dict[str, Any]:
    conn, owned = _connect()
    try:
        if not _has_table(conn, "prompt_versions"):
            return _unavailable("prompt_versions table not present")
        rows = _rows(
            conn,
            "SELECT prompt_name, version, status, template_hash "
            "FROM prompt_versions ORDER BY prompt_name, version",
        )
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"capture failed: {exc}")
    finally:
        if owned:
            conn.close()
    active = {r["prompt_name"]: r["version"] for r in rows if r.get("status") == "active"}
    return {"available": True, "versions": rows, "active": active}


def _restore_prompts(captured: dict[str, Any]) -> list[str]:
    """Re-point every prompt at the version that was active at snapshot time.

    Two moves, in this order. Versions that did not exist at snapshot time are
    archived first — ``prompt_versions`` is not append-only but a registered
    version is still evidence, so it is archived, never deleted. Then each
    prompt whose active version drifted is put back with
    ``prompt_registry.rollback_prompt``, which is the per-prompt rollback that
    already exists; this module does not re-implement it.
    """
    if not captured.get("available"):
        return []
    from tools.llm import prompt_registry

    applied: list[str] = []
    known = {(r["prompt_name"], r["version"]) for r in captured.get("versions", [])}
    conn, owned = _connect()
    try:
        if not _has_table(conn, "prompt_versions"):
            return ["prompts: prompt_versions table disappeared — nothing restored"]
        current = _rows(conn, "SELECT prompt_name, version, status FROM prompt_versions")
        for row in current:
            key = (row["prompt_name"], row["version"])
            if key in known or row.get("status") == "archived":
                continue
            conn.execute(
                "UPDATE prompt_versions SET status = 'archived', updated_at = %s "
                "WHERE prompt_name = %s AND version = %s",
                (_utcnow(), row["prompt_name"], row["version"]),
            )
            applied.append(f"prompts: archived {row['prompt_name']} v{row['version']} (added during cycle)")
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("refinement_cycle: archiving new prompt versions failed: %s", exc)
    finally:
        if owned:
            conn.close()

    for name, version in (captured.get("active") or {}).items():
        try:
            result = prompt_registry.rollback_prompt(name, int(version), actor="refinement_cycle")
        except prompt_registry.BasePromptImmutableError:
            # The reserved base prompt is immutable by design, so it cannot have
            # drifted and there is nothing to put back.
            continue
        except Exception as exc:  # noqa: BLE001
            applied.append(f"prompts: {name} → v{version} FAILED ({exc})")
            continue
        if result.get("status") == "ok":
            applied.append(f"prompts: {name} → v{version}")
        else:
            applied.append(f"prompts: {name} → v{version} skipped ({result.get('message')})")

    try:
        prompt_registry.invalidate_layer_cache()
    except Exception as exc:  # noqa: BLE001
        logger.debug("refinement_cycle: layer cache invalidation failed: %s", exc)
    return applied


def _prompt_paths() -> list[str]:
    # Prompt state lives entirely in the database; hardprompts/ is the import
    # source, not the live state, so it is deliberately not snapshotted.
    return []


# --- skills ----------------------------------------------------------------
_SKILLS_ROOT = ".agents/skills"


def _capture_skills() -> dict[str, Any]:
    conn, owned = _connect()
    try:
        if not _has_table(conn, "sag_skill_registry"):
            return _unavailable("sag_skill_registry table not present")
        rows = _rows(
            conn,
            "SELECT name, status, pinned, skill_dir FROM sag_skill_registry ORDER BY name",
        )
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"capture failed: {exc}")
    finally:
        if owned:
            conn.close()
    return {"available": True, "skills": rows}


def _restore_skills(captured: dict[str, Any]) -> list[str]:
    """Put every auto-skill's status/pin back, archiving ones added mid-cycle."""
    if not captured.get("available"):
        return []
    applied: list[str] = []
    by_name = {r["name"]: r for r in captured.get("skills", [])}
    conn, owned = _connect()
    try:
        if not _has_table(conn, "sag_skill_registry"):
            return ["skills: sag_skill_registry table disappeared — nothing restored"]
        current = _rows(conn, "SELECT name, status, pinned FROM sag_skill_registry")
        for row in current:
            before = by_name.get(row["name"])
            if before is None:
                conn.execute(
                    "UPDATE sag_skill_registry SET status = 'archived', updated_at = %s WHERE name = %s",
                    (_utcnow(), row["name"]),
                )
                applied.append(f"skills: archived {row['name']} (promoted during cycle)")
                continue
            if row.get("status") == before.get("status") and int(row.get("pinned") or 0) == int(
                before.get("pinned") or 0
            ):
                continue
            conn.execute(
                "UPDATE sag_skill_registry SET status = %s, pinned = %s, updated_at = %s WHERE name = %s",
                (before.get("status"), int(before.get("pinned") or 0), _utcnow(), row["name"]),
            )
            applied.append(
                f"skills: {row['name']} status {row.get('status')} → {before.get('status')}"
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        applied.append(f"skills: restore FAILED ({exc})")
    finally:
        if owned:
            conn.close()
    return applied


def _skill_paths() -> list[str]:
    # Only the auto-generated subset: hand-authored skills are not supplemental
    # state and must not be reverted by an "undo the refinement cycle".
    return _files_under(_SKILLS_ROOT, patterns=("icdev-auto-*/**/*", "_archive/**/*"))


# --- goals -----------------------------------------------------------------
_GOALS_ROOT = "data/genesis/suggested_goals"


def _capture_goals() -> dict[str, Any]:
    conn, owned = _connect()
    try:
        if not _has_table(conn, "genesis_generated_goals"):
            return _unavailable("genesis_generated_goals table not present")
        rows = _rows(
            conn,
            "SELECT id, status, version, goal_file_path FROM genesis_generated_goals ORDER BY id",
        )
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"capture failed: {exc}")
    finally:
        if owned:
            conn.close()
    return {"available": True, "goals": rows}


def _restore_goals(captured: dict[str, Any]) -> list[str]:
    """Put learned-goal statuses back; goals learned mid-cycle become superseded.

    ``superseded`` rather than a delete: it is already in the table's CHECK, it
    keeps the evidence, and it is what the status means — the cycle that
    produced the goal was undone.
    """
    if not captured.get("available"):
        return []
    applied: list[str] = []
    by_id = {r["id"]: r for r in captured.get("goals", [])}
    conn, owned = _connect()
    try:
        if not _has_table(conn, "genesis_generated_goals"):
            return ["goals: genesis_generated_goals table disappeared — nothing restored"]
        current = _rows(conn, "SELECT id, status FROM genesis_generated_goals")
        for row in current:
            before = by_id.get(row["id"])
            if before is None:
                conn.execute(
                    "UPDATE genesis_generated_goals SET status = 'superseded', updated_at = %s WHERE id = %s",
                    (_utcnow(), row["id"]),
                )
                applied.append(f"goals: superseded {row['id']} (learned during cycle)")
                continue
            if row.get("status") == before.get("status"):
                continue
            conn.execute(
                "UPDATE genesis_generated_goals SET status = %s, updated_at = %s WHERE id = %s",
                (before.get("status"), _utcnow(), row["id"]),
            )
            applied.append(f"goals: {row['id']} status {row.get('status')} → {before.get('status')}")
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        applied.append(f"goals: restore FAILED ({exc})")
    finally:
        if owned:
            conn.close()
    return applied


def _goal_paths() -> list[str]:
    return _files_under(_GOALS_ROOT, patterns=("*.md",))


register_provider(Provider("prompts", _capture_prompts, _restore_prompts, _prompt_paths))
register_provider(Provider("skills", _capture_skills, _restore_skills, _skill_paths))
register_provider(Provider("goals", _capture_goals, _restore_goals, _goal_paths))


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
def capture_state(names: Optional[list[str]] = None) -> dict[str, Any]:
    """Serialise the supplemental state of every (or the named) provider."""
    selected = names or sorted(_PROVIDERS)
    state: dict[str, Any] = {"captured_at": _utcnow(), "providers": {}}
    for name in selected:
        provider = _PROVIDERS.get(name)
        if provider is None:
            state["providers"][name] = _unavailable("no such provider")
            continue
        try:
            state["providers"][name] = provider.capture()
        except Exception as exc:  # noqa: BLE001
            logger.warning("refinement_cycle: provider %s capture failed: %s", name, exc)
            state["providers"][name] = _unavailable(f"capture raised: {exc}")
    return state


def state_hash(state: dict[str, Any]) -> str:
    """Digest of a captured state, so drift is detectable without a diff."""
    payload = json.dumps(state.get("providers", {}), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot_paths(names: Optional[list[str]] = None) -> list[str]:
    selected = names or sorted(_PROVIDERS)
    paths: list[str] = []
    for name in selected:
        provider = _PROVIDERS.get(name)
        if provider is None:
            continue
        try:
            paths.extend(provider.paths())
        except Exception as exc:  # noqa: BLE001
            logger.warning("refinement_cycle: provider %s paths() failed: %s", name, exc)
    return sorted(set(paths))


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def _audit(action: str, actor: str, details: dict[str, Any]) -> Optional[int]:
    """Write the chained audit row for a cycle event; return its entry id.

    Best effort, and deliberately so: losing the ability to *record* a
    refinement must not stop the refinement from being snapshotted. A row whose
    ``audit_entry_id`` is NULL is visible to :func:`verify_cycle` as
    ``unaudited``, which is a louder failure than a snapshot that never happened.
    """
    try:
        from tools.audit.audit_logger import log_event

        entry_id = log_event(
            event_type=AUDIT_EVENT_TYPE,
            actor=actor,
            action=action,
            details=details,
            classification="CUI",
        )
        return int(entry_id) if entry_id and int(entry_id) > 0 else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("refinement_cycle: audit write for %s failed: %s", action, exc)
        return None


# ---------------------------------------------------------------------------
# Open a cycle
# ---------------------------------------------------------------------------
def open_cycle(
    label: str = "",
    *,
    actor: str = "system",
    provider_names: Optional[list[str]] = None,
    kind: str = KIND_OPEN,
    cycle_id: Optional[str] = None,
    conn=None,
) -> dict[str, Any]:
    """Snapshot the supplemental state and start a refinement cycle.

    Returns ``{cycle_id, snapshot_id, checkpoint_id, state_hash, audit_entry_id,
    providers, paths}``.

    The INSERT is **not** wrapped in a best-effort ``except``. A database that
    has not run migration 20260812074403 must fail here and loudly: a caller
    that believes it holds a snapshot, then refines, then discovers at rollback
    time that nothing was recorded is exactly the swallowed-INSERT failure this
    repo keeps hitting. The audit write above is best-effort for the opposite
    reason — losing the ability to *describe* a snapshot must not stop the
    snapshot from being taken, and a NULL ``audit_entry_id`` is visible to
    :func:`verify_cycle` as ``unaudited``.
    """
    cid = cycle_id or _new_id("rc")
    snapshot_id = _new_id("rcs")
    state = capture_state(provider_names)
    digest = state_hash(state)
    paths = _snapshot_paths(provider_names)

    checkpoint_id: Optional[str] = None
    try:
        from tools.agent_runtime.checkpoints import create_checkpoint

        checkpoint = create_checkpoint(
            paths, label=f"refinement cycle {cid}: {label}".strip(": "), tool_name="refinement_cycle"
        )
        checkpoint_id = checkpoint.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("refinement_cycle: file checkpoint for %s failed: %s", cid, exc)

    audit_entry_id = _audit(
        "snapshot_created",
        actor,
        {
            "cycle_id": cid,
            "snapshot_id": snapshot_id,
            "kind": kind,
            "state_hash": digest,
            "checkpoint_id": checkpoint_id,
            "providers": sorted(state.get("providers", {})),
            "file_count": len(paths),
            "label": label,
        },
    )

    db, owned = _connect(conn)
    try:
        db.execute(
            f"INSERT INTO {SNAPSHOT_TABLE} "
            "(id, cycle_id, kind, label, actor, checkpoint_id, state_json, state_hash, "
            " audit_entry_id, classification, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                snapshot_id,
                cid,
                kind,
                label,
                actor,
                checkpoint_id,
                json.dumps(state, default=str),
                digest,
                audit_entry_id,
                "CUI",
                _utcnow(),
            ),
        )
        db.commit()
    finally:
        if owned:
            db.close()

    logger.info("refinement_cycle: opened %s (%s, %d file(s))", cid, kind, len(paths))
    return {
        "cycle_id": cid,
        "snapshot_id": snapshot_id,
        "kind": kind,
        "checkpoint_id": checkpoint_id,
        "state_hash": digest,
        "audit_entry_id": audit_entry_id,
        "providers": sorted(state.get("providers", {})),
        "paths": paths,
    }


def record_refinement(
    cycle_id: str,
    provider: str,
    action: str,
    *,
    target: str = "",
    actor: str = "system",
    details: Optional[dict[str, Any]] = None,
    conn=None,
) -> dict[str, Any]:
    """Record one applied refinement inside ``cycle_id``, with a chained audit row."""
    rid = _new_id("rcr")
    payload = dict(details or {})
    audit_entry_id = _audit(
        "refinement_applied" if provider != CYCLE_PROVIDER else f"cycle_{action}",
        actor,
        {
            "cycle_id": cycle_id,
            "refinement_id": rid,
            "provider": provider,
            "refinement_action": action,
            "target": target,
            **payload,
        },
    )
    db, owned = _connect(conn)
    try:
        db.execute(
            f"INSERT INTO {REFINEMENT_TABLE} "
            "(id, cycle_id, provider, action, target, actor, details, audit_entry_id, "
            " classification, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                rid,
                cycle_id,
                provider,
                action,
                target,
                actor,
                json.dumps(payload, default=str),
                audit_entry_id,
                "CUI",
                _utcnow(),
            ),
        )
        db.commit()
    finally:
        if owned:
            db.close()
    return {"refinement_id": rid, "cycle_id": cycle_id, "audit_entry_id": audit_entry_id}


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def get_snapshot(cycle_id: str, *, conn=None) -> Optional[dict[str, Any]]:
    """The opening snapshot of a cycle — the state a rollback returns to.

    Deliberately not filtered on ``kind``: a cycle has exactly one snapshot, and
    the kind records only *why* it was taken. An undo cycle's snapshot is a
    ``pre_rollback`` one, and filtering to ``open`` would make the undo cycle
    un-rollbackable — i.e. a rollback you cannot undo, which is the property
    this whole module exists to provide.
    """
    db, owned = _connect(conn)
    try:
        if not _has_table(db, SNAPSHOT_TABLE):
            return None
        rows = _rows(
            db,
            f"SELECT * FROM {SNAPSHOT_TABLE} WHERE cycle_id = %s ORDER BY created_at ASC, id ASC",
            (cycle_id,),
        )
    finally:
        if owned:
            db.close()
    return rows[0] if rows else None


def list_refinements(cycle_id: str, *, conn=None) -> list[dict[str, Any]]:
    db, owned = _connect(conn)
    try:
        if not _has_table(db, REFINEMENT_TABLE):
            return []
        return _rows(
            db,
            f"SELECT * FROM {REFINEMENT_TABLE} WHERE cycle_id = %s ORDER BY created_at ASC, id ASC",
            (cycle_id,),
        )
    finally:
        if owned:
            db.close()


def cycle_status(cycle_id: str, *, conn=None) -> str:
    """``open``, ``rolled_back`` or ``unknown`` — derived, never stored.

    Both tables are append-only, so "this cycle was rolled back" is the presence
    of a ``('cycle', 'rolled_back')`` row, not a flag someone UPDATEd.
    """
    db, owned = _connect(conn)
    try:
        if get_snapshot(cycle_id, conn=db) is None:
            return "unknown"
        for row in list_refinements(cycle_id, conn=db):
            if row.get("provider") == CYCLE_PROVIDER and row.get("action") == "rolled_back":
                return "rolled_back"
        return "open"
    finally:
        if owned:
            db.close()


def list_cycles(limit: int = 20, *, conn=None) -> list[dict[str, Any]]:
    """Cycles newest first, with a derived status and refinement count.

    Undo cycles (``kind='pre_rollback'``) are listed alongside the rest, because
    undoing an undo is a thing you do from this list.
    """
    db, owned = _connect(conn)
    try:
        if not _has_table(db, SNAPSHOT_TABLE):
            return []
        snapshots = _rows(
            db,
            f"SELECT * FROM {SNAPSHOT_TABLE} ORDER BY created_at DESC, id DESC",
        )[: max(0, limit)]
        out = []
        for snap in snapshots:
            refinements = list_refinements(snap["cycle_id"], conn=db)
            out.append(
                {
                    "cycle_id": snap["cycle_id"],
                    "kind": snap.get("kind"),
                    "label": snap.get("label") or "",
                    "created_at": snap.get("created_at"),
                    "actor": snap.get("actor"),
                    "checkpoint_id": snap.get("checkpoint_id"),
                    "state_hash": snap.get("state_hash"),
                    "audit_entry_id": snap.get("audit_entry_id"),
                    "refinements": len(refinements),
                    "status": cycle_status(snap["cycle_id"], conn=db),
                }
            )
        return out
    finally:
        if owned:
            db.close()


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------
def _recoverable_from(checkpoint_id: Optional[str], rel_path: str) -> bool:
    """Whether ``rel_path`` is genuinely restorable from ``checkpoint_id``.

    This is the precondition for deleting a file that appeared during the cycle.
    Copy-based capture (untracked) or a git stash object (tracked) both count; a
    checkpoint that recorded the path but captured no bytes does not.
    """
    if not checkpoint_id:
        return False
    try:
        from tools.agent_runtime.checkpoints import load_checkpoint

        checkpoint = load_checkpoint(checkpoint_id)
    except Exception:  # noqa: BLE001
        return False
    if checkpoint is None:
        return False
    for entry in checkpoint.files:
        if entry.path != rel_path or not entry.existed:
            continue
        if entry.copy_name:
            return True
        return bool(entry.tracked and checkpoint.stash_sha)
    return False


def _files_added_since(snapshot_paths: list[str], provider_names: Optional[list[str]]) -> list[str]:
    """Files under the supplemental roots that did not exist at snapshot time."""
    return sorted(set(_snapshot_paths(provider_names)) - set(snapshot_paths))


def describe_rollback(cycle_id: str, *, conn=None) -> dict[str, Any]:
    """What :func:`rollback_cycle` would do, without doing any of it."""
    snap = get_snapshot(cycle_id, conn=conn)
    if snap is None:
        return {"ok": False, "reason": f"cycle {cycle_id!r} has no opening snapshot"}
    try:
        state = json.loads(snap.get("state_json") or "{}")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"snapshot state is unreadable: {exc}"}

    provider_names = sorted(state.get("providers", {}))
    current = capture_state(provider_names)
    drifted = [
        name
        for name in provider_names
        if json.dumps(current["providers"].get(name), sort_keys=True, default=str)
        != json.dumps(state["providers"].get(name), sort_keys=True, default=str)
    ]

    file_changes: list[str] = []
    checkpoint_id = snap.get("checkpoint_id")
    if checkpoint_id:
        try:
            from tools.agent_runtime.checkpoints import describe_changes, load_checkpoint

            checkpoint = load_checkpoint(checkpoint_id)
            if checkpoint is not None:
                file_changes = describe_changes(checkpoint)
        except Exception as exc:  # noqa: BLE001
            file_changes = [f"(file checkpoint {checkpoint_id} unreadable: {exc})"]

    added = _files_added_since(_paths_from_checkpoint(checkpoint_id), provider_names)

    return {
        "ok": True,
        "cycle_id": cycle_id,
        "status": cycle_status(cycle_id, conn=conn),
        "snapshot_id": snap.get("id"),
        "checkpoint_id": checkpoint_id,
        "providers": provider_names,
        "drifted_providers": drifted,
        "file_changes": file_changes,
        "files_added_during_cycle": added,
        "refinements": len(list_refinements(cycle_id, conn=conn)),
    }


def _paths_from_checkpoint(checkpoint_id: Optional[str]) -> list[str]:
    if not checkpoint_id:
        return []
    try:
        from tools.agent_runtime.checkpoints import load_checkpoint

        checkpoint = load_checkpoint(checkpoint_id)
    except Exception:  # noqa: BLE001
        return []
    return [f.path for f in checkpoint.files] if checkpoint else []


def rollback_cycle(
    cycle_id: str,
    *,
    actor: str = "system",
    force: bool = False,
    remove_added_files: bool = True,
    conn=None,
) -> dict[str, Any]:
    """Undo a whole refinement cycle: rows, then files, as one unit.

    Order matters. The current state is captured as an *undo* cycle first, so
    the rollback is reversible before anything is touched — and so a file that
    appeared during the cycle can be removed only after that undo cycle's
    checkpoint is confirmed to hold its bytes.

    Args:
        force: roll back a cycle that was already rolled back.
        remove_added_files: delete files that appeared during the cycle (only
            ever when the undo checkpoint holds a recoverable copy).
    """
    snap = get_snapshot(cycle_id, conn=conn)
    if snap is None:
        return {"ok": False, "reason": f"cycle {cycle_id!r} has no opening snapshot"}
    if cycle_status(cycle_id, conn=conn) == "rolled_back" and not force:
        return {
            "ok": False,
            "cycle_id": cycle_id,
            "reason": "cycle already rolled back (pass force=True to repeat)",
        }
    try:
        state = json.loads(snap.get("state_json") or "{}")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"snapshot state is unreadable: {exc}"}

    provider_names = sorted(state.get("providers", {}))
    snapshot_paths = _paths_from_checkpoint(snap.get("checkpoint_id"))

    # 1. Capture the present as its own cycle, so this rollback is reversible.
    undo = open_cycle(
        f"pre-rollback of {cycle_id}",
        actor=actor,
        provider_names=provider_names,
        kind=KIND_PRE_ROLLBACK,
        conn=conn,
    )

    # 2. Restore the row half, provider by provider.
    applied: list[str] = []
    for name in provider_names:
        provider = _PROVIDERS.get(name)
        captured = state["providers"].get(name) or {}
        if provider is None:
            applied.append(f"{name}: provider not registered — NOT restored")
            continue
        try:
            applied.extend(provider.restore(captured))
        except Exception as exc:  # noqa: BLE001
            logger.warning("refinement_cycle: provider %s restore failed: %s", name, exc)
            applied.append(f"{name}: restore FAILED ({exc})")

    # 3. Restore the file half through checkpoints.py.
    file_result: dict[str, Any] = {}
    if snap.get("checkpoint_id"):
        try:
            from tools.agent_runtime.checkpoints import rollback as checkpoint_rollback

            # snapshot_current=False: the undo cycle above already checkpointed
            # exactly these paths, so a second one would be duplicate bytes.
            file_result = checkpoint_rollback(
                snap["checkpoint_id"], confirm=lambda _changes: True, snapshot_current=False
            )
        except Exception as exc:  # noqa: BLE001
            file_result = {"ok": False, "reason": str(exc)}
        applied.extend(f"files: {line}" for line in (file_result.get("applied") or []))

    # 4. Remove files that appeared during the cycle — only ones the undo
    #    checkpoint can hand back.
    removed: list[str] = []
    skipped: list[str] = []
    if remove_added_files:
        root = repo_root()
        for rel in _files_added_since(snapshot_paths, provider_names):
            if not _recoverable_from(undo.get("checkpoint_id"), rel):
                skipped.append(rel)
                continue
            try:
                (root / rel).unlink()
                removed.append(rel)
            except Exception as exc:  # noqa: BLE001
                logger.warning("refinement_cycle: cannot remove %s: %s", rel, exc)
                skipped.append(rel)
        applied.extend(f"files: removed {rel} (added during cycle)" for rel in removed)

    # 5. Record the rollback as an append-only row + chained audit row.
    record = record_refinement(
        cycle_id,
        CYCLE_PROVIDER,
        "rolled_back",
        target=cycle_id,
        actor=actor,
        details={
            "undo_cycle_id": undo["cycle_id"],
            "undo_checkpoint_id": undo.get("checkpoint_id"),
            "restored_state_hash": snap.get("state_hash"),
            "applied": applied,
            "files_removed": removed,
            "files_not_removed": skipped,
            "file_checkpoint": file_result.get("checkpoint"),
        },
        conn=conn,
    )

    logger.info(
        "refinement_cycle: rolled back %s (undo=%s, %d action(s))",
        cycle_id,
        undo["cycle_id"],
        len(applied),
    )
    return {
        "ok": True,
        "cycle_id": cycle_id,
        "undo_cycle_id": undo["cycle_id"],
        "applied": applied,
        "files_removed": removed,
        "files_not_removed": skipped,
        "audit_entry_id": record["audit_entry_id"],
    }


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
def verify_cycle(cycle_id: str, *, conn=None) -> dict[str, Any]:
    """Re-verify every audit row this cycle wrote, through the shared verifier.

    ``provenance_verifier.verify_audit_integrity`` recomputes the digest with the
    one recipe in ``tools/audit/row_hash.py`` and checks the link to the row
    before it, so a cycle whose rows all come back ``chained`` and ``ok`` is
    evidence the self-modification record has not been altered.
    """
    db, owned = _connect(conn)
    try:
        events: list[dict[str, Any]] = []
        if _has_table(db, SNAPSHOT_TABLE):
            for row in _rows(
                db,
                f"SELECT id, kind, audit_entry_id, created_at FROM {SNAPSHOT_TABLE} "
                "WHERE cycle_id = %s ORDER BY created_at ASC, id ASC",
                (cycle_id,),
            ):
                events.append(
                    {"row_id": row["id"], "type": f"snapshot:{row.get('kind')}",
                     "audit_entry_id": row.get("audit_entry_id")}
                )
        if _has_table(db, REFINEMENT_TABLE):
            for row in list_refinements(cycle_id, conn=db):
                events.append(
                    {"row_id": row["id"], "type": f"refinement:{row.get('provider')}/{row.get('action')}",
                     "audit_entry_id": row.get("audit_entry_id")}
                )
    finally:
        if owned:
            db.close()

    if not events:
        return {"ok": False, "cycle_id": cycle_id, "reason": "no rows for this cycle",
                "events": [], "verified": 0, "unaudited": 0, "failed": 0}

    from tools.blockchain.provenance_verifier import verify_audit_integrity

    verified = unaudited = failed = 0
    for event in events:
        entry_id = event.get("audit_entry_id")
        if not entry_id:
            event["verdict"] = "unaudited"
            unaudited += 1
            continue
        try:
            result = verify_audit_integrity(int(entry_id))
        except Exception as exc:  # noqa: BLE001
            event["verdict"] = "error"
            event["error"] = str(exc)
            failed += 1
            continue
        event["chain_status"] = result.get("chain_status")
        event["hash_valid"] = result.get("hash_valid")
        event["chain_valid"] = result.get("chain_valid")
        event["signature_valid"] = result.get("signature_valid")
        if result.get("hash_valid") and result.get("chain_valid"):
            event["verdict"] = "verified"
            verified += 1
        else:
            event["verdict"] = "unverified"
            failed += 1

    return {
        "ok": failed == 0 and unaudited == 0,
        "cycle_id": cycle_id,
        "status": cycle_status(cycle_id),
        "events": events,
        "verified": verified,
        "unaudited": unaudited,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Snapshot / roll back / verify the supplemental harness state."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_open = sub.add_parser("open", help="Snapshot supplemental state and start a cycle.")
    p_open.add_argument("--label", default="", help="Human label for the cycle.")
    p_open.add_argument("--actor", default="cli")
    p_open.add_argument("--providers", default="", help="Comma-separated subset.")

    p_list = sub.add_parser("list", help="List recent cycles, newest first.")
    p_list.add_argument("--limit", type=int, default=20)

    p_show = sub.add_parser("show", help="Show what a rollback of a cycle would do.")
    p_show.add_argument("cycle_id")

    p_back = sub.add_parser("rollback", help="Roll a cycle back as a unit.")
    p_back.add_argument("cycle_id")
    p_back.add_argument("--yes", action="store_true", help="Apply (default: preview only).")
    p_back.add_argument("--actor", default="cli")
    p_back.add_argument("--force", action="store_true", help="Repeat an already-rolled-back cycle.")

    p_verify = sub.add_parser("verify", help="Verify a cycle's chained audit rows.")
    p_verify.add_argument("cycle_id")

    for p in (p_open, p_list, p_show, p_back, p_verify):
        p.add_argument("--json", action="store_true", help="Machine-readable output.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "open":
        names = [n.strip() for n in args.providers.split(",") if n.strip()] or None
        result = open_cycle(args.label, actor=args.actor, provider_names=names)
    elif args.command == "list":
        result = {"cycles": list_cycles(args.limit)}
    elif args.command == "show":
        result = describe_rollback(args.cycle_id)
    elif args.command == "rollback":
        if not args.yes:
            result = describe_rollback(args.cycle_id)
            result["preview_only"] = True
            result["hint"] = "re-run with --yes to apply"
        else:
            result = rollback_cycle(args.cycle_id, actor=args.actor, force=args.force)
    else:
        result = verify_cycle(args.cycle_id)

    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
