#!/usr/bin/env python3
# CUI // SP-CTI
"""RAG Evaluation Dashboard API — campaign listing, quality status, dataset balance.

Endpoints:
    GET  /api/rag-eval/campaigns          — List evaluation campaigns
    GET  /api/rag-eval/campaigns/<id>     — Campaign detail with per-type breakdowns
    POST /api/rag-eval/run                — Trigger a new evaluation campaign
    GET  /api/rag-eval/quality            — Current quality monitor status
    GET  /api/rag-eval/dataset-balance    — Taxonomy label distribution
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Blueprint, jsonify, request as flask_request  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402

rag_eval_api = Blueprint("rag_eval_api", __name__)


@rag_eval_api.route("/api/rag-eval/campaigns", methods=["GET"])
def list_campaigns():
    """List evaluation campaigns with pagination."""
    limit = flask_request.args.get("limit", 20, type=int)
    offset = flask_request.args.get("offset", 0, type=int)

    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT id, name, scoring_mode, task_type, total_cases,
                      aggregate_crag_score, aggregate_ragas_score,
                      status, created_at, completed_at
               FROM rag_evaluation_campaigns
               ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            (limit, offset),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM rag_evaluation_campaigns").fetchone()[0]
        conn.close()

        return jsonify(
            {
                "campaigns": [dict(r) for r in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc), "campaigns": [], "total": 0})


@rag_eval_api.route("/api/rag-eval/campaigns/<campaign_id>", methods=["GET"])
def campaign_detail(campaign_id: str):
    """Get campaign detail with per-question-type and per-tier breakdowns."""
    try:
        conn = get_connection()

        # Campaign header
        campaign = conn.execute(
            "SELECT * FROM rag_evaluation_campaigns WHERE id = %s",
            (campaign_id,),
        ).fetchone()
        if not campaign:
            conn.close()
            return jsonify({"error": "Campaign not found"}), 404

        # Results
        results = conn.execute(
            """SELECT id, query, question_type, entity_popularity,
                      crag_score, ragas_ndcg, ragas_mrr,
                      ragas_faithfulness, ragas_relevancy,
                      is_false_premise, created_at
               FROM rag_evaluation_results
               WHERE campaign_id = %s
               ORDER BY created_at""",
            (campaign_id,),
        ).fetchall()

        # Per-type breakdown
        type_breakdown = conn.execute(
            """SELECT question_type,
                      COUNT(*) as count,
                      AVG(crag_score) as avg_crag_score
               FROM rag_evaluation_results
               WHERE campaign_id = %s
               GROUP BY question_type""",
            (campaign_id,),
        ).fetchall()

        # Per-tier breakdown
        tier_breakdown = conn.execute(
            """SELECT entity_popularity,
                      COUNT(*) as count,
                      AVG(crag_score) as avg_crag_score
               FROM rag_evaluation_results
               WHERE campaign_id = %s AND entity_popularity IS NOT NULL
               GROUP BY entity_popularity""",
            (campaign_id,),
        ).fetchall()

        conn.close()

        return jsonify(
            {
                "campaign": dict(campaign),
                "results": [dict(r) for r in results],
                "by_question_type": [dict(r) for r in type_breakdown],
                "by_entity_popularity": [dict(r) for r in tier_breakdown],
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rag_eval_api.route("/api/rag-eval/run", methods=["POST"])
def run_campaign():
    """Trigger a new evaluation campaign."""
    data = flask_request.get_json(silent=True) or {}
    test_set = data.get("test_set_path", "")
    task_type = data.get("task_type", "T1")
    campaign_name = data.get("name", "")

    try:
        from tools.rag.crag_evaluator import CRAGBenchmarkRunner

        runner = CRAGBenchmarkRunner()
        result = runner.run_campaign(
            test_set_path=test_set or str(BASE_DIR / "data" / "rag" / "crag_test_template.json"),
            task_type=task_type,
            campaign_name=campaign_name,
        )
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "CRAG evaluator not available"}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rag_eval_api.route("/api/rag-eval/quality", methods=["GET"])
def quality_status():
    """Get current quality monitor status with feedback loop info."""
    try:
        from tools.rag.quality_feedback_loop import get_feedback_status

        status = get_feedback_status()
        return jsonify(status)
    except ImportError:
        return jsonify({"error": "quality_feedback_loop not available"}), 500
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rag_eval_api.route("/api/rag-eval/dataset-balance", methods=["GET"])
def dataset_balance():
    """Get taxonomy label distribution across datasets."""
    dataset_id = flask_request.args.get("dataset_id", "")

    try:
        conn = get_connection()

        if dataset_id:
            rows = conn.execute(
                """SELECT taxonomy_label, COUNT(*) as count
                   FROM ft_dataset_examples
                   WHERE dataset_id = %s AND taxonomy_label IS NOT NULL
                   GROUP BY taxonomy_label""",
                (dataset_id,),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM ft_dataset_examples WHERE dataset_id = %s",
                (dataset_id,),
            ).fetchone()[0]
        else:
            rows = conn.execute(
                """SELECT taxonomy_label, COUNT(*) as count
                   FROM ft_dataset_examples
                   WHERE taxonomy_label IS NOT NULL
                   GROUP BY taxonomy_label"""
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM ft_dataset_examples").fetchone()[0]

        conn.close()

        distribution = {r["taxonomy_label"]: r["count"] for r in rows}
        labeled_total = sum(distribution.values())

        # Check balance
        warnings = []
        for label, count in distribution.items():
            pct = (count / labeled_total * 100) if labeled_total > 0 else 0
            if pct < 10:
                warnings.append(f"{label} underrepresented ({pct:.1f}%)")
            elif pct > 60:
                warnings.append(f"{label} overrepresented ({pct:.1f}%)")

        return jsonify(
            {
                "dataset_id": dataset_id or "all",
                "total_examples": total,
                "labeled_examples": labeled_total,
                "distribution": distribution,
                "balance_warnings": warnings,
                "balanced": len(warnings) == 0,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc), "distribution": {}, "total_examples": 0})
