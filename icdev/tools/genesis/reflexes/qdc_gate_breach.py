# CUI // SP-CTI
"""Genesis Reflex — QDC Quality Gate Breach Detector (4h cadence).

Scans qdc_gate_results for gates with status='fail' or 'error', then
publishes 'qdc.gate.breached' events and emits kanban suggestions for
designs where quality has regressed.

Air-gap safe: no LLM calls — pure DB heuristics.
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger

from typing import Any, Dict

logger = get_logger(__name__)

CADENCE_HOURS = 4


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Find QDC gate results that indicate a breach (status != 'pass').

    Returns:
        gates_checked: int
        breached_gates: list of {design_id, gate_id, status, executed_at}
        events_published: int
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "gates_checked": 0,
        "breached_gates": [],
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.qdc_canvas.db.init_db import get_connection as qdc_conn
        conn_qdc = qdc_conn()
        try:
            # Latest result per (design_id, gate_id) to avoid duplicate alerts
            rows = conn_qdc.execute(
                """
                SELECT r.design_id, r.gate_id, r.status, r.executed_at
                FROM qdc_gate_results r
                INNER JOIN (
                    SELECT design_id, gate_id, MAX(executed_at) as max_at
                    FROM qdc_gate_results GROUP BY design_id, gate_id
                ) latest ON r.design_id=latest.design_id
                    AND r.gate_id=latest.gate_id
                    AND r.executed_at=latest.max_at
                WHERE r.status NOT IN ('pass', 'skip')
                ORDER BY r.executed_at DESC
                """
            ).fetchall()
        finally:
            conn_qdc.close()

        result["gates_checked"] = len(rows)
        breached = []
        for row in rows:
            breached.append({
                "design_id": row["design_id"] if isinstance(row, dict) else row[0],
                "gate_id": row["gate_id"] if isinstance(row, dict) else row[1],
                "status": row["status"] if isinstance(row, dict) else row[2],
                "executed_at": row["executed_at"] if isinstance(row, dict) else row[3],
            })

        result["breached_gates"] = breached

        if breached and not dry_run:
            try:
                from tools.canvas.event_bus import publish
                for g in breached:
                    publish("qdc", "qdc.gate.breached", {
                        "design_id": g["design_id"],
                        "gate_id": g["gate_id"],
                        "status": g["status"],
                        "executed_at": g["executed_at"],
                    })
                    result["events_published"] += 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

    except Exception as exc:
        logger.error("qdc_gate_breach reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    import json as _json
    print(_json.dumps(run({}), indent=2))
