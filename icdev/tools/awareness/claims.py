# CUI // SP-CTI
"""The registered claims (rem-hyg-17).

Every claim here was a REAL DEFECT found by hand on 2026-08-20. That is
deliberate on two counts:

  * the verifier has known true positives from day one, so "it reports clean"
    cannot be confused with "it does nothing" — the failure mode this codebase
    ships most; and
  * the fixes made that day gain a guard they otherwise lack. Their unit tests
    run against FIXTURES. These run against the LIVE surface and the LIVE
    primary source, so a reduction that regresses in production is caught even
    when every fixture-based test still passes.

THE RULE FOR ADDING ONE. ``reported`` and ``derived`` must not share code. If
the verifier calls what the surface calls, it proves the function is
deterministic — which was never in question. Every defect below survived
because one computation was trusted twice.

AND IT CITES ITS INCIDENT (autonomy-lrn-01). Every claim carries an
``Incident`` naming the card(s) that fixed the defect it was learned from. A
claim is seeded from a VERIFIED FACT — the card is done and the id is on main,
checked by ``tools/awareness/incident_claims.py`` — never from a pattern in the
system's own reported history. ``python tools/awareness/claim_verifier.py
--incidents`` names the window's fixed incidents that still have NO claim.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from tools.awareness.claim_verifier import Claim, Incident, independent_observations


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


# --------------------------------------------------------------------------- #
# 1. A score requires evidence  (rem-hyg-09)
# --------------------------------------------------------------------------- #
#: Canvas -> the table its score is derived from. Kept here rather than imported
#: from posture.py ON PURPOSE: importing posture's own mapping would make the
#: "independent" side a re-run of the reported side.
_EVIDENCE_TABLE = {
    "Security": "sc_assessments", "Network": "nc_compliance_checks",
    "Pipeline": "pc_compliance_checks", "Infra": "idc_assessments",
    "Data": "dd_assessments", "Boundary": "bd_assessments",
    "Observability": "od_assessments", "Agentic AI": "aadc_assessments",
    "AI/ML": "aiml_assessments", "QDC": "qdc_assessments",
    "Migration": "mc_assessments",
}


def _posture_scored_canvases() -> List[str]:
    """What the posture surface REPORTS a number for."""
    from tools.canvas_compliance.posture import compute_canvas_posture
    conn = _conn()
    try:
        rows, _overall = compute_canvas_posture(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return sorted(r["name"] for r in rows if r.get("score") is not None)


def _canvases_with_evidence() -> List[str]:
    """Which canvases actually HOLD evidence — counted straight off each table."""
    from tools.canvas_compliance.posture import _open_canvas_connection
    out = []
    for name, table in _EVIDENCE_TABLE.items():
        cc = _open_canvas_connection(name)
        if cc is None:
            continue
        try:
            row = cc.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()  # nosec B608
            if int(dict(row).get("c") or 0) > 0:
                out.append(name)
        except Exception:
            continue          # unreadable table is not evidence
        finally:
            try:
                cc.close()
            except Exception:
                pass
    return sorted(out)


def _scored_implies_evidence(reported: List[str], derived: List[str]) -> bool:
    """Every canvas showing a NUMBER must hold evidence.

    One-directional on purpose. A canvas holding evidence but scoring None is
    fine (it may be unreadable, or out of scope); a canvas scoring 100.0 with
    zero rows behind it is the defect — measured 2026-08-20, Network, Pipeline
    and Migration all did, and inflated the headline from 87.9 to 90.7.

    GovLift and Zero Trust are scored outside the canvas loop from tables this
    map does not cover, so they are not asserted here rather than being asserted
    wrongly.
    """
    known = set(_EVIDENCE_TABLE)
    return not [c for c in reported if c in known and c not in set(derived)]


# --------------------------------------------------------------------------- #
# 2. `unlogged` is measured, not inferred  (cch-obs-03)
# --------------------------------------------------------------------------- #
def _reported_unlogged() -> Any:
    from tools.cache_savings.savings import get_savings_stats
    return get_savings_stats().get("unlogged")


def _actual_unlogged() -> Any:
    """Straight from the PostgreSQL catalogue. 'u' is UNLOGGED, 'p' permanent."""
    conn = _conn()
    try:
        if not str(getattr(conn, "_backend", "")).startswith("postgres"):
            return None                      # not a PG concept -> unmeasurable
        row = conn.execute(
            "SELECT relpersistence FROM pg_class WHERE relname = 'llm_response_cache'"
        ).fetchone()
        if not row:
            return None
        value = dict(row).get("relpersistence")
        return str(value) == "u"
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 3. Recovery counts OUTCOMES, not attempts  (rem-hyg-16)
# --------------------------------------------------------------------------- #
def _reported_recoveries() -> int:
    """What the panel would headline as recovered."""
    from tools.dashboard.recovery_summary import summarize_recovery
    return sum(1 for r in summarize_recovery(_recovery_rows(), limit=10_000)
               if r["outcome"] == "recovered")


def _derived_recoveries() -> int:
    """Re-derived here without touching summarize_recovery.

    A task is recovered only if the watcher attempted it, it merged, and it was
    never escalated — escalation being the watcher's own "manual intervention
    required". A merge after an escalation is a HUMAN's merge.
    """
    attempted, escalated, merged = set(), set(), set()
    for row in _recovery_rows():
        record = dict(row)
        kind = str(record.get("action") or "").split(".")[-1]
        try:
            payload = json.loads(record.get("d") or "{}")
        except (ValueError, TypeError):
            continue
        task_id = payload.get("task_id")
        if not task_id:
            continue
        if kind in ("resume", "rebase"):
            attempted.add(task_id)
        elif kind == "escalate":
            escalated.add(task_id)
        elif kind == "merge":
            merged.add(task_id)
    return len(attempted & merged - escalated)


def _recovery_rows() -> List[Dict[str, Any]]:
    conn = _conn()
    try:
        pg = str(getattr(conn, "_backend", "")).startswith("postgres")
        details = "details::text" if pg else "details"
        cut = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        return [dict(r) for r in conn.execute(
            f"SELECT action, {details} AS d, created_at FROM audit_trail "  # nosec B608
            "WHERE action IN ('pr_watcher.rebase','pr_watcher.resume',"
            "'pr_watcher.escalate','pr_watcher.merge') AND created_at >= %s",
            (cut,),
        ).fetchall()]
    finally:
        try:
            conn.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 4. Repetition is not corroboration — a stuck writer  (the trap itself)
# --------------------------------------------------------------------------- #
#: A long series carrying one distinct value is SUSPECT — but only against its
#: INPUT. This claim's first live run was a FALSE POSITIVE, and narrowing it is
#: the fix.
#:
#: It flagged odc_gap_scores: 91 rows spanning 2026-07-18..08-20 with ONE
#: distinct value for ONE subject. That is a genuine snapshot series — the
#: `odc_coverage_refresh` reflex recomputes coverage every 6h and persists the
#: result — and `observability_designs.updated_at` is 2026-06-28, UNCHANGED
#: since creation. Identical output from identical input is a correctly working
#: historian, not a stuck writer. (The 0 covered_count is real too: `covered`
#: requires EVERY required signal source present, and across 1,820 technique
#: rows the design has 546 partial and 1,274 gap.)
#:
#: So output-identity ALONE cannot discriminate. The stuck case is output that
#: did not move WHILE THE INPUT DID — an answer frozen against a subject that
#: changed. That needs the input, so each series names one.
#:
#: Narrowing it here rather than leaving it in place is the same discipline the
#: repo applies to arming any check: a rule that fires on correct behaviour
#: refuses routine work, and that is how a check earns itself a `|| true`.
_STUCK_MIN_ROWS = 20
#: (canvas, series table, subject col, value col, input table, input time col)
_STUCK_SERIES = [
    ("Observability", "odc_gap_scores", "design_id", "overall_gap_score",
     "observability_designs", "updated_at"),
]


def _input_changed_since_series_start(cc, series_table: str, input_table: str,
                                      input_col: str) -> bool:
    """Did the INPUT move after the series began repeating itself?

    Fail-safe to False — "the input never changed" — so an unreadable input can
    never manufacture a stuck-writer finding. A false positive here accuses a
    correctly working reflex, which is exactly what this claim did on its first
    live run.
    """
    try:
        first = cc.execute(
            f"SELECT MIN(assessed_at) AS t FROM {series_table}"  # nosec B608
        ).fetchone()
        latest_input = cc.execute(
            f"SELECT MAX({input_col}) AS t FROM {input_table}"  # nosec B608
        ).fetchone()
        started = dict(first).get("t")
        changed = dict(latest_input).get("t")
        if not started or not changed:
            return False
        return str(changed) > str(started)
    except Exception:
        return False


def _reported_series_health() -> Dict[str, str]:
    """What the row count alone would say about each series."""
    from tools.canvas_compliance.posture import _open_canvas_connection
    out: Dict[str, str] = {}
    for canvas, table, _subj, _val, _itbl, _icol in _STUCK_SERIES:
        cc = _open_canvas_connection(canvas)
        if cc is None:
            continue
        try:
            row = cc.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()  # nosec B608
            n = int(dict(row).get("c") or 0)
            out[table] = "well_corroborated" if n >= _STUCK_MIN_ROWS else "sparse"
        except Exception:
            continue
        finally:
            try:
                cc.close()
            except Exception:
                pass
    return out


def _derived_series_health() -> Dict[str, str]:
    """What INDEPENDENT observations say — distinct (subject, value) pairs."""
    from tools.canvas_compliance.posture import _open_canvas_connection
    out: Dict[str, str] = {}
    for canvas, table, subj, val, input_table, input_col in _STUCK_SERIES:
        cc = _open_canvas_connection(canvas)
        if cc is None:
            continue
        try:
            rows = cc.execute(
                f"SELECT {subj} AS s, {val} AS v FROM {table}"  # nosec B608
            ).fetchall()
            n = len(rows)
            distinct = independent_observations(
                [{"s": dict(r).get("s"), "v": dict(r).get("v")} for r in rows], "s", "v")
            if n < _STUCK_MIN_ROWS:
                out[table] = "sparse"
            elif distinct > 1:
                out[table] = "well_corroborated"
            else:
                # One value across a long series. STUCK only if the input moved;
                # otherwise a snapshot writer faithfully reporting an unchanged
                # subject, which is what odc_gap_scores actually is.
                out[table] = ("stuck_writer" if _input_changed_since_series_start(
                    cc, table, input_table, input_col) else "stable_input")
        except Exception:
            continue
        finally:
            try:
                cc.close()
            except Exception:
                pass
    return out


def _no_stuck_writer(reported: Dict[str, str], derived: Dict[str, str]) -> bool:
    """Only a STUCK WRITER is a finding.

    The two sides differ by construction whenever a series repeats: the reported
    side is what a ROW COUNT alone would conclude ("well_corroborated"), the
    derived side is what INDEPENDENT observation concludes. Demanding equality
    would flag every legitimate snapshot series — which is precisely the false
    positive this claim produced on its first live run, against a reflex doing
    its job.

    So the disagreement is narrowed to the case that is actually wrong: an
    output frozen while its input moved.
    """
    return "stuck_writer" not in (derived or {}).values()


# --------------------------------------------------------------------------- #
# 5. An approval park is WHOLE, at every site  (hgx-park-01 + rem-hyg-19)
# --------------------------------------------------------------------------- #
#: THE INCIDENT THIS MODULE'S PROVENANCE RULE WAS WRITTEN FOR. hgx-park-01 made
#: `workflow_runner._park_for_approval` commit the gate row and the run row in
#: ONE transaction and pinned it with structural tests reading THAT function's
#: source. `mcp_executor.open_approval_gate` had the identical two-commit
#: defect, kept failing `assert 'running' == 'awaiting_approval'` on the Windows
#: runner, and was read as flake until rem-hyg-19 — weeks later.
#:
#: A claim over the DATA has no second-site blind spot: whichever function
#: parks, a gate row awaiting a decision under a run that does not read
#: `awaiting_approval` (or a parked run with no pending gate) is the half-commit,
#: observed. The reported side is the HITL surface's own list of pending gates;
#: the derived side reads the RUN table and joins across, sharing no code.
#:
#: Measured 2026-08-21: this board has never held a parked gate (121 step rows,
#: none awaiting), so the claim reads UNMEASURABLE today — never `agrees`.
def _reported_pending_gates() -> List[str]:
    """What the HITL surface lists as awaiting a decision (step ids)."""
    from tools.studio.workflow_runner import get_pending_approvals
    return sorted(get_pending_approvals())


def _derived_pending_gates() -> List[str]:
    """From the RUN side: pending gates under PARKED runs, plus a sentinel for
    every parked run that has no pending gate at all. Raw SQL, no runner code."""
    conn = _conn()
    try:
        whole = conn.execute(
            "SELECT s.step_run_id AS g FROM studio_workflow_runs r "
            "JOIN studio_workflow_run_steps s ON s.run_id = r.run_id "
            "WHERE r.status = 'awaiting_approval' AND s.status = 'awaiting_approval'"
        ).fetchall()
        gateless = conn.execute(
            "SELECT r.run_id AS g FROM studio_workflow_runs r "
            "WHERE r.status = 'awaiting_approval' AND NOT EXISTS ("
            "  SELECT 1 FROM studio_workflow_run_steps s "
            "  WHERE s.run_id = r.run_id AND s.status = 'awaiting_approval')"
        ).fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out = [str(dict(r)["g"]) for r in whole]
    out += [f"run-without-gate:{dict(r)['g']}" for r in gateless]
    return sorted(out)


def _park_is_whole(reported: List[str], derived: List[str]) -> bool:
    """Every gate the surface shows sits under a parked run, and every parked
    run shows a gate. A gate in `reported` missing from `derived` is the first
    half of a two-commit park; a `run-without-gate:` sentinel is the other
    order. Either one is the defect, whichever site wrote it."""
    return set(reported or []) == set(derived or [])


# --------------------------------------------------------------------------- #
# 5. A held task lease has a live holder  (rem-hyg-15)
# --------------------------------------------------------------------------- #
# THE INCIDENT. A lease taken by a one-shot script pinned three tasks after the
# process that took it had exited. `promote_backlog_to_scheduled` skipped every
# pinned task, and the board sat `idle [review_bound]` for an hour with free
# capacity — reporting idleness as a fact about the QUEUE when it was a fact
# about a dead process.
#
# The two sides share no code: the LEASE FILES on disk say what is held; the
# PROCESS TABLE and the task heartbeat say who is alive. A dead pid alone is not
# proof (the dispatching pid exits after handoff, which is what rem-hyg-15 got
# wrong first), so the derivation asks the same two-signal question adm-03
# consolidated — and `None` from either means CANNOT TELL, which counts as ALIVE.
def _reported_task_leases() -> Any:
    """Every kanban task lease the coordination layer currently reports held."""
    try:
        from tools.coordination import leases
        from tools.coordination.constants import LEASE_DIR
    except Exception:  # noqa: BLE001
        return None
    try:
        if not LEASE_DIR.exists():
            return []
        held = []
        for path in sorted(LEASE_DIR.glob("kanban_task_*.json")):
            # The file name is the resource, sanitised. Recover the task id from
            # the lease's own metadata rather than un-mangling the name.
            meta = leases.holder(_resource_from_meta(path))
            if meta:
                held.append(str(meta.get("resource") or path.stem))
        return sorted(set(held))
    except Exception:  # noqa: BLE001
        return None


def _resource_from_meta(path) -> str:
    """The `resource` a lease file records, or its stem if unreadable."""
    import json

    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("resource")
                   or path.stem)
    except Exception:  # noqa: BLE001
        return path.stem


def _derived_leases_with_a_live_holder() -> Any:
    """The same leases, kept only where the holder is alive or unknowable.

    UNKNOWABLE COUNTS AS ALIVE. `holder_is_alive` returns None when it cannot
    tell, and treating that as dead is precisely how a live worker loses its
    lease — the error rem-hyg-15 made on its first probe, reaping a lease whose
    task had heartbeat four seconds earlier.
    """
    try:
        from tools.coordination import leases
    except Exception:  # noqa: BLE001
        return None
    reported = _reported_task_leases()
    if reported is None:
        return None

    alive = []
    for resource in reported:
        try:
            verdict = leases.holder_is_alive(resource)
        except Exception:  # noqa: BLE001
            verdict = None
        if verdict is not False:          # True or None -> alive
            alive.append(resource)
            continue
        # A dead pid is not dead work: adm-03 requires a heartbeat as an
        # independent second signal before anything is treated as gone.
        task_id = resource.split(":")[-1]
        if _task_is_heartbeating(task_id):
            alive.append(resource)
    return sorted(alive)


def _task_is_heartbeating(task_id: str) -> bool:
    """Second signal, asked of the board rather than the process table."""
    try:
        from tools.kanban.lease_liveness import task_is_heartbeating
        return bool(task_is_heartbeating(task_id))
    except Exception:  # noqa: BLE001
        # Cannot tell -> assume alive, for the same reason as above.
        return True


# --------------------------------------------------------------------------- #
# 6. A dispatchable task has no open PR  (rem-hyg-18)
# --------------------------------------------------------------------------- #
# THE INCIDENT. `pr_opened` had exactly ONE writer in the tree — the stale
# reaper, gated on `status = 'in_progress'` — so a task in `scheduled` or
# `backlog` with an open PR could reach it by NO path. The Home panel read the
# forge and showed three PRs while the Kanban column read the status and showed
# none. 77 `pr_opened` transitions in seven days with zero tasks in that state
# is the shape of a status reachable only as a side effect.
#
# The two sides are the BOARD and the FORGE — two systems, no shared code.
_DISPATCHABLE_STATUSES = ("backlog", "scheduled")


def _reported_dispatchable_tasks() -> Any:
    """Tasks the board is willing to hand to a worker."""
    try:
        with _conn() as conn:
            marks = ", ".join(["%s"] * len(_DISPATCHABLE_STATUSES))
            rows = conn.execute(
                "SELECT id FROM kanban_tasks WHERE status IN (" + marks + ")",
                _DISPATCHABLE_STATUSES,
            ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    return sorted({str(dict(r).get("id")) for r in rows or []})


def _forge_open_head_branches() -> Optional[Set[str]]:
    """Head branch of every OPEN PR, straight from `gh`; None if it cannot answer.

    An unreachable forge is UNMEASURABLE, never "no open PRs" — the latter
    would report every board clean whenever `gh` was unavailable. Deliberately
    NOT the scheduler's cached listing: the claims that read this exist to
    check the scheduler's own view of the board against a second system.
    """
    import json
    import subprocess  # nosec B404 — gh only, fixed argv, shell=False

    try:
        result = subprocess.run(  # nosec B603 B607
            ["gh", "pr", "list", "--state", "open", "--limit", "300",
             "--json", "headRefName"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=45, check=False, shell=False,
        )
        if result.returncode != 0:
            return None
        return {str(pr.get("headRefName") or "")
                for pr in json.loads(result.stdout or "[]")}
    except Exception:  # noqa: BLE001
        return None


def _derived_tasks_without_an_open_pr() -> Any:
    """The same set, minus every task the FORGE says already has an open PR."""
    dispatchable = _reported_dispatchable_tasks()
    if dispatchable is None:
        return None
    branches = _forge_open_head_branches()
    if branches is None:
        return None
    with_pr = {t for t in dispatchable if "kanban/" + t in branches}
    return sorted(set(dispatchable) - with_pr)


# --------------------------------------------------------------------------- #
# 8b. A parked task with an open PR is OWNED  (mfx-own-01, 2026-09-03)
# --------------------------------------------------------------------------- #
# rmf-rfp-01 and rmf-wp-02 parked `token exhaustion: parked for retry 2/60`
# with PRs #2040 / #2042 OPEN and red on CI, and their resume_at slid forward
# every cycle for six hours. The dispatcher's respawn guard refuses a task
# whose branch has an open PR (correctly) and pr_watcher polls only
# pr_opened / ci_failed / merge_conflict / changes_requested -- so a parked
# task with an open PR was owned by NEITHER, and a human landed both by hand.
#
# The two sides are the BOARD and the FORGE. `reported` is what the board says
# is parked and waiting for a scheduler retry; `derived` is which kanban/<id>
# branches the forge says carry an open PR. A task in BOTH is the finding: the
# scheduler cannot retry it (the respawn guard) and the watcher cannot see it
# (not pr_opened). One scheduler cycle of grace, because the hand-off runs at
# park time and at every retry evaluation, and a task parked seconds ago has
# not yet had its evaluation.
SCHEDULER_CYCLE_SECONDS = 60


def _parse_board_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text = str(raw).strip().replace("Z", "+00:00")
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _reported_parked_tasks_older_than_a_cycle() -> Any:
    """Tasks the board says are parked token_exhausted, and have been for more
    than one scheduler cycle -- i.e. the retry loop has had its chance to
    notice an open PR and hand the task off."""
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id, updated_at FROM kanban_tasks "
                "WHERE status = 'token_exhausted'",
            ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=SCHEDULER_CYCLE_SECONDS)
    parked: List[str] = []
    for r in rows or []:
        rec = dict(r)
        ts = _parse_board_ts(rec.get("updated_at"))
        # An unparseable timestamp is OLD, not fresh: the grace exists to
        # excuse a task parked seconds ago, and a row that cannot prove that
        # does not get the excuse.
        if ts is None or ts < cutoff:
            parked.append(str(rec.get("id")))
    return sorted(parked)


def _derived_forge_open_kanban_task_ids() -> Any:
    """The task id behind every OPEN kanban/<id> PR, from the forge alone."""
    branches = _forge_open_head_branches()
    if branches is None:
        return None
    return sorted({b[len("kanban/"):] for b in branches if b.startswith("kanban/")})


def _no_parked_task_has_an_open_pr(reported: Any, derived: Any) -> bool:
    """A task that is BOTH parked (board) and has an open PR (forge) is owned by
    nobody -- that intersection is the finding. Disjoint sets agree."""
    return not (set(reported or ()) & set(derived or ()))


# --------------------------------------------------------------------------- #
# 9. A live scheduler heartbeats  (kpr-stale-03, 2026-09-02)
# --------------------------------------------------------------------------- #
#: The script name the launcher starts; a process whose command line carries
#: it IS a scheduler, whatever the registry says about it.
SCHEDULER_SCRIPT = "kanban_scheduler.py"
#: The service name the scheduler claims (service_identity); its registry rows
#: are `kanban-scheduler-<pid>`, or the bare name from a pre-sid-01 deployment.
SCHEDULER_SERVICE = "kanban-scheduler"
#: Ten cycles at the 60s interval. Well above one missed beat; a scheduler that
#: has not heartbeat in ten minutes is not "busy", it is not looping.
SCHEDULER_HEARTBEAT_WINDOW_MINUTES = 10


def _live_scheduler_pids() -> Any:
    """PIDs of running kanban scheduler processes, from the PROCESS TABLE.

    None when the table cannot be read (no psutil, or a scan error): that is
    unmeasurable, never "no scheduler".
    """
    try:
        import psutil
    except ImportError:
        return None
    pids: List[int] = []
    try:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
            except Exception:  # noqa: BLE001 -- a vanished process
                continue
            if SCHEDULER_SCRIPT in cmd:
                pids.append(int(proc.info["pid"]))
    except Exception:  # noqa: BLE001
        return None
    return sorted(set(pids))


def _kanban_session_rows() -> Any:
    """(pid, last_heartbeat) for every active SCHEDULER row, from the REGISTRY
    TABLE. None when unreadable. Shares nothing with the process scan.

    Scheduler rows, not `agent_type = 'kanban'` rows (claim-verif-33c9f4cd11):
    every process the scheduler spawns inherits `ICDEV_AGENT=kanban`, so a
    worker session's coordination-hook row, and any one-shot command run inside
    a worker, is a `kanban` row too. Reading those here put a hook process's pid
    on the card as "what the primary data says" and, worse, let a board with NO
    scheduler read `agrees` — the reported side empty, the derived side full of
    hook rows. A scheduler row is one `is_service_session` recognises, which a
    descendant's `.../child-<pid>` row deliberately is not.
    """
    from tools.coordination.service_identity import is_service_session

    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT session_id, pid, last_heartbeat FROM agent_sessions "
                "WHERE status = %s",
                ("active",),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if is_service_session(str(d.get("session_id") or ""), SCHEDULER_SERVICE):
                out.append((d.get("pid"), d.get("last_heartbeat")))
        return out
    except Exception:  # noqa: BLE001
        return None


def _reported_scheduler_pids() -> Any:
    """What the host REPORTS: scheduler processes that exist right now."""
    return _live_scheduler_pids()


def _derived_scheduler_pids_heartbeating() -> Any:
    """Independently: scheduler pids the registry has heard from recently."""
    rows = _kanban_session_rows()
    if rows is None:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=SCHEDULER_HEARTBEAT_WINDOW_MINUTES)
    fresh: List[int] = []
    for pid, beat in rows:
        try:
            ts = beat if isinstance(beat, datetime) else datetime.fromisoformat(
                str(beat).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                fresh.append(int(pid))
        except (TypeError, ValueError):
            continue
    return sorted(set(fresh))


def _every_live_scheduler_heartbeats(reported: Any, derived: Any) -> bool:
    """A scheduler that exists must be one the registry has heard from."""
    return set(reported or []) <= set(derived or [])


# --------------------------------------------------------------------------- #
# 10-13. The four RMF standing claims  (rmf-ui-02)
# --------------------------------------------------------------------------- #
# Every mitigation in the RMF project reduces to ONE rule: a surface may not
# render a number whose supporting evidence nothing independently re-derived.
# Each card below fixed one face of that defect with a fixture-based unit test
# pinning the function it changed. These four claims run the same question
# against the LIVE surface and the LIVE primary data every six hours, so a
# regression is a `disagrees` verdict on the board rather than a tile nobody
# questions.
#
# The derived side of each reads YAML with `yaml.safe_load` and tables with raw
# SQL, and shares NOTHING with the module it checks: no loader, no resolver, no
# classifier. Where a vocabulary or a mapping has to be known (the ZT pillar ->
# DevSecOps stage map, the classification-method vocabulary), it is copied here
# as a local constant ON PURPOSE, exactly as `_EVIDENCE_TABLE` above is --
# importing the surface's own constant would make the "independent" side a
# re-run of the reported side.
#
# MEASURED ON THE LIVE BOARD 2026-09-03 before registration: asset_identity 0
# rows, asset_visibility_snapshots 0 rows, zta_posture_evidence 0 rows,
# zta_maturity_scores 8 rows ALL `unmeasured` with a NULL score,
# rmf_workflow_stages 0 rows. Every one of these claims therefore agrees today
# on the honest side of the ledger -- the surfaces report unmeasured, the
# substrates ARE empty -- and that agreement is a measurement, not a vacuous
# one: a surface rendering a percentage, a maturity band, a reduction ratio or
# a classification method over those same empty tables is precisely the
# regression each card removed. The verifier's both-sides-empty rule is not
# reached, because each side carries the scalar the surface actually asserts
# (`measurable: False`, `total: 0`, `ratio_emitted: False`, an unmeasured
# pillar map), and a scalar is a real answer.


def _yaml_at(relpath: str) -> Any:
    """Read one repo YAML file directly. None when unreadable -- never {}."""
    import yaml
    from icdev.core.paths import repo_root

    try:
        with open(repo_root(__file__) / relpath, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _count_rows(table: str) -> Any:
    """COUNT(*) over one table through the platform connection.

    None when the table cannot be read -- an unmigrated database is
    UNMEASURABLE, never "zero rows".
    """
    try:
        with _conn() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()  # nosec B608
        return int(dict(row).get("n") or 0)
    except Exception:  # noqa: BLE001
        return None


# ---- 10. asset_visibility_has_denominator  (rmf-vis-01) ------------------- #
# THE DEFECT. "Asset visibility: 100%" over an estate nobody has sized -- the
# `seen / total * 100 if total else 100.0` shape, drawing a full green bar for
# a fabric nothing has ever scanned. rmf-vis-01 made visibility_pct None unless
# an authoritative denominator is DECLARED for the fabric in
# args/asset_denominators.yaml, and unmeasurable while asset_identity is empty.
#
# Reported: what `measure()` renders -- per fabric, the percentage it shows and
# the denominator kind it credits. Derived: the YAML read directly (which
# fabrics declare a recognised kind at all) and a raw COUNT of asset_identity.
# A percentage for a fabric with no declaration, or a `measurable` report over
# an identity table with no rows, is the fabrication coming back.
def _reported_visibility() -> Any:
    from tools.assets.visibility import measure

    report = measure()
    assessed: Dict[str, Dict[str, Any]] = {}
    for fab in report.get("fabrics") or []:
        if fab.get("visibility_pct") is not None:
            assessed[str(fab.get("fabric_id"))] = {
                "pct": fab.get("visibility_pct"),
                "source": fab.get("denominator_source"),
            }
    return {
        "measurable": bool(report.get("measurable")),
        "identity_rows": (report.get("identity") or {}).get("total"),
        "assessed": assessed,
    }


def _derived_visibility_denominators() -> Any:
    cfg = _yaml_at("args/asset_denominators.yaml")
    rows = _count_rows("asset_identity")
    if cfg is None or rows is None:
        return None
    kinds = {
        str(k.get("kind")) for k in (cfg.get("kinds") or []) if isinstance(k, dict)
    }
    declared: Dict[str, List[str]] = {}
    for fabric_id, decls in (cfg.get("fabrics") or {}).items():
        if isinstance(decls, dict):
            decls = [decls]
        recognised = sorted(
            str(d.get("kind")) for d in (decls or [])
            if isinstance(d, dict) and str(d.get("kind")) in kinds
        )
        if recognised:
            declared[str(fabric_id)] = recognised
    return {"identity_rows": rows, "declared": declared}


def _visibility_pct_is_backed(reported: Any, derived: Any) -> bool:
    """A percentage only ever stands on a DECLARED denominator, over a
    non-empty identity table -- and the kind it credits is one the fabric
    actually declares. One-directional: a declared fabric with no percentage
    is `not_assessed` and fine (nothing observed there yet)."""
    if reported.get("measurable") and not (derived.get("identity_rows") or 0):
        return False
    for fabric_id, shown in (reported.get("assessed") or {}).items():
        declared = (derived.get("declared") or {}).get(fabric_id)
        if not declared:
            return False
        if shown.get("source") not in declared:
            return False
    return True


# ---- 11. zt_score_has_evidence  (rmf-zt-02) ------------------------------- #
# THE DEFECT. zta_maturity_scorer averaged posture_score over
# zta_posture_evidence rows whose evidence_data was NULL -- a ratio over a
# checkbox list -- and over an EMPTY table it was structurally 0/5, so a
# pillar nobody had assessed persisted a number and a 'traditional' band that
# cato_monitor, the ZIG bridge and the MCP zta_posture_check all read as
# measured. rmf-zt-02 made an unmeasured pillar persist score NULL and
# maturity_level 'unmeasured'.
#
# Reported: `get_latest_score()`, the read-only accessor the ZIG bridge
# consumes -- which pillars carry a number. Derived: for the SAME project (its
# id read straight off zta_maturity_scores), which pillars have ANY
# evidence-backed signal in the primary tables: a project_controls row for one
# of the pillar's NIST controls, a 'current' zta_posture_evidence row with
# non-empty evidence_data for one of its evidence types, or a devsecops_profiles
# row where the pillar maps to a stage. Config is read with yaml directly.
#: The scorer's own pillar -> stage map, copied so this side imports nothing
#: from it. A pillar mapping to no stage cannot be evidenced by a profile row.
_ZT_PILLAR_STAGES = {
    "user_identity": (), "device": (), "network": ("policy_as_code",),
    "application_workload": ("sast", "container_scan", "image_signing"),
    "data": ("secret_detection", "sbom_attestation"),
    "visibility_analytics": ("sca", "license_compliance"),
    "automation_orchestration": ("rasp", "policy_as_code"),
}
#: evidence_data values that are PRESENT and still carry nothing. A scalar 0 or
#: false is evidence ("measured, and it was zero"); these are ticks.
_ZT_EMPTY_EVIDENCE = {"", "null", "none", "{}", "[]", '""', "''"}


def _reported_zt_scores() -> Any:
    from tools.devsecops.zta_maturity_scorer import get_latest_score

    latest = get_latest_score()
    if latest is None:
        return None                       # never assessed -> unmeasurable
    return {
        "project_id": latest.get("project_id"),
        "scored": sorted(latest.get("pillar_scores") or {}),
        "unmeasured": sorted(latest.get("unmeasured_pillars") or []),
        "overall_scored": latest.get("overall_score") is not None,
    }


def _zt_evidence_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value.strip().lower() not in _ZT_EMPTY_EVIDENCE
    if isinstance(value, (dict, list, tuple, set)):
        return len(value) > 0
    return True                           # 0 / False are measurements


def _derived_zt_evidence_backed() -> Any:
    cfg = _yaml_at("args/zta_config.yaml")
    if cfg is None:
        return None
    pillars = cfg.get("pillars") or {}
    try:
        with _conn() as conn:
            latest = conn.execute(
                "SELECT project_id FROM zta_maturity_scores "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not latest:
                return None
            project_id = dict(latest)["project_id"]
            controls = {
                str(dict(r)["control_id"]) for r in conn.execute(
                    "SELECT control_id FROM project_controls WHERE project_id = %s",
                    (project_id,),
                ).fetchall()
            }
            evidenced_types = {
                str(dict(r)["evidence_type"]) for r in conn.execute(
                    "SELECT evidence_type, evidence_data FROM zta_posture_evidence "
                    "WHERE project_id = %s AND status = %s",
                    (project_id, "current"),
                ).fetchall()
                if _zt_evidence_present(dict(r).get("evidence_data"))
            }
            profile = conn.execute(
                "SELECT active_stages FROM devsecops_profiles WHERE project_id = %s",
                (project_id,),
            ).fetchone()
    except Exception:  # noqa: BLE001
        return None

    backed: List[str] = []
    for pillar, spec in pillars.items():
        spec = spec or {}
        nist = {str(c) for c in (spec.get("nist_800_53_controls") or [])}
        types = {str(t) for t in (spec.get("evidence_types") or [])}
        has_signal = bool(nist & controls) or bool(types & evidenced_types)
        if profile is not None and _ZT_PILLAR_STAGES.get(str(pillar)):
            has_signal = True             # a profile row IS an observation
        if has_signal:
            backed.append(str(pillar))
    return {"project_id": project_id, "evidence_backed": sorted(backed)}


def _scored_pillar_has_evidence(reported: Any, derived: Any) -> bool:
    """Every pillar the surface shows a NUMBER for holds a signal in primary
    data, for the same project; an overall number needs at least one. A pillar
    with evidence and no number is a stale assessment, not a fabrication, and
    is deliberately not asserted."""
    if reported.get("project_id") != derived.get("project_id"):
        return False
    backed = set(derived.get("evidence_backed") or [])
    if not set(reported.get("scored") or []) <= backed:
        return False
    return not (reported.get("overall_scored") and not backed)


# ---- 12. rmf_baseline_recorded  (rmf-cyc-01) ------------------------------ #
# THE DEFECT. "Months -> 72 hours" with no denominator: the "months" half was a
# word, never a number, and every anecdotal ATO duration is wall-clock to the
# signed authorization and so CONTAINS the AO's queue -- dividing it by an
# automation-only clock is the blend wearing a percentage. rmf-cyc-01 declares
# the baseline in args/rmf_cycle_baseline.yaml with its provenance and REFUSES
# the comparison until it is quantified and AO-queue-free.
#
# Reported: whether `collect_report()` emits a `comparison` (a reduction
# ratio) and against what baseline_hours. Derived: the YAML read directly --
# is a baseline RECORDED (value_hours present, not including decision latency
# under the file's own rules) -- and a raw COUNT of rmf_workflow_stages, since
# a ratio also needs an automation clock and a clock needs rows.
def _reported_rmf_ratio() -> Any:
    from tools.compliance.rmf_cycle_time import collect_report

    report = collect_report()
    source = report.get("baseline_source") or {}
    comparison = source.get("comparison") or None
    return {
        "state": report.get("state"),
        "ratio_emitted": comparison is not None,
        "baseline_hours": (comparison or {}).get("baseline_hours"),
        "refused": sorted(source.get("comparison_refused") or []),
    }


def _derived_rmf_baseline() -> Any:
    cfg = _yaml_at("args/rmf_cycle_baseline.yaml")
    rows = _count_rows("rmf_workflow_stages")
    if cfg is None or rows is None:
        return None
    baseline = cfg.get("baseline") or {}
    rules = cfg.get("comparison") or {}
    hours = baseline.get("value_hours")
    includes_queue = bool(baseline.get("includes_decision_latency"))
    permitted = rows > 0
    if rules.get("require_quantified_baseline", True) and hours is None:
        permitted = False
    if rules.get("refuse_when_baseline_includes_decision_latency", True) and includes_queue:
        permitted = False
    return {
        "declared_hours": hours,
        "includes_decision_latency": includes_queue,
        "stage_rows": rows,
        "ratio_permitted": permitted,
    }


def _ratio_only_over_a_recorded_baseline(reported: Any, derived: Any) -> bool:
    """A ratio is emitted only when the declaration permits one, and then
    against the hours the declaration records. A permitted-but-unemitted ratio
    is fine: the automation clock may be unmeasured for reasons of its own."""
    if not reported.get("ratio_emitted"):
        return True
    if not derived.get("ratio_permitted"):
        return False
    return reported.get("baseline_hours") == derived.get("declared_hours")


# ---- 13. classification_method_declared  (rmf-ident-01) ------------------- #
# THE DEFECT. Three asset stacks, no shared key, and a classification label
# with no record of HOW it was arrived at. rmf-ident-01 added
# classification_method (rule | oui | model | human_confirmed) to the canonical
# asset_identity row, where NULL is a FOURTH state -- nothing has classified
# it -- that must never be read as 'rule'.
#
# Reported: the `stats()` histogram the CLI renders, which buckets NULL as
# 'unclassified'. Derived: a raw GROUP BY over the same column. The two must
# agree bucket for bucket -- a surface that COALESCEs NULL into a method, or
# drops the unclassified bucket, moves rows between them -- and no stored value
# may sit outside the vocabulary.
_CLASSIFICATION_METHODS = ("rule", "oui", "model", "human_confirmed")
_UNCLASSIFIED = "unclassified"


def _reported_classification_methods() -> Any:
    from tools.assets.identity import stats

    out = stats()
    if not out.get("measurable"):
        return None
    return {
        "total": out.get("total"),
        "methods": dict(out.get("classification_method") or {}),
    }


def _derived_classification_methods() -> Any:
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT classification_method AS m, COUNT(*) AS n "
                "FROM asset_identity GROUP BY classification_method"
            ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    methods: Dict[str, int] = {}
    outside: List[str] = []
    for row in rows:
        record = dict(row)
        method = record.get("m")
        key = _UNCLASSIFIED if method is None else str(method)
        methods[key] = methods.get(key, 0) + int(record.get("n") or 0)
        if method is not None and str(method) not in _CLASSIFICATION_METHODS:
            outside.append(str(method))
    return {
        "total": sum(methods.values()),
        "methods": methods,
        "outside_vocabulary": sorted(set(outside)),
    }


def _methods_declared_bucket_for_bucket(reported: Any, derived: Any) -> bool:
    if derived.get("outside_vocabulary"):
        return False
    if reported.get("total") != derived.get("total"):
        return False
    return dict(reported.get("methods") or {}) == dict(derived.get("methods") or {})


REGISTRY: List[Claim] = [
    Claim(
        claim_id="posture_score_needs_evidence",
        description=(
            "A canvas that shows a compliance NUMBER must hold at least one "
            "assessment row. On 2026-08-20 Network, Pipeline and Migration each "
            "scored 100.0 with zero rows behind them and inflated the headline "
            "from 87.9 to 90.7."
        ),
        reported=_posture_scored_canvases,
        derived=_canvases_with_evidence,
        agree=_scored_implies_evidence,
        tier="propose",
        tags=["compliance", "rem-hyg-09"],
        incident=Incident(["rem-hyg-09"], "2026-08-20",
                          "posture returns None, never 100.0, for a canvas with no rows"),
    ),
    Claim(
        claim_id="cache_unlogged_is_measured",
        description=(
            "The reported `unlogged` flag must match pg_class.relpersistence. It "
            "was `backend.startswith('postgres')` — a constant wearing the name "
            "of a measurement — and asserted the opposite of the truth from the "
            "day migration 20260816123233 made the table LOGGED."
        ),
        reported=_reported_unlogged,
        derived=_actual_unlogged,
        tier="propose",
        tags=["cache", "cch-obs-03"],
        incident=Incident(["cch-obs-03"], "2026-08-20",
                          "`unlogged` is read from pg_class.relpersistence"),
    ),
    Claim(
        claim_id="recovery_counts_outcomes_not_attempts",
        description=(
            "'Auto-recovered' must count tasks that recovered, not audit rows. "
            "Over 7 days the panel claimed 331 where the honest figure was 46 of "
            "86 — because a task retried to the five-attempt cap and then fixed "
            "by hand contributed FIVE rows to the success list."
        ),
        reported=_reported_recoveries,
        derived=_derived_recoveries,
        tier="propose",
        tags=["autonomy", "rem-hyg-16"],
        incident=Incident(["rem-hyg-16"], "2026-08-20",
                          "the recovery panel counts tasks that merged un-escalated"),
    ),
    Claim(
        claim_id="repetition_is_not_corroboration",
        description=(
            "A long series carrying one distinct value is a STUCK WRITER, not a "
            "stable measurement. odc_gap_scores holds 91 rows over a month with "
            "one value for one subject; anything that gains confidence from row "
            "count rates that as strongly corroborated."
        ),
        reported=_reported_series_health,
        derived=_derived_series_health,
        agree=_no_stuck_writer,
        tier="propose",
        tags=["evidence-quality", "rem-hyg-17"],
        incident=Incident(["rem-hyg-17"], "2026-08-20",
                          "this claim's own first live run: narrowed to output "
                          "frozen while its INPUT moved"),
    ),
    Claim(
        claim_id="approval_park_is_whole",
        description=(
            "A Studio approval gate awaiting a decision must sit under a run "
            "that reads awaiting_approval, and a parked run must show a gate. "
            "The park was TWO commits at two sites: hgx-park-01 made "
            "workflow_runner._park_for_approval atomic and its structural tests "
            "read that function; mcp_executor.open_approval_gate kept the "
            "defect for weeks, read as Windows flake, until rem-hyg-19."
        ),
        reported=_reported_pending_gates,
        derived=_derived_pending_gates,
        agree=_park_is_whole,
        tier="propose",
        tags=["studio", "hitl", "hgx-park-01", "rem-hyg-19"],
        incident=Incident(["hgx-park-01", "rem-hyg-19"], "2026-08-20",
                          "both park sites commit the gate row and the run row "
                          "in one transaction"),
    ),
    Claim(
        claim_id="held_task_lease_has_a_live_holder",
        description=(
            "Every held kanban task lease must have a holder that is alive, or "
            "whose liveness cannot be determined. A lease taken by a one-shot "
            "script pinned three tasks after its process exited; the board sat "
            "`idle [review_bound]` for an hour with free capacity, reporting "
            "idleness as a fact about the QUEUE when it was a fact about a dead "
            "process."
        ),
        reported=_reported_task_leases,
        derived=_derived_leases_with_a_live_holder,
        tier="propose",
        tags=["kanban", "rem-hyg-15"],
        incident=Incident(["rem-hyg-15", "autonomy-adm-03"], "2026-08-20",
                          "liveness needs a dead pid AND no heartbeat, and "
                          "cannot-tell counts as alive"),
    ),
    Claim(
        claim_id="dispatchable_task_has_no_open_pr",
        description=(
            "A task the board is willing to dispatch must not already have an "
            "open PR. `pr_opened` had ONE writer, gated on `in_progress`, so a "
            "scheduled task with a PR could reach it by no path — the Home "
            "panel read the forge and showed three, the Kanban column read the "
            "status and showed none."
        ),
        reported=_reported_dispatchable_tasks,
        derived=_derived_tasks_without_an_open_pr,
        tier="propose",
        tags=["kanban", "rem-hyg-18"],
        incident=Incident(["rem-hyg-18"], "2026-08-21",
                          "_reconcile_pr_opened moves a scheduled/backlog task "
                          "with an open PR into pr_opened every cycle"),
    ),
    Claim(
        claim_id="parked_task_with_open_pr_is_owned",
        description=(
            "A task parked token_exhausted whose branch has an OPEN PR must be "
            "handed to pr_watcher as pr_opened, never left to nobody. On "
            "2026-09-03 rmf-rfp-01 and rmf-wp-02 parked for retry with PRs "
            "#2040 / #2042 open and red on CI; the respawn guard refused every "
            "scheduler retry, the watcher never polls token_exhausted, and their "
            "resume_at slid forward for six hours until a human fixed both."
        ),
        reported=_reported_parked_tasks_older_than_a_cycle,
        derived=_derived_forge_open_kanban_task_ids,
        agree=_no_parked_task_has_an_open_pr,
        tier="propose",
        tags=["kanban", "autonomy", "mfx-own-01", "rmf-rfp-01", "rmf-wp-02"],
        incident=Incident(["mfx-own-01"], "2026-09-03",
                          "the scheduler hands a parked task with an open PR to "
                          "pr_watcher at park time, on every retry evaluation and "
                          "at startup recovery"),
    ),
    Claim(
        claim_id="scheduler_heartbeat_is_fresh",
        description=(
            "Every kanban scheduler PROCESS that is alive must have heartbeat in "
            "agent_sessions within the last ten minutes. On 2026-09-02 pid 29880 "
            "ran for five hours with no heartbeat and no log line while the board "
            "sat idle for 8h; the supervisor restarts only on EXIT, so an "
            "alive-but-not-looping scheduler was never restarted, and it was "
            "found by a human noticing nothing had moved."
        ),
        reported=_reported_scheduler_pids,
        derived=_derived_scheduler_pids_heartbeating,
        agree=_every_live_scheduler_heartbeats,
        tier="propose",
        tags=["autonomy", "kanban", "kpr-stale-03"],
        incident=Incident(["kpr-stale-03"], "2026-09-02",
                          "the silent scheduler was found and killed by hand while "
                          "fixing the lease leak; this claim is its detector"),
    ),
    Claim(
        claim_id="asset_visibility_has_denominator",
        description=(
            "A fabric that shows a visibility PERCENTAGE must have an "
            "authoritative denominator declared for it in "
            "args/asset_denominators.yaml, credited by the kind it declares, "
            "over a non-empty asset_identity table. The shape it refuses is "
            "`seen / total * 100 if total else 100.0` -- a full green bar for "
            "a fabric nothing has ever scanned."
        ),
        reported=_reported_visibility,
        derived=_derived_visibility_denominators,
        agree=_visibility_pct_is_backed,
        tier="propose",
        tags=["rmf", "assets", "rmf-vis-01"],
        incident=Incident(["rmf-vis-01"], "2026-09-02",
                          "visibility_pct is None, never 0.0 or 100.0, without a "
                          "declared denominator; corroboration depth is the number "
                          "that needs none"),
    ),
    Claim(
        claim_id="zt_score_has_evidence",
        description=(
            "Every ZT pillar the latest persisted assessment shows a NUMBER "
            "for must hold an evidence-backed signal in primary data for that "
            "project: a project_controls row, a 'current' zta_posture_evidence "
            "row with non-empty evidence_data, or a DevSecOps profile. The "
            "scorer used to average over a checkbox list and, over an empty "
            "table, persist 0/5 as a 'traditional' band."
        ),
        reported=_reported_zt_scores,
        derived=_derived_zt_evidence_backed,
        agree=_scored_pillar_has_evidence,
        tier="propose",
        tags=["rmf", "zero-trust", "rmf-zt-02"],
        incident=Incident(["rmf-zt-02"], "2026-09-02",
                          "an unmeasured pillar persists score NULL and "
                          "maturity_level 'unmeasured'; self-attested and "
                          "evidence-backed are two numbers"),
    ),
    Claim(
        claim_id="rmf_baseline_recorded",
        description=(
            "The RMF cycle-time report emits a months->72h reduction ratio "
            "ONLY when args/rmf_cycle_baseline.yaml records a quantified "
            "baseline that excludes the AO's queue, and then against exactly "
            "the hours it records. An anecdotal ATO duration contains the "
            "decision latency; dividing it by an automation-only clock is the "
            "blend wearing a percentage."
        ),
        reported=_reported_rmf_ratio,
        derived=_derived_rmf_baseline,
        agree=_ratio_only_over_a_recorded_baseline,
        tier="propose",
        tags=["rmf", "compliance", "rmf-cyc-01"],
        incident=Incident(["rmf-cyc-01"], "2026-09-02",
                          "the baseline is declared with its provenance and the "
                          "comparison is refused until it is quantified and "
                          "AO-queue-free"),
    ),
    Claim(
        claim_id="classification_method_declared",
        description=(
            "The asset-identity histogram of HOW each asset was classified "
            "(rule | oui | model | human_confirmed) must match a raw GROUP BY "
            "over asset_identity bucket for bucket, with NULL as its own "
            "'unclassified' bucket and no value outside the vocabulary. NULL "
            "is a fourth state -- nothing has classified it -- and must never "
            "be read as 'rule'."
        ),
        reported=_reported_classification_methods,
        derived=_derived_classification_methods,
        agree=_methods_declared_bucket_for_bucket,
        tier="propose",
        tags=["rmf", "assets", "rmf-ident-01"],
        incident=Incident(["rmf-ident-01"], "2026-09-02",
                          "classification_method lives on the canonical "
                          "asset_identity row under a CHECK, nullable on purpose"),
    ),
]
