# CUI // SP-CTI
"""IQE TimesFM Forecasting collection adapter.

Registers 1 collection on the module-level Executor:
  forecast.jobs — forecast_jobs rows (TimesFM time-series forecast requests)
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _forecast_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.db.storage import get_connection  # noqa: PLC0415
    return get_connection(), True


def jobs_adapter(conn: Any) -> list[dict]:
    """Return forecast_jobs rows, newest first."""
    c, owned = _forecast_conn(conn)
    try:
        cur = c.execute(
            "SELECT id, source, context, input_rows, status, model_id, "
            "error_message, created_at, updated_at, completed_at, classification "
            "FROM forecast_jobs ORDER BY created_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if owned:
            c.close()


register_collection("forecast.jobs", jobs_adapter)
