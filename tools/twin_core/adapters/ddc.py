# CUI // SP-CTI — DDC (Data Design Canvas) twin adapter
"""Thin adapter over ``tools/data_canvas/twin.py``.

DDC's ``simulate_delta`` yields the verdict; its ``quality_gate`` yields the
grounded, lineage-backed violations (null-safety / referential-integrity /
classification-boundary). This adapter combines both into one canonical
envelope. Violations are lineage-grounded, so ``method='lineage-analysis'``.
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class DDCTwinAdapter(TwinAdapter):
    canvas_key = "ddc"
    method = "lineage-analysis"
    snapshot_table = "data_twin_snapshots"
    snapshot_time_col = "created_at"

    def _fleet_conn(self):
        from tools.data_canvas.db.init_db import get_connection

        return get_connection()

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        from tools.data_canvas import twin as ddc_twin

        return ddc_twin.take_snapshot(target_id, label=label, classification=kwargs.get("classification", "CUI"))

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """``delta`` is the native ``schema_changes`` list."""
        from tools.data_canvas import twin as ddc_twin

        changes = delta or []
        native = ddc_twin.simulate_delta(
            target_id, changes,
            classification=kwargs.get("classification", "CUI"),
            baseline_snap_id=kwargs.get("baseline_snap_id"),
        )
        gate = ddc_twin.quality_gate(target_id, changes, baseline_snap_id=kwargs.get("baseline_snap_id"))
        violations = [
            {
                "severity": v.get("severity", "high"),
                "category": "compliance",
                "recommendation": v.get("recommendation") or v.get("title") or "",
                "title": v.get("title"),
                "rule_id": v.get("type") or v.get("id"),
                "auto_fixable": False,
            }
            for v in gate.get("violations", [])
        ]
        return self._wrap(
            target_id,
            native.get("verdict"),
            violations,
            simulation_id=native.get("simulation_id"),
            coverage_score=native.get("coverage_score"),
            extra={"orphan_count": native.get("orphan_count"), "schema_drift": native.get("schema_drift"),
                   "gate": gate.get("gate")},
        )
