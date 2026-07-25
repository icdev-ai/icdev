# CUI // SP-CTI — IDC (Infrastructure Design Canvas) IaC-twin adapter
"""Thin adapter over the IDC IaC twin (flat files in ``tools/infra_canvas/``).

Reality note (corrects stale source docs): the IDC twin is NOT a
``tools/infra_canvas/twin/`` package — it is flat files
(``snapshot_writer.py``, ``preapply_gate.py``, ``importers/``, ``emitters/``).
There is no ``cross_csp.py`` / ``compliance_gate.py``. See
``tools/manifest/idc-twin.md``.

The twin is snapshot-by-graph + a pre-apply gate rather than the
``take_snapshot(id)`` / ``simulate_delta(id, delta)`` shape of the other twins:

* ``take_snapshot`` requires a ``graph=`` (terraform-show JSON) — IDC has no
  "current graph by project id" to freeze, so we surface an honest error when
  it's absent rather than fabricate one.
* ``simulate_delta`` runs the pre-apply compliance gate over a Terraform
  ``plan_json`` delta and canonicalizes the STIG-CAT violations.
Provenance: ``method='iqe-gate'`` (IQE seed checks over planned rows).
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class IDCTwinAdapter(TwinAdapter):
    canvas_key = "idc"
    method = "iqe-gate"
    snapshot_table = "idc_twin_snapshots"
    snapshot_time_col = "created_at"
    violation_table = "idc_twin_violations"
    violation_severity_col = "severity"

    def _fleet_conn(self):
        from tools.db.storage import get_connection

        return get_connection()

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        """Persist an IDC graph snapshot. Requires ``graph=`` (terraform-show JSON)."""
        graph = kwargs.get("graph")
        if graph is None:
            raise ValueError(
                "IDC take_snapshot requires graph= (terraform-show JSON); "
                "IDC has no current-graph-by-id to freeze"
            )
        from tools.infra_canvas.snapshot_writer import write_snapshot

        snap_id = write_snapshot(
            graph,
            source=label or target_id or "twin_core",
            classification=kwargs.get("classification", "CUI"),
        )
        return {"id": snap_id, "snapshot_id": snap_id, "source": label or target_id,
                "node_count": len(graph.get("nodes", []))}

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """``delta`` is a parsed Terraform ``plan -json`` object."""
        from tools.infra_canvas.preapply_gate import run_gate

        native = run_gate(delta or {})
        violations = [
            {
                "severity": v.get("severity", "CAT3"),
                "category": "compliance",
                "recommendation": v.get("detail") or v.get("check") or "",
                "title": v.get("check"),
                "rule_id": v.get("check"),
                "auto_fixable": False,
                "detail": ", ".join(str(a) for a in v.get("affected", [])) or None,
            }
            for v in native.get("violations", [])
        ]
        return self._wrap(
            target_id,
            native.get("gate"),
            violations,
            extra={"delta": native.get("delta")},
        )
