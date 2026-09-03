# CUI // SP-CTI
"""Requeue an ORPHANED ``validating`` row — bounded, proven, audited (kpr-stale-05).

THE DEFECT, measured 2026-09-03. Fourteen cards (13 rmf-* plus
mc-reflex-881c114a, the latter since 2026-08-29) sat in ``validating`` with NO
branch, NO worktree and NO worker. All 13 rmf cards were parked by
``worktree-isolation-guard`` inside one 35-minute window (10:27-11:02 UTC)
because ``git worktree add -b kanban/<id> ... origin/main`` TIMED OUT after 30s
under concurrent gate runs on the same repo. The guard is correct — fail-closed
beats building in the shared checkout — but ``validating`` was a dead end:
``stranded_audit`` reported such rows as ``orphan_validating``, the reflex
consuming that report DID NOTHING with them, and the scheduler dispatches
backlog/scheduled only. The only reader of the state was a human audit, and the
fourteen were requeued by hand.

SURVEYED over ``kanban_status_transitions`` before building (2026-09-03, live
board): 65 rows lifetime were parked in ``validating`` by a dispatch guard
(``worktree-isolation-guard`` 53, ``repo-aware-guard`` 12, ``dispatch-admission``
0) and NONE was parked twice. What happened NEXT to each park, lifetime:
``manual -> done`` 39, ``cli -> scheduled`` 15, ``manual -> backlog`` 1,
``manual -> in_progress`` 1, and 9 with NO exit at all. Not one automated
exit. The 2026-09-03 window: 11 parks in the 10:00 hour, 3 in the 11:00 hour.
The one row still ``validating`` at survey time (task-det-e9a2e3ea16, parked
20:41 UTC by the same guard) HAS a branch and a worktree — the timed-out
``git worktree add -b`` had created both before the guard fired — so it is the
"worker died mid-validate" shape this module deliberately leaves REPORTED and
untouched.

THE ACT IS prove -> requeue -> confirm, and every step is bounded.

  prove    re-derived from PRIMARY data, never from the audit's claim. The row
           IS ``validating`` (the audit ran minutes ago; a human may have moved
           it since); no ``kanban/<id>`` branch exists locally or on origin; no
           worktree under ``.tmp/worktrees/<id>``; no LIVE lease
           (``lease_liveness.task_lease_verdict`` — ``free`` or ``litter`` pass,
           ``live``/``working`` refuse, and an unreadable lease store is
           ``None`` and REFUSES: reaping on ignorance is how a live worker loses
           its task); and the transition that parked it names a DISPATCH GUARD
           as actor. A row a human parked is a human's decision.
  requeue  through ``tools.kanban.requeue.requeue_task`` — the field-set owner.
           Never a raw UPDATE (which leaves ``last_failure_reason`` set and
           makes ``failure_triage`` read a clean requeue as a fresh failure) and
           never ``--set-status`` (which cannot write from a pipeline-owned
           state). Actor ``kanban_stranded_reflex``; the guard's own parking
           reason is quoted on the transition row, so the requeue is
           attributable to the park it answers.
  cap      ``max_requeues_per_run`` (default 10) from the reflex's config
           block. The remainder is reported as ``deferred`` BY NAME, never
           dropped — the next daily run takes them, oldest park first.
  recur    a row parked TWICE by the same guard within 24h is NOT requeued a
           third time. It gets a ``suggested`` card carrying BOTH parking
           reasons, because a recurring park is the cause the guard's own
           comment says not to hide ("Do NOT 'fix' a recurring park by retrying
           until creation succeeds; that hides the cause").

UNMEASURABLE, never a clean zero: a board that cannot be read reports
``state: unmeasurable`` with an ``error``, and the reflex still returns
``success: True`` — an unreadable board is not a reflex failure, and marking it
one would trip the circuit breaker on the daily audit this act rides on.

NOT here, on purpose: the 30s worktree timeout is not raised and the guard does
not retry — both are forbidden by the guard's comment. A litter lease is not
reaped here; the dispatch window's own reaper (autonomy-adm-03) asks that
question when it next considers the row.

A library. Consumed by ``tools/genesis/reflexes/kanban_stranded_reflex.py``.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from icdev.core.paths import repo_root

BASE_DIR = repo_root(__file__)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.common.helpers import parse_utc_timestamp  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("kanban.orphan_requeue")

#: The three actors that park a task in ``validating`` from the dispatch path
#: (tools/genesis/reflexes/kanban.py). A park by any other actor — ``manual``,
#: ``cli``, the dashboard — is a decision this module has no standing to undo.
GUARD_ACTORS = frozenset({
    "worktree-isolation-guard",
    "repo-aware-guard",
    "dispatch-admission",
})

#: Written as ``actor`` on the requeue transition, so the row says who did it.
ACTOR = "kanban_stranded_reflex"

#: Where a requeued orphan goes. ``scheduled`` rather than ``backlog``: the row
#: was already promoted once (the guard fired at dispatch), and sending it to
#: ``backlog`` would make it wait for promotion a second time.
REQUEUE_STATUS = "scheduled"

DEFAULT_MAX_REQUEUES_PER_RUN = 10

#: Two parks by the same guard inside this window is a RECURRING park.
REPARK_WINDOW_HOURS = 24

#: Lease states that pass the proof. ``litter`` is a dead holder with no
#: heartbeat — nobody owns the row. ``free`` is nobody at all.
_LEASE_PASSES = frozenset({"free", "litter"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── default probes (each injectable, for the fake-board tests) ─────────────


def _branch_exists(task_id: str) -> bool:
    """Any local or origin ref carrying ``task_id`` — the gate's own matcher.

    FAIL-CLOSED for this module: an error reads as ``True`` (a branch might
    exist), because a requeue clears ``branch_name`` and a rebuild on top of an
    unseen branch is the duplicate-PR shape.
    """
    try:
        from tools.genesis.reflexes.kanban import _branches_for_task

        return bool(_branches_for_task(task_id, BASE_DIR))
    except Exception as exc:  # noqa: BLE001
        logger.warning("orphan_requeue: branch probe for %s errored (%s) — assuming a branch",
                       task_id, exc)
        return True


def _worktree_exists(task_id: str) -> bool:
    try:
        from tools.genesis.reflexes.kanban import _task_worktree_path

        return _task_worktree_path(task_id).exists()
    except Exception as exc:  # noqa: BLE001
        logger.warning("orphan_requeue: worktree probe for %s errored (%s) — assuming one",
                       task_id, exc)
        return True


def _lease_state(task_id: str) -> Optional[str]:
    """``lease_liveness``'s verdict, or ``None`` when it cannot be read."""
    try:
        from tools.kanban.lease_liveness import task_lease_verdict

        return task_lease_verdict(task_id).state
    except Exception as exc:  # noqa: BLE001
        logger.warning("orphan_requeue: lease verdict for %s unreadable (%s)", task_id, exc)
        return None


