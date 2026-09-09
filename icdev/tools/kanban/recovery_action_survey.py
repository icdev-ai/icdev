# CUI // SP-CTI
"""Two row sets, one measurement -- what does widening the recovery detector cost?

THE DEFECT THIS SURVEYS (autonomy-act-05). Three readers answer "did pr_watcher
recover this PR" from ``audit_trail`` and they did NOT fetch the same rows:

  * ``tools/dashboard/app.py`` (the Home panel) reads the EXPORTED
    ``recovery_summary.AUDIT_ACTIONS`` -- eight action names;
  * ``tools/kanban/detector_findings.py::recovery_rows`` and
    ``tools/awareness/claims.py::_recovery_rows`` each hard-coded the
    pre-rmf-disc-01 FOUR-value literal.

So rmf-disc-01's widening -- which taught the classifier that ``rebase_failed``
and ``ci_retrigger`` are ATTEMPTS -- reached the panel a human reads and never
reached the two readers that FILE AND CLEAR CARDS.

WIDENING IS NOT PURELY ADDITIVE, AND THAT IS WHY IT NEEDED A SURVEY. Three
distinct effects move in three directions:

  ADDS ATTEMPTS   ``rebase_failed`` / ``ci_retrigger`` rows raise ``attempts``
                  and can move ``at`` (the newest counted attempt) FORWARD,
                  which pushes ``earliest_clear_at`` LATER -- every affected
                  finding lives longer.
  SUBTRACTS       ``resume_refund`` / ``rebase_refund`` rows DECREMENT
                  ``attempts``. The legacy set fetched neither, so it counted a
                  withdrawn attempt the watcher's own accounting had already
                  cancelled -- and a task whose every attempt was refunded is
                  dropped entirely by the shipped set.
  ADDS SUBJECTS   a task attempted ONLY by ``rebase_failed`` / ``ci_retrigger``
                  is INVISIBLE to the legacy set -- it can be escalated and draw
                  no finding at all, because a reader cannot see an attempt kind
                  it does not fetch.

WHAT IS REPLAYED. The SHIPPED reduction, never a copy: ``summarize_recovery``
collapses the rows and ``recovery_findings`` turns entries into findings. ONLY
THE ROW FILTER DIFFERS between the two arms -- that is the whole experiment, and
re-deriving the collapse here would measure this file instead of the detector.

THE GRID. ``detector_runs`` is a per-detector ROLLUP (one row carrying a ``runs``
counter) and not a run log, so there is no recorded per-run instant to anchor
on. The reflex is ``every 6h`` with ``window_hours: 24``
(args/genesis_config.yaml), so the replay walks 6h boundaries across the
recorded corpus and asks each arm the detector's own question at each one.
Stated rather than implied: these are REPLAY instants at the reflex's cadence,
not the runs the rollup counts.

``task_status`` IS NOT REPLAYED, and does not need to be. It separates
``recovered`` from ``unresolved`` only -- ``escalated`` is tested FIRST and wins
-- and ``recovery_findings`` emits ``needed_a_human`` and nothing else. A
historical board status is unknowable anyway; asserting one would be the
fabrication these surveys exist to refuse.

Report only, no ``--gate`` (kpr-fix-03): it measures the BOARD, not a diff.
Exit 0 = a report was produced, whatever it says; exit 2 = it could not be,
which is never the same as a clean survey.

    python -m tools.kanban.recovery_action_survey
    python -m tools.kanban.recovery_action_survey --json
    python -m tools.kanban.recovery_action_survey --window-hours 24 --step-hours 6
    python -m tools.kanban.recovery_action_survey --at 2026-09-09T02:35:00Z
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tools.dashboard.recovery_summary import AUDIT_ACTIONS, summarize_recovery
from tools.kanban.detector_findings import recovery_findings

#: THE HISTORICAL RULE, LABELLED HISTORY. This four-value literal is what
#: ``detector_findings.recovery_rows`` and ``claims._recovery_rows`` hard-coded
#: before autonomy-act-05, copied here verbatim so the survey can replay it. It
#: lives ONLY in this survey and is never the shipped rule -- "today" is always
#: asked of ``AUDIT_ACTIONS``, the exported declaration, and never of a copy.
LEGACY_AUDIT_ACTIONS: Tuple[str, ...] = (
    "pr_watcher.rebase",
    "pr_watcher.resume",
    "pr_watcher.escalate",
    "pr_watcher.merge",
)

ARM_SHIPPED = "shipped"
ARM_LEGACY = "legacy"


class SurveyError(RuntimeError):
    """The survey could not be produced. Never a clean survey (exit 2)."""


def _parse_instant(text: str) -> datetime:
    raw = str(text).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            return _parse_instant(value)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# Corpus
# --------------------------------------------------------------------------- #
def load_corpus(conn) -> List[Dict[str, Any]]:
    """Every audit row EITHER arm could fetch, once, ordered.

    The union of both action sets is read in ONE query and each arm then filters
    it in Python. Two SQL reads taken minutes apart on a live board describe two
    different boards, and the delta between them would be attributed to the rule
    under test.
    """
    actions = tuple(sorted(set(AUDIT_ACTIONS) | set(LEGACY_AUDIT_ACTIONS)))
    pg = str(getattr(conn, "_backend", "")).startswith("postgres")
    details = "details::text" if pg else "details"
    placeholders = ",".join(["%s"] * len(actions))
    try:
        rows = conn.execute(
            f"SELECT action, {details} AS d, created_at FROM audit_trail "  # nosec B608
            f"WHERE action IN ({placeholders}) ORDER BY created_at",
            actions,
        ).fetchall()
    except Exception as exc:  # pragma: no cover - environment dependent
        raise SurveyError(f"could not read audit_trail: {exc}") from exc
    out: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        at = _as_utc(record.get("created_at"))
        if at is None:
            continue
        record["_at"] = at
        out.append(record)
    return out


def _window(corpus: Sequence[Mapping[str, Any]], arm: Sequence[str],
            at: datetime, window_hours: int) -> List[Dict[str, Any]]:
    keep = set(arm)
    cut = at - timedelta(hours=int(window_hours))
    return [dict(r) for r in corpus
            if r["action"] in keep and cut <= r["_at"] <= at]


def _findings(rows: Sequence[Mapping[str, Any]],
              window_hours: int) -> Dict[str, Dict[str, Any]]:
    """Subject -> finding, through the SHIPPED reduction."""
    entries = summarize_recovery(list(rows), limit=10_000)
    return {str(f["subject"]): dict(f)
            for f in recovery_findings(entries, window_hours, rows=list(rows))}


# --------------------------------------------------------------------------- #
# One instant
# --------------------------------------------------------------------------- #
def compare_at(corpus: Sequence[Mapping[str, Any]], at: datetime,
               window_hours: int) -> Dict[str, Any]:
    """Both arms at ONE instant. Pure over ``corpus``."""
    shipped = _findings(_window(corpus, AUDIT_ACTIONS, at, window_hours), window_hours)
    legacy = _findings(_window(corpus, LEGACY_AUDIT_ACTIONS, at, window_hours), window_hours)

    added = sorted(set(shipped) - set(legacy))
    removed = sorted(set(legacy) - set(shipped))
    common = sorted(set(shipped) & set(legacy))

    title_changes: List[Dict[str, Any]] = []
    clear_changes: List[Dict[str, Any]] = []
    for subject in common:
        s, lg = shipped[subject], legacy[subject]
        if s.get("title") != lg.get("title"):
            title_changes.append({
                "subject": subject,
                "legacy_title": lg.get("title"),
                "shipped_title": s.get("title"),
                "legacy_attempts": (lg.get("evidence") or {}).get("attempts"),
                "shipped_attempts": (s.get("evidence") or {}).get("attempts"),
                "legacy_kind": (lg.get("evidence") or {}).get("kind"),
                "shipped_kind": (s.get("evidence") or {}).get("kind"),
            })
        if s.get("earliest_clear_at") != lg.get("earliest_clear_at"):
            s_clear = _as_utc(s.get("earliest_clear_at"))
            l_clear = _as_utc(lg.get("earliest_clear_at"))
            # None -- NEVER 0.0 -- when either side has no clear time: an
            # unmeasurable delta and a delta of zero justify opposite reads.
            delta = (round((s_clear - l_clear).total_seconds() / 3600.0, 4)
                     if s_clear is not None and l_clear is not None else None)
            clear_changes.append({
                "subject": subject,
                "legacy_earliest_clear_at": lg.get("earliest_clear_at"),
                "shipped_earliest_clear_at": s.get("earliest_clear_at"),
                "delta_hours": delta,
            })

    return {
        "at": at.isoformat(),
        "window_hours": window_hours,
        "shipped_findings": len(shipped),
        "legacy_findings": len(legacy),
        "added": added,
        "removed": removed,
        "title_changes": title_changes,
        "clear_changes": clear_changes,
        "shipped_subjects": sorted(shipped),
        "legacy_subjects": sorted(legacy),
    }


# --------------------------------------------------------------------------- #
# The replay
# --------------------------------------------------------------------------- #
def survey(corpus: Sequence[Mapping[str, Any]], *, window_hours: int = 24,
           step_hours: int = 6, since: Optional[datetime] = None,
           until: Optional[datetime] = None) -> Dict[str, Any]:
    """Replay both arms across the recorded corpus. Pure over ``corpus``."""
    if not corpus:
        # A board with no pr_watcher history cannot say whether widening costs
        # anything. UNMEASURABLE, never "the two rules agree".
        return {
            "state": "unmeasurable",
            "reason": "no pr_watcher audit rows on this deployment",
            "window_hours": window_hours, "step_hours": step_hours,
            "instants": 0, "corpus_rows": 0,
            "action_counts": {}, "instants_detail": [],
            "subjects_added": [], "subjects_removed": [],
            "title_changes": [], "clear_changes": [],
            "instants_with_added": None, "instants_with_removed": None,
            "instants_with_title_change": None, "instants_with_clear_change": None,
            "max_clear_delta_hours": None, "min_clear_delta_hours": None,
            "median_clear_delta_hours": None,
        }

    first, last = corpus[0]["_at"], corpus[-1]["_at"]
    start = since or (first + timedelta(hours=window_hours))
    end = until or last
    if start > end:
        start = end

    instants: List[datetime] = []
    cursor = start
    step = timedelta(hours=int(step_hours))
    while cursor <= end:
        instants.append(cursor)
        cursor += step
    if not instants or instants[-1] != end:
        instants.append(end)

    details = [compare_at(corpus, at, window_hours) for at in instants]

    added_subjects: Dict[str, str] = {}
    removed_subjects: Dict[str, str] = {}
    title_subjects: Dict[str, Dict[str, Any]] = {}
    clear_subjects: Dict[str, Dict[str, Any]] = {}
    clear_deltas: List[float] = []
    for d in details:
        for s in d["added"]:
            added_subjects.setdefault(s, d["at"])
        for s in d["removed"]:
            removed_subjects.setdefault(s, d["at"])
        for t in d["title_changes"]:
            title_subjects.setdefault(t["subject"], {**t, "first_seen_at": d["at"]})
        for c in d["clear_changes"]:
            clear_subjects.setdefault(c["subject"], {**c, "first_seen_at": d["at"]})
            if c["delta_hours"] is not None:
                clear_deltas.append(c["delta_hours"])

    action_counts: Dict[str, int] = {}
    for r in corpus:
        action_counts[r["action"]] = action_counts.get(r["action"], 0) + 1

    ordered = sorted(clear_deltas)
    if not ordered:
        median = None
    elif len(ordered) % 2:
        median = ordered[len(ordered) // 2]
    else:
        median = round((ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2, 4)

    return {
        "state": "measured",
        "window_hours": window_hours,
        "step_hours": step_hours,
        "corpus_rows": len(corpus),
        "corpus_first_at": first.isoformat(),
        "corpus_last_at": last.isoformat(),
        "action_counts": action_counts,
        "shipped_actions": list(AUDIT_ACTIONS),
        "legacy_actions": list(LEGACY_AUDIT_ACTIONS),
        "actions_only_shipped": sorted(set(AUDIT_ACTIONS) - set(LEGACY_AUDIT_ACTIONS)),
        "actions_only_legacy": sorted(set(LEGACY_AUDIT_ACTIONS) - set(AUDIT_ACTIONS)),
        "instants": len(instants),
        "first_instant": instants[0].isoformat(),
        "last_instant": instants[-1].isoformat(),
        "instants_with_added": sum(1 for d in details if d["added"]),
        "instants_with_removed": sum(1 for d in details if d["removed"]),
        "instants_with_title_change": sum(1 for d in details if d["title_changes"]),
        "instants_with_clear_change": sum(1 for d in details if d["clear_changes"]),
        "subjects_added": [{"subject": s, "first_at": a}
                           for s, a in sorted(added_subjects.items())],
        "subjects_removed": [{"subject": s, "first_at": a}
                             for s, a in sorted(removed_subjects.items())],
        "title_changes": sorted(title_subjects.values(), key=lambda x: x["subject"]),
        "clear_changes": sorted(clear_subjects.values(), key=lambda x: x["subject"]),
        "max_clear_delta_hours": max(clear_deltas) if clear_deltas else None,
        "min_clear_delta_hours": min(clear_deltas) if clear_deltas else None,
        "median_clear_delta_hours": median,
        "instants_detail": details,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _render(report: Mapping[str, Any]) -> str:
    if report.get("state") == "unmeasurable":
        return ("recovery action survey: UNMEASURABLE -- "
                f"{report.get('reason')}\nThis is not a clean survey.")
    lines = [
        "Recovery-evidence row sets: shipped vs legacy",
        f"  corpus         {report['corpus_rows']} rows, "
        f"{report['corpus_first_at']} .. {report['corpus_last_at']}",
        f"  replay         {report['instants']} instants, every {report['step_hours']}h, "
        f"{report['window_hours']}h window",
        f"  only shipped   {', '.join(report['actions_only_shipped']) or '(none)'}",
        f"  only legacy    {', '.join(report['actions_only_legacy']) or '(none)'}",
        "",
        "Row counts in the corpus (* = shipped only):",
    ]
    for action, n in sorted(report["action_counts"].items(), key=lambda kv: -kv[1]):
        mark = " " if action in report["legacy_actions"] else "*"
        lines.append(f"  {mark} {action:<34} {n}")
    lines += [
        "",
        f"Findings ADDED by the shipped set:   {len(report['subjects_added'])} subject(s) "
        f"over {report['instants_with_added']} instant(s)",
    ]
    for item in report["subjects_added"]:
        lines.append(f"    + {item['subject']}  (first at {item['first_at']})")
    lines.append(
        f"Findings REMOVED by the shipped set: {len(report['subjects_removed'])} subject(s) "
        f"over {report['instants_with_removed']} instant(s)")
    for item in report["subjects_removed"]:
        lines.append(f"    - {item['subject']}  (first at {item['first_at']})")
    lines += [
        "",
        f"Card TITLES changed: {len(report['title_changes'])} subject(s) "
        f"over {report['instants_with_title_change']} instant(s)",
    ]
    for t in report["title_changes"][:60]:
        lines.append(f"    {t['subject']}: {t['legacy_attempts']} {t['legacy_kind']} "
                     f"-> {t['shipped_attempts']} {t['shipped_kind']}")
    lines += [
        "",
        f"earliest_clear_at changed: {len(report['clear_changes'])} subject(s) "
        f"over {report['instants_with_clear_change']} instant(s)",
        f"  delta hours  min {report['min_clear_delta_hours']}  "
        f"median {report['median_clear_delta_hours']}  max {report['max_clear_delta_hours']}",
    ]
    for c in report["clear_changes"][:60]:
        lines.append(f"    {c['subject']}: {c['legacy_earliest_clear_at']} -> "
                     f"{c['shipped_earliest_clear_at']}  ({c['delta_hours']}h)")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Replay the shipped and legacy recovery row sets over history.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--window-hours", type=int, default=24)
    ap.add_argument("--step-hours", type=int, default=6)
    ap.add_argument("--since", help="ISO instant; default = first row + window")
    ap.add_argument("--until", help="ISO instant; default = last recorded row")
    ap.add_argument("--at", help="compare ONE instant only")
    ap.add_argument("--detail", action="store_true",
                    help="include the per-instant table in --json output")
    args = ap.parse_args(list(argv) if argv is not None else None)

    from tools.db.storage import get_connection

    conn = None
    try:
        conn = get_connection()
        corpus = load_corpus(conn)
    except Exception as exc:
        print(f"survey could not be produced: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    try:
        if args.at:
            report: Dict[str, Any] = compare_at(
                corpus, _parse_instant(args.at), args.window_hours)
            print(json.dumps(report, indent=2, default=str))
            return 0
        since = _parse_instant(args.since) if args.since else None
        until = _parse_instant(args.until) if args.until else None
    except ValueError as exc:
        print(f"survey could not be produced: bad instant: {exc}", file=sys.stderr)
        return 2

    report = survey(corpus, window_hours=args.window_hours,
                    step_hours=args.step_hours, since=since, until=until)
    if args.json:
        payload = dict(report)
        if not args.detail:
            payload.pop("instants_detail", None)
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(_render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
