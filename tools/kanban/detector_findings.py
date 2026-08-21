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

UNMEASURABLE CLEARS NOTHING. status_churn on an idle board, born_red_survey on
an unmigrated baseline, recovery_summary with no audit rows — each reports
that it could not measure, and a run that could not measure must not be read
as "the finding is gone". Only a MEASURABLE run that no longer reports a
finding clears it. ``detector_runs`` is the denominator that keeps "never
ran", "ran and could not measure" and "ran and found nothing" apart.

Seeds through ``tools.kanban.task_factory.create_tasks``, never a raw INSERT.

    python -m tools.kanban.detector_findings --json          # run, seed, report
    python -m tools.kanban.detector_findings --dry-run       # run; write NOTHING
    python -m tools.kanban.detector_findings --list          # browse the projection
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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.kanban.detector_findings")

FINDINGS_TABLE = "detector_findings"
RUNS_TABLE = "detector_runs"
MIGRATION = "20260821050135_detector_findings_projection"

DETECTOR_STATUS_CHURN = "status_churn"
DETECTOR_BORN_RED = "born_red"
DETECTOR_RECOVERY = "recovery"
DETECTORS = (DETECTOR_STATUS_CHURN, DETECTOR_BORN_RED, DETECTOR_RECOVERY)

#: detector_runs.last_state — and the per-detector ``state`` in a report.
RUN_FINDINGS = "findings"
RUN_CLEAN = "clean"
RUN_UNMEASURABLE = "unmeasurable"
RUN_ERROR = "error"