def _file_card(spec: Dict[str, Any]) -> Optional[str]:
    """Seed ONE suggested card through the canonical seeder. Returns its id."""
    from tools.kanban.task_factory import create_tasks

    created = create_tasks([spec])
    return created[0] if created else None


# ── the proof ──────────────────────────────────────────────────────────────


def _parks(conn, task_id: str) -> List[Dict[str, Any]]:
    """Every transition INTO ``validating`` for the row, newest first."""
    rows = conn.execute(
        "SELECT actor, reason, recorded_at FROM kanban_status_transitions "
        "WHERE task_id = %s AND to_status = %s ORDER BY recorded_at DESC",
        (task_id, "validating"),
    ).fetchall()
    return [dict(r) for r in rows]


def prove(
    task_id: str,
    conn,
    *,
    branch_exists: Callable[[str], bool],
    worktree_exists: Callable[[str], bool],
    lease_state: Callable[[str], Optional[str]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Re-derive "this row is an orphaned guard park" from primary data.

    Returns ``{task_id, proven, reasons[], park, repark, parks[], parked_at}``.
    ``proven`` is True only when every check passed; ``reasons`` names each
    that did not. ``repark`` is the recurring-park verdict (True when the same
    guard parked it twice inside :data:`REPARK_WINDOW_HOURS`), decided ONLY
    once the row is otherwise an orphan.
    """
    now = now or _now()
    out: Dict[str, Any] = {"task_id": task_id, "proven": False, "reasons": [],
                           "park": None, "repark": False, "parks": [],
                           "parked_at": None}
    reasons = out["reasons"]

    row = conn.execute(
        "SELECT status FROM kanban_tasks WHERE id = %s", (task_id,)
    ).fetchone()
    if row is None:
        reasons.append("not_found")
        return out
    if dict(row).get("status") != "validating":
        reasons.append("not_validating")

    parks = _parks(conn, task_id)
    out["parks"] = parks
    if not parks:
        reasons.append("no_park_recorded")
    else:
        latest = parks[0]
        out["park"] = latest
        out["parked_at"] = latest.get("recorded_at")
        if latest.get("actor") not in GUARD_ACTORS:
            reasons.append("not_guard_park")

    if branch_exists(task_id):
        reasons.append("branch_exists")
    if worktree_exists(task_id):
        reasons.append("worktree_exists")

    state = lease_state(task_id)
    if state is None:
        reasons.append("lease_unknown")
    elif state not in _LEASE_PASSES:
        reasons.append("lease_live")

    if reasons:
        return out

    # Recurrence: a second park by the SAME guard within the window. Counted
    # from the latest park backwards, so a park from last week is history.
    latest = parks[0]
    cutoff = now - timedelta(hours=REPARK_WINDOW_HOURS)
    same_guard_recent = [
        p for p in parks
        if p.get("actor") == latest.get("actor")
        and (parse_utc_timestamp(p.get("recorded_at")) or now) >= cutoff
    ]
    out["repark"] = len(same_guard_recent) >= 2
    out["repark_parks"] = same_guard_recent
    out["proven"] = True
    return out


# ── the act ────────────────────────────────────────────────────────────────


def _repark_card_spec(proof: Dict[str, Any]) -> Dict[str, Any]:
    tid = proof["task_id"]
    parks = proof.get("repark_parks") or proof["parks"]
    guard = (proof["park"] or {}).get("actor")
    lines = [
        f"Task {tid} was parked in 'validating' {len(parks)} times by "
        f"`{guard}` within {REPARK_WINDOW_HOURS}h. kanban_stranded_reflex requeued "
        f"it once and REFUSES to requeue it again: a recurring park is the CAUSE, "
        f"and the guard's own comment forbids hiding it behind a retry.",
        "",
        "Parking reasons, newest first (verbatim from kanban_status_transitions):",
    ]
    for p in parks:
        lines.append(f"  - {p.get('recorded_at')}  {p.get('actor')}: {p.get('reason')}")
    lines += [
        "",
        "Re-derive:",
        f"  python -m tools.kanban.stranded_audit --json    # is {tid} still orphan_validating?",
        f"  python tools/kanban/cli.py --show {tid}",
        "",
        "Fixed looks like: the cause of the park is removed (the worktree add no "
        "longer times out under concurrent gates / the external repo root is "
        "configured / the admission refusal is resolved), and THEN the task is "
        f"requeued by hand: python tools/kanban/cli.py --requeue {tid} "
        "--requeue-status scheduled. Do NOT raise the 30s worktree timeout and do "
        "NOT retry inside the guard.",
    ]
    return {
        "id": f"kph-repark-{tid}",
        "title": f"[REPARK] {tid}: parked twice by {guard} within {REPARK_WINDOW_HOURS}h",
        "description": "\n".join(lines),
        "task_type": "chore",
        "priority": "high",
        "status": "suggested",
        "idempotency_key": f"orphan-repark-{tid}",
    }


def act_on_orphans(
    findings: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    *,
    get_conn: Optional[Callable[[], Any]] = None,
    branch_exists: Callable[[str], bool] = _branch_exists,
    worktree_exists: Callable[[str], bool] = _worktree_exists,
    lease_state: Callable[[str], Optional[str]] = _lease_state,
    requeue: Optional[Callable[..., Dict[str, Any]]] = None,
    file_card: Callable[[Dict[str, Any]], Optional[str]] = _file_card,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Turn the audit's ``orphan_validating`` list into bounded, proven acts.

    ``findings`` is what ``stranded_audit.audit_stranded_tasks`` returned; only
    its ``orphan_validating`` ids are read, and each is RE-PROVEN here.

    Returns::

        state       unmeasurable | clean | acted
        candidates  how many orphan ids the audit named
        requeued    ids requeued this run, in order
        carded      ids that were parked twice and got a suggested card
        deferred    ids proven but past the cap — named, never dropped
        refused     [{task_id, reasons[]}] — the proof failed; reported only
        cards       ids of the cards filed
        max_requeues_per_run, requeue_status
        error       when unmeasurable
    """
    config = config or {}
    try:
        cap = int(config.get("max_requeues_per_run", DEFAULT_MAX_REQUEUES_PER_RUN))
    except (TypeError, ValueError):
        cap = DEFAULT_MAX_REQUEUES_PER_RUN
    cap = max(cap, 0)

    ids = [f.get("id") for f in (findings or {}).get("orphan_validating", []) or []]
    ids = [i for i in ids if i]
    out: Dict[str, Any] = {
        "state": "clean",
        "candidates": len(ids),
        "requeued": [],
        "carded": [],
        "deferred": [],
        "refused": [],
        "cards": [],
        "max_requeues_per_run": cap,
        "requeue_status": REQUEUE_STATUS,
        "error": None,
    }
    if not ids:
        return out

    if get_conn is None:
        from tools.db.storage import get_connection as get_conn  # noqa: F811
    if requeue is None:
        from tools.kanban.requeue import requeue_task as requeue  # noqa: F811

    # PROVE every candidate first, then act on the proven ones oldest park
    # first — so the cap is applied to what is actually an orphan, and the
    # card that has waited longest is never the one deferred.
    proofs: List[Dict[str, Any]] = []
    try:
        conn = get_conn()
        try:
            for tid in ids:
                proofs.append(prove(
                    tid, conn, branch_exists=branch_exists,
                    worktree_exists=worktree_exists, lease_state=lease_state,
                    now=now,
                ))
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("orphan_requeue: board unreadable — unmeasurable (%s)", exc)
        out["state"] = "unmeasurable"
        out["error"] = str(exc)
        return out

    for p in proofs:
        if not p["proven"]:
            out["refused"].append({"task_id": p["task_id"], "reasons": p["reasons"]})

    proven = [p for p in proofs if p["proven"]]
    proven.sort(key=lambda p: parse_utc_timestamp(p.get("parked_at")) or (now or _now()))

    for p in proven:
        tid = p["task_id"]
        if p["repark"]:
            try:
                card_id = file_card(_repark_card_spec(p))
            except Exception as exc:  # noqa: BLE001
                logger.warning("orphan_requeue: could not card %s (%s)", tid, exc)
                card_id = None
            out["carded"].append(tid)
            if card_id:
                out["cards"].append(card_id)
            continue

        if len(out["requeued"]) >= cap:
            out["deferred"].append(tid)
            continue

        park = p["park"] or {}
        reason = (
            f"orphan_validating: parked by {park.get('actor')} at "
            f"{park.get('recorded_at')} — {park.get('reason')!s}; no branch, no "
            f"worktree, no live lease — requeued by kanban_stranded_reflex (kpr-stale-05)"
        )
        try:
            res = requeue(tid, status=REQUEUE_STATUS, reason=reason, actor=ACTOR,
                          get_conn=get_conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("orphan_requeue: requeue of %s raised (%s)", tid, exc)
            out["refused"].append({"task_id": tid, "reasons": [f"requeue_error:{exc}"]})
            continue
        if res.get("requeued"):
            out["requeued"].append(tid)
            logger.info("orphan_requeue: %s validating -> %s (parked by %s)",
                        tid, REQUEUE_STATUS, park.get("actor"))
        else:
            out["refused"].append({"task_id": tid,
                                   "reasons": [f"requeue_refused:{res.get('error')}"]})

    if out["requeued"] or out["carded"] or out["deferred"]:
        out["state"] = "acted"
    if out["deferred"]:
        logger.warning("orphan_requeue: requeued %d and DEFERRED %d to the next run (cap=%d): %s",
                       len(out["requeued"]), len(out["deferred"]), cap,
                       ", ".join(out["deferred"]))
    return out
