#!/usr/bin/env python3
"""LLM-as-a-Judge service — Prometheus-2 via SageMaker async endpoint.

Architecture:
  - Deterministic bias scoring runs in Lambda (instant, cheap)
  - LLM judge evaluation via SageMaker (Prometheus-2 7B Q4_K_M)
  - Combined: 50% deterministic + 50% LLM judge = hybrid score
  - Falls back to deterministic-only when SageMaker unavailable
  - Usage capped at 125 hrs/month (free tier enforcement)
"""
import json
import logging
import time
from pathlib import Path

import boto3
import yaml

from src import config
from src.services import bias_scorer

logger = logging.getLogger("intaas.services.judge")

RUBRICS_DIR = Path(__file__).resolve().parent.parent.parent / "rubrics"
_rubric_cache: dict[str, dict] = {}


def _load_rubric(rubric_name: str) -> dict:
    """Load rubric YAML by name. Cached after first load."""
    if rubric_name in _rubric_cache:
        return _rubric_cache[rubric_name]

    rubric_path = RUBRICS_DIR / f"{rubric_name}.yaml"
    if not rubric_path.exists():
        logger.warning("Rubric not found: %s", rubric_path)
        return {}

    with open(rubric_path, encoding="utf-8") as f:
        rubric = yaml.safe_load(f) or {}

    _rubric_cache[rubric_name] = rubric
    return rubric


def _build_prometheus_prompt(text: str, rubric: dict, reference: str = "") -> str:
    """Build Prometheus-2 evaluation prompt with rubric anchors."""
    dimensions = rubric.get("dimensions", {})
    rubric_text = ""
    for dim_name, dim in dimensions.items():
        rubric_text += f"\n### {dim_name.replace('_', ' ').title()}"
        rubric_text += f" (weight: {dim.get('weight', 0.2)})\n"
        rubric_text += f"{dim.get('description', '')}\n"
        for score_val, anchor in sorted(dim.get("anchors", {}).items()):
            rubric_text += f"  Score {score_val}: {anchor}\n"

    few_shot = ""
    examples = rubric.get("few_shot_examples", [])
    if examples:
        few_shot = "\n## Calibration Examples\n"
        for ex in examples[:2]:
            few_shot += f'\nExample (score {ex["score"]}):\n'
            few_shot += f'Text: "{ex["excerpt"][:200]}"\n'
            few_shot += f'Reasoning: {ex["reasoning"]}\n'

    ref_section = f"\n## Reference\n{reference}\n" if reference else ""

    return f"""You are a professional media bias evaluator. Evaluate the following news article using the rubric.

IMPORTANT: Do NOT favor longer responses. Score based on quality, not length.

## Rubric
{rubric_text}
{few_shot}
{ref_section}

## Article to Evaluate
{text[:6000]}

## Instructions
1. List 2-3 STRENGTHS of the article
2. List 2-3 WEAKNESSES or bias indicators
3. Score each dimension 1-5 using the rubric anchors

Return ONLY valid JSON:
{{"scores": {{"dimension_name": {{"score": 1-5, "reasoning": "one sentence"}}}},
 "strengths": ["str1", "str2"],
 "weaknesses": ["str1", "str2"],
 "overall_assessment": "one paragraph summary"}}"""


