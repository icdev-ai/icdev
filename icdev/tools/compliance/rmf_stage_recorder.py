# CUI // SP-CTI
"""RMF workflow stage recorder — the ONE writer of ``rmf_workflow_stages``.

rmf-cyc-01.

WHY THIS EXISTS
---------------
``rmf_workflow_stages`` has carried the six NIST SP 800-37 Rev 2 steps since it
was created and held ZERO rows on the live board — measured 2026-09-02, along
with ``ssp_documents``, ``poam_items``, ``stig_findings``, ``oscal_artifacts``
and ``cato_evidence``, all also empty. The table was read by the ATO dashboard,
which returned six synthetic ``not_started`` stages whenever it found nothing,
so an estate that had never been assessed rendered identically to one at the
start of its lifecycle. Nothing wrote to it because nothing was WIRED to write
to it: it was a hand-maintained board, and hand-maintained boards do not get
maintained.

So the writes here are not a new bookkeeping step somebody must remember. They
happen as a CONSEQUENCE of an artifact being produced. If an SSP was generated,
the ``select`` stage started; if STIG findings were assessed, ``assess``
started; if cATO evidence was collected, ``monitor`` is live. The stage row
cannot drift from the artifacts because the artifacts are what write it.

THE TWO CLOCKS
--------------
The columns exist to keep two spans apart that a single ``completed_at`` merges:

    started_at  -> submitted_at    OURS.   automation_time — the 72h claim.
    submitted_at -> completed_at   THEIRS. decision_latency — the AO's queue.

A single elapsed figure across both is unfalsifiable. A deployment can halve
its automation and watch the headline get worse because an Authorizing Official
took leave; it can change nothing and watch it improve. That is why
``submitted_at`` is a separate column from ``completed_at`` and why
``tools/compliance/rmf_cycle_time.py`` never adds them.

WHAT IS NOT WIRED, AND SAID OUT LOUD
------------------------------------
``categorize`` has no automated producer here. ``fips199_categorizer`` is the
tool that would write it and this card deliberately does not reach into it —
five producers were named and five were wired. The consequence is visible
rather than papered over: ``categorize`` reports ``not_started`` for every
project, and ``rmf_cycle_time`` names it under ``stages_without_producer``. An
automation clock that silently began at ``select`` because nobody wired the step
before it would be measuring a shorter job than the one being claimed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

# The six RMF steps, imported rather than re-declared: the ATO dashboard already
# owns this list and the SQL CHECK constraint is derived from it. Two copies is
# how a seventh stage gets added to one and refused by the other.
from tools.ato_compliance.dashboard import RMF_STAGES

logger = get_logger("icdev.compliance.rmf_stage_recorder")


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------

# Which RMF step an artifact is EVIDENCE OF, per NIST SP 800-37 Rev 2. Declared
# once, here, rather than spelled at each call site — a mapping every reviewer
# can read in one place is auditable in a way five scattered string literals are
# not.
#
#   ssp                        Task S-4/S-5, the security plan is developed and
#                              approved in SELECT.
#   poam                       Task A-6, the POA&M is an ASSESS output.
#   stig_assessment            Task A-4, assessment reports.
#   oscal_component_definition Task I-1, control implementation.
#   cato_evidence              Step 7 MONITOR, Task M-2 ongoing assessment.
#
# An OSCAL artifact is attributed by WHAT IT IS, not by the fact that
# oscal_generator produced it: an OSCAL SSP is still an SSP.
ARTIFACT_STAGE: dict[str, str] = {
    "ssp": "select",
    "poam": "assess",
    "stig_assessment": "assess",
    "oscal_ssp": "select",
    "oscal_poam": "assess",
    "oscal_assessment_plan": "assess",
    "oscal_assessment_results": "assess",
    "oscal_component_definition": "implement",
    "cato_evidence": "monitor",
}

# Actors whose writes count as AUTOMATION. Anything prefixed 'human:' is manual.
# Anything else is UNKNOWN and is counted as neither — see actor_kind().
AUTOMATED_ACTORS: frozenset[str] = frozenset(
    {
        "ssp_generator",
        "poam_generator",
        "stig_checker",
        "oscal_generator",
        "cato_monitor",
    }
)

HUMAN_ACTOR_PREFIX = "human:"

# Statuses the table's CHECK constraint admits.
_VALID_STATUSES = ("not_started", "in_progress", "complete", "blocked")

# How an AO decision maps onto those four. 'denied' is `blocked`, not
# `complete`: a refused package is not a finished stage, and recording it as one
# would make a denial indistinguishable from an authorization in every count
# that reads `status`.
DECISION_STATUS: dict[str, str] = {
    "authorized": "complete",
    "conditional": "complete",
    "denied": "blocked",
}


def actor_kind(actor: str | None) -> str:
    """Classify an actor as ``automated``, ``human`` or ``unknown``.

    Three values, not two. An actor this module does not recognise must not be
    counted as automation — that would inflate the very number the platform is
    making a claim about — and must not be counted as manual either, because the
    manual population is the control group a ``measured_here`` baseline is
    derived from. Unknown is its own bucket and is reported as one.
    """
    if not actor:
        return "unknown"
    if actor in AUTOMATED_ACTORS:
        return "automated"
    if actor.startswith(HUMAN_ACTOR_PREFIX):
        return "human"
    return "unknown"


def evidence_ref(kind: str, ref: Any) -> str:
    """Build the ``evidence_ref`` pointer string: ``<kind>:<ref>``.

    ``kind`` is where to look (a table name, or ``file``) and ``ref`` is the key
    within it. A stage row that asserts progress with nothing to point at is the
    defect this platform keeps rediscovering — an assertion whose supporting
    evidence nothing can re-derive.
    """
    return f"{kind}:{ref}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_connection(db_path=None):
    """Open the default storage connection (used when no conn is injected)."""
    from tools.db.storage import get_connection

    return get_connection(db_path=db_path) if db_path else get_connection()


def _table_ready(conn) -> bool:
    """Is ``rmf_workflow_stages`` present AND widened by migration 20260902233931?

    A database that has the table but not the columns is a REAL population — the
    live PostgreSQL board was exactly that until this card — and writing the
    narrow shape there would record a stage with no clock on it, which reads
    downstream as "measured, and it took no time". Refusing is the honest
    outcome and it is reported, never swallowed.
    """
    from tools.db.storage import table_exists

    if not table_exists(conn, "rmf_workflow_stages"):
        return False
    backend = getattr(conn, "_backend", "sqlite")
    try:
        if backend == "postgresql":
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'rmf_workflow_stages'"
            ).fetchall()
            cols = {dict(r)["column_name"] for r in rows}
        else:
            # pg-portability: sqlite-only path — PRAGMA is the SQLite catalogue;
            # the PostgreSQL branch above reads information_schema.
            rows = conn.execute("PRAGMA table_info(rmf_workflow_stages)").fetchall()
            cols = set()
            for r in rows:
                d = dict(r) if hasattr(r, "keys") else None
                cols.add(d["name"] if d and "name" in d else r[1])
    except Exception as exc:  # pragma: no cover - catalogue read failure
        logger.warning("rmf_workflow_stages column probe failed: %s", exc)
        return False
    return {"started_at", "actor", "evidence_ref", "submitted_at"} <= cols


def _fetch_row(conn, project_id: str, stage: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM rmf_workflow_stages WHERE project_id = %s AND stage = %s",
        (project_id, stage),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def record_artifact(
    project_id: str,
    artifact_kind: str,
    *,
    actor: str,
    evidence: str,
    conn: Any = None,
    db_path: str | None = None,
    now: str | None = None,
) -> dict:
    """Record that ``artifact_kind`` was produced for ``project_id``.

    Called from the artifact producers themselves, on the success path, after
    the artifact has been persisted. Never raises: a compliance artifact that
    was generated successfully must not be reported as a failure because a
    bookkeeping write did not land. It does not *swallow* either — a refusal is
    logged and returned in ``recorded``/``reason``.

    ``started_at`` is stamped ONCE and never moved. An SSP regenerated on day 3
    of a package must not reset the automation clock to day 3; the clock started
    when the first artifact appeared.

    Returns a dict with ``recorded`` (bool), ``stage``, ``created`` and, when
    the write did not happen, ``reason``.
    """
    stage = ARTIFACT_STAGE.get(artifact_kind)
    if stage is None:
        logger.warning(
            "rmf stage not recorded: unknown artifact_kind %r (known: %s)",
            artifact_kind,
            ", ".join(sorted(ARTIFACT_STAGE)),
        )
        return {"recorded": False, "reason": "unknown_artifact_kind", "stage": None}

    return record_stage_event(
        project_id,
        stage,
        actor=actor,
        evidence=evidence,
        conn=conn,
        db_path=db_path,
        now=now,
    )


def record_stage_event(
    project_id: str,
    stage: str,
    *,
    actor: str,
    evidence: str,
    conn: Any = None,
    db_path: str | None = None,
    now: str | None = None,
) -> dict:
    """Upsert the (project, stage) row for a piece of produced evidence."""
    if stage not in RMF_STAGES:
        logger.warning("rmf stage not recorded: unknown stage %r", stage)
        return {"recorded": False, "reason": "unknown_stage", "stage": stage}
    if not project_id or not actor:
        return {"recorded": False, "reason": "missing_project_or_actor", "stage": stage}

    ts = now or _now()
    _conn = conn or _get_connection(db_path)
    close_after = conn is None
    try:
        if not _table_ready(_conn):
            logger.warning(
                "rmf stage not recorded for %s/%s: rmf_workflow_stages is absent or "
                "predates migration 20260902233931",
                project_id,
                stage,
            )
            return {"recorded": False, "reason": "schema_not_ready", "stage": stage}

        existing = _fetch_row(_conn, project_id, stage)
        if existing is None:
            _conn.execute(
                """INSERT INTO rmf_workflow_stages
                   (project_id, stage, status, started_at, actor, evidence_ref,
                    created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (project_id, stage, "in_progress", ts, actor, evidence, ts, ts),
            )
            _conn.commit()
            return {"recorded": True, "stage": stage, "created": True, "started_at": ts}

        # A stage already marked complete is NOT reopened by a regenerated
        # artifact — re-running the SSP generator against an approved package is
        # routine and does not undo the approval.
        status = existing.get("status") or "not_started"
        new_status = status if status in ("complete", "blocked") else "in_progress"
        started_at = existing.get("started_at") or ts
        _conn.execute(
            """UPDATE rmf_workflow_stages
               SET status = %s, started_at = %s, actor = %s,
                   evidence_ref = %s, updated_at = %s
               WHERE project_id = %s AND stage = %s""",
            (new_status, started_at, actor, evidence, ts, project_id, stage),
        )
        _conn.commit()
        return {
            "recorded": True,
            "stage": stage,
            "created": False,
            "started_at": started_at,
        }

    except Exception as exc:
        logger.warning(
            "rmf stage write failed for %s/%s (artifact was still produced): %s",
            project_id,
            stage,
            exc,
        )
        return {"recorded": False, "reason": f"error: {exc}", "stage": stage}
    finally:
        if close_after:
            try:
                _conn.close()
            except Exception:
                pass


