# CUI // SP-CTI
"""Is intervention actually falling? The AUTONOMY card, held to its own standard (autonomy-lrn-02).

A self-improving system that cannot show its improvement is a claim. The AUTONOMY
card shipped an identity record (id-01), a staleness detector (id-02), a
supervision model (id-03), an admission gate (adm-01/02) and a claim registry
(rem-hyg-17, lrn-01) — every one of them a surface that asserts something. This
module measures, over a window and with an honest denominator, whether the loop
they form is doing what the card says it does.

FIVE MEASURES, AND WHAT EACH ONE'S DENOMINATOR IS:

    claims              registered claims; how many name a REAL incident. The
                        registry SAYS every claim came from a defect — that is
                        verified against the board, not trusted.
    live_catches        disagreements the verifier found BEFORE a human did.
    duplicate_dispatch  branches that drew more than one PR, over branches.
                        Baseline 11.6% (27 of 232), recorded 2026-08-20.
    admission           dispatches the gate refuses, split by whether the
                        refusal was RIGHT — replayed through the gate's OWN
                        `classify`, never a second copy of the rule.
    stale_daemons       live processes running superseded code, and for how
                        long.

EVERY RATE IS None, NEVER 0.0, WHEN NOTHING WAS MEASURED. A deployment with no
operating history reports UNMEASURABLE, and a fresh worktree does not manufacture
a perfect score: `perfect_score_census` (rem-hyg-13) drained all twelve sites of
exactly that defect and args/perfect_score_gate.yaml is ratcheted to 0, so a
`pct if total else 100.0` here would breach a gate this platform just closed.
:func:`_rate` is the ONE place a percentage is computed, and an empty
denominator returns None from it.

THREE ABSENCES MEASURED ON THE LIVE BOARD 2026-08-21, each reported by name
rather than as a zero, because each sends a reader to a different fix:

  * the admission gate in `report` mode LOGS its verdict and persists nothing —
    so `recorded_refusals` counts ENFORCED refusals only, and what the gate
    WOULD have said is recovered by replaying history through `classify`;
  * nothing persists a claim-verifier run — it runs when a human types the
    command (autonomy-act-01 schedules it) — so a disagreement cannot be DATED,
    and "caught before a human" cannot be decided without a date. The current
    verdicts are carried as `snapshot_now`, labelled as a snapshot;
  * `agent_sessions` on the live database lacked the code-identity columns
    (migration 20260821024132 unapplied), so every live process read "no
    recorded code version" and the fleet's stale count was UNKNOWN, not 0.

A TREND NEEDS TWO POINTS. The baselines are carried AS RECORDED on the card,
dated, and never recomputed here; the current value is re-derived by this tool
with its own definition stated beside it. A delta is None whenever either side
is unmeasurable, and the headline cannot be `falling` while any metric it rests
on is unmeasured — the holes are listed next to it.

REPORT ONLY, no --gate. This measures the BOARD and the FLEET, not a diff, so
failing a commit on it would refuse work the committer did not cause
(kpr-fix-03). Exit 2 only when no report could be produced at all.

Usage:
    python -m tools.awareness.autonomy_loop                 # human report, 7-day window
    python -m tools.awareness.autonomy_loop --json
    python -m tools.awareness.autonomy_loop --window-days 30
    python -m tools.awareness.autonomy_loop --no-live-verify  # skip running the claim verifier
    python -m tools.awareness.autonomy_loop --no-forge        # no `gh` call: PR-based measures read unmeasurable
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 — git only, fixed argv, shell=False
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

MEASURED = "measured"
UNMEASURABLE = "unmeasurable"

DEFAULT_WINDOW_DAYS = 7
GIT_TIMEOUT_SECONDS = 20

#: Baselines AS RECORDED on the AUTONOMY card and in autonomy-adm-01's survey.
#: Dated. Never recomputed here — a baseline that moves with the measurement
#: cannot show a trend.
BASELINES: Dict[str, Dict[str, Any]] = {
    "duplicate_dispatch": {
        "measured_on": "2026-08-20",
        "branches": 232, "with_multiple_prs": 27, "rate_pct": 11.6,
        "definition": "kanban task branches that drew more than one PR, over 523 PRs",
        "source": "AUTONOMY card / autonomy-adm-01",
    },
    "admission": {
        "measured_on": "2026-08-21",
        "dispatches": 6528, "fires": 195, "right": 172, "wrong": 23,
        "fire_rate_pct": 2.99, "wrong_of_fires_pct": 11.8,
        "definition": "every recorded scheduler dispatch replayed through classify()",
        "source": "python -m tools.kanban.dispatch_admission --survey",
    },
}

#: The shape of a kanban card id: `<prefix>-<epic>-<NN>` (rem-hyg-09, cch-obs-03,
#: autonomy-lrn-02). A claim tag matching it is a DECLARED incident reference;
#: whether the card exists is checked against the board.
_CARD_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+-\d{2,}$")

#: The migration that adds the identity columns autonomy-id-01 records into.
#: Named so an unmeasurable fleet can say WHICH fix it needs.
_IDENTITY_MIGRATION = "20260821024132_agent_sessions_code_identity"
_IDENTITY_COLUMNS = ("module", "code_version")


# ────────────────────────────────────────────────────────────────────────────
# The one place a percentage is computed
# ────────────────────────────────────────────────────────────────────────────
def _rate(numerator: Optional[int], denominator: Optional[int],
          digits: int = 2) -> Optional[float]:
    """A rate is None — never 0.0, never 100.0 — when the denominator is empty.

    "Nothing measured" and "measured zero" justify opposite decisions, and a
    perfect score over an empty denominator closes the question a missing
    number would have opened (rem-hyg-13).
    """
    if not denominator or numerator is None:
        return None
    return round(numerator / denominator * 100, digits)


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ────────────────────────────────────────────────────────────────────────────
# 1. Claims — registered, and seeded from a REAL incident
# ────────────────────────────────────────────────────────────────────────────
def incident_refs(claim: Any) -> List[str]:
    """Card ids a claim DECLARES as its origin.

    An explicit ``incident`` attribute (autonomy-lrn-01's field, when a claim
    carries one) is read first; failing that, any tag shaped like a card id.
    The registry's docstring asserting "every claim was a real defect" is not
    a reference — a claim must NAME its incident to count.
    """
    refs: List[str] = []
    explicit = getattr(claim, "incident", None)
    if explicit:
        refs.append(str(explicit).strip())
    for tag in getattr(claim, "tags", None) or []:
        text = str(tag).strip()
        if _CARD_ID.match(text) and text not in refs:
            refs.append(text)
    return refs


def measure_claims(registry: Sequence[Any],
                   board_ids: Optional[Iterable[str]]) -> Dict[str, Any]:
    """How many claims exist, and how many are anchored to a card that EXISTS.

    *board_ids* is None when the board could not be read — then
    ``incident_verified_on_board`` is None, never 0, while the DECLARED count
    (a fact about the registry alone) is still reported.
    """
    registered = len(registry)
    declared = [c for c in registry if incident_refs(c)]
    unreferenced = [getattr(c, "claim_id", "?") for c in registry
                    if not incident_refs(c)]
    if board_ids is None:
        verified: Optional[int] = None
    else:
        known = set(board_ids)
        verified = sum(1 for c in declared
                       if any(r in known for r in incident_refs(c)))
    by_tier: Dict[str, int] = {}
    for c in registry:
        tier = str(getattr(c, "tier", "report"))
        by_tier[tier] = by_tier.get(tier, 0) + 1
    out: Dict[str, Any] = {
        "state": MEASURED if registered else UNMEASURABLE,
        "registered": registered,
        "incident_declared": len(declared),
        "incident_verified_on_board": verified,
        "incident_share_pct": _rate(len(declared), registered, 1),
        "verified_share_pct": _rate(verified, registered, 1),
        "unreferenced_claims": unreferenced,
        "by_tier": by_tier,
    }
    if not registered:
        out["reason"] = "no claims registered — nothing to measure"
    elif board_ids is None:
        out["reason"] = "board unreadable — declared references could not be verified"
    return out


def _board_ids(conn, refs: Sequence[str]) -> Optional[List[str]]:
    """Which of *refs* exist on the board. None when the board cannot answer."""
    if not refs:
        return []
    try:
        placeholders = ", ".join(["%s"] * len(refs))
        rows = conn.execute(
            f"SELECT id FROM kanban_tasks WHERE id IN ({placeholders})",  # nosec B608
            tuple(refs),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    return [str(dict(r).get("id")) for r in rows or []]


# ────────────────────────────────────────────────────────────────────────────
# 2. Live catches — a disagreement found before a human filed it
# ────────────────────────────────────────────────────────────────────────────
def measure_live_catches(reflex_row: Optional[Dict[str, Any]],
                         snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Disagreements caught LIVE. UNMEASURABLE until a verifier run is persisted.

    "Before a human" is a statement about ORDER, and order needs two dates: when
    the verifier first said `disagrees`, and when a human filed the card. The
    verifier runs only on demand and persists nothing, so the first date does
    not exist. The reflex autonomy-act-01 registers is surfaced when present —
    proof that it RUNS on its own — but a run count is not a disagreement
    history, and the count stays None until one exists.

    *snapshot* is the verifier's CURRENT verdicts, carried under a name that
    says so. A snapshot of 0 disagreements today is not 0 caught live.
    """
    out: Dict[str, Any] = {
        "state": UNMEASURABLE,
        "caught_live": None,
        "reason": ("nothing persists a verifier run: claim_verifier runs only when "
                   "invoked by hand (autonomy-act-01 schedules it), so a disagreement "
                   "cannot be dated and 'before a human did' cannot be decided"),
        "verifier_reflex": None,
        "snapshot_now": None,
    }
    if reflex_row:
        out["verifier_reflex"] = {
            "reflex_name": reflex_row.get("reflex_name"),
            "total_runs": reflex_row.get("total_runs"),
            "last_run_at": reflex_row.get("last_run_at"),
        }
    if snapshot is not None:
        counts = dict(snapshot.get("counts") or {})
        out["snapshot_now"] = {
            "agrees": counts.get("agrees"),
            "disagrees": counts.get("disagrees"),
            "unmeasurable": counts.get("unmeasurable"),
            "disagreeing": [r.get("claim_id") for r in snapshot.get("results") or []
                            if r.get("verdict") == "disagrees"],
            "note": "current verdicts, not a history — 0 here is not 0 caught live",
        }
    return out


