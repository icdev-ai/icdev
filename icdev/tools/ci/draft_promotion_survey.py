# CUI // SP-CTI
"""kpr-watch-06 — is the draft -> ready -> merged round trip actually turning over?

Every kanban PR now OPENS as a draft (``tools/genesis/reflexes/kanban.py::
_pr_opens_as_draft``) and ``pr_watcher.PRWatcher._mark_ready`` is the single
point that promotes one. That inversion makes the hold a database row instead of
an external poller — and it makes ONE new failure mode possible, which is
strictly worse than the default it replaced: a draft nobody promotes is a
stalled pipeline, and a stalled pipeline is quiet. This module is what makes it
loud, as a number rather than as a backlog somebody eventually notices.

Four quantities, kept apart on purpose:

``opened_per_hour``
    The rate the card asks to compare before and after. A promotion regression
    does NOT change it — the runner keeps opening PRs — so it is the control,
    not the signal.
``promotions``
    ``pr_watcher.auto_ready`` rows in ``audit_trail``. The watcher writes one per
    PR, on success only, so this counts promotions and never polls.
``merged``
    PRs that actually landed in the window. ``promotions`` without ``merged`` is
    a merge problem; ``merged`` without ``promotions`` means those PRs were not
    drafts (a pre-inversion PR, or ``ICDEV_KANBAN_PR_DRAFT=0``).
``stuck``
    Open kanban PRs still in draft older than ``--stuck-hours``. THIS is the
    regression signal, and it is the one the old default could not produce.

A ZERO IS NOT A VERDICT. ``unmeasurable`` is reported when ``gh`` cannot answer
or the window holds no kanban PRs at all, because a repository with no operating
history in the window and a pipeline that has stopped promoting are different
things and must not print the same 0. ``promotions`` is ``None`` — never 0 —
when the audit trail cannot be read.

REPORT ONLY. There is deliberately no ``--gate``: this measures a pipeline whose
own latency is hours, and a gate on it would be neutralised inside a week.

    python -m tools.ci.draft_promotion_survey --json
    python -m tools.ci.draft_promotion_survey --window-hours 168
    python -m tools.ci.draft_promotion_survey --stuck-hours 6
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

DEFAULT_WINDOW_HOURS = 168
DEFAULT_STUCK_HOURS = 6
KANBAN_BRANCH_PREFIX = "kanban/"

#: Only success rows. ``_mark_ready`` never audits a refusal — a hold repeats on
#: every poll and would flood the trail — so this action counts promotions
#: exactly, one row per promoted PR.
PROMOTION_ACTION = "pr_watcher.auto_ready"


def _parse_ts(value: Any) -> Optional[datetime]:
    """GitHub ISO-8601 -> aware datetime, or None when unreadable."""
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def fetch_prs(*, limit: int = 300, runner=None, gh_bin: str = "gh") -> Optional[List[dict]]:
    """Every recent PR, or None when gh cannot answer.

    None, not ``[]`` — "the forge did not reply" and "there are no PRs" are two
    causes of the same empty list and only one of them is measurable.
    """
    runner = runner or subprocess.run
    try:
        proc = runner(
            [gh_bin, "pr", "list", "--state", "all", "--limit", str(limit),
             "--json", "number,headRefName,createdAt,mergedAt,closedAt,"
                       "isDraft,state,url"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
    except Exception:  # noqa: BLE001 — an unreachable forge is unmeasurable
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    try:
        data = json.loads(getattr(proc, "stdout", "") or "[]")
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, list) else None


def count_promotions(get_conn, since: datetime) -> Optional[int]:
    """``pr_watcher.auto_ready`` rows since ``since``, or None when unreadable."""
    try:
        conn = get_conn()
    except Exception:  # noqa: BLE001
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_trail "
            "WHERE action = %s AND created_at >= %s",
            (PROMOTION_ACTION, since.isoformat()),
        ).fetchone()
    except Exception:  # noqa: BLE001 — a missing/renamed table is unmeasurable
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if row is None:
        return 0
    value = row["n"] if isinstance(row, dict) else row[0]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def survey(
    prs: Optional[List[dict]],
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    stuck_hours: int = DEFAULT_STUCK_HOURS,
    now: Optional[datetime] = None,
    promotions: Optional[int] = None,
) -> Dict[str, Any]:
    """The pure half — no gh, no database, so it is testable without either."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, int(window_hours)))
    out: Dict[str, Any] = {
        "window_hours": int(window_hours),
        "stuck_hours": int(stuck_hours),
        "generated_at": now.isoformat(),
        "promotions": promotions,
        "unmeasurable": None,
    }
    if prs is None:
        out["unmeasurable"] = "gh could not list pull requests"
        return out

    kanban = [p for p in prs
              if str(p.get("headRefName") or "").startswith(KANBAN_BRANCH_PREFIX)]
    in_window = [p for p in kanban
                 if (_parse_ts(p.get("createdAt")) or now) >= cutoff]
    if not in_window:
        out["unmeasurable"] = (
            "no kanban PR was opened in the last %dh — nothing to describe"
            % window_hours)
        return out

    merged = [p for p in in_window if (p.get("state") or "").upper() == "MERGED"]
    open_prs = [p for p in kanban if (p.get("state") or "").upper() == "OPEN"]
    stuck_cutoff = now - timedelta(hours=max(0, int(stuck_hours)))
    stuck = [
        {
            "url": p.get("url"),
            "branch": p.get("headRefName"),
            "age_hours": round(
                (now - (_parse_ts(p.get("createdAt")) or now)).total_seconds() / 3600.0, 1),
        }
        for p in open_prs
        if p.get("isDraft") and (_parse_ts(p.get("createdAt")) or now) <= stuck_cutoff
    ]

    out.update({
        "opened": len(in_window),
        "opened_per_hour": round(len(in_window) / float(window_hours), 3),
        "merged": len(merged),
        "open_now": len(open_prs),
        "open_drafts_now": sum(1 for p in open_prs if p.get("isDraft")),
        "stuck_count": len(stuck),
        # Oldest first: the longest-stalled draft is the one to look at.
        "stuck": sorted(stuck, key=lambda s: -s["age_hours"]),
    })
    return out


