# CUI // SP-CTI
"""Canonical task seeder — never use raw INSERT directly.

Usage:
    from tools.kanban.task_factory import create_tasks

    created = create_tasks([{
        "id": "my-task-01",
        "title": "Do the thing",
        "description": "...",
        "task_type": "build",          # optional, default 'build'
        "priority": "medium",          # optional, default 'high'
        "status": "backlog",           # optional, default 'backlog'
        "scheduled_at": None,          # optional — ISO ts; makes the row
                                       #   dispatchable without waiting for
                                       #   promote_backlog_to_scheduled
        "depends_on_task_id": None,    # optional
        "source_doc_id": "abc123",     # optional — DIC document source
        "source_collection_id": "c1", # optional — DIC collection source
    }])
    # returns list of IDs that were actually inserted (skips duplicates)
"""
from __future__ import annotations

import os
import sqlite3
from tools.kanban.gates import (
    GATE_ID_SEPARATOR,
    GATE_TITLE_MARKER,
    RISK_MARKER,
    declares_gate,
    has_gate_id,
)
from tools.logging.icdev_logger import get_logger
from datetime import datetime, timezone

logger = get_logger(__name__)


class BoardBackendError(RuntimeError):
    """Raised when a board write would land somewhere that is not the board."""


#: Mirrors the live CHECK constraint ``kanban_tasks_task_type_check``.
#:
#: Note there is no ``bug`` — use ``fix``. SQLite does NOT enforce CHECK
#: constraints, so an illegal value seeds cleanly against a fallback database and
#: only aborts part-way through the insert loop on PostgreSQL, rolling the whole
#: batch back. Validating in Python means the one backend that would not tell you
#: no longer has to.
VALID_TASK_TYPES = frozenset(
    {"build", "run", "fix", "research", "deploy", "test", "chore"}
)

#: Escape hatch for tests and genuinely fresh installs, which legitimately seed
#: an empty local board.
_ALLOW_LOCAL_BOARD_ENV = "ICDEV_KANBAN_ALLOW_LOCAL_BOARD"


