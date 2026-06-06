# CUI // SP-CTI
"""CCC — overview aggregator for the dashboard."""
from __future__ import annotations

from datetime import datetime, timezone


def get_ccc_overview(conn=None) -> dict:
    """Return counts and health summary for the CCC overview card."""
    close = False
    if conn is None:
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        close = True
    try:
        def _q(sql, params=()):
            try:
                row = conn.execute(sql, params).fetchone()
                return dict(row) if row else {}
            except Exception:
                return {}

        def _scalar(sql, params=(), default=0):
            try:
                row = conn.execute(sql, params).fetchone()
                return row[0] if row and row[0] is not None else default
            except Exception:
                return default

        total_circuits    = _scalar("SELECT COUNT(*) FROM ccc_circuits")
        active_circuits   = _scalar("SELECT COUNT(*) FROM ccc_circuits WHERE status='active'")
        maint_circuits    = _scalar("SELECT COUNT(*) FROM ccc_circuits WHERE status='maintenance'")
        high_util         = _scalar("SELECT COUNT(*) FROM ccc_circuits WHERE utilization_pct >= 70")
        crit_util         = _scalar("SELECT COUNT(*) FROM ccc_circuits WHERE utilization_pct >= 85")
        total_xc          = _scalar("SELECT COUNT(*) FROM ccc_cross_connects")
        active_xc         = _scalar("SELECT COUNT(*) FROM ccc_cross_connects WHERE status='active'")
        pending_xc        = _scalar("SELECT COUNT(*) FROM ccc_cross_connects WHERE status IN ('pending_loa','installing')")
        open_loa          = _scalar("SELECT COUNT(*) FROM ccc_loa_requests WHERE status IN ('draft','submitted','approved')")
        total_capacity    = _scalar("SELECT COALESCE(SUM(bandwidth_gbps),0) FROM ccc_circuits WHERE status='active'")
        total_mrr         = _scalar("SELECT COALESCE(SUM(mrr_usd),0) FROM ccc_circuits WHERE status='active'")
        dwdm_spans        = _scalar("SELECT COUNT(*) FROM ccc_dwdm_spans")
        dwdm_degraded     = _scalar("SELECT COUNT(*) FROM ccc_dwdm_spans WHERE status='degraded'")
        expiring_90d      = _scalar(
            "SELECT COUNT(*) FROM ccc_circuits WHERE contract_end != '' "
            "AND contract_end <= date('now','+90 days') AND status='active'"
        )

        return {
            "circuits": {
                "total": total_circuits, "active": active_circuits,
                "maintenance": maint_circuits, "high_util": high_util,
                "critical_util": crit_util, "expiring_90d": expiring_90d,
            },
            "cross_connects": {
                "total": total_xc, "active": active_xc, "pending": pending_xc,
            },
            "loa": {"open": open_loa},
            "capacity": {
                "total_gbps": round(total_capacity, 2),
                "total_tbps": round(total_capacity / 1000, 3),
                "monthly_cost_usd": round(total_mrr, 2),
            },
            "dwdm": {"spans": dwdm_spans, "degraded": dwdm_degraded},
            "health": "critical" if crit_util > 0 else ("warn" if high_util > 0 else "ok"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if close:
            conn.close()
