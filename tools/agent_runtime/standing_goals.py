# CUI // SP-CTI
"""Standing goals for the standalone agent runtime (hgx-goal-01).

A *standing goal* is a durable objective that outlives a single session — "keep
the migration backlog at zero", "always propose a test before a fix". While a
goal is ``active`` the runtime re-injects it into the system prompt, so the agent
keeps pursuing it across turns and across sessions.

This module owns three things and nothing else:

* :class:`GoalStatus` — the closed lifecycle vocabulary
  (pending/active/paused/blocked/completed/cancelled) **and** the transition
  table. Every state change goes through :meth:`GoalManager.transition`, so an
  illegal move (``completed`` → ``active``, say) is rejected in one place rather
  than re-checked at each call site.
* :class:`StandingGoal` — a plain dataclass row mapping.
* :class:`GoalManager` — CRUD plus the lifecycle verbs.

Design notes
------------
**Storage.** ``sag_standing_goals`` (migration ``20260808161736``), reached only
through :func:`tools.db.storage.get_connection` so RLS applies; the table carries
first-class ``user_id`` / ``tenant_id`` / ``classification`` columns. Structured
fields (``tags``, ``metadata``) are JSON TEXT parsed in Python — never with
``json_extract``/``json_each``, which is SQLite-only dialect.

**Degradation is the contract.** These tables are intentionally absent from
``tests/conftest.py::MINIMAL_ICDEV_SCHEMA``, and a checkout may not have run the
migration. So :meth:`GoalManager` self-creates the table via ``_ensure_schema()``
and, when the DB or the table is unavailable anyway, every method returns an
empty result (``None`` / ``[]`` / ``False``) instead of raising. The agent must
stay usable without the goals subsystem. The *one* deliberate exception is
:exc:`InvalidGoalTransition`: an illegal lifecycle move is a caller error, not a
missing subsystem, and silently swallowing it would let ``/goal complete`` claim
success on a cancelled goal.

**No LLM.** Goal text is operator-authored; nothing here calls a model, so there
is no provider coupling to degrade.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.standing_goals")

_TABLE = "sag_standing_goals"
_DEFAULT_USER = "default"
_DEFAULT_PRIORITY = 50
_MAX_TITLE = 300

_DDL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    goal_id        TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL DEFAULT 'default',
    tenant_id      TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    title          TEXT NOT NULL,
    detail         TEXT DEFAULT '',
    status         TEXT DEFAULT 'pending',
    priority       INTEGER DEFAULT 50,
    progress       INTEGER DEFAULT 0,
    context_id     TEXT DEFAULT '',
    session_id     TEXT DEFAULT '',
    tags_json      TEXT DEFAULT '[]',
    metadata_json  TEXT DEFAULT '{{}}',
    blocked_reason TEXT DEFAULT '',
    created_at     TEXT,
    updated_at     TEXT,
    activated_at   TEXT,
    completed_at   TEXT
)
"""

#: Column order used by every SELECT/INSERT here. Kept as one list so the row
#: mapper and the INSERT cannot drift apart.
_COLUMNS = [
    "goal_id", "user_id", "tenant_id", "classification", "title", "detail",
    "status", "priority", "progress", "context_id", "session_id", "tags_json",
    "metadata_json", "blocked_reason", "created_at", "updated_at",
    "activated_at", "completed_at",
]
_SELECT_COLS = ", ".join(_COLUMNS)