def _assert_real_board(conn) -> None:
    """Refuse to write the board when the connection is a local SQLite fallback.

    ``.env`` is gitignored, so a git worktree has no PostgreSQL config and
    ``get_connection()`` silently falls back to SQLite at
    ``<worktree>/data/icdev.db``. On 2026-08-08 a seeder run that way reported
    "36/36 created" against a database that was then deleted with the worktree —
    the PR merged and the board had nothing on it. A write that goes nowhere must
    fail loudly rather than succeed.

    Set ``ICDEV_KANBAN_ALLOW_LOCAL_BOARD=1`` when a local board is what you mean.
    """
    if os.environ.get(_ALLOW_LOCAL_BOARD_ENV, "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return
    backend = getattr(conn, "backend", None) or getattr(conn, "_backend", None)
    is_sqlite = isinstance(conn, sqlite3.Connection) or (
        isinstance(backend, str) and "sqlite" in backend.lower()
    )
    if not is_sqlite:
        return
    raise BoardBackendError(
        "refusing to write kanban tasks to a SQLite fallback database. This is "
        "almost always a seeder run from a git worktree, where .env is absent so "
        "get_connection() fell back to a local file that dies with the worktree. "
        "Copy .env into the worktree, or set "
        f"{_ALLOW_LOCAL_BOARD_ENV}=1 if a local board is genuinely what you want."
    )


def _work_id_suggestion(task_id: str) -> str:
    """The id this task should have carried — ``tsg-gate-01`` -> ``tsg-<epic>-01``.

    Named in the refusal so the message ends in an edit rather than a puzzle.
    ``<epic>`` is left for the seeder to fill because only it knows the epic;
    tsg-gate-01 became tsg-policy-01 under a new ``policy`` epic.
    """
    prefix, _, number = task_id.rpartition(GATE_ID_SEPARATOR)
    return f"{prefix}-<epic>-{number}"


def _sentinel_shaped_work(task_specs: list[dict]) -> list[str]:
    """Ids that claim to be manual-mode gates while carrying work.

    Not a narrowing of ``is_manual_gate`` — that predicate stays wide on purpose,
    and a card's SECOND gate (``hgx-gate-01``) has to keep matching it. This asks
    the seed-time question instead: does anything except the id say "gate"?
    """
    impostors: list[str] = []
    for t in task_specs:
        task_id = str(t.get("id") or "").strip()
        if has_gate_id(task_id) and not declares_gate(t.get("title"), t.get("description")):
            impostors.append(task_id)
    return impostors


#: How long a seeded-and-claimed task stays claimed. Long enough to cover a real
#: build; short enough that a session which dies frees the task the same working
#: day rather than stranding it.
SEED_CLAIM_TTL_SECONDS = 4 * 60 * 60


def claim_seeded_tasks(created: list[str]) -> dict:
    """Take the per-task coordination lease for every id in `created`.

    Extracted from `create_tasks` so it can be tested against the REAL code
    rather than a copy of it in the test file.

    BEST-EFFORT BY DESIGN. A claim that cannot be taken must not undo an insert
    that already succeeded: the rows exist and the board is correct, and the
    caller only needs to know to watch for a parallel build. Failing the seed
    here would turn a coordination nicety into a seeding outage.

    Every refusal and every error is logged, because a task seeded WITHOUT a
    claim is precisely the situation this exists to prevent — silence would hand
    the caller the old behaviour while looking like the new one.

    Returns ``{claimed, refused, failed}`` so a caller can act on it.
    """
    out = {"claimed": [], "refused": [], "failed": []}
    if not created:
        return out

    from tools.coordination import leases

    for task_id in created:
        try:
            lease = leases.acquire(
                f"kanban:task:{task_id}",
                intent="seeded-and-claimed by this session",
                ttl_seconds=SEED_CLAIM_TTL_SECONDS,
                block=False,
            )
        except Exception as exc:  # noqa: BLE001 — a lease must not break seeding
            out["failed"].append(task_id)
            logger.warning(
                "task_factory: %s seeded but the claim failed (%s) — release is "
                "not needed, but watch for a parallel build", task_id, exc,
            )
            continue
        if lease is None:
            out["refused"].append(task_id)
            logger.warning(
                "task_factory: %s was seeded but could NOT be claimed — another "
                "live session holds it, so the runner may build it in parallel",
                task_id,
            )
        else:
            out["claimed"].append(task_id)
    return out


def create_tasks(task_specs: list[dict], *, claim: bool = False) -> list[str]:
    """Insert tasks that don't already exist. Returns list of inserted IDs.

    ``claim=True`` also takes the per-task coordination lease for THIS
    session on every row it inserts, so the autonomous runner will not build
    a task you seeded in order to build yourself.

    That race is not hypothetical and the seeder is where it starts. A
    session seeds a task and begins implementing; the runner sees an
    eligible row and builds the same task in parallel; two implementations
    exist and one must be closed by hand. Four times in two days — PRs
    #1784, #1792, #1806 and #1807 — and #1807 also sat open on
    ``kanban/kpr-fix-02``, which made the respawn guard withhold that task
    from dispatch while the board reported ``review_bound`` with capacity
    free. A duplicate does not just waste a build; it blocks the queue.

    THE MECHANISM ALREADY EXISTED and was not the gap. ``cli.py --claim``
    takes this same lease, and the runner already refuses a task another
    live session holds. What was missing is that seeding and claiming were
    two separate acts with a window between them, and nothing pointed a
    seeder at the second one — so the reliable-looking alternative,
    ``--pause-runner``, got used instead. That halts the whole board to
    protect one task and lapses silently after 4h with no renewal, which is
    exactly how #1806 and #1807 were built hours after a pause was taken.

    Release with ``tools/kanban/cli.py --release <id>`` when the work lands.
    A claim from a session that dies is freed by its TTL, so forgetting to
    release delays a task rather than stranding it.

    Raises ``ValueError`` for a ``task_type`` the DB forbids and for a work task
    wearing a gate sentinel's id, and ``BoardBackendError`` when the write would
    land in a throwaway local database — all BEFORE anything is inserted, so a
    batch never half-lands.

    Three further checks are evaluated before the first insert for the same
    reason. Two report by default and refuse only when their own named switch
    says to: whether the id has already landed on the default branch
    (``KANBAN_LANDED_CHECK``, ``landed_check``, trust-disc-05) and whether any
    epic claims the id (``KANBAN_IDENTITY_CHECK``, ``task_identity``,
    rem-hyg-02/04). The third REPORTS and never refuses: whether the batch puts
    two unserialized tasks on the same file (``lane_conflicts``, rem-hyg-07),
    which needs its own fire-rate survey before it can be armed.
    """
    if not task_specs:
        return []

    bad_types = sorted({
        str(t.get("task_type"))
        for t in task_specs
        if t.get("task_type") is not None
        and t.get("task_type") not in VALID_TASK_TYPES
    })
    if bad_types:
        raise ValueError(
            f"task_type {', '.join(repr(b) for b in bad_types)} violates "
            f"kanban_tasks_task_type_check; allowed: {sorted(VALID_TASK_TYPES)}. "
            "(There is no 'bug' — use 'fix'.)"
        )

    # kax-exec-04: a gate-shaped id on a work task is undispatchable, silently.
    # `is_manual_gate` returns True for ANY `<card>-gate-<n>` id, so
    # promote_backlog_to_scheduled filters the task out forever — and nothing
    # goes red, because a task nobody can dispatch looks exactly like a task
    # nobody has got to yet. tsg-gate-01 ("decide the CI allowlist policy") sat in
    # backlog from 02:22 while the board idled with three free dispatch slots.
    # Refuse at SEEDING time, where the id is still a keystroke rather than a row.
    impostors = _sentinel_shaped_work(task_specs)
    if impostors:
        named = ", ".join(
            f"{i!r} (use e.g. {_work_id_suggestion(i)!r})" for i in impostors
        )
        raise ValueError(
            f"refusing to seed work with a gate-shaped id: {named}. Any id ending "
            f"{GATE_ID_SEPARATOR}<number> makes tools/kanban/gates.py::is_manual_gate "
            "return True, so promote_backlog_to_scheduled will never dispatch it and "
            "nothing will report it as stuck. Rename it, or — if it really is a "
            f"manual-mode gate — say so: put {GATE_TITLE_MARKER!r} in the title, or a "
            f"{RISK_MARKER!r} line in the description stating what goes wrong if the "
            "runner builds the card unattended."
        )

    # trust-disc-05: is any of these ids ALREADY on the default branch? The board
    # tracks task -> PR and nothing checked task -> main, so a card could be
    # re-seeded for work that had already landed — and every downstream gate would
    # say green, because every downstream gate asks about the PR. Seeding is the
    # cheapest place to find out: the id is still a keystroke rather than a row,
    # and one `git log` answers for the whole batch.
    #
    # Reports by default and refuses only under KANBAN_LANDED_CHECK=enforce, and
    # the check runs BEFORE any insert so a refusal cannot half-land a batch.
    # FAIL-OPEN: any git error leaves seeding exactly as it was.
    _already_landed: list[dict] = []
    try:
        from tools.kanban import landed_check as _lc

        if _lc.mode() != "off":
            _ids = [str(t.get("id") or "").strip() for t in task_specs]
            for _rep in _lc.check_landed_bulk([i for i in _ids if i]).values():
                if _rep.get("landed"):
                    _already_landed.append(_rep)
    except Exception as _lc_exc:  # noqa: BLE001 — advisory; never break seeding
        logger.debug("task_factory: landed check unavailable (%s)", _lc_exc)

    if _already_landed:
        _named = "; ".join(
            f"{r['task_id']} ({r['confidence']}: "
            f"{r['commits'][0]['sha'] if r['commits'] else '?'})"
            for r in sorted(_already_landed, key=lambda r: r["task_id"])
        )
        _detail = (
            f"seeding {len(_already_landed)} task id(s) that ALREADY appear in a "
            f"commit on the default branch: {_named}. The board tracks task -> PR "
            f"and nothing checks task -> main; a task whose work has landed will be "
            f"dispatched again and produce a PR that can only merge as a revert. "
            f"Verify before building, or reuse a fresh id."
        )
        from tools.kanban.landed_check import mode as _lc_mode
        if _lc_mode() == "enforce":
            raise ValueError(f"refusing to seed — {_detail}")
        logger.warning("task_factory: %s", _detail)

    # rem-hyg-02: does an epic actually CLAIM each of these ids? Every number on
    # a project card comes from `<task_prefix><epic_key>-%` patterns, never from
    # task_prefix alone, so a row no epic matches is counted by nothing — and
    # when every row of a card is unclaimed the card vanishes entirely, which
    # looks exactly like a project with no work. Seeding the HCX card by hand on
    # 2026-08-16 put 25 rows under a `hcx-` prefix that was in no card; reading
    # args/projects.yaml proved nothing, because the board is the other half of
    # the state, and the collision was found only by querying it afterwards —
    # three of the new tasks had already been dispatched.
    #
    # rem-hyg-04 arms it, behind KANBAN_IDENTITY_CHECK=enforce|report|off and
    # defaulting to `report` — because that is what rem-hyg-03's survey of the
    # live board supports, not because arming was left half-done. Refusing every
    # unclaimed id would have fired on 35.17% of 3,244 rows; exempting opaque
    # machine ids (`task-<hex>`, which the dashboard's own create-task API and
    # awareness/suggested_card_writer generate, and which no card was ever meant
    # to count) narrows that to 10.85% lifetime and 15.81% over the last 30 days.
    # 15.81% is ten times the rate CLAUDE.md already calls refusing routine work,
    # so `enforce` is offered and documented rather than defaulted. The findings
    # are real, which is why they are still logged at the default.
    #
    # Evaluated BEFORE any insert so a refusal cannot half-land a batch, and
    # FAIL-OPEN — an unreadable projects.yaml leaves seeding exactly as it was.
    _identity_findings: list[dict] = []
    _identity_mode = "off"
    try:
        from tools.kanban import task_identity as _ti

        _identity_mode = _ti.mode()
        if _identity_mode != "off":
            _identity_findings = _ti.check_batch(task_specs)
    except Exception as _ti_exc:  # noqa: BLE001 — advisory; never break seeding
        logger.debug("task_factory: identity check unavailable (%s)", _ti_exc)

    for _f in _identity_findings:
        logger.warning("task_factory: unclaimed task id — %s", _f["detail"])

    # Only the enforceable subset refuses; the rest have already been logged
    # above, so the narrowing is visible in the log rather than hidden by
    # omission.
    _enforceable = [f for f in _identity_findings if f.get("enforced")]
    if _enforceable and _identity_mode == "enforce":
        from tools.kanban.task_identity import MODE_ENV as _ID_ENV

        _named = "; ".join(
            f"{f['task_id']} ({f['reason']} — use {f['suggestion']!r})"
            for f in sorted(_enforceable, key=lambda f: f["task_id"])
        )
        raise ValueError(
            f"refusing to seed {len(_enforceable)} task id(s) no epic claims: "
            f"{_named}. Every number on a project card comes from "
            f"<task_prefix><epic_key>-% patterns and never from task_prefix alone, "
            f"so these rows would be counted by nothing — and a card ALL of whose "
            f"rows are unclaimed vanishes from Home entirely, which looks exactly "
            f"like a project with no work. Fix it in one of two places: rename the "
            f"id to an epic the card already declares, or register the epic (or the "
            f"whole card) in args/projects.yaml. Stand this check down with "
            f"{_ID_ENV}=report to log instead of refusing, or {_ID_ENV}=off.\n\n"
            + "\n".join(f"  - {f['detail']}" for f in _enforceable)
        )

    # rem-hyg-07: will two of these tasks — or one of these and something already
    # on the board — fight over the same file? pr_watcher already answers that,
    # but only for OPEN PRs, which is after both sessions have built: #1684
    # dispatched a producer and its consumer together and 1,058 lines of the
    # loser's branch were discarded. Measured on the live board 2026-08-16, 54
    # pairs shared a file with no dependency path between them and 16 of those
    # were dispatchable simultaneously; two had already been serialized by hand
    # that day and a third had turned PR #1730 DIRTY against main.
    #
    # REPORT ONLY, and the report says which grade of evidence it rests on:
    # seed-time contention can only be read out of PROSE, and telling "this task
    # will WRITE this file" from "this task MENTIONS this file" is a heuristic,
    # not a fact. Arming it needs a fire-rate survey first, exactly as
    # rem-hyg-03/04 do for the identity check. Evaluated BEFORE any insert so the
    # eventual refusal cannot half-land a batch, and FAIL-OPEN — an unreachable
    # board leaves seeding exactly as it was.
    try:
        from tools.kanban import lane_conflicts as _lcf

        for _c in _lcf.check_batch(task_specs):
            logger.warning("task_factory: sibling file contention — %s", _c["detail"])
    except Exception as _lcf_exc:  # noqa: BLE001 — advisory; never break seeding
        logger.debug("task_factory: lane-conflict check unavailable (%s)", _lcf_exc)

    from tools.db.storage import get_connection
    from tools.kanban.init_db import init_kanban_tables
    from tools.kanban import policy_drift

    init_kanban_tables()
    now = datetime.now(timezone.utc).isoformat()

    # kax-merge-02: stamp the card's operating policy as a DELIMITED block
    # rather than letting each seeder paste its own copy. A pasted copy is
    # frozen at seed time — correcting the card then leaves every existing row
    # saying the old thing, which is how 35 hgx rows went on telling sessions to
    # open --draft after the card said not to. A block is re-synced against
    # `policy:` in args/projects.yaml by tools/kanban/policy_drift.py.
    # Loaded ONCE per batch, and a no-op for the ~156 cards with no `policy:`.
    # load_exemptions (not load_rules) so a malformed rule can never take down
    # every seeder — the seeder needs the exemption veto, not the rules.
    _projects = policy_drift.load_projects()
    _ruleset = policy_drift.load_exemptions()

    created: list[str] = []
    conn = get_connection()
    _assert_real_board(conn)
    try:
        for t in task_specs:
            task_id = str(t.get("id") or "").strip()
            if not task_id:
                logger.warning("task_factory: skipping task with no id: %s", t.get("title"))
                continue

            existing = conn.execute(
                "SELECT id FROM kanban_tasks WHERE id = %s", (task_id,)
            ).fetchone()
            if existing:
                logger.debug("task_factory: skip existing task %s", task_id)
                continue

            # Idempotency key check — deduplicates webhook/automation retries
            idem_key = t.get("idempotency_key") or None
            if idem_key:
                idem_exists = conn.execute(
                    "SELECT id FROM kanban_tasks WHERE idempotency_key = %s", (idem_key,)
                ).fetchone()
                if idem_exists:
                    logger.debug(
                        "task_factory: skip duplicate idempotency_key=%s (task %s)",
                        idem_key, task_id,
                    )
                    continue

            max_retries = int(t.get("max_retries") or 5)
            max_runtime_seconds = t.get("max_runtime_seconds")
            if max_runtime_seconds is not None:
                max_runtime_seconds = int(max_runtime_seconds)

            conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    scheduled_at,
                    depends_on_task_id, source_prediction_id,
                    source_doc_id, source_collection_id,
                    dispatch_source, idempotency_key, max_retries,
                    max_runtime_seconds, loop_type, adversarial_enabled,
                    acceptance_criteria,
                    created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    task_id,
                    str(t.get("title", "Untitled task"))[:255],
                    policy_drift.apply_policy_block(
                        t.get("description") or "", task_id, _projects, _ruleset
                    ),
                    t.get("task_type", "build"),
                    t.get("priority", "high"),
                    t.get("status", "backlog"),
                    # rem-hyg-06: a raw-INSERT writer that set scheduled_at could
                    # not be routed through this seeder without it — the column is
                    # what makes the row dispatchable, so dropping it on the way in
                    # would have silently parked every converted reflex card in
                    # backlog. NULL when absent, which is the pre-existing default.
                    t.get("scheduled_at"),
                    t.get("depends_on_task_id"),
                    t.get("source_prediction_id"),
                    t.get("source_doc_id"),
                    t.get("source_collection_id"),
                    t.get("dispatch_source", "dic_notebook"),
                    idem_key,
                    max_retries,
                    max_runtime_seconds,
                    t.get("loop_type", "deterministic"),
                    1 if t.get("adversarial_enabled") else 0,
                    # Persisted so the dispatcher can put it in the prompt.
                    # Without this the column stayed empty on every seeded task
                    # (0 of 2427 populated), which left review_conformance
                    # unable to judge and the agent with no machine-checkable
                    # definition of done.
                    t.get("acceptance_criteria"),
                    now,
                    now,
                ),
            )
            created.append(task_id)

        conn.commit()
    except Exception as exc:
        logger.error("task_factory: commit failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    logger.info("task_factory: inserted %d / %d tasks", len(created), len(task_specs))

    if claim:
        claim_seeded_tasks(created)

    return created
