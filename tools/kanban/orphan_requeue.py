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
           state). The transition row names the reflex that ACTUALLY acted
           (``actor``, default :data:`ACTOR`) and quotes the guard's own parking
           reason, so the requeue is attributable to the park it answers.
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

THE SECOND SHAPE (kpr-stale-06): NEITHER ORPHAN NOR STRANDED. The one row left
``validating`` on 2026-09-03 (task-det-e9a2e3ea16) had a ``kanban/<id>`` branch
with ZERO commits ahead of origin/main and a ``.tmp/worktrees/<id>`` directory.
``stranded_audit`` counts that CLEAN (nothing unmerged), the orphan proof above
correctly REFUSES it (``branch_exists``, ``worktree_exists``), and nothing on any
runtime path consumed it -- the only exit was a human.

SURVEYED before building (2026-09-03, live board, re-derived from the branch
reflog and ``git worktree list`` rather than from the cards): of 65 guard parks
lifetime, the branch that exists TODAY was created BEFORE the park for 53
(81.5%), 11 cannot be told (the branch was deleted since, and a deleted branch
takes its reflog with it), and 1 was created after (rmf-ui-08, rebuilt later).
Every one of the 13 rmf-* parks of 2026-09-03 had its branch 1-2 minutes before
the guard fired. So "the timed-out ``worktree add`` had already created the
branch" is the COMMON case, not the edge. Whether the WORKTREE existed at park
time cannot be re-derived for a park whose checkout has since been rebuilt or
removed; for the one live row it can: the directory is a full checkout from the
task's FIRST dispatch (17:53 UTC) whose ``.git`` file is gone -- unregistered,
the "partial delete" shape ``_worktree_is_disposable`` refuses -- so the
card's own rule refuses it too, by name. Exits from the 65: manual->done 39,
cli->scheduled 15, pr_watcher->done 5, manual->backlog 1, manual->in_progress 1,
NONE 4. Not one automated exit.

``act_on_empty_checkouts`` is a SEPARATE proof; the orphan proof is not loosened.
  prove    the row is ``validating`` and guard-parked (shared with the orphan
           proof); EVERY ref matching the id has 0 commits ahead of
           origin/<default> by ``git cherry`` AND its tip is an ANCESTOR of
           origin/<default> (``merge-base --is-ancestor`` -- the general form of
           "tip equals origin/main", which stops being true the moment main
           moves); the worktree is ABSENT or REGISTERED in ``git worktree list
           --porcelain`` on the task's own branch with ``git status
           --porcelain`` EMPTY and ``rev-parse --show-toplevel`` equal to the
           directory. An UNREGISTERED directory REFUSES: kpr-dup-10 cost three
           sessions their work by rmtree-ing "orphan" directories holding live
           edits, and -- measured here -- ``git status`` run inside an
           unregistered directory under the repo describes the ENCLOSING
           checkout, so a dirty reading there is not even about the directory.
           Lease as above. ``None`` anywhere refuses.
  audit    ONE ``worktree_cleaned`` row (actor = the acting reflex, action
           ``orphan_requeue.empty_checkout.intent``) BEFORE acting,
           raise_on_error=True. No row, no act: ``unaudited_refused``.
  apply    ``git worktree remove <path>`` (git's own, never rmtree, never
           ``--force``), then ``git branch -D`` on each LOCAL ref -- an origin
           branch is NEVER deleted here. Each step is confirmed by re-reading
           the world; ``applied_unconfirmed`` is never ``applied``.
  requeue  through ``requeue_task`` with the acting reflex as actor,
           under the SAME ``max_requeues_per_run`` cap as the orphan act (what
           it already requeued this run counts) and the same twice-parked-in-24h
           card rule.

THE CONSUMER MOVED (mfx-own-03). Both acts were reachable ONLY as downstream
consumers of ``kanban_stranded_reflex``, whose audit walks every terminal task.
MEASURED 2026-09-05: 3,892 ``done`` rows against the 2 ``validating`` rows these
acts care about; that reflex's recorded durations run to 1200.2s against its own
1200s watchdog; 242 recorded runs carry ``orphan_requeue`` ZERO times (5 of them
since the acts landed); and three consecutive timeouts opened its breaker, after
which ``tools/daemon/base.py`` skips it entirely. Of 73 lifetime guard parks the
first exit was a HUMAN 63 times and that reflex ZERO times. So
:func:`board_findings` derives the candidate set from ONE indexed board query
and ``tools/genesis/reflexes/kanban_requeue_reflex.py`` runs both acts on a
30-minute cadence with its own breaker. Neither proof was loosened, no budget
was raised, and ``kanban_stranded_reflex`` is untouched -- it still calls both
acts after its audit, which is now belt to the new consumer's braces.

