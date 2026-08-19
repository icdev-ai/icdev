# CUI // SP-CTI
"""kpr-watch-02: alarm on eligible-but-unmerged — the merger has stalled.

THE STATE WORTH PAGING ON. ``tools/ci/merge_readiness.py`` answers "why is this PR
not merging" for every rung the ladder refuses on. It cannot answer the one case
where the ladder refuses NOTHING: a PR classified ``ready`` — green, MERGEABLE, not
a draft, no hold label, correct base, not behind — that is STILL open on the next
poll. Nothing is wrong with that PR. The actor should have merged it and did not,
and that is an automation-liveness problem with a completely different repair.

Every gate in this pipeline asks "is this PR finished?". None of them asked "is the
thing that merges finished PRs still working?", and until this module nothing
reported the difference.

WHAT THIS IS NOT. It never merges, pushes, un-drafts, rebases or closes; the only
forge commands it runs are ``gh pr list`` and ``gh auth status``, both read-only,
and ``tests/test_merge_stall.py`` proves that by AST. The one thing it WRITES is its
own append-only observation table — see RECORDING below.

────────────────────────────────────────────────────────────────────────────────
DISTINGUISHING THE CAUSES IS THE WHOLE DESIGN, AND IT IS MEASURED
────────────────────────────────────────────────────────────────────────────────

The card names four previously-observed causes, and they do not deserve the same
response: the pr_watcher daemon down or serving stale code; a stale ``gh`` token
returning 401 (an auth failure looks exactly like "nothing to merge"); the
sibling-conflict hold serialising a merge; the enforced done-gate holding on a
stale verification. A held sibling is WORKING AS DESIGNED. A 401 is an OUTAGE.
Reporting both as "stuck" teaches people to ignore the report.

That is not a presentation preference. Surveyed over the last 150 merged PRs
(args/merge_stall.yaml carries the full table), attributing each PR's post-green
wait against the 42,742 ``pr_watcher.wait`` rows in ``audit_trail``:

    THE ENTIRE TAIL BELONGS TO HOLDS WORKING AS DESIGNED. Attributed (n=30):
    p95 68.72, max 116.37 min — 17 done-gate, 12 sibling hold, 1 forge outage.
    Unattributed (n=120): p95 9.97, max 13.98 min, and NOTHING above it.

So at the 20-minute threshold this ships with, an alarm that IGNORES cause fires on
4.67% of routine merges and one that ATTRIBUTES cause first fires on 0.00%. At 15
minutes the same comparison is 6.67% against 0.00%. CLAUDE.md already calls 1.63%
grounds for standing a check down, so attribution is the difference between an
alarm and a ``|| true``. Re-run it with ``--survey``; the table lives in
args/merge_stall.yaml, including why the threshold is 20 and not 15.

────────────────────────────────────────────────────────────────────────────────
AGE: TWO SOURCES, NEVER MERGED
────────────────────────────────────────────────────────────────────────────────

An alarm needs an age, and nothing was keeping the fact it needs — WHEN a PR became
eligible. The forge does not record it (``updatedAt`` moves on a comment or a
label) and the watcher's audit rows record ACTIONS, not the moment a refusal
stopped applying.

  * ``recorded``     — from ``pr_merge_eligibility_events``, this module's own
                       append-only observation table. Exact. Preferred.
  * ``ci_estimate``  — ``max(statusCheckRollup[].completedAt)``, i.e. when the last
                       check went green. A PROXY, and it is the one the arming
                       survey was measured with. It is NOT the same fact:
                       eligibility can arrive LATER than green (a hold label
                       removed, a changes-requested review dismissed, a rebase that
                       cleared ``behind_main``), so the proxy reads such a PR as
                       instantly hours old and would alarm on first sight.
  * ``unmeasured``   — neither is available. Reported as its own severity. It is
                       never a reassuring zero and it can never raise an alarm.

BOTH are reported on every row, always — ``age_minutes`` from the chosen source and
``ci_green_age_minutes`` from the estimate — so "recorded 0 min but green 3 hours
ago" is legible instead of being silently collapsed into one number. The alarm
reads the chosen source, which on a first-ever run is ``recorded`` at age 0: a
clock that starts when observation starts cannot manufacture a backlog of alarms
out of history it was not present for.

RECORDING is per TRANSITION, never per poll: a row is written only when a PR's
``(state, head_sha)`` differs from its newest row. A PR sitting ``ready`` for an
hour therefore has exactly ONE row whose ``observed_at`` IS its first-seen-ready,
so the read is a single indexed lookup and a 30s poll costs a handful of rows a day
rather than ~29,000. The head sha is part of the key because a force-push to a
``ready`` PR is a NEW merge opportunity whose clock must restart.

────────────────────────────────────────────────────────────────────────────────
ELIGIBILITY IS ASKED WITH THE ``linked`` RUNG SKIPPED
────────────────────────────────────────────────────────────────────────────────

``classify_merge_readiness`` short-circuits to ``linked`` for any PR a kanban task
points at, because the task path owns it. That is a statement about WHICH ACTOR
merges, not about whether the PR is finished — and the task path is where three of
the four named causes live. So this module calls the SAME pure function with
``linked_urls=()`` and carries ownership separately as ``door``. There is no second
copy of the ladder here and there must never be one; the door only changes who is
asked to fix it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Tuple

from tools.ci.merge_readiness import (  # the ONE ladder — never re-transcribed
    _GH_FIELDS,
    DEFAULT_MAX_BEHIND_COMMITS,
    READY,
    classify_merge_readiness,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "args" / "merge_stall.yaml"
WATCHER_CONFIG_PATH = REPO_ROOT / "args" / "pr_watcher_config.yaml"

#: The append-only observation table (migration 20260819011454).
EVENTS_TABLE = "pr_merge_eligibility_events"


# ────────────────────────────────────────────────────────────────────────────
# Vocabulary
# ────────────────────────────────────────────────────────────────────────────

# ── severities ──
SEV_OK = "ok"                    # not eligible, or eligible and young
SEV_BY_DESIGN = "by_design"      # eligible, held on purpose, and the hold is named
SEV_UNMEASURED = "unmeasured"    # eligible, but nothing knows for how long
SEV_OUTAGE = "outage"            # the merger cannot act: it is down or blind
SEV_ALARM = "alarm"              # eligible, aged out, and NOBODY CAN SAY WHY

#: Ordered worst-first, which is also the order the table sorts in.
SEVERITIES: Tuple[str, ...] = (
    SEV_ALARM, SEV_OUTAGE, SEV_UNMEASURED, SEV_BY_DESIGN, SEV_OK)

# ── causes ──
CAUSE_NOT_ELIGIBLE = "not_eligible"        # the ladder refuses; merge_readiness says why
CAUSE_WITHIN_THRESHOLD = "within_threshold"
CAUSE_DAEMON_DOWN = "daemon_down"
CAUSE_FORGE_UNREACHABLE = "forge_unreachable"
CAUSE_FORGE_AUTH = "forge_auth"
CAUSE_SIBLING_HOLD = "sibling_hold"
CAUSE_DONE_GATE = "done_gate"
CAUSE_LANDED_HOLD = "landed_hold"
CAUSE_PROTECTED_PATH = "protected_path"
CAUSE_MERGER_DISABLED = "merger_disabled"
CAUSE_APPROVAL_REQUIRED = "approval_required"
CAUSE_CI_RUNNING = "ci_running"
CAUSE_UNMEASURED = "unmeasured"
CAUSE_UNATTRIBUTED = "unattributed"        # THE ALARM

#: Causes that mean the pipeline is doing what it was told to do. They are still
#: REPORTED — the card names "the sibling-conflict hold serialising a merge
#: indefinitely" as a real stall — but they escalate on their own, much longer
#: threshold rather than the unattributed one.
#: ``ci_running`` is here for a case that looks like a contradiction and is not:
#: the rollup this report reads says every check is green, while the watcher's own
#: newest observation of the same PR says CI was still running. The two are minutes
#: apart and a `needs:`-gated job that had not yet been created when the watcher
#: looked is enough to produce it. Believing the report over the watcher there would
#: alarm on a PR the merger correctly declined to merge, so the watcher's word wins
#: and the PR is held — for `by_design_stall_after_minutes`, after which the
#: disagreement has lasted too long to be a race and IS reported.
BY_DESIGN_CAUSES: FrozenSet[str] = frozenset({
    CAUSE_SIBLING_HOLD, CAUSE_DONE_GATE, CAUSE_LANDED_HOLD,
    CAUSE_PROTECTED_PATH, CAUSE_MERGER_DISABLED, CAUSE_APPROVAL_REQUIRED,
    CAUSE_CI_RUNNING,
})

#: Causes that mean the merger CANNOT act, whatever the PRs look like. An outage
#: is attributed ONCE to the fleet rather than N times to N innocent PRs — a dead
#: daemon that raises eight identical alarms is how an alarm gets muted.
OUTAGE_CAUSES: FrozenSet[str] = frozenset({
    CAUSE_DAEMON_DOWN, CAUSE_FORGE_UNREACHABLE, CAUSE_FORGE_AUTH,
})

DEFAULT_STALL_AFTER_MINUTES = 15.0
DEFAULT_BY_DESIGN_STALL_AFTER_MINUTES = 180.0
DEFAULT_WATCHER_STALE_AFTER_MINUTES = 15.0
DEFAULT_ATTRIBUTION_LOOKBACK_HOURS = 24

#: Fallback attribution table, used only when args/merge_stall.yaml is unreadable.
#: Every entry was taken from live ``audit_trail`` rows, never invented — a pattern
#: that matches nothing is indistinguishable from a cause that never happens.
DEFAULT_HOLD_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("sibling file conflict", CAUSE_SIBLING_HOLD),
    ("shares source file(s) with open PR", CAUSE_SIBLING_HOLD),
    ("enforced gate:", CAUSE_DONE_GATE),
    ("already on main", CAUSE_LANDED_HOLD),
    ("already landed", CAUSE_LANDED_HOLD),
    ("protected path", CAUSE_PROTECTED_PATH),
    ("approval required", CAUSE_APPROVAL_REQUIRED),
    ("ci still running", CAUSE_CI_RUNNING),
    ("awaiting ci results", CAUSE_CI_RUNNING),
    ("fetch failed", CAUSE_FORGE_UNREACHABLE),
    ("error connecting to api.github.com", CAUSE_FORGE_UNREACHABLE),
)

DOOR_LINKED = "linked"
DOOR_UNLINKED = "unlinked"


class StallVerdict(NamedTuple):
    """The verdict for one PR. Unpacks as ``(severity, cause, detail)``."""

    severity: str
    cause: str
    detail: str

    @property
    def alarming(self) -> bool:
        """True when a human should be woken. An outage counts; a hold does not."""
        return self.severity in (SEV_ALARM, SEV_OUTAGE)


# ────────────────────────────────────────────────────────────────────────────
# The decision table — pure. No I/O, no clock of its own, no LLM.
# ────────────────────────────────────────────────────────────────────────────


def classify_stall(
    *,
    eligible: bool,
    age_minutes: Optional[float],
    stall_after_minutes: float = DEFAULT_STALL_AFTER_MINUTES,
    by_design_after_minutes: float = DEFAULT_BY_DESIGN_STALL_AFTER_MINUTES,
    watcher_alive: Optional[bool] = None,
    forge_ok: Optional[bool] = None,
    hold_cause: Optional[str] = None,
    merger_enabled: bool = True,
    ineligible_reason: str = "",
) -> StallVerdict:
    """Is this eligible-but-open PR a stall, and whose problem is it?

    Pure: every fact it needs is an argument, so the same inputs always give the
    same verdict and the whole table is testable without a forge or a database.
    The impure collectors that produce those facts live below and are named for
    what they measure.

    ``watcher_alive`` / ``forge_ok`` are TRISTATE on purpose. ``None`` means the
    signal could not be measured, and an unmeasured signal is never read as
    healthy — but it is also never read as broken, because a probe that cannot
    run must not manufacture an outage. It simply does not attribute.

    ``age_minutes`` of ``None`` is UNMEASURED and can never raise an alarm. That
    is the same posture ``merge_readiness`` takes for ``behind_by``: an unmeasured
    branch and a branch measured level with main are different facts and only one
    of them is evidence.
    """
    if not eligible:
        return StallVerdict(
            SEV_OK, CAUSE_NOT_ELIGIBLE,
            ineligible_reason or "the merge-eligibility ladder refuses this PR")

    # ── OUTAGE FIRST, and attributed to the FLEET, not to the PR ─────────────
    # A dead daemon or a blind one explains every eligible PR at once. Ranking it
    # ahead of the per-PR rungs is what stops one outage printing N identical
    # alarms — the failure mode that gets an alarm muted. Note the asymmetry with
    # the age check below: an outage is reported the moment it is observed, with
    # no threshold, because "the merger is down" does not become truer with time
    # and waiting 15 minutes to say so wastes 15 minutes.
    if watcher_alive is False:
        return StallVerdict(
            SEV_OUTAGE, CAUSE_DAEMON_DOWN,
            "the pr_watcher daemon has not completed a poll recently -- nothing "
            "is merging anything; restart it before reading these PRs as stuck")
    if forge_ok is False:
        return StallVerdict(
            SEV_OUTAGE, CAUSE_FORGE_AUTH,
            "the forge refused this host's credentials -- an auth failure looks "
            "exactly like 'nothing to merge', so it is reported as an outage")

    if not merger_enabled:
        return StallVerdict(
            SEV_BY_DESIGN, CAUSE_MERGER_DISABLED,
            "auto-merge is switched off for this door in args/pr_watcher_config.yaml")

    if age_minutes is None:
        # Eligible, and nothing knows for how long. Its own severity: counting it
        # healthy would hide a stall, and counting it an alarm would invent one.
        return StallVerdict(
            SEV_UNMEASURED, CAUSE_UNMEASURED,
            "eligible, but no recorded first-seen-ready and no CI completion to "
            "estimate from -- age UNMEASURED, so no alarm can be raised")

    if hold_cause in OUTAGE_CAUSES:
        # A per-PR forge failure the watcher itself recorded. Not the fleet-wide
        # probe above, so it is attributed here rather than jumping the queue.
        return StallVerdict(
            SEV_OUTAGE, hold_cause,
            "the watcher could not reach the forge for this PR (%.1f min eligible)"
            % age_minutes)

    if hold_cause in BY_DESIGN_CAUSES:
        if age_minutes > by_design_after_minutes:
            # Serialising is correct. Serialising with no end is a stall wearing a
            # design's clothes, which is exactly the case the card names.
            return StallVerdict(
                SEV_ALARM, hold_cause,
                "held by %s for %.1f min, past the %.0f min ceiling -- the hold is "
                "correct but it is not ending; a human should break the queue"
                % (hold_cause, age_minutes, by_design_after_minutes))
        return StallVerdict(
            SEV_BY_DESIGN, hold_cause,
            "held by %s for %.1f min -- working as designed"
            % (hold_cause, age_minutes))

    if age_minutes > stall_after_minutes:
        return StallVerdict(
            SEV_ALARM, CAUSE_UNATTRIBUTED,
            "eligible for %.1f min (threshold %.0f) and NOTHING explains it -- the "
            "merger should have merged this and did not"
            % (age_minutes, stall_after_minutes))

    return StallVerdict(
        SEV_OK, CAUSE_WITHIN_THRESHOLD,
        "eligible for %.1f min -- within the %.0f min threshold"
        % (age_minutes, stall_after_minutes))


def attribute_reason(
    reason: str, patterns: Iterable[Tuple[str, str]] = DEFAULT_HOLD_PATTERNS
) -> Optional[str]:
    """Map one watcher refusal reason to a cause. ``None`` when nothing matches.

    First match wins, in the order the patterns are declared, so a refusal that
    merely mentions an HTTP error still classifies on its own terms first.
    ``None`` is the honest answer and it is the one that becomes the alarm — do
    not add a catch-all pattern here to make the unattributed bucket look empty.
    """
    low = (reason or "").lower()
    if not low:
        return None
    for needle, cause in patterns:
        if needle.lower() in low:
            return cause
    return None


# ────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────


def load_config(path: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """args/merge_stall.yaml, or documented defaults when it cannot be read.

    Never raises and never falls back to "no threshold": an unreadable config
    must degrade to the surveyed defaults, not to an alarm that cannot fire.
    """
    try:
        import yaml  # noqa: PLC0415 — optional at import time

        raw = yaml.safe_load(
            (path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    patterns: List[Tuple[str, str]] = []
    for entry in raw.get("hold_patterns") or []:
        if isinstance(entry, dict) and entry.get("pattern") and entry.get("cause"):
            patterns.append((str(entry["pattern"]), str(entry["cause"])))
    return {
        "stall_after_minutes": float(
            raw.get("stall_after_minutes", DEFAULT_STALL_AFTER_MINUTES)),
        "by_design_stall_after_minutes": float(
            raw.get("by_design_stall_after_minutes",
                    DEFAULT_BY_DESIGN_STALL_AFTER_MINUTES)),
        "watcher_stale_after_minutes": float(
            raw.get("watcher_stale_after_minutes",
                    DEFAULT_WATCHER_STALE_AFTER_MINUTES)),
        "record_observations": bool(raw.get("record_observations", True)),
        "record_from_pr_watcher": bool(raw.get("record_from_pr_watcher", True)),
        "attribution_lookback_hours": int(
            raw.get("attribution_lookback_hours",
                    DEFAULT_ATTRIBUTION_LOOKBACK_HOURS)),
        "hold_patterns": tuple(patterns) or DEFAULT_HOLD_PATTERNS,
    }


def watcher_config(path: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """The MERGER's own config, read from the merger's own file.

    Whether auto-merge is on, and which paths a human must merge by hand, are the
    merger's facts. Reading them from anywhere else would let this report describe
    a policy the merger does not have — the drift ``merge_readiness`` exists to stop.
    """
    try:
        import yaml  # noqa: PLC0415

        raw = yaml.safe_load(
            (path or WATCHER_CONFIG_PATH).read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ────────────────────────────────────────────────────────────────────────────
# Impure collectors — each named for the ONE thing it measures
# ────────────────────────────────────────────────────────────────────────────


def parse_ts(raw: Any) -> Optional[datetime]:
    """ISO-8601 (or a live datetime) -> aware UTC datetime. ``None`` when absent.

    Rejects a year <= 2000: ``gh`` emits ``0001-01-01T00:00:00Z`` for a check run
    that exists but has not started, and treating that as a completion time would
    date every such PR to the first century.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt if dt.year > 2000 else None


