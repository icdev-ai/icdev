# CUI // SP-CTI
"""Mission Canvas — Living Digital Twin wrapper.

Wraps data_canvas.twin and pipeline.twin to surface a unified
mission-environment twin snapshot.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from typing import Optional

logger = get_logger("icdev.mission_canvas.twin")


def take_mission_twin(mission_id: str, label: Optional[str] = None) -> dict:
    """Capture a twin snapshot for the current mission environment.

    Aggregates data lineage twin + pipeline twin state into one
    mission-level snapshot record.
    """
    result = {
        "mission_id": mission_id,
        "label": label or f"mission-{mission_id}",
        "data_twin": None,
        "pipeline_twin": None,
        "status": "ok",
    }
    try:
        from tools.data_canvas.twin import take_snapshot as data_take_snapshot

        result["data_twin"] = data_take_snapshot(
            design_id=mission_id,
            label=label,
            classification="CUI",
        )
    except Exception as exc:
        logger.warning("Data twin snapshot failed: %s", exc)
        result["data_twin"] = {"error": str(exc)}

    try:
        from tools.pipeline.twin import list_snapshots as pipe_list_snapshots

        result["pipeline_twin"] = pipe_list_snapshots(pipeline_id=mission_id)
    except Exception as exc:
        logger.warning("Pipeline twin snapshot failed: %s", exc)
        result["pipeline_twin"] = {"error": str(exc)}

    return result


def simulate_delta(mission_id: str, changes: list[dict]) -> dict:
    """Simulate downstream impact of proposed environment changes."""
    try:
        from tools.data_canvas.twin import simulate_delta as _sim

        return _sim(design_id=mission_id, schema_changes=changes, classification="CUI")
    except Exception as exc:
        logger.warning("Twin delta simulation failed: %s", exc)
        return {"mission_id": mission_id, "error": str(exc)}
