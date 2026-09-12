# CUI // SP-CTI
"""Consume the detectors nobody runs, and turn each finding into a card that
carries its own evidence (autonomy-act-02).

THE DEFECT. Three detectors were built in one week and measured on 2026-08-20
to be imported by NOBODY on any runtime path under tools/genesis, tools/ci or
tools/kanban:

    status_churn      (kpr-watch-11)  two writers taking turns on one row.
                                      It found a task flipping done<->backlog
                                      95 times in 5.5 hours.
    born_red_survey   (rem-hyg-14)    a test red since the day it landed.
                                      It found one red for six weeks.
    recovery_summary  (rem-hyg-16)    retry ATTEMPTS miscounted as recoveries.
                                      The panel claimed 331; 46 of 86 were real.

A fourth joined them (autonomy-dep-02):

    migration_drift   (autonomy-dep-01) a migration on the default branch that
                                      was never applied HERE. Found id-01's own
                                      migration pending, which is why that card
                                      recorded nothing after it merged.

Each was built because a human found the defect BY HAND, and each then sat
waiting for a human to run it by hand — declared-but-unconsumed, this
platform's signature defect, reaching its own self-observation layer. This
module is the consumer. It BUILDS NO DETECTOR: it calls the three that exist,
on the Genesis cadence, and files what they report.

A CARD CARRIES ITS DERIVATION, never a bare alert. Every card rendered here
holds the detector's own row verbatim, the exact command that re-derives it,
and what "fixed" looks like. A finding without its derivation cannot be acted
on and gets dismissed; a finding WITH it is a work item.

DEDUPE ON THE FINDING, NOT THE RUN. A detector that re-files the same card
every six hours floods the board and gets switched off, which puts the finding
back where it started. One row per (detector, subject, fingerprint) in
``detector_findings``, upserted on re-observation with ``seen_count`` — the
projection shape cef-ui-02 uses for Cortex conflicts. A card is seeded when a
finding is FIRST seen, and again only if it RECURS after its card was closed
(``card_count`` says how often). ``idempotency_key`` on the seeded spec is the
second lock, inside ``create_tasks`` itself.

A CARD CLOSED EARLY IS NOT A RECURRENCE (task-f05d2bc8d1). A recovery finding
is derived from pr_watcher audit rows inside a 24h window, and ``escalate``
outranks any later merge (rem-hyg-16, correct and untouched), so the finding
CANNOT leave the summary before last-attempt + window_hours whatever a human
does -- fixing the PR, landing it and releasing the lease all leave the row in
place. The recurrence rule used to read a card marked ``done`` inside that
window as "the fix did not hold" and file a ``-r2``, which was then DISPATCHED
against a subject already delivered. Measured on the live board 2026-09-04:
3 of 3 ``card_count=2`` recovery findings had their first card closed before
the finding's earliest possible clear time; the 10 whose card closed AFTER
``cleared_at`` never recurred. So a Finding may carry ``earliest_clear_at``
(recovery: the newest counted attempt row's stamp + window_hours, both already
in hand), and a TERMINAL card while ``now < earliest_clear_at`` is held -- the
finding stays active on its existing task_id, ``seen_count`` rises, nothing is
filed, and the hold is REPORTED (``held_closed_early``). A ``-rN`` is filed
only when a MEASURABLE run after that time still reports the subject, or when
the finding was seen ``cleared`` and reappears -- the plain meaning of "did not
hold". born_red and status_churn carry no such time and keep the plain rule.

A RECORD IS NOT A DISPATCHABLE CARD (autonomy-act-04). ``summarize_recovery``
gives ``escalate`` priority over any later ``merge``, and that is CORRECT --
counting a post-escalation merge as a recovery is exactly the inflation
rem-hyg-16 exists to refuse. Nothing here changes that verdict, its threshold
or its window. The objection is one layer up, about SEEDING: a
``needed_a_human`` finding whose subject has since merged and gone terminal is
a true statement about the PAST with no remaining work, and a card for it
dispatches a worker session against a delivered subject -- a dispatch that
CANNOT go RED, because there is nothing left to change. Measured on the live
board 2026-09-05 over all 25 recorded ``recovery`` findings: every subject was
``done`` (21) or ``pr_opened`` (4), NONE abandoned or stuck; 16 carried a
``pr_watcher.merge`` after their escalation and for 12 of those the merge was
the WATCHER'S OWN (``auto-merge ok``) -- the escalation asked for a human and
no human came. The rule is a CONJUNCTION of two pieces of primary data, no
elapsed time and no threshold: a ``merge`` row NEWER than the newest
``escalate`` row for the subject, AND the subject task CLOSED on the board. It
holds for 15 of the 25. The finding is still RECORDED, upserted and cleared
exactly as before -- suppressing the row would delete the measurement, and only
the CARD is at issue. EVERY UNKNOWN KEEPS THE CARD: an unreadable order, a
subject not on the board, a subject still in flight. A wasted dispatch and a
silently demoted escalation are not the same price.

UNMEASURABLE CLEARS NOTHING. status_churn on an idle board, born_red_survey on
an unmigrated baseline, recovery_summary with no audit rows, migration_drift on
a database with no migration history — each reports
that it could not measure, and a run that could not measure must not be read
as "the finding is gone". Only a MEASURABLE run that no longer reports a
finding clears it. ``detector_runs`` is the denominator that keeps "never
ran", "ran and could not measure" and "ran and found nothing" apart.

Seeds through ``tools.kanban.task_factory.create_tasks``, never a raw INSERT.

    python -m tools.kanban.detector_findings --json          # run, seed, report
    python -m tools.kanban.detector_findings --dry-run       # run; write NOTHING
    python -m tools.kanban.detector_findings --list          # browse the projection
    python -m tools.kanban.detector_findings --records       # card vs record, re-derived
    python -m tools.kanban.detector_findings --list --detector born_red --status cleared
    python -m tools.kanban.detector_findings --stats
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from tools.common.helpers import parse_utc_timestamp
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.kanban.detector_findings")

FINDINGS_TABLE = "detector_findings"
RUNS_TABLE = "detector_runs"
MIGRATION = "20260821050135_detector_findings_projection"

DETECTOR_STATUS_CHURN = "status_churn"
DETECTOR_BORN_RED = "born_red"
DETECTOR_RECOVERY = "recovery"
DETECTOR_MIGRATION_DRIFT = "migration_drift"
DETECTOR_DEPLOYMENT_FRESHNESS = "deployment_freshness"
DETECTORS = (DETECTOR_STATUS_CHURN, DETECTOR_BORN_RED, DETECTOR_RECOVERY,
             DETECTOR_MIGRATION_DRIFT, DETECTOR_DEPLOYMENT_FRESHNESS)

#: detector_runs.last_state — and the per-detector ``state`` in a report.
RUN_FINDINGS = "findings"
RUN_CLEAN = "clean"
RUN_UNMEASURABLE = "unmeasurable"
RUN_ERROR = "error"

FINDING_ACTIVE = "active"
FINDING_CLEARED = "cleared"

#: Every fingerprint in this module is built by joining parts with this.
FINGERPRINT_SEP = "|"

#: Detectors whose fingerprint is a SET OF INDEPENDENTLY RESOLVABLE MEMBERS,
#: and NOT merely a string that happens to contain a separator (autonomy-dep-05).
#:
#: A finding's identity is its fingerprint, and ``_clear_missing`` clears every
#: active finding the current run did not report. For a SET-valued fingerprint
#: those two rules compose into a FALSE CLEAR: when the set changes because a
#: NEW member arrived -- not because any old member was resolved -- the
#: finding_id changes, the old row is not in ``still_active``, and it is written
#: ``cleared`` while everything it names is still true. MEASURED on the live
#: board over the whole recorded ``migration_drift`` population (6 clears,
#: 2026-09-03..09): TWO of the six cleared a finding whose migration was applied
#: 2h59m and 1h11m LATER. ``cleared`` is what a detector card's acceptance
#: criterion reads, so a card was verifiable complete before the work happened.
#:
#: A member here must be resolvable ON ITS OWN, because the survival rule is
#: membership: the finding is still true while ANY member is still reported.
#: ``migration_drift`` qualifies -- a pending migration version is applied
#: individually and answers for itself.
#:
#: DELIBERATELY NOT DECLARED, and each for its own reason:
#:   status_churn   ``<cycle>|contested`` -- a cycle and a flag, not two members.
#:   born_red       a CONSTANT fingerprint; there is no set.
#:   recovery       a CONSTANT fingerprint; there is no set.
#:   deployment_freshness  ``<reason>|<conflicts...>`` IS the same shape and is
#:                  NAMED, NOT SURVEYED (autonomy-dep-05's card says so in
#:                  terms). Its first member is a REASON, not a conflicting
#:                  file, so membership would have to be defined over the tail
#:                  only -- a real design decision, and asserting it without
#:                  measuring the population would be the very defect this
#:                  constant exists to fix. Declare it when it has a survey.
SET_VALUED_FINGERPRINT_DETECTORS = frozenset({DETECTOR_MIGRATION_DRIFT})

#: What ``consume`` does with a finding it has just projected (autonomy-act-04).
#: A finding is ALWAYS recorded; this decides only whether anybody is asked to
#: do something about it.
DISPOSITION_CARD = "card"      # somebody has to act
DISPOSITION_RECORD = "record"  # a true statement about the past; nothing to act on

#: The two rows ``merge_after_escalation`` ORDERS -- a DISTINCT named constant,
#: and the written reason it differs from ``recovery_summary.AUDIT_ACTIONS``
#: (autonomy-act-05). The recovery row set answers "was this task attempted, and
#: how many times"; this one answers "which of these two rows is newer". An
#: ATTEMPT row is neither an escalation nor a merge, so it has no place in that
#: ordering, and widening this set to match would put rows into a comparison
#: that cannot use them. That is a deliberate difference with a reason, not the
#: silent drift this card fixed -- two UNNAMED literals disagreeing was the
#: defect, a named constant saying why is the answer.
OUTCOME_ACTIONS = ("pr_watcher.escalate", "pr_watcher.merge")

#: The SECOND door a landing comes through (autonomy-act-06). ``OUTCOME_ACTIONS``
#: sees only what the WATCHER merged, and a PR landed by
#: ``cli.py --set-status <id> done --merge`` (mfx-mrg-04), by the Actions
#: auto-merge workflow (mfx-mrg-07) or by a hand ``gh pr merge`` writes no
#: ``pr_watcher.merge`` row at all -- so a fully-delivered subject was ordered
#: "the escalation is the newer of the two rows" forever. These are the merge
#: LEDGER's rows, written by ``tools/idp/delivery_events``: an action naming the
#: task and a payload carrying ``source`` and ``task_id``.
LEDGER_ACTION_PREFIX = "change landed on main: "
LEDGER_SOURCE = "kanban_merge_ledger"

#: Which door a RECORD's landing came through. Named, because "the watcher
#: merged it" and "somebody else did and the ledger recorded it" are different
#: facts and a record that blurs them cannot be audited.
LANDED_VIA_WATCHER = "pr_watcher.merge"
LANDED_VIA_LEDGER = "merge_ledger"

#: A card in one of these is no longer anybody's work item, so a finding that
#: comes back while its card sits here has RECURRED and earns a fresh card.
TERMINAL_CARD_STATUSES = frozenset({"done", "failed"})

#: Most cards one run may seed, across all detectors, worst-first. The first
#: run on a board with a backlog must not bury the queue — a remediation queue
#: nobody can read gets ignored wholesale. The remainder arrives next cycle and
#: the report SAYS how many were deferred.
DEFAULT_MAX_CARDS_PER_RUN = 6

#: Cards land in the HITL quarantine by default. Each of the three findings is
#: a pipeline behaviour a human decides about (who owns a row; fix or delete a
#: test; why the watcher gave up), and a reflex that files dispatchable work
#: straight into the runner is the thing people switch off.
DEFAULT_SEED_STATUS = "suggested"

_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _loads(value: Any, default: Any):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Findings — the unit every detector adapter returns
# ---------------------------------------------------------------------------
class Finding(dict):
    """One thing a detector reported, with everything a card needs.

    ``subject`` is what it is about; ``fingerprint`` is what makes a later
    observation the SAME finding; ``evidence`` is the detector's own row,
    verbatim; ``derivation`` is the command that re-derives it; ``advice`` is
    what to do about it.

    ``earliest_clear_at`` is OPTIONAL: the instant before which the detector
    structurally cannot stop reporting this finding, whatever anybody does
    about it (a window over audit rows). A detector that has no such time
    leaves it None, and the recurrence rule then reads a closed card at face
    value. It is carried on the in-memory finding of the run that computed it
    and never persisted -- every run re-derives it from the rows it read.

    ``merge_after_escalation`` is OPTIONAL and carried the SAME way, for the
    same reason (autonomy-act-04): it is the ORDER of two audit rows, and a
    stored verdict about an order would go stale the moment either row's
    successor is written. Re-derived from primary data on every run.
    """

    def __init__(self, detector: str, subject: str, fingerprint: str, *,
                 title: str, priority: str, task_type: str,
                 evidence: Mapping[str, Any], derivation: str, advice: str,
                 earliest_clear_at: Any = None,
                 merge_after_escalation: Optional[Mapping[str, Any]] = None):
        super().__init__(
            detector=detector, subject=str(subject), fingerprint=str(fingerprint),
            title=title, priority=priority, task_type=task_type,
            evidence={k: _iso(v) for k, v in dict(evidence).items()},
            derivation=derivation, advice=advice,
            earliest_clear_at=_iso(earliest_clear_at) if earliest_clear_at is not None else None,
            merge_after_escalation=dict(merge_after_escalation) if merge_after_escalation else None,
        )
        self["finding_id"] = finding_ident(detector, self["subject"], self["fingerprint"])


def finding_ident(detector: str, subject: str, fingerprint: str) -> str:
    """Stable id for (detector, subject, fingerprint)."""
    raw = f"{detector}|{subject}|{fingerprint}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def fingerprint_members(fingerprint: str) -> frozenset:
    """The members of a SET-valued fingerprint, as it was stored.

    Pure string work on the fingerprint COLUMN — deliberately not a re-parse of
    the evidence blob, which is refreshed by every upsert and so describes the
    latest observation rather than the one this finding is.
    """
    return frozenset(p for p in str(fingerprint or "").split(FINGERPRINT_SEP) if p)


def reported_members(detector: str, findings: Sequence[Mapping[str, Any]]) -> frozenset:
    """Every member the current run reported, for a set-valued detector.

    Read from THIS RUN'S OWN findings — the same output ``_upsert_finding``
    writes and ``still_active`` is built from — so the survival question is
    answered from the detector's own report and never from a second derivation
    of it. Empty for a detector whose fingerprint is not a set, which is what
    makes ``survives_clearing`` a no-op there.
    """
    if detector not in SET_VALUED_FINGERPRINT_DETECTORS:
        return frozenset()
    out: set = set()
    for f in findings:
        out |= fingerprint_members(f.get("fingerprint"))
    return frozenset(out)


def survives_clearing(detector: str, fingerprint: str, reported: frozenset) -> frozenset:
    """The members of ``fingerprint`` the current run STILL reports.

    Non-empty means the finding's OWN condition has not gone away and it must
    NOT be written ``cleared`` — it merely stopped being the newest way to
    spell the condition. Empty means every member it names is gone, which is
    the honest clear and is left exactly as it was.

    A member set that has SHRUNK is not special-cased: a finding naming two
    migrations of which one has been applied still names one that has not, and
    "some of what this says is still true" is not a clear.
    """
    if detector not in SET_VALUED_FINGERPRINT_DETECTORS:
        return frozenset()
    return fingerprint_members(fingerprint) & reported


def opaque_token(hexdigest: str, length: int = 10) -> str:
    """A slice of ``hexdigest`` that is NOT all digits.

    Card ids here are machine ids and must classify as opaque, never as card
    work: ``task-det-<token>``. A token that happens to be all digits has in
    the past been parsed as a card's ``<N>`` and invented a whole project card
    (``task-<hex>-<8 digits>``), so the slice is walked until it holds a letter.
    """
    text = str(hexdigest)
    for start in range(0, max(1, len(text) - length + 1)):
        window = text[start:start + length]
        if not window.isdigit():
            return window
    return "f" + text[:length - 1]


def card_id_for(finding_id: str, revision: int = 1) -> str:
    base = f"task-det-{opaque_token(finding_id)}"
    return base if revision <= 1 else f"{base}-r{revision}"


# ---------------------------------------------------------------------------
# Detector adapters — PURE (report -> findings), so they are testable on the
# detectors' real output shapes without a board
# ---------------------------------------------------------------------------
def churn_findings(report: Mapping[str, Any]) -> List[Finding]:
    """status_churn.churn_report() -> findings. One per oscillating task."""
    out: List[Finding] = []
    window = report.get("window_hours")
    min_returns = report.get("min_returns")
    derivation = (f"python -m tools.kanban.status_churn --json "
                  f"--window-hours {window} --min-returns {min_returns}")
    for row in report.get("tasks") or []:
        task_id = str(row.get("task_id") or "").strip()
        if not task_id:
            continue
        contested = bool(row.get("contested"))
        actors = [str(a) for a in (row.get("actors") or [])]
        cycle = str(row.get("cycle") or "")
        if contested:
            advice = (
                f"CONTESTED: two writers disagree about who owns this row "
                f"({', '.join(actors) or '?'}), each legitimately. The fix is a "
                "rule about ownership at the seam (`_move_task`), never a per-move "
                "guard — every individual transition was valid. See kpr-dup-09 for "
                "the shape (pr_watcher completing a task the scheduler was demoting)."
            )
        else:
            advice = (
                f"SINGLE-WRITER RETRY LOOP: {', '.join(actors) or '?'} keeps returning "
                f"the row through `{cycle}`. A retry loop needs a BUDGET, not an "
                "ownership rule — find what it is retrying and bound it."
            )
        out.append(Finding(
            DETECTOR_STATUS_CHURN, task_id,
            fingerprint=f"{cycle}|{'contested' if contested else 'single'}",
            title=(f"{task_id} oscillating {row.get('returns')}x via {cycle}"
                   + (" — CONTESTED" if contested else "")),
            priority="high" if contested else "medium",
            task_type="fix",
            evidence=row, derivation=derivation, advice=advice,
        ))
    return out


def born_red_findings(report: Mapping[str, Any]) -> List[Finding]:
    """born_red_survey.survey() -> findings. One per born-red test file."""
    out: List[Finding] = []
    for row in report.get("findings") or []:
        if str(row.get("state")) != "born_red":
            # `broke_after_birth` is the drift reflex's half, deliberately not
            # re-reported here (rem-hyg-14).
            continue
        path = str(row.get("path") or "").strip()
        if not path:
            continue
        basis = str(row.get("red_days_basis") or "")
        red_days = row.get("red_days")
        advice = (
            f"This test file has NEVER been observed passing ({row.get('detail') or 'no detail'}). "
            f"It has been red for up to {red_days} days (basis: {basis} — "
            "`file_age_upper_bound` is how long the file has EXISTED, an upper bound; "
            "`observed_red_days` is the proven span). Confirm at its landing commit with "
            "`python tools/ci/born_red_survey.py --confirm 1`, then FIX it or DELETE it "
            "with a written reason. If you fix it, gate it in the same PR "
            "(args/ci_test_files/core.d/<task-id>.txt) so it can never be born red again. "
            "Do NOT register it in a census to make the survey quiet."
        )
        out.append(Finding(
            DETECTOR_BORN_RED, path, fingerprint="born_red",
            title=f"{path} has been red since it landed",
            priority="medium", task_type="fix",
            evidence=row,
            derivation="python tools/ci/born_red_survey.py --json",
            advice=advice,
        ))
    return out


def recovery_findings(entries: Sequence[Mapping[str, Any]],
                      window_hours: int, *,
                      rows: Optional[Sequence[Mapping[str, Any]]] = None) -> List[Finding]:
    """recovery_summary.summarize_recovery() -> findings.

    Only ``needed_a_human`` is a finding: it is the watcher's OWN verdict
    ("resume cap reached, manual intervention required"), so it needs no
    inference. ``recovered`` is the system working and ``unresolved`` is a
    verdict not yet reached — neither is a work item.

    ``rows`` are the pr_watcher audit rows the entries were collapsed FROM. When
    given, each finding carries ``merge_after_escalation`` — the ORDER of its
    subject's newest ``escalate`` and any later ``merge``, which is what decides
    whether the finding is a card or a record (autonomy-act-04). Asking the
    detector's OWN rows means the disposition can never be derived from evidence
    the verdict was not.
    """
    out: List[Finding] = []
    for entry in entries or []:
        if str(entry.get("outcome")) != "needed_a_human":
            continue
        task_id = str(entry.get("task_id") or "").strip()
        if not task_id:
            continue
        reason = str(entry.get("reason") or "").strip()
        # The newest counted attempt row is `at`; the window keeps that row
        # (and so the `needed_a_human` verdict) until at + window_hours.
        last_attempt = parse_utc_timestamp(entry.get("at"))
        earliest_clear_at = (
            last_attempt + timedelta(hours=int(window_hours)) if last_attempt else None)
        advice = (
            f"pr_watcher attempted this task {entry.get('attempts')} time(s) "
            f"({entry.get('kind') or 'resume'}) and ESCALATED. Its last recorded reason: "
            f"{reason or '(none recorded)'}. An LLM resume cannot fix this class — the "
            "branch it is asked to repair has no defect IN it (a stale branch, a "
            "host-dependent path comparison, a union-only conflict). Find the actual "
            "cause, land it by hand, and release the claim. A merge recorded AFTER the "
            "escalation is a human's merge and must not be counted as a recovery."
        )
        out.append(Finding(
            DETECTOR_RECOVERY, task_id, fingerprint="needed_a_human",
            title=f"{task_id}: pr_watcher escalated after {entry.get('attempts')} attempt(s)",
            priority="high", task_type="chore",
            evidence=dict(entry),
            derivation=(
                "python - <<'EOF'\n"
                "from tools.awareness.claims import _recovery_rows\n"
                "from tools.dashboard.recovery_summary import summarize_recovery\n"
                f"print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) "
                f"if e['task_id'] == {task_id!r}])\n"
                "EOF\n"
                f"# or: Home (/) -> Autonomous Recovery panel, {window_hours}h window"
            ),
            advice=advice,
            earliest_clear_at=earliest_clear_at,
            merge_after_escalation=(
                merge_after_escalation(rows, task_id) if rows is not None else None),
        ))
    return out


# ---------------------------------------------------------------------------
# Card disposition — a RECORD is not a work item (autonomy-act-04)
# ---------------------------------------------------------------------------
def merge_after_escalation(rows: Sequence[Mapping[str, Any]],
                           subject: str) -> Dict[str, Any]:
    """Did a ``pr_watcher.merge`` land AFTER the newest ``pr_watcher.escalate``?

    ``rows`` are ``recovery_rows()``/``watcher_outcome_rows()`` output: an
    ``action``, a JSON ``d`` payload carrying ``task_id``/``reason``, and a
    ``created_at``. TWO AUDIT ROWS AND THEIR ORDER, and nothing else — no
    elapsed time, no threshold, no inference from a board status.

    ``measurable`` is False when there is no readable escalation to order
    against, and ``superseded`` is then None — NEVER False. "I cannot tell" and
    "no, the escalation still stands" send a caller to opposite places, and
    merging them is how an escalation nobody answered goes quiet.
    """
    escalated_at: Optional[datetime] = None
    merges: List[tuple] = []
    for row in rows or []:
        record = dict(row)
        kind = str(record.get("action") or "").split(".")[-1]
        if kind not in ("escalate", "merge"):
            continue
        try:
            payload = json.loads(record.get("d") or "{}")
        except (TypeError, ValueError):
            continue
        if str(payload.get("task_id") or "") != str(subject):
            continue
        at = parse_utc_timestamp(record.get("created_at"))
        if at is None:
            continue
        if kind == "escalate":
            if escalated_at is None or at > escalated_at:
                escalated_at = at
        else:
            merges.append((at, str(payload.get("reason") or "")[:160]))

    if escalated_at is None:
        return {"measurable": False, "superseded": None, "escalated_at": None,
                "merged_at": None, "merge_reason": None,
                "reason": f"no readable pr_watcher.escalate row for {subject}"}
    later = sorted(m for m in merges if m[0] > escalated_at)
    if not later:
        return {"measurable": True, "superseded": False,
                "escalated_at": escalated_at.isoformat(), "merged_at": None,
                "merge_reason": None,
                "reason": "the escalation is the newer of the two rows"}
    return {"measurable": True, "superseded": True,
            "escalated_at": escalated_at.isoformat(),
            "merged_at": later[0][0].isoformat(),
            "merge_reason": later[0][1],
            "reason": "a pr_watcher.merge landed after the escalation"}


def ledger_landing(rows: Sequence[Mapping[str, Any]], subject: str, *,
                   escalated_at: Any) -> Dict[str, Any]:
    """Did the MERGE LEDGER record this subject landing on main AFTER its
    escalation? PURE (autonomy-act-06).

    THE COMPANION QUESTION TO ``merge_after_escalation``, ASKED OF THE OTHER
    DOOR. That function reads ``pr_watcher.merge``, which only the WATCHER
    writes; three other merge paths exist and none of them writes one. This
    reads the row the merge ledger writes for EVERY landing whatever merged it
    -- ``tools/idp/delivery_events``' ``change landed on main: <task-id>``,
    whose payload carries ``source: kanban_merge_ledger``.

    Measured on the live board 2026-09-12 over all 42 lifetime dispositioned
    recovery findings: of the 10 ``card`` verdicts, NINE were subjects already
    ``done`` with exactly one ledger row and zero ``pr_watcher.merge`` rows,
    and in all nine the landing was AFTER the escalation. Only ``xrv-cost-05``
    was genuinely still open.

    THE ORDERING DISCIPLINE IS THE SAME ONE, DELIBERATELY. A landing older than
    the newest escalation is not an answer to it -- the watcher escalated about
    something that came after -- so it reports ``landed: False``, exactly as a
    pre-escalation ``pr_watcher.merge`` does. And ``measurable`` is False with
    ``landed`` None -- NEVER False -- when there is no escalation to order
    against: "I cannot tell" and "it has not landed" send a caller to opposite
    places.

    ``event_type`` is NOT the filter. ``deployment_initiated`` is a shared
    vocabulary word any writer may use; ``source`` is what names the ledger, so
    a row from another writer is not counted as a landing.
    """
    at0 = (parse_utc_timestamp(escalated_at)
           if isinstance(escalated_at, str) else escalated_at)
    if not isinstance(at0, datetime):
        return {"measurable": False, "landed": None, "landed_at": None,
                "ledger_rows": 0,
                "reason": f"no escalation to order a landing for {subject} against"}

    landings: List[datetime] = []
    for row in rows or []:
        record = dict(row)
        if not str(record.get("action") or "").startswith(LEDGER_ACTION_PREFIX):
            continue
        try:
            payload = json.loads(record.get("d") or "{}")
        except (TypeError, ValueError):
            continue
        if str(payload.get("source") or "") != LEDGER_SOURCE:
            continue
        if str(payload.get("task_id") or "") != str(subject):
            continue
        at = parse_utc_timestamp(record.get("created_at"))
        if at is not None:
            landings.append(at)

    later = sorted(a for a in landings if a > at0)
    if not later:
        return {"measurable": True, "landed": False, "landed_at": None,
                "ledger_rows": len(landings),
                "reason": (f"the merge ledger records no landing for {subject} after "
                           f"the escalation at {at0.isoformat()}")}
    return {"measurable": True, "landed": True, "landed_at": later[0].isoformat(),
            "ledger_rows": len(landings),
            "reason": "the merge ledger recorded a landing after the escalation"}


def card_disposition(f: Mapping[str, Any], *,
                     subject_status: Optional[str],
                     landed_on_main: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """CARD or RECORD for one projected finding. PURE.

    THE OBJECTION THIS ANSWERS (autonomy-act-04). ``summarize_recovery`` is
    RIGHT that ``escalate`` outranks any later ``merge`` — counting a
    post-escalation merge as a recovery is exactly the inflation rem-hyg-16
    exists to refuse, and NOTHING here changes that verdict, its threshold or
    its window. The objection is one layer up, about SEEDING: a
    ``needed_a_human`` finding whose subject has since merged and gone terminal
    is a true statement about the past with no remaining work, and filing it as
    a DISPATCHABLE card sends a worker session against a delivered subject.
    Such a dispatch cannot go RED — there is nothing left to change.

    THE CONJUNCTION IS THE CONTROL, and both halves are primary data:
      * the subject LANDED ON MAIN after the newest ``pr_watcher.escalate``
        row for the subject (the ORDER of two audit rows), AND
      * the subject task is CLOSED on the board.
    Measured on the live board 2026-09-05, all 25 recorded ``recovery``
    findings: 15 hold both, and the merge that answered the escalation was the
    watcher's OWN (``auto-merge ok``) for 12 of the 16 subjects that merged
    after escalating — the escalation asked for a human and no human came.

    "LANDED" HAS TWO DOORS, AND READING ONLY ONE WAS WRONG 90% OF THE TIME
    (autonomy-act-06). The first half used to accept a ``pr_watcher.merge`` row
    and NOTHING ELSE. Only the watcher writes one, and three other merge paths
    exist — ``cli.py --set-status <id> done --merge`` (mfx-mrg-04), the Actions
    auto-merge workflow (mfx-mrg-07) and a hand ``gh pr merge`` — so a subject
    that landed through any of them was ordered "the escalation is the newer of
    the two rows", i.e. REAL WORK LEFT, forever. Measured over all 42 lifetime
    dispositioned recovery findings 2026-09-12: of the 10 ``card`` verdicts,
    NINE were subjects already ``done`` on the board carrying a merge-ledger
    row and zero watcher merges, and each of those nine cost an LLM session
    re-deriving "nothing outstanding". So the first half now accepts EITHER
    row, ``landed_via`` says which, and the ordering discipline and the second
    half of the conjunction are untouched. Widening what counts as LANDED
    changes what is REPORTED, never what is FILED — ``summarize_recovery``'s
    verdict, its threshold and its window are not in this function's reach.

    EVERY UNKNOWN KEEPS THE CARD. An unreadable order, a subject that is not on
    the board, a subject still in flight: each returns CARD. A card filed
    against delivered work costs a wasted dispatch; an escalation silently
    demoted to a record costs the one signal the watcher has for "I gave up".
    Those are not the same price.

    The finding is RECORDED either way — suppressing the row would delete the
    measurement, and only the CARD is at issue.
    """
    # Mirrors ``_compute_project_progress``'s notion of closed EXACTLY, by
    # importing the one declaration rather than respelling it.
    from tools.dashboard.recovery_summary import CLOSED_STATUSES

    def _card(reason: str, **extra: Any) -> Dict[str, Any]:
        return {"disposition": DISPOSITION_CARD, "reason": reason, **extra}

    if str(f.get("detector") or "") != DETECTOR_RECOVERY:
        return _card("not a recovery finding — the rule is scoped to the "
                     "watcher's own escalations")
    order = dict(f.get("merge_after_escalation") or {})
    ledger = dict(landed_on_main or {})
    if not order:
        return _card("escalation/merge order UNMEASURED: no watcher rows were read")
    if not order.get("measurable"):
        return _card(f"escalation/merge order UNMEASURABLE: {order.get('reason')}")

    if order.get("superseded"):
        landed_via, landed_at = LANDED_VIA_WATCHER, order.get("merged_at")
    elif ledger.get("landed"):
        landed_via, landed_at = LANDED_VIA_LEDGER, ledger.get("landed_at")
    else:
        # Neither door. The ledger's own account is appended when it was read,
        # so "no watcher merge" and "no landing at all" stay distinguishable.
        reason = str(order.get("reason") or "the escalation still stands")
        if ledger.get("reason"):
            reason = f"{reason}; {ledger['reason']}"
        return _card(reason)

    evidence = {"escalated_at": order.get("escalated_at"), "landed_at": landed_at,
                "landed_via": landed_via, "merged_at": order.get("merged_at")}
    if subject_status is None:
        return _card("the subject is not on the board — its status cannot be read",
                     **evidence)
    if str(subject_status) not in CLOSED_STATUSES:
        return _card(f"the subject is `{subject_status}`, not closed — work may remain",
                     subject_status=str(subject_status), **evidence)
    return {
        "disposition": DISPOSITION_RECORD,
        "reason": (f"the subject landed on main at {landed_at} (via {landed_via}) "
                   f"AFTER the escalation at {order['escalated_at']}, and it is "
                   f"`{subject_status}`: nothing is left to land"),
        "subject_status": str(subject_status),
        "merge_reason": order.get("merge_reason"),
        **evidence,
    }


# ---------------------------------------------------------------------------
# Detector runners — the three live seams. Each returns
#   {"state": findings|clean|unmeasurable|error, "reason": str,
#    "findings": [Finding], "summary": dict}
# ---------------------------------------------------------------------------
def _result(state: str, findings: Sequence[Finding] = (), *, reason: str = "",
            summary: Optional[dict] = None) -> dict:
    return {"state": state, "reason": reason, "findings": list(findings),
            "summary": dict(summary or {})}


def run_status_churn(conn, cfg: Mapping[str, Any]) -> dict:
    from tools.kanban.status_churn import (
        DEFAULT_MIN_RETURNS, DEFAULT_WINDOW_HOURS, churn_report)

    report = churn_report(
        conn,
        window_hours=int(cfg.get("window_hours") or DEFAULT_WINDOW_HOURS),
        min_returns=int(cfg.get("min_returns") or DEFAULT_MIN_RETURNS),
    )
    if not report.get("measurable"):
        return _result(RUN_UNMEASURABLE, reason=str(report.get("reason") or "unmeasurable"),
                       summary={k: v for k, v in report.items() if k != "tasks"})
    findings = churn_findings(report)
    summary = {k: v for k, v in report.items() if k != "tasks"}
    return _result(RUN_FINDINGS if findings else RUN_CLEAN, findings, summary=summary)


def end_read_txn(conn) -> None:
    """Close the implicit transaction a read opened, without writing anything.

    psycopg2 opens a transaction on the first execute and leaves it open until
    commit/rollback. The connection string sets
    ``idle_in_transaction_session_timeout`` (ICDEV_PG_IDLE_TXN_TIMEOUT_MS,
    default 30s), so a session that READS and then sits in git for 32s is
    KILLED mid-run — measured 2026-08-21: the born-red survey took 26.6s and
    passed, 32s and died with "could not receive data from server". A read
    that is finished must not hold a transaction across work that does no SQL.
    """
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 — a backend with no transaction to end
        pass


def run_born_red(conn, cfg: Mapping[str, Any]) -> dict:
    from tools.ci.born_red_survey import SurveyError, load_observations, survey

    # Read the baseline through the connection we were handed, so a test (or
    # a tenant database) is surveyed rather than whatever get_connection()
    # resolves to inside the survey.
    try:
        observations = load_observations(conn)
    except SurveyError as exc:
        return _result(RUN_UNMEASURABLE, reason=str(exc))
    # The survey does git work for tens of seconds and no SQL at all; the
    # transaction the read above opened must not sit idle across it.
    end_read_txn(conn)
    report = survey(observations=observations)
    summary = {k: v for k, v in report.items() if k != "findings"}
    if report.get("state") == "unmeasurable":
        return _result(RUN_UNMEASURABLE,
                       reason="no ungated test has ever been observed on this deployment",
                       summary=summary)
    findings = born_red_findings(report)
    return _result(RUN_FINDINGS if findings else RUN_CLEAN, findings, summary=summary)


def recovery_rows(conn, window_hours: int) -> List[dict]:
    """The pr_watcher audit rows the recovery panel and claim_verifier read.

    THE ROW SET IS ``recovery_summary.AUDIT_ACTIONS`` AND NOTHING ELSE
    (autonomy-act-05). This function used to carry a hand-written FOUR-value
    literal while the panel read the exported constant, so the panel and the
    detector answered the same question over different rows: measured on the
    live board 2026-09-09 the panel saw 103 rows over 10 tasks and this reader
    saw 66 over 9, at the same instant. A reader cannot see an attempt kind it
    does not FETCH -- ``summarize_recovery`` drops a task with zero attempts, so
    ``sbx-fld-01`` (177 ``escalate`` rows, 2 ``rebase_failed`` attempts) drew no
    finding here at all while the panel showed it.
    """
    from tools.dashboard.recovery_summary import AUDIT_ACTIONS

    pg = str(getattr(conn, "_backend", "")).startswith("postgres")
    details = "details::text" if pg else "details"
    cut = (_now() - timedelta(hours=window_hours)).isoformat()
    placeholders = ",".join(["%s"] * len(AUDIT_ACTIONS))
    return [dict(r) for r in conn.execute(
        f"SELECT action, {details} AS d, created_at FROM audit_trail "  # nosec B608
        f"WHERE action IN ({placeholders}) AND created_at >= %s "
        "ORDER BY created_at",
        (*AUDIT_ACTIONS, cut),
    ).fetchall()]


def watcher_outcome_rows(conn, *, window_hours: Optional[int] = None) -> List[dict]:
    """The two pr_watcher actions ``merge_after_escalation`` orders.

    ``window_hours=None`` is LIFETIME — what the browse surface and the survey
    read, because a finding projected days ago has its escalation outside any
    recent window and would otherwise report UNMEASURABLE. ``consume`` does NOT
    call this: it orders against the rows ``recovery_rows`` already fetched, so
    the disposition and the verdict are derived from the same evidence.

    IT READS ``OUTCOME_ACTIONS`` AND NOT ``AUDIT_ACTIONS`` (autonomy-act-05).
    That is a DELIBERATE difference with a written reason, not the drift this
    card fixed: ``merge_after_escalation`` asks which of TWO rows is newer, and
    an ATTEMPT row is neither an escalation nor a merge, so widening this set
    would put rows into an ordering that has no place for them.
    """
    pg = str(getattr(conn, "_backend", "")).startswith("postgres")
    details = "details::text" if pg else "details"
    placeholders = ",".join(["%s"] * len(OUTCOME_ACTIONS))
    sql = (f"SELECT action, {details} AS d, created_at FROM audit_trail "  # nosec B608
           f"WHERE action IN ({placeholders})")
    params: tuple = tuple(OUTCOME_ACTIONS)
    if window_hours is not None:
        sql += " AND created_at >= %s"
        params = (*params, (_now() - timedelta(hours=int(window_hours))).isoformat())
    return [dict(r) for r in conn.execute(sql + " ORDER BY created_at", params).fetchall()]


def merge_ledger_rows(conn, *, window_hours: Optional[int] = None) -> List[dict]:
    """The merge ledger's landing rows ``ledger_landing`` orders (autonomy-act-06).

    A SEPARATE READER FOR A SEPARATE ROW SET, for the same reason
    ``watcher_outcome_rows`` is separate from ``recovery_rows``: these rows
    answer "did this subject reach main", the watcher rows answer "which of
    these two watcher actions is newer", and a reader cannot see a row kind it
    does not FETCH. Filtered on the ACTION PREFIX rather than on
    ``event_type``, because ``deployment_initiated`` is a shared vocabulary
    word — ``ledger_landing`` then re-checks ``source`` in the payload, so a
    row from another writer that happened to borrow the prefix is still not
    counted as a landing.

    ``window_hours=None`` is LIFETIME, matching ``watcher_outcome_rows``: a
    finding projected days ago has its landing outside any recent window.
    """
    pg = str(getattr(conn, "_backend", "")).startswith("postgres")
    details = "details::text" if pg else "details"
    sql = (f"SELECT action, {details} AS d, created_at FROM audit_trail "  # nosec B608
           "WHERE action LIKE %s")
    params: tuple = (f"{LEDGER_ACTION_PREFIX}%",)
    if window_hours is not None:
        sql += " AND created_at >= %s"
        params = (*params, (_now() - timedelta(hours=int(window_hours))).isoformat())
    return [dict(r) for r in conn.execute(sql + " ORDER BY created_at", params).fetchall()]


def run_recovery(conn, cfg: Mapping[str, Any]) -> dict:
    from tools.dashboard.recovery_summary import summarize_recovery

    window_hours = int(cfg.get("window_hours") or 24)
    rows = recovery_rows(conn, window_hours)
    if not rows:
        # No attempt, no escalation, no merge in the window: the watcher may be
        # idle, down, or the audit writer may be bypassed. None of those is
        # "every recovery succeeded".
        return _result(RUN_UNMEASURABLE,
                       reason=f"no pr_watcher audit rows in the last {window_hours}h",
                       summary={"window_hours": window_hours, "rows": 0})
    entries = summarize_recovery(rows, limit=10_000)
    outcomes: Dict[str, int] = {}
    for e in entries:
        outcomes[e["outcome"]] = outcomes.get(e["outcome"], 0) + 1
    summary = {"window_hours": window_hours, "rows": len(rows),
               "tasks_attempted": len(entries), "outcomes": outcomes}
    findings = recovery_findings(entries, window_hours, rows=rows)
    return _result(RUN_FINDINGS if findings else RUN_CLEAN, findings, summary=summary)


def migration_drift_findings(report: Mapping[str, Any]) -> List[Finding]:
    """migration_drift.drift() -> findings. ONE finding, not one per migration.

    A deployment is either running the merged schema or it is not; three
    pending migrations are one condition with three names, and filing three
    cards would put the same repair in the queue three times. The migrations
    are carried in the evidence and named in the advice.
    """
    pending = list(report.get("pending") or [])
    if not pending:
        return []
    names = [str(p.get("name") or p.get("version")) for p in pending]
    ref = str(report.get("ref") or "origin/main")
    advice = (
        f"{len(names)} migration(s) are on {ref} and NOT applied here: "
        f"{', '.join(names)}. Until they are, any capability depending on their "
        "schema is INERT on this deployment — its code is present, its tables or "
        "columns are not, and its tests can be green the whole time. That is how "
        "autonomy-id-01 recorded nothing after it merged. Apply with "
        "`python tools/db/migrate.py --up` (preview with --dry-run) from a "
        "checkout that actually CONTAINS them: migrate.py reads the FILESYSTEM, "
        "so a checkout behind the branch reports `Pending: 0` and applies "
        "nothing — check `python tools/genesis/deployment_freshness.py` first. "
        "Applying is a deployment act and is deliberately not automated."
    )
    # The FINGERPRINT is the set of pending versions: while the same migrations
    # stay pending this is the SAME finding (seen_count rises, no second card),
    # and a different set is genuinely a new condition worth its own.
    versions = sorted(str(p.get("version")) for p in pending)
    return [Finding(
        DETECTOR_MIGRATION_DRIFT, subject=str(report.get("root") or "deployment"),
        fingerprint="|".join(versions),
        title=f"{len(names)} merged migration(s) are not applied on this deployment",
        priority="high", task_type="chore",
        evidence={k: v for k, v in report.items() if k != "applied_not_on_branch"},
        derivation="python tools/db/migration_drift.py --json",
        advice=advice,
    )]


def run_migration_drift(conn, cfg: Mapping[str, Any]) -> dict:
    from tools.db.migration_drift import CURRENT, UNMEASURABLE, drift

    # The drift check shells out to git; do not hold a read transaction across it.
    end_read_txn(conn)
    report = drift(ref=str(cfg.get("ref") or "origin/main"), conn=conn)
    summary = {k: v for k, v in report.items() if k != "pending"}
    if report.get("state") == UNMEASURABLE:
        # A fresh database, an unreadable branch, an unreachable
        # schema_migrations. UNMEASURABLE CLEARS NOTHING — a run that could not
        # look must never read as "the migrations arrived".
        return _result(RUN_UNMEASURABLE,
                       reason=str(report.get("reason") or "drift not measurable"),
                       summary=summary)
    if report.get("state") == CURRENT:
        return _result(RUN_CLEAN, summary=summary)
    findings = migration_drift_findings(report)
    return _result(RUN_FINDINGS if findings else RUN_CLEAN, findings, summary=summary)


def freshness_findings(report: Mapping[str, Any],
                       restore: Optional[Mapping[str, Any]] = None) -> List[Finding]:
    """deployment_freshness.freshness() -> findings. ONE finding per freeze.

    Filed only for a `blocked` deployment the restore tier did NOT clear — so
    the card carries the act's own refusal (a human edit, a foreign file, an
    unreadable board) beside the guard's verdict, and says what a human does
    about each. The FINGERPRINT is the guard's reason plus the files it names:
    the same freeze on the same files is the SAME finding however many cycles
    it persists (seen_count rises, no second card); a different file set is a
    new condition.
    """
    if str(report.get("state") or "") != "blocked":
        return []
    conflicts = sorted(str(c) for c in (report.get("conflicts") or []))
    reason = str(report.get("reason") or "")
    root = str(report.get("root") or "deployment")
    ref = str(report.get("ref") or "origin/main")
    behind = report.get("behind_by")
    outcome = str((restore or {}).get("outcome") or "not_attempted")
    why = str((restore or {}).get("reason") or "")
    advice = (
        f"This checkout is {behind} commit(s) behind {ref} and has STOPPED updating: "
        f"the update guard (code_reload.pull_if_safe) refuses — {reason}"
        f"{' — on ' + ', '.join(conflicts) if conflicts else ''}. Every merged fix is "
        "absent from the services running from it while every board, PR and CI signal "
        "stays green (autonomy-dep-03 measured the cost; autonomy-dep-04 the recurrence). "
        f"The restore tier (restore_acts.restore_auto_managed_file) reports `{outcome}`"
        f"{': ' + why if why else ''}. "
        "It restores a file ONLY when the file is an enumerated auto-managed one AND its "
        "writer, re-run over HEAD, reproduces the local diff — so if it refused, the "
        "local change is NOT regenerable: commit it (`git add -p` the human edit, push "
        "it on a branch) or, if it is truly disposable, `git checkout -- <file>`; never "
        "widen the guard to pull over it. Re-derive with "
        f"`python tools/genesis/deployment_freshness.py --root {root} --json` and "
        f"`python tools/awareness/restore_acts.py --plan --root {root}`."
    )
    evidence: Dict[str, Any] = {k: v for k, v in report.items()}
    if restore is not None:
        evidence["restore"] = {k: restore.get(k) for k in
                               ("act", "target", "outcome", "proven", "reason", "audit_id",
                                "confirmed")}
    return [Finding(
        DETECTOR_DEPLOYMENT_FRESHNESS, subject=root,
        fingerprint=f"{reason}|{'|'.join(conflicts)}",
        title=f"deployment frozen {behind} commit(s) behind {ref}: {reason}",
        priority="high", task_type="fix",
        evidence=evidence,
        derivation=f"python tools/genesis/deployment_freshness.py --root {root} --json",
        advice=advice,
    )]


def _restore_blocked_files(report: Mapping[str, Any], *, dry_run: bool) -> Dict[str, Any]:
    """Try the restore tier on every enumerated auto-managed file the guard
    names. ``outcome`` is ``applied`` only if EVERY attempted act applied; a
    file no writer regenerates is reported as ``not_attempted`` for that file."""
    from tools.awareness import restore_acts as ra

    root = report.get("root")
    attempts: List[Dict[str, Any]] = []
    for path in sorted(str(c) for c in (report.get("conflicts") or [])):
        rel = ra._rel(path)
        if rel not in ra.AUTO_MANAGED_FILES:
            attempts.append({"act": "restore_auto_managed_file", "target": rel,
                             "outcome": "not_attempted",
                             "reason": "not an enumerated auto-managed file"})
            continue
        attempts.append(ra.perform("restore_auto_managed_file", rel,
                                   root=Path(root) if root else None, dry_run=dry_run))
    if not attempts:
        return {"outcome": "not_attempted", "reason": "the guard named no file", "acts": []}
    first = attempts[0]
    applied = all(a.get("outcome") == ra.APPLIED for a in attempts)
    summary = {"outcome": ra.APPLIED if applied else str(first.get("outcome")),
               "reason": "" if applied else str(first.get("reason") or ""),
               "acts": attempts}
    for key in ("act", "target", "proven", "audit_id", "confirmed"):
        if key in first:
            summary[key] = first[key]
    return summary


def run_deployment_freshness(conn, cfg: Mapping[str, Any]) -> dict:
    """Ask the SAME reporter dep-03 ships; on `blocked`, CONSUME the restore
    tier before filing anything.

    The order is the card's whole point: a report nobody reads is the defect
    dep-03 itself named, and a card for a freeze that a proven, audited act
    could have cleared is a human doing a machine's work. So: measure; if
    blocked, let the restore tier prove/audit/apply/confirm; re-measure; file a
    card ONLY for what is still blocked, carrying the act's refusal.
    """
    from tools.genesis import deployment_freshness as dfm

    # The reporter shells out to git (fetch included); hold no read txn across it.
    end_read_txn(conn)
    root = str(cfg.get("root")) if cfg.get("root") else None
    ref = str(cfg.get("ref") or "origin/main")
    dry_run = bool(cfg.get("dry_run"))
    report = dfm.freshness(root=root, ref=ref)
    state = str(report.get("state") or "")
    summary: Dict[str, Any] = {k: report.get(k) for k in ("state", "behind_by", "reason",
                                                          "conflicts", "root", "ref")}
    if state == dfm.UNMEASURABLE:
        # UNMEASURABLE CLEARS NOTHING: an unreachable remote is not a current
        # checkout, and a run that could not look must not clear a live freeze.
        return _result(RUN_UNMEASURABLE,
                       reason=str(report.get("reason") or "freshness not measurable"),
                       summary=summary)
    if state != dfm.BLOCKED:
        return _result(RUN_CLEAN, summary=summary)

    restore: Optional[Dict[str, Any]] = None
    if cfg.get("restore", True) is not False:
        restore = _restore_blocked_files(report, dry_run=dry_run)
        summary["restore"] = {k: restore.get(k) for k in ("outcome", "reason", "target")}
        if restore.get("outcome") == "applied":
            after = dfm.freshness(root=root, ref=ref)
            summary["after"] = {k: after.get(k) for k in ("state", "behind_by", "reason")}
            if str(after.get("state") or "") != dfm.BLOCKED:
                # The freeze is CLEARED — measured again, not assumed from the
                # act's own confirm. A clean run is what clears the projection.
                return _result(RUN_CLEAN, summary=summary)
            report = after
    else:
        summary["restore"] = {"outcome": "disabled", "reason": "detectors.deployment_"
                              "freshness.restore is false", "target": None}
    findings = freshness_findings(report, restore)
    return _result(RUN_FINDINGS if findings else RUN_CLEAN, findings, summary=summary)


#: Cheap, SQL-only detectors first; the ones that leave the database for tens
#: of seconds last, with everything before them already committed.
DEFAULT_RUNNERS: Dict[str, Callable[[Any, Mapping[str, Any]], dict]] = {
    DETECTOR_STATUS_CHURN: run_status_churn,
    DETECTOR_RECOVERY: run_recovery,
    DETECTOR_BORN_RED: run_born_red,
    DETECTOR_MIGRATION_DRIFT: run_migration_drift,
    DETECTOR_DEPLOYMENT_FRESHNESS: run_deployment_freshness,
}

#: One line per detector, for the card: what it measures and where it came from.
DETECTOR_BLURB = {
    DETECTOR_STATUS_CHURN: ("status_churn (kpr-watch-11) — a task whose status RETURNS "
                            "(A -> B -> A) ten or more times in the window: two writers "
                            "taking turns on one row, or one writer in a retry loop."),
    DETECTOR_BORN_RED: ("born_red_survey (rem-hyg-14) — an ungated test file every "
                        "recorded observation of which has been a failure. The drift "
                        "reflex can only see a file FALL; this sees one that never stood."),
    DETECTOR_RECOVERY: ("recovery_summary (rem-hyg-16) — pr_watcher audit rows collapsed "
                        "to ONE outcome per task. `escalate` is the watcher's own "
                        "'manual intervention required' and outranks a later merge."),
    DETECTOR_MIGRATION_DRIFT: (
        "migration_drift (autonomy-dep-01) — a migration that is on the default "
        "branch and NOT applied to this deployment. Its capability is inert here: "
        "the code merged, the schema did not, and every test stays green."),
    DETECTOR_DEPLOYMENT_FRESHNESS: (
        "deployment_freshness (autonomy-dep-03/04) — this checkout is behind the "
        "default branch and the update guard REFUSES to pull, so every merged fix "
        "is absent from the services running here. The restore tier was asked "
        "first (restore_acts.restore_auto_managed_file); this card exists because "
        "it could not prove the local change regenerable."),
}


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------
def tables_present(conn) -> bool:
    from tools.db.storage import table_exists
    return table_exists(conn, FINDINGS_TABLE) and table_exists(conn, RUNS_TABLE)


def _load_existing(conn, detector: str) -> Dict[str, dict]:
    rows = conn.execute(
        f"SELECT finding_id, status, seen_count, card_count, task_id, first_seen_at "  # nosec B608
        f"FROM {FINDINGS_TABLE} WHERE detector = %s",
        (detector,),
    ).fetchall()
    return {str(dict(r)["finding_id"]): dict(r) for r in rows}


def _upsert_finding(conn, f: Finding, now_iso: str) -> None:
    conn.execute(
        f"INSERT INTO {FINDINGS_TABLE} (finding_id, detector, subject, fingerprint, "  # nosec B608
        "title, priority, evidence_json, derivation, status, seen_count, card_count, "
        "first_seen_at, last_seen_at, cleared_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 0, %s, %s, NULL) "
        "ON CONFLICT (finding_id) DO UPDATE SET "
        f"seen_count = {FINDINGS_TABLE}.seen_count + 1, "
        "last_seen_at = EXCLUDED.last_seen_at, "
        "status = EXCLUDED.status, "
        "cleared_at = NULL, "
        "title = EXCLUDED.title, "
        "priority = EXCLUDED.priority, "
        "evidence_json = EXCLUDED.evidence_json, "
        "derivation = EXCLUDED.derivation",
        (f["finding_id"], f["detector"], f["subject"], f["fingerprint"], f["title"],
         f["priority"], _dumps(f["evidence"]), f["derivation"], FINDING_ACTIVE,
         now_iso, now_iso),
    )


def _clear_missing(conn, detector: str, still_active: Sequence[str], now_iso: str,
                   reported: frozenset = frozenset()) -> Dict[str, Any]:
    """A MEASURABLE run no longer reports these findings — clear them.

    ``cleared`` still means what it has always meant, and is still written on
    the first measurable run that stops reporting a finding. What changed
    (autonomy-dep-05) is that for a SET-VALUED fingerprint, "the run stopped
    reporting THIS finding_id" is not the same question as "the run stopped
    reporting what this finding SAYS" — a set that GROWS gets a new
    finding_id while every member of the old one is still reported. Such a
    finding is HELD ACTIVE and the members still reported are named; it is
    cleared on the run where NONE of them is.

    It is held ACTIVE rather than moved to a new terminal status on purpose: a
    seeded card's acceptance criterion reads ``status=cleared`` verbatim, so a
    third status would make every already-filed card unclosable — and the card
    that asked for this fix says in terms that an unclearable finding is worse
    than a false clear.

    Returns ``{"cleared": n, "held": [...]}``; ``held`` names each finding kept
    and the members that kept it, because a suppression nobody can see is a
    suppression nobody can audit.
    """
    rows = conn.execute(
        f"SELECT finding_id, fingerprint FROM {FINDINGS_TABLE} "  # nosec B608
        "WHERE detector = %s AND status = %s",
        (detector, FINDING_ACTIVE),
    ).fetchall()
    keep = set(still_active)
    cleared: List[str] = []
    held: List[Dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        fid = str(r["finding_id"])
        if fid in keep:
            continue
        survivors = survives_clearing(detector, r.get("fingerprint"), reported)
        if survivors:
            held.append({"detector": detector, "finding_id": fid,
                         "fingerprint": str(r.get("fingerprint") or ""),
                         "still_reported": sorted(survivors)})
            continue
        cleared.append(fid)
    for fid in cleared:
        conn.execute(
            f"UPDATE {FINDINGS_TABLE} SET status = %s, cleared_at = %s WHERE finding_id = %s",  # nosec B608
            (FINDING_CLEARED, now_iso, fid),
        )
    if held:
        logger.warning(
            "detector_findings: %s — %d superseded finding(s) HELD ACTIVE; the run no "
            "longer reports their finding_id but still reports what they name: %s",
            detector, len(held),
            "; ".join(f"{h['finding_id']} ({', '.join(h['still_reported'])})" for h in held))
    return {"cleared": len(cleared), "held": held}


def _record_run(conn, detector: str, state: str, reason: str, findings: Optional[int],
                summary: dict, now_iso: str) -> None:
    measurable = 1 if state in (RUN_FINDINGS, RUN_CLEAN) else 0
    conn.execute(
        f"INSERT INTO {RUNS_TABLE} (detector, runs, measurable_runs, last_state, "  # nosec B608
        "last_reason, last_findings, last_summary_json, last_run_at, last_measurable_at) "
        "VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (detector) DO UPDATE SET "
        f"runs = {RUNS_TABLE}.runs + 1, "
        f"measurable_runs = {RUNS_TABLE}.measurable_runs + EXCLUDED.measurable_runs, "
        "last_state = EXCLUDED.last_state, "
        "last_reason = EXCLUDED.last_reason, "
        "last_findings = EXCLUDED.last_findings, "
        "last_summary_json = EXCLUDED.last_summary_json, "
        "last_run_at = EXCLUDED.last_run_at, "
        f"last_measurable_at = COALESCE(EXCLUDED.last_measurable_at, {RUNS_TABLE}.last_measurable_at)",
        (detector, measurable, state, reason[:500], findings, _dumps(summary), now_iso,
         now_iso if measurable else None),
    )


def _before_earliest_clear(f: Mapping[str, Any], now_dt: datetime) -> bool:
    """True while this finding COULD NOT yet have left its detector's report.

    A finding with no ``earliest_clear_at`` (born_red, status_churn) returns
    False and keeps the plain rule; an unreadable stamp is the same as none,
    so a malformed value can never hold a finding forever.
    """
    earliest = parse_utc_timestamp(f.get("earliest_clear_at"))
    if earliest is None:
        return False
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    return now_dt < earliest


def _task_status(conn, task_id: Optional[str]) -> Optional[str]:
    """One task's board status, or None when it is not on the board at all.

    Read for a finding's CARD (has it been closed?) and for a recovery
    finding's SUBJECT (has the work landed?) — the same question of the same
    column, so it is one function.
    """
    if not task_id:
        return None
    row = conn.execute("SELECT status FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone()
    return str(dict(row)["status"]) if row else None


def _ledger_rows_safe(conn) -> List[dict]:
    """``merge_ledger_rows`` with an unreadable ledger degraded to NO EVIDENCE.

    ``[]`` here means "the ledger records no landing", which KEEPS the card —
    the safe direction, and the same one every other unknown takes in
    ``card_disposition``. The read is logged so a silently empty ledger is not
    mistaken for a board on which nothing has ever merged.
    """
    try:
        return merge_ledger_rows(conn)
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger KEEPS the card
        logger.warning("detector_findings: merge ledger unreadable: %s", exc)
        return []


def _disposition(conn, f: Mapping[str, Any], *,
                 ledger_rows: Optional[Sequence[Mapping[str, Any]]] = None
                 ) -> Dict[str, Any]:
    """``card_disposition`` with the reads it needs (autonomy-act-04/-06).

    ``ledger_rows`` is hoisted by the caller when there are many findings to
    disposition — one query for the run rather than one per finding.
    """
    if str(f.get("detector") or "") != DETECTOR_RECOVERY:
        return card_disposition(f, subject_status=None)
    try:
        subject_status = _task_status(conn, str(f.get("subject") or ""))
    except Exception as exc:  # noqa: BLE001 — an unreadable board KEEPS the card
        logger.warning("detector_findings: subject status unreadable for %s: %s",
                       f.get("subject"), exc)
        return {"disposition": DISPOSITION_CARD,
                "reason": f"the board could not be read: {type(exc).__name__}"}
    rows = _ledger_rows_safe(conn) if ledger_rows is None else ledger_rows
    order = dict(f.get("merge_after_escalation") or {})
    landed = ledger_landing(rows, str(f.get("subject") or ""),
                            escalated_at=order.get("escalated_at"))
    return card_disposition(f, subject_status=subject_status, landed_on_main=landed)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def render_description(f: Finding, *, seen_count: int, first_seen_at: Optional[str],
                       revision: int) -> str:
    evidence = json.dumps(f["evidence"], indent=2, default=str, sort_keys=True)
    # A card's own account of when it clears must be TRUE of the detector that
    # filed it (autonomy-dep-05). For a set-valued fingerprint the plain rule is
    # wrong in the direction that matters: the finding_id changes whenever the
    # set changes, and the card's acceptance criterion reads `cleared`.
    lifecycle = (
        f" This finding's fingerprint is a SET (`{f['fingerprint']}`): a member "
        "arriving or leaving makes a NEW finding_id, so this row is cleared only "
        "on a measurable run that reports NONE of the members it names -- never "
        "merely because the set it belongs to changed shape."
        if f["detector"] in SET_VALUED_FINGERPRINT_DETECTORS else "")
    recurrence = ""
    if revision > 1:
        recurrence = (
            f"\n**RECURRENCE.** This is card #{revision} for the same finding: it was "
            "filed before, that card was closed, and the detector reports it again. "
            "Whatever closed the last card did not hold.\n"
        )
    return (
        f"**Detector:** {DETECTOR_BLURB.get(f['detector'], f['detector'])}\n\n"
        f"**Finding:** `{f['finding_id']}` — subject `{f['subject']}`, seen "
        f"{seen_count}x since {first_seen_at or 'this run'}. One projection row in "
        f"`{FINDINGS_TABLE}`; the reflex bumps `seen_count` on every cycle that still "
        "reports it and marks it `cleared` on the first MEASURABLE cycle that does not."
        f"{lifecycle}\n"
        f"{recurrence}\n"
        "**Derivation — re-derive it yourself before acting:**\n"
        "```\n"
        f"{f['derivation']}\n"
        "```\n\n"
        "**Evidence (the detector's own row, verbatim):**\n"
        "```json\n"
        f"{evidence}\n"
        "```\n\n"
        f"**What to do:** {f['advice']}\n\n"
        "**Do NOT** edit the detector, its threshold or its window so the finding goes "
        "away — an actuator never edits what it verifies. If the detector is wrong, "
        "that is a separate card against the detector, with the survey that proves it.\n\n"
        "**Done when:** the derivation above no longer reports this subject. Then "
        "`python -m tools.kanban.detector_findings --list --status cleared` shows this "
        "finding cleared on the next reflex cycle (filed by detector_findings_reflex)."
    )


def build_spec(f: Finding, *, seen_count: int, first_seen_at: Optional[str],
               revision: int, seed_status: str) -> dict:
    task_id = card_id_for(f["finding_id"], revision)
    prefix = {
        DETECTOR_STATUS_CHURN: "[CHURN]",
        DETECTOR_BORN_RED: "[BORN-RED]",
        DETECTOR_RECOVERY: "[NEEDED-A-HUMAN]",
    }.get(f["detector"], "[DETECTOR]")
    return {
        "id": task_id,
        "title": f"{prefix} {f['title']}"[:255],
        "description": render_description(
            f, seen_count=seen_count, first_seen_at=first_seen_at, revision=revision),
        "task_type": f["task_type"],
        "priority": f["priority"],
        "status": seed_status,
        "dispatch_source": "detector_findings_reflex",
        # The seeder's own dedupe: a retried batch cannot file this twice.
        "idempotency_key": f"detector-finding-{f['finding_id']}-r{revision}",
        "acceptance_criteria": (
            f"`{f['derivation'].splitlines()[0]}` no longer reports `{f['subject']}`; "
            f"detector_findings row `{f['finding_id']}` reads status=cleared."
        ),
    }


def _seed_cards(specs: List[dict]) -> List[str]:
    from tools.kanban.task_factory import create_tasks
    return create_tasks(specs)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
def consume(config: Optional[Mapping[str, Any]] = None, *, conn=None, seed: bool = True,
            runners: Optional[Mapping[str, Callable[[Any, Mapping[str, Any]], dict]]] = None,
            now: Optional[datetime] = None) -> dict:
    """Run every detector, project its findings, seed a card per NEW finding.

    ``seed=False`` is a true dry run: detectors run, NOTHING is written — no
    projection row, no run row, no card.

    ``runners`` is injectable so the projection, dedupe and seeding logic can
    be tested without the three live detectors.
    """
    cfg = dict(config or {})
    runners = dict(runners or DEFAULT_RUNNERS)
    detector_cfg = cfg.get("detectors") or {}
    max_cards = int(cfg.get("max_cards_per_run") or DEFAULT_MAX_CARDS_PER_RUN)
    seed_status = str(cfg.get("seed_status") or DEFAULT_SEED_STATUS)
    now_dt = now or _now()
    now_iso = now_dt.isoformat()
    started = time.monotonic()

    report: Dict[str, Any] = {
        "generated_at": now_iso,
        "dry_run": not seed,
        "seed_status": seed_status,
        "max_cards_per_run": max_cards,
        "detectors": {},
        "findings_seen": 0,
        "findings_new": 0,
        "findings_recurring": 0,
        "findings_held_closed_early": 0,
        "findings_record_only": 0,
        "findings_cleared": 0,
        # Findings the run no longer reports BY finding_id but whose set-valued
        # fingerprint it still reports member-for-member (autonomy-dep-05). Held
        # ACTIVE, never cleared, and SURFACED here — a superseded-but-still-true
        # finding that vanished silently is how a card came to be verifiable
        # complete 71 minutes before its migration was applied.
        "findings_held_still_true": 0,
        "held_still_true": [],
        # Every finding filed as a RECORD instead of a card, with the two audit
        # stamps that decided it. The finding is projected either way; this is
        # where it is SURFACED, because a suppression nobody can see is a
        # suppression nobody can audit.
        "records": [],
        "cards_seeded": [],
        "cards_deferred": 0,
        "errors": [],
    }

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        if not tables_present(conn):
            report["state"] = "unmigrated"
            report["errors"].append(
                f"{FINDINGS_TABLE}/{RUNS_TABLE} absent — migration {MIGRATION} has not "
                "run on this database; nothing projected, nothing seeded")
            return report
        end_read_txn(conn)

        candidates: List[dict] = []   # {"finding", "existing", "revision"}
        for name, runner in runners.items():
            d_cfg = dict(detector_cfg.get(name) or {})
            # A detector that CONSUMES the restore tier (deployment_freshness)
            # must know a dry run is a dry run: `seed=False` writes no row and
            # no card, and must act on nothing either.
            d_cfg.setdefault("dry_run", not seed)
            t0 = time.monotonic()
            try:
                res = runner(conn, d_cfg)
            except Exception as exc:  # noqa: BLE001 — one broken detector must not stop the others
                logger.warning("detector_findings: %s raised: %s", name, exc)
                res = _result(RUN_ERROR, reason=f"{type(exc).__name__}: {exc}")
            state = res["state"]
            findings: List[Finding] = list(res.get("findings") or [])
            entry = {
                "state": state,
                "reason": res.get("reason") or "",
                # None, never 0, when the run could not measure.
                "findings": len(findings) if state in (RUN_FINDINGS, RUN_CLEAN) else None,
                "new": 0, "recurring": 0, "held_closed_early": 0,
                "record_only": 0, "cleared": 0, "held_still_true": 0,
                "summary": res.get("summary") or {},
                "elapsed_seconds": round(time.monotonic() - t0, 1),
            }
            report["detectors"][name] = entry
            if state == RUN_ERROR:
                report["errors"].append(f"{name}: {entry['reason']}")

            if state in (RUN_FINDINGS, RUN_CLEAN):
                report["findings_seen"] += len(findings)
                existing = _load_existing(conn, name)
                for f in findings:
                    prior = existing.get(f["finding_id"])
                    revision = None
                    # A RECORD is filed, never dispatched (autonomy-act-04).
                    # Asked FIRST, so a delivered subject is never counted as
                    # new or recurring work on ANY run — the projection row
                    # carries no task_id, so without this the "still owed its
                    # first card" branch would re-file it every cycle.
                    disp = _disposition(conn, f)
                    if disp["disposition"] == DISPOSITION_RECORD:
                        entry["record_only"] += 1
                        report["records"].append({
                            "detector": name, "finding_id": f["finding_id"],
                            "subject": f["subject"], "title": f["title"],
                            **{k: v for k, v in disp.items() if k != "disposition"},
                        })
                    elif prior is None:
                        entry["new"] += 1
                        revision = 1
                    elif not prior.get("task_id"):
                        # Projected on an earlier run and DEFERRED by the cap (or
                        # the seeder failed): still owed its first card.
                        entry["new"] += 1
                        revision = int(prior.get("card_count") or 0) + 1
                    else:
                        card_state = _task_status(conn, prior.get("task_id"))
                        was_cleared = str(prior.get("status")) == FINDING_CLEARED
                        card_closed = card_state in TERMINAL_CARD_STATUSES
                        # A terminal card while the detector structurally cannot
                        # have stopped reporting yet is a card closed EARLY, not
                        # a fix that did not hold: hold the finding on its card.
                        closed_early = (
                            card_closed and not was_cleared
                            and _before_earliest_clear(f, now_dt))
                        if closed_early:
                            entry["held_closed_early"] += 1
                        elif was_cleared or card_closed:
                            entry["recurring"] += 1
                            revision = int(prior.get("card_count") or 0) + 1
                    if seed:
                        _upsert_finding(conn, f, now_iso)
                    if revision is not None:
                        candidates.append({
                            "finding": f, "revision": revision,
                            "seen_count": (int(prior.get("seen_count") or 0) + 1) if prior else 1,
                            "first_seen_at": _iso(prior.get("first_seen_at")) if prior else now_iso,
                        })
                if seed:
                    outcome = _clear_missing(
                        conn, name, [f["finding_id"] for f in findings], now_iso,
                        reported_members(name, findings))
                    entry["cleared"] = outcome["cleared"]
                    entry["held_still_true"] = len(outcome["held"])
                    report["findings_cleared"] += entry["cleared"]
                    report["findings_held_still_true"] += entry["held_still_true"]
                    report["held_still_true"].extend(outcome["held"])
            report["findings_new"] += entry["new"]
            report["findings_recurring"] += entry["recurring"]
            report["findings_held_closed_early"] += entry["held_closed_early"]
            report["findings_record_only"] += entry["record_only"]

            if seed:
                _record_run(conn, name, state, entry["reason"], entry["findings"],
                            entry["summary"], now_iso)
                # One detector's projection is durable before the next detector
                # runs — so a slow detector that gets the session killed cannot
                # take a finished detector's rows down with it.
                conn.commit()
            else:
                end_read_txn(conn)

        # Worst first, then oldest-known first. Bounded, and the bound is REPORTED.
        candidates.sort(key=lambda c: (
            _PRIORITY_RANK.get(c["finding"]["priority"], 9),
            -c["revision"], str(c["first_seen_at"] or ""), c["finding"]["finding_id"]))
        if len(candidates) > max_cards:
            report["cards_deferred"] = len(candidates) - max_cards
            logger.warning(
                "detector_findings: seeding %d card(s) and DEFERRING %d to the next run "
                "(cap=%d); the findings are projected and still exist",
                max_cards, report["cards_deferred"], max_cards)
            candidates = candidates[:max_cards]

        specs = [build_spec(c["finding"], seen_count=c["seen_count"],
                            first_seen_at=c["first_seen_at"], revision=c["revision"],
                            seed_status=seed_status) for c in candidates]
        report["cards_planned"] = [s["id"] for s in specs]

        if seed and specs:
            conn.commit()   # the projection is durable before the seeder opens its own connection
            created: List[str] = []
            try:
                created = list(_seed_cards(specs) or [])
            except Exception as exc:  # noqa: BLE001 — report, never hide
                logger.warning("detector_findings: create_tasks failed: %s", exc)
                report["errors"].append(f"create_tasks: {type(exc).__name__}: {exc}")
            report["cards_seeded"] = created
            by_id = {s["id"]: c for s, c in zip(specs, candidates)}
            for task_id in created:
                c = by_id.get(task_id)
                if not c:
                    continue
                conn.execute(
                    f"UPDATE {FINDINGS_TABLE} SET task_id = %s, card_count = %s "  # nosec B608
                    "WHERE finding_id = %s",
                    (task_id, c["revision"], c["finding"]["finding_id"]),
                )
        if seed:
            conn.commit()
        report["state"] = "ok" if not report["errors"] else "partial"
        return report
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        if own_conn:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------
def list_findings(conn=None, *, detector: Optional[str] = None,
                  status: Optional[str] = None, limit: int = 200) -> List[dict]:
    own = conn is None
    if own:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        if not tables_present(conn):
            return []
        clauses, params = [], []
        if detector:
            clauses.append("detector = %s")
            params.append(detector)
        if status:
            clauses.append("status = %s")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT finding_id, detector, subject, fingerprint, title, priority, status, "  # nosec B608
            f"seen_count, card_count, task_id, first_seen_at, last_seen_at, cleared_at, "
            f"evidence_json, derivation FROM {FINDINGS_TABLE}{where} "
            f"ORDER BY status, last_seen_at DESC LIMIT %s",
            tuple(params) + (int(limit),),
        ).fetchall()
        out = []
        for r in rows:
            rec = {k: _iso(v) for k, v in dict(r).items()}
            rec["evidence"] = _loads(rec.pop("evidence_json", None), {})
            out.append(rec)
        return out
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def dispositions(conn=None, *, window_hours: Optional[int] = None,
                 status: Optional[str] = None) -> Dict[str, Any]:
    """Every projected ``recovery`` finding, with its disposition RE-DERIVED.

    The browse surface for what ``consume`` filed as a record rather than a
    card — and the SURVEY, so a fire rate is measured with the SHIPPED
    predicate (``merge_after_escalation`` and ``card_disposition`` themselves)
    and never with a second copy of the rule.

    ``window_hours=None`` reads pr_watcher rows LIFETIME, which is what a
    survey over historical findings needs; ``consume`` orders against its own
    24h window. The predicate is the same function either way.

    ``measured`` is False when nothing could be read: no projection, or no
    escalate/merge rows at all. UNMEASURABLE IS NOT "no records".
    """
    own = conn is None
    if own:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        if not tables_present(conn):
            return {"state": "unmigrated", "measured": False, "findings": [],
                    "record_only": None, "card": None}
        findings = list_findings(conn, detector=DETECTOR_RECOVERY, status=status,
                                 limit=10_000)
        rows = watcher_outcome_rows(conn, window_hours=window_hours)
        ledger = _ledger_rows_safe(conn)
        if not findings:
            return {"state": "no_findings", "measured": False, "findings": [],
                    "record_only": None, "card": None, "watcher_rows": len(rows),
                    "ledger_rows": len(ledger)}
        out: List[Dict[str, Any]] = []
        for rec in findings:
            subject = str(rec.get("subject") or "")
            probe = dict(rec)
            order = merge_after_escalation(rows, subject)
            probe["merge_after_escalation"] = order
            disp = card_disposition(
                probe, subject_status=_task_status(conn, subject),
                landed_on_main=ledger_landing(ledger, subject,
                                              escalated_at=order.get("escalated_at")))
            out.append({
                "finding_id": rec.get("finding_id"), "subject": subject,
                "finding_status": rec.get("status"), "task_id": rec.get("task_id"),
                "seen_count": rec.get("seen_count"), "card_count": rec.get("card_count"),
                **disp,
            })
        record_only = sum(1 for r in out if r["disposition"] == DISPOSITION_RECORD)
        return {
            "state": "ok", "measured": True, "window_hours": window_hours,
            "watcher_rows": len(rows), "ledger_rows": len(ledger), "findings": out,
            "record_only": record_only, "card": len(out) - record_only,
            # `pct if total else 100.0` here would breach args/perfect_score_gate.yaml.
            "record_only_pct": (round(100.0 * record_only / len(out), 1) if out else None),
        }
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def stats(conn=None) -> dict:
    """Per-detector denominator. ``state`` is never a clean zero by accident."""
    own = conn is None
    if own:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        if not tables_present(conn):
            return {"state": "unmigrated", "detectors": {}}
        runs = {str(dict(r)["detector"]): {k: _iso(v) for k, v in dict(r).items()}
                for r in conn.execute(f"SELECT * FROM {RUNS_TABLE}").fetchall()}  # nosec B608
        counts: Dict[str, Dict[str, int]] = {}
        for r in conn.execute(
            f"SELECT detector, status, COUNT(*) AS n FROM {FINDINGS_TABLE} "  # nosec B608
            "GROUP BY detector, status"
        ).fetchall():
            rec = dict(r)
            counts.setdefault(str(rec["detector"]), {})[str(rec["status"])] = int(rec["n"])
        detectors = {}
        for name in DETECTORS:
            run = runs.get(name)
            run_summary = None
            if run:
                run_summary = dict(run)
                run_summary["last_summary"] = _loads(run_summary.pop("last_summary_json", None), {})
            detectors[name] = {
                # never_ran | unmeasurable | clean | findings | error
                "state": ("never_ran" if not run else
                          str(run.get("last_state") or "unmeasurable")),
                "active": counts.get(name, {}).get(FINDING_ACTIVE, 0) if run else None,
                "cleared": counts.get(name, {}).get(FINDING_CLEARED, 0) if run else None,
                "run": run_summary,
            }
        return {"state": "ok" if runs else "never_ran", "detectors": detectors}
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def render(report: dict) -> str:
    if report.get("state") == "unmigrated":
        return "UNMIGRATED — " + "; ".join(report.get("errors") or [])
    lines = [f"detector_findings  {'DRY RUN' if report.get('dry_run') else 'run'}  "
             f"{report.get('generated_at')}"]
    for name, d in (report.get("detectors") or {}).items():
        n = d["findings"]
        lines.append(
            f"  {name:<14} {d['state']:<12} findings={'?' if n is None else n:<4} "
            f"new={d['new']} recurring={d['recurring']} record={d.get('record_only', 0)} "
            f"cleared={d['cleared']} held={d.get('held_still_true', 0)} "
            f"({d['elapsed_seconds']}s)" + (f"  — {d['reason']}" if d.get("reason") else ""))
    lines.append(
        f"  seen={report.get('findings_seen')} new={report.get('findings_new')} "
        f"recurring={report.get('findings_recurring')} "
        f"record_only={report.get('findings_record_only')} "
        f"cleared={report.get('findings_cleared')} "
        f"held_still_true={report.get('findings_held_still_true')}")
    for h in report.get("held_still_true") or []:
        lines.append(
            f"  HELD {h['finding_id']} ({h['detector']}) — superseded, NOT cleared: "
            f"still reported {', '.join(h['still_reported'])}")
    for rec in report.get("records") or []:
        lines.append(f"  RECORD {rec['subject']} — {rec['reason']}")
    planned = report.get("cards_planned") or []
    if report.get("dry_run"):
        lines.append(f"  would seed {len(planned)} card(s): {', '.join(planned) or '-'}")
    else:
        lines.append(f"  seeded {len(report.get('cards_seeded') or [])} card(s): "
                     f"{', '.join(report.get('cards_seeded') or []) or '-'}")
    if report.get("cards_deferred"):
        lines.append(f"  DEFERRED {report['cards_deferred']} card(s) to the next run (cap)")
    for err in report.get("errors") or []:
        lines.append(f"  ERROR {err}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="run the detectors; write NOTHING (no rows, no cards)")
    parser.add_argument("--list", action="store_true", help="browse the projection")
    parser.add_argument("--stats", action="store_true", help="per-detector denominator")
    parser.add_argument("--records", action="store_true",
                        help="re-derive the card/record disposition of every projected "
                             "recovery finding (autonomy-act-04); runs no detector")
    parser.add_argument("--window-hours", type=int, default=None,
                        help="--records: pr_watcher rows to order against (default lifetime)")
    parser.add_argument("--detector", choices=DETECTORS)
    parser.add_argument("--status", choices=(FINDING_ACTIVE, FINDING_CLEARED))
    parser.add_argument("--max-cards", type=int, default=None)
    parser.add_argument("--seed-status", default=None,
                        help=f"kanban status for seeded cards (default {DEFAULT_SEED_STATUS})")
    args = parser.parse_args(argv)

    if args.list:
        rows = list_findings(detector=args.detector, status=args.status)
        if args.json:
            print(json.dumps(rows, indent=2, default=str))
        else:
            for r in rows:
                print(f"{r['status']:<8} {r['detector']:<13} x{r['seen_count']:<4} "
                      f"{r['task_id'] or '-':<26} {r['title']}")
            if not rows:
                print("(no findings projected)")
        return 0
    if args.records:
        d = dispositions(window_hours=args.window_hours, status=args.status)
        if args.json:
            print(json.dumps(d, indent=2, default=str))
        else:
            for r in d.get("findings") or []:
                print(f"{r['disposition']:<7} {r['subject']:<28} "
                      f"{str(r.get('finding_status')):<8} {r['reason']}")
            if not d.get("measured"):
                print(f"UNMEASURED — {d['state']}: no disposition can be derived")
            else:
                print(f"\n{d['record_only']} record / {d['card']} card "
                      f"of {len(d['findings'])} projected recovery finding(s) "
                      f"({d['record_only_pct']}% record)")
        return 0
    if args.stats:
        s = stats()
        print(json.dumps(s, indent=2, default=str) if args.json else
              "\n".join(f"{k:<14} {v['state']:<12} active={v['active']} cleared={v['cleared']}"
                        for k, v in s["detectors"].items()) or s["state"])
        return 0

    cfg: Dict[str, Any] = {}
    if args.max_cards is not None:
        cfg["max_cards_per_run"] = args.max_cards
    if args.seed_status:
        cfg["seed_status"] = args.seed_status
    report = consume(cfg, seed=not args.dry_run)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    # Report only: this measures the board and the test backlog, not a diff.
    return 2 if report.get("state") == "unmigrated" else 0


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board as the
    # daemon; override=True because a pip-installed ICDEV may have loaded a
    # different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _P
        from dotenv import load_dotenv as _load
        _load(_P(__file__).resolve().parents[2] / ".env", override=True)
    except ImportError:
        pass
    sys.exit(main())