def ci_green_at(pr: Dict[str, Any]) -> Optional[datetime]:
    """When the last check in the rollup reported. The ``ci_estimate`` age source.

    An ESTIMATE of when the PR became eligible, not a measurement of it — see the
    module docstring. Returns ``None`` for an empty rollup or one with no usable
    completion time, which is honest: such a PR has no estimate at all.
    """
    stamps = [parse_ts(c.get("completedAt"))
              for c in (pr.get("statusCheckRollup") or [])]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def probe_forge_auth(*, runner=None, gh_bin: str = "gh") -> Tuple[Optional[bool], str]:
    """Does this host's ``gh`` credential still work? ``(ok, detail)``.

    TRISTATE. ``True`` authenticated, ``False`` refused, ``None`` UNMEASURED — a
    missing ``gh`` binary or a timeout is not evidence of an auth failure, and a
    probe that cannot run must not manufacture an outage.

    It answers for THIS host. The daemon may hold a different token, so a stale
    credential over there surfaces through the watcher's own recorded ``fetch
    failed`` rows instead; both paths land on an outage severity and the detail
    says which one saw it.
    """
    if runner is None:
        runner = subprocess.run
    try:
        proc = runner([gh_bin, "auth", "status"], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=30)
    except Exception as exc:  # noqa: BLE001 — reported, never swallowed
        return None, "could not run `gh auth status`: %s" % exc
    out = ((getattr(proc, "stdout", "") or "")
           + (getattr(proc, "stderr", "") or ""))
    if getattr(proc, "returncode", 1) == 0:
        return True, "gh reports an active, authenticated account"
    low = out.lower()
    if "401" in low or "bad credentials" in low or "token" in low:
        return False, "gh auth status failed: %s" % out.strip()[:200]
    return None, ("gh auth status was inconclusive: %s"
                  % (out.strip()[:200] or "no output"))


