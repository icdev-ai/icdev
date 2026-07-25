# CUI // SP-CTI — Mission (Mission Control Canvas) twin adapter
"""Thin adapter over ``tools/mission_canvas/twin.py``.

Mission is a thin aggregator twin: it composes the DDC + PDC twins rather than
owning a snapshot table of its own. ``take_snapshot`` returns the aggregated
sub-twin state; ``simulate_delta`` delegates to the DDC twin (schema-change
what-if scoped to the mission). No native snapshot table, so fleet snapshot
stats are unavailable (honestly reported as such).
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class MissionTwinAdapter(TwinAdapter):
    canvas_key = "mission_canvas"
    method = "aggregate"
    supports_snapshots = True
    # No dedicated snapshot table — Mission composes DDC + PDC sub-twins.
    snapshot_table = None

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        from tools.mission_canvas import twin as mission_twin

        return mission_twin.take_mission_twin(target_id, label=label)

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """``delta`` is a list of schema-change dicts (delegated to the DDC twin)."""
        from tools.mission_canvas import twin as mission_twin

        native = mission_twin.simulate_delta(target_id, delta or [])
        # The DDC-shaped result carries a verdict; downstream_impacts become
        # advisory context (no severity of their own) preserved in extra.
        return self._wrap(
            target_id,
            native.get("verdict"),
            [],
            simulation_id=native.get("simulation_id"),
            coverage_score=native.get("coverage_score"),
            extra={"downstream_impacts": native.get("downstream_impacts"),
                   "orphan_count": native.get("orphan_count")},
        )