class GoalStatus(str, Enum):
    """Closed lifecycle vocabulary for a standing goal."""

    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @classmethod
    def coerce(cls, value: "GoalStatus | str") -> "GoalStatus":
        """Return the enum member for ``value``; raise ValueError if unknown."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            raise ValueError(
                f"unknown goal status {value!r} "
                f"(expected one of {', '.join(s.value for s in cls)})"
            ) from None


#: Terminal states — nothing leaves them. A finished goal is history; re-opening
#: one would lose the record of when it finished, so the caller creates a new goal.
TERMINAL_STATUSES: frozenset[GoalStatus] = frozenset(
    {GoalStatus.COMPLETED, GoalStatus.CANCELLED}
)

#: Statuses whose goals are injected into the system prompt / shown as "in play".
LIVE_STATUSES: tuple[GoalStatus, ...] = (GoalStatus.ACTIVE, GoalStatus.BLOCKED)

#: The transition table. A goal may always be cancelled while it is non-terminal.
_TRANSITIONS: dict[GoalStatus, frozenset[GoalStatus]] = {
    GoalStatus.PENDING: frozenset({GoalStatus.ACTIVE, GoalStatus.CANCELLED}),
    GoalStatus.ACTIVE: frozenset({
        GoalStatus.PAUSED, GoalStatus.BLOCKED, GoalStatus.COMPLETED,
        GoalStatus.CANCELLED,
    }),
    GoalStatus.PAUSED: frozenset({GoalStatus.ACTIVE, GoalStatus.CANCELLED}),
    GoalStatus.BLOCKED: frozenset({
        GoalStatus.ACTIVE, GoalStatus.PAUSED, GoalStatus.CANCELLED,
    }),
    GoalStatus.COMPLETED: frozenset(),
    GoalStatus.CANCELLED: frozenset(),
}


class InvalidGoalTransition(ValueError):
    """Raised when a lifecycle move is not permitted by :data:`_TRANSITIONS`."""

    def __init__(self, current: GoalStatus, requested: GoalStatus):
        self.current = current
        self.requested = requested
        allowed = sorted(s.value for s in _TRANSITIONS.get(current, frozenset()))
        super().__init__(
            f"cannot move goal from {current.value} to {requested.value}"
            + (f" (allowed: {', '.join(allowed)})" if allowed
               else f" ({current.value} is terminal)")
        )


def can_transition(current: "GoalStatus | str", requested: "GoalStatus | str") -> bool:
    """True when ``current`` → ``requested`` is a legal lifecycle move.

    A no-op move (``active`` → ``active``) is legal so that idempotent callers
    — a retried ``/goal resume``, say — do not fail.
    """
    try:
        cur = GoalStatus.coerce(current)
        req = GoalStatus.coerce(requested)
    except ValueError:
        return False
    if cur == req:
        return True
    return req in _TRANSITIONS.get(cur, frozenset())


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_goal_id() -> str:
    return f"goal-{uuid.uuid4().hex[:12]}"


def _as_int(value: Any, fallback: int) -> int:
    """Int coercion that keeps a legitimate 0 (unlike ``value or fallback``)."""
    if value is None or value == "":
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _clamp_progress(value: Any) -> int:
    try:
        pct = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, pct))


def _loads(raw: Any, fallback: Any) -> Any:
    if raw in (None, ""):
        return fallback
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed is not None else fallback


@dataclass
class StandingGoal:
    """One durable objective."""

    goal_id: str
    title: str
    detail: str = ""
    status: GoalStatus = GoalStatus.PENDING
    priority: int = _DEFAULT_PRIORITY
    progress: int = 0
    user_id: str = _DEFAULT_USER
    tenant_id: str = ""
    classification: str = "CUI"
    context_id: str = ""
    session_id: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    blocked_reason: str = ""
    created_at: str = ""
    updated_at: str = ""
    activated_at: str = ""
    completed_at: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe mapping (status flattened to its string value)."""
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "detail": self.detail,
            "status": self.status.value,
            "priority": self.priority,
            "progress": self.progress,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "classification": self.classification,
            "context_id": self.context_id,
            "session_id": self.session_id,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "blocked_reason": self.blocked_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "activated_at": self.activated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_row(cls, row: Iterable[Any]) -> Optional["StandingGoal"]:
        """Map a ``_COLUMNS``-ordered DB row; None if it cannot be understood."""
        values = list(row)
        if len(values) < len(_COLUMNS):
            return None
        data = dict(zip(_COLUMNS, values))
        try:
            status = GoalStatus.coerce(data.get("status") or GoalStatus.PENDING)
        except ValueError:
            # A row written by a newer/older version. Surfacing it as pending is
            # better than dropping the operator's goal on the floor.
            logger.debug("standing_goals: unknown status %r", data.get("status"))
            status = GoalStatus.PENDING
        return cls(
            goal_id=str(data["goal_id"]),
            title=str(data.get("title") or ""),
            detail=str(data.get("detail") or ""),
            status=status,
            # `or _DEFAULT_PRIORITY` would silently rewrite a legitimate
            # priority of 0 into 50 and reorder the list.
            priority=_as_int(data.get("priority"), _DEFAULT_PRIORITY),
            progress=_clamp_progress(data.get("progress")),
            user_id=str(data.get("user_id") or _DEFAULT_USER),
            tenant_id=str(data.get("tenant_id") or ""),
            classification=str(data.get("classification") or "CUI"),
            context_id=str(data.get("context_id") or ""),
            session_id=str(data.get("session_id") or ""),
            tags=[str(t) for t in _loads(data.get("tags_json"), [])],
            metadata=_loads(data.get("metadata_json"), {}) or {},
            blocked_reason=str(data.get("blocked_reason") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            activated_at=str(data.get("activated_at") or ""),
            completed_at=str(data.get("completed_at") or ""),
        )


class GoalManager:
    """CRUD + lifecycle for standing goals, scoped to one ``(user, tenant)``.

    Every method degrades: with the DB unreachable or the table missing, reads
    return empty and writes return ``None``/``False``. Only an illegal lifecycle
    move raises (:exc:`InvalidGoalTransition`).
    """

    def __init__(
        self,
        user_id: str = _DEFAULT_USER,
        tenant_id: str = "",
        *,
        classification: str = "CUI",
    ):
        self.user_id = user_id or _DEFAULT_USER
        self.tenant_id = tenant_id or ""
        self.classification = classification or "CUI"

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def _conn(self):
        """Return an RLS-aware connection with the schema ensured, or None."""
        try:
            from tools.db.storage import get_connection

            conn = get_connection()
        except Exception as exc:  # noqa: BLE001
            logger.debug("standing_goals: no DB connection: %s", exc)
            return None
        self._ensure_schema(conn)
        return conn

    @staticmethod
    def _ensure_schema(conn) -> None:
        """Self-create the table; best-effort so a read-only DB still works."""
        try:
            conn.execute(_DDL)
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("standing_goals: ensure_schema failed: %s", exc)

    def _scope(self) -> tuple[str, str]:
        return (self.user_id, self.tenant_id)

    # ------------------------------------------------------------------
    # Create / read
    # ------------------------------------------------------------------
    def create(
        self,
        title: str,
        *,
        detail: str = "",
        status: "GoalStatus | str" = GoalStatus.PENDING,
        priority: int = _DEFAULT_PRIORITY,
        context_id: str = "",
        session_id: str = "",
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        goal_id: Optional[str] = None,
    ) -> Optional[StandingGoal]:
        """Persist a new goal and return it (None when the DB is unavailable).

        ``title`` is required — an untitled goal cannot be rendered into a system
        prompt, so it is rejected up front rather than stored as noise.
        """
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("a standing goal needs a title")
        initial = GoalStatus.coerce(status)
        if initial in TERMINAL_STATUSES:
            raise ValueError(f"cannot create a goal already {initial.value}")

        now = _utcnow()
        goal = StandingGoal(
            goal_id=goal_id or _new_goal_id(),
            title=clean_title[:_MAX_TITLE],
            detail=detail or "",
            status=initial,
            priority=int(priority),
            progress=0,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            classification=self.classification,
            context_id=context_id or "",
            session_id=session_id or "",
            tags=[str(t) for t in (tags or [])],
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
            activated_at=now if initial is GoalStatus.ACTIVE else "",
        )

        conn = self._conn()
        if conn is None:
            return None
        try:
            conn.execute(
                f"INSERT INTO {_TABLE} ({_SELECT_COLS}) VALUES "
                f"({', '.join(['%s'] * len(_COLUMNS))})",
                (
                    goal.goal_id, goal.user_id, goal.tenant_id, goal.classification,
                    goal.title, goal.detail, goal.status.value, goal.priority,
                    goal.progress, goal.context_id, goal.session_id,
                    json.dumps(goal.tags), json.dumps(goal.metadata),
                    goal.blocked_reason, goal.created_at, goal.updated_at,
                    goal.activated_at, goal.completed_at,
                ),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("standing_goals: create failed: %s", exc)
            return None
        return goal

    def get(self, goal_id: str) -> Optional[StandingGoal]:
        """Return one goal in this scope, or None (missing / DB unavailable)."""
        conn = self._conn()
        if conn is None:
            return None
        try:
            cur = conn.execute(
                f"SELECT {_SELECT_COLS} FROM {_TABLE} "
                f"WHERE goal_id = %s AND user_id = %s AND tenant_id = %s",
                (goal_id, *self._scope()),
            )
            row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001
            logger.debug("standing_goals: get failed: %s", exc)
            return None
        return StandingGoal.from_row(row) if row else None

    def list_goals(
        self,
        *,
        statuses: Optional[Iterable["GoalStatus | str"]] = None,
        context_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[StandingGoal]:
        """List goals in this scope, most important (then newest) first.

        Filtering is done in SQL for the scope and in Python for status/context,
        keeping the query dialect-neutral and the JSON columns untouched.
        """
        conn = self._conn()
        if conn is None:
            return []
        try:
            cur = conn.execute(
                f"SELECT {_SELECT_COLS} FROM {_TABLE} "
                f"WHERE user_id = %s AND tenant_id = %s",
                self._scope(),
            )
            rows = cur.fetchall() or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("standing_goals: list failed: %s", exc)
            return []

        wanted: Optional[set[GoalStatus]] = None
        if statuses is not None:
            wanted = set()
            for s in statuses:
                try:
                    wanted.add(GoalStatus.coerce(s))
                except ValueError:
                    continue

        goals: list[StandingGoal] = []
        for row in rows:
            goal = StandingGoal.from_row(row)
            if goal is None:
                continue
            if wanted is not None and goal.status not in wanted:
                continue
            if context_id is not None and goal.context_id != context_id:
                continue
            goals.append(goal)

        goals.sort(key=lambda g: (-g.priority, g.created_at, g.goal_id))
        return goals[:limit] if limit else goals

    def list_active(self, limit: Optional[int] = None) -> list[StandingGoal]:
        """Goals currently being pursued (``active`` only)."""
        return self.list_goals(statuses=(GoalStatus.ACTIVE,), limit=limit)

    def list_for_context(
        self,
        context_id: str,
        *,
        include_global: bool = True,
        statuses: Optional[Iterable["GoalStatus | str"]] = LIVE_STATUSES,
        limit: Optional[int] = None,
    ) -> list[StandingGoal]:
        """Goals in play for one conversation/session context.

        A standing goal is cross-cutting by nature, so goals with no
        ``context_id`` (global to the operator) are included by default alongside
        the ones pinned to ``context_id``. Pass ``include_global=False`` for the
        strictly-scoped list. ``statuses=None`` returns every status.
        """
        goals = self.list_goals(statuses=statuses)
        target = context_id or ""
        scoped = [
            g for g in goals
            if g.context_id == target or (include_global and not g.context_id)
        ]
        return scoped[:limit] if limit else scoped

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def transition(
        self,
        goal_id: str,
        new_status: "GoalStatus | str",
        *,
        blocked_reason: str = "",
        progress: Optional[int] = None,
    ) -> Optional[StandingGoal]:
        """Move a goal to ``new_status``.

        Returns the updated goal, or None when it does not exist or the DB is
        unavailable. Raises :exc:`InvalidGoalTransition` when the move is illegal
        — that is a caller bug, not a missing subsystem, and swallowing it would
        let a command report success on a goal it never changed.
        """
        target = GoalStatus.coerce(new_status)
        goal = self.get(goal_id)
        if goal is None:
            return None
        if not can_transition(goal.status, target):
            raise InvalidGoalTransition(goal.status, target)

        now = _utcnow()
        goal.status = target
        goal.updated_at = now
        if target is GoalStatus.ACTIVE and not goal.activated_at:
            goal.activated_at = now
        if target is GoalStatus.BLOCKED:
            goal.blocked_reason = blocked_reason or goal.blocked_reason
        elif target is GoalStatus.ACTIVE:
            goal.blocked_reason = ""
        if target in TERMINAL_STATUSES:
            goal.completed_at = now
        if target is GoalStatus.COMPLETED and progress is None:
            progress = 100
        if progress is not None:
            goal.progress = _clamp_progress(progress)

        conn = self._conn()
        if conn is None:
            return None
        try:
            conn.execute(
                f"UPDATE {_TABLE} SET status = %s, progress = %s, "
                f"blocked_reason = %s, updated_at = %s, activated_at = %s, "
                f"completed_at = %s "
                f"WHERE goal_id = %s AND user_id = %s AND tenant_id = %s",
                (
                    goal.status.value, goal.progress, goal.blocked_reason,
                    goal.updated_at, goal.activated_at, goal.completed_at,
                    goal.goal_id, *self._scope(),
                ),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("standing_goals: transition failed: %s", exc)
            return None
        return goal

    def activate(self, goal_id: str) -> Optional[StandingGoal]:
        """Start (or resume) pursuing a goal."""
        return self.transition(goal_id, GoalStatus.ACTIVE)

    def pause(self, goal_id: str) -> Optional[StandingGoal]:
        """Stop injecting a goal without ending it."""
        return self.transition(goal_id, GoalStatus.PAUSED)

    def complete(self, goal_id: str) -> Optional[StandingGoal]:
        """Finish a goal (progress is forced to 100)."""
        return self.transition(goal_id, GoalStatus.COMPLETED)

    def block(self, goal_id: str, reason: str = "") -> Optional[StandingGoal]:
        """Mark a goal blocked, recording why."""
        return self.transition(goal_id, GoalStatus.BLOCKED, blocked_reason=reason)

    def cancel(self, goal_id: str) -> Optional[StandingGoal]:
        """Abandon a goal. Terminal — a cancelled goal cannot be revived."""
        return self.transition(goal_id, GoalStatus.CANCELLED)

    # ------------------------------------------------------------------
    # Progress / delete
    # ------------------------------------------------------------------
    def update_progress(
        self, goal_id: str, progress: Any, *, note: str = ""
    ) -> Optional[StandingGoal]:
        """Set percent-complete (clamped to 0-100) without changing status.

        ``note`` is appended to ``metadata['progress_notes']`` so the history of
        how a goal advanced survives; reaching 100 does NOT auto-complete — that
        stays an explicit operator act.
        """
        goal = self.get(goal_id)
        if goal is None:
            return None
        if goal.is_terminal:
            raise ValueError(
                f"cannot record progress on a {goal.status.value} goal"
            )

        goal.progress = _clamp_progress(progress)
        goal.updated_at = _utcnow()
        if note:
            notes = goal.metadata.get("progress_notes")
            if not isinstance(notes, list):
                notes = []
            notes.append({"at": goal.updated_at, "progress": goal.progress, "note": note})
            goal.metadata["progress_notes"] = notes[-20:]

        conn = self._conn()
        if conn is None:
            return None
        try:
            conn.execute(
                f"UPDATE {_TABLE} SET progress = %s, metadata_json = %s, "
                f"updated_at = %s "
                f"WHERE goal_id = %s AND user_id = %s AND tenant_id = %s",
                (
                    goal.progress, json.dumps(goal.metadata), goal.updated_at,
                    goal.goal_id, *self._scope(),
                ),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("standing_goals: update_progress failed: %s", exc)
            return None
        return goal

    def delete(self, goal_id: str) -> bool:
        """Hard-delete a goal. True when a row was removed.

        Goals are operator working state, not an audit record, so this is a real
        DELETE — use :meth:`cancel` to keep the history.
        """
        if self.get(goal_id) is None:
            return False
        conn = self._conn()
        if conn is None:
            return False
        try:
            conn.execute(
                f"DELETE FROM {_TABLE} "
                f"WHERE goal_id = %s AND user_id = %s AND tenant_id = %s",
                (goal_id, *self._scope()),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("standing_goals: delete failed: %s", exc)
            return False
        return True

    # ------------------------------------------------------------------
    # Rendering (used by the /goal command layer and prompt injection)
    # ------------------------------------------------------------------
    def render_active(self, *, limit: int = 5) -> str:
        """Compact block of the top active goals, or '' when there are none.

        Capped so a long goal list cannot crowd the context window.
        """
        goals = self.list_active(limit=limit)
        if not goals:
            return ""
        lines = ["## Standing goals"]
        for g in goals:
            suffix = f" [{g.progress}%]" if g.progress else ""
            lines.append(f"  - {g.title}{suffix}")
            if g.detail:
                lines.append(f"      {g.detail.strip()[:200]}")
        return "\n".join(lines)