FINDING_ACTIVE = "active"
FINDING_CLEARED = "cleared"

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
    """

    def __init__(self, detector: str, subject: str, fingerprint: str, *,
                 title: str, priority: str, task_type: str,
                 evidence: Mapping[str, Any], derivation: str, advice: str):
        super().__init__(
            detector=detector, subject=str(subject), fingerprint=str(fingerprint),
            title=title, priority=priority, task_type=task_type,
            evidence={k: _iso(v) for k, v in dict(evidence).items()},
            derivation=derivation, advice=advice,
        )
        self["finding_id"] = finding_ident(detector, self["subject"], self["fingerprint"])


def finding_ident(detector: str, subject: str, fingerprint: str) -> str:
    """Stable id for (detector, subject, fingerprint)."""
    raw = f"{detector}|{subject}|{fingerprint}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


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
                      window_hours: int) -> List[Finding]:
    """recovery_summary.summarize_recovery() -> findings.

    Only ``needed_a_human`` is a finding: it is the watcher's OWN verdict
    ("resume cap reached, manual intervention required"), so it needs no
    inference. ``recovered`` is the system working and ``unresolved`` is a
    verdict not yet reached — neither is a work item.
    """
    out: List[Finding] = []
    for entry in entries or []:
        if str(entry.get("outcome")) != "needed_a_human":
            continue
        task_id = str(entry.get("task_id") or "").strip()
        if not task_id:
            continue
        reason = str(entry.get("reason") or "").strip()
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
        ))
    return out


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


def run_born_red(conn, cfg: Mapping[str, Any]) -> dict:
    from tools.ci.born_red_survey import SurveyError, load_observations, survey

    # Read the baseline through the connection we were handed, so a test (or
    # a tenant database) is surveyed rather than whatever get_connection()
    # resolves to inside the survey.
    try:
        observations = load_observations(conn)
    except SurveyError as exc:
        return _result(RUN_UNMEASURABLE, reason=str(exc))
    report = survey(observations=observations)
    summary = {k: v for k, v in report.items() if k != "findings"}
    if report.get("state") == "unmeasurable":
        return _result(RUN_UNMEASURABLE,
                       reason="no ungated test has ever been observed on this deployment",
                       summary=summary)
    findings = born_red_findings(report)
    return _result(RUN_FINDINGS if findings else RUN_CLEAN, findings, summary=summary)


def recovery_rows(conn, window_hours: int) -> List[dict]:
    """The pr_watcher audit rows the recovery panel and claim_verifier read."""
    pg = str(getattr(conn, "_backend", "")).startswith("postgres")
    details = "details::text" if pg else "details"
    cut = (_now() - timedelta(hours=window_hours)).isoformat()
    return [dict(r) for r in conn.execute(
        f"SELECT action, {details} AS d, created_at FROM audit_trail "  # nosec B608
        "WHERE action IN ('pr_watcher.rebase','pr_watcher.resume',"
        "'pr_watcher.escalate','pr_watcher.merge') AND created_at >= %s "
        "ORDER BY created_at",
        (cut,),
    ).fetchall()]


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
    findings = recovery_findings(entries, window_hours)
    return _result(RUN_FINDINGS if findings else RUN_CLEAN, findings, summary=summary)


DEFAULT_RUNNERS: Dict[str, Callable[[Any, Mapping[str, Any]], dict]] = {
    DETECTOR_STATUS_CHURN: run_status_churn,
    DETECTOR_BORN_RED: run_born_red,
    DETECTOR_RECOVERY: run_recovery,
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


def _clear_missing(conn, detector: str, still_active: Sequence[str], now_iso: str) -> int:
    """A MEASURABLE run no longer reports these: clear them. Returns the count."""
    rows = conn.execute(
        f"SELECT finding_id FROM {FINDINGS_TABLE} WHERE detector = %s AND status = %s",  # nosec B608
        (detector, FINDING_ACTIVE),
    ).fetchall()
    keep = set(still_active)
    cleared = [str(dict(r)["finding_id"]) for r in rows if str(dict(r)["finding_id"]) not in keep]
    for fid in cleared:
        conn.execute(
            f"UPDATE {FINDINGS_TABLE} SET status = %s, cleared_at = %s WHERE finding_id = %s",  # nosec B608
            (FINDING_CLEARED, now_iso, fid),
        )
    return len(cleared)


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


def _card_status(conn, task_id: Optional[str]) -> Optional[str]:
    if not task_id:
        return None
    row = conn.execute("SELECT status FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone()
    return str(dict(row)["status"]) if row else None


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def render_description(f: Finding, *, seen_count: int, first_seen_at: Optional[str],
                       revision: int) -> str:
    evidence = json.dumps(f["evidence"], indent=2, default=str, sort_keys=True)
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
        "reports it and marks it `cleared` on the first MEASURABLE cycle that does not.\n"
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
        "findings_cleared": 0,
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

        candidates: List[dict] = []   # {"finding", "existing", "revision"}
        for name, runner in runners.items():
            d_cfg = dict(detector_cfg.get(name) or {})
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
                "new": 0, "recurring": 0, "cleared": 0,
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
                    if prior is None:
                        entry["new"] += 1
                        revision = 1
                    elif not prior.get("task_id"):
                        # Projected on an earlier run and DEFERRED by the cap (or
                        # the seeder failed): still owed its first card.
                        entry["new"] += 1
                        revision = int(prior.get("card_count") or 0) + 1
                    else:
                        card_state = _card_status(conn, prior.get("task_id"))
                        recurred = (
                            str(prior.get("status")) == FINDING_CLEARED
                            or card_state in TERMINAL_CARD_STATUSES
                        )
                        if recurred:
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
                    entry["cleared"] = _clear_missing(
                        conn, name, [f["finding_id"] for f in findings], now_iso)
                    report["findings_cleared"] += entry["cleared"]
            report["findings_new"] += entry["new"]
            report["findings_recurring"] += entry["recurring"]

            if seed:
                _record_run(conn, name, state, entry["reason"], entry["findings"],
                            entry["summary"], now_iso)

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
            f"new={d['new']} recurring={d['recurring']} cleared={d['cleared']} "
            f"({d['elapsed_seconds']}s)" + (f"  — {d['reason']}" if d.get("reason") else ""))
    lines.append(
        f"  seen={report.get('findings_seen')} new={report.get('findings_new')} "
        f"recurring={report.get('findings_recurring')} cleared={report.get('findings_cleared')}")
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
