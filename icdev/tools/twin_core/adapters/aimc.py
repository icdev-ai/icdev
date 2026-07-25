# CUI // SP-CTI — AIML/AIMC (AI/ML Design Canvas) twin adapter
"""Thin adapter over ``tools/aiml_canvas/twin.py`` (wave-2, twx-cov-02).

Verdict/violations are grounded in the canvas's real AI-governance assessments
(the twin reuses ``aiml_assessments``), so ``method='assessment-analysis'``.
Registry key is ``aimc`` (component_registry canvas key for the AI/ML Canvas).
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class AIMLTwinAdapter(TwinAdapter):
    canvas_key = "aimc"
    method = "assessment-analysis"
    snapshot_table = "aiml_twin_snapshots"
    snapshot_time_col = "created_at"
    simulation_table = "aiml_simulations"
    simulation_time_col = "created_at"
    simulation_verdict_col = "verdict"

    def _fleet_conn(self):
        from tools.aiml_canvas.db.init_db import get_connection

        return get_connection()

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        from tools.aiml_canvas import twin as aiml_twin

        return aiml_twin.take_snapshot(target_id, label=label, user_id=kwargs.get("user_id", "system"))

    def list_snapshots(self, target_id: str, limit: int = 100, **kwargs) -> list[dict]:
        from tools.aiml_canvas import twin as aiml_twin

        try:
            return aiml_twin.list_snapshots(target_id, limit=limit)
        except Exception:  # noqa: BLE001
            return []

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        from tools.aiml_canvas import twin as aiml_twin

        native = aiml_twin.simulate_delta(
            target_id, delta or {},
            baseline_snap_id=kwargs.get("baseline_snap_id"),
            user_id=kwargs.get("user_id", "system"),
        )
        violations = [
            {"severity": f.get("severity", "high"), "category": f.get("category", "compliance"),
             "recommendation": f.get("recommendation") or f.get("title") or "",
             "title": f.get("title"), "rule_id": f.get("id"), "auto_fixable": False}
            for f in native.get("findings", [])
        ]
        return self._wrap(target_id, native.get("verdict"), violations,
                          simulation_id=native.get("id"), extra={"diff": native.get("diff")})
