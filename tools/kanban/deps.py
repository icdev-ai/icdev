#!/usr/bin/env python3
# CUI // SP-CTI
"""Task dependency gating — the single source of truth (kpr-fix-02).

The board carries TWO dependency mechanisms and every dispatch decision used to
require BOTH:

``kanban_task_deps`` (junction)
    The REAL graph. Fan-in prerequisites, written deliberately:
    ``cef-di-03`` depends on ``cef-rsv-01``, ``cef-rsv-02``, ``cef-rsv-03``.

``kanban_tasks.depends_on_task_id`` (scalar)
    SEEDING ORDER. Written by a seeder as it walks its list, so a batch comes
    out as a linear chain — ``cef-di-03 -> cef-di-02`` — whether or not the work
    is actually sequential.

ANDing them lets the accidental declaration override the deliberate one. MEASURED
on the live board 2026-08-18: ``cef-di-03/04/05/06`` had every real prerequisite
satisfied (``cef-rsv-01/02/03`` all done) and were blocked SOLELY by seeding
order — five independent migrations onto one already-built API, forced to run one
at a time. 16 backlog tasks, 15 dependency-blocked, ZERO dispatchable, all day.

THE RULE
--------
When a task HAS junction dependencies, they ARE its declaration and the scalar
adds no second gate. When it has NONE, the scalar is the only declaration and is
honoured in full — that is what holds a task behind a manual gate.

ONE EXCEPTION, and it is the reason this module imports :mod:`tools.kanban.gates`:
a scalar dep on a MANUAL GATE always holds, junction rows or not. A gate is a
HOLD, not seeding order — a human decided this card does not ship unattended —
and letting a junction row release it would turn the one deliberate scalar
declaration on the board into the one the rule discards. Measured the same day:
two non-terminal tasks are scalar-held by a gate and one of them
(``kpr-stale-02``) also carries junction rows, so without this carve-out the
guarantee would be accidental rather than stated.

WHY IT LIVES HERE
-----------------
Six enforcement sites and five reporters re-derived this predicate, each with its
own copy. A reporter that disagrees with the dispatcher describes a policy the
board does not have (the same defect ``merge_readiness``/``pr_watcher`` share one
``classify_merge_readiness`` to avoid). Import from here; do NOT write a seventh
copy.

The SQL form (:func:`dep_clause_sql`) and the Python form (:func:`deps_satisfied`)
are two renderings of ONE rule and are tested against each other. They agree on
the two cases that used to diverge: a dangling junction dep BLOCKS in both (the
old SQL's inner JOIN silently dropped it), and gate recognition in SQL is
deliberately WIDER than :func:`tools.kanban.gates.is_manual_gate` — it may only
ever hold MORE, never release more, which is the safe direction for a hold.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from tools.kanban.gates import is_manual_gate

#: A dependency is discharged by either. ``decomposed`` counts because a parent
#: that was split will never reach ``done`` directly, and its dependents must not
#: wait forever for a status the row can no longer take.
SATISFIED_STATUSES: tuple[str, ...] = ("done", "decomposed")

#: LIKE patterns that recognise a manual gate in SQL. Bound as PARAMETERS, never
#: interpolated: a literal ``%`` in a query string is a psycopg format directive
#: the moment any parameter is passed — including one RLS injects — so the same
#: clause would be correct in one call site and a ``ValueError`` in the next.
SQL_GATE_ID_PATTERN = "%-gate-%"
SQL_GATE_TITLE_PATTERN = "%MANUAL-MODE GATE%"


# ────────────────────────────────────────────────────────────────────────────
# The rule, as a pure function. Everything below renders it.
# ────────────────────────────────────────────────────────────────────────────

def effective_dep_ids(
    scalar_dep_id: str | None,
    junction_dep_ids: Iterable[str] | None,
    *,
    scalar_is_gate: bool = False,
) -> tuple[str, ...]:
    """The dependencies that actually gate this task, in declaration order.

    ``scalar_is_gate`` is the caller's answer to "is the scalar parent a manual
    gate", because a gate holds regardless of what the junction says.
    """
    junction = tuple(dict.fromkeys(str(d) for d in (junction_dep_ids or ()) if d))
    scalar = str(scalar_dep_id) if scalar_dep_id else ""
    if not scalar:
        return junction
    if junction and not scalar_is_gate:
        return junction
    return (scalar,) + tuple(d for d in junction if d != scalar)


def scalar_is_superseded(
    scalar_dep_id: str | None,
    junction_ids: Iterable[str] | None,
    *,
    scalar_is_gate: bool = False,
) -> bool:
    """True when the scalar dep is seeding order the junction graph replaces.

    Exposed for the reporters, which need to SAY that a declared dependency is
    no longer gating rather than silently omitting it.
    """
    if not scalar_dep_id:
        return False
    return scalar_dep_id not in effective_dep_ids(
        scalar_dep_id, junction_ids, scalar_is_gate=scalar_is_gate
    )


# ────────────────────────────────────────────────────────────────────────────
# Board reads
# ────────────────────────────────────────────────────────────────────────────

def _row_get(row: Any, key: str) -> Any:
    if row is None:
        return None
    try:
        return dict(row).get(key)
    except (TypeError, ValueError):
        return getattr(row, key, None)


def junction_dep_ids(task_id: str, conn) -> list[str]:
    """Junction prerequisites for one task. An unreadable table means NONE.

    Fail-open on the read, deliberately: the junction table is the mechanism
    that RELAXES the gate here, so a missing table must degrade to the old
    scalar-only behaviour rather than releasing everything.
    """
    try:
        rows = conn.execute(
            "SELECT depends_on_id FROM kanban_task_deps WHERE task_id = %s",
            (task_id,),
        ).fetchall()
    except Exception:  # noqa: BLE001 — migration 041 may not have run
        return []
    return [
        str(_row_get(r, "depends_on_id"))
        for r in rows
        if _row_get(r, "depends_on_id")
    ]


def blocking_deps(task_id: str, conn) -> list[str]:
    """Dependency ids that are not satisfied, ``"<id> (missing)"`` when absent.

    A dependency pointing at a row that does not exist BLOCKS. Between the two
    ways of being wrong, refusing to dispatch is the recoverable one.
    """
    row = conn.execute(
        "SELECT t.depends_on_task_id AS dep_id, p.title AS dep_title "
        "FROM kanban_tasks t "
        "LEFT JOIN kanban_tasks p ON p.id = t.depends_on_task_id "
        "WHERE t.id = %s",
        (task_id,),
    ).fetchone()
    scalar = _row_get(row, "dep_id")
    scalar_is_gate = bool(scalar) and is_manual_gate(scalar, _row_get(row, "dep_title"))

    gating = effective_dep_ids(
        scalar, junction_dep_ids(task_id, conn), scalar_is_gate=scalar_is_gate
    )
    blocking: list[str] = []
    for dep_id in gating:
        dep_row = conn.execute(
            "SELECT status FROM kanban_tasks WHERE id = %s", (dep_id,)
        ).fetchone()
        if dep_row is None:
            blocking.append(f"{dep_id} (missing)")
        elif _row_get(dep_row, "status") not in SATISFIED_STATUSES:
            blocking.append(dep_id)
    return blocking


def deps_satisfied(task_id: str, conn) -> bool:
    """True when every GATING dependency is done/decomposed."""
    return not blocking_deps(task_id, conn)


def blocking_deps_bulk(task_ids: Sequence[str], conn) -> dict[str, list[str]]:
    """:func:`blocking_deps` for many tasks in THREE queries, not ``2 + N`` each.

    The board's list endpoint annotates every row it returns, so the per-task
    form there is an N+1 over the whole board. Same rule, same answers — asserted
    against :func:`blocking_deps` in the tests.
    """
    return {
        tid: [
            dep if status is not None else f"{dep} (missing)"
            for dep, status in detail
        ]
        for tid, detail in blocking_dep_status_bulk(task_ids, conn).items()
    }


def blocking_dep_status_bulk(
    task_ids: Sequence[str], conn
) -> dict[str, list[tuple[str, str | None]]]:
    """``{task: [(blocking dep id, its status or None when the row is gone)]}``.

    The status is carried because "blocked by g-00" and "blocked by g-00
    (in_progress)" are different amounts of help to whoever is trying to work out
    why the board is not draining, and re-querying for it per row is what made
    the wrong answer easy to reach in the first place.
    """
    ids = [str(t) for t in task_ids if t]
    if not ids:
        return {}

    statuses: dict[str, str] = {}
    titles: dict[str, str] = {}
    for row in conn.execute("SELECT id, status, title FROM kanban_tasks").fetchall():
        rid = _row_get(row, "id")
        if rid is None:
            continue
        statuses[str(rid)] = str(_row_get(row, "status") or "")
        titles[str(rid)] = str(_row_get(row, "title") or "")

    scalars: dict[str, str] = {}
    for row in conn.execute(
        "SELECT id, depends_on_task_id FROM kanban_tasks "
        "WHERE depends_on_task_id IS NOT NULL"
    ).fetchall():
        scalars[str(_row_get(row, "id"))] = str(_row_get(row, "depends_on_task_id"))

    junction: dict[str, list[str]] = {}
    try:
        for row in conn.execute(
            "SELECT task_id, depends_on_id FROM kanban_task_deps"
        ).fetchall():
            junction.setdefault(str(_row_get(row, "task_id")), []).append(
                str(_row_get(row, "depends_on_id"))
            )
    except Exception:  # noqa: BLE001 — migration 041 may not have run
        junction = {}

    out: dict[str, list[tuple[str, str | None]]] = {}
    for tid in ids:
        scalar = scalars.get(tid)
        gating = effective_dep_ids(
            scalar,
            junction.get(tid, ()),
            scalar_is_gate=bool(scalar)
            and is_manual_gate(scalar, titles.get(scalar)),
        )
        out[tid] = [
            (dep, statuses.get(dep))
            for dep in gating
            if statuses.get(dep) not in SATISFIED_STATUSES
        ]
    return out


def parent_holds_done(task_id: str, conn) -> tuple[bool, str | None]:
    """``(may_complete, reason)`` for the done-transition guards.

    The guard exists because a row set to ``done`` without its prerequisite is
    the E-gate incident class. It must ask the same question dispatch asked:
    holding a FINISHED task because its seeding predecessor is still open
    refuses work that was never sequential to begin with.
    """
    try:
        blocking = blocking_deps(task_id, conn)
    except Exception:  # noqa: BLE001 — a guard that cannot read must not wedge
        return True, None
    if not blocking:
        return True, None
    return False, f"dependency {blocking[0]!r} is not done"


# ────────────────────────────────────────────────────────────────────────────
# SQL rendering — for the set-based dispatch query
# ────────────────────────────────────────────────────────────────────────────

def dep_clause_sql(alias: str = "kt") -> tuple[str, tuple[str, str]]:
    """``(clause, params)`` — the rule as a WHERE fragment over ``<alias>``.

    Splice the params into the call site's tuple AT THE POSITION the clause
    appears in the statement. Returned rather than interpolated so the ``%``
    wildcards never reach psycopg's format pass.
    """
    a = alias
    clause = (
        "("
        # The scalar gates only when it is not superseded — or when it is a gate.
        "  ("
        f"    {a}.depends_on_task_id IS NULL"
        "     OR EXISTS (SELECT 1 FROM kanban_tasks dep"
        f"                WHERE dep.id = {a}.depends_on_task_id"
        "                  AND dep.status IN ('done', 'decomposed'))"
        "     OR (EXISTS (SELECT 1 FROM kanban_task_deps d0"
        f"                 WHERE d0.task_id = {a}.id)"
        "         AND NOT EXISTS (SELECT 1 FROM kanban_tasks g"
        f"                         WHERE g.id = {a}.depends_on_task_id"
        "                           AND (g.id LIKE %s"
        "                                OR COALESCE(g.title, '') LIKE %s)))"
        "  )"
        # Every junction dep must be satisfied. LEFT JOIN, not JOIN: a dep
        # pointing at a deleted row must block, exactly as the Python does.
        "  AND NOT EXISTS (SELECT 1 FROM kanban_task_deps d2"
        "                  LEFT JOIN kanban_tasks p ON p.id = d2.depends_on_id"
        f"                  WHERE d2.task_id = {a}.id"
        "                    AND (p.id IS NULL"
        "                         OR p.status NOT IN ('done', 'decomposed')))"
        ")"
    )
    return clause, (SQL_GATE_ID_PATTERN, SQL_GATE_TITLE_PATTERN)


def sql_recognises_gate(task_id: str | None, title: str | None) -> bool:
    """What :func:`dep_clause_sql`'s LIKE pair matches, evaluated in Python.

    Only exists so the two renderings can be asserted against each other: this
    must be TRUE everywhere :func:`is_manual_gate` is, and is allowed to be true
    in more places (it holds work; being wide is the safe direction).
    """
    tid = str(task_id or "")
    return "-gate-" in tid or "MANUAL-MODE GATE" in (title or "")


__all__: Sequence[str] = (
    "SATISFIED_STATUSES",
    "SQL_GATE_ID_PATTERN",
    "SQL_GATE_TITLE_PATTERN",
    "blocking_deps",
    "blocking_deps_bulk",
    "blocking_dep_status_bulk",
    "dep_clause_sql",
    "deps_satisfied",
    "effective_dep_ids",
    "junction_dep_ids",
    "parent_holds_done",
    "scalar_is_superseded",
    "sql_recognises_gate",
)
