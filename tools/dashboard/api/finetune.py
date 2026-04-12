#!/usr/bin/env python3
# CUI // SP-CTI
"""Fine-Tuning API Blueprint — REST endpoints for fine-tuning dashboard (Phase 64 Extension).

Provides /api/finetune/* endpoints: datasets, jobs, models, evaluations,
labeling, training, promotion.
"""

import json
import os
import sqlite3
from tools.db.storage import get_connection
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None

finetune_api = Blueprint("finetune_api", __name__, url_prefix="/api/finetune")


def _get_db() -> sqlite3.Connection:
    if get_db_connection:
        return get_db_connection(DB_PATH)
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _safe_json(text: str, default=None):
    """Parse JSON safely."""
    try:
        return json.loads(text) if text else default
    except (json.JSONDecodeError, TypeError):
        return default


# ── Overview Stats ────────────────────────────────────────────


@finetune_api.route("/stats", methods=["GET"])
def stats():
    """Overview statistics for the fine-tuning dashboard."""
    try:
        conn = _get_db()
        datasets = conn.execute("SELECT COUNT(*) as cnt FROM ft_datasets").fetchone()
        jobs = conn.execute("SELECT COUNT(*) as cnt FROM ft_training_jobs").fetchone()
        active_jobs = conn.execute(
            "SELECT COUNT(*) as cnt FROM ft_training_jobs WHERE status IN ('pending','preparing','training','exporting','evaluating')"  # noqa: E501
        ).fetchone()
        models = conn.execute("SELECT COUNT(*) as cnt FROM ft_model_versions").fetchone()
        promoted = conn.execute("SELECT COUNT(*) as cnt FROM ft_model_versions WHERE status = 'promoted'").fetchone()
        active_overrides = conn.execute(
            "SELECT COUNT(*) as cnt FROM ft_active_models WHERE deactivated_at IS NULL"
        ).fetchone()
        evaluations = conn.execute("SELECT COUNT(*) as cnt FROM ft_evaluations").fetchone()
        conn.close()
        return jsonify(
            {
                "datasets": datasets["cnt"] if datasets else 0,
                "total_jobs": jobs["cnt"] if jobs else 0,
                "active_jobs": active_jobs["cnt"] if active_jobs else 0,
                "model_versions": models["cnt"] if models else 0,
                "promoted_models": promoted["cnt"] if promoted else 0,
                "active_overrides": active_overrides["cnt"] if active_overrides else 0,
                "evaluations": evaluations["cnt"] if evaluations else 0,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Datasets ──────────────────────────────────────────────────


@finetune_api.route("/datasets", methods=["GET"])
def list_datasets():
    """List all fine-tuning datasets."""
    try:
        conn = _get_db()
        rows = conn.execute("SELECT * FROM ft_datasets ORDER BY updated_at DESC").fetchall()
        conn.close()
        return jsonify({"datasets": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@finetune_api.route("/datasets/<dataset_id>", methods=["GET"])
def get_dataset(dataset_id):
    """Get dataset details with example count."""
    try:
        conn = _get_db()
        ds = conn.execute("SELECT * FROM ft_datasets WHERE id = ?", (dataset_id,)).fetchone()
        if not ds:
            conn.close()
            return jsonify({"error": "Dataset not found"}), 404
        examples = conn.execute(
            "SELECT * FROM ft_dataset_examples WHERE dataset_id = ? ORDER BY id DESC LIMIT 100",
            (dataset_id,),
        ).fetchall()
        conn.close()
        return jsonify(
            {
                "dataset": dict(ds),
                "examples": [dict(e) for e in examples],
                "example_count": len(examples),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Training Jobs ─────────────────────────────────────────────


@finetune_api.route("/jobs", methods=["GET"])
def list_jobs():
    """List all training jobs."""
    try:
        conn = _get_db()
        rows = conn.execute("SELECT * FROM ft_training_jobs ORDER BY created_at DESC").fetchall()
        conn.close()
        jobs = []
        for r in rows:
            j = dict(r)
            j["loss_history"] = _safe_json(j.get("loss_history", "[]"), [])
            j["hyperparams"] = _safe_json(j.get("hyperparams", "{}"), {})
            jobs.append(j)
        return jsonify({"jobs": jobs, "total": len(jobs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@finetune_api.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    """Get training job details with events."""
    try:
        conn = _get_db()
        job = conn.execute("SELECT * FROM ft_training_jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            conn.close()
            return jsonify({"error": "Job not found"}), 404
        events = conn.execute(
            "SELECT * FROM ft_training_job_events WHERE job_id = ? ORDER BY created_at DESC",
            (job_id,),
        ).fetchall()
        conn.close()
        j = dict(job)
        j["loss_history"] = _safe_json(j.get("loss_history", "[]"), [])
        j["hyperparams"] = _safe_json(j.get("hyperparams", "{}"), {})
        return jsonify(
            {
                "job": j,
                "events": [dict(e) for e in events],
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Models ────────────────────────────────────────────────────


@finetune_api.route("/models", methods=["GET"])
def list_models():
    """List all model versions."""
    try:
        conn = _get_db()
        rows = conn.execute("SELECT * FROM ft_model_versions ORDER BY created_at DESC").fetchall()
        conn.close()
        return jsonify({"models": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@finetune_api.route("/models/<model_id>", methods=["GET"])
def get_model(model_id):
    """Get model version details with evaluations and promotion history."""
    try:
        conn = _get_db()
        model = conn.execute("SELECT * FROM ft_model_versions WHERE id = ?", (model_id,)).fetchone()
        if not model:
            conn.close()
            return jsonify({"error": "Model not found"}), 404
        evals = conn.execute(
            "SELECT * FROM ft_evaluations WHERE model_version_id = ? ORDER BY evaluated_at DESC",
            (model_id,),
        ).fetchall()
        promotions = conn.execute(
            "SELECT * FROM ft_promotion_log WHERE model_version_id = ? ORDER BY created_at DESC",
            (model_id,),
        ).fetchall()
        conn.close()
        return jsonify(
            {
                "model": dict(model),
                "evaluations": [dict(e) for e in evals],
                "promotions": [dict(p) for p in promotions],
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Active Model Overrides ────────────────────────────────────


@finetune_api.route("/active-models", methods=["GET"])
def list_active_models():
    """List currently active fine-tuned model overrides."""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM ft_active_models WHERE deactivated_at IS NULL ORDER BY activated_at DESC"
        ).fetchall()
        conn.close()
        return jsonify({"active_models": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Evaluations ───────────────────────────────────────────────


@finetune_api.route("/evaluations", methods=["GET"])
def list_evaluations():
    """List all evaluations."""
    try:
        conn = _get_db()
        rows = conn.execute("SELECT * FROM ft_evaluations ORDER BY evaluated_at DESC").fetchall()
        conn.close()
        evals = []
        for r in rows:
            e = dict(r)
            e["custom_metrics"] = _safe_json(e.get("custom_metrics", "{}"), {})
            e["comparison_scores"] = _safe_json(e.get("comparison_scores", "{}"), {})
            e["details"] = _safe_json(e.get("details", "{}"), {})
            evals.append(e)
        return jsonify({"evaluations": evals, "total": len(evals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Promotion Log ─────────────────────────────────────────────


@finetune_api.route("/promotions", methods=["GET"])
def list_promotions():
    """List promotion log entries."""
    try:
        conn = _get_db()
        rows = conn.execute("SELECT * FROM ft_promotion_log ORDER BY created_at DESC LIMIT 100").fetchall()
        conn.close()
        return jsonify({"promotions": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Hyperparam Search ─────────────────────────────────────────


@finetune_api.route("/hyperparam-results", methods=["GET"])
def list_hyperparam_results():
    """List hyperparameter search results."""
    search_id = request.args.get("search_id", "")
    try:
        conn = _get_db()
        if search_id:
            rows = conn.execute(
                "SELECT * FROM ft_hyperparam_results WHERE search_id = ? ORDER BY composite_score DESC",
                (search_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM ft_hyperparam_results ORDER BY created_at DESC LIMIT 50").fetchall()
        conn.close()
        results = []
        for r in rows:
            d = dict(r)
            d["hyperparams"] = _safe_json(d.get("hyperparams", "{}"), {})
            results.append(d)
        return jsonify({"results": results, "total": len(results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── GPU Status ────────────────────────────────────────────────


@finetune_api.route("/gpu-status", methods=["GET"])
def gpu_status():
    """Get current GPU detection info."""
    try:
        from tools.finetune.gpu_detector import detect_gpu

        result = detect_gpu()
        return jsonify(result.to_dict())
    except Exception as e:
        return jsonify({"error": str(e), "available": False})


# ── Labeling ──────────────────────────────────────────────────


@finetune_api.route("/datasets/<dataset_id>/examples/<int:example_id>/label", methods=["POST"])
def label_example(dataset_id, example_id):
    """Label a single training example."""
    data = request.get_json(silent=True) or {}
    quality = data.get("quality_score", 0)
    compliance = data.get("compliance_score", 0)
    relevance = data.get("relevance_score", 0)
    approved = 1 if data.get("approved", False) else 0
    labeled_by = data.get("labeled_by", "dashboard_user")

    try:
        conn = _get_db()
        conn.execute(
            """UPDATE ft_dataset_examples
               SET quality_score = ?, compliance_score = ?, relevance_score = ?,
                   approved = ?, labeled_by = ?, labeled_at = CURRENT_TIMESTAMP
               WHERE id = ? AND dataset_id = ?""",
            (quality, compliance, relevance, approved, labeled_by, example_id, dataset_id),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "example_id": example_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@finetune_api.route("/datasets/<dataset_id>/batch-label", methods=["POST"])
def batch_label(dataset_id):
    """Batch approve/reject training examples."""
    data = request.get_json(silent=True) or {}
    example_ids = data.get("example_ids", [])
    approved = 1 if data.get("approved", False) else 0
    labeled_by = data.get("labeled_by", "dashboard_user")

    if not example_ids:
        return jsonify({"error": "No example_ids provided"}), 400

    try:
        conn = _get_db()
        for eid in example_ids:
            conn.execute(
                """UPDATE ft_dataset_examples
                   SET approved = ?, labeled_by = ?, labeled_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND dataset_id = ?""",
                (approved, labeled_by, eid, dataset_id),
            )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "labeled_count": len(example_ids)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Trajectories (D-FT-TRAJ) ──────────────────────────────────


@finetune_api.route("/trajectories", methods=["GET"])
def list_trajectories():
    """List captured trajectories with optional filters."""
    outcome = request.args.get("outcome")
    workflow_type = request.args.get("workflow_type")
    finalized_only = request.args.get("finalized_only", "false").lower() == "true"
    min_reward = request.args.get("min_reward", type=float)
    limit = min(int(request.args.get("limit", 50)), 200)

    try:
        conn = _get_db()
        where: list = []
        params: list = []
        if outcome:
            where.append("outcome = ?")
            params.append(outcome)
        if workflow_type:
            where.append("workflow_type = ?")
            params.append(workflow_type)
        if finalized_only:
            where.append("captured_at IS NOT NULL")
        if min_reward is not None:
            where.append("reward >= ?")
            params.append(min_reward)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"""
            SELECT id, trace_id, workflow_type, source, outcome, reward,
                   step_count, dataset_id, project_id, classification,
                   captured_at, created_at
            FROM ft_trajectories
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
        conn.close()
        return jsonify({"trajectories": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@finetune_api.route("/trajectories/<traj_id>", methods=["GET"])
def get_trajectory(traj_id):
    """Get trajectory detail including steps and ShareGPT JSON."""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM ft_trajectories WHERE id = ?", (traj_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "trajectory not found"}), 404

        steps = conn.execute(
            """SELECT step_index, tool_name, tool_input, tool_output,
                      status, duration_ms, span_id, created_at
               FROM ft_trajectory_steps
               WHERE trajectory_id = ?
               ORDER BY step_index ASC""",
            (traj_id,),
        ).fetchall()
        conn.close()

        traj = dict(row)
        traj["steps"] = [dict(s) for s in steps]
        if traj.get("sharegpt_json"):
            traj["sharegpt"] = _safe_json(traj["sharegpt_json"])
        return jsonify(traj)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@finetune_api.route("/trajectories/stats", methods=["GET"])
def trajectory_stats():
    """Aggregate statistics for the trajectory pipeline."""
    try:
        from tools.finetune.trajectory_capture import get_stats
        return jsonify(get_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@finetune_api.route("/trajectories/export", methods=["POST"])
def export_trajectories():
    """Export finalized successful trajectories as ShareGPT JSONL.

    Body (JSON, all optional):
      output_path  — path to write JSONL (default: data/finetune/trajectories.jsonl)
      min_reward   — minimum reward threshold (default: 0.7)
      workflow_types — list of workflow type strings to filter
    """
    data = request.get_json(silent=True) or {}
    output_path = data.get("output_path", "data/finetune/trajectories.jsonl")
    min_reward = float(data.get("min_reward", 0.7))
    workflow_types = data.get("workflow_types") or None

    try:
        from tools.finetune.trajectory_capture import export_jsonl
        result = export_jsonl(
            output_path=output_path,
            min_reward=min_reward,
            workflow_types=workflow_types,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@finetune_api.route("/trajectories/<traj_id>/ingest", methods=["POST"])
def ingest_trajectory(traj_id):
    """Ingest a finalized successful trajectory into an ft_dataset.

    Body (JSON):
      dataset_id   — required: target dataset ID
      project_id   — optional
    """
    data = request.get_json(silent=True) or {}
    dataset_id = data.get("dataset_id", "")
    if not dataset_id:
        return jsonify({"error": "dataset_id is required"}), 400

    try:
        from tools.finetune.trajectory_capture import ingest_to_dataset
        result = ingest_to_dataset(
            session_id=traj_id,
            dataset_id=dataset_id,
            project_id=data.get("project_id", ""),
        )
        status = 200 if result.get("success") else 400
        return jsonify(result), status
    except Exception as e:
        return jsonify({"error": str(e)}), 500