def _parse_response(content: str, rubric: dict) -> dict:
    """Parse Prometheus-2 JSON response with fallback."""
    import re

    content = content.strip()
    content = re.sub(r"^```json?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    parsed = None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    if not parsed or not parsed.get("scores"):
        return {"composite": 3.0, "dimensions": {}}

    dimensions = rubric.get("dimensions", {})
    dim_scores = {}
    total_weight = 0
    weighted_sum = 0

    for dim_name, dim_config in dimensions.items():
        score_data = parsed["scores"].get(dim_name, {})
        if isinstance(score_data, dict):
            score = score_data.get("score", 3)
            reasoning = score_data.get("reasoning", "")
        elif isinstance(score_data, (int, float)):
            score = score_data
            reasoning = ""
        else:
            score = 3
            reasoning = ""

        score = max(1, min(5, float(score)))
        weight = dim_config.get("weight", 0.2)
        dim_scores[dim_name] = {
            "score": score,
            "reasoning": reasoning,
        }
        weighted_sum += score * weight
        total_weight += weight

    composite = round(weighted_sum / total_weight, 2) if total_weight > 0 else 3.0

    return {
        "dimensions": dim_scores,
        "composite": composite,
        "strengths": parsed.get("strengths", []),
        "weaknesses": parsed.get("weaknesses", []),
        "overall_assessment": parsed.get("overall_assessment", ""),
    }


def _score_to_color(score: float) -> tuple[str, str]:
    for color_name, rating in config.COLOR_RATINGS.items():
        if score >= rating["min_score"]:
            return color_name, rating["label"]
    return "red", "Unacceptable"


async def evaluate_with_prometheus(
    text: str,
    rubric_name: str = "bias_forensics",
    reference: str = "",
    article_id: str = "",
) -> dict:
    """Evaluate text: deterministic first, then Prometheus-2 if available.

    Returns hybrid 50/50 score when SageMaker is up,
    deterministic-only when it's not.
    """
    start = time.time()

    # Always compute deterministic score first (instant)
    det_result = bias_scorer.score_article(text)

    if not config.LLM_JUDGE_ENABLED and not config.SAGEMAKER_ENABLED:
        return _format_deterministic(det_result, rubric_name)

    # Load rubric
    rubric = _load_rubric(rubric_name)
    if not rubric or not rubric.get("dimensions"):
        return _format_deterministic(det_result, rubric_name)

    prompt = _build_prometheus_prompt(text, rubric, reference)
    llm_content = ""
    model_used = ""

    try:
        # Option 1: Anthropic API (Claude Haiku — cheap, fast, no infra)
        if config.LLM_JUDGE_ENABLED and config.ANTHROPIC_API_KEY:
            llm_content, model_used = _call_anthropic(prompt)

        # Option 2: SageMaker (Prometheus-2 — requires endpoint)
        elif config.SAGEMAKER_ENABLED:
            llm_content, model_used = _call_sagemaker(prompt)

        if not llm_content:
            return _format_deterministic(det_result, rubric_name)

        llm_result = _parse_response(llm_content, rubric)
        elapsed_ms = int((time.time() - start) * 1000)

        hybrid = _combine(det_result, llm_result)

        return {
            "status": "evaluated",
            "method": "hybrid",
            "rubric": rubric_name,
            "dimension_scores": hybrid["dimensions"],
            "composite_score": hybrid["composite"],
            "color": hybrid["color"],
            "color_label": hybrid["color_label"],
            "strengths": llm_result.get("strengths", []),
            "weaknesses": llm_result.get(
                "weaknesses", det_result.get("findings", []),
            ),
            "overall_assessment": llm_result.get("overall_assessment", ""),
            "model": model_used,
            "eval_ms": elapsed_ms,
            "deterministic_score": det_result["composite"],
            "llm_score": llm_result.get("composite", 3.0),
        }

    except Exception as e:
        logger.warning("Judge failed, deterministic fallback: %s", e)
        return _format_deterministic(det_result, rubric_name)


def _call_anthropic(prompt: str) -> tuple[str, str]:
    """Call Claude Haiku via Anthropic API for judging."""
    import requests as req

    model = config.LLM_JUDGE_MODEL
    resp = req.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data.get("content", [{}])[0].get("text", "")
    return content, model


def _call_sagemaker(prompt: str) -> tuple[str, str]:
    """Call Prometheus-2 via SageMaker endpoint."""
    sagemaker = boto3.client("sagemaker-runtime")
    response = sagemaker.invoke_endpoint(
        EndpointName=config.SAGEMAKER_ENDPOINT,
        ContentType="application/json",
        Body=json.dumps({
            "prompt": prompt,
            "max_tokens": 1024,
            "temperature": 0.0,
        }),
    )
    body = json.loads(response["Body"].read().decode("utf-8"))
    content = body.get("content", body.get("generated_text", ""))
    return content, "prometheus-7b-v2.0-Q4_K_M"


def _combine(det: dict, llm: dict) -> dict:
    """50% deterministic + 50% LLM hybrid scoring."""
    det_composite = det.get("composite", 3.0)
    llm_composite = llm.get("composite", 3.0)
    combined = round(0.5 * det_composite + 0.5 * llm_composite, 2)
    color, label = _score_to_color(combined)

    dims = {}
    for dim_name in config.BIAS_DIMENSIONS:
        d_score = det.get("dimensions", {}).get(dim_name, 3.0)
        l_dim = llm.get("dimensions", {}).get(dim_name, {})
        l_score = l_dim.get("score", 3.0) if isinstance(l_dim, dict) else 3.0
        dims[dim_name] = {
            "score": round(0.5 * d_score + 0.5 * l_score, 2),
            "deterministic": d_score,
            "llm": l_score,
            "reasoning": l_dim.get("reasoning", "") if isinstance(l_dim, dict) else "",
        }

    return {
        "dimensions": dims,
        "composite": combined,
        "color": color,
        "color_label": label,
    }


def _format_deterministic(det: dict, rubric_name: str) -> dict:
    """Format deterministic-only result."""
    return {
        "status": "evaluated",
        "method": "deterministic",
        "rubric": rubric_name,
        "dimension_scores": {
            dim: {"score": score, "reasoning": "Keyword pattern analysis"}
            for dim, score in det.get("dimensions", {}).items()
        },
        "composite_score": det["composite"],
        "color": det["color"],
        "color_label": det["color_label"],
        "strengths": [],
        "weaknesses": det.get("findings", []),
        "overall_assessment": "Deterministic scoring (LLM judge not active).",
        "model": "deterministic",
        "eval_ms": 0,
    }
