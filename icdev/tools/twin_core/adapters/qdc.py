# CUI // SP-CTI — QDC (Quality Design Canvas) twin adapter
"""Thin adapter over ``tools/qdc_canvas/twin.py`` (new in twx-cov-01).

Verdict/violations are grounded in the real gate engine (the twin reuses the
qdc_gate_breach reflex read), so ``method='gate-analysis'``.
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class QDCTwinAdapter(TwinAdapter):
    canvas_key = "qdc"
    method = "gate-analysis"
    snapshot_table = "qdc_twin_snapshots"
    snapshot_time_col = "created_at"
    simulation_table = "qdc_simulations"
    simulation_time_col = "created_at"
    simulation_verdict_col = "verdict"

    def _fleet_conn(self):
        from tools.qdc_canvas.db.init_db import get_connection

        return get_connection()

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        from tools.qdc_canvas import twin as qdc_twin

        return qdc_twin.take_snapshot(target_id, label=label, user_id=kwargs.get("user_id", "system"))

    def list_snapshots(self, target_id: str, limit: int = 100, **kwargs) -> list[dict]:
        from tools.qdc_canvas import twin as qdc_twin

        try:
            return qdc_twin.list_snapshots(target_id, limit=limit)
        except Exception:  # noqa: BLE001
            return []

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        from tools.qdc_canvas import twin as qdc_twin

        native = qdc_twin.simulate_delta(
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
