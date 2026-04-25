# CUI // SP-CTI
"""FathomDesk API — trap event history endpoint.

Inline-route blueprint (routes are hardcoded with /fathomdesk/api/... prefix).
Register via _mount_inline(fathomdesk_api) in tools/dashboard/api/__init__.py.

GET /fathomdesk/api/traps
  ?ticker=AAPL       — filter by ticker (optional)
  ?trap_type=bull_trap — filter by pattern column (optional)
  ?limit=50          — max rows to return (default 50, max 200)

Returns: {"traps": [...], "count": N}
"""

from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

fathomdesk_api = Blueprint("fathomdesk_api", __name__)

_MAX_LIMIT = 200


@fathomdesk_api.route("/fathomdesk/api/traps", methods=["GET"])
def list_traps():
    """Return trap events from ad_trap_events, optionally filtered."""
    ticker = request.args.get("ticker", "").strip().upper() or None
    trap_type = request.args.get("trap_type", "").strip().lower() or None
    try:
        limit = min(int(request.args.get("limit", 50)), _MAX_LIMIT)
    except (ValueError, TypeError):
        limit = 50

    try:
        conn = get_connection()
        ph = "%s" if getattr(conn, "_dialect", "sqlite") == "postgresql" else "?"
        try:
            # Build WHERE clauses dynamically
            conditions = []
            params: list = []
            if ticker:
                conditions.append(f"ticker = {ph}")
                params.append(ticker)
            if trap_type:
                conditions.append(f"pattern = {ph}")
                params.append(trap_type)

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)
            rows = conn.execute(
                f"SELECT id, ticker, pattern, broken_level, breakout_bar, "  # nosec B608
                f"reentry_bar, confidence, volume_ratio, bar_age, timeframe, "
                f"evidence_json, created_at "
                f"FROM ad_trap_events {where} "
                f"ORDER BY created_at DESC LIMIT {ph}",
                params,
            ).fetchall()
            traps = [dict(r) for r in rows]
        finally:
            conn.close()
        return jsonify({"traps": traps, "count": len(traps)})
    except Exception as exc:
        return jsonify({"traps": [], "count": 0, "error": str(exc)}), 200
