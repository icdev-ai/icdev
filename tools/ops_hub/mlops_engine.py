# CUI // SP-CTI
"""OHC — MLOps Engine.

Experiment tracking CRUD, model registry, and dataset drift summary.
Uses OHC's own ohc_canvas.db tables + MLflow/Evidently adapters when available.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any


def _conn():
    from tools.ops_hub.db.init_db import get_connection
    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Experiments ───────────────────────────────────────────────────────────────

def create_experiment(name: str, description: str = "", tags: dict | None = None,
                      classification: str = "CUI") -> dict:
    """Create a new experiment (internal + optional MLflow sync)."""
    eid = str(uuid.uuid4())
    now = _now()
    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO ohc_experiments (id, name, description, tags_json, classification, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (eid, name, description, json.dumps(tags or {}), classification, now, now))
        conn.commit()
    finally:
        conn.close()

    # Mirror to MLflow if available
    try:
        from tools.ops_hub.adapter_registry import get_adapter
        mlflow = get_adapter("mlflow")
        if mlflow and mlflow.available():
            mlflow.push_event("create_experiment", {"name": name, "tags": tags or {}})
    except Exception:
        pass

    return {"id": eid, "name": name, "created_at": now}


def list_experiments(limit: int = 50) -> list[dict]:
    """List experiments ordered by creation time descending."""
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT id, name, description, classification, created_at,
                   (SELECT COUNT(*) FROM ohc_experiment_runs r WHERE r.experiment_id = e.id) AS run_count
            FROM ohc_experiments e
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_run(experiment_id: str, run_name: str = "", params: dict | None = None,
               tags: dict | None = None, classification: str = "CUI") -> dict:
    """Create a new experiment run."""
    rid = str(uuid.uuid4())
    now = _now()
    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO ohc_experiment_runs
                (id, experiment_id, run_name, status, start_time,
                 params_json, metrics_json, tags_json, source_adapter,
                 classification, created_at, updated_at)
            VALUES (?, ?, ?, 'running', ?, ?, '{}', ?, 'internal', ?, ?, ?)
        """, (rid, experiment_id, run_name, now,
              json.dumps(params or {}), json.dumps(tags or {}),
              classification, now, now))
        conn.commit()
    finally:
        conn.close()
    return {"id": rid, "experiment_id": experiment_id, "status": "running", "start_time": now}


def finish_run(run_id: str, metrics: dict, status: str = "completed") -> dict:
    """Mark a run complete and log its final metrics."""
    now = _now()
    conn = _conn()
    try:
        # Compute duration
        row = conn.execute("SELECT start_time FROM ohc_experiment_runs WHERE id=?", (run_id,)).fetchone()
        duration_ms = 0
        if row:
            try:
                from datetime import datetime as _dt
                start = _dt.fromisoformat(row["start_time"])
                end = _dt.fromisoformat(now)
                duration_ms = int((end - start).total_seconds() * 1000)
            except Exception:
                pass
        conn.execute("""
            UPDATE ohc_experiment_runs
            SET status=?, end_time=?, duration_ms=?, metrics_json=?, updated_at=?
            WHERE id=?
        """, (status, now, duration_ms, json.dumps(metrics), now, run_id))
        conn.commit()
    finally:
        conn.close()
    return {"run_id": run_id, "status": status, "end_time": now, "metrics": metrics}


def list_runs(experiment_id: str = "", status: str = "", limit: int = 50) -> list[dict]:
    """List runs, optionally filtered by experiment or status."""
    conn = _conn()
    try:
        query = "SELECT * FROM ohc_experiment_runs WHERE 1=1"
        params: list = []
        if experiment_id:
            query += " AND experiment_id=?"
            params.append(experiment_id)
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY start_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Model Registry ────────────────────────────────────────────────────────────

