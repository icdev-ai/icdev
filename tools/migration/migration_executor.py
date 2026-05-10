#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Migration Workflow — Migration Executor.

Pre-flight validation and execution checklist generator for migration waves.
Checks compliance gates, dependency resolution, and cutover readiness before
generating a sequenced execution plan.

Does NOT perform live system changes — produces an execution package (JSON)
that can be handed off to automation or an ops team.

Usage (called by workflow runner):
  python tools/migration/migration_executor.py --project-id default --json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _run_preflight(conn) -> list[dict]:
    """Run pre-flight checks and return gate results."""
    gates = []

    # Gate 1: At least one project defined
    project_count = conn.execute("SELECT COUNT(*) FROM mc_projects").fetchone()[0]
    gates.append({
        "gate": "Project Defined",
        "passed": project_count > 0,
        "detail": f"{project_count} project(s) found",
        "severity": "CAT1",
    })

    # Gate 2: Server inventory populated
    srv_count = conn.execute("SELECT COUNT(*) FROM mc_srv_inventory").fetchone()[0] \
        if _table_exists(conn, "mc_srv_inventory") else 0
    gates.append({
        "gate": "Server Inventory",
        "passed": srv_count > 0,
        "detail": f"{srv_count} server(s) in inventory",
        "severity": "CAT1",
    })

    # Gate 3: Wave plan exists
    wave_count = conn.execute("SELECT COUNT(*) FROM mc_wave_plans").fetchone()[0] \
        if _table_exists(conn, "mc_wave_plans") else 0
    mig_wave_count = conn.execute("SELECT COUNT(*) FROM mc_migration_waves").fetchone()[0] \
        if _table_exists(conn, "mc_migration_waves") else 0
    gates.append({
        "gate": "Wave Plan Exists",
        "passed": (wave_count + mig_wave_count) > 0,
        "detail": f"{wave_count + mig_wave_count} wave(s) defined",
        "severity": "CAT1",
    })

    # Gate 4: Compliance checks recorded
    comp_count = conn.execute("SELECT COUNT(*) FROM mc_compliance_checks").fetchone()[0] \
        if _table_exists(conn, "mc_compliance_checks") else 0
    gates.append({
        "gate": "Compliance Checks",
        "passed": comp_count > 0,
        "detail": f"{comp_count} compliance check(s) on record",
        "severity": "CAT2",
    })

    # Gate 5: No blocked waves
    blocked = conn.execute(
        "SELECT COUNT(*) FROM mc_migration_waves WHERE status='blocked'"
    ).fetchone()[0] if _table_exists(conn, "mc_migration_waves") else 0
    gates.append({
        "gate": "No Blocked Waves",
        "passed": blocked == 0,
        "detail": f"{blocked} wave(s) blocked" if blocked else "No blocked waves",
        "severity": "CAT2",
    })

    # Gate 6: Runbooks exist
    rb_count = conn.execute("SELECT COUNT(*) FROM mc_runbooks").fetchone()[0] \
        if _table_exists(conn, "mc_runbooks") else 0
    gates.append({
        "gate": "Runbooks Available",
        "passed": rb_count > 0,
        "detail": f"{rb_count} runbook(s) available",
        "severity": "CAT2",
    })

    return gates


def _build_execution_steps(conn) -> list[dict]:
    """Generate sequenced execution steps from waves."""
    steps = []
    waves = conn.execute(
        "SELECT wave_number, name, cutover_date, status "
        "FROM mc_migration_waves ORDER BY wave_number"
    ).fetchall() if _table_exists(conn, "mc_migration_waves") else []

    design_waves = conn.execute(
        "SELECT wave_number, name, strategy, status FROM mc_wave_plans ORDER BY wave_number"
    ).fetchall() if _table_exists(conn, "mc_wave_plans") else []

    seq = 1
    for w in design_waves:
        steps.append({
            "sequence": seq,
            "phase": "pre-migration",
            "action": f"Validate Wave {w[0]} — {w[1]}",
            "type": "validation",
            "strategy": w[2],
            "status": w[3],
        })
        seq += 1
        steps.append({
            "sequence": seq,
            "phase": "execution",
            "action": f"Execute Wave {w[0]} ({w[2] or 'rehost'})",
            "type": "migration",
            "strategy": w[2],
            "estimated_duration_hrs": {"lift_and_shift": 2, "rehost": 2, "replatform": 4,
                                       "refactor": 8, "rearchitect": 12}.get(w[2] or "", 4),
        })
        seq += 1

    for w in waves:
        steps.append({
            "sequence": seq,
            "phase": "cutover",
            "action": f"Cutover Wave {w[0]} — {w[1]}",
            "type": "cutover",
            "cutover_date": w[2],
            "current_status": w[3],
        })
        seq += 1

    if not steps:
        steps.append({
            "sequence": 1,
            "phase": "pre-migration",
            "action": "Define wave plans in Migration Canvas before executing",
            "type": "prerequisite",
        })

    return steps


def run(project_id: str = "default") -> dict:
    conn = _conn()
    try:
        gates = _run_preflight(conn)
        exec_steps = _build_execution_steps(conn)

        passed = sum(1 for g in gates if g["passed"])
        cat1_failed = [g for g in gates if not g["passed"] and g["severity"] == "CAT1"]
        cat2_failed = [g for g in gates if not g["passed"] and g["severity"] == "CAT2"]

        go_nogo = "GO" if not cat1_failed else "NO-GO"
        gate_score = round(passed / len(gates) * 100) if gates else 0

        return {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "go_nogo": go_nogo,
            "gate_score": gate_score,
            "gates_passed": passed,
            "gates_total": len(gates),
            "cat1_failures": len(cat1_failed),
            "cat2_failures": len(cat2_failed),
            "preflight_gates": gates,
            "execution_plan": exec_steps,
            "execution_step_count": len(exec_steps),
            "summary": (
                f"Pre-flight: {go_nogo} — {passed}/{len(gates)} gates passed "
                f"({len(cat1_failed)} CAT1 failures). "
                f"Execution plan: {len(exec_steps)} step(s) generated."
            ),
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Migration Executor — Pre-flight & Execution Plan")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run(args.project_id)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