def record_submission(
    project_id: str,
    *,
    actor: str,
    evidence: str,
    stage: str = "authorize",
    conn: Any = None,
    db_path: str | None = None,
    now: str | None = None,
) -> dict:
    """Record that the package was HANDED to the decider — the clock boundary.

    Everything before ``submitted_at`` is the platform's; everything after it is
    the Authorizing Official's queue.

    A RESUBMISSION overwrites ``submitted_at`` and CLEARS any recorded decision.
    ``decision_latency`` therefore measures the most recent submit -> decide
    pair, never a sum across rework cycles: a package returned for corrections
    and resubmitted has spent time in OUR queue in between, and carrying the
    first submission forward would fold that rework into the AO's number. One
    row can hold one pending decision; that is stated rather than approximated.
    """
    if stage not in RMF_STAGES:
        return {"recorded": False, "reason": "unknown_stage", "stage": stage}
    if not project_id or not actor:
        return {"recorded": False, "reason": "missing_project_or_actor", "stage": stage}

    ts = now or _now()
    _conn = conn or _get_connection(db_path)
    close_after = conn is None
    try:
        if not _table_ready(_conn):
            logger.warning(
                "rmf submission not recorded for %s: rmf_workflow_stages is absent "
                "or predates migration 20260902233931",
                project_id,
            )
            return {"recorded": False, "reason": "schema_not_ready", "stage": stage}

        existing = _fetch_row(_conn, project_id, stage)
        if existing is None:
            _conn.execute(
                """INSERT INTO rmf_workflow_stages
                   (project_id, stage, status, started_at, submitted_at, actor,
                    evidence_ref, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (project_id, stage, "in_progress", ts, ts, actor, evidence, ts, ts),
            )
        else:
            _conn.execute(
                """UPDATE rmf_workflow_stages
                   SET status = 'in_progress', started_at = %s, submitted_at = %s,
                       completed_at = NULL, actor = %s, evidence_ref = %s,
                       updated_at = %s
                   WHERE project_id = %s AND stage = %s""",
                (
                    existing.get("started_at") or ts,
                    ts,
                    actor,
                    evidence,
                    ts,
                    project_id,
                    stage,
                ),
            )
        _conn.commit()
        return {"recorded": True, "stage": stage, "submitted_at": ts}

    except Exception as exc:
        logger.warning("rmf submission write failed for %s: %s", project_id, exc)
        return {"recorded": False, "reason": f"error: {exc}", "stage": stage}
    finally:
        if close_after:
            try:
                _conn.close()
            except Exception:
                pass


def record_decision(
    project_id: str,
    decision: str,
    *,
    actor: str,
    evidence: str | None = None,
    stage: str = "authorize",
    conn: Any = None,
    db_path: str | None = None,
    now: str | None = None,
) -> dict:
    """Record the decider's verdict, closing ``decision_latency``.

    A decision recorded against a row with no ``submitted_at`` is ACCEPTED and
    its latency is UNMEASURABLE — not zero. Refusing the write would lose a real
    authorization; recording it as instantaneous would fabricate the best
    possible number for the one clock the platform does not control.
    """
    if decision not in DECISION_STATUS:
        return {
            "recorded": False,
            "reason": f"unknown_decision (known: {', '.join(sorted(DECISION_STATUS))})",
            "stage": stage,
        }
    if stage not in RMF_STAGES or not project_id or not actor:
        return {"recorded": False, "reason": "invalid_arguments", "stage": stage}

    ts = now or _now()
    status = DECISION_STATUS[decision]
    _conn = conn or _get_connection(db_path)
    close_after = conn is None
    try:
        if not _table_ready(_conn):
            return {"recorded": False, "reason": "schema_not_ready", "stage": stage}

        existing = _fetch_row(_conn, project_id, stage)
        note = f"decision={decision}"
        if existing is None:
            _conn.execute(
                """INSERT INTO rmf_workflow_stages
                   (project_id, stage, status, completed_at, actor, evidence_ref,
                    notes, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (project_id, stage, status, ts, actor, evidence, note, ts, ts),
            )
            measurable = False
        else:
            # Two statements rather than COALESCE(%s, evidence_ref): a NULL text
            # parameter inside COALESCE leaves PostgreSQL unable to infer the
            # parameter's type, and the failure would land in the except below —
            # losing an authorization decision to a type-inference error.
            if evidence:
                _conn.execute(
                    """UPDATE rmf_workflow_stages
                       SET status = %s, completed_at = %s, actor = %s,
                           evidence_ref = %s, notes = %s, updated_at = %s
                       WHERE project_id = %s AND stage = %s""",
                    (status, ts, actor, evidence, note, ts, project_id, stage),
                )
            else:
                _conn.execute(
                    """UPDATE rmf_workflow_stages
                       SET status = %s, completed_at = %s, actor = %s,
                           notes = %s, updated_at = %s
                       WHERE project_id = %s AND stage = %s""",
                    (status, ts, actor, note, ts, project_id, stage),
                )
            measurable = bool(existing.get("submitted_at"))
        _conn.commit()
        return {
            "recorded": True,
            "stage": stage,
            "decision": decision,
            "status": status,
            "completed_at": ts,
            "latency_measurable": measurable,
        }

    except Exception as exc:
        logger.warning("rmf decision write failed for %s: %s", project_id, exc)
        return {"recorded": False, "reason": f"error: {exc}", "stage": stage}
    finally:
        if close_after:
            try:
                _conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLI — the door for the two human events the producers cannot observe
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Record an RMF workflow stage event (rmf-cyc-01)"
    )
    parser.add_argument("--project-id", "--project", required=True, dest="project_id")
    parser.add_argument(
        "--actor",
        required=True,
        help="Who: a generator name, or 'human:<name>' for a manual event",
    )
    parser.add_argument("--evidence", default="", help="Pointer to the artifact, '<kind>:<ref>'")
    parser.add_argument("--db", dest="db_path", help="Database path override")
    parser.add_argument("--json", action="store_true", help="Emit JSON")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--submit", action="store_true", help="Package submitted to the AO")
    group.add_argument(
        "--decision",
        choices=sorted(DECISION_STATUS),
        help="Record the AO's verdict",
    )
    group.add_argument("--artifact", choices=sorted(ARTIFACT_STAGE), help="Record an artifact")
    parser.add_argument("--stage", default="authorize", choices=list(RMF_STAGES))

    args = parser.parse_args(argv)

    if args.submit:
        result = record_submission(
            args.project_id,
            actor=args.actor,
            evidence=args.evidence,
            stage=args.stage,
            db_path=args.db_path,
        )
    elif args.decision:
        result = record_decision(
            args.project_id,
            args.decision,
            actor=args.actor,
            evidence=args.evidence or None,
            stage=args.stage,
            db_path=args.db_path,
        )
    else:
        result = record_artifact(
            args.project_id,
            args.artifact,
            actor=args.actor,
            evidence=args.evidence,
            db_path=args.db_path,
        )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        state = "recorded" if result.get("recorded") else f"REFUSED ({result.get('reason')})"
        print(f"{args.project_id} / {result.get('stage')}: {state}")
    return 0 if result.get("recorded") else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
