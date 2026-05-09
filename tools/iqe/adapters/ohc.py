# CUI // SP-CTI
"""IQE Ops Hub Canvas collection adapters.

Importing this module registers six collections on the module-level Executor:
  ohc.experiments     — Experiment tracking (ohc_experiments); filter by name, classification.
  ohc.runs            — Experiment run metrics (ohc_experiment_runs); filter by status, experiment_id.
  ohc.models          — Model registry (ohc_model_registry); filter by stage, framework.
  ohc.datasets        — Dataset versions (ohc_dataset_versions); filter by name, drift_score.
  ohc.adapters        — Adapter health status (ohc_adapter_status); filter by domain, available.
  ohc.drift_events    — Data drift events (ohc_data_drift_events); filter by dataset_name, passed.

Note: tables are created by tools/ops_hub/db/init_db.py (ohc_canvas.db). If tables are
absent the adapters return [] gracefully rather than raising an exception.
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _ohc_conn(conn: Any) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    from tools.ops_hub.db.init_db import get_connection  # noqa: PLC0415
    return get_connection(), True


def _safe_fetch(conn: Any, sql: str) -> list[dict]:
    """Execute sql and return rows; return [] if the table does not exist yet."""
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []


def experiments_adapter(conn: Any) -> list[dict]:
    """Return rows from ohc_experiments."""
    c, owned = _ohc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, name, description, tags_json, classification, "
            "created_at, updated_at "
            "FROM ohc_experiments ORDER BY created_at DESC",
        )
    finally:
        if owned:
            c.close()


def runs_adapter(conn: Any) -> list[dict]:
    """Return rows from ohc_experiment_runs."""
    c, owned = _ohc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, experiment_id, run_name, status, start_time, end_time, "
            "duration_ms, params_json, metrics_json, tags_json, artifacts_json, "
            "source_adapter, classification, created_at, updated_at "
            "FROM ohc_experiment_runs ORDER BY created_at DESC LIMIT 200",
        )
    finally:
        if owned:
            c.close()


def models_adapter(conn: Any) -> list[dict]:
    """Return rows from ohc_model_registry."""
    c, owned = _ohc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, name, version, stage, description, run_id, "
            "artifact_path, model_type, framework, metrics_json, tags_json, "
            "source_adapter, classification, created_at, updated_at "
            "FROM ohc_model_registry ORDER BY created_at DESC",
        )
    finally:
        if owned:
            c.close()


def datasets_adapter(conn: Any) -> list[dict]:
    """Return rows from ohc_dataset_versions."""
    c, owned = _ohc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, name, version, description, path, size_bytes, row_count, "
            "schema_json, drift_score, quality_score, tags_json, source_adapter, "
            "classification, created_at "
            "FROM ohc_dataset_versions ORDER BY created_at DESC",
        )
    finally:
        if owned:
            c.close()


def adapters_adapter(conn: Any) -> list[dict]:
    """Return rows from ohc_adapter_status."""
    c, owned = _ohc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, adapter_name, adapter_type, domain, available, version, "
            "endpoint, last_probe, probe_result, classification, created_at, updated_at "
            "FROM ohc_adapter_status ORDER BY domain, adapter_name",
        )
    finally:
        if owned:
            c.close()


def drift_events_adapter(conn: Any) -> list[dict]:
    """Return rows from ohc_data_drift_events."""
    c, owned = _ohc_conn(conn)
    try:
        return _safe_fetch(
            c,
            "SELECT id, dataset_name, drift_type, drift_score, threshold, passed, "
            "details_json, source_adapter, classification, detected_at "
            "FROM ohc_data_drift_events ORDER BY detected_at DESC LIMIT 100",
        )
    finally:
        if owned:
            c.close()


register_collection("ohc.experiments", experiments_adapter)
register_collection("ohc.runs", runs_adapter)
register_collection("ohc.models", models_adapter)
register_collection("ohc.datasets", datasets_adapter)
register_collection("ohc.adapters", adapters_adapter)
register_collection("ohc.drift_events", drift_events_adapter)
