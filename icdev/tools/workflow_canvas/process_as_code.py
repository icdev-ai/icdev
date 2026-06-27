"""Process as Code — export/import a chain as a clean YAML spec."""
from __future__ import annotations
import json


def export_chain_yaml(chain_id: str) -> str:
    """Export a chain + all its phases + linked workflow definitions as a YAML spec."""
    import yaml
    from tools.db.storage import get_canvas_connection, get_connection, sql_placeholder

    cc = get_canvas_connection("ICDEV_WFC_ENABLED")
    ph = sql_placeholder(cc)

    chain = cc.execute(
        f"SELECT * FROM wfc_process_chains WHERE id={ph}", (chain_id,)
    ).fetchone()
    if not chain:
        cc.close()
        raise ValueError(f"Chain {chain_id} not found")
    chain = dict(chain)

    phases = [
        dict(p) for p in cc.execute(
            f"SELECT * FROM wfc_chain_phases WHERE chain_id={ph} ORDER BY phase_number",
            (chain_id,),
        ).fetchall()
    ]
    cc.close()

    # Load workflow definitions for each phase
    conn = get_connection()
    ph2 = sql_placeholder(conn)
    spec = {
        "kind": "ProcessChain",
        "name": chain.get("name"),
        "description": chain.get("description"),
        "industry": chain.get("industry"),
        "status": chain.get("status"),
        "phases": [],
    }

    for phase in phases:
        wf_ids = json.loads(phase.get("workflow_ids") or "[]")
        workflows = []
        for wf_id in wf_ids:
            row = conn.execute(
                f"SELECT name, template_yaml FROM studio_workflows WHERE workflow_id={ph2}",
                (wf_id,),
            ).fetchone()
            if row:
                try:
                    wf_data = yaml.safe_load(row["template_yaml"] or "{}") or {}
                except Exception:
                    wf_data = {}
                workflows.append({
                    "workflow_id": wf_id,
                    "name": row["name"],
                    "definition": wf_data,
                })

        spec["phases"].append({
            "phase_number": phase.get("phase_number"),
            "name": phase.get("name"),
            "team_name": phase.get("team_name"),
            "team_role": phase.get("team_role"),
            "status": phase.get("status"),
            "unlock_threshold": phase.get("unlock_threshold"),
            "workflows": workflows,
        })

    conn.close()
    return yaml.dump(spec, allow_unicode=True, sort_keys=False, default_flow_style=False)


def import_chain_yaml(yaml_text: str) -> str:
    """Create a new chain (and phases) from a YAML spec. Returns the new chain_id."""
    import yaml
    from tools.db.storage import get_canvas_connection, get_connection, sql_placeholder
    from datetime import datetime, timezone
    import uuid

    spec = yaml.safe_load(yaml_text) or {}
    if spec.get("kind") != "ProcessChain":
        raise ValueError("YAML must have kind: ProcessChain")

    now = datetime.now(timezone.utc).isoformat()
    chain_id = f"chn-{uuid.uuid4().hex[:8]}"

    cc = get_canvas_connection("ICDEV_WFC_ENABLED")
    ph = sql_placeholder(cc)

    cc.execute(
        f"""INSERT INTO wfc_process_chains
            (id, name, description, industry, status, created_at, updated_at)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
        (chain_id, spec.get("name", "Imported Chain"),
         spec.get("description"), spec.get("industry"), "draft", now, now),
    )

    for phase_spec in spec.get("phases") or []:
        phase_id = f"phs-{uuid.uuid4().hex[:8]}"
        wf_ids = [w.get("workflow_id") for w in (phase_spec.get("workflows") or []) if w.get("workflow_id")]
        cc.execute(
            f"""INSERT INTO wfc_chain_phases
                (id, chain_id, phase_number, name, team_name, team_role,
                 workflow_ids, status, unlock_threshold, handoff_checklist, created_at, updated_at)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
            (phase_id, chain_id, phase_spec.get("phase_number", 1),
             phase_spec.get("name", "Phase"), phase_spec.get("team_name"),
             phase_spec.get("team_role"), json.dumps(wf_ids), "pending",
             phase_spec.get("unlock_threshold", 100), "[]", now, now),
        )

    cc.commit()
    cc.close()
    return chain_id
