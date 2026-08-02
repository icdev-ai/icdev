# CUI // SP-CTI
"""Score history — persist a scorecard evaluation per component, per window.

``tools/idp/scorecard.py`` (idp-score-02) computes a component's standing —
weighted score plus attained ladder level — and then throws it away. That is
the same shape as every other live signal in the platform: canvas health RAG,
canvas compliance posture, platform health, all recomputed per request and
never kept. Measured 2026-08-02: 25 of the 29 score/readiness/grade tables in
the database hold zero rows, and the single working score time-series
(``readiness_scores``) is keyed to an intake session rather than a component.
So nothing can answer the one question a scorecard exists to answer: *is this
component getting better or worse?*

This module is the missing half. It follows the shape already proven in
``tools/requirements/readiness_scorer.py`` — one persisted row per evaluation
plus a ``get_score_trend()`` reader — pointed at components.

    # record one point per component (always writes)
    python tools/idp/score_history.py --record

    # record only if the scorecard's evaluation window has rolled over —
    # this is how the scheduled reflex calls it
    python tools/idp/score_history.py --record --if-due

    # read one component's series back
    python tools/idp/score_history.py --trend ndc --json

    # every ladder promotion/demotion across the estate
    python tools/idp/score_history.py --level-changes

Two things make the series worth keeping rather than just a number over time:

**The ladder level is a column, not a derived value.** A component can gain
five points without changing anything an operator cares about, and it can lose
one point and fall off Gold. ``ladder_level``/``level_rank`` are persisted with
every point, so a promotion or demotion is a comparison of two adjacent rows —
no re-evaluation of historical rule sets required (which would be impossible
anyway, since the rules are config and change over time).

**The window is the cadence, not a uniqueness constraint.** ``window_start`` is
``evaluated_at`` floored to the scorecard's declared ``evaluation.window``
bucket. ``if_due=True`` skips a run whose bucket is already recorded, which is
what lets a 3h reflex feed a 24h scorecard without writing eight identical
rows a day. An explicit ``--record`` always writes, so re-scoring right after a
fix shows the improvement immediately instead of waiting out the window.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.idp.scorecard import (  # noqa: E402
    Scorecard,
    ScorecardError,
    evaluate,
    load_scorecard,
    load_scorecards,
)

TABLE = "idp_scorecard_history"
CLASSIFICATION = "CUI // SP-CTI"

#: Columns written by :func:`persist_evaluation`. Asserted against the
#: migration's DDL by the tests — an INSERT naming a column the live schema
#: lacks raises, gets swallowed, and reports success while persisting nothing.
INSERT_COLUMNS = (
    "id",
    "run_id",
    "scorecard_key",
    "component_key",
    "evaluated_at",
    "window_start",
    "window_label",
    "ladder_level",
    "level_rank",
    "score",
    "earned_weight",
    "total_weight",
    "failing_rules",
    "rule_outcomes",
    "tenant_id",
    "classification",
)

#: Statuses whose rule identifiers are kept per point. ``pass`` is omitted on
#: purpose — it is the complement of the other three and storing it would
#: roughly double the row for no new information.
_RECORDED_STATUSES = ("fail", "exempt", "not_applicable")

_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_WINDOW_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

#: Default when a scorecard declares no window. Matches the awareness reflex's
#: 3h cadence so a recorder running alongside it writes one point per cycle.
DEFAULT_WINDOW_SECONDS = 3 * 3600


class ScoreHistoryError(RuntimeError):
    """Raised when history cannot be persisted or read."""


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def parse_window(window: str | None) -> int:
    """Parse a duration like ``24h`` / ``4h`` / ``30m`` into seconds.

    Returns :data:`DEFAULT_WINDOW_SECONDS` for an empty value; raises for a
    malformed one, because silently falling back on a typo would make the
    cadence differ from the one the YAML plainly states.
    """
    if not window or not str(window).strip():
        return DEFAULT_WINDOW_SECONDS
    match = _WINDOW_RE.match(str(window))
    if not match:
        raise ScoreHistoryError(
            f"unparseable evaluation window {window!r} — expected <n><s|m|h|d|w>, e.g. '4h'"
        )
    amount = int(match.group(1))
    if amount <= 0:
        raise ScoreHistoryError(f"evaluation window {window!r} must be positive")
    return amount * _WINDOW_UNITS[match.group(2).lower()]


def window_start(now: datetime, window_seconds: int) -> str:
    """Floor *now* to its window bucket, as an ISO-8601 UTC timestamp.

    Buckets are anchored on the epoch, so a 4h window aligns to 00/04/08/…
    UTC and a 24h window to UTC midnight — the same bucket regardless of which
    process computes it or when it started.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    epoch = int(now.astimezone(timezone.utc).timestamp())
    floored = epoch - (epoch % window_seconds)
    return datetime.fromtimestamp(floored, tz=timezone.utc).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