A library. Consumed by ``kanban_requeue_reflex`` and ``kanban_stranded_reflex``.
``python -m tools.kanban.orphan_requeue --plan [--json]`` re-derives BOTH proofs
for every ``validating`` row and acts on nothing.
"""
from __future__ import annotations

import os
import subprocess  # nosec B404 -- git plumbing only, no user input in argv
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# sys.path BOOTSTRAP first, so `python tools/kanban/orphan_requeue.py --plan`
# reaches main() (kax-conflict-04); then the ONE root resolver.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

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

#: DEFAULT ``actor`` for the requeue transition. The row must say who ACTUALLY
#: did it: both acts take an ``actor`` argument, and ``kanban_requeue_reflex``
#: (mfx-own-03) passes its own name. Leaving every row stamped with the reflex
#: that no longer runs the act would point a reader at a breaker-open reflex and
#: an impossible row -- misattribution one layer inside a card about ownership.
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
    out = _board_proof(task_id, conn)
    if out["reasons"] and "not_found" in out["reasons"]:
        return out
    reasons = out["reasons"]

    if branch_exists(task_id):
        reasons.append("branch_exists")
    if worktree_exists(task_id):
        reasons.append("worktree_exists")

    _lease_check(task_id, lease_state, reasons)

    if reasons:
        return out

    _repark_verdict(out, now)
    out["proven"] = True
    return out


def _board_proof(task_id: str, conn) -> Dict[str, Any]:
    """The part of a proof BOTH acts share: the row is ``validating`` and its
    latest park was by a dispatch guard. Returns the proof skeleton with any
    board-level refusals already in ``reasons``."""
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
    return out


def _lease_check(task_id: str, lease_state: Callable[[str], Optional[str]],
                 reasons: List[str]) -> None:
    state = lease_state(task_id)
    if state is None:
        reasons.append("lease_unknown")
    elif state not in _LEASE_PASSES:
        reasons.append("lease_live")


def _repark_verdict(out: Dict[str, Any], now: datetime) -> None:
    """Recurrence: a second park by the SAME guard within the window. Counted
    from the latest park backwards, so a park from last week is history."""
    parks = out["parks"]
    latest = parks[0]
    cutoff = now - timedelta(hours=REPARK_WINDOW_HOURS)
    same_guard_recent = [
        p for p in parks
        if p.get("actor") == latest.get("actor")
        and (parse_utc_timestamp(p.get("recorded_at")) or now) >= cutoff
    ]
    out["repark"] = len(same_guard_recent) >= 2
    out["repark_parks"] = same_guard_recent


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
    actor: str = ACTOR,
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

    # An UNMEASURABLE candidate set is not an empty one (mfx-own-03): a board
    # nobody could read must never be reported as a board with nothing to do.
    if (findings or {}).get("state") == "unmeasurable":
        return {"state": "unmeasurable", "candidates": None, "requeued": [],
                "carded": [], "deferred": [], "refused": [], "cards": [],
                "max_requeues_per_run": cap, "requeue_status": REQUEUE_STATUS,
                "error": str((findings or {}).get("error") or "candidates unmeasurable")}

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
            res = requeue(tid, status=REQUEUE_STATUS, reason=reason, actor=actor,
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


# ══════════════════════════════════════════════════════════════════════════
# kpr-stale-06 -- the row that is NEITHER orphan NOR stranded
# ══════════════════════════════════════════════════════════════════════════

#: Audit event type for the checkout removal. Already admitted by the deployed
#: audit_trail CHECK (a Phase 41 type -- measured on the live PG board
#: 2026-09-03), so no migration; the act and its phase ride in ``action`` as
#: ``orphan_requeue.empty_checkout.<phase>``, the restore_acts shape.
AUDIT_EVENT_TYPE = "worktree_cleaned"
AUDIT_ACTION_PREFIX = "orphan_requeue.empty_checkout"
UNAUDITED_REFUSED = "unaudited_refused"

#: Worktree states that pass the proof. ``absent`` has nothing to remove;
#: ``registered_clean`` is removable through git's own door.
_WORKTREE_PASSES = frozenset({"absent", "registered_clean"})


@dataclass(frozen=True)
class GitContext:
    """Which repository the git proofs run against, and where a task's
    worktree lives. Injectable so the proofs run against a temporary repo."""

    repo_root: Path
    default_branch: str = "main"
    worktree_path_for: Optional[Callable[[str], Path]] = None

    def path_for(self, task_id: str) -> Path:
        if self.worktree_path_for is not None:
            return Path(self.worktree_path_for(task_id))
        from tools.genesis.reflexes.kanban import _task_worktree_path

        return _task_worktree_path(task_id)


def _git(args: List[str], cwd, timeout: int = 60) -> Tuple[Optional[int], str, str]:
    """(returncode, stdout, stderr). ``None`` returncode = could not run."""
    try:
        proc = subprocess.run(  # nosec B603 B607 -- fixed argv, git only
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except Exception as exc:  # noqa: BLE001
        return None, "", str(exc)


def _norm_path(p) -> str:
    """One spelling for a path so ``git worktree list`` output (forward slashes,
    whatever case git was given) compares equal to a ``Path``."""
    s = str(Path(p).resolve()).replace("\\", "/").rstrip("/")
    return s.lower() if os.name == "nt" else s


def parse_worktree_list(porcelain: str) -> List[Dict[str, Any]]:
    """``git worktree list --porcelain`` -> one dict per entry."""
    entries: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {}
    for raw in porcelain.splitlines() + [""]:
        line = raw.rstrip("\r")
        if not line.strip():
            if cur:
                entries.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur["path"] = val
        elif key == "HEAD":
            cur["head"] = val
        elif key == "branch":
            cur["branch"] = val
        elif key in ("detached", "bare", "locked", "prunable"):
            cur[key] = val or True
    return entries


def probe_branch(task_id: str, ctx: GitContext) -> Dict[str, Any]:
    """Is every ref matching ``task_id`` PROVABLY EMPTY against origin/<default>?

    Returns ``{refs, empty, reasons, per_ref}``. ``empty`` is True | False |
    None: None when there is NO ref (that is the orphan proof's case, not this
    one) or when git could not answer for a ref. ``per_ref[ref]`` carries the
    two measurements, ``ahead`` (``git cherry`` ``+`` lines) and ``ancestor``
    (``merge-base --is-ancestor``), so a refusal names the evidence.
    """
    out: Dict[str, Any] = {"refs": [], "empty": None, "reasons": [], "per_ref": {}}
    try:
        from tools.genesis.reflexes.kanban import _branches_for_task

        refs = _branches_for_task(task_id, ctx.repo_root)
    except Exception as exc:  # noqa: BLE001
        out["reasons"].append(f"refs_unreadable:{exc}")
        return out
    out["refs"] = list(refs)
    if not refs:
        out["reasons"].append("no_branch")
        return out

    base = f"origin/{ctx.default_branch}"
    unknown = False
    empty = True
    for ref in refs:
        rc, cherry, _ = _git(["cherry", base, ref], ctx.repo_root)
        ahead = (len([ln for ln in cherry.splitlines() if ln.startswith("+")])
                 if rc == 0 else None)
        rc2, _, _ = _git(["merge-base", "--is-ancestor", ref, base], ctx.repo_root)
        ancestor = True if rc2 == 0 else (False if rc2 == 1 else None)
        out["per_ref"][ref] = {"ahead": ahead, "ancestor": ancestor}
        if ahead is None or ancestor is None:
            unknown = True
            out["reasons"].append(f"branch_unreadable:{ref}")
            continue
        if ahead > 0:
            empty = False
            out["reasons"].append(f"branch_ahead:{ref}:{ahead}")
        elif not ancestor:
            # Patch-equivalent but not contained: a squash-merged branch based
            # on an older main. Nothing here proves deleting it loses nothing
            # under every reading, so it is a human's.
            empty = False
            out["reasons"].append(f"branch_not_ancestor:{ref}")
    out["empty"] = None if unknown else empty
    return out


def probe_worktree(task_id: str, ctx: GitContext) -> Dict[str, Any]:
    """Is the task's worktree ABSENT, or REGISTERED and PROVABLY CLEAN?

    Returns ``{path, state, reasons, entry}`` with ``state`` one of
    absent | registered_clean | registered_dirty | unregistered | other_branch |
    unreadable. Only the first two pass.

    An UNREGISTERED directory refuses even when it looks empty: nothing this
    repo claims is thereby disposable (kpr-dup-10). And ``git status`` is read
    ONLY after the directory is proven to be its own toplevel -- run inside an
    unregistered directory under the repo, ``git status`` walks UP and describes
    the enclosing checkout (measured 2026-09-03 on task-det-e9a2e3ea16: 59
    entries, every one of them the main checkout's).
    """
    path = ctx.path_for(task_id)
    out: Dict[str, Any] = {"path": str(path), "state": None, "reasons": [], "entry": None}
    if not path.exists():
        out["state"] = "absent"
        return out

    rc, listing, err = _git(["worktree", "list", "--porcelain"], ctx.repo_root)
    if rc != 0:
        out["state"] = "unreadable"
        out["reasons"].append(f"worktree_list_failed:{err.strip() or rc}")
        return out
    want = _norm_path(path)
    entry = next((e for e in parse_worktree_list(listing)
                  if "path" in e and _norm_path(e["path"]) == want), None)
    if entry is None:
        out["state"] = "unregistered"
        out["reasons"].append("worktree_unregistered")
        return out
    out["entry"] = entry
    if entry.get("prunable"):
        out["state"] = "unregistered"
        out["reasons"].append("worktree_prunable")
        return out
    expected = f"refs/heads/kanban/{task_id}"
    if entry.get("detached") or entry.get("branch") != expected:
        out["state"] = "other_branch"
        out["reasons"].append(
            f"worktree_on_other_branch:{entry.get('branch') or 'detached'}")
        return out

    rc, top, _ = _git(["rev-parse", "--show-toplevel"], path)
    if rc != 0 or _norm_path(top.strip()) != want:
        out["state"] = "unregistered"
        out["reasons"].append("worktree_toplevel_mismatch")
        return out
    rc, status, err = _git(["status", "--porcelain"], path)
    if rc != 0:
        out["state"] = "unreadable"
        out["reasons"].append(f"worktree_status_failed:{err.strip() or rc}")
        return out
    entries = [ln for ln in status.splitlines() if ln.strip()]
    if entries:
        out["state"] = "registered_dirty"
        out["reasons"].append(f"worktree_dirty:{len(entries)}")
        return out
    out["state"] = "registered_clean"
    return out


def prove_empty_checkout(
    task_id: str,
    conn,
    *,
    branch_state: Callable[[str], Dict[str, Any]],
    worktree_state: Callable[[str], Dict[str, Any]],
    lease_state: Callable[[str], Optional[str]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Re-derive "this guard park's branch is empty and its worktree is clean".

    Same skeleton as :func:`prove` and deliberately a DIFFERENT predicate on
    git: this proof REQUIRES a branch (``no_branch`` refuses -- that row is the
    orphan proof's) and requires it empty; the orphan proof refuses any branch.
    The two are disjoint by construction, so a row is never claimed by both.
    """
    now = now or _now()
    out = _board_proof(task_id, conn)
    if "not_found" in out["reasons"]:
        return out
    reasons = out["reasons"]

    branch = branch_state(task_id)
    out["branch"] = branch
    if branch.get("empty") is not True:
        reasons.extend(branch.get("reasons") or ["branch_unknown"])

    wt = worktree_state(task_id)
    out["worktree"] = wt
    if wt.get("state") not in _WORKTREE_PASSES:
        reasons.extend(wt.get("reasons") or [f"worktree_{wt.get('state')}"])

    _lease_check(task_id, lease_state, reasons)

    if reasons:
        return out

    _repark_verdict(out, now)
    out["proven"] = True
    return out


# ── the act: audit -> remove -> delete -> confirm -> requeue ──────────────


def _audit_event(phase: str, details: Dict[str, Any], actor: str = ACTOR) -> Any:
    """ONE audit row per phase, fail-closed. Returns the entry id.

    ``actor`` names the reflex that is ACTUALLY acting (mfx-own-03), not the
    module: an assessor reading audit_trail must be able to go and look at that
    reflex's state, and ``kanban_stranded_reflex`` no longer runs this act.
    """
    from tools.audit.audit_logger import log_event

    return log_event(AUDIT_EVENT_TYPE, actor, f"{AUDIT_ACTION_PREFIX}.{phase}",
                     details=details, raise_on_error=True)


def _remove_worktree(path: Path, ctx: GitContext) -> Tuple[Optional[int], str]:
    """git's own door, never rmtree, never ``--force``: a dirty or locked
    worktree makes git refuse, which is the answer we want."""
    rc, _, err = _git(["worktree", "remove", str(path)], ctx.repo_root)
    return rc, err.strip()


def _delete_local_branch(ref: str, ctx: GitContext) -> Tuple[Optional[int], str]:
    """``-D``, because the proof already established the ref is an ancestor of
    origin/<default> with nothing ahead; ``-d`` would judge it against the main
    checkout's HEAD, which may lag origin -- a refusal about the checkout, not
    the branch."""
    rc, _, err = _git(["branch", "-D", ref], ctx.repo_root)
    return rc, err.strip()


def _ref_gone(ref: str, ctx: GitContext) -> Optional[bool]:
    rc, _, _ = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"], ctx.repo_root)
    if rc == 0:
        return False
    if rc == 1:
        return True
    return None


def _apply_empty_checkout(
    proof: Dict[str, Any],
    ctx: GitContext,
    *,
    audit: Callable[[str, Dict[str, Any]], Any],
    remove_worktree: Callable[[Path, GitContext], Tuple[Optional[int], str]],
    delete_branch: Callable[[str, GitContext], Tuple[Optional[int], str]],
) -> Tuple[bool, Any]:
    """prove has passed; audit BEFORE acting, act, confirm. Returns
    ``(ok, outcome | refusal_reason)``."""
    tid = proof["task_id"]
    wt = proof["worktree"]
    branch = proof["branch"]
    local_refs = [r for r in branch["refs"] if not r.startswith("origin/")]
    details = {
        "task_id": tid, "card": "kpr-stale-06",
        "worktree": wt["path"], "worktree_state": wt["state"],
        "refs": branch["refs"], "local_refs": local_refs, "per_ref": branch["per_ref"],
        "park": proof.get("park"),
    }

    def _after(phase: str, extra: Dict[str, Any]) -> None:
        # The INTENT row is the load-bearing one; an outcome row that fails to
        # write is logged, never allowed to undo an act already made.
        try:
            audit(phase, {**details, **extra})
        except Exception as exc:  # noqa: BLE001
            logger.warning("orphan_requeue: %s audit for %s not recorded (%s)", phase, tid, exc)

    try:
        row_id = audit("intent", details)
        if not row_id:
            raise RuntimeError("audit returned no row id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("orphan_requeue: REFUSING to act on %s -- intent not audited (%s)", tid, exc)
        return False, f"{UNAUDITED_REFUSED}:{exc}"

    outcome: Dict[str, Any] = {"removed_worktree": None, "deleted_branches": []}
    if wt["state"] == "registered_clean":
        path = Path(wt["path"])
        rc, err = remove_worktree(path, ctx)
        if rc != 0:
            _after("failed", {"step": "worktree_remove", "error": err})
            return False, f"worktree_remove_failed:{err or rc}"
        still_listed = any(
            "path" in e and _norm_path(e["path"]) == _norm_path(path)
            for e in parse_worktree_list(_git(["worktree", "list", "--porcelain"], ctx.repo_root)[1])
        )
        if path.exists() or still_listed:
            _after("unconfirmed", {"step": "worktree_remove"})
            return False, "worktree_remove_unconfirmed"
        outcome["removed_worktree"] = str(path)

    for ref in local_refs:
        rc, err = delete_branch(ref, ctx)
        if rc != 0:
            _after("failed", {"step": "branch_delete", "ref": ref, "error": err, **outcome})
            return False, f"branch_delete_failed:{ref}:{err or rc}"
        if _ref_gone(ref, ctx) is not True:
            _after("unconfirmed", {"step": "branch_delete", "ref": ref, **outcome})
            return False, f"branch_delete_unconfirmed:{ref}"
        outcome["deleted_branches"].append(ref)

    _after("applied", outcome)
    return True, outcome


#: ``requeue._record_transition`` keeps the first 200 characters of a reason.
#: The park is quoted FIRST so the requeue stays attributable to it within
#: that budget; the full park, refs and paths ride on the audit rows.
_REASON_BUDGET = 200


def _requeue_reason(park: Dict[str, Any], outcome: Dict[str, Any]) -> str:
    head = (f"empty_checkout (kpr-stale-06): {park.get('actor')} park "
            f"{str(park.get('recorded_at') or '')[:19]} -- ")
    tail = (f" | worktree {'removed' if outcome.get('removed_worktree') else 'absent'}, "
            f"local branch deleted")
    # The park's own words outrank the mechanics: the actor is the transition
    # row's own column, and the removal is on the audit row.
    reason = head + str(park.get("reason") or "")
    if len(reason) + len(tail) <= _REASON_BUDGET:
        reason += tail
    return reason[:_REASON_BUDGET]


def _unmeasurable_empty(cap: int, already: int, error: str) -> Dict[str, Any]:
    return {"state": "unmeasurable", "candidates": None, "requeued": [], "carded": [],
            "deferred": [], "refused": [], "cards": [], "acts": [],
            "max_requeues_per_run": cap, "already_requeued": already,
            "requeue_status": REQUEUE_STATUS, "dry_run": False, "error": error}


def act_on_empty_checkouts(
    findings: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    *,
    already_requeued: int = 0,
    dry_run: bool = False,
    get_conn: Optional[Callable[[], Any]] = None,
    ctx: Optional[GitContext] = None,
    branch_state: Optional[Callable[[str], Dict[str, Any]]] = None,
    worktree_state: Optional[Callable[[str], Dict[str, Any]]] = None,
    lease_state: Callable[[str], Optional[str]] = _lease_state,
    requeue: Optional[Callable[..., Dict[str, Any]]] = None,
    file_card: Callable[[Dict[str, Any]], Optional[str]] = _file_card,
    audit: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    remove_worktree: Callable[[Path, GitContext], Tuple[Optional[int], str]] = _remove_worktree,
    delete_branch: Callable[[str, GitContext], Tuple[Optional[int], str]] = _delete_local_branch,
    actor: str = ACTOR,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Turn the audit's ``validating_with_branch`` list into bounded, proven,
    AUDITED acts. Each id is RE-PROVEN here (:func:`prove_empty_checkout`).

    ``already_requeued`` is what the orphan act requeued this run: the two acts
    share ONE ``max_requeues_per_run``. ``dry_run`` proves everything and acts
    on nothing (``would_act`` names what it would have done).

    Returns::

        state       unmeasurable | clean | acted
        candidates  how many ids the audit named
        requeued    ids requeued this run, in order
        acts        [{task_id, removed_worktree, deleted_branches}]
        carded      ids parked twice and carded instead
        deferred    ids proven but past the shared cap -- named, never dropped
        refused     [{task_id, reasons[]}] -- proof or act failed; reported only
        would_act   (dry_run) ids that would have been acted on
        cards, max_requeues_per_run, already_requeued, requeue_status, dry_run,
        error (when unmeasurable)
    """
    config = config or {}
    try:
        cap = int(config.get("max_requeues_per_run", DEFAULT_MAX_REQUEUES_PER_RUN))
    except (TypeError, ValueError):
        cap = DEFAULT_MAX_REQUEUES_PER_RUN
    cap = max(cap, 0)
    already = max(int(already_requeued or 0), 0)

    # As above: unmeasurable candidates are not zero candidates (mfx-own-03).
    if (findings or {}).get("state") == "unmeasurable":
        return _unmeasurable_empty(
            cap, already,
            str((findings or {}).get("error") or "candidates unmeasurable"))

    ids = [f.get("id") for f in (findings or {}).get("validating_with_branch", []) or []]
    ids = [i for i in ids if i]
    out: Dict[str, Any] = {
        "state": "clean", "candidates": len(ids),
        "requeued": [], "acts": [], "carded": [], "deferred": [], "refused": [],
        "cards": [], "would_act": [],
        "max_requeues_per_run": cap, "already_requeued": already,
        "requeue_status": REQUEUE_STATUS, "dry_run": bool(dry_run), "error": None,
    }
    if not ids:
        return out

    if audit is None:
        # Bind the CALLER's actor onto the default writer, so the intent row and
        # the transition row name the same reflex.
        audit = lambda phase, details: _audit_event(phase, details, actor=actor)  # noqa: E731
    if ctx is None:
        ctx = GitContext(repo_root=BASE_DIR,
                         default_branch=(findings or {}).get("default_branch") or "main")
    if branch_state is None:
        branch_state = lambda tid: probe_branch(tid, ctx)  # noqa: E731
    if worktree_state is None:
        worktree_state = lambda tid: probe_worktree(tid, ctx)  # noqa: E731
    if get_conn is None:
        from tools.db.storage import get_connection as get_conn  # noqa: F811
    if requeue is None:
        from tools.kanban.requeue import requeue_task as requeue  # noqa: F811

    proofs: List[Dict[str, Any]] = []
    try:
        conn = get_conn()
        try:
            for tid in ids:
                proofs.append(prove_empty_checkout(
                    tid, conn, branch_state=branch_state,
                    worktree_state=worktree_state, lease_state=lease_state, now=now,
                ))
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("orphan_requeue: board unreadable -- unmeasurable (%s)", exc)
        return _unmeasurable_empty(cap, already, str(exc))

    for p in proofs:
        if not p["proven"]:
            out["refused"].append({"task_id": p["task_id"], "reasons": p["reasons"]})

    proven = [p for p in proofs if p["proven"]]
    proven.sort(key=lambda p: parse_utc_timestamp(p.get("parked_at")) or (now or _now()))

    for p in proven:
        tid = p["task_id"]
        if p["repark"]:
            if dry_run:
                out["carded"].append(tid)
                continue
            try:
                card_id = file_card(_repark_card_spec(p))
            except Exception as exc:  # noqa: BLE001
                logger.warning("orphan_requeue: could not card %s (%s)", tid, exc)
                card_id = None
            out["carded"].append(tid)
            if card_id:
                out["cards"].append(card_id)
            continue

        if already + len(out["requeued"]) + len(out["would_act"]) >= cap:
            out["deferred"].append(tid)
            continue

        if dry_run:
            out["would_act"].append(tid)
            continue

        ok, res = _apply_empty_checkout(
            p, ctx, audit=audit, remove_worktree=remove_worktree, delete_branch=delete_branch)
        if not ok:
            out["refused"].append({"task_id": tid, "reasons": [res]})
            continue
        out["acts"].append({"task_id": tid, **res})

        park = p["park"] or {}
        reason = _requeue_reason(park, res)
        try:
            r = requeue(tid, status=REQUEUE_STATUS, reason=reason, actor=actor,
                        get_conn=get_conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("orphan_requeue: requeue of %s raised (%s)", tid, exc)
            out["refused"].append({"task_id": tid, "reasons": [f"requeue_error:{exc}"]})
            continue
        if r.get("requeued"):
            out["requeued"].append(tid)
            logger.info("orphan_requeue: %s validating -> %s (empty checkout removed; parked by %s)",
                        tid, REQUEUE_STATUS, park.get("actor"))
        else:
            out["refused"].append({"task_id": tid,
                                   "reasons": [f"requeue_refused:{r.get('error')}"]})

    if out["requeued"] or out["carded"] or out["deferred"] or out["would_act"] or out["acts"]:
        out["state"] = "acted"
    if out["deferred"]:
        logger.warning("orphan_requeue: empty-checkout act DEFERRED %d to the next run "
                       "(shared cap=%d, orphan act used %d): %s",
                       len(out["deferred"]), cap, already, ", ".join(out["deferred"]))
    return out


# ── --plan: re-derive both proofs, act on nothing ─────────────────────────


def board_findings(get_conn: Optional[Callable[[], Any]] = None,
                   default_branch: str = "main") -> Dict[str, Any]:
    """The acts' candidate set, derived from the BOARD and from nothing else
    (mfx-own-03).

    Both acts were reachable only as consumers of ``stranded_audit``, which
    walks EVERY terminal task -- 3,892 ``done`` rows on the live board against
    the 2 ``validating`` rows these acts care about -- comparing divergent
    branches by patch-id. Measured 2026-09-05: median recorded reflex run
    300.0s, max 1200.2s against a 1200s watchdog; 242 recorded runs carry
    ``orphan_requeue`` ZERO times; three consecutive timeouts then opened the
    circuit breaker, after which the daemon SKIPS the reflex entirely. The act
    was built, registered and unreachable -- this repo's signature defect one
    layer in.

    Nothing about the act needs the audit. Its candidates are ONE indexed query.
    Both keys carry the SAME rows on purpose: ``orphan_validating`` and
    ``validating_with_branch`` are the audit's partition of one population by
    "does a branch exist", and each act RE-DERIVES that itself
    (:func:`prove` refuses ``branch_exists``; :func:`prove_empty_checkout`
    requires a branch that is provably empty). Partitioning here would be a
    second opinion on a question the proofs already own.

    UNMEASURABLE, never a clean empty list: an unreadable board returns
    ``state: "unmeasurable"`` with both lists ``None``, so a caller cannot read
    "could not look" as "nothing to do".
    """
    out: Dict[str, Any] = {
        "state": "measured",
        "source": "board",
        "default_branch": default_branch,
        "orphan_validating": None,
        "validating_with_branch": None,
        "error": None,
    }
    if get_conn is None:
        from tools.db.storage import get_connection as get_conn  # noqa: F811
    try:
        conn = get_conn()
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, title FROM kanban_tasks WHERE status = %s", ("validating",)
            ).fetchall()]
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("orphan_requeue: board unreadable — candidates unmeasurable (%s)", exc)
        out["state"] = "unmeasurable"
        out["error"] = str(exc)
        return out
    out["orphan_validating"] = rows
    out["validating_with_branch"] = list(rows)
    return out


def plan(get_conn: Optional[Callable[[], Any]] = None,
         ctx: Optional[GitContext] = None) -> Dict[str, Any]:
    """Every ``validating`` row through BOTH proofs. Writes nothing."""
    if get_conn is None:
        from tools.db.storage import get_connection as get_conn  # noqa: F811
    ctx = ctx or GitContext(repo_root=BASE_DIR)
    findings = board_findings(get_conn=get_conn, default_branch=ctx.default_branch)
    if findings["state"] == "unmeasurable":
        return {"state": "unmeasurable", "error": findings["error"], "rows": []}
    rows = findings["orphan_validating"]
    orphan = act_on_orphans(findings, {}, get_conn=get_conn,
                            requeue=lambda *a, **k: {"requeued": False, "error": "dry_run"},
                            file_card=lambda spec: None)
    empty = act_on_empty_checkouts(findings, {}, get_conn=get_conn, ctx=ctx, dry_run=True)
    return {"state": "measured", "validating_rows": rows,
            "orphan_proof": {"refused": orphan["refused"],
                             "would_requeue": orphan["requeued"] + [
                                 r["task_id"] for r in orphan["refused"]
                                 if r["reasons"] == ["requeue_refused:dry_run"]],
                             "carded": orphan["carded"]},
            "empty_checkout_proof": {"refused": empty["refused"],
                                     "would_act": empty["would_act"],
                                     "carded": empty["carded"]}}


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--plan", action="store_true",
                    help="re-derive both proofs for every validating row; act on nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.plan:
        ap.print_help()
        return 2
    try:
        from dotenv import load_dotenv

        load_dotenv(BASE_DIR / ".env", override=True)
    except ImportError:
        pass
    report = plan()
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"state: {report['state']}")
        if report.get("error"):
            print(f"error: {report['error']}")
        for row in report.get("validating_rows", []):
            tid = row["id"]
            o = next((r for r in report["orphan_proof"]["refused"] if r["task_id"] == tid), None)
            e = next((r for r in report["empty_checkout_proof"]["refused"] if r["task_id"] == tid), None)
            print(f"  {tid}")
            print(f"    orphan proof:         "
                  f"{'would requeue' if tid in report['orphan_proof']['would_requeue'] else 'REFUSED ' + ', '.join(o['reasons']) if o else 'carded'}")
            print(f"    empty-checkout proof: "
                  f"{'would act' if tid in report['empty_checkout_proof']['would_act'] else 'REFUSED ' + ', '.join(e['reasons']) if e else 'carded'}")
    return 0 if report["state"] == "measured" else 2


if __name__ == "__main__":
    sys.exit(_main())