def _render(report: Dict[str, Any]) -> str:
    if report.get("unmeasurable"):
        return "UNMEASURABLE: %s" % report["unmeasurable"]
    promo = report.get("promotions")
    lines = [
        "kanban draft -> ready -> merged, last %dh" % report["window_hours"],
        "  opened          %d  (%.3f/hour)" % (report["opened"], report["opened_per_hour"]),
        "  merged          %d" % report["merged"],
        "  promotions      %s" % ("unreadable (audit_trail)" if promo is None else promo),
        "  open now        %d  (%d still draft)"
        % (report["open_now"], report["open_drafts_now"]),
        "  stuck >%dh      %d" % (report["stuck_hours"], report["stuck_count"]),
    ]
    for s in report.get("stuck", []):
        lines.append("      %s  %s  %.1fh" % (s["branch"], s["url"], s["age_hours"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="kanban draft promotion survey (report only)")
    ap.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS)
    ap.add_argument("--stuck-hours", type=int, default=DEFAULT_STUCK_HOURS)
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    prs = fetch_prs(limit=args.limit)
    try:
        from tools.db.storage import get_connection

        promotions = count_promotions(
            get_connection,
            datetime.now(timezone.utc) - timedelta(hours=args.window_hours),
        )
    except Exception:  # noqa: BLE001 — an unreadable ledger reports None
        promotions = None

    report = survey(
        prs,
        window_hours=args.window_hours,
        stuck_hours=args.stuck_hours,
        promotions=promotions,
    )
    print(json.dumps(report, indent=2) if args.json else _render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
