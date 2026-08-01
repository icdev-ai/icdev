# CUI // SP-CTI — ODC (Observability Design Canvas) twin adapter
"""Thin adapter over ``tools/observability_canvas/twin.py``.

Maps coverage gaps to canonical ``security`` violations (monitoring/detection
coverage is a security control family, NIST AU/SI). ODC figures are heuristic
estimates (``estimate=True``), so ``method='heuristic-estimate'`` is carried
through and the projection ``basis`` string is preserved in ``extra``.
ODC is one of two twins (with PDC) that ships a native ``list_snapshots``.
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class ODCTwinAdapter(TwinAdapter):
    canvas_key = "odc"
    method = "heuristic-estimate"
    snapshot_table = "odc_twin_snapshots"
    snapshot_time_col = "created_at"

    def _fleet_conn(self):
        from tools.observability_canvas.twin import _get_conn

        return _get_conn()

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        from tools.observability_canvas import twin as odc_twin

        return odc_twin.take_snapshot(target_id, label=label)

    def list_snapshots(self, target_id: str, limit: int = 20, **kwargs) -> list[dict]:
        from tools.observability_canvas import twin as odc_twin

        try:
            return odc_twin.list_snapshots(target_id, limit=limit)
        except Exception:  # noqa: BLE001
            return []

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """``delta`` is the native ``collector_delta`` (add_receivers/remove_exporters/services)."""
        from tools.observability_canvas import twin as odc_twin

        native = odc_twin.simulate_delta(
            target_id,
            delta or {},
            signals=kwargs.get("signals"),
            baseline_snap_id=kwargs.get("baseline_snap_id"),
        )
        violations = [
            {
                "severity": g.get("severity", "high"),
                "category": "security",
                "recommendation": g.get("recommendation") or g.get("title") or "",
                "title": g.get("title"),
                "rule_id": g.get("id"),
                "auto_fixable": False,
            }
            for g in native.get("gaps", [])
        ]
        return self._wrap(
            target_id,
            native.get("verdict"),
            violations,
            simulation_id=native.get("simulation_id"),
            coverage_score=native.get("projected_coverage_pct"),
            extra={"estimate": native.get("estimate"), "basis": native.get("basis"),
                   "slo_impact": native.get("slo_impact")},
        )
