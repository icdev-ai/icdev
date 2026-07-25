# CUI // SP-CTI — NDC (Network Design Canvas) twin adapter
"""Thin adapter over ``tools/network/twin.py``.

Wraps the network digital twin's snapshot/simulate/blast-radius surface into the
canonical twin_core schema. The underlying twin's intent evaluation is a
hardcoded heuristic, so ``method='heuristic'`` is carried onto every violation —
the wrapper labels provenance, it does not upgrade it.

NDC's native module has no ``list_snapshots``; this adapter reads
``network_twin_snapshots`` directly (read-only) so the uniform surface holds.
"""
from __future__ import annotations

from typing import Any

from tools.twin_core.registry import TwinAdapter, register_twin


@register_twin
class NDCTwinAdapter(TwinAdapter):
    canvas_key = "ndc"
    method = "heuristic"
    supports_snapshots = True
    supports_simulation = True
    snapshot_table = "network_twin_snapshots"
    snapshot_time_col = "created_at"

    def _fleet_conn(self):
        from tools.network.twin import _ensure_snapshots_table, _nc_conn

        conn = _nc_conn()
        _ensure_snapshots_table(conn)
        return conn

    def take_snapshot(self, target_id: str, label: str | None = None, **kwargs) -> dict:
        from tools.network import twin as ndc_twin

        return ndc_twin.take_snapshot(target_id, label=label)

    def list_snapshots(self, target_id: str, limit: int = 50, **kwargs) -> list[dict]:
        # NDC's twin has no list function; read the append-only snapshot table.
        from tools.network.twin import _nc_conn, _ensure_snapshots_table

        conn = _nc_conn()
        try:
            _ensure_snapshots_table(conn)
            rows = conn.execute(
                "SELECT id, project_id, label, device_count, link_count, created_at "
                "FROM network_twin_snapshots WHERE project_id=%s "
                "ORDER BY created_at DESC LIMIT %s",
                (target_id, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:  # noqa: BLE001 — honest empty on read failure
            return []
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def simulate_delta(self, target_id: str, delta: Any, **kwargs) -> dict:
        """Run a topology delta through the NDC twin and canonicalize the result.

        ``delta`` is the native ``topology_delta`` dict
        (``add_devices``/``remove_devices``/``add_links``/``remove_links``/``acl_changes``).
        """
        from tools.network import twin as ndc_twin

        intent_rules = kwargs.get("intent_rules")
        baseline_snap_id = kwargs.get("baseline_snap_id")
        native = ndc_twin.simulate_delta(
            target_id, delta or {}, intent_rules=intent_rules, baseline_snap_id=baseline_snap_id
        )
        violations = [
            {
                "severity": f.get("severity", "high"),
                "category": "network",
                "recommendation": f.get("recommendation") or f.get("title") or "",
                "title": f.get("title"),
                "rule_id": f.get("id"),
                "auto_fixable": False,
            }
            for f in native.get("compliance_findings", [])
        ]
        return self._wrap(
            target_id,
            native.get("verdict"),
            violations,
            simulation_id=native.get("id"),
            extra={"intent_results": native.get("intent_results", [])},
        )