def _open_connection() -> Any:
    from tools.db.storage import get_connection  # noqa: PLC0415

    return get_connection()


def _rows(cursor_result: Any) -> list[dict[str, Any]]:
    """Normalise fetchall() output to plain dicts across both backends."""
    out: list[dict[str, Any]] = []
    for row in cursor_result or []:
        if isinstance(row, dict):
            out.append(dict(row))
        else:
            try:
                out.append(dict(row))
            except (TypeError, ValueError):  # pragma: no cover — tuple cursor
                out.append({"_row": row})
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _outcome_summary(rule_rows: list[dict[str, Any]]) -> tuple[int, str]:
    """Compact the per-rule outcomes to (failing_count, JSON status map)."""
    grouped: dict[str, list[str]] = {status: [] for status in _RECORDED_STATUSES}
    for rule in rule_rows:
        status = str(rule.get("status") or "")
        if status in grouped:
            grouped[status].append(str(rule.get("identifier") or ""))
    summary = {status: ids for status, ids in grouped.items() if ids}
    return len(grouped["fail"]), json.dumps(summary, sort_keys=True)


def persist_evaluation(
    report: dict[str, Any],
    conn: Any = None,
    *,
    run_id: str | None = None,
    now: datetime | None = None,
    window: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Write one history row per component in an :func:`evaluate` *report*.

    Always writes — the window check lives in :func:`record_scorecard`, so a
    caller that wants a point right now gets one.

    Returns ``{run_id, window_start, rows_written, components}``.
    """
    own = conn is None
    conn = conn or _open_connection()
    run_id = run_id or uuid.uuid4().hex
    now = now or _utcnow()
    evaluated_at = now.astimezone(timezone.utc).isoformat()
    window_label = str(window or report.get("window") or "")
    bucket = window_start(now, parse_window(window_label))
    placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))
    sql = (
        f"INSERT INTO {TABLE} ({', '.join(INSERT_COLUMNS)}) VALUES ({placeholders})"
    )

    written = 0
    components: list[str] = []
    try:
        for result in report.get("results") or []:
            failing, outcomes = _outcome_summary(result.get("rules") or [])
            conn.execute(
                sql,
                (
                    uuid.uuid4().hex,
                    run_id,
                    str(report.get("scorecard") or ""),
                    str(result.get("entity") or ""),
                    evaluated_at,
                    bucket,
                    window_label,
                    result.get("level"),
                    int(result.get("level_rank") or 0),
                    float(result.get("score") or 0.0),
                    int(result.get("earned_weight") or 0),
                    int(result.get("total_weight") or 0),
                    failing,
                    outcomes,
                    tenant_id,
                    CLASSIFICATION,
                ),
            )
            written += 1
            components.append(str(result.get("entity") or ""))
        conn.commit()
    finally:
        if own:
            conn.close()

    return {
        "run_id": run_id,
        "scorecard": report.get("scorecard"),
        "window_start": bucket,
        "window": window_label,
        "evaluated_at": evaluated_at,
        "rows_written": written,
        "components": components,
    }


def last_recorded_window(scorecard_key: str, conn: Any) -> str | None:
    """The most recent ``window_start`` recorded for *scorecard_key*."""
    rows = _rows(
        conn.execute(
            f"SELECT window_start FROM {TABLE} WHERE scorecard_key = %s "
            "ORDER BY window_start DESC LIMIT 1",
            (scorecard_key,),
        ).fetchall()
    )
    return str(rows[0]["window_start"]) if rows else None


def is_due(
    scorecard: Scorecard, conn: Any, now: datetime | None = None
) -> tuple[bool, str, str | None]:
    """Is a fresh evaluation due for *scorecard*?

    Returns ``(due, current_window_start, last_recorded_window)``. Due when the
    current bucket has not been recorded yet — which also makes the first ever
    run due, and makes a re-run inside the same bucket a no-op.
    """
    bucket = window_start(now or _utcnow(), parse_window(scorecard.window))
    last = last_recorded_window(scorecard.key, conn)
    return (last != bucket, bucket, last)


def record_scorecard(
    scorecard: Scorecard | str,
    conn: Any = None,
    *,
    if_due: bool = False,
    now: datetime | None = None,
    tenant_id: str | None = None,
    directory: Path | str | None = None,
) -> dict[str, Any]:
    """Evaluate *scorecard* and persist the result.

    With ``if_due=True`` the run is skipped when the current evaluation window
    has already been recorded — the mode the scheduled reflex uses, so its
    cadence can be finer than the scorecard's window without duplicating rows.
    """
    card = (
        scorecard
        if isinstance(scorecard, Scorecard)
        else load_scorecard(str(scorecard), directory)
    )
    own = conn is None
    conn = conn or _open_connection()
    try:
        due, bucket, last = is_due(card, conn, now=now)
        if if_due and not due:
            return {
                "status": "skipped",
                "reason": "window already recorded",
                "scorecard": card.key,
                "window_start": bucket,
                "last_window": last,
                "rows_written": 0,
            }
        report = evaluate(card, conn=conn)
        written = persist_evaluation(
            report,
            conn,
            now=now,
            window=card.window,
            tenant_id=tenant_id,
        )
        written["status"] = "recorded"
        written["level_distribution"] = report.get("level_distribution")
        return written
    finally:
        if own:
            conn.close()


def record_all(
    conn: Any = None,
    *,
    if_due: bool = False,
    now: datetime | None = None,
    directory: Path | str | None = None,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Record every scorecard on disk."""
    own = conn is None
    conn = conn or _open_connection()
    try:
        return [
            record_scorecard(
                card, conn, if_due=if_due, now=now, tenant_id=tenant_id
            )
            for card in load_scorecards(directory)
        ]
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

_TREND_COLUMNS = (
    "run_id, evaluated_at, window_start, window_label, ladder_level, level_rank, "
    "score, earned_weight, total_weight, failing_rules, rule_outcomes"
)


def _decode_outcomes(row: dict[str, Any]) -> dict[str, Any]:
    """Parse ``rule_outcomes`` in Python — never with backend JSON SQL.

    CLAUDE.md: runtime SQL is authored for PostgreSQL and SQLite-dialect JSON
    functions must not be load-bearing. Reading the raw column and calling
    ``json.loads`` is portable by construction.
    """
    raw = row.get("rule_outcomes")
    if isinstance(raw, dict):
        row["rule_outcomes"] = raw
        return row
    try:
        row["rule_outcomes"] = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        row["rule_outcomes"] = {}
    return row


def _level_changes(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ladder transitions between adjacent points, oldest first."""
    changes: list[dict[str, Any]] = []
    for previous, current in zip(points, points[1:]):
        if previous.get("ladder_level") == current.get("ladder_level"):
            continue
        before = int(previous.get("level_rank") or 0)
        after = int(current.get("level_rank") or 0)
        changes.append(
            {
                "at": current.get("evaluated_at"),
                "from": previous.get("ladder_level"),
                "to": current.get("ladder_level"),
                "from_rank": before,
                "to_rank": after,
                "direction": "promotion" if after > before else "demotion",
            }
        )
    return changes


def get_score_trend(
    component: str,
    scorecard_key: str | None = None,
    conn: Any = None,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """One component's score series, oldest point first.

    Mirrors ``readiness_scorer.get_score_trend`` — same contract, different
    subject — and adds ``level_changes``, because a ladder demotion is the
    event an operator actually wants paged about; a two-point score dip inside
    the same level usually is not.
    """
    own = conn is None
    conn = conn or _open_connection()
    try:
        where = ["component_key = %s"]
        params: list[Any] = [component]
        if scorecard_key:
            where.append("scorecard_key = %s")
            params.append(scorecard_key)
        params.append(int(limit))
        rows = _rows(
            conn.execute(
                f"SELECT {_TREND_COLUMNS} FROM {TABLE} WHERE {' AND '.join(where)} "
                "ORDER BY evaluated_at DESC LIMIT %s",
                tuple(params),
            ).fetchall()
        )
    finally:
        if own:
            conn.close()

    # Newest-first in SQL so LIMIT keeps the RECENT points, oldest-first in the
    # payload so a caller can plot it without reversing.
    points = [_decode_outcomes(row) for row in reversed(rows)]
    delta = (
        round(float(points[-1]["score"]) - float(points[0]["score"]), 1)
        if len(points) > 1
        else 0.0
    )
    if len(points) < 2:
        direction = "unknown"
    elif delta > 0:
        direction = "improving"
    elif delta < 0:
        direction = "declining"
    else:
        direction = "flat"

    return {
        "status": "ok",
        "component": component,
        "scorecard": scorecard_key,
        "data_points": len(points),
        "trend": points,
        "current_score": points[-1]["score"] if points else None,
        "current_level": points[-1]["ladder_level"] if points else None,
        "score_delta": delta,
        "direction": direction,
        "level_changes": _level_changes(points),
    }


def get_level_changes(
    scorecard_key: str | None = None,
    conn: Any = None,
    *,
    since: str | None = None,
    limit_per_component: int = 50,
) -> dict[str, Any]:
    """Every ladder promotion/demotion across the estate, newest first.

    Grouping and comparison happen in Python rather than as a windowed SQL
    query: the row count here is components × retained points, and the
    portability rule pushes anything cleverer than a filtered SELECT out of
    SQL anyway.
    """
    own = conn is None
    conn = conn or _open_connection()
    try:
        where: list[str] = []
        params: list[Any] = []
        if scorecard_key:
            where.append("scorecard_key = %s")
            params.append(scorecard_key)
        if since:
            where.append("evaluated_at >= %s")
            params.append(since)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = _rows(
            conn.execute(
                f"SELECT component_key, scorecard_key, evaluated_at, ladder_level, "
                f"level_rank, score FROM {TABLE} {clause} "
                "ORDER BY component_key, evaluated_at",
                tuple(params),
            ).fetchall()
        )
    finally:
        if own:
            conn.close()

    by_component: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_component.setdefault(str(row.get("component_key")), []).append(row)

    changes: list[dict[str, Any]] = []
    for component, points in by_component.items():
        for change in _level_changes(points[-limit_per_component:]):
            change["component"] = component
            change["scorecard"] = points[-1].get("scorecard_key")
            changes.append(change)
    changes.sort(key=lambda c: str(c.get("at") or ""), reverse=True)

    return {
        "status": "ok",
        "scorecard": scorecard_key,
        "since": since,
        "components_with_history": len(by_component),
        "change_count": len(changes),
        "promotions": sum(1 for c in changes if c["direction"] == "promotion"),
        "demotions": sum(1 for c in changes if c["direction"] == "demotion"),
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_record(results: list[dict[str, Any]]) -> str:
    lines = []
    for result in results:
        if result.get("status") == "skipped":
            lines.append(
                f"{result['scorecard']}: skipped — window {result['window_start']} "
                "already recorded"
            )
            continue
        lines.append(
            f"{result['scorecard']}: recorded {result['rows_written']} component(s) "
            f"at {result['evaluated_at']} (window {result['window_start']}, "
            f"{result.get('window') or 'default'})"
        )
        dist = result.get("level_distribution") or {}
        if dist:
            lines.append("  ladder: " + ", ".join(f"{k}={v}" for k, v in dist.items()))
    return "\n".join(lines)


def _render_trend(trend: dict[str, Any]) -> str:
    lines = [
        f"{trend['component']} — {trend['data_points']} point(s), "
        f"{trend['direction']} ({trend['score_delta']:+.1f})",
        "",
        f"{'evaluated_at':<28} {'level':<12} {'score':>6}  failing",
        "-" * 62,
    ]
    for point in trend["trend"]:
        lines.append(
            f"{str(point['evaluated_at']):<28} {point['ladder_level'] or '-':<12} "
            f"{float(point['score']):>5.0f}%  {point['failing_rules']}"
        )
    if trend["level_changes"]:
        lines.append("")
        lines.append("Ladder changes:")
        for change in trend["level_changes"]:
            lines.append(
                f"  {change['at']}  {change['from'] or 'unranked'} -> "
                f"{change['to'] or 'unranked'}  ({change['direction']})"
            )
    return "\n".join(lines)


def _render_changes(payload: dict[str, Any]) -> str:
    lines = [
        f"{payload['change_count']} ladder change(s) across "
        f"{payload['components_with_history']} component(s) with history "
        f"({payload['promotions']} up, {payload['demotions']} down)",
        "",
    ]
    for change in payload["changes"]:
        lines.append(
            f"{str(change['at']):<28} {change['component']:<24} "
            f"{change['from'] or 'unranked'} -> {change['to'] or 'unranked'}  "
            f"({change['direction']})"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python tools/idp/score_history.py",
        description="Persist and read per-component scorecard history.",
    )
    ap.add_argument("--record", action="store_true", help="Evaluate and persist a point")
    ap.add_argument(
        "--if-due",
        action="store_true",
        help="With --record: skip when the current evaluation window is already recorded",
    )
    ap.add_argument("--scorecard", metavar="KEY", help="Limit to one scorecard")
    ap.add_argument("--trend", metavar="COMPONENT", help="Show one component's series")
    ap.add_argument(
        "--level-changes", action="store_true", help="Show ladder promotions/demotions"
    )
    ap.add_argument("--since", metavar="ISO8601", help="With --level-changes: lower bound")
    ap.add_argument("--limit", type=int, default=100, help="Max points per component")
    ap.add_argument("--dir", metavar="PATH", help="Scorecard directory")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args(argv)

    if not (args.record or args.trend or args.level_changes):
        ap.error("one of --record, --trend or --level-changes is required")

    conn = None
    try:
        conn = _open_connection()
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot open the database: {exc}", file=sys.stderr)
        return 2

    try:
        if args.record:
            if args.scorecard:
                results = [
                    record_scorecard(
                        args.scorecard,
                        conn,
                        if_due=args.if_due,
                        directory=args.dir,
                    )
                ]
            else:
                results = record_all(conn, if_due=args.if_due, directory=args.dir)
            print(
                json.dumps(results, indent=2, default=str)
                if args.json
                else _render_record(results)
            )

        if args.trend:
            trend = get_score_trend(
                args.trend, args.scorecard, conn, limit=args.limit
            )
            print(
                json.dumps(trend, indent=2, default=str)
                if args.json
                else _render_trend(trend)
            )

        if args.level_changes:
            payload = get_level_changes(args.scorecard, conn, since=args.since)
            print(
                json.dumps(payload, indent=2, default=str)
                if args.json
                else _render_changes(payload)
            )
    except (ScorecardError, ScoreHistoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001, S110
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
