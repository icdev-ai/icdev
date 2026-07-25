# CUI // SP-CTI — AADC (Agentic AI Canvas) twin adapter
"""Thin adapter over ``tools/agentic_ai_canvas/twin.py`` (new in twx-cov-01).

Wraps the agent-failure-cascade simulation. Cascade impact is a graph
computation weighted by governance flags, so ``method='cascade-analysis'``.
Impacted agents map to ``security`` violations.
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class AADCTwinAdapter(TwinAdapter):
    canvas_key = "aadc"
    method = "cascade-analysis"
    snapshot_table = "aadc_twin_snapshots"
    snapshot_time_col = "created_at"
    simulation_table = "aadc_simulations"
    simulation_time_col = "created_at"
    simulation_verdict_col = "verdict"

    def _fleet_conn(self):
        from tools.agentic_ai_canvas.db.init_db import get_connection

        return get_connection()

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        from tools.agentic_ai_canvas import twin as aadc_twin

        return aadc_twin.take_snapshot(target_id, label=label, user_id=kwargs.get("user_id", "system"))

    def list_snapshots(self, target_id: str, limit: int = 100, **kwargs) -> list[dict]:
        from tools.agentic_ai_canvas import twin as aadc_twin

        try:
            return aadc_twin.list_snapshots(target_id, limit=limit)
        except Exception:  # noqa: BLE001
            return []

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """``delta`` names failing agents: {"fail_nodes": [...]} (aliases accepted)."""
        from tools.agentic_ai_canvas import twin as aadc_twin

        native = aadc_twin.simulate_delta(
            target_id, delta or {},
            baseline_snap_id=kwargs.get("baseline_snap_id"),
            user_id=kwargs.get("user_id", "system"),
        )
        violations = [
            {"severity": f.get("severity", "high"), "category": f.get("category", "security"),
             "recommendation": f.get("recommendation") or f.get("title") or "",
             "title": f.get("title"), "rule_id": f.get("id"), "auto_fixable": False}
            for f in native.get("findings", [])
        ]
        return self._wrap(target_id, native.get("verdict"), violations,
                          simulation_id=native.get("id"),
                          extra={"diff": native.get("diff"), "impacted_agents": native.get("impacted_agents")})
