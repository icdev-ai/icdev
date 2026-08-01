# CUI // SP-CTI
"""FORGE Academy — instructor and cohort workflow (aca-trn-04).

Before this module the Academy could measure a training programme but not run
one. ``/academy/org-readiness`` reported cohort counts, skill gaps and a
composite score to the leadership tier; nothing anywhere could *assign* a
mission, set a due date, look at what a learner actually submitted, or record a
human verdict on it. Every route was per-learner and self-service.

Three tables carry the workflow:

``fa_assignments``
    One row per assignment. The target is either a single learner
    (``target_type='learner'``) or a cohort (``target_type='cohort'`` plus a
    role token, or ``all``). Cohort membership is resolved **at read time**,
    not frozen at assign time: a learner who enrols into the role afterwards
    inherits the assignment, which is what "assign the SecOps track to SecOps"
    means to the person typing it. The scope is either one mission or a
    *track* — every active mission whose ``role_filter`` covers a role token.

``fa_instructor_reviews``
    One row per human verdict. A review may carry an ``override_score``.

``fa_instructor_audit``
    Append-only. Every assign / cancel / review lands here with the actor, so
    an override is attributable. Registered in ``APPEND_ONLY_TABLES``.

**An override changes the score, never the XP.** ``fa_mission_progress`` keeps
``score`` and ``xp_earned`` as separate columns, and XP in this codebase is
machine-earned with a provenance row in ``fa_xp_ledger`` for every point
(aca-int-07). Letting an instructor mint XP would reopen exactly the hole that
card closed — a rank bought rather than demonstrated — and would do it through
a surface with no test behind it. So an override records a human judgement about
a score and says so; the ledger stays a record of what was actually graded.

Authorisation is NOT re-invented here. Every caller is a route wearing
``@require_org_intel`` (admin/pm/isso), the same gate Org Readiness and the
Oracle already use. This module additionally scopes every read and write by
``tenant_id`` so an instructor in one tenant cannot see or grade another's
learners.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from tools.db.storage import get_connection

from .constants import ROLES
from .db import role_matches

_log = logging.getLogger(__name__)

# What an instructor may target. A cohort is a role token (or "all"); a learner
# is one fa_users.id.
TARGET_TYPES = frozenset({"learner", "cohort"})

# What may be assigned. "mission" is one fa_missions row; "track" is every
# active mission whose role_filter covers a role token.
ASSIGNMENT_TYPES = frozenset({"mission", "track"})

# Verdicts an instructor may record. A value outside this set is a bug in the
# caller, not a new category — adding one means deciding how the roster renders
# it, which is what this set exists to force.
REVIEW_VERDICTS = frozenset({"approved", "needs_rework", "rejected"})

ASSIGNMENT_STATUSES = frozenset({"open", "cancelled"})

# Cohort token meaning "every enrolled learner in the tenant".
COHORT_ALL = "all"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tenant_clause(alias: str, tenant_id: str | None, params: list) -> str:
    """SQL fragment scoping ``alias`` to a tenant.

    A NULL tenant and an empty-string tenant are the same population: single-
    tenant installs write NULL, while ``refresh_leaderboard_cache`` and the SaaS
    middleware have both written ''. Treating them as different would split one
    real cohort into two invisible halves.
    """
    if tenant_id:
        params.append(tenant_id)
        return f"{alias}.tenant_id=%s"
    return f"({alias}.tenant_id IS NULL OR {alias}.tenant_id='')"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def log_audit(action: str, actor: str, *, actor_role: str = "",
              subject_type: str = "", subject_id: str = "",
              detail: dict | None = None, tenant_id: str | None = None,
              conn=None) -> None:
    """Append one immutable audit row.

    Never raises: an audit write that fails must not roll back a grade the
    instructor already saw succeed. It logs at error level instead, so a broken
    audit trail is loud rather than invisible.
    """
    own = conn is None
    try:
        if own:
            conn = get_connection()
        conn.execute(
            "INSERT INTO fa_instructor_audit "
            "(action, actor, actor_role, subject_type, subject_id, detail_json, "
            " tenant_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (action, actor, actor_role or "", subject_type or "",
             str(subject_id or ""), json.dumps(detail or {}, default=str),
             tenant_id, _now()),
        )
        if own:
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        _log.error("instructor audit write failed for %s by %s: %s",
                   action, actor, exc, exc_info=True)


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def roster(tenant_id: str | None = None, role: str | None = None) -> list[dict]:
    """Every enrolled learner in the tenant, with progress and open work.

    ``role='unset'`` learners (signed in, never picked a role) are included
    deliberately — "who has not started" is the first question an instructor
    asks, and the leaderboard's ``role != 'unset'`` filter is precisely why they
    were invisible.
    """
    conn = get_connection()
    params: list = []
    q = (
        "SELECT u.id, u.username, u.display_name, u.role, u.xp, u.level, "
        "       u.streak_days, u.last_active, u.guild_id, u.tenant_id "
        "FROM fa_users u WHERE " + _tenant_clause("u", tenant_id, params)
    )
    if role:
        q += " AND u.role=%s"
        params.append(role)
    q += " ORDER BY u.xp DESC, u.display_name"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]

    total_missions = _active_mission_count(conn)
    for row in rows:
        row.update(_learner_counters(conn, row["id"]))
        row["total_missions"] = total_missions
        row["open_assignments"] = 0
        row["overdue_assignments"] = 0

    by_id = {r["id"]: r for r in rows}
    if by_id:
        for item in list_assignments(tenant_id=tenant_id, include_cancelled=False):
            for target in item.get("targets", []):
                learner = by_id.get(target["user_id"])
                if learner is None:
                    continue
                if target.get("complete"):
                    continue
                learner["open_assignments"] += 1
                if item.get("overdue"):
                    learner["overdue_assignments"] += 1
    return rows


def _active_mission_count(conn) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM fa_missions WHERE is_active=1"
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def _learner_counters(conn, user_id: int) -> dict:
    """Completed missions, completed steps and pending submissions for a learner."""
    out = {"missions_completed": 0, "steps_completed": 0, "submissions": 0,
           "reviews": 0}
    try:
        out["missions_completed"] = int(conn.execute(
            "SELECT COUNT(*) FROM fa_mission_progress "
            "WHERE user_id=%s AND status='completed'", (user_id,),
        ).fetchone()[0])
        out["steps_completed"] = int(conn.execute(
            "SELECT COUNT(*) FROM fa_step_progress "
            "WHERE user_id=%s AND status='completed'", (user_id,),
        ).fetchone()[0])
        out["submissions"] = int(conn.execute(
            "SELECT COUNT(*) FROM fa_step_progress "
            "WHERE user_id=%s AND submission IS NOT NULL AND submission <> ''",
            (user_id,),
        ).fetchone()[0])
        out["reviews"] = int(conn.execute(
            "SELECT COUNT(*) FROM fa_instructor_reviews WHERE user_id=%s",
            (user_id,),
        ).fetchone()[0])
    except Exception as exc:  # noqa: BLE001
        _log.debug("learner counters failed for %s: %s", user_id, exc)
    return out


def get_learner(user_id: int, tenant_id: str | None = None) -> dict | None:
    """One learner, or None when they do not exist IN THIS TENANT.

    The tenant check is the authorisation boundary for every per-learner route
    below, so it returns None rather than raising: the caller 404s and an
    instructor cannot probe another tenant's id space by response code.
    """
    conn = get_connection()
    params: list = [user_id]
    row = conn.execute(
        "SELECT * FROM fa_users u WHERE u.id=%s AND "
        + _tenant_clause("u", tenant_id, params),
        params,
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Mission scope
# ---------------------------------------------------------------------------

def _active_missions(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, slug, title, tier, topic, role_filter, xp_reward "
        "FROM fa_missions WHERE is_active=1 ORDER BY tier, order_idx, title"
    ).fetchall()
    return [dict(r) for r in rows]


def track_missions(track_key: str, conn=None) -> list[dict]:
    """Every active mission whose ``role_filter`` covers ``track_key``.

    Filtered in Python via ``role_matches`` rather than in SQL: the column is a
    comma-joined TEXT list and a ``LIKE '%swe%'`` also matches ``swe_arch``,
    which is the aca-hyg-02 defect. One comparison, one place.
    """
    conn = conn or get_connection()
    if track_key == COHORT_ALL:
        return _active_missions(conn)
    return [m for m in _active_missions(conn)
            if role_matches(m.get("role_filter"), track_key)]


def assignment_missions(assignment: dict, conn=None) -> list[dict]:
    """The missions an assignment covers."""
    conn = conn or get_connection()
    if assignment.get("assignment_type") == "track":
        return track_missions(assignment.get("track_key") or COHORT_ALL, conn)
    mission_id = assignment.get("mission_id")
    if not mission_id:
        return []
    row = conn.execute(
        "SELECT id, slug, title, tier, topic, role_filter, xp_reward "
        "FROM fa_missions WHERE id=%s", (mission_id,),
    ).fetchone()
    return [dict(row)] if row else []


def assignment_targets(assignment: dict, conn=None) -> list[dict]:
    """The learners an assignment applies to, resolved now.

    Cohort membership is deliberately not frozen at assign time — see the module
    docstring. A learner assigned individually is returned even if their role
    later changes; that assignment named *them*.
    """
    conn = conn or get_connection()
    tenant_id = assignment.get("tenant_id")
    if assignment.get("target_type") == "learner":
        learner = get_learner(assignment.get("target_user_id"), tenant_id)
        return [learner] if learner else []
    role = assignment.get("target_role") or COHORT_ALL
    return roster_lite(tenant_id, None if role == COHORT_ALL else role, conn)


def roster_lite(tenant_id: str | None, role: str | None, conn=None) -> list[dict]:
    """Learner identity rows only — no counters. Used inside assignment fan-out."""
    conn = conn or get_connection()
    params: list = []
    q = ("SELECT u.id, u.username, u.display_name, u.role, u.xp "
         "FROM fa_users u WHERE " + _tenant_clause("u", tenant_id, params))
    if role:
        q += " AND u.role=%s"
        params.append(role)
    q += " ORDER BY u.display_name"
    return [dict(r) for r in conn.execute(q, params).fetchall()]


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

class AssignmentError(ValueError):
    """A rejected assignment request. The message is safe to show the caller."""


def create_assignment(*, assigned_by: str, assignment_type: str = "mission",
                      mission_id: int | None = None, track_key: str | None = None,
                      target_type: str = "learner", target_user_id: int | None = None,
                      target_role: str | None = None, due_at: str | None = None,
                      note: str = "", tenant_id: str | None = None,
                      actor_role: str = "") -> dict:
    """Validate and persist one assignment, then audit it.

    Raises ``AssignmentError`` with a caller-safe message on any invalid input.
    Nothing is written when validation fails, so a rejected request leaves no
    audit row claiming an assignment that does not exist.
    """
    assignment_type = (assignment_type or "mission").strip().lower()
    target_type = (target_type or "learner").strip().lower()
    if assignment_type not in ASSIGNMENT_TYPES:
        raise AssignmentError(f"unknown assignment type {assignment_type!r}")
    if target_type not in TARGET_TYPES:
        raise AssignmentError(f"unknown target type {target_type!r}")

    conn = get_connection()

    if assignment_type == "mission":
        try:
            mission_id = int(mission_id)
        except (TypeError, ValueError):
            raise AssignmentError("mission_id is required for a mission assignment") from None
        exists = conn.execute(
            "SELECT id FROM fa_missions WHERE id=%s AND is_active=1", (mission_id,)
        ).fetchone()
        if not exists:
            raise AssignmentError(f"mission {mission_id} does not exist or is inactive")
        track_key = None
    else:
        track_key = (track_key or "").strip()
        if track_key not in ROLES and track_key != COHORT_ALL:
            raise AssignmentError(f"unknown track {track_key!r}")
        if not track_missions(track_key, conn):
            # An assignment nobody can complete is worse than a refusal: it
            # shows as permanently 0% and reads as a learner failure.
            raise AssignmentError(f"track {track_key!r} has no active missions")
        mission_id = None

    if target_type == "learner":
        try:
            target_user_id = int(target_user_id)
        except (TypeError, ValueError):
            raise AssignmentError("target_user_id is required for a learner assignment") from None
        if not get_learner(target_user_id, tenant_id):
            raise AssignmentError(f"learner {target_user_id} is not in this tenant")
        target_role = None
    else:
        target_role = (target_role or COHORT_ALL).strip()
        if target_role not in ROLES and target_role != COHORT_ALL:
            raise AssignmentError(f"unknown cohort role {target_role!r}")
        target_user_id = None

    due_at = _normalise_due(due_at)

    conn.execute(
        "INSERT INTO fa_assignments "
        "(assignment_type, mission_id, track_key, target_type, target_user_id, "
        " target_role, due_at, note, assigned_by, status, tenant_id, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s,%s)",
        (assignment_type, mission_id, track_key, target_type, target_user_id,
         target_role, due_at, (note or "").strip(), assigned_by, tenant_id, _now()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM fa_assignments WHERE assigned_by=%s ORDER BY id DESC LIMIT 1",
        (assigned_by,),
    ).fetchone()
    assignment = dict(row) if row else {}
    log_audit(
        "assignment.create", assigned_by, actor_role=actor_role,
        subject_type="assignment", subject_id=assignment.get("id", ""),
        detail={"assignment_type": assignment_type, "mission_id": mission_id,
                "track_key": track_key, "target_type": target_type,
                "target_user_id": target_user_id, "target_role": target_role,
                "due_at": due_at},
        tenant_id=tenant_id,
    )
    return assignment


def _normalise_due(due_at: str | None) -> str | None:
    """Accept ``YYYY-MM-DD`` or a full ISO timestamp; reject anything else.

    A due date that silently failed to parse would render as "no due date" and
    the assignment would never go overdue — the failure mode is a deadline that
    quietly does not exist, so this refuses instead.
    """
    raw = (due_at or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            parsed = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise AssignmentError(
            f"due_at {due_at!r} is not a date (YYYY-MM-DD) or ISO timestamp"
        ) from None
    return parsed.isoformat()


def cancel_assignment(assignment_id: int, *, actor: str, actor_role: str = "",
                      tenant_id: str | None = None) -> bool:
    """Mark an assignment cancelled. Returns False when it is not in this tenant."""
    conn = get_connection()
    params: list = [assignment_id]
    row = conn.execute(
        "SELECT id FROM fa_assignments a WHERE a.id=%s AND "
        + _tenant_clause("a", tenant_id, params),
        params,
    ).fetchone()
    if not row:
        return False
    conn.execute(
        "UPDATE fa_assignments SET status='cancelled' WHERE id=%s", (assignment_id,)
    )
    conn.commit()
    log_audit("assignment.cancel", actor, actor_role=actor_role,
              subject_type="assignment", subject_id=assignment_id,
              tenant_id=tenant_id)
    return True


def list_assignments(tenant_id: str | None = None, *, user_id: int | None = None,
                     include_cancelled: bool = True) -> list[dict]:
    """Assignments in the tenant, each with resolved targets and completion.

    ``user_id`` filters to assignments that apply to one learner — including
    cohort assignments they are a member of, which a query matching only on the
    ``target_user_id`` column would miss.
    """
    conn = get_connection()
    params: list = []
    q = ("SELECT a.* FROM fa_assignments a WHERE "
         + _tenant_clause("a", tenant_id, params))
    if not include_cancelled:
        q += " AND a.status='open'"
    q += " ORDER BY a.id DESC"
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]

    now = datetime.now(timezone.utc)
    out = []
    for row in rows:
        missions = assignment_missions(row, conn)
        targets = assignment_targets(row, conn)
        mission_ids = [m["id"] for m in missions]
        detail_targets = []
        for learner in targets:
            done = _completed_count(conn, learner["id"], mission_ids)
            detail_targets.append({
                "user_id": learner["id"],
                "display_name": learner.get("display_name") or learner.get("username"),
                "role": learner.get("role"),
                "completed": done,
                "total": len(mission_ids),
                "complete": bool(mission_ids) and done >= len(mission_ids),
            })
        if user_id is not None and not any(t["user_id"] == user_id for t in detail_targets):
            continue
        row["missions"] = missions
        row["targets"] = detail_targets
        row["target_count"] = len(detail_targets)
        row["complete_count"] = sum(1 for t in detail_targets if t["complete"])
        row["percent"] = (
            round(100.0 * row["complete_count"] / len(detail_targets))
            if detail_targets else 0
        )
        row["overdue"] = _is_overdue(row, now)
        row["label"] = _assignment_label(row)
        out.append(row)
    return out


def _is_overdue(assignment: dict, now: datetime) -> bool:
    if assignment.get("status") != "open":
        return False
    if assignment.get("complete_count", 0) >= assignment.get("target_count", 0) \
            and assignment.get("target_count", 0) > 0:
        return False
    due = assignment.get("due_at")
    if not due:
        return False
    try:
        parsed = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed < now


def _assignment_label(assignment: dict) -> str:
    if assignment.get("assignment_type") == "track":
        key = assignment.get("track_key") or COHORT_ALL
        name = ROLES.get(key, {}).get("label", key)
        return f"{name} track ({len(assignment.get('missions', []))} missions)"
    missions = assignment.get("missions") or []
    return missions[0]["title"] if missions else "Mission (removed)"


def _completed_count(conn, user_id: int, mission_ids) -> int:
    if not mission_ids:
        return 0
    try:
        rows = conn.execute(
            "SELECT mission_id FROM fa_mission_progress "
            "WHERE user_id=%s AND status='completed'", (user_id,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return 0
    # Intersected in Python rather than with a SQL IN-list: the id set is small
    # and a hand-built IN clause is the classic place a placeholder count drifts
    # from the parameter tuple.
    done = {r[0] for r in rows}
    return sum(1 for mid in mission_ids if mid in done)


# ---------------------------------------------------------------------------
# Submissions and evidence
# ---------------------------------------------------------------------------

# Step types the server can actually grade (grading.ASSESSED_STEP_TYPES). A
# completed step outside this set is a page turned, not a demonstration, and the
# learner detail view labels it that way rather than counting it as evidence.
_ASSESSED_STEP_TYPES = frozenset({"coding", "reflect"})


def learner_submissions(user_id: int, limit: int = 100) -> list[dict]:
    """Every step a learner has submitted work against, newest first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT sp.id, sp.step_id, sp.status, sp.submission, sp.score,
                  sp.hints_used, sp.started_at, sp.completed_at,
                  s.title AS step_title, s.step_type, s.step_num, s.xp_partial,
                  m.id AS mission_id, m.slug AS mission_slug, m.title AS mission_title,
                  m.tier
           FROM fa_step_progress sp
           JOIN fa_mission_steps s ON s.id=sp.step_id
           JOIN fa_missions m ON m.id=s.mission_id
           WHERE sp.user_id=%s
           ORDER BY COALESCE(sp.completed_at, sp.started_at) DESC, sp.id DESC
           LIMIT %s""",
        (user_id, limit),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["assessed"] = (item.get("step_type") or "") in _ASSESSED_STEP_TYPES
        item["has_submission"] = bool((item.get("submission") or "").strip())
        out.append(item)
    return out


def learner_evidence(user_id: int, limit: int = 100) -> list[dict]:
    """Verified provenance for a learner: XP ledger rows and certificates.

    This reads ``fa_xp_ledger``, the append-only record aca-int-07 added, rather
    than recomputing anything. It is the only place that can say *why* a learner
    has the points they have — and it distinguishes attendance from earned work,
    because the whole point of that card was that 1465 of one learner's 1715
    points were logins.
    """
    conn = get_connection()
    out: list[dict] = []
    try:
        rows = conn.execute(
            """SELECT xp_delta, reason, source_type, source_id, is_attendance,
                      verified, note, created_at
               FROM fa_xp_ledger WHERE user_id=%s
               ORDER BY id DESC LIMIT %s""",
            (user_id, limit),
        ).fetchall()
        for row in rows:
            item = dict(row)
            item["kind"] = "xp"
            out.append(item)
    except Exception as exc:  # noqa: BLE001
        _log.debug("xp ledger read failed for %s: %s", user_id, exc)
    try:
        rows = conn.execute(
            "SELECT cert_tier, issued_at, token FROM fa_certificates "
            "WHERE user_id=%s ORDER BY id DESC", (user_id,),
        ).fetchall()
        for row in rows:
            item = dict(row)
            item["kind"] = "certificate"
            out.append(item)
    except Exception as exc:  # noqa: BLE001
        _log.debug("certificate read failed for %s: %s", user_id, exc)
    return out


def learner_reviews(user_id: int) -> list[dict]:
    """Every recorded review for a learner, newest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT r.*, m.title AS mission_title, m.slug AS mission_slug
               FROM fa_instructor_reviews r
               LEFT JOIN fa_missions m ON m.id=r.mission_id
               WHERE r.user_id=%s ORDER BY r.id DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        _log.debug("review read failed for %s: %s", user_id, exc)
        return []