def watcher_liveness(
    stale_after_minutes: float, *, conn=None
) -> Tuple[Optional[bool], Dict[str, Any]]:
    """Is the pr_watcher daemon completing polls? ``(alive, raw)``.

    Delegates to ``tools.kanban.metrics.watcher_heartbeat`` rather than reading
    ``heartbeat_checks`` here, so this report and the kanban surfaces cannot
    disagree about whether the same watcher is alive.

    TRISTATE, and ``never_polled`` is deliberately ``None`` and not ``False``: on
    a fresh database or an install that has never run the daemon, "no heartbeat
    row" means nobody has ever measured it, which is not the same finding as "the
    daemon stopped". The first needs a daemon started; the second needs one
    restarted, and an alarm that cannot tell them apart sends people to the wrong
    place.
    """
    try:
        from tools.kanban.metrics import watcher_heartbeat  # noqa: PLC0415

        raw = watcher_heartbeat(conn=conn, stale_after_minutes=stale_after_minutes)
    except Exception as exc:  # noqa: BLE001
        return None, {"state": "unmeasured", "error": str(exc)}
    state = raw.get("state")
    if state == "polling":
        return True, raw
    if state == "stale":
        return False, raw
    return None, raw


def watcher_holds(
    urls: Iterable[str],
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    lookback_hours: int = DEFAULT_ATTRIBUTION_LOOKBACK_HOURS,
    patterns: Iterable[Tuple[str, str]] = DEFAULT_HOLD_PATTERNS,
    get_connection=None,
) -> Dict[str, Dict[str, Any]]:
    """url -> the most recent hold the watcher itself recorded, if any.

    THE SUBSTRATE IS ALREADY THERE, WITH ROWS. ``audit_trail`` holds 104,319
    ``pr_watcher`` rows (measured 2026-08-19), 42,742 of them ``pr_watcher.wait``
    carrying the refusal's own reason text. Attribution therefore needs no new
    writer, no new instrumentation and no new table — it needs the existing
    record READ, which is exactly what nothing was doing.

    Reads only rows the WATCHER wrote about THIS url, newest first, and stops at
    the first one a pattern matches. FAIL-OPEN: any failure returns ``{}``, so
    every PR falls through to ``unattributed`` rather than being silently
    excused. Excusing on missing evidence is how an alarm goes quiet.

    ``until`` BOUNDS THE WINDOW ABOVE and is not optional in spirit. Without it
    the newest-first scan can settle on a row written AFTER the moment being
    explained — for the live report "after now" is empty so it never bit, but the
    survey asks "what held this PR BEFORE it merged" and a post-merge row masked
    the earlier one that answered. Measured while building this: the unbounded
    version attributed 28 of 150 merged PRs where the bounded one attributes 58,
    which moved the unattributed maximum from 10.65 to 19.98 minutes — i.e. it
    would have talked the threshold this module ships with out of its own
    evidence. A bound that only matters retrospectively still has to be right.
    """
    wanted = [u for u in {(u or "").strip() for u in urls} if u]
    if not wanted:
        return {}
    if get_connection is None:
        try:
            from tools.db.storage import get_connection as get_connection  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return {}
    cutoff = since or (datetime.now(timezone.utc)
                       - timedelta(hours=max(1, lookback_hours)))
    ceiling = until or datetime.now(timezone.utc)
    try:
        conn = get_connection()
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for url in wanted:
            try:
                rows = conn.execute(
                    "SELECT created_at, details FROM audit_trail "
                    "WHERE actor = %s AND created_at >= %s AND created_at <= %s "
                    "AND details LIKE %s ORDER BY created_at DESC LIMIT 40",
                    ("pr_watcher", cutoff.isoformat(), ceiling.isoformat(),
                     "%" + url + "%"),
                ).fetchall()
            except Exception:  # noqa: BLE001 — one bad url must not lose the rest
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                continue
            for row in rows or []:
                detail = row["details"] if isinstance(row, dict) else row[1]
                created = row["created_at"] if isinstance(row, dict) else row[0]
                try:
                    payload = json.loads(detail or "{}")
                except (ValueError, TypeError):
                    continue
                # The audit row's `details` is a whole WatcherAction, and a LIKE
                # on the serialized blob matches any field it appears in. Confirm
                # the url is this action's SUBJECT before attributing to it.
                if (payload.get("pr_url") or "").strip() != url:
                    continue
                cause = attribute_reason(payload.get("reason") or "", patterns)
                if cause:
                    out[url] = {
                        "cause": cause,
                        "reason": (payload.get("reason") or "")[:300],
                        "action": payload.get("action"),
                        "observed_at": str(created),
                    }
                    break
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return out


