# CUI // SP-CTI
r"""Would a host-load rung before `git worktree add` have been RIGHT to refuse? NO.

SURVEYED, AND THE ANSWER IS THE DELIVERABLE. This is a measurement tool, not a
gate, and it is deliberately wired into NOTHING. `python -m tools.kanban.host_io
--survey` exists so the next person who reaches for a load gate gets the answer
in one command instead of rebuilding it -- the same shape and the same reason as
tools/kanban/landed_dispatch_survey.py (kpr-fix-03), whose own headline is
"Surveyed; answer is NO".

THE QUESTION. `git worktree add` runs under a 30s budget that may not rise and
may not be retried (both forbidden by kph-repark-kph-repark-mfx-ci-04). An add
takes 5.1-25.3s on a quiet host and 34.2-44.5s on a busy one, so a dispatch
issued into a loaded host is killed and its task PARKED by
`worktree-isolation-guard` -- three tasks in one evening, one needing a human.
mfx-own-06's cross-process lock removed the COLLISION half; four of the measured
kills had no other add in flight at all. The obvious next rung is "decline to
START an add when the host is slow", which is not the retry the dispatch site
forbids: a declined task is never dispatched and stays `scheduled`.

THE MEASUREMENT THAT KILLED IT, over the 86 adds this host recorded in 7 days
(13 killed, a 15.12% base rate):

  hypothesis 1 -- a SLOW add (>=70% of budget) predicts the next
      fires 5 (5.81%);  0 were killed anyway,  5 would have succeeded
      => precision 0%. Every refusal would have been WRONG.

  hypothesis 2 -- a KILLED add predicts the next, at five cooldowns
      60s    fires  3 ( 3.49%)  right 0  wrong  3   precision  0.0%
      180s   fires 11 (12.79%)  right 3  wrong  8   precision 27.3%
      300s   fires 11 (12.79%)  right 3  wrong  8   precision 27.3%
      600s   fires 11 (12.79%)  right 3  wrong  8   precision 27.3%
      1800s  fires 13 (15.12%)  right 3  wrong 10   precision 23.1%

Knowing the previous add was killed moves the odds from a 15.12% base rate to
about 25% -- while refusing eight good adds to catch three bad ones, at a fire
rate of 12.79%. CLAUDE.md stands a check down at 1.63% of routine work. Only 3
of 13 kills follow another kill, so the kills do not cluster: THE HOST'S STATE
AT THE LAST ADD DOES NOT PREDICT ITS STATE AT THE NEXT ONE, and no
parameterisation tested changes that. A rung built on this signal would refuse
routine work to prevent almost nothing.

DO NOT TUNE THE THRESHOLD UNTIL IT LOOKS GOOD. That is the move this repo's
survey discipline exists to prevent, and both hypotheses above were measured
with the SHIPPED predicate rather than a second copy of the rule.

WHAT WOULD ACTUALLY HELP, since this does not: make the add CHEAPER rather than
better-timed. The tree is ~20,200 files / 275 MB, and a worker needs a fraction
of it -- `--no-checkout` plus a sparse-checkout of the paths a task touches is
the untested lever, and it attacks the 34-44s duration itself rather than
guessing when to avoid it. That owes its own measurement and its own card.

WHY IT READS OUR OWN LOG RATHER THAN A SYSTEM COUNTER, recorded because it is
the first thing a reader will want to change. Measured on this host,
`Get-Counter '\PhysicalDisk(_Total)\Avg. Disk Queue Length'` costs 2-6s per
sample -- unaffordable on a per-dispatch check -- and is Windows-only, against
the standing rule that everything here is OS- and LLM-agnostic.
`os.getloadavg()` is POSIX-only and measures CPU, which is NOT the constraint:
during the slow window CPU was 15% while the disk queue was 25.8. Our own add
durations are already recorded, free to read, cross-platform, and measure the
thing that actually matters. The budget is stated IN each log line, so this
module never imports tools.genesis.reflexes.kanban -- that import would be
circular and would drag 13,552 lines into a cheap predicate.

A PROXY, AND LABELLED AS ONE on every verdict as `basis`: this measures recent
add LATENCY, not present host load.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# sys.path BOOTSTRAP first, so `python tools/kanban/host_io.py --json` reaches
# main() (kax-conflict-04); then the ONE root resolver.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)

#: The log `_create_worktree` writes every add to.
LOG_RELPATH = Path(".logs") / "tools.genesis.reflexes.kanban.ndjson"

#: An add is SLOW at or above this fraction of its own stated budget. 0.70 of a
#: 30s budget is 21s. MEASURED on this host: the quiet population tops out at
#: 25.3s and clusters at 4.9-18.3s, while every kill was 34.2s or worse -- so
#: this sits above the common case and below every failure, and a 25.0s add
#: (recorded 2026-09-11) correctly reads slow because it came within 5s of
#: being killed.
SLOW_BUDGET_FRACTION = float(os.environ.get("KANBAN_HOST_IO_SLOW_FRACTION", "0.70"))

#: How far back to look for evidence. Beyond this the host has had time to
#: change and an old add says nothing about now.
WINDOW_MINUTES = int(os.environ.get("KANBAN_HOST_IO_WINDOW_MINUTES", "20"))

#: How long a slow add suppresses the next dispatch. One add's own worst
#: observed duration, rounded up -- long enough that a queued dispatch is not
#: issued into the same spike, short enough that a recovered host is not held.
COOLDOWN_SECONDS = int(os.environ.get("KANBAN_HOST_IO_COOLDOWN_SECONDS", "60"))

#: Lines to read from the tail of the log for a LIVE verdict. The log is chatty
#: -- MEASURED on this host, 33,789 lines carry 160 add events, so an add is
#: roughly one line in 200 -- and a live verdict only needs WINDOW_MINUTES of
#: history, so this is sized to cover that with room rather than to find every
#: add. `survey` passes None and reads the whole file: a fire rate computed over
#: a truncated tail would report 1 add where the log holds 160, which is exactly
#: the fabricated-denominator defect this repo refuses.
TAIL_LINES = 4000

#: Cooldowns the survey replays hypothesis 2 over, so the docstring's table is
#: re-derived by the shipped tool rather than quoted from a scratch script.
KILL_PREDICTS_COOLDOWNS = (60, 180, 300, 600, 1800)

_OK_RE = re.compile(r"Created worktree for (?P<task>\S+) .*? in (?P<secs>[\d.]+)s \(budget (?P<budget>\d+)s\)")
_KILL_RE = re.compile(r"git worktree add for (?P<task>\S+) exceeded its (?P<budget>\d+)s budget after (?P<secs>[\d.]+)s")

# Verdict vocabulary is should_run's; this module returns plain dicts and never
# imports it, so the dependency runs one way only.
PROCEED = "proceed"
WAIT = "wait"
UNMEASURABLE = "unmeasurable"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def log_path() -> Path:
    return BASE_DIR / LOG_RELPATH


def _tail(path: Path, lines: Optional[int]) -> List[str]:
    """Last *lines* lines, without reading the whole file.

    The log is appended to continuously and is tens of MB; reading it whole on
    every dispatch candidate would cost more than the check saves.
    """
    if lines is None:
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    block = 64 * 1024
    data = b""
    with path.open("rb") as fh:
        while size > 0 and data.count(b"\n") <= lines:
            step = min(block, size)
            size -= step
            fh.seek(size)
            data = fh.read(step) + data
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]


def recent_adds(within_minutes: Optional[int] = None,
                path: Optional[Path] = None,
                tail_lines: Optional[int] = TAIL_LINES) -> Dict[str, Any]:
    """Every add this log records inside the window, newest first.

    Returns ``{"readable": bool, "adds": [...], "reason": str}``. An unreadable
    or absent log is ``readable: False`` -- never an empty list, because "no
    adds happened" and "we could not look" justify opposite decisions and this
    module's whole contract is that it never fabricates a clean bill of health.
    """
    window = WINDOW_MINUTES if within_minutes is None else within_minutes
    p = path or log_path()
    if not p.exists():
        return {"readable": False, "adds": [], "reason": f"no add log at {p}"}
    try:
        lines = _tail(p, tail_lines)
    except Exception as exc:  # noqa: BLE001 - an unreadable log must not raise into a dispatch
        return {"readable": False, "adds": [], "reason": f"add log unreadable: {exc}"}

    cutoff = _now() - timedelta(minutes=window)
    adds: List[Dict[str, Any]] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue  # a live append can tear the last line; that is normal
        msg = str(rec.get("message") or "")
        m = _OK_RE.search(msg)
        killed = False
        if m is None:
            m = _KILL_RE.search(msg)
            killed = m is not None
        if m is None:
            continue
        try:
            ts = datetime.fromisoformat(str(rec.get("ts", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        try:
            secs = float(m.group("secs"))
            budget = float(m.group("budget"))
        except (TypeError, ValueError):
            continue
        adds.append({
            "task_id": m.group("task"), "seconds": secs, "budget_seconds": budget,
            "killed": killed, "at": ts.isoformat(),
            # The budget is read from the LINE, never imported: the line states
            # the budget that actually applied to that add.
            "slow": killed or (budget > 0 and secs >= SLOW_BUDGET_FRACTION * budget),
        })
    adds.sort(key=lambda a: a["at"], reverse=True)
    return {"readable": True, "adds": adds, "reason": ""}


def assess(within_minutes: Optional[int] = None,
           path: Optional[Path] = None) -> Dict[str, Any]:
    """`proceed` | `wait` | `unmeasurable` on whether to START an add now.

    `wait` carries `resets_at`, so a caller can say WHEN rather than merely
    that it declined.
    """
    window = WINDOW_MINUTES if within_minutes is None else within_minutes
    found = recent_adds(window, path)
    base: Dict[str, Any] = {
        "basis": "recent_add_latency",
        "window_minutes": window,
        "slow_fraction": SLOW_BUDGET_FRACTION,
        "cooldown_seconds": COOLDOWN_SECONDS,
    }
    if not found["readable"]:
        return {"verdict": UNMEASURABLE, "reason": found["reason"], **base}

    adds = found["adds"]
    base["adds_in_window"] = len(adds)
    if not adds:
        # No evidence either way. NOT `proceed`: a quiet log and a quiet host are
        # different claims, and only a measurement distinguishes them.
        return {"verdict": UNMEASURABLE,
                "reason": f"no worktree add recorded in the last {window} min",
                **base}

    slow = [a for a in adds if a["slow"]]
    base["slow_recent"] = len(slow)
    base["last_add_seconds"] = adds[0]["seconds"]
    base["last_add_task"] = adds[0]["task_id"]

    if not slow:
        return {"verdict": PROCEED,
                "reason": (f"{len(adds)} add(s) in {window} min, none at or above "
                           f"{SLOW_BUDGET_FRACTION:.0%} of budget "
                           f"(newest {adds[0]['seconds']:.1f}s)"),
                **base}

    newest_slow = slow[0]
    try:
        slow_at = datetime.fromisoformat(newest_slow["at"])
    except ValueError:
        slow_at = _now()
    resets = slow_at + timedelta(seconds=COOLDOWN_SECONDS)
    base["resets_at"] = resets.isoformat()
    if resets <= _now():
        return {"verdict": PROCEED,
                "reason": (f"newest slow add ({newest_slow['seconds']:.1f}s, "
                           f"{newest_slow['task_id']}) is older than the "
                           f"{COOLDOWN_SECONDS}s cooldown"),
                **base}
    return {"verdict": WAIT,
            "reason": (f"host is slow: {newest_slow['task_id']}'s add took "
                       f"{newest_slow['seconds']:.1f}s of a "
                       f"{newest_slow['budget_seconds']:.0f}s budget"
                       f"{' and was KILLED' if newest_slow['killed'] else ''}; "
                       f"starting another now risks the same park"),
            **base}


def survey(days: float = 7.0, path: Optional[Path] = None) -> Dict[str, Any]:
    """What WOULD this rung have done, over the adds the log already holds.

    The fire rate is what decides whether it may ever be armed -- this repo
    stands a check down at 1.63% of routine work -- and it is derived by
    replaying the SHIPPED predicate over recorded adds, never a second copy of
    the rule.
    """
    # tail_lines=None: the WHOLE file. A fire rate over a truncated tail
    # invents its own denominator.
    found = recent_adds(int(days * 24 * 60), path, tail_lines=None)
    if not found["readable"]:
        return {"state": "unmeasurable", "reason": found["reason"],
                "adds": None, "would_wait": None, "fire_rate_pct": None}
    adds = found["adds"]
    if not adds:
        return {"state": "unmeasurable",
                "reason": f"no add recorded in {days} day(s)",
                "adds": 0, "would_wait": None, "fire_rate_pct": None}

    # An add would have been DECLINED if a slow add sat inside the cooldown
    # immediately before it. Replayed in recorded order, oldest first.
    ordered = list(reversed(adds))
    would_wait = 0
    # A REFUSAL IS NOT AUTOMATICALLY A COST. The question that decides whether
    # this rung may ever be armed is not how often it fires but whether it was
    # RIGHT -- the landed_dispatch_survey split. An add it declines that WAS
    # then killed is a park prevented; one that succeeded is a cycle spent.
    # Reported as two numbers and never as one rate.
    right, wrong = 0, 0
    for i, add in enumerate(ordered):
        prior = ordered[:i]
        if not prior:
            continue
        last = prior[-1]
        try:
            gap = (datetime.fromisoformat(add["at"])
                   - datetime.fromisoformat(last["at"])).total_seconds()
        except ValueError:
            continue
        if last["slow"] and gap < COOLDOWN_SECONDS:
            would_wait += 1
            if add["killed"]:
                right += 1
            else:
                wrong += 1
    killed = sum(1 for a in adds if a["killed"])
    slow = sum(1 for a in adds if a["slow"])

    # HYPOTHESIS 2, re-derivable rather than merely quoted in the docstring: does
    # a KILLED add predict the next one, at a range of cooldowns? A published
    # number in this repo is re-derived by the shipped tool, so the table in the
    # module docstring is produced HERE and not by a scratch script.
    by_cooldown: List[Dict[str, Any]] = []
    for cd in KILL_PREDICTS_COOLDOWNS:
        fires = right2 = wrong2 = 0
        for i in range(1, len(ordered)):
            prev, cur = ordered[i - 1], ordered[i]
            try:
                g = (datetime.fromisoformat(cur["at"])
                     - datetime.fromisoformat(prev["at"])).total_seconds()
            except ValueError:
                continue
            if prev["killed"] and g < cd:
                fires += 1
                right2 += 1 if cur["killed"] else 0
                wrong2 += 0 if cur["killed"] else 1
        by_cooldown.append({
            "cooldown_seconds": cd, "fires": fires,
            "fire_rate_pct": round(100.0 * fires / len(adds), 4) if adds else None,
            "right": right2, "wrong": wrong2,
            # None, never 0.0, when it never fired -- an unfired rule has no
            # precision, a different statement from a precision of zero.
            "precision_pct": round(100.0 * right2 / fires, 1) if fires else None,
        })
    consecutive_kill_pairs = sum(
        1 for i in range(1, len(ordered))
        if ordered[i - 1]["killed"] and ordered[i]["killed"]
    )
    return {
        "state": "measured",
        "days": days,
        "adds": len(adds),
        "slow": slow,
        "killed": killed,
        "would_wait": would_wait,
        "declined_and_would_have_been_killed": right,
        "declined_but_would_have_succeeded": wrong,
        # None, never 0.0, over an empty denominator (args/perfect_score_gate.yaml).
        "fire_rate_pct": round(100.0 * would_wait / len(adds), 4) if adds else None,
        "kill_rate_pct": round(100.0 * killed / len(adds), 4) if adds else None,
        "slow_fraction": SLOW_BUDGET_FRACTION,
        "cooldown_seconds": COOLDOWN_SECONDS,
        "kill_predicts_kill": by_cooldown,
        "consecutive_kill_pairs": consecutive_kill_pairs,
        "verdict": "do_not_arm",
        "verdict_reason": (
            "the host's state at the last add does not predict its state at the "
            "next one; every parameterisation tested refuses routine work to "
            "prevent almost nothing"
        ),
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Is this host too slow right now to start a worktree add?")
    ap.add_argument("--survey", action="store_true",
                    help="replay the shipped predicate over recorded adds")
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--window-minutes", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    out = survey(args.days) if args.survey else assess(args.window_minutes)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    if args.survey:
        if out["state"] != "measured":
            print(f"UNMEASURABLE: {out['reason']}")
            return 0
        print(f"{out['adds']} add(s) over {out['days']} day(s): "
              f"{out['slow']} slow, {out['killed']} killed")
        print(f"would have declined {out['would_wait']} "
              f"({out['fire_rate_pct']}%) at slow>={out['slow_fraction']:.0%} "
              f"of budget, cooldown {out['cooldown_seconds']}s")
        print(f"  of those: {out['declined_and_would_have_been_killed']} were "
              f"KILLED anyway (a park prevented), "
              f"{out['declined_but_would_have_succeeded']} would have succeeded "
              f"(a cycle spent)")
        print("")
        print(f"base kill rate {out['kill_rate_pct']}%; consecutive kill pairs "
              f"{out['consecutive_kill_pairs']} of {out['killed']}")
        print("does a KILLED add predict the next?")
        for row in out["kill_predicts_kill"]:
            print(f"  cooldown {row['cooldown_seconds']:>5}s: fires {row['fires']:>2} "
                  f"({row['fire_rate_pct']}%)  right {row['right']:>2}  "
                  f"wrong {row['wrong']:>2}  precision {row['precision_pct']}")
        print("")
        print(f"VERDICT: {out['verdict'].upper()} -- {out['verdict_reason']}")
    else:
        print(f"{out['verdict'].upper()}: {out['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
