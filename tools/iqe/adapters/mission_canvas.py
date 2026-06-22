# CUI // SP-CTI
"""Mission Canvas — IQE adapter."""
from __future__ import annotations

from tools.iqe.registry import register_collection


def register_mission_canvas_collections() -> None:
    register_collection(
        key="mission_canvas.missions",
        label="Missions",
        description="Query active and historical mission records.",
        source_module="tools.mission_canvas",
    )
    register_collection(
        key="mission_canvas.objectives",
        label="Mission Objectives",
        description="Query mission objectives and task assignments.",
        source_module="tools.mission_canvas",
    )
    register_collection(
        key="mission_canvas.events",
        label="Mission Events",
        description="Query mission timeline events and status changes.",
        source_module="tools.mission_canvas",
    )


register_mission_canvas_collections()