# ────────────────────────────────────────────────────────────────────────────
# The observation table — the only thing this module writes
# ────────────────────────────────────────────────────────────────────────────


def latest_observations(
    urls: Iterable[str], *, get_connection=None
) -> Dict[str, Dict[str, Any]]:
    """url -> its newest ``pr_merge_eligibility_events`` row, or absent.

    ONE indexed lookup per PR and no aggregation, because rows are written per
    TRANSITION: the newest row for a PR that has been ``ready`` for an hour IS
    the row that recorded it going ready. Returns ``{}`` if the table does not
    exist yet, which degrades the age to ``ci_estimate`` rather than raising.
    """
    wanted = [u for u in {(u or "").strip() for u in urls} if u]
    if not wanted:
        return {}
    if get_connection is None:
        try:
            from tools.db.storage import get_connection as get_connection  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return {}
    try:
        conn = get_connection()
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for url in wanted:
            try:
                row = conn.execute(
                    "SELECT pr_url, head_sha, state, eligible, door, reason, "
                    "observed_at FROM " + EVENTS_TABLE + " WHERE pr_url = %s "
                    "ORDER BY observed_at DESC, id DESC LIMIT 1",
                    (url,),
                ).fetchone()
            except Exception:  # noqa: BLE001 — table may not exist yet
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                return out
            if row is None:
                continue
            out[url] = row if isinstance(row, dict) else {
                "pr_url": row[0], "head_sha": row[1], "state": row[2],
                "eligible": row[3], "door": row[4], "reason": row[5],
                "observed_at": row[6],
            }
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def record_transitions(
    rows: List[Dict[str, Any]],
    *,
    previous: Optional[Dict[str, Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
    recorded_by: str = "cli",
    get_connection=None,
) -> Dict[str, Any]:
    """Append one row per CHANGED ``(state, head_sha)``. Returns what it did.

    APPEND-ONLY — ``pr_merge_eligibility_events`` is registered in
    APPEND_ONLY_TABLES. An observation that a PR was eligible at 03:14 does not
    stop being true when it merges at 03:15, so nothing here updates or deletes.

    Writing only on CHANGE is what makes ``observed_at`` mean "first seen in this
    state" with no aggregation anywhere. Writing every poll would make the newest
    row mean "seen 30 seconds ago" — true, useless, and 29,000 rows a day.

    Best-effort by design: a recording failure returns ``ok: False`` with the
    error and never raises, because the caller is either a report (which must
    still print) or the watch loop (which must never stop).
    """
    now = now or datetime.now(timezone.utc)
    prev = previous if previous is not None else latest_observations(
        [r.get("url") for r in rows], get_connection=get_connection)
    pending: List[Dict[str, Any]] = []
    for row in rows:
        url = (row.get("url") or "").strip()
        if not url:
            continue
        before = prev.get(url)
        head = (row.get("head_sha") or "") or None
        if before is not None:
            same_state = str(before.get("state") or "") == str(row.get("state") or "")
            same_head = (before.get("head_sha") or None) == head
            if same_state and same_head:
                continue
        pending.append({
            "pr_url": url,
            "pr_number": row.get("number"),
            "head_sha": head,
            "head_ref": row.get("head") or None,
            "state": str(row.get("state") or ""),
            "eligible": 1 if row.get("state") == READY else 0,
            "door": row.get("door"),
            "reason": (row.get("reason") or "")[:500] or None,
            "observed_at": now.isoformat(),
            "recorded_by": recorded_by,
        })
    result: Dict[str, Any] = {
        "ok": True, "written": 0, "unchanged": len(rows) - len(pending),
        "error": None}
    if not pending:
        return result
    if get_connection is None:
        try:
            from tools.db.storage import get_connection as get_connection  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            result.update(ok=False, error="no storage backend: %s" % exc)
            return result
    try:
        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        result.update(ok=False, error="cannot open a connection: %s" % exc)
        return result
    try:
        for item in pending:
            conn.execute(
                "INSERT INTO " + EVENTS_TABLE + " (pr_url, pr_number, head_sha, "
                "head_ref, state, eligible, door, reason, observed_at, "
                "recorded_by, classification) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (item["pr_url"], item["pr_number"], item["head_sha"],
                 item["head_ref"], item["state"], item["eligible"], item["door"],
                 item["reason"], item["observed_at"], item["recorded_by"],
                 # The LABEL, never a banner. 'CUI // SP-CTI' matches no label at
                 # any clearance, so the row would be written, retained and invisible.
                 "CUI"),
            )
            result["written"] += 1
        if hasattr(conn, "commit"):
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — never stop a report or a watch loop
        result.update(ok=False, error=str(exc)[:300])
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return result


# ────────────────────────────────────────────────────────────────────────────
# Report
# ────────────────────────────────────────────────────────────────────────────


def eligibility_rows(
    prs: List[Dict[str, Any]],
    *,
    default_branch: str,
    linked_urls: Iterable[str] = (),
    protected_paths: Iterable[str] = (),
    behind_by_url: Optional[Dict[str, Optional[int]]] = None,
    max_behind_commits: int = DEFAULT_MAX_BEHIND_COMMITS,
) -> List[Dict[str, Any]]:
    """Classify every PR with the ``linked`` rung SKIPPED, carrying the door apart.

    ``linked_urls`` is used ONLY to label ``door``; it is never handed to the
    ladder. See the module docstring: ownership says who merges, not whether the
    PR is finished, and the task path is where most stalls live.
    """
    linked = {(u or "").strip() for u in linked_urls}
    behind = dict(behind_by_url or {})
    out: List[Dict[str, Any]] = []
    for pr in prs:
        url = (pr.get("url") or "").strip()
        files = pr.get("files")
        verdict = classify_merge_readiness(
            pr, default_branch=default_branch,
            linked_urls=(),                       # <- the point
            behind_by=behind.get(url),
            max_behind_commits=max_behind_commits,
            changed_files=([f.get("path") for f in files if f.get("path")]
                           if files is not None else None),
            protected_paths=protected_paths,
        )
        green = ci_green_at(pr)
        out.append({
            "number": pr.get("number"),
            "url": url,
            "title": pr.get("title") or "",
            "head": pr.get("headRefName") or "",
            "head_sha": pr.get("headRefOid") or "",
            "state": verdict.state,
            "reason": verdict.reason,
            "eligible": verdict.state == READY,
            "door": DOOR_LINKED if url in linked else DOOR_UNLINKED,
            "ci_green_at": green.isoformat() if green else None,
        })
    return out


def build_stall_report(
    rows: List[Dict[str, Any]],
    *,
    now: datetime,
    config: Dict[str, Any],
    observations: Optional[Dict[str, Dict[str, Any]]] = None,
    holds: Optional[Dict[str, Dict[str, Any]]] = None,
    watcher_alive: Optional[bool] = None,
    watcher_raw: Optional[Dict[str, Any]] = None,
    forge_ok: Optional[bool] = None,
    forge_detail: str = "",
    merger_enabled_by_door: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Classify every row and summarise. Pure — takes data, returns data."""
    obs = observations or {}
    hold_map = holds or {}
    doors = merger_enabled_by_door or {DOOR_LINKED: True, DOOR_UNLINKED: True}
    stall_after = float(config["stall_after_minutes"])
    by_design_after = float(config["by_design_stall_after_minutes"])

    out_rows: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    cause_counts: Dict[str, int] = {}
    for row in rows:
        url = row["url"]
        eligible = bool(row.get("eligible"))

        # ── age, from two sources that are never merged ────────────────────
        ready_since, source = None, "unmeasured"
        record = obs.get(url)
        if record is not None and record.get("eligible") in (1, True, "1"):
            # A recorded row about a DIFFERENT branch tip is about a branch that
            # no longer exists. Carrying its age forward would date a force-pushed
            # PR to the commit it replaced.
            rec_head = (record.get("head_sha") or "") or None
            cur_head = (row.get("head_sha") or "") or None
            if rec_head is None or cur_head is None or rec_head == cur_head:
                ready_since = parse_ts(record.get("observed_at"))
                if ready_since is not None:
                    source = "recorded"
        green_at = parse_ts(row.get("ci_green_at"))
        if ready_since is None and green_at is not None:
            ready_since, source = green_at, "ci_estimate"

        age = (round((now - ready_since).total_seconds() / 60.0, 2)
               if ready_since is not None else None)
        ci_age = (round((now - green_at).total_seconds() / 60.0, 2)
                  if green_at is not None else None)

        hold = hold_map.get(url) or {}
        verdict = classify_stall(
            eligible=eligible,
            age_minutes=age,
            stall_after_minutes=stall_after,
            by_design_after_minutes=by_design_after,
            watcher_alive=watcher_alive,
            forge_ok=forge_ok,
            hold_cause=hold.get("cause"),
            merger_enabled=doors.get(row.get("door"), True),
            ineligible_reason=row.get("reason") or "",
        )
        counts[verdict.severity] = counts.get(verdict.severity, 0) + 1
        cause_counts[verdict.cause] = cause_counts.get(verdict.cause, 0) + 1
        out_rows.append({
            **row,
            "severity": verdict.severity,
            "cause": verdict.cause,
            "detail": verdict.detail,
            "alarming": verdict.alarming,
            # THE AGE, and where it came from. Never one without the other.
            "age_minutes": age,
            "ready_since": ready_since.isoformat() if ready_since else None,
            "ready_since_source": source,
            # Reported even when `recorded` won, so "recorded 0 min but green 3
            # hours ago" is legible rather than collapsed into a single number.
            "ci_green_age_minutes": ci_age,
            "hold_reason": hold.get("reason"),
            "hold_action": hold.get("action"),
        })

    order = {s: i for i, s in enumerate(SEVERITIES)}
    out_rows.sort(key=lambda r: (order.get(r["severity"], 99),
                                 -(r["age_minutes"] or 0.0)))
    return {
        "generated_at": now.isoformat(),
        "stall_after_minutes": stall_after,
        "by_design_stall_after_minutes": by_design_after,
        "total": len(out_rows),
        "eligible": sum(1 for r in out_rows if r.get("eligible")),
        # The headline the card asks for: of the PRs the ladder says are ready,
        # how many are still open past the threshold with nothing explaining it.
        "alarms": sum(1 for r in out_rows if r["severity"] == SEV_ALARM),
        "outages": sum(1 for r in out_rows if r["severity"] == SEV_OUTAGE),
        "by_design": sum(1 for r in out_rows if r["severity"] == SEV_BY_DESIGN),
        "unmeasured": sum(1 for r in out_rows if r["severity"] == SEV_UNMEASURED),
        "counts": dict(sorted(counts.items())),
        "causes": dict(sorted(cause_counts.items())),
        "watcher": watcher_raw or {},
        "watcher_alive": watcher_alive,
        "forge_auth_ok": forge_ok,
        "forge_auth_detail": forge_detail,
        "merger_enabled": dict(doors),
        "prs": out_rows,
    }


_TRISTATE_WATCHER = {True: "POLLING", False: "STALE -- NOT MERGING",
                     None: "UNMEASURED"}
_TRISTATE_AUTH = {True: "ok", False: "REFUSED", None: "unmeasured"}


def render_table(report: Dict[str, Any]) -> str:
    """ASCII-only — a box-drawing character raises on a cp1252 console."""
    watcher = report.get("watcher") or {}
    lines: List[str] = [
        "%d open PR(s), %d merge-ELIGIBLE -- %d alarm(s), %d outage(s), "
        "%d held by design, %d unmeasured"
        % (report["total"], report["eligible"], report["alarms"],
           report["outages"], report["by_design"], report["unmeasured"]),
        "pr_watcher: %s (%s); forge auth: %s"
        % (_TRISTATE_WATCHER[report.get("watcher_alive")],
           watcher.get("summary") or watcher.get("state") or "?",
           _TRISTATE_AUTH[report.get("forge_auth_ok")]),
    ]
    if not report["prs"]:
        return "\n".join(lines)
    lines.append("")
    lines.append("%-7s %-11s %-18s %-9s %-11s %s"
                 % ("PR", "SEVERITY", "CAUSE", "AGE(min)", "AGE SOURCE", "DETAIL"))
    lines.append("%-7s %-11s %-18s %-9s %-11s %s"
                 % ("-" * 7, "-" * 11, "-" * 18, "-" * 9, "-" * 11, "-" * 40))
    for row in report["prs"]:
        # "?" not "0" -- an unmeasured age and a zero age are different facts.
        age = "?" if row.get("age_minutes") is None else "%.1f" % row["age_minutes"]
        lines.append("%-7s %-11s %-18s %-9s %-11s %s" % (
            "#%s" % row["number"], row["severity"], row["cause"], age,
            row["ready_since_source"], row["detail"][:90]))
    lines.append("")
    lines.append("by cause: " + (", ".join(
        "%s=%d" % (k, v) for k, v in report["causes"].items()) or "-"))
    lines.append(
        "thresholds: unattributed %.0f min, held-by-design %.0f min "
        "(args/merge_stall.yaml records the survey that chose them)"
        % (report["stall_after_minutes"], report["by_design_stall_after_minutes"]))
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# Survey — re-derive the thresholds from the repo's own merge history
# ────────────────────────────────────────────────────────────────────────────

#: The candidate thresholds the survey reports a would-fire rate for.
SURVEY_THRESHOLDS: Tuple[int, ...] = (5, 10, 15, 20, 30, 45, 60, 120)


def survey_merged(
    prs: List[Dict[str, Any]],
    *,
    holds_for=None,
) -> Dict[str, Any]:
    """How long did merged PRs actually wait after going green, and why?

    THE MEASUREMENT THE THRESHOLD COMES FROM, kept in the tool so it can be
    re-run rather than re-derived by hand. Eligibility is estimated as the last
    check to complete AT OR BEFORE the merge: ``E2E (Playwright)`` and ``Two-Tier
    LLM Build`` are ``needs:``-gated jobs whose check runs do not exist yet when
    the merger polls, so counting completions after the merge dated 57 of 150 PRs
    as merged before they were green.

    ``holds_for(url, since, until) -> Optional[str]`` attributes a wait to a
    recorded hold. Pass ``None`` to measure raw latency only; the two are
    reported separately because the whole finding is how far apart they are.
    """
    attributed: List[float] = []
    unattributed: List[float] = []
    per_cause: Dict[str, int] = {}
    skipped: Dict[str, int] = {}
    for pr in prs:
        merged_at = parse_ts(pr.get("mergedAt"))
        if merged_at is None:
            skipped["no_mergedAt"] = skipped.get("no_mergedAt", 0) + 1
            continue
        stamps = [parse_ts(c.get("completedAt"))
                  for c in (pr.get("statusCheckRollup") or [])]
        stamps = [s for s in stamps if s is not None and s <= merged_at]
        if not stamps:
            skipped["no_check_before_merge"] = skipped.get(
                "no_check_before_merge", 0) + 1
            continue
        green = max(stamps)
        minutes = (merged_at - green).total_seconds() / 60.0
        cause = None
        if holds_for is not None:
            try:
                cause = holds_for((pr.get("url") or "").strip(), green, merged_at)
            except Exception:  # noqa: BLE001 — a failed lookup is not a hold
                cause = None
        if cause:
            attributed.append(minutes)
            per_cause[cause] = per_cause.get(cause, 0) + 1
        else:
            unattributed.append(minutes)
    measured = len(attributed) + len(unattributed)
    return {
        "population": len(prs),
        "measured": measured,
        "skipped": skipped,
        "raw": _percentiles(sorted(attributed + unattributed)),
        "attributed": _percentiles(sorted(attributed)),
        "unattributed": _percentiles(sorted(unattributed)),
        "causes": dict(sorted(per_cause.items())),
        "fire_rate_raw": _fire_rates(attributed + unattributed, measured),
        "fire_rate_attributed": _fire_rates(unattributed, measured),
    }


def _percentiles(values: List[float]) -> Dict[str, Any]:
    """p50/p90/p95/p99/max over a SORTED list. ``n: 0`` reports None, never 0.0."""
    if not values:
        return {"n": 0, "p50": None, "p90": None, "p95": None,
                "p99": None, "max": None}

    def at(q: float) -> float:
        idx = min(len(values) - 1, int(round((len(values) - 1) * q)))
        return round(values[idx], 2)

    return {"n": len(values), "p50": at(0.5), "p90": at(0.9), "p95": at(0.95),
            "p99": at(0.99), "max": round(values[-1], 2)}


def _fire_rates(values: Iterable[float], population: int) -> Dict[str, float]:
    """What fraction of the WHOLE population each candidate threshold fires on.

    Denominator is the whole population on purpose, including the PRs excluded by
    attribution — the question is "how often would this alarm interrupt a routine
    merge", and a routine merge that was correctly attributed still happened.
    """
    if not population:
        return {}
    vals = list(values)
    return {str(t): round(100.0 * sum(1 for v in vals if v > t) / population, 2)
            for t in SURVEY_THRESHOLDS}


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def list_prs(state: str, *, runner=None, limit: int = 100, gh_bin: str = "gh",
             fields: str = _GH_FIELDS) -> List[Dict[str, Any]]:
    """``gh pr list`` for one state. Raises on failure.

    Never returns an empty list to stand in for an error: an empty table and a
    broken forge must not print the same thing.
    """
    if runner is None:
        runner = subprocess.run
    proc = runner(
        [gh_bin, "pr", "list", "--state", state, "--limit", str(int(limit)),
         "--json", fields],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
    )
    if getattr(proc, "returncode", 1) != 0:
        raise RuntimeError(
            "gh pr list --state %s failed (rc=%s): %s"
            % (state, getattr(proc, "returncode", "?"),
               (getattr(proc, "stderr", "") or "").strip()[:300]))
    return list(json.loads(getattr(proc, "stdout", "") or "[]"))


def main(argv: Optional[List[str]] = None) -> int:
    """Exit 0 = reported (or clean under --gate), 1 = an alarm under --gate,
    2 = THE REPORT COULD NOT BE PRODUCED.

    2 is not "found nothing". A report that could not run must not read the same
    as a pipeline with nothing stuck — that is the mistake this whole module
    exists to stop making about merges.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci.merge_stall",
        description="Which merge-ELIGIBLE PRs are still open, for how long, and "
                    "why? Read-only against the forge; writes only its own "
                    "append-only observation table.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable")
    parser.add_argument("--gate", action="store_true",
                        help="exit 1 when any PR is in the `alarm` severity")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--default-branch", default=None)
    parser.add_argument("--from-json", default=None, metavar="PATH",
                        help="classify a saved `gh pr list --json` file instead "
                             "of calling gh (offline / replay)")
    parser.add_argument("--no-record", action="store_true",
                        help="do not append observations; every age then falls "
                             "back to the labelled `ci_estimate`")
    parser.add_argument("--stall-after", type=float, default=None, metavar="MIN",
                        help="override stall_after_minutes for this run")
    parser.add_argument("--survey", action="store_true",
                        help="re-derive the thresholds from merged-PR history "
                             "instead of reporting on open PRs")
    parser.add_argument("--survey-limit", type=int, default=150,
                        help="how many merged PRs the survey reads (default 150)")
    args = parser.parse_args(argv)

    config = load_config()
    if args.stall_after is not None:
        config["stall_after_minutes"] = float(args.stall_after)
    now = datetime.now(timezone.utc)

    if args.survey:
        return _run_survey(args, config, now)
    return _run_report(args, config, now)


def _run_survey(args, config: Dict[str, Any], now: datetime) -> int:
    try:
        merged = list_prs("merged", limit=args.survey_limit,
                          fields="number,url,mergedAt,statusCheckRollup")
    except Exception as exc:  # noqa: BLE001
        return _fail(args.json, "cannot list merged PRs: %s" % exc)
    patterns = config["hold_patterns"]
    lookback = config["attribution_lookback_hours"]

    def holds_for(url: str, since: datetime, until: datetime) -> Optional[str]:
        # BOTH bounds. A hold recorded after the merge cannot have delayed it,
        # and — the part that actually bit — letting one into the newest-first
        # scan masks the earlier row that did.
        found = watcher_holds([url], since=since, until=until, patterns=patterns,
                              lookback_hours=lookback)
        entry = found.get(url)
        return entry.get("cause") if entry else None

    result = survey_merged(merged, holds_for=holds_for)
    result["surveyed_at"] = now.isoformat()
    result["live_thresholds"] = {
        "stall_after_minutes": config["stall_after_minutes"],
        "by_design_stall_after_minutes": config["by_design_stall_after_minutes"],
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_survey(result, config))
    return 0


def _run_report(args, config: Dict[str, Any], now: datetime) -> int:
    if args.from_json:
        path = pathlib.Path(args.from_json)
        try:
            prs = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return _fail(args.json, "cannot read %s: %s" % (path, exc))
        if not isinstance(prs, list):
            return _fail(args.json, "%s does not contain a PR list" % path)
    else:
        try:
            prs = list_prs("open", limit=args.limit)
        except Exception as exc:  # noqa: BLE001
            return _fail(args.json, "cannot list open PRs: %s" % exc)

    default_branch = args.default_branch
    if not default_branch:
        try:
            from tools.ci.pr_watcher import repo_default_branch  # noqa: PLC0415

            default_branch = repo_default_branch()
        except Exception as exc:  # noqa: BLE001
            return _fail(args.json, "cannot resolve the default branch: %s" % exc)

    try:
        from tools.ci.merge_readiness import linked_pr_urls  # noqa: PLC0415

        linked = linked_pr_urls()
    except Exception as exc:  # noqa: BLE001 — degraded, and it SAYS so
        linked = frozenset()
        print("warning: kanban board unreadable, every PR reads as unlinked (%s)"
              % exc, file=sys.stderr)

    wcfg = watcher_config()
    rows = eligibility_rows(
        prs, default_branch=default_branch, linked_urls=linked,
        protected_paths=wcfg.get("protected_paths") or [],
        max_behind_commits=int(wcfg.get("max_behind_commits",
                                        DEFAULT_MAX_BEHIND_COMMITS)))

    # RECORD BEFORE READING. A PR that just became eligible then reads back at
    # age 0, which is what "first seen ready" means — the clock starts when
    # observation starts, and cannot manufacture a backlog of alarms out of
    # history this recorder was not present for.
    recording = {"ok": None, "written": 0, "error": "recording disabled"}
    if config["record_observations"] and not args.no_record:
        recording = record_transitions(rows, now=now, recorded_by="cli")

    urls = [r["url"] for r in rows]
    alive, watcher_raw = watcher_liveness(config["watcher_stale_after_minutes"])
    forge_ok, forge_detail = probe_forge_auth()
    report = build_stall_report(
        rows, now=now, config=config,
        observations=latest_observations(urls),
        holds=watcher_holds(urls, patterns=config["hold_patterns"],
                            lookback_hours=config["attribution_lookback_hours"]),
        watcher_alive=alive, watcher_raw=watcher_raw, forge_ok=forge_ok,
        forge_detail=forge_detail,
        merger_enabled_by_door={
            DOOR_LINKED: bool(wcfg.get("auto_merge_enabled", True)),
            DOOR_UNLINKED: bool(wcfg.get("merge_unlinked_prs", True)),
        })
    report["recording"] = recording
    report["default_branch"] = default_branch

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_table(report))
    return 1 if (args.gate and report["alarms"]) else 0


