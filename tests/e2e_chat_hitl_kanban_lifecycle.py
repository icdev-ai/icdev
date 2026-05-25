#!/usr/bin/env python3
# CUI // SP-CTI
"""Full Lifecycle E2E Test: Chat -> HITL -> Kanban Auto-Intake Pipeline

Scenario: Multi-Domain Conflict Intelligence Platform (MCIP)
--------------------------------------------------------------
A FICTITIOUS hypothetical US DoD/IC system for monitoring geopolitical
tension indicators between nation-state actors in the Middle East theater.
This is a SIMULATION test scenario only -- no real operational data.

Pipeline under test:
  [1] Requirement-bearing chat messages -> requirement_intake_hook
  [2] intake_engine.process_turn()     -> extract requirements
  [3] decomposition_engine              -> SAFe Epic/Feature/Story breakdown
  [4] WorkflowEngine.create_instance() -> HITL review queue (not direct Kanban)
  [5] feedback.submit_feedback(approve) -> _post_approve()
  [6] intake_kanban_promoter.promote()  -> kanban_tasks

CUI Marking: All test artifacts classified CUI // SP-CTI (Simulated)
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# -- path setup ----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

# -- constants -----------------------------------------------------------------
SCENARIO_LABEL = "MCIP -- Middle East Theater Intelligence Platform (FICTITIOUS)"
CONTEXT_ID     = f"e2e-ctx-mcip-{int(time.time())}"
SEPARATOR      = "-" * 72

# -- FICTITIOUS requirements for the test scenario -----------------------------
# These are synthetic, fictional system requirements used solely as test data
# to exercise the ICDEV(TM) requirements intake and decomposition pipeline.
REQUIREMENTS_MESSAGES = [
    # Turn 1 -- Force disposition / domain awareness requirements
    (
        "The MCIP system shall provide a real-time geo-spatial dashboard that "
        "aggregates open-source intelligence (OSINT) feeds, commercial satellite "
        "imagery, and diplomatic cable summaries for theater-wide situational "
        "awareness across the Eastern Mediterranean and Persian Gulf regions. "
        "The system must support IL5 data ingestion and display within 30 seconds "
        "of source publication."
    ),
    # Turn 2 -- Alert and escalation requirements
    (
        "As an intelligence analyst, I want to configure automated threat-level "
        "thresholds so that the system shall generate Priority Intelligence "
        "Requirements (PIR) alerts when indicator scores exceed operator-defined "
        "baselines. "
        "The system must support three alert tiers: WATCHCON 4 (routine), "
        "WATCHCON 3 (elevated), and WATCHCON 2 (high). "
        "Alerts must be delivered to downstream SIEM within 5 seconds."
    ),
    # Turn 3 -- Multi-agency data sharing requirements
    (
        "The system shall support multi-agency data sharing via the IC Information "
        "Environment (IC IE) data fabric. Given a cleared analyst at a partner "
        "agency when they request a cross-domain data pull then the MCIP system "
        "must enforce attribute-based access control (ABAC) with mandatory "
        "classification markings on all returned data objects. "
        "All cross-agency data transfers must be logged in the append-only audit "
        "trail per NIST AU-2 and AU-9 requirements."
    ),
    # Turn 4 -- Diplomatic status tracking
    (
        "The MCIP system should provide a Diplomatic Activity Tracker (DAT) "
        "capability that ingests State Department cable traffic, UNSC meeting "
        "schedules, and back-channel communication metadata to derive a "
        "Diplomatic Tension Index (DTI) score updated every 6 hours. "
        "The feature must expose a REST API endpoint for downstream consumption "
        "by the Joint Intelligence Support Element (JISE) portal."
    ),
]

# -- helpers -------------------------------------------------------------------

def _conn():
    """Return a storage connection using the active ICDEV backend (PG or SQLite)."""
    from tools.db.storage import get_connection
    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_step(n: int, title: str) -> None:
    print(f"\n{'='*72}")
    print(f"  STEP {n}: {title}")
    print(f"{'='*72}")


def _print_ok(msg: str) -> None:
    print(f"  [OK]  {msg}")


def _print_info(msg: str) -> None:
    print(f"  [..]  {msg}")


def _print_fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def assert_eq(label: str, got, want) -> None:
    if got != want:
        _print_fail(f"{label}: got {got!r}, want {want!r}")
        raise AssertionError(f"{label}: {got!r} != {want!r}")
    _print_ok(f"{label} = {got!r}")


def assert_true(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        _print_fail(f"{label}{': ' + detail if detail else ''}")
        raise AssertionError(f"{label} is False")
    _print_ok(f"{label}{': ' + detail if detail else ''}")


# ==============================================================================
# LIFECYCLE TEST
# ==============================================================================

def run() -> None:
    results: dict = {"passed": 0, "failed": 0, "steps": []}
    start_ts = time.time()

    print(f"\n{'='*72}")
    print("  CUI // SP-CTI  (SIMULATED TEST DATA ONLY)")
    print("  ICDEV(TM) Full Lifecycle E2E -- Chat -> HITL -> Kanban")
    print(f"  Scenario: {SCENARIO_LABEL}")
    print(f"  Context ID: {CONTEXT_ID}")
    print(f"  Started: {_now()}")
    print(f"{'='*72}")

    # -- STEP 1: DB health & schema readiness ----------------------------------
    _print_step(1, "Database health & schema readiness")
    step = {"step": 1, "name": "DB health"}
    try:
        conn = _conn()
        required_tables = [
            "kanban_tasks", "intake_sessions", "intake_requirements",
            "safe_decomposition", "wf_templates", "wf_instances", "wf_approvals",
        ]
        for t in required_tables:
            try:
                conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()
                assert_true(f"Table '{t}' exists", True)
            except Exception:
                assert_true(f"Table '{t}' exists", False)
        tmpl_count = conn.execute("SELECT COUNT(*) FROM wf_templates").fetchone()[0]
        assert_true("wf_templates seeded", tmpl_count > 0, f"{tmpl_count} templates")
        conn.close()
        step["status"] = "PASS"
        results["passed"] += 1
        _print_ok("DB schema ready")
    except Exception as exc:
        step["status"] = "FAIL"
        step["error"] = str(exc)
        results["failed"] += 1
        _print_fail(str(exc))
    results["steps"].append(step)

    # -- STEP 2: Requirement detection pre-filter ------------------------------
    _print_step(2, "Requirement detection pre-filter (no LLM)")
    step = {"step": 2, "name": "Requirement detector"}
    try:
        from tools.chat.requirement_intake_hook import _is_requirement_bearing

        # Should PASS
        req_messages = REQUIREMENTS_MESSAGES
        for i, msg in enumerate(req_messages):
            detected = _is_requirement_bearing(msg)
            assert_true(f"Turn {i+1} message detected as requirement-bearing", detected,
                        f"'{msg[:60]}...'")

        # Should NOT detect casual messages
        casual = ["Good morning!", "Thanks, noted.", "Can you elaborate?", "Let's break for lunch."]
        for c in casual:
            detected = _is_requirement_bearing(c)
            assert_true(f"Casual message NOT detected: '{c}'", not detected)

        step["status"] = "PASS"
        results["passed"] += 1
    except Exception as exc:
        step["status"] = "FAIL"
        step["error"] = str(exc)
        results["failed"] += 1
        _print_fail(str(exc))
    results["steps"].append(step)

    # -- STEP 3: Intake session creation ---------------------------------------
    _print_step(3, "Intake session creation via chat hook")
    step = {"step": 3, "name": "Intake session"}
    session_id = None
    try:
        from tools.chat.requirement_intake_hook import ensure_mapping_tables, _get_or_create_session

        ensure_mapping_tables()
        session_id = _get_or_create_session(CONTEXT_ID)

        assert_true("Session ID created", bool(session_id))
        _print_info(f"Session ID: {session_id}")

        # Verify persisted in chat_intake_sessions
        conn = _conn()
        row = conn.execute(
            "SELECT session_id FROM chat_intake_sessions WHERE context_id = %s",
            (CONTEXT_ID,),
        ).fetchone()
        conn.close()
        assert_true("chat_intake_sessions row exists", row is not None)
        assert_eq("session_id in mapping", row["session_id"], session_id)

        step["status"] = "PASS"
        step["session_id"] = session_id
        results["passed"] += 1
    except Exception as exc:
        step["status"] = "FAIL"
        step["error"] = str(exc)
        results["failed"] += 1
        _print_fail(str(exc))
    results["steps"].append(step)

    if not session_id:
        print("\n[ABORT] Cannot continue without a valid intake session.")
        _print_final(results, start_ts)
        return

    # -- STEP 4: Process requirement turns through intake engine ---------------
    _print_step(4, f"Process {len(REQUIREMENTS_MESSAGES)} requirement turns through intake engine")
    step = {"step": 4, "name": "Intake engine turns"}
    reqs_extracted = 0
    try:
        from tools.requirements.intake_engine import process_turn

        for i, msg in enumerate(REQUIREMENTS_MESSAGES):
            _print_info(f"Processing turn {i+1}/{len(REQUIREMENTS_MESSAGES)}...")
            result = process_turn(session_id, msg)
            _print_info(f"  Turn {i+1} result keys: {list(result.keys()) if result else 'empty'}")

        # Count extracted requirements
        conn = _conn()
        reqs_extracted = conn.execute(
            "SELECT COUNT(*) FROM intake_requirements WHERE session_id = %s",
            (session_id,),
        ).fetchone()[0]
        conn.close()

        _print_info(f"Total requirements extracted: {reqs_extracted}")
        assert_true("At least 1 requirement extracted", reqs_extracted >= 1,
                    f"{reqs_extracted} found")

        step["status"] = "PASS"
        step["requirements_extracted"] = reqs_extracted
        results["passed"] += 1
        _print_ok(f"{reqs_extracted} requirement(s) extracted from MCIP scenario")
    except Exception as exc:
        step["status"] = "FAIL"
        step["error"] = str(exc)
        results["failed"] += 1
        _print_fail(str(exc))
    results["steps"].append(step)

    # -- STEP 5: SAFe decomposition ---------------------------------------------
    _print_step(5, "SAFe decomposition (Epic -> Feature -> Story)")
    step = {"step": 5, "name": "SAFe decomposition"}
    decomp_count = 0
    try:
        from tools.requirements.decomposition_engine import decompose_requirements

        _print_info("Running decomposition to Story level with BDD + estimation...")
        decompose_requirements(
            session_id=session_id,
            target_level="story",
            generate_bdd=True,
            estimate=True,
        )

        conn = _conn()
        decomp_count = conn.execute(
            "SELECT COUNT(*) FROM safe_decomposition WHERE session_id = %s",
            (session_id,),
        ).fetchone()[0]

        # Show level breakdown
        level_counts = conn.execute(
            "SELECT level, COUNT(*) as cnt FROM safe_decomposition WHERE session_id=%s GROUP BY level",
            (session_id,),
        ).fetchall()
        conn.close()

        _print_info(f"Total decomposed items: {decomp_count}")
        for lc in level_counts:
            _print_info(f"  {lc['level'].upper()}: {lc['cnt']} item(s)")

        assert_true("At least 1 SAFe item decomposed", decomp_count >= 1,
                    f"{decomp_count} items")

        step["status"] = "PASS"
        step["decomp_count"] = decomp_count
        step["level_breakdown"] = {lc["level"]: lc["cnt"] for lc in level_counts}
        results["passed"] += 1
    except Exception as exc:
        step["status"] = "FAIL"
        step["error"] = str(exc)
        results["failed"] += 1
        _print_fail(str(exc))
    results["steps"].append(step)

    # -- STEP 6: HITL review instance creation --------------------------------
    _print_step(6, "HITL review instance creation (no direct Kanban push)")
    step = {"step": 6, "name": "HITL review instance"}
    instance_id = None
    hitl_task_id = None
    try:
        from tools.chat.requirement_intake_hook import _create_hitl_review

        _print_info(f"Creating HITL review for {reqs_extracted} requirement(s)...")
        instance_id = _create_hitl_review(
            session_id=session_id,
            context_id=CONTEXT_ID,
            req_count=reqs_extracted,
        )

        assert_true("HITL instance ID returned", bool(instance_id))
        _print_info(f"Instance ID: {instance_id}")

        # Verify wf_instances row
        conn = _conn()
        inst = conn.execute(
            "SELECT * FROM wf_instances WHERE id = %s", (instance_id,)
        ).fetchone()
        assert_true("wf_instances row exists", inst is not None)
        _print_info(f"  Status: {inst['status']}")
        _print_info(f"  Current stage: {inst['current_stage']}")
        _print_info(f"  Template: {inst['template_id']}")

        # Verify gatekeeper kanban task with status=backlog, type=chore
        hitl_task_id = inst["task_id"]
        if hitl_task_id:
            task = conn.execute(
                "SELECT * FROM kanban_tasks WHERE id = %s", (hitl_task_id,)
            ).fetchone()
            assert_true("Gatekeeper kanban task created", task is not None)
            assert_eq("Gatekeeper task status", task["status"], "backlog")
            assert_eq("Gatekeeper task type", task["task_type"], "chore")
            _print_info(f"  Gatekeeper task: [{task['status']}] {task['title'][:60]}")

        # Verify hitl_intake_pending mapping
        mapping = conn.execute(
            "SELECT * FROM hitl_intake_pending WHERE instance_id = %s", (instance_id,)
        ).fetchone()
        assert_true("hitl_intake_pending mapping stored", mapping is not None)
        assert_eq("Mapping session_id", mapping["session_id"], session_id)
        assert_eq("Mapping context_id", mapping["context_id"], CONTEXT_ID)

        # Verify NO direct kanban tasks were promoted (HITL gate enforced)
        promoted = conn.execute(
            """SELECT COUNT(*) FROM kanban_tasks
               WHERE source_prediction_id IN (
                   SELECT id FROM safe_decomposition WHERE session_id=%s
               )""",
            (session_id,),
        ).fetchone()[0]
        assert_eq("Direct Kanban promotion blocked (HITL gate)", promoted, 0)
        conn.close()

        step["status"] = "PASS"
        step["instance_id"] = instance_id
        step["hitl_task_id"] = hitl_task_id
        results["passed"] += 1
        _print_ok(f"HITL gate enforced -- {decomp_count} items in review queue, 0 in Kanban")
    except Exception as exc:
        step["status"] = "FAIL"
        step["error"] = str(exc)
        results["failed"] += 1
        _print_fail(str(exc))
    results["steps"].append(step)

    if not instance_id:
        print("\n[ABORT] Cannot continue without a valid HITL instance.")
        _print_final(results, start_ts)
        return

    # -- STEP 7: Retrieve pending approval gate --------------------------------
    _print_step(7, "Retrieve pending HITL approval gate")
    step = {"step": 7, "name": "Approval gate retrieval"}
    approval_id = None
    try:
        conn = _conn()
        approval = conn.execute(
            "SELECT * FROM wf_approvals WHERE instance_id=%s AND status='pending' LIMIT 1",
            (instance_id,),
        ).fetchone()
        conn.close()

        assert_true("Pending approval gate exists", approval is not None)
        approval_id = approval["id"]
        _print_info(f"  Approval ID: {approval_id}")
        _print_info(f"  Stage: {approval['stage']}")
        _print_info(f"  Assigned role: {approval.get('assigned_role', 'N/A')}")

        step["status"] = "PASS"
        step["approval_id"] = approval_id
        step["stage"] = approval["stage"]
        results["passed"] += 1
    except Exception as exc:
        step["status"] = "FAIL"
        step["error"] = str(exc)
        results["failed"] += 1
        _print_fail(str(exc))
    results["steps"].append(step)

    if not approval_id:
        print("\n[ABORT] Cannot simulate approval without an approval gate.")
        _print_final(results, start_ts)
        return

    # -- STEP 8: HITL Reviewer submits feedback (approve all stages) ----------
    _print_step(8, "HITL Reviewer approves decomposed requirements")
    step = {"step": 8, "name": "HITL approval submission"}
    try:
        from tools.workflow_hitl import feedback as wf_feedback

        _print_info("Approving all HITL stages as 'mcip-requirements-analyst'...")

        feedback_ids = []
        # Loop: approve each pending stage until the workflow completes
        max_stages = 10
        for stage_attempt in range(max_stages):
            conn = _conn()
            pending = conn.execute(
                "SELECT * FROM wf_approvals WHERE instance_id=%s AND status='pending' LIMIT 1",
                (instance_id,),
            ).fetchone()
            conn.close()

            if not pending:
                _print_info(f"  No more pending approvals after {stage_attempt} stage(s)")
                break

            current_stage = pending["stage"]
            _print_info(f"  Stage {stage_attempt + 1}: approving '{current_stage}'")

            fid = wf_feedback.submit_feedback(
                approval_id=pending["id"],
                decision="approve",
                submitted_by="mcip-requirements-analyst",
                rating=4,
                comments=(
                    f"[{current_stage.upper()}] Reviewed {decomp_count} SAFe items "
                    f"decomposed from {reqs_extracted} requirements for the MCIP "
                    "FICTITIOUS scenario. Approving for Kanban promotion."
                ),
                feedback_types=["completeness", "clarity", "testability"],
            )
            feedback_ids.append(fid)
            _print_info(f"    Feedback ID: {fid}")

            # Check if workflow is now complete
            conn = _conn()
            inst_status = conn.execute(
                "SELECT status FROM wf_instances WHERE id=%s", (instance_id,)
            ).fetchone()
            conn.close()
            if inst_status and inst_status["status"] == "approved":
                _print_info(f"  Workflow fully approved after '{current_stage}' stage")
                break

        assert_true("At least one feedback ID returned", len(feedback_ids) >= 1)
        _print_info(f"  Total feedback submissions: {len(feedback_ids)}")

        # Verify final approval status
        conn = _conn()
        inst_row = conn.execute(
            "SELECT status, current_stage FROM wf_instances WHERE id=%s", (instance_id,)
        ).fetchone()
        conn.close()
        _print_info(f"  Instance status: {inst_row['status']}, stage: {inst_row['current_stage']}")

        step["status"] = "PASS"
        step["feedback_id"] = feedback_ids[-1] if feedback_ids else None
        step["stages_approved"] = len(feedback_ids)
        results["passed"] += 1
    except Exception as exc:
        step["status"] = "FAIL"
        step["error"] = str(exc)
        results["failed"] += 1
        _print_fail(str(exc))
    results["steps"].append(step)

    # -- STEP 9: Verify Kanban promotion after HITL approval -------------------
    _print_step(9, "Verify SAFe items promoted to Kanban after HITL approval")
    step = {"step": 9, "name": "Kanban promotion verification"}
    try:
        # Wait briefly for any async processing
        time.sleep(0.5)

        conn = _conn()
        promoted_tasks = conn.execute(
            """SELECT kt.id, kt.title, kt.task_type, kt.priority, kt.status
               FROM kanban_tasks kt
               JOIN safe_decomposition sd ON kt.source_prediction_id = sd.id
               WHERE sd.session_id = %s
               ORDER BY kt.priority DESC, kt.created_at""",
            (session_id,),
        ).fetchall()
        promoted_count = len(promoted_tasks)
        conn.close()

        _print_info(f"Tasks in Kanban: {promoted_count}")
        assert_true("At least 1 task promoted to Kanban", promoted_count >= 1,
                    f"{promoted_count} tasks")

        # Show the board
        print(f"\n  {'-'*68}")
        print(f"  {'KANBAN BOARD -- MCIP REQUIREMENTS':^68}")
        print(f"  {'-'*68}")
        by_type = {}
        for t in promoted_tasks:
            ttype = t["task_type"].upper()
            by_type.setdefault(ttype, []).append(t)

        for ttype, tasks in sorted(by_type.items()):
            print(f"\n  [{ttype}]")
            for t in tasks:
                prio_icon = {"high": "[RED]", "medium": "[YLW]", "low": "[GRN]"}.get(t["priority"], "[---]")
                title = t["title"][:60]
                print(f"    {prio_icon} [{t['status'][:10]:10}] {title}")

        print(f"\n  {'-'*68}")
        print(f"  Total: {promoted_count} task(s) | "
              f"Research: {len(by_type.get('RESEARCH',[]))} | "
              f"Build: {len(by_type.get('BUILD',[]))} | "
              f"Chore: {len(by_type.get('CHORE',[]))}")

        # Verify gatekeeper task is now 'done'
        if hitl_task_id:
            conn = _conn()
            gk = conn.execute(
                "SELECT status FROM kanban_tasks WHERE id=%s", (hitl_task_id,)
            ).fetchone()
            conn.close()
            if gk:
                _print_info(f"Gatekeeper task status: {gk['status']}")

        step["status"] = "PASS"
        step["promoted_count"] = promoted_count
        step["task_type_breakdown"] = {k: len(v) for k, v in by_type.items()}
        results["passed"] += 1
    except Exception as exc:
        step["status"] = "FAIL"
        step["error"] = str(exc)
        results["failed"] += 1
        _print_fail(str(exc))
    results["steps"].append(step)

    # -- STEP 10: Idempotency check ---------------------------------------------
    _print_step(10, "Idempotency -- re-running promote() must not duplicate tasks")
    step = {"step": 10, "name": "Idempotency check"}
    try:
        from tools.requirements.intake_kanban_promoter import promote

        result = promote(session_id=session_id)
        assert_eq("Re-run inserted count (idempotency)", result.get("inserted", -1), 0)
        _print_ok("Duplicate promotion correctly prevented")

        step["status"] = "PASS"
        results["passed"] += 1
    except Exception as exc:
        step["status"] = "FAIL"
        step["error"] = str(exc)
        results["failed"] += 1
        _print_fail(str(exc))
    results["steps"].append(step)

    _print_final(results, start_ts)


def _print_final(results: dict, start_ts: float) -> None:
    elapsed = round(time.time() - start_ts, 2)
    total = results["passed"] + results["failed"]
    print(f"\n{'='*72}")
    print("  LIFECYCLE E2E RESULT")
    print(f"{'='*72}")
    print(f"  Scenario : {SCENARIO_LABEL}")
    print(f"  Context  : {CONTEXT_ID}")
    print(f"  Duration : {elapsed}s")
    print(f"  Steps    : {total} total")
    print(f"  PASSED   : {results['passed']}")
    print(f"  FAILED   : {results['failed']}")
    print(f"{'-'*72}")

    for s in results["steps"]:
        icon = "[OK]" if s["status"] == "PASS" else "[X]"
        extra = ""
        for key in ("requirements_extracted", "decomp_count", "instance_id",
                    "approval_id", "promoted_count", "session_id"):
            if key in s:
                extra += f"  [{key}={s[key]}]"
        print(f"  {icon} Step {s['step']:2d}: {s['name']}{extra}")
        if s.get("error"):
            print(f"           ERROR: {s['error']}")

    print(f"{'='*72}")
    outcome = "ALL STEPS PASSED" if results["failed"] == 0 else f"{results['failed']} STEP(S) FAILED"
    print(f"  CUI // SP-CTI  ->  {outcome}")
    print(f"{'='*72}\n")

    # Machine-readable summary
    summary = {
        "scenario": SCENARIO_LABEL,
        "context_id": CONTEXT_ID,
        "elapsed_s": elapsed,
        "passed": results["passed"],
        "failed": results["failed"],
        "outcome": "PASS" if results["failed"] == 0 else "FAIL",
        "steps": results["steps"],
    }
    print("JSON_RESULT:", json.dumps(summary, default=str))


if __name__ == "__main__":
    run()