def register_model(name: str, version: str, run_id: str = "", artifact_path: str = "",
                   model_type: str = "llm", framework: str = "",
                   metrics: dict | None = None, tags: dict | None = None,
                   classification: str = "CUI") -> dict:
    """Register a model version in the OHC model registry."""
    mid = str(uuid.uuid4())
    now = _now()
    conn = _conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO ohc_model_registry
                (id, name, version, stage, run_id, artifact_path,
                 model_type, framework, metrics_json, tags_json,
                 source_adapter, classification, created_at, updated_at)
            VALUES (?, ?, ?, 'none', ?, ?, ?, ?, ?, ?, 'internal', ?, ?, ?)
        """, (mid, name, version, run_id, artifact_path, model_type, framework,
              json.dumps(metrics or {}), json.dumps(tags or {}), classification, now, now))
        conn.commit()
    finally:
        conn.close()

    # Mirror to MLflow if available
    try:
        from tools.ops_hub.adapter_registry import get_adapter
        mlflow = get_adapter("mlflow")
        if mlflow and mlflow.available():
            mlflow.push_event("register_model", {
                "name": name, "version": version,
                "run_id": run_id, "artifact_path": artifact_path,
            })
    except Exception:
        pass

    # Inspect ONNX metadata if the artifact is a .onnx file
    onnx_meta: dict = {}
    if artifact_path and artifact_path.endswith(".onnx"):
        try:
            from tools.ops_hub.adapter_registry import get_adapter as _ga
            onnx = _ga("onnx")
            if onnx and onnx.available():
                onnx_meta = onnx._inspect_model(artifact_path)
        except Exception:
            pass

    return {"id": mid, "name": name, "version": version, "stage": "none",
            **({"onnx_meta": onnx_meta} if onnx_meta else {})}


def transition_model_stage(name: str, version: str, stage: str) -> dict:
    """Move a model version to a new lifecycle stage."""
    from tools.ops_hub.constants import MODEL_STAGES
    if stage not in MODEL_STAGES:
        return {"error": f"invalid stage '{stage}'; valid: {MODEL_STAGES}"}
    now = _now()
    conn = _conn()
    try:
        rows = conn.execute(
            "UPDATE ohc_model_registry SET stage=?, updated_at=? WHERE name=? AND version=?",
            (stage, now, name, version)
        ).rowcount
        conn.commit()
    finally:
        conn.close()

    if rows == 0:
        return {"error": f"model {name}@{version} not found"}

    # Mirror to MLflow if available
    try:
        from tools.ops_hub.adapter_registry import get_adapter
        mlflow = get_adapter("mlflow")
        if mlflow and mlflow.available():
            mlflow.push_event("transition_stage", {
                "name": name, "version": version, "stage": stage,
            })
    except Exception:
        pass

    return {"name": name, "version": version, "stage": stage, "updated_at": now}


def list_models(stage: str = "", limit: int = 50) -> list[dict]:
    """List model registry entries."""
    conn = _conn()
    try:
        query = "SELECT * FROM ohc_model_registry WHERE 1=1"
        params: list = []
        if stage:
            query += " AND stage=?"
            params.append(stage)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Data Drift ────────────────────────────────────────────────────────────────

def get_data_drift_summary(dataset_name: str = "", limit: int = 20) -> list[dict]:
    """Return recent data drift events, optionally from Evidently adapter."""
    # Try Evidently adapter first
    try:
        from tools.ops_hub.adapter_registry import get_adapter
        ev = get_adapter("evidently")
        if ev and ev.available():
            resources = ev.list_resources("drift_events", dataset_name=dataset_name, limit=limit)
            if resources:
                return [r.to_dict() for r in resources]
    except Exception:
        pass

    # Fall back to OHC DB
    conn = _conn()
    try:
        query = "SELECT * FROM ohc_data_drift_events WHERE 1=1"
        params: list = []
        if dataset_name:
            query += " AND dataset_name=?"
            params.append(dataset_name)
        query += " ORDER BY detected_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── ONNX Model Inspection ─────────────────────────────────────────────────────

def list_onnx_models() -> list[dict]:
    """List ONNX model files from the configured model directory via the ONNX adapter."""
    try:
        from tools.ops_hub.adapter_registry import get_adapter
        onnx = get_adapter("onnx")
        if onnx and onnx.available():
            resources = onnx.list_resources("onnx_models")
            return [r.to_dict() for r in resources]
    except Exception:
        pass
    return []


# ── Summary ───────────────────────────────────────────────────────────────────

def get_mlops_summary() -> dict[str, Any]:
    """Roll-up for /ops/models overview card."""
    experiments = list_experiments(limit=5)
    runs = list_runs(status="running", limit=5)
    models_prod = list_models(stage="production", limit=5)
    drift = get_data_drift_summary(limit=3)
    failed_drift = [d for d in drift if not d.get("passed", True)]
    onnx_models = list_onnx_models()

    status = "healthy"
    if failed_drift:
        status = "degraded"
    if any(r.get("status") == "failed" for r in list_runs(status="failed", limit=1)):
        status = "degraded"

    return {
        "status": status,
        "active_experiments": len(experiments),
        "running_runs": len(runs),
        "models_in_production": len(models_prod),
        "drift_failures": len(failed_drift),
        "onnx_model_count": len(onnx_models),
        "recent_experiments": experiments[:3],
        "recent_runs": runs[:3],
        "onnx_models": onnx_models[:5],
        "domain": "mlops",
    }
