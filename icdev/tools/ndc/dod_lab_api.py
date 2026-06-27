# CUI // SP-CTI
"""DoD BCAP Lab Demo API — launches 6-scenario demo in Studio Execution Runner.

POST /api/ndc/lab/demo/run  → creates/finds the DoD workflow, starts a run,
                               returns {"ok": true, "run_id": "...", "redirect": "/studio/execution/..."}
GET  /api/ndc/lab/demo/status → recent runs for the DoD demo workflow
"""

from __future__ import annotations

import textwrap

from flask import Blueprint, jsonify

from tools.db.storage import get_connection

_WORKFLOW_ID = "wf-dod-lab-demo"

_WORKFLOW_YAML = textwrap.dedent("""\
  steps:
    - id: s1_seed
      name: "S1: NIPR to AWS GovCloud (seed data)"
      tool: tools/ndc/dod_lab_demo_runner.py
      args:
        seed: true
        scenario: 1
      inject_project_id: false
      inject_run_id: false
      json_output: true
      required: true
      timeout: 120

    - id: s2
      name: "S2: ISP Redundancy + AI Predictive Failover"
      tool: tools/ndc/dod_lab_demo_runner.py
      args:
        scenario: 2
      depends_on: [s1_seed]
      inject_project_id: false
      inject_run_id: false
      json_output: true
      required: false
      timeout: 120

    - id: s3
      name: "S3: BCAP Threat Detection + Autonomous SOC"
      tool: tools/ndc/dod_lab_demo_runner.py
      args:
        scenario: 3
      depends_on: [s1_seed]
      inject_project_id: false
      inject_run_id: false
      json_output: true
      required: false
      timeout: 120

    - id: s4
      name: "S4: JWICS Cross-Domain Guard + AI Approval"
      tool: tools/ndc/dod_lab_demo_runner.py
      args:
        scenario: 4
      depends_on: [s1_seed]
      inject_project_id: false
      inject_run_id: false
      json_output: true
      required: false
      timeout: 120

    - id: s5
      name: "S5: TCCM Zero-Trust Credential Management"
      tool: tools/ndc/dod_lab_demo_runner.py
      args:
        scenario: 5
      depends_on: [s1_seed]
      inject_project_id: false
      inject_run_id: false
      json_output: true
      required: false
      timeout: 120

    - id: s6
      name: "S6: Agentic AI Modernization Advisor (Kanban+Qwen3)"
      tool: tools/ndc/dod_lab_demo_runner.py
      args:
        scenario: 6
      depends_on: [s1_seed]
      inject_project_id: false
      inject_run_id: false
      json_output: true
      required: false
      timeout: 180
""")

dod_lab_api = Blueprint("dod_lab_api", __name__, url_prefix="/api/ndc/lab")


def _ensure_workflow() -> None:
    """Upsert the DoD demo workflow so it always reflects current YAML."""
    from tools.studio.workflow_editor import get_workflow, save_workflow  # noqa: PLC0415

    existing = get_workflow(_WORKFLOW_ID)
    if not existing:
        save_workflow(
            _WORKFLOW_ID,
            "DoD BCAP Lab Demo",
            _WORKFLOW_YAML,
            category="ndc",
            description=(
                "6-scenario DoD BCAP/NIPR/JWICS/CSP AI/ML demo "
                "with Kanban + Qwen3. Runs in Studio Execution Runner."
            ),
            created_by="dod_lab_api",
        )


@dod_lab_api.route("/demo/run", methods=["POST"])
def run_demo():
    """Launch the DoD BCAP demo workflow. Returns run_id for the execution viewer."""
    try:
        _ensure_workflow()
        from tools.studio.workflow_runner import start_run  # noqa: PLC0415

        run_id = start_run(_WORKFLOW_ID, project_id="ndc-dod-lab")
        return jsonify({
            "ok": True,
            "run_id": run_id,
            "redirect": f"/studio/execution/{run_id}",
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@dod_lab_api.route("/demo/status", methods=["GET"])
def demo_status():
    """Return the 5 most recent DoD demo runs."""
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT run_id, status, started_at, completed_at, summary_json "
                "FROM studio_workflow_runs "
                "WHERE workflow_id = %s ORDER BY started_at DESC LIMIT 5",
                (_WORKFLOW_ID,),
            ).fetchall()
            runs = [dict(r) for r in rows]
        finally:
            conn.close()
        return jsonify({"ok": True, "workflow_id": _WORKFLOW_ID, "recent_runs": runs})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
