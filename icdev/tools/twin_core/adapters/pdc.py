# CUI // SP-CTI — PDC (Pipeline Design Canvas) twin adapter
"""Thin adapter over ``tools/pipeline/twin.py``.

Wraps the pipeline twin's snapshot/simulate surface into the canonical schema.
PDC has the strongest native hygiene (sha256 snapshot dedup + auto-snapshot
retention, task pdx-perf-01); this adapter passes those straight through — it
never bypasses dedup or writes its own snapshots. Antipattern findings map to
``security`` violations, compliance-rule failures to ``compliance``; provenance
is ``method='static-analysis'`` (PDC runs the antipattern detector + SLSA
assessor + compliance engine, not a heuristic guess).
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class PDCTwinAdapter(TwinAdapter):
    canvas_key = "pdc"
    method = "static-analysis"
    supports_snapshots = True
    supports_simulation = True
    snapshot_table = "pdc_snapshots"
    snapshot_time_col = "created_at"
    simulation_table = "pdc_simulations"
    simulation_time_col = "created_at"
    simulation_verdict_col = "verdict"

    def _fleet_conn(self):
        from tools.pipeline.twin import _get_connection

        return _get_connection()

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        from tools.pipeline import twin as pdc_twin

        return pdc_twin.take_snapshot(target_id, label=label, user_id=kwargs.get("user_id", "system"))

    def list_snapshots(self, target_id: str, limit: int = 100, **kwargs) -> list[dict]:
        from tools.pipeline import twin as pdc_twin

        try:
            return pdc_twin.list_snapshots(target_id, limit=limit)
        except Exception:  # noqa: BLE001
            return []

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """Run a pipeline delta graph through PDC analysis and canonicalize it.

        ``delta`` is the native ``delta_graph`` ({"nodes": [...], "edges": [...]}).
        """
        from tools.pipeline import twin as pdc_twin

        native = pdc_twin.simulate_delta(
            target_id,
            delta or {},
            baseline_snap_id=kwargs.get("baseline_snap_id"),
            user_id=kwargs.get("user_id", "system"),
        )
        violations: list[dict] = []
        for ap in native.get("antipatterns", []):
            violations.append({
                "severity": ap.get("severity", "high"),
                "category": "security",
                "recommendation": ap.get("recommendation") or ap.get("description") or ap.get("title") or "",
                "title": ap.get("title") or ap.get("name"),
                "rule_id": ap.get("id"),
                "auto_fixable": False,
            })
        compliance = native.get("compliance", {}) or {}
        for rule in compliance.get("failures", compliance.get("failed_rules", [])) or []:
            if isinstance(rule, dict):
                violations.append({
                    "severity": rule.get("severity", "medium"),
                    "category": "compliance",
                    "recommendation": rule.get("recommendation") or rule.get("title") or "",
                    "title": rule.get("title") or rule.get("id"),
                    "rule_id": rule.get("id"),
                    "auto_fixable": False,
                })
        violations, verdict = self._airgap_augment(delta or {}, violations, native.get("verdict"), kwargs)
        return self._wrap(
            target_id,
            verdict,
            violations,
            simulation_id=native.get("id"),
            snapshot_id=native.get("baseline_snap_id"),
            extra={
                "slsa": native.get("slsa"),
                "diff": native.get("diff"),
                "critical_count": native.get("critical_count"),
                "high_count": native.get("high_count"),
            },
        )

    def latest_status(self, target_id: str, **kwargs) -> dict:
        """Surface the latest persisted simulation verdict for the pipeline."""
        from tools.pipeline.twin import _get_connection

        snaps = self.list_snapshots(target_id, limit=1)
        verdict = "unknown"
        sim_id = None
        try:
            conn = _get_connection()
            row = conn.execute(
                "SELECT id, verdict FROM pdc_simulations WHERE pipeline_id=%s "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (target_id, 1),
            ).fetchone()
            conn.close()
            if row:
                d = dict(row)
                verdict = d.get("verdict") or "unknown"
                sim_id = d.get("id")
        except Exception:  # noqa: BLE001
            pass
        return {
            "canvas": self.canvas_key,
            "target_id": target_id,
            "verdict": verdict,
            "simulation_id": sim_id,
            "snapshot_count": len(snaps),
            "latest_snapshot": snaps[0] if snaps else None,
            "method": self.method,
        }
