"""Critical-path chain simulator for Process-Ify."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta


_STEP_DEFAULTS = {
    "approval": 4.0,
    "review": 2.0,
    "execution": 8.0,
    "notify": 0.5,
    "default": 2.0,
}


def _estimate_step_hours(step: dict) -> float:
    """Estimate hours for a single step based on type keyword in title."""
    title = (step.get("title") or "").lower()
    for kw, hours in _STEP_DEFAULTS.items():
        if kw in title:
            return hours
    return _STEP_DEFAULTS["default"]


def simulate_chain(phases: list[dict]) -> dict:
    """
    Estimate timeline for a chain from its phases.

    phases — list of dicts with keys: name, workflow_id (optional), steps (list)
    Returns timeline per phase, critical path, estimated_completion_dt.
    """
    now_utc = datetime.now(timezone.utc)
    timeline: list[dict] = []
    cursor = now_utc
    total_hours = 0.0

    for i, phase in enumerate(phases):
        steps = phase.get("steps") or []
        phase_hours = sum(_estimate_step_hours(s) for s in steps) or _STEP_DEFAULTS["default"]
        phase_start = cursor
        phase_end = cursor + timedelta(hours=phase_hours)

        # Identify critical step (longest)
        critical_step = max(steps, key=_estimate_step_hours) if steps else None
        timeline.append({
            "phase_number": i + 1,
            "phase_name": phase.get("name") or f"Phase {i + 1}",
            "estimated_hours": round(phase_hours, 1),
            "start_dt": phase_start.isoformat(),
            "end_dt": phase_end.isoformat(),
            "step_count": len(steps),
            "critical_step": critical_step.get("title") if critical_step else None,
        })
        cursor = phase_end
        total_hours += phase_hours

    return {
        "phase_count": len(phases),
        "total_estimated_hours": round(total_hours, 1),
        "total_estimated_days": round(total_hours / 8, 1),
        "estimated_completion_dt": cursor.isoformat(),
        "timeline": timeline,
        "generated_at": now_utc.isoformat(),
    }


def simulate_chain_from_db(chain_id: str) -> dict:
    """Load chain phases from DB and run simulation."""
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

    phases: list[dict] = []
    for p in phases_rows:
        row = dict(p) if hasattr(p, "keys") else {}
        import yaml
        wf_yaml = row.get("workflow_snapshot_yaml") or "{}"
        try:
            wf = yaml.safe_load(wf_yaml) or {}
        except Exception:
            wf = {}
        phases.append({
            "name": row.get("phase_name") or wf.get("workflow_name") or f"Phase {row.get('phase_number', '')}",
            "steps": wf.get("steps") or [],
        })

    result = simulate_chain(phases)
    result["chain_id"] = chain_id
    result["chain_name"] = dict(chain_row).get("name") if hasattr(chain_row, "keys") else ""
    return result