def _verifier_reflex_row(conn) -> Optional[Dict[str, Any]]:
    try:
        rows = conn.execute(
            "SELECT reflex_name, total_runs, last_run_at FROM genesis_reflex_state "
            "WHERE reflex_name LIKE %s OR reflex_name LIKE %s",
            ("%claim%", "%verif%"),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    return dict(rows[0]) if rows else None


def _verifier_snapshot() -> Optional[Dict[str, Any]]:
    try:
        from tools.awareness.claim_verifier import verify_all
        from tools.awareness.claims import REGISTRY
        return verify_all(list(REGISTRY))
    except Exception:  # noqa: BLE001
        return None


# ────────────────────────────────────────────────────────────────────────────
# 3. Duplicate dispatch — a branch that drew more than one PR
# ────────────────────────────────────────────────────────────────────────────
def _first_created(prs: Sequence[Dict[str, Any]]) -> Optional[datetime]:
    dates = [d for d in (_parse_dt(p.get("createdAt")) for p in prs) if d]
    return min(dates) if dates else None


def duplicate_stats(branches: Iterable[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    """Per-branch duplication over a population of branches' PR lists."""
    total = prs = multi = two_merged = 0
    for branch_prs in branches:
        total += 1
        prs += len(branch_prs)
        if len(branch_prs) > 1:
            multi += 1
        if sum(1 for p in branch_prs if p.get("mergedAt")) > 1:
            two_merged += 1
    return {
        "branches": total, "prs": prs,
        "with_multiple_prs": multi, "with_two_merged": two_merged,
        "multiple_pr_rate_pct": _rate(multi, total, 1),
        "two_merged_rate_pct": _rate(two_merged, total, 1),
    }


def measure_duplicates(prs_by_branch: Optional[Dict[str, List[Dict[str, Any]]]],
                       window_start: Optional[datetime]) -> Dict[str, Any]:
    """Duplicate-dispatch rate, lifetime (within the PR sample) and in-window.

    The window population is branches whose FIRST PR opened inside the window.
    A branch that opened yesterday has had less time to draw a second PR than
    one that opened a month ago, so the windowed rate is a LOWER BOUND; that is
    stated on the result rather than left for a reader to work out.
    """
    if prs_by_branch is None:
        return {"state": UNMEASURABLE, "reason": "PR history unavailable (forge not consulted or unreachable)",
                "lifetime": None, "window": None}
    if not prs_by_branch:
        return {"state": UNMEASURABLE, "reason": "no kanban PRs in the forge's answer",
                "lifetime": None, "window": None}
    all_dates = [d for v in prs_by_branch.values() for d in (_first_created(v),) if d]
    lifetime = duplicate_stats(prs_by_branch.values())
    lifetime["definition"] = ("kanban/* head branches among the newest PRs the forge "
                              "returned; 'lifetime' is bounded by that sample")
    lifetime["sample_oldest_pr"] = min(all_dates).isoformat() if all_dates else None
    window: Optional[Dict[str, Any]] = None
    if window_start is not None:
        in_window = [v for v in prs_by_branch.values()
                     if (_first_created(v) or datetime.min.replace(tzinfo=timezone.utc))
                     >= window_start]
        window = duplicate_stats(in_window)
        window["window_start"] = window_start.isoformat()
        window["censoring"] = ("branches whose first PR opened inside the window have "
                               "had less time to draw a second; this rate is a lower bound")
    return {"state": MEASURED, "lifetime": lifetime, "window": window}


# ────────────────────────────────────────────────────────────────────────────
# 4. Admission — what the gate refuses, and whether it was right
# ────────────────────────────────────────────────────────────────────────────
def measure_admission(survey: Optional[Dict[str, Any]],
                      recorded_refusals: Optional[int],
                      mode: Optional[str]) -> Dict[str, Any]:
    """The gate's refusals, split RIGHT / WRONG, plus what it actually PERSISTED.

    *survey* is `dispatch_admission.survey`'s result — history replayed through
    the gate's own `classify`, so this can never describe a rule the gate does
    not have. *recorded_refusals* counts transition rows the gate wrote, which
    it does ONLY in `enforce` mode; in `report` mode it logs and persists
    nothing, and that is why the two numbers are kept side by side.
    """
    out: Dict[str, Any] = {
        "state": UNMEASURABLE,
        "mode": mode,
        "recorded_refusals": recorded_refusals,
        "recorded_refusals_note": (
            "the gate writes a transition row ONLY when it blocks (mode=enforce); "
            "in report mode its verdict is logged and never persisted"),
        "dispatches": None, "fires": None, "right": None, "wrong": None,
        "fire_rate_pct": None, "wrong_of_fires_pct": None,
        "wrong_of_dispatches_pct": None,
    }
    if survey is None:
        out["reason"] = "survey not run (forge not consulted)"
        return out
    if survey.get("state") != MEASURED:
        out["reason"] = survey.get("reason") or "survey unmeasurable"
        out["dispatches"] = survey.get("dispatches")
        return out
    out.update({
        "state": MEASURED,
        "dispatches": survey.get("dispatches"),
        "fires": survey.get("fires"),
        "right": survey.get("right"),
        "wrong": survey.get("wrong"),
        # Re-derived through _rate so an empty denominator is None here too,
        # whatever the survey chose to print.
        "fire_rate_pct": _rate(survey.get("fires"), survey.get("dispatches")),
        "wrong_of_fires_pct": _rate(survey.get("wrong"), survey.get("fires"), 1),
        "wrong_of_dispatches_pct": _rate(survey.get("wrong"), survey.get("dispatches")),
    })
    return out


def _recorded_refusals(conn, window_start: Optional[datetime]) -> Optional[int]:
    try:
        sql = ("SELECT COUNT(*) AS c FROM kanban_status_transitions "
               "WHERE actor = 'dispatch-admission'")
        params: List[Any] = []
        if window_start is not None:
            sql += " AND recorded_at >= %s"
            params.append(window_start.isoformat())
        row = conn.execute(sql, tuple(params)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return int(dict(row).get("c") or 0) if row else 0


# ────────────────────────────────────────────────────────────────────────────
# 5. Stale daemons — and for how long
# ────────────────────────────────────────────────────────────────────────────
def _run_git(args: List[str], root: Path):
    return subprocess.run(  # nosec B603 B607 — fixed argv, shell=False, git only
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GIT_TIMEOUT_SECONDS, check=False, shell=False,
    )


def stale_since(version: str, until: str, files: Sequence[str],
                root: Optional[Path] = None, runner=None) -> Optional[datetime]:
    """When did the FIRST change to something this process imports land?

    The earliest commit in ``version..until`` touching any of *files*. None
    when git cannot answer — which is UNMEASURED, never "just now".
    """
    if not version or not files:
        return None
    run = runner or _run_git
    try:
        result = run(["log", "--format=%cI", "--reverse", f"{version}..{until}",
                      "--", *files], root or _BASE)
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    for line in (getattr(result, "stdout", "") or "").splitlines():
        parsed = _parse_dt(line.strip())
        if parsed:
            return parsed
    return None


def _humanize(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    seconds = int(seconds)
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def measure_fleet(staleness: Dict[str, Any], *, identity_columns_present: Optional[bool],
                  now: Optional[datetime] = None, until: str = "origin/main",
                  root: Optional[Path] = None, runner=None) -> Dict[str, Any]:
    """Stale processes over ASSESSED processes, with how long each has been stale.

    *staleness* is `code_staleness.report()`'s result. A process that could not
    be assessed is not a current one: when NONE could be, the stale count is
    unknown and the section is UNMEASURABLE — and it says which of two fixes
    that needs, because the live board on 2026-08-21 needed the first one.
    """
    moment = now or _now()
    state = staleness.get("state")
    if state != MEASURED:
        return {"state": UNMEASURABLE, "reason": staleness.get("reason") or state,
                "live_processes": 0 if state == "no_live_processes" else None,
                "assessed": None, "stale": None, "current": None,
                "stale_rate_pct": None, "processes": []}

    processes = []
    for p in staleness.get("processes") or []:
        entry = {
            "session_id": p.get("session_id"), "module": p.get("module"),
            "pid": p.get("pid"), "verdict": p.get("verdict"),
            "code_version": p.get("code_version"), "dirty": p.get("dirty"),
            "reason": p.get("reason"), "changed_count": p.get("changed_count"),
            "stale_since": None, "stale_for_seconds": None, "stale_for": None,
            "stale_for_is_lower_bound": None,
        }
        if p.get("verdict") == "stale":
            listed = list(p.get("changed_in_closure") or [])
            since = stale_since(p.get("code_version"), until, listed, root, runner)
            if since is not None:
                secs = max(0.0, (moment - since).total_seconds())
                entry.update(stale_since=since.isoformat(), stale_for_seconds=int(secs),
                             stale_for=_humanize(secs))
            # The detector caps the file list it returns; the earliest commit
            # over a SUBSET is never earlier than over the whole, so a capped
            # list makes the duration a lower bound.
            entry["stale_for_is_lower_bound"] = (
                (p.get("changed_count") or 0) > len(listed))
        processes.append(entry)

    stale = sum(1 for p in processes if p["verdict"] == "stale")
    current = sum(1 for p in processes if p["verdict"] == "current")
    unmeasurable = sum(1 for p in processes if p["verdict"] == UNMEASURABLE)
    assessed = stale + current
    out: Dict[str, Any] = {
        "state": MEASURED if assessed else UNMEASURABLE,
        "live_processes": len(processes),
        "assessed": assessed, "stale": stale if assessed else None,
        "current": current if assessed else None,
        "unmeasurable": unmeasurable,
        "stale_rate_pct": _rate(stale, assessed, 1),
        "processes": processes,
    }
    if not assessed and processes:
        if identity_columns_present is False:
            out["reason"] = (f"{len(processes)} live process(es), none assessable: "
                             f"agent_sessions lacks the code-identity columns — "
                             f"migration {_IDENTITY_MIGRATION} has not been applied "
                             f"on this database")
        elif identity_columns_present is True:
            out["reason"] = (f"{len(processes)} live process(es), none assessable: the "
                             f"columns exist but no process has registered an identity "
                             f"— each booted before autonomy-id-01 and needs a restart")
        else:
            out["reason"] = (f"{len(processes)} live process(es), none assessable, and "
                             f"the catalogue could not be read to say why")
    elif not processes:
        out["reason"] = "no live processes"
    return out


def _identity_columns_present(conn) -> Optional[bool]:
    """Does `agent_sessions` carry the identity columns? Read from the catalogue.

    Reuses session_registry's own reader so this cannot disagree with the
    writer about what "present" means.
    """
    try:
        from tools.coordination.session_registry import _live_columns
        cols = _live_columns(conn)
    except Exception:  # noqa: BLE001
        return None
    if not cols:
        return None
    return set(_IDENTITY_COLUMNS).issubset(cols)


# ────────────────────────────────────────────────────────────────────────────
# 6. Trend — two points, or None
# ────────────────────────────────────────────────────────────────────────────
def trend(current: Optional[float], baseline: Optional[float],
          *, lower_is_better: bool = True) -> Dict[str, Any]:
    """Direction of travel. None in every field when either point is missing."""
    if current is None or baseline is None:
        return {"baseline_pct": baseline, "current_pct": current,
                "delta_pct_points": None, "direction": None, "improving": None}
    delta = round(current - baseline, 2)
    direction = "down" if delta < 0 else ("up" if delta > 0 else "flat")
    improving = (delta < 0) if lower_is_better else (delta > 0)
    if delta == 0:
        improving = False
    return {"baseline_pct": baseline, "current_pct": current,
            "delta_pct_points": delta, "direction": direction, "improving": improving}


def headline(trends: Dict[str, Dict[str, Any]],
             unmeasured_sections: Sequence[str]) -> Dict[str, Any]:
    """`falling` only when every MEASURED trend improves and nothing is hidden.

    The unmeasured sections are listed beside the verdict, and their presence
    forbids `falling`: a loop that cannot show three of its five measures has
    not shown that intervention is falling, whatever the other two say.
    """
    measured = {k: v for k, v in trends.items() if v.get("improving") is not None}
    if not measured:
        verdict = None
    elif any(not v["improving"] for v in measured.values()):
        verdict = "not_falling"
    elif unmeasured_sections:
        verdict = "falling_where_measured"
    else:
        verdict = "falling"
    return {
        "verdict": verdict,
        "measured_trends": sorted(measured),
        "unmeasured_trends": sorted(k for k in trends if k not in measured),
        "unmeasured_sections": list(unmeasured_sections),
    }


# ────────────────────────────────────────────────────────────────────────────
# Collecting it
# ────────────────────────────────────────────────────────────────────────────
def collect(window_days: Optional[int] = DEFAULT_WINDOW_DAYS, *, conn=None,
            prs_by_branch: Optional[Dict[str, List[Dict[str, Any]]]] = None,
            use_forge: bool = True, live_verify: bool = True,
            until: str = "origin/main", now: Optional[datetime] = None,
            registry: Optional[Sequence[Any]] = None,
            staleness: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Gather every section. Never raises; a section that cannot run is unmeasurable."""
    moment = now or _now()
    window_start = (moment - timedelta(days=window_days)) if window_days else None

    close = False
    if conn is None:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            close = True
        except Exception:  # noqa: BLE001
            conn = None

    try:
        # 1. claims
        if registry is None:
            try:
                from tools.awareness.claims import REGISTRY as registry  # type: ignore[no-redef]
            except Exception:  # noqa: BLE001
                registry = []
        refs = sorted({r for c in registry for r in incident_refs(c)})
        board = _board_ids(conn, refs) if conn is not None else None
        claims = measure_claims(list(registry), board)

        # 2. live catches
        reflex = _verifier_reflex_row(conn) if conn is not None else None
        snapshot = _verifier_snapshot() if live_verify else None
        live = measure_live_catches(reflex, snapshot)
        if not live_verify:
            live["snapshot_now"] = None

        # 3 + 4. forge-backed
        from tools.kanban import dispatch_admission as da
        if use_forge and prs_by_branch is None:
            try:
                prs_by_branch = da._all_kanban_prs()  # noqa: SLF001 — the gate's own reader, not a second copy
            except Exception:  # noqa: BLE001
                prs_by_branch = None
        dup = measure_duplicates(prs_by_branch if use_forge else None, window_start)

        # The baseline was a LIFETIME survey. A 7-day window is a different
        # population, so both are measured: lifetime is trended against the
        # baseline like for like, the window says what is happening now.
        def _survey(days):
            if not (use_forge and prs_by_branch is not None):
                return None
            try:
                return da.survey(window_days=days, conn=conn, prs_by_branch=prs_by_branch)
            except Exception as exc:  # noqa: BLE001
                return {"state": UNMEASURABLE, "reason": f"survey failed: {exc}"}

        recorded_all = _recorded_refusals(conn, None) if conn is not None else None
        recorded_win = _recorded_refusals(conn, window_start) if conn is not None else None
        adm_lifetime = measure_admission(_survey(None), recorded_all, da.mode())
        adm_window = measure_admission(_survey(window_days), recorded_win, da.mode())
        admission = {
            "state": MEASURED if MEASURED in (adm_lifetime["state"], adm_window["state"])
            else UNMEASURABLE,
            "mode": da.mode(),
            "recorded_refusals_note": adm_lifetime.pop("recorded_refusals_note"),
            # Measured 2026-08-21: lifetime fires read 190 against the baseline's
            # 195 with MORE dispatches, because the forge reader returns the
            # newest PRs only — a dispatch whose PRs have aged out of that sample
            # replays as `allow`. The lifetime figure therefore drifts DOWN as
            # the board ages, and a reader trending it must know that.
            "sample_note": ("history is replayed against the newest PRs the forge "
                            "returns; a dispatch whose PRs have aged out of that "
                            "sample replays as allow, so the lifetime fire count "
                            "can only drift down over time"),
            "lifetime": adm_lifetime,
            "window": adm_window,
        }
        adm_window.pop("recorded_refusals_note", None)
        if admission["state"] != MEASURED:
            admission["reason"] = adm_lifetime.get("reason") or adm_window.get("reason")

        # 5. fleet
        if staleness is None:
            try:
                from tools.awareness.code_staleness import report as staleness_report
                staleness = staleness_report(until=until)
            except Exception as exc:  # noqa: BLE001
                staleness = {"state": UNMEASURABLE, "reason": f"staleness detector failed: {exc}"}
        fleet = measure_fleet(
            staleness,
            identity_columns_present=_identity_columns_present(conn) if conn is not None else None,
            now=moment, until=until)
    finally:
        if close and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    trends = {
        "duplicate_dispatch_lifetime": trend(
            (dup.get("lifetime") or {}).get("multiple_pr_rate_pct"),
            BASELINES["duplicate_dispatch"]["rate_pct"]),
        "duplicate_dispatch_window": trend(
            (dup.get("window") or {}).get("multiple_pr_rate_pct"),
            BASELINES["duplicate_dispatch"]["rate_pct"]),
        "admission_fire_rate_lifetime": trend(
            admission["lifetime"].get("fire_rate_pct"),
            BASELINES["admission"]["fire_rate_pct"]),
        "admission_fire_rate_window": trend(
            admission["window"].get("fire_rate_pct"),
            BASELINES["admission"]["fire_rate_pct"]),
        "admission_wrong_of_fires_lifetime": trend(
            admission["lifetime"].get("wrong_of_fires_pct"),
            BASELINES["admission"]["wrong_of_fires_pct"]),
    }
    sections = {"claims": claims, "live_catches": live, "duplicate_dispatch": dup,
                "admission": admission, "stale_daemons": fleet}
    unmeasured = [k for k, v in sections.items() if v.get("state") != MEASURED]
    return {
        "generated_at": moment.isoformat(),
        "window_days": window_days,
        "window_start": window_start.isoformat() if window_start else None,
        "baselines": BASELINES,
        **sections,
        "trend": trends,
        "headline": headline(trends, unmeasured),
    }


# ────────────────────────────────────────────────────────────────────────────
# Rendering
# ────────────────────────────────────────────────────────────────────────────
def _pct(value: Optional[float]) -> str:
    return "?" if value is None else f"{value}%"


def render(rep: Dict[str, Any]) -> str:
    out = [f"AUTONOMY loop — is intervention falling?  window {rep['window_days']}d  "
           f"(generated {rep['generated_at'][:19]}Z)"]
    head = rep["headline"]
    out.append(f"  headline: {head['verdict'] or 'UNMEASURABLE'}")
    if head["unmeasured_sections"]:
        out.append(f"  unmeasured: {', '.join(head['unmeasured_sections'])} "
                   f"— not clean, not counted")
    out.append("")

    c = rep["claims"]
    out.append(f"claims [{c['state']}]  registered {c['registered']} · "
               f"incident-declared {c['incident_declared']} · "
               f"verified on board {'?' if c['incident_verified_on_board'] is None else c['incident_verified_on_board']}"
               f" ({_pct(c['verified_share_pct'])})")
    if c.get("unreferenced_claims"):
        out.append(f"        no incident named: {', '.join(c['unreferenced_claims'])}")
    if c.get("reason"):
        out.append(f"        {c['reason']}")

    lv = rep["live_catches"]
    out.append(f"live catches [{lv['state']}]  caught live: "
               f"{'?' if lv['caught_live'] is None else lv['caught_live']}")
    out.append(f"        {lv['reason']}")
    if lv.get("verifier_reflex"):
        r = lv["verifier_reflex"]
        out.append(f"        reflex {r['reflex_name']}: {r['total_runs']} run(s), last {r['last_run_at']}")
    if lv.get("snapshot_now"):
        s = lv["snapshot_now"]
        out.append(f"        snapshot now: agrees {s['agrees']} · disagrees {s['disagrees']} · "
                   f"unmeasurable {s['unmeasurable']}  ({s['note']})")

    d = rep["duplicate_dispatch"]
    out.append(f"duplicate dispatch [{d['state']}]")
    if d.get("reason"):
        out.append(f"        {d['reason']}")
    for label in ("lifetime", "window"):
        blk = d.get(label)
        if blk:
            out.append(f"        {label:8} {blk['with_multiple_prs']}/{blk['branches']} branches "
                       f"drew >1 PR ({_pct(blk['multiple_pr_rate_pct'])}) · "
                       f"{blk['with_two_merged']} landed two merged ({_pct(blk['two_merged_rate_pct'])})")
    b = rep["baselines"]["duplicate_dispatch"]
    out.append(f"        baseline {b['with_multiple_prs']}/{b['branches']} ({b['rate_pct']}%) on {b['measured_on']}")

    a = rep["admission"]
    out.append(f"admission [{a['state']}]  mode={a['mode']}")
    for label in ("lifetime", "window"):
        blk = a.get(label) or {}
        rec = '?' if blk.get('recorded_refusals') is None else blk['recorded_refusals']
        if blk.get("state") == MEASURED:
            out.append(f"        {label:8} would refuse {blk['fires']}/{blk['dispatches']} "
                       f"({_pct(blk['fire_rate_pct'])}) · right {blk['right']} · wrong {blk['wrong']} "
                       f"({_pct(blk['wrong_of_fires_pct'])} of fires, "
                       f"{_pct(blk['wrong_of_dispatches_pct'])} of dispatches) · recorded {rec}")
        else:
            out.append(f"        {label:8} unmeasurable: {blk.get('reason', '?')} · recorded {rec}")
    b = rep["baselines"]["admission"]
    out.append(f"        baseline {b['fires']}/{b['dispatches']} ({b['fire_rate_pct']}%), "
               f"{b['wrong_of_fires_pct']}% wrong, on {b['measured_on']}")
    out.append(f"        {a['recorded_refusals_note']}")

    f = rep["stale_daemons"]
    out.append(f"stale daemons [{f['state']}]  live {f.get('live_processes') if f.get('live_processes') is not None else '?'} · "
               f"assessed {'?' if f.get('assessed') is None else f['assessed']} · "
               f"stale {'?' if f.get('stale') is None else f['stale']} ({_pct(f.get('stale_rate_pct'))})")
    if f.get("reason"):
        out.append(f"        {f['reason']}")
    for p in f.get("processes") or []:
        if p["verdict"] == "stale":
            bound = " (lower bound)" if p.get("stale_for_is_lower_bound") else ""
            out.append(f"        STALE {str(p['module'] or p['session_id'])[:40]:40} "
                       f"{p['changed_count']} file(s) · stale for {p['stale_for'] or '?'}{bound}")

    out.append("")
    out.append("trend (lower is better; None = one side unmeasured)")
    for name, t in rep["trend"].items():
        arrow = {"down": "v", "up": "^", "flat": "="}.get(t["direction"] or "", "?")
        out.append(f"  {arrow} {name:30} baseline {_pct(t['baseline_pct']):>7}  now {_pct(t['current_pct']):>7}"
                   f"  delta {'?' if t['delta_pct_points'] is None else t['delta_pct_points']}")
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--no-live-verify", action="store_true",
                        help="do not run the claim verifier for a current snapshot")
    parser.add_argument("--no-forge", action="store_true",
                        help="make no gh call; PR-based measures report unmeasurable")
    parser.add_argument("--until", default="origin/main",
                        help="ref the fleet's code is compared against")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        rep = collect(window_days=args.window_days, use_forge=not args.no_forge,
                      live_verify=not args.no_live_verify, until=args.until)
    except Exception as exc:  # noqa: BLE001
        print(f"autonomy_loop: no report could be produced: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=2, default=str) if args.json else render(rep))
    # Report only — no --gate (kpr-fix-03). It measures the board and the
    # fleet, not a diff.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
