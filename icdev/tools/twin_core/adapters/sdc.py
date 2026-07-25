# CUI // SP-CTI — SDC (Security Design Canvas) twin adapter
"""Thin adapter over ``tools/security_canvas/twin.py``.

Maps enumerated attack paths to canonical ``security`` violations. Risk/STRIDE
scoring is a boolean heuristic, so ``method='heuristic'`` is carried through.
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class SDCTwinAdapter(TwinAdapter):
    canvas_key = "sdc"
    method = "heuristic"
    snapshot_table = "sdc_attack_snapshots"
    snapshot_time_col = "created_at"

    def _fleet_conn(self):
        from tools.security_canvas.twin import get_connection

        return get_connection()

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        from tools.security_canvas import twin as sdc_twin

        return sdc_twin.take_snapshot(target_id, label=label)

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """``delta`` is the native ``delta_graph`` ({"nodes": [...], "edges": [...]})."""
        from tools.security_canvas import twin as sdc_twin

        native = sdc_twin.simulate_delta(
            target_id,
            delta or {},
            entry_point=kwargs.get("entry_point"),
            target_goal=kwargs.get("target_goal"),
            baseline_snap_id=kwargs.get("baseline_snap_id"),
        )
        violations = [
            {
                "severity": p.get("severity", "high"),
                "category": "security",
                "recommendation": p.get("description") or "Harden or remove this attack path",
                "title": p.get("description"),
                "rule_id": p.get("path_id"),
                "auto_fixable": False,
                "detail": " -> ".join(str(x) for x in p.get("path", [])),
            }
            for p in native.get("attack_paths", [])
        ]
        return self._wrap(
            target_id,
            native.get("verdict"),
            violations,
            simulation_id=native.get("simulation_id"),
            extra={"risk_score": native.get("risk_score"), "stride_coverage": native.get("stride_coverage")},
        )
