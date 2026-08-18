#!/usr/bin/env python3
# CUI // SP-CTI
"""What would the identity check have refused on the REAL board? (rem-hyg-03)

THE SURVEY THAT COMES BEFORE THE ARMING
=======================================
``tools/kanban/task_identity.py`` (rem-hyg-02) is wired REPORT-ONLY into
``task_factory.create_tasks``. Arming it — turning an unclaimed id into a refused
seed — is rem-hyg-04, and CLAUDE.md's rule says the measurement comes first:

    Never enable a security hook without a fire-rate survey first.

That rule was learned expensively. Over 96,818 real tool calls, eight of the
twelve PreToolUse checks were refusing routine work — 4.86% of all calls — and
the worst refused one call in forty. The check that motivated the survey,
``check_write_outside_worktree``, had shipped as a hard block a task earlier and
its rate had *still* never been observed, because ``|| true`` was discarding
everything it returned. **A check that is nominally enforcing but never measured
is UNMEASURED, not proven.**

This module is that measurement for the identity check: replay
:func:`~tools.kanban.task_identity.resolve` over every id the board actually
holds, and count what an armed rem-hyg-04 would have refused.

WHAT THE MEASUREMENT DECIDED (rem-hyg-04)
=========================================
Run against the live board it answered 35.17% naive / 10.85% narrowed lifetime /
**15.81% narrowed over 30 days**, so rem-hyg-04 shipped the switch defaulting to
``report`` rather than ``enforce``. ``NARROWED`` is not a hypothetical column any
more: it is computed through
:func:`~tools.kanban.task_identity.is_enforceable`, the same predicate
``task_factory`` calls, so the number here is exactly the population
``KANBAN_IDENTITY_CHECK=enforce`` refuses. Re-run this before ever changing that
default, and read ``enforcement.mode`` in the output to see which posture the
rate is currently describing.

REPORT ONLY. It refuses nothing, writes nothing, and has no ``--gate``. The one
database statement is a ``SELECT``.

WHAT IT COUNTS, AND WHY THE BUCKETS DO NOT MERGE
================================================
``claimed``
    An epic key claims the row: ``<task_prefix><epic_key>-<N>``. A card figure
    counts it. This is the population an armed check must leave alone.
``gate_sentinel``
    A ``<prefix>gate-<n>`` hold sentinel. It holds the card, it is not work, and
    it must never appear in a progress figure — so it is counted **separately
    and never as an orphan**. Folding gates into the orphan bucket would inflate
    the fire rate by one row per manual-mode card (30 cards declare ``gate``)
    and make arming look far more dangerous than it is.
``no_epic``
    A card owns the prefix but no epic key claims the id. The row is counted by
    NOTHING and the card's percentage silently describes a subset. ACTIONABLE.
``no_card``
    No registered ``task_prefix`` owns the id at all — the HCX case, where 25
    live rows existed under a prefix no card had ever declared. ACTIONABLE.

``actionable = no_epic + no_card``, which is exactly
:data:`~tools.kanban.task_identity.ACTIONABLE_REASONS`. That set is imported
rather than restated: a second copy of "is this a finding?" is how the two
halves drift.

THE TWO ZEROES THAT ARE NOT A CLEAN BILL OF HEALTH
==================================================
Both report ``measured: false`` and neither is ever rendered as "0 findings",
because a survey that could not run must not read as a survey that found
nothing — that is the ``|| true`` failure in report form.

``no_registry``
    ``args/projects.yaml`` unreadable. Every id would resolve to ``no_card``,
    i.e. 3,000 fabricated findings rather than a measurement.
``empty_board``
    No rows. This is the **worktree trap**, and it is the likely one: a git
    worktree has no ``.env``, so ``get_connection()`` falls back to a throwaway
    SQLite database that has never held a task. The sweep would report a 0%
    fire rate over 0 rows and read as "safe to arm". Point at the real board
    with ``--env-file`` (or a configured environment) and check ``board.backend``
    in the output.

WHY A RECENT WINDOW IS REPORTED SEPARATELY
==========================================
rem-hyg-04 would refuse at SEED time, so it acts on ids being created now, not
on the historical board. A lifetime rate over 3,000 mostly-``done`` rows
describes years of seeding conventions that have since changed; the recent
windows describe the population arming would actually hit. Both are reported,
and the headline rate is the lifetime one only because it is the larger and more
conservative number.

Usage::

    python -m tools.kanban.identity_survey --json
    python -m tools.kanban.identity_survey                    # per-card table
    python -m tools.kanban.identity_survey --card pgrt --ids
    python -m tools.kanban.identity_survey --status backlog --status scheduled
    python -m tools.kanban.identity_survey --env-file C:/AI/ICDev/.env --json

Task ids are not CUI, so ``--json`` includes the unclaimed ids: the report is
evidence for rem-hyg-04 and a count without the ids it refers to cannot be
acted on.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence

# rem-hyg-02 precedent: run by path, sys.path[0] is this file's own directory and
# never the import root. Resolved from __file__ and NEVER os.getcwd(), because
# this is run from worktrees.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.kanban.task_identity import (  # noqa: E402
    ACTIONABLE_REASONS,
    REASON_CLAIMED,
    REASON_GATE,
    REASON_NO_CARD,
    REASON_NO_EPIC,
    MODE_ENV,
    REASON_NOT_ID_SHAPED,
    SHAPE_CARD,
    SHAPE_OPAQUE,
    classify_shape,
    is_enforceable,
    load_cards,
    mode,
    resolve,
    suggested_id,
    unregistered_prefixes,
)
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

#: Reported alongside the lifetime rate. rem-hyg-04 refuses at SEED time, so the
#: population it would actually hit is recent, not the whole board.
DEFAULT_WINDOWS_DAYS = (7, 30, 90)

#: ``measured: false`` values. Neither is ever rendered as "0 findings".
UNMEASURABLE_NO_REGISTRY = "no_registry"
UNMEASURABLE_EMPTY_BOARD = "empty_board"

# ``classify_shape``, ``SHAPE_CARD`` and ``SHAPE_OPAQUE`` were written here and
# MOVED to task_identity when rem-hyg-04 armed the check. They stay importable
# from this module because that is where every existing caller looks for them,
# but there is deliberately only one copy: this survey's NARROWED column has to
# be the exact population ``KANBAN_IDENTITY_CHECK=enforce`` refuses, and two
# copies of the predicate is how a surveyed rate and an enforced rate drift
# apart while both look measured.


# --------------------------------------------------------------------------
# board
# --------------------------------------------------------------------------
def read_board(conn=None, statuses: Optional[Sequence] = None) -> list:
    """Every ``(id, status, created_at)`` on the board. The only DB access here.

    Read-only by construction: one ``SELECT``. Returns ``[]`` (loudly) rather
    than raising when the table is unreachable — the caller reports that as
    UNMEASURABLE, never as a clean sweep.

    ``conn`` is borrowed, never closed: closing a caller-owned connection is the
    defect ctx-obs fixed in the IQE adapters.
    """
    wanted = [str(s).strip() for s in (statuses or []) if str(s).strip()]
    sql = "SELECT id, status, created_at FROM kanban_tasks"
    if wanted:
        # Only "%s" placeholders are interpolated — one per status — and every
        # status value is bound as a parameter. Nothing from the caller reaches
        # the SQL text. Same idiom as landed_check._board_ids.
        sql += " WHERE status IN (%s)" % ", ".join(["%s"] * len(wanted))  # nosec B608

    def _run(c) -> list:
        rows = c.execute(sql, tuple(wanted)).fetchall() if wanted else c.execute(sql).fetchall()
        out = []
        for row in rows:
            # psycopg2 DictRow and sqlite3.Row both index by name; a plain tuple
            # does not, so fall back to position.
            try:
                out.append((row["id"], row["status"], row["created_at"]))
            except (TypeError, KeyError, IndexError):
                out.append((row[0], row[1], row[2]))
        return out

    try:
        if conn is not None:
            return _run(conn)
        from tools.db.storage import get_connection

        with get_connection() as own:
            return _run(own)
    except Exception as exc:  # noqa: BLE001 — a report must not wedge on the DB
        logger.warning("identity_survey: cannot read kanban_tasks (%s) — board NOT measured", exc)
        return []


def _backend_name() -> str:
    """Which backend the sweep actually read, so an empty result is diagnosable."""
    try:
        import os

        return str(os.getenv("ICDEV_STORAGE_BACKEND") or "sqlite").strip().lower()
    except Exception:  # noqa: BLE001
        return "unknown"


def _created_within(created_at, days: int, now=None) -> bool:
    """Was this row created in the last ``days``? Unparseable timestamps say no.

    ``created_at`` is a free-text column, so a value this cannot read is EXCLUDED
    from the window rather than counted into it — a window that silently absorbs
    every unparseable row is not a window.
    """
    from datetime import datetime, timedelta, timezone

    if not created_at:
        return False
    raw = str(created_at).strip().replace("Z", "+00:00")
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return ts >= ref - timedelta(days=days)


# --------------------------------------------------------------------------
# survey
# --------------------------------------------------------------------------
def _blank_counts() -> dict:
    return {
        REASON_CLAIMED: 0,
        REASON_GATE: 0,
        REASON_NO_EPIC: 0,
        REASON_NO_CARD: 0,
        REASON_NOT_ID_SHAPED: 0,
    }


def _rate(actionable: int, total: int) -> float:
    return round(actionable / total, 6) if total else 0.0


def survey(rows: Iterable, cards: Optional[Sequence] = None,
           windows: Sequence = DEFAULT_WINDOWS_DAYS, now=None) -> dict:
    """Replay :func:`resolve` over the whole board and aggregate it.

    ``rows`` are ``(id, status, created_at)`` tuples. ``cards`` is the registry,
    loaded once — omitting it re-reads ``args/projects.yaml``.

    The return value always carries ``measured``. When it is False, every count
    is absent rather than zero, so no caller can mistake "could not run" for
    "found nothing".
    """
    registry = list(cards) if cards is not None else load_cards()
    rows = [(str(r[0] or "").strip(), r[1], r[2]) for r in rows]

    if not registry:
        return {
            "measured": False,
            "unmeasurable_reason": UNMEASURABLE_NO_REGISTRY,
            "detail": (
                "args/projects.yaml could not be read, so no card claims anything and "
                "every one of the %d board rows would resolve to 'no_card'. That is a "
                "fabrication, not a measurement." % len(rows)
            ),
            "board": {"rows": len(rows), "backend": _backend_name()},
            "registry": {"cards": 0},
        }

    if not rows:
        return {
            "measured": False,
            "unmeasurable_reason": UNMEASURABLE_EMPTY_BOARD,
            "detail": (
                "kanban_tasks holds no rows on the '%s' backend. A 0%% fire rate over 0 "
                "rows is not evidence that arming is safe. If this ran from a git "
                "worktree it read a throwaway SQLite database — point at the real board "
                "with --env-file." % _backend_name()
            ),
            "board": {"rows": 0, "backend": _backend_name()},
            "registry": {"cards": len(registry)},
        }

    totals = _blank_counts()
    by_card: dict = {}
    unclaimed: list = []
    shapes = {SHAPE_CARD: 0, SHAPE_OPAQUE: 0}
    window_counts = {int(d): {"rows": 0, "actionable": 0, "narrowed": 0} for d in windows}

    for tid, status, created_at in rows:
        report = resolve(tid, registry)
        reason = report["reason"]
        totals[reason] = totals.get(reason, 0) + 1

        actionable = reason in ACTIONABLE_REASONS
        # An opaque no_card id is not a missing card, so it is not part of the
        # narrowed population an armed check should act on. Asked through
        # task_identity.is_enforceable — the predicate the armed seeder itself
        # calls — so this column cannot describe a different population from the
        # one KANBAN_IDENTITY_CHECK=enforce would actually refuse.
        shape = classify_shape(tid) if reason == REASON_NO_CARD else None
        if shape:
            shapes[shape] += 1
        narrowed = is_enforceable(reason, tid)
        for days, bucket in window_counts.items():
            if _created_within(created_at, days, now=now):
                bucket["rows"] += 1
                if actionable:
                    bucket["actionable"] += 1
                if narrowed:
                    bucket["narrowed"] += 1

        # no_card rows belong to no card by definition; they are grouped by
        # apparent prefix in `unregistered_prefixes` instead.
        key = report["card"]
        if key:
            entry = by_card.setdefault(key, {
                "card": key, "prefix": report["prefix"],
                "rows": 0, "unclaimed_ids": [], **_blank_counts(),
            })
            entry["rows"] += 1
            entry[reason] = entry.get(reason, 0) + 1
            if reason == REASON_NO_EPIC:
                entry["unclaimed_ids"].append(tid)

        if actionable:
            owner = next((c for c in registry if c.prefix == report["prefix"]), None)
            unclaimed.append({
                "task_id": tid, "reason": reason, "status": status,
                "card": report["card"], "prefix": report["prefix"],
                "shape": shape, "narrowed": narrowed,
                "suggestion": suggested_id(tid, owner),
            })

    # Cards declaring epics but owning no board row at all — not a finding, but
    # the population an armed check would never see, so it belongs in the same
    # report as the denominator.
    seen = set(by_card)
    empty_cards = sorted(c.key for c in registry if c.key not in seen)

    for entry in by_card.values():
        entry["unclaimed_ids"].sort()
        entry["actionable"] = entry[REASON_NO_EPIC]

    orphan_prefixes = unregistered_prefixes([r[0] for r in rows], registry)
    # An unregistered prefix ALL of whose ids are opaque is an autonomous
    # writer's namespace, not a card somebody forgot to register. Split so the
    # "register these cards" list is the one a human can act on.
    prefix_report = {}
    for prefix, ids in orphan_prefixes.items():
        card_ids = [i for i in ids if classify_shape(i) == SHAPE_CARD]
        prefix_report[prefix] = {
            "count": len(ids),
            "card_shaped": len(card_ids),
            "opaque": len(ids) - len(card_ids),
            "ids": ids,
        }

    actionable_total = totals[REASON_NO_EPIC] + totals[REASON_NO_CARD]
    narrowed_total = totals[REASON_NO_EPIC] + shapes[SHAPE_CARD]
    return {
        "measured": True,
        "unmeasurable_reason": None,
        # What the seeder would do RIGHT NOW with these numbers. A survey that
        # did not say which posture is live leaves the reader unable to tell a
        # rate that is being enforced from one that is only being logged.
        "enforcement": {"mode": mode(), "refuses": "actionable_narrowed"},
        "board": {"rows": len(rows), "backend": _backend_name()},
        "registry": {
            "cards": len(registry),
            "cards_with_epics": sum(1 for c in registry if c.epics),
            "cards_with_no_epics": sorted(c.key for c in registry if not c.epics),
        },
        "totals": {
            "rows": len(rows),
            "claimed": totals[REASON_CLAIMED],
            "gate_sentinels": totals[REASON_GATE],
            "no_epic": totals[REASON_NO_EPIC],
            "no_card": totals[REASON_NO_CARD],
            "not_id_shaped": totals[REASON_NOT_ID_SHAPED],
            "no_card_card_shaped": shapes[SHAPE_CARD],
            "no_card_opaque": shapes[SHAPE_OPAQUE],
            "actionable": actionable_total,
            "fire_rate": _rate(actionable_total, len(rows)),
            # The rate an armed check would carry if it exempted opaque machine
            # ids — the same narrowing the PreToolUse survey applied to go from
            # 4.86% to 1.63%.
            "actionable_narrowed": narrowed_total,
            "fire_rate_narrowed": _rate(narrowed_total, len(rows)),
        },
        "windows": [
            {
                "days": d,
                "rows": window_counts[d]["rows"],
                "actionable": window_counts[d]["actionable"],
                "fire_rate": _rate(window_counts[d]["actionable"], window_counts[d]["rows"]),
                "actionable_narrowed": window_counts[d]["narrowed"],
                "fire_rate_narrowed": _rate(window_counts[d]["narrowed"], window_counts[d]["rows"]),
            }
            for d in sorted(window_counts)
        ],
        "by_card": sorted(
            by_card.values(),
            key=lambda e: (-e["actionable"], -e["rows"], e["card"]),
        ),
        "cards_owning_no_rows": empty_cards,
        "unregistered_prefixes": prefix_report,
        "unclaimed": sorted(unclaimed, key=lambda u: u["task_id"]),
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def render(report: dict, show_ids: bool = False) -> str:
    """The human half: a per-card table plus the headline rate."""
    if not report.get("measured"):
        return (
            "IDENTITY SURVEY -- NOT MEASURED (%s)\n%s\n\nNothing was counted. This is NOT "
            "a 0%% fire rate." % (report.get("unmeasurable_reason"), report.get("detail", ""))
        )

    t = report["totals"]
    out = [
        "IDENTITY SURVEY -- what an armed rem-hyg-04 would have refused",
        "=" * 66,
        "board            %d rows (%s backend)" % (report["board"]["rows"], report["board"]["backend"]),
        "registry         %d cards, %d declaring epics" % (
            report["registry"]["cards"], report["registry"]["cards_with_epics"]),
        "",
        "claimed          %6d   an epic counts the row -- arming leaves these alone" % t["claimed"],
        "gate sentinels   %6d   holds the card, never work, NEVER an orphan" % t["gate_sentinels"],
        "no_epic          %6d   card owns the prefix, no epic claims it   ACTIONABLE" % t["no_epic"],
        "no_card          %6d   no registered task_prefix owns it         ACTIONABLE" % t["no_card"],
        "  |- card-shaped  %6d     a card is genuinely missing" % t["no_card_card_shaped"],
        "  `- opaque       %6d     task-<hex> / <name>-reflex-<hex> -- NEVER card work" % t["no_card_opaque"],
        "not id-shaped    %6d   blank id -- task_factory already skips these" % t["not_id_shaped"],
        "-" * 66,
        "ACTIONABLE       %6d   fire rate %.2f%%  (refuse every unclaimed id)" % (
            t["actionable"], t["fire_rate"] * 100),
        "NARROWED         %6d   fire rate %.2f%%  (exempt opaque machine ids)" % (
            t["actionable_narrowed"], t["fire_rate_narrowed"] * 100),
        "",
        "%s=%s -- %s" % (
            MODE_ENV, report["enforcement"]["mode"],
            {"enforce": "the NARROWED population above is REFUSED at seed time",
             "report": "every ACTIONABLE row is logged; nothing is refused",
             "off": "the check does not run at all"}[report["enforcement"]["mode"]],
        ),
        "",
        "Recent windows (rem-hyg-04 refuses at SEED time, so this is the population",
        "arming would actually hit):",
        "  %-10s %7s %12s %8s %11s %8s" % ("WINDOW", "ROWS", "ACTIONABLE", "RATE", "NARROWED", "RATE"),
    ]
    for w in report["windows"]:
        out.append("  last %3dd  %7d %12d %7.2f%% %11d %7.2f%%" % (
            w["days"], w["rows"], w["actionable"], w["fire_rate"] * 100,
            w["actionable_narrowed"], w["fire_rate_narrowed"] * 100))

    cards = [c for c in report["by_card"] if c["actionable"]]
    out += ["", "Cards owning rows no epic claims (%d):" % len(cards), ""]
    if cards:
        out.append("  %-22s %-12s %6s %8s %6s %6s" % (
            "CARD", "PREFIX", "ROWS", "CLAIMED", "GATE", "ORPHAN"))
        for c in cards:
            out.append("  %-22s %-12s %6d %8d %6d %6d" % (
                c["card"][:22], c["prefix"][:12], c["rows"],
                c[REASON_CLAIMED], c[REASON_GATE], c["actionable"]))
            if show_ids:
                for tid in c["unclaimed_ids"]:
                    out.append("        %s" % tid)
    else:
        out.append("  (none -- every card's rows are claimed by an epic)")

    unreg = report["unregistered_prefixes"]
    missing = {p: i for p, i in unreg.items() if i["card_shaped"]}
    writers = {p: i for p, i in unreg.items() if not i["card_shaped"]}

    out += ["", "Prefixes NO card declares, holding card-shaped ids (%d) -- register these:" % len(missing), ""]
    if missing:
        out.append("  %-14s %6s %12s %8s" % ("PREFIX", "ROWS", "CARD-SHAPED", "OPAQUE"))
        for prefix, info in sorted(missing.items(), key=lambda kv: -kv[1]["card_shaped"]):
            out.append("  %-14s %6d %12d %8d" % (
                prefix, info["count"], info["card_shaped"], info["opaque"]))
            if show_ids:
                for tid in info["ids"]:
                    out.append("        %s" % tid)
    else:
        out.append("  (none)")

    out += ["", "Prefixes NO card declares, ENTIRELY opaque (%d) -- autonomous writers," % len(writers),
            "not missing cards. An armed check must exempt these:", ""]
    if writers:
        for prefix, info in sorted(writers.items(), key=lambda kv: -kv[1]["count"]):
            out.append("  %-14s %6d rows" % (prefix, info["count"]))
    else:
        out.append("  (none)")

    return "\n".join(out)


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------
def main(argv: Optional[Sequence] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Survey what the rem-hyg-02 identity check would refuse on the real board "
                    "(rem-hyg-03). REPORT ONLY — refuses nothing, writes nothing.")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--ids", action="store_true",
                    help="list the individual unclaimed ids in the table")
    ap.add_argument("--card", action="append", default=[],
                    help="restrict the table to these card keys (repeatable)")
    ap.add_argument("--status", action="append", default=[],
                    help="only rows in these statuses (repeatable; default all)")
    ap.add_argument("--window", action="append", type=int, default=[],
                    help="recent-window size in days (repeatable; default 7/30/90)")
    ap.add_argument("--env-file",
                    help="load this .env before connecting — a git worktree has none, so "
                         "get_connection() would otherwise read a throwaway SQLite database")
    args = ap.parse_args(argv)

    if args.env_file:
        try:
            from dotenv import load_dotenv

            load_dotenv(args.env_file, override=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("identity_survey: could not load %s (%s)", args.env_file, exc)

    report = survey(
        read_board(statuses=args.status or None),
        windows=[w for w in args.window if w > 0] or DEFAULT_WINDOWS_DAYS,
    )

    if args.card and report.get("measured"):
        wanted = set(args.card)
        report["by_card"] = [c for c in report["by_card"] if c["card"] in wanted]

    print(json.dumps(report, indent=2, default=str) if args.json
          else render(report, show_ids=args.ids))
    # Report only: a finding is not a failure, and there is deliberately no --gate.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
