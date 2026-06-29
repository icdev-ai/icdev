# CUI // SP-CTI
"""IQE adapter for the TimesFM forecasting microservice.

Collections registered:
  forecast.jobs  — forecast_jobs table (status, source, context, created_at)
"""
from __future__ import annotations

from typing import Any

from icdev.tools.iqe.executor import register_collection


def forecast_jobs_adapter(conn: Any) -> list[dict]:
    """Return forecast job rows."""
    if conn is None:
        from icdev.tools.db.storage import get_connection  # noqa: PLC0415
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, source, context, input_rows, status, model_id, "
        "created_at, completed_at, error_message "
        "FROM forecast_jobs ORDER BY created_at DESC LIMIT 500"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


register_collection("forecast.jobs", forecast_jobs_adapter)
