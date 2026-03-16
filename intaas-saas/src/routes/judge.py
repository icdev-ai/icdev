#!/usr/bin/env python3
"""LLM-as-a-Judge routes — Prometheus-2 rubric-based evaluation via SageMaker."""
import logging

from fastapi import APIRouter, Request

from src.db import dynamo
from src.models import JudgeRequest
from src.services import judge_service

logger = logging.getLogger("intaas.routes.judge")
router = APIRouter()


@router.post("/evaluate")
async def evaluate(req: JudgeRequest, request: Request):
    """Evaluate an article using Prometheus-2 via SageMaker.

    Supports rubrics: bias_forensics, perspective_analysis, fact_check.
    Returns dimensional scores (1-5) with reasoning and color rating.

    If SageMaker endpoint is unavailable, falls back to deterministic scoring.
    """
    text = req.text or ""
    if req.article_id and not text:
        article = dynamo.get_article(req.article_id)
        if not article:
            return {"error": "Article not found"}
        text = article.get("content", "")

    if not text:
        return {"error": "text or article_id required"}

    result = await judge_service.evaluate_with_prometheus(
        text=text,
        rubric_name=req.rubric,
        reference=req.reference,
        article_id=req.article_id or "",
    )

    # Store result
    if result.get("status") == "evaluated" and req.article_id:
        dynamo.store_judge_eval(
            article_id=req.article_id,
            rubric=req.rubric,
            dimension_scores=result.get("dimension_scores", {}),
            composite_score=result.get("composite_score", 3.0),
            color=result.get("color", "green"),
            strengths=result.get("strengths", []),
            weaknesses=result.get("weaknesses", []),
            overall_assessment=result.get("overall_assessment", ""),
            model_used=result.get("model", ""),
            eval_ms=result.get("eval_ms", 0),
        )

    return result


@router.get("/{article_id}")
async def get_evaluations(article_id: str, request: Request):
    """Get all LLM judge evaluations for an article."""
    import json

    evals = dynamo.get_judge_evals(article_id)
    return {
        "article_id": article_id,
        "evaluation_count": len(evals),
        "evaluations": [
            {
                "eval_id": e.get("eval_id", ""),
                "rubric": e.get("rubric", ""),
                "composite_score": float(e.get("composite_score", 3.0)),
                "color": e.get("color", "green"),
                "dimension_scores": json.loads(e.get("dimension_scores", "{}")),
                "strengths": json.loads(e.get("strengths", "[]")),
                "weaknesses": json.loads(e.get("weaknesses", "[]")),
                "model_used": e.get("model_used", ""),
                "created_at": e.get("created_at", ""),
            }
            for e in evals
        ],
    }