# ---------------------------------------------------------------------------
# Review / override
# ---------------------------------------------------------------------------

def record_review(*, user_id: int, verdict: str, reviewer: str,
                  mission_id: int | None = None, step_id: int | None = None,
                  assignment_id: int | None = None, override_score=None,
                  comment: str = "", tenant_id: str | None = None,
                  actor_role: str = "") -> dict:
    """Record a human verdict, optionally overriding a mission score.

    Raises ``AssignmentError`` on invalid input. The override writes
    ``fa_mission_progress.score`` and records the prior value on the review row,
    so the change is reversible by inspection. It does NOT move XP — see the
    module docstring.
    """
    verdict = (verdict or "").strip().lower()
    if verdict not in REVIEW_VERDICTS:
        raise AssignmentError(
            f"unknown verdict {verdict!r}; expected one of {sorted(REVIEW_VERDICTS)}"
        )
    learner = get_learner(user_id, tenant_id)
    if not learner:
        raise AssignmentError(f"learner {user_id} is not in this tenant")

    if override_score is not None and override_score != "":
        try:
            override_score = int(override_score)
        except (TypeError, ValueError):
            raise AssignmentError("override_score must be a whole number 0-100") from None
        if not 0 <= override_score <= 100:
            raise AssignmentError("override_score must be between 0 and 100")
        if mission_id is None:
            raise AssignmentError("a score override needs the mission it applies to")
    else:
        override_score = None

    conn = get_connection()
    mission_id = int(mission_id) if mission_id not in (None, "") else None
    step_id = int(step_id) if step_id not in (None, "") else None
    assignment_id = int(assignment_id) if assignment_id not in (None, "") else None

    prior_score = None
    if mission_id is not None:
        row = conn.execute(
            "SELECT score FROM fa_mission_progress WHERE user_id=%s AND mission_id=%s",
            (user_id, mission_id),
        ).fetchone()
        if row is None and override_score is not None:
            # Overriding a mission the learner never opened would fabricate a
            # progress row out of an instructor's opinion. Refuse.
            raise AssignmentError(
                "this learner has no progress on that mission, so there is no "
                "score to override"
            )
        prior_score = row["score"] if row is not None else None

    if override_score is not None:
        conn.execute(
            "UPDATE fa_mission_progress SET score=%s WHERE user_id=%s AND mission_id=%s",
            (override_score, user_id, mission_id),
        )

    conn.execute(
        "INSERT INTO fa_instructor_reviews "
        "(user_id, mission_id, step_id, assignment_id, verdict, override_score, "
        " prior_score, comment, reviewer, tenant_id, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (user_id, mission_id, step_id, assignment_id, verdict, override_score,
         prior_score, (comment or "").strip(), reviewer, tenant_id, _now()),
    )
    conn.commit()

    log_audit(
        "review.record", reviewer, actor_role=actor_role,
        subject_type="learner", subject_id=user_id,
        detail={"verdict": verdict, "mission_id": mission_id, "step_id": step_id,
                "assignment_id": assignment_id, "override_score": override_score,
                "prior_score": prior_score},
        tenant_id=tenant_id,
    )
    return {
        "ok": True, "user_id": user_id, "verdict": verdict,
        "override_score": override_score, "prior_score": prior_score,
    }


def audit_trail(tenant_id: str | None = None, limit: int = 50) -> list[dict]:
    """Recent instructor actions in this tenant, newest first."""
    conn = get_connection()
    params: list = []
    q = ("SELECT a.* FROM fa_instructor_audit a WHERE "
         + _tenant_clause("a", tenant_id, params)
         + " ORDER BY a.id DESC LIMIT %s")
    params.append(limit)
    try:
        return [dict(r) for r in conn.execute(q, params).fetchall()]
    except Exception as exc:  # noqa: BLE001
        _log.debug("audit trail read failed: %s", exc)
        return []
