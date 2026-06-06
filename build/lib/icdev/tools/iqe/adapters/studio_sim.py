# CUI // SP-CTI
"""IQE Studio Simulation adapter — exposes per-canvas simulation status.

Collections exposed:
    sim.statuses        — last simulation result per canvas (gate, probes, traffic, training)
    sim.training_pairs  — count of training_pair.json files per canvas artifact dir
    sim.probes          — aggregated probe pass/fail counts across all canvases
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.iqe.adapters import IQEAdapter

_COLLECTIONS = ["sim.statuses", "sim.training_pairs", "sim.probes"]


class StudioSimAdapter(IQEAdapter):
    """IQE adapter for the Studio Simulation Hub."""

    def list_collections(self) -> list[str]:
        return _COLLECTIONS

    def get_collection(self, collection: str, topology_id: str | None = None) -> list[dict]:
        from tools.studio.sim.sim_hub import get_all_canvas_statuses

        statuses = get_all_canvas_statuses()

        if collection == "sim.statuses":
            return statuses

        if collection == "sim.training_pairs":
            return [
                {"canvas": s["canvas"], "label": s["label"],
                 "training_examples": s["training_examples"],
                 "gate": s["gate"]}
                for s in statuses
            ]

        if collection == "sim.probes":
            return [
                {"canvas": s["canvas"], "label": s["label"],
                 "probes_passed": s["probes_passed"],
                 "probes_total": s["probes_total"],
                 "probes_failed": s["probes_total"] - s["probes_passed"]}
                for s in statuses
                if s["probes_total"] > 0
            ]

        return []


_adapter = StudioSimAdapter()


def get_adapter() -> StudioSimAdapter:
    return _adapter
# CUI // SP-CTI
