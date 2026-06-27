"""Process as Code — YAML export/import of chain+phases spec."""
from __future__ import annotations
import yaml
from datetime import datetime, timezone


def export_chain_yaml(chain_row: dict, phases_rows: list[dict]) -> str:
    """Serialise a chain + its phases to a portable YAML string."""
    spec: dict = {
        "chain": {
            "id": chain_row.get("id"),
            "name": chain_row.get("name"),
            "description": chain_row.get("description") or "",
            "status": chain_row.get("status") or "active",
            "created_at": (chain_row.get("created_at") or "").isoformat()
                if hasattr(chain_row.get("created_at"), "isoformat") else chain_row.get("created_at"),
        },
        "phases": [],
    }
    for p in phases_rows:
        row = dict(p) if hasattr(p, "keys") else {}
        phase_yaml = row.get("workflow_snapshot_yaml") or ""
        try:
            wf = yaml.safe_load(phase_yaml) or {}
        except Exception:
            wf = {}
        spec["phases"].append({
            "phase_number": row.get("phase_number"),
            "phase_name": row.get("phase_name") or wf.get("workflow_name") or "",
            "workflow": wf,
            "phase_status": row.get("phase_status") or "pending",
        })
    return yaml.dump(spec, allow_unicode=True, sort_keys=False)


def import_chain_yaml(spec_yaml: str, conn) -> str:
    """Create a new chain from a YAML spec. Returns new chain_id."""
    import uuid
    from tools.db.storage import sql_placeholder, get_canvas_connection

    spec = yaml.safe_load(spec_yaml) or {}
    chain_spec = spec.get("chain") or {}
    phases_spec = spec.get("phases") or []

    cc = get_canvas_connection("ICDEV_WFC_ENABLED")
    cc_ph = sql_placeholder(cc)

    chain_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    cc.execute(
        f"""INSERT INTO wfc_process_chains (id, name, description, status, created_at, updated_at)
            VALUES ({cc_ph},{cc_ph},{cc_ph},{cc_ph},{cc_ph},{cc_ph})""",
        (chain_id, chain_spec.get("name") or "Imported Chain",
         chain_spec.get("description") or "", "active", now, now),
    )
    for phase in phases_spec:
        phase_yaml = yaml.dump(phase.get("workflow") or {}, allow_unicode=True)
        phase_id = str(uuid.uuid4())
        cc.execute(
            f"""INSERT INTO wfc_chain_phases
                (id, chain_id, phase_number, phase_name, workflow_snapshot_yaml, phase_status, created_at)
                VALUES ({cc_ph},{cc_ph},{cc_ph},{cc_ph},{cc_ph},{cc_ph},{cc_ph})""",
            (phase_id, chain_id, phase.get("phase_number") or 1,
             phase.get("phase_name") or "", phase_yaml,
             phase.get("phase_status") or "pending", now),
        )
    cc.commit()
    cc.close()
    return chain_id


def export_chain_yaml_from_db(chain_id: str) -> str:
    """Load from DB and export."""
    from tools.db.storage import sql_placeholder, get_canvas_connection
    cc = get_canvas_connection("ICDEV_WFC_ENABLED")
    cc_ph = sql_placeholder(cc)
    chain_row = cc.execute(
        f"SELECT * FROM wfc_process_chains WHERE id = {cc_ph}", (chain_id,)
    ).fetchone()
    if not chain_row:
        cc.close()
        raise ValueError(f"Chain {chain_id} not found")
    phases_rows = cc.execute(
        f"SELECT * FROM wfc_chain_phases WHERE chain_id = {cc_ph} ORDER BY phase_number",
        (chain_id,),
    ).fetchall()
    cc.close()
    return export_chain_yaml(
        dict(chain_row) if hasattr(chain_row, "keys") else {},
        [dict(p) if hasattr(p, "keys") else {} for p in phases_rows],
    )
