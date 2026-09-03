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
from typing import Any, Dict, List

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


def _derived_tasks_without_an_open_pr() -> Any:
    """The same set, minus every task the FORGE says already has an open PR."""
    import json
    import subprocess  # nosec B404 — gh only, fixed argv, shell=False

    dispatchable = _reported_dispatchable_tasks()
    if dispatchable is None:
        return None
    try:
        result = subprocess.run(  # nosec B603 B607
            ["gh", "pr", "list", "--state", "open", "--limit", "300",
             "--json", "headRefName"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=45, check=False, shell=False,
        )
        if result.returncode != 0:
            return None
        branches = {str(pr.get("headRefName") or "")
                    for pr in json.loads(result.stdout or "[]")}
    except Exception:  # noqa: BLE001
        # An unreachable forge is UNMEASURABLE, never "no open PRs" — the latter
        # would report every board clean whenever `gh` was unavailable.
        return None
    with_pr = {t for t in dispatchable if "kanban/" + t in branches}
    return sorted(set(dispatchable) - with_pr)


# --------------------------------------------------------------------------- #
# 9. A live scheduler heartbeats  (kpr-stale-03, 2026-09-02)
# --------------------------------------------------------------------------- #
#: The script name the launcher starts; a process whose command line carries
#: it IS a scheduler, whatever the registry says about it.
SCHEDULER_SCRIPT = "kanban_scheduler.py"
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
    """(pid, last_heartbeat) for every active `kanban` row, from the REGISTRY
    TABLE. None when unreadable. Shares nothing with the process scan."""
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT pid, last_heartbeat FROM agent_sessions "
                "WHERE agent_type = %s AND status = %s",
                ("kanban", "active"),
            ).fetchall()
        return [(dict(r).get("pid"), dict(r).get("last_heartbeat")) for r in rows]
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
]