def render_survey(result: Dict[str, Any], config: Dict[str, Any]) -> str:
    """ASCII-only survey table — the evidence a threshold change has to beat."""
    lines = ["merged-PR wait after going green -- %d of %d PR(s) measured"
             % (result["measured"], result["population"])]
    if result["skipped"]:
        lines.append("skipped: " + ", ".join(
            "%s=%d" % kv for kv in sorted(result["skipped"].items())))
    lines.append("")
    lines.append("%-14s %5s %8s %8s %8s %8s %8s"
                 % ("POPULATION", "n", "p50", "p90", "p95", "p99", "max"))
    for name in ("raw", "attributed", "unattributed"):
        p = result[name]
        cells = ["-" if p[k] is None else "%.2f" % p[k]
                 for k in ("p50", "p90", "p95", "p99", "max")]
        lines.append("%-14s %5d %8s %8s %8s %8s %8s" % (name, p["n"], *cells))
    lines.append("")
    lines.append("would-fire rate over the WHOLE population, by threshold (min):")
    lines.append("%-26s %s" % ("threshold", "  ".join(
        "%6s" % t for t in result["fire_rate_raw"])))
    lines.append("%-26s %s" % ("ignoring cause (RAW age)", "  ".join(
        "%5.2f%%" % v for v in result["fire_rate_raw"].values())))
    lines.append("%-26s %s" % ("attributing cause first", "  ".join(
        "%5.2f%%" % v for v in result["fire_rate_attributed"].values())))
    lines.append("")
    lines.append("attributed to: " + (", ".join(
        "%s=%d" % kv for kv in result["causes"].items()) or "-"))
    lines.append(
        "live thresholds: unattributed %.0f min, held-by-design %.0f min"
        % (config["stall_after_minutes"],
           config["by_design_stall_after_minutes"]))
    return "\n".join(lines)


def _fail(as_json: bool, message: str) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}, indent=2))
    else:
        print("ERROR: %s" % message, file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover — exercised via main(argv)
    sys.exit(main())
