# CUI // SP-CTI — BDC (Boundary & Supply Chain Canvas) twin adapter
"""Thin adapter over ``tools/boundary_canvas/twin.py``.

BDC emits rating bands (``green/amber/red/unknown``) which the canonical schema
normalizes to ``pass/warn/fail/unknown``. BDC's honesty invariants are preserved:
``unknown`` (nothing to score) is never green-washed, and the Chain-of-Debate
``cod_method`` (``heuristic`` vs ``llm_debate``) is carried through as the
violation ``method`` — the wrapper labels provenance, never upgrades it.
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class BDCTwinAdapter(TwinAdapter):
    canvas_key = "bdc"
    method = "heuristic"
    snapshot_table = "compliance_snapshots"
    snapshot_time_col = "taken_at"

    def _fleet_conn(self):
        from tools.boundary_canvas.twin import get_connection

        return get_connection()

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        from tools.boundary_canvas import twin as bdc_twin

        return bdc_twin.take_snapshot(target_id, framework_id=kwargs.get("framework_id", "FedRAMP Moderate"))

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """``delta`` is BDC's control list; each entry carries implementation_status."""
        from tools.boundary_canvas import twin as bdc_twin

        native = bdc_twin.simulate_delta(
            target_id,
            delta or [],
            framework_id=kwargs.get("framework_id", "FedRAMP Moderate"),
            baseline_snap_id=kwargs.get("baseline_snap_id"),
            use_cod=kwargs.get("use_cod", False),
        )
        provenance = native.get("cod_method") or self.method
        violations = [
            {
                "severity": v.get("severity", "high"),
                "category": "compliance",
                "recommendation": v.get("recommendation") or v.get("title") or "",
                "title": v.get("title"),
                "rule_id": v.get("id"),
                "method": provenance,
                "auto_fixable": False,
            }
            for v in native.get("violations", [])
        ]
        return self._wrap(
            target_id,
            native.get("verdict") or native.get("rating"),
            violations,
            simulation_id=native.get("simulation_id"),
            coverage_score=native.get("score"),
            extra={"compliance_delta": native.get("compliance_delta"), "cod_method": native.get("cod_method")},
        )
