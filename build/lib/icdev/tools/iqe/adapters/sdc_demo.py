# CUI // SP-CTI
"""IQE SDC Demo collection adapters.

Importing this module registers four collections:
  sdc_demo.runs            — sdc_demo_runs table (run history)
  sdc_demo.scenarios       — static list of 3 SDC demo scenarios (A/B/C)
  sdc_demo.threat_summary  — before/after aggregates from sdc_compliance_timeline (canvas DB)
  sdc_demo.workflow_steps  — 12-step workflow runs from sdc_workflow_step_runs (canvas DB)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from tools.iqe.executor import register_collection

_CANVAS_DB = Path(__file__).resolve().parents[3] / "data" / "security_canvas.db"

_STATIC_SCENARIOS = [
    {
        "scenario_id": "A",
        "title": "Red Team: Enumerate Vulnerabilities",
        "audience": "exec+tech+engineer",
        "hook": "47 STRIDE findings → 15 CAT1 → 3 live attack paths → posture grade F",
        "duration_min": 5,
    },
    {
        "scenario_id": "B",
        "title": "12-Step Workflow + ISSO Approval Gate",
        "audience": "exec+tech",
        "hook": "Design → threat scan → STIG check → ISSO gate → IaC → deployed in 17h",
        "duration_min": 5,
    },
    {
        "scenario_id": "C",
        "title": "After State: Compliant + IaC Generated",
        "audience": "exec+tech+prospect",
        "hook": "0 CAT1, posture A, 87% NIST 800-53, $29K saved, cATO ready",
        "duration_min": 5,
    },
]


def _main_conn(conn: Any):
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
    return conn


def _canvas_conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_CANVAS_DB))
    c.row_factory = sqlite3.Row
    return c


def runs_adapter(conn: Any) -> list[dict]:
    """Return rows from sdc_demo_runs (main ICDEV DB)."""
    conn = _main_conn(conn)
    try:
        cur = conn.execute(
            "SELECT id, audience, scenarios_json, status, created_at "
            "FROM sdc_demo_runs ORDER BY created_at DESC LIMIT 50"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def scenarios_adapter(conn: Any) -> list[dict]:
    """Return the static list of 3 SDC demo scenarios."""
    return _STATIC_SCENARIOS


def threat_summary_adapter(conn: Any) -> list[dict]:
    """Return before/after compliance snapshots from sdc_compliance_timeline (canvas DB)."""
    try:
        cc = _canvas_conn()
        cur = cc.execute(
            """
            SELECT id, design_id, snapshot_label, cat1_count, cat2_count, cat3_count,
                   risk_score, posture_grade, controls_implemented, controls_total,
                   remediation_hours, snapshot_at
            FROM sdc_compliance_timeline
            ORDER BY design_id, snapshot_label
            LIMIT 100
            """
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        cc.close()
        return rows
    except Exception:
        return []


def workflow_steps_adapter(conn: Any) -> list[dict]:
    """Return 12-step workflow run records from sdc_workflow_step_runs (canvas DB)."""
    try:
        cc = _canvas_conn()
        cur = cc.execute(
            """
            SELECT id, design_id, step_id, step_name, status,
                   approved_by, approved_at, started_at, completed_at, created_at
            FROM sdc_workflow_step_runs
            ORDER BY design_id, step_id
            LIMIT 200
            """
        )
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        cc.close()
        return rows
    except Exception:
        return []


register_collection("sdc_demo.runs",           runs_adapter)
register_collection("sdc_demo.scenarios",       scenarios_adapter)
register_collection("sdc_demo.threat_summary",  threat_summary_adapter)
register_collection("sdc_demo.workflow_steps",  workflow_steps_adapter)
