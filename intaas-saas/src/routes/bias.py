#!/usr/bin/env python3
"""Bias routes — article-level bias forensics and source profiles."""
import logging

from fastapi import APIRouter, Request

from src.db import dynamo
from src.services import bias_scorer, judge_service

logger = logging.getLogger("intaas.routes.bias")
router = APIRouter()


@router.get("/{article_id}")
async def get_article_bias(article_id: str, request: Request):
    """Get article-level bias forensics with dimensional breakdown.

    Returns multi-axis bias score: loaded_language, framing_balance,
    statistical_honesty, source_transparency, omission_severity.
    """
    article = dynamo.get_article(article_id)
    if not article:
        return {"error": "Article not found"}

    existing = dynamo.get_bias_scores(article_id)
    if existing:
        import json
        latest = existing[-1]
        return {
            "article_id": article_id,
            "title": article.get("title", ""),
            "scores": json.loads(latest.get("scores", "{}")),
            "composite": float(latest.get("composite", 3.0)),
            "color": latest.get("color", "green"),
            "evaluated_at": latest.get("created_at", ""),
        }

    # Score on demand
    scores = bias_scorer.score_article(article.get("content", ""), article.get("title", ""))
    dynamo.store_bias_score(article_id, scores["dimensions"], scores["composite"], scores["color"])

    return {
        "article_id": article_id,
        "title": article.get("title", ""),
        "scores": scores["dimensions"],
        "composite": scores["composite"],
        "color": scores["color"],
        "color_label": scores["color_label"],
        "findings": scores.get("findings", []),
    }


@router.post("/{article_id}/deep-analysis")
async def deep_analysis(article_id: str, request: Request):
    """Run full LLM-as-a-Judge deep analysis on a specific article.

    Returns hybrid 50/50 score with:
    - Per-dimension scores with LLM reasoning
    - Strengths and weaknesses
    - Overall assessment
    """
    article = dynamo.get_article(article_id)
    if not article:
        return {"error": "Article not found"}

    content = article.get("content", "")
    if not content or len(content) < 20:
        return {
            "error": "Article content too short for deep analysis",
            "article_id": article_id,
        }

    result = await judge_service.evaluate_with_prometheus(
        text=content,
        rubric_name="bias_forensics",
        article_id=article_id,
    )

    # Store the evaluation
    if result.get("status") == "evaluated":
        dynamo.store_judge_eval(
            article_id=article_id,
            rubric="bias_forensics",
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


@router.get("/source/{source_id}")
async def get_source_bias_profile(source_id: str, request: Request):
    """Get historical bias profile for a source across all articles."""
    return {
        "source_id": source_id,
        "historical_composite": 3.0,
        "article_count": 0,
        "trend": "stable",
        "message": "Source profiling builds over time.",
    }
