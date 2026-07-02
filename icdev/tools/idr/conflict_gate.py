# CUI // SP-CTI
"""IDRConflictGate — blocks MERGE_CONFLICT → IN_PROGRESS until a human resolves all conflicts.

Usage
-----
    gate = IDRConflictGate()

    # When a worktree/CI conflict is detected:
    cid = gate.record_conflict(task_id, "merge_conflict", "Branch diverged: 3 files conflicted")

    # Gate check before allowing resume_from_conflict transition:
    if gate.has_unresolved_conflicts(task_id):
        raise ConflictNotResolved(task_id)

    # HITL resolution (human reviewer):
    gate.resolve_conflict(task_id, cid, resolver="alice", note="Rebased onto main, conflicts cleared")

Conflicts are persisted in ``kanban_tasks.hitl_stage`` as a JSON envelope so no schema
migration is required.  The column already exists (migration 040_kanban_bypass_flag).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_KEY = "idr_conflicts"


class ConflictNotResolved(Exception):
    """Raised when a task still has unresolved IDR conflicts and auto-resume is blocked."""


class IDRConflictGate:
    """HITL gate that serialises merge/CI conflicts and requires human approval to clear."""

    # ------------------------------------------------------------------ read

    def get_conflicts(self, task_id: str) -> List[Dict]:
        """Return all IDR conflict records for *task_id* (resolved + unresolved)."""
        return self._load(task_id)

    def has_unresolved_conflicts(self, task_id: str) -> bool:
        """Return True if *task_id* has at least one unresolved IDR conflict."""
        return any(not c.get("resolved", False) for c in self._load(task_id))

    def can_resume(self, task_id: str) -> bool:
        """Return True when all conflicts are resolved (safe to transition to IN_PROGRESS)."""
        conflicts = self._load(task_id)
        return not conflicts or all(c.get("resolved", False) for c in conflicts)

    # ------------------------------------------------------------------ write

    def record_conflict(
        self,
        task_id: str,
        conflict_type: str,
        details: str = "",
    ) -> str:
        """Register a new conflict and return its ``conflict_id``.

        Parameters
        ----------
        task_id:
            Kanban task PK.
        conflict_type:
            Short label, e.g. ``"merge_conflict"``, ``"ci_failure"``,
            ``"changes_requested"``.
        details:
            Human-readable description of the conflict.

        Returns
        -------
        str
            UUID string identifying this conflict record.
        """
        conflicts = self._load(task_id)
        cid = str(uuid.uuid4())
        conflicts.append(
            {
                "id": cid,
                "type": conflict_type,
                "details": details,
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "resolved": False,
                "resolved_by": None,
                "resolved_at": None,
                "resolution_note": None,
            }
        )
        self._save(task_id, conflicts)
        logger.info("IDR conflict recorded task=%s cid=%s type=%s", task_id, cid, conflict_type)
        return cid

    def resolve_conflict(
        self,
        task_id: str,
        conflict_id: str,
        resolver: str,
        note: str = "",
    ) -> bool:
        """Mark a specific conflict as resolved (HITL approval).

        Parameters
        ----------
        task_id:
            Kanban task PK.
        conflict_id:
            UUID returned by :meth:`record_conflict`.
        resolver:
            Human actor who resolved the conflict (username / agent ID).
        note:
            Optional resolution note explaining what was done.

        Returns
        -------
        bool
            True if the conflict was found and updated, False if not found.
        """
        conflicts = self._load(task_id)
        found = False
        for c in conflicts:
            if c["id"] == conflict_id:
                c["resolved"] = True
                c["resolved_by"] = resolver
                c["resolved_at"] = datetime.now(timezone.utc).isoformat()
                c["resolution_note"] = note
                found = True
                break
        if found:
            self._save(task_id, conflicts)
            logger.info(
                "IDR conflict resolved task=%s cid=%s resolver=%s",
                task_id,
                conflict_id,
                resolver,
            )
        else:
            logger.warning("IDR conflict_id=%s not found for task=%s", conflict_id, task_id)
        return found

    def resolve_all(self, task_id: str, resolver: str, note: str = "") -> int:
        """Resolve every outstanding conflict for *task_id*. Returns count resolved."""
        conflicts = self._load(task_id)
        count = 0
        now = datetime.now(timezone.utc).isoformat()
        for c in conflicts:
            if not c.get("resolved", False):
                c["resolved"] = True
                c["resolved_by"] = resolver
                c["resolved_at"] = now
                c["resolution_note"] = note
                count += 1
        if count:
            self._save(task_id, conflicts)
            logger.info("IDR resolve_all task=%s count=%d resolver=%s", task_id, count, resolver)
        return count

    def clear_conflicts(self, task_id: str) -> None:
        """Remove all conflict records for *task_id* (e.g., after task is fully done)."""
        self._save(task_id, [])

    # ------------------------------------------------------------------ guard

    def assert_can_resume(self, task_id: str) -> None:
        """Raise :class:`ConflictNotResolved` if unresolved conflicts block resume.

        Call this immediately before emitting the ``resume_from_conflict``
        transition so the state machine never advances with open conflicts.
        """
        if self.has_unresolved_conflicts(task_id):
            unresolved = [c for c in self._load(task_id) if not c.get("resolved", False)]
            raise ConflictNotResolved(
                f"Task {task_id} has {len(unresolved)} unresolved IDR conflict(s); "
                "HITL approval required before resume."
            )

    # ------------------------------------------------------------------ private

    def _load(self, task_id: str) -> List[Dict]:
        """Load conflict list from ``kanban_tasks.hitl_stage``. Returns [] on any error."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT hitl_stage FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
            if not row or not row[0]:
                return []
            envelope = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            return envelope.get(_KEY, []) if isinstance(envelope, dict) else []
        except Exception as exc:
            logger.debug("IDR _load error task=%s: %s", task_id, exc)
            return []
        finally:
            conn.close()

    def _save(self, task_id: str, conflicts: List[Dict]) -> None:
        """Persist *conflicts* back into ``kanban_tasks.hitl_stage``."""
        conn = get_connection()
        try:
            # Merge with any existing hitl_stage envelope so we don't clobber
            # other keys set by different subsystems.
            row = conn.execute(
                "SELECT hitl_stage FROM kanban_tasks WHERE id = %s",
                (task_id,),
            ).fetchone()
            if row and row[0]:
                envelope = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                if not isinstance(envelope, dict):
                    envelope = {}
            else:
                envelope = {}
            envelope[_KEY] = conflicts
            conn.execute(
                "UPDATE kanban_tasks SET hitl_stage = %s, updated_at = %s WHERE id = %s",
                (json.dumps(envelope), datetime.now(timezone.utc).isoformat(), task_id),
            )
            conn.commit()
        except Exception as exc:
            logger.error("IDR _save error task=%s: %s", task_id, exc)
            raise
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Convenience guard for use in kanban auto-remediator / pr_watcher hooks
# ---------------------------------------------------------------------------

_default_gate: Optional[IDRConflictGate] = None


def get_gate() -> IDRConflictGate:
    """Return the singleton IDRConflictGate instance."""
    global _default_gate
    if _default_gate is None:
        _default_gate = IDRConflictGate()
    return _default_gate
