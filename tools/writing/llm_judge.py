# CUI // SP-CTI
"""LLM-as-a-Judge — rubric-based semantic evaluation for Pulse and ProposalAI.

Uses Prometheus-2 7B (purpose-trained evaluator, local Ollama) for rubric-anchored
scoring across 5 dimensions per content type. Complements WriteGuard's deterministic
checks (grammar, readability, tone, plagiarism, AI detection) with semantic quality:
coherence, persuasiveness, specificity, completeness, audience fit.

Architecture:
  - WriteGuard (deterministic, unchanged) → surface quality (0-100)
  - LLM Judge (Prometheus-2 via Ollama) → semantic quality (1-5 per dimension)
  - Combined: 50% deterministic + 50% LLM judge → color rating (Blue/Purple/Green/Yellow/Red)

Supports 4 content types with YAML rubrics:
  - blog (Pulse articles)
  - proposal_technical (GovCon technical volume)
  - proposal_management (GovCon management volume)
  - compliance_narrative (SSP, POAM, control statements)

CLI:
  python tools/writing/llm_judge.py --evaluate --text "..." --content-type blog --json
  python tools/writing/llm_judge.py --health --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_PATH = BASE_DIR / "args" / "llm_judge_config.yaml"
RUBRICS_DIR = BASE_DIR / "context" / "writeguard" / "rubrics"

_config_cache: dict | None = None


def _load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f) or {}
    else:
        _config_cache = {}
    return _config_cache


def _load_rubric(content_type: str) -> dict:
    """Load a YAML rubric for the given content type."""
    config = _load_config()
    ct_config = config.get("content_types", {}).get(content_type, {})
    rubric_path = ct_config.get("rubric", "")
    if rubric_path:
        full_path = BASE_DIR / rubric_path
    else:
        full_path = RUBRICS_DIR / f"{content_type}.yaml"

    if not full_path.exists():
        logger.warning("Rubric not found: %s", full_path)
        return {}

    with open(full_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Color rating
# ---------------------------------------------------------------------------

COLOR_ORDER = ["blue", "purple", "green", "yellow", "red"]


def score_to_color(score: float) -> dict:
    """Map a 1-5 composite score to a FAR 15.305 color rating."""
    config = _load_config()
    ratings = config.get("color_ratings", {})

    for color in COLOR_ORDER:
        rating = ratings.get(color, {})
        if score >= rating.get("min_score", 999):
            return {
                "color": color,
                "label": rating.get("label", color.title()),
                "description": rating.get("description", ""),
                "css_class": rating.get("css_class", f"badge-{color}-rating"),
            }

    # Fallback: red
    red = ratings.get("red", {})
    return {
        "color": "red",
        "label": red.get("label", "Unacceptable"),
        "description": red.get("description", ""),
        "css_class": red.get("css_class", "badge-red-rating"),
    }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_judge_prompt(
    text: str,
    rubric: dict,
    reference: str = "",
) -> str:
    """Build a Prometheus-2 evaluation prompt with rubric anchors and bias mitigation."""
    config = _load_config()
    bias = config.get("bias_mitigation", {})

    dimensions = rubric.get("dimensions", {})
    rubric_text = ""
    for dim_name, dim in dimensions.items():
        rubric_text += f"\n### {dim_name.replace('_', ' ').title()} (weight: {dim.get('weight', 0.2)})\n"
        rubric_text += f"{dim.get('description', '')}\n"
        for score_val, anchor in sorted(dim.get("anchors", {}).items()):
            rubric_text += f"  Score {score_val}: {anchor}\n"

    # Few-shot examples
    few_shot = ""
    examples = rubric.get("few_shot_examples", [])
    if examples and bias.get("few_shot_anchoring", True):
        few_shot = "\n## Calibration Examples\n"
        for ex in examples[:3]:
            few_shot += f"\nExample (score {ex['score']}):\n"
            few_shot += f'Text: "{ex["excerpt"]}"\n'
            few_shot += f"Reasoning: {ex['reasoning']}\n"

    # Reference context (for proposal sections — RFP shall-statement)
    ref_section = ""
    if reference:
        ref_section = f"\n## Reference Requirement\n{reference}\n"

    # Anti-verbosity instruction
    anti_verb = ""
    if bias.get("anti_verbosity", True):
        anti_verb = (
            "\nIMPORTANT: Do NOT favor longer responses. A concise, precise response "
            "that fully addresses the requirement is better than a verbose one with padding.\n"
        )

    # Chain-of-thought instruction
    cot = ""
    if bias.get("chain_of_thought", True):
        cot = (
            "\nEvaluation process:\n"
            "1. First, list 2-3 STRENGTHS of the text\n"
            "2. Then, list 2-3 WEAKNESSES or gaps\n"
            "3. Then, score each dimension 1-5 using the rubric anchors\n"
        )

    prompt = f"""You are a professional content evaluator. Evaluate the following text using the rubric provided.

## Rubric
{rubric_text}
{few_shot}
{ref_section}
{anti_verb}
{cot}

## Text to Evaluate
{text[:8000]}

## Instructions
Score each dimension on a 1-5 scale using the rubric anchors above.
Return your evaluation as JSON with this exact structure:
{{
  "strengths": ["strength 1", "strength 2"],
  "weaknesses": ["weakness 1", "weakness 2"],
  "scores": {{
    "dimension_name": {{"score": 1-5, "reasoning": "one sentence"}}
  }},
  "overall_assessment": "one paragraph summary"
}}

Return ONLY valid JSON. No markdown code fences."""

    return prompt


# ---------------------------------------------------------------------------
# Judge invocation
# ---------------------------------------------------------------------------


def _invoke_prometheus(prompt: str) -> dict:
    """Call Prometheus-2 via Ollama native API."""
    from tools.http.client import request as _http_request

    config = _load_config()
    model_config = config.get("model", {})
    model_name = model_config.get("name", "tensortemplar/prometheus2:7b-fp16")
    timeout = model_config.get("timeout_seconds", 60)
    max_tokens = model_config.get("max_tokens", 1024)

    try:
        resp = _http_request(
            "POST",
            "http://localhost:11434/api/chat",
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": model_config.get("temperature", 0.0),
                },
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        eval_ms = int(data.get("eval_duration", 0) / 1e6)
        return {"content": content, "model": model_name, "eval_ms": eval_ms}
    except Exception as e:
        logger.error("Prometheus-2 invocation failed: %s", e)
        return {"content": "", "model": model_name, "error": str(e)}


def _parse_judge_response(content: str, rubric: dict) -> dict:
    """Parse the judge response — tries JSON first, falls back to prose extraction."""
    content = content.strip()

    # Strip markdown fences
    content = re.sub(r"^```json?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    dimensions = rubric.get("dimensions", {})
    parsed = None

    # Strategy 1: Parse as JSON
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    if parsed and parsed.get("scores"):
        # JSON path — extract structured scores
        scores = parsed.get("scores", {})
        dimension_scores = {}
        for dim_name in dimensions:
            dim_data = scores.get(dim_name, {})
            if isinstance(dim_data, dict):
                score = dim_data.get("score", 3)
                reasoning = dim_data.get("reasoning", "")
            elif isinstance(dim_data, (int, float)):
                score = dim_data
                reasoning = ""
            else:
                score = 3
                reasoning = ""
            score = max(1, min(5, float(score)))
            dimension_scores[dim_name] = {
                "score": score,
                "weight": dimensions[dim_name].get("weight", 0.2),
                "reasoning": reasoning,
            }
        return {
            "dimension_scores": dimension_scores,
            "strengths": parsed.get("strengths", []),
            "weaknesses": parsed.get("weaknesses", []),
            "overall_assessment": parsed.get("overall_assessment", ""),
        }

    # Strategy 2: Extract scores from prose using regex patterns
    # Look for patterns like "Score: 4", "4/5", "score of 4", dimension names + numbers
    logger.info("JSON parse failed, extracting scores from prose")
    content_lower = content.lower()
    dimension_scores = {}
    strengths = []
    weaknesses = []

    for dim_name in dimensions:
        dim_label = dim_name.replace("_", " ")
        # Try various patterns
        score = None
        reasoning = ""

        # Pattern: "dimension_name: 4" or "dimension name: 4/5"
        for pattern in [
            rf"{dim_label}\s*[:\-]\s*(\d(?:\.\d)?)\s*(?:/\s*5)?",
            rf"(?:score|rating)\s*(?:of|for|:)\s*(?:{dim_label})\s*[:\-]?\s*(\d(?:\.\d)?)",
            rf"{dim_label}.*?(\d(?:\.\d)?)\s*/\s*5",
            rf"{dim_label}.*?score\s*(?:of|:)?\s*(\d(?:\.\d)?)",
        ]:
            match = re.search(pattern, content_lower)
            if match:
                try:
                    score = float(match.group(1))
                    # Get surrounding text as reasoning
                    start = max(0, match.start() - 20)
                    end = min(len(content), match.end() + 100)
                    reasoning = content[start:end].strip()
                    break
                except ValueError:
                    continue

        # Fallback: look for any "N/5" pattern near the dimension name
        if score is None:
            idx = content_lower.find(dim_label)
            if idx >= 0:
                nearby = content_lower[idx : idx + 200]
                nums = re.findall(r"(\d(?:\.\d)?)\s*/\s*5", nearby)
                if nums:
                    score = float(nums[0])

        # Final fallback: overall sentiment
        if score is None:
            # Look for overall score pattern anywhere
            overall_match = re.search(
                r"(?:overall|total|final)\s*(?:score|rating)\s*[:\-]?\s*(\d(?:\.\d)?)", content_lower
            )
            if overall_match:
                score = float(overall_match.group(1))
            else:
                score = 3.0  # Neutral default

        score = max(1, min(5, score))
        dimension_scores[dim_name] = {
            "score": score,
            "weight": dimensions[dim_name].get("weight", 0.2),
            "reasoning": reasoning[:200] if reasoning else "",
        }

    # Extract strengths/weaknesses from prose
    for marker in ["strength", "positive", "well", "good", "excellent"]:
        for match in re.finditer(rf"[-•*]\s*(.*?{marker}.*?)(?:\n|$)", content_lower):
            strengths.append(match.group(1).strip()[:100])
    for marker in ["weakness", "lack", "missing", "could", "should", "improve"]:
        for match in re.finditer(rf"[-•*]\s*(.*?{marker}.*?)(?:\n|$)", content_lower):
            weaknesses.append(match.group(1).strip()[:100])

    # Use first paragraph as overall assessment
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    overall = paragraphs[0][:300] if paragraphs else ""

    return {
        "dimension_scores": dimension_scores,
        "strengths": strengths[:5],
        "weaknesses": weaknesses[:5],
        "overall_assessment": overall,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate(
    text: str,
    content_type: str = "blog",
    reference: str = "",
    writeguard_score: float | None = None,
) -> dict:
    """Evaluate text using the LLM judge with rubric-based scoring.

    Args:
        text: Content to evaluate.
        content_type: One of 'blog', 'proposal_technical', 'proposal_management',
                      'compliance_narrative'.
        reference: Optional reference text (e.g., RFP shall-statement for proposals).
        writeguard_score: Optional WriteGuard composite score (0-1). If below
                          min_writeguard_score threshold, judge is skipped.

    Returns:
        dict with dimension_scores, composite_score, color_rating, findings, etc.
    """
    start = time.time()
    config = _load_config()
    ct_config = config.get("content_types", {}).get(content_type, {})

    # Check WriteGuard threshold
    min_wg = ct_config.get("min_writeguard_score", 0.0)
    if writeguard_score is not None and writeguard_score < min_wg:
        return {
            "status": "skipped",
            "reason": f"WriteGuard score {writeguard_score:.2f} below threshold {min_wg}",
            "content_type": content_type,
        }

    # Load rubric
    rubric = _load_rubric(content_type)
    if not rubric or not rubric.get("dimensions"):
        return {
            "status": "error",
            "error": f"No rubric found for content type: {content_type}",
        }

    # Build prompt and invoke
    prompt = _build_judge_prompt(text, rubric, reference)
    result = _invoke_prometheus(prompt)

    if result.get("error"):
        return {
            "status": "error",
            "error": result["error"],
            "model": result.get("model", ""),
        }

    # Parse response
    parsed = _parse_judge_response(result["content"], rubric)
    if parsed.get("error"):
        return {
            "status": "parse_error",
            "error": parsed["error"],
            "raw_response": parsed.get("raw", ""),
            "model": result.get("model", ""),
        }

    # Compute weighted composite score (1-5)
    dimension_scores = parsed.get("dimension_scores", {})
    total_weight = sum(d["weight"] for d in dimension_scores.values())
    if total_weight > 0:
        composite = sum(d["score"] * d["weight"] for d in dimension_scores.values()) / total_weight
    else:
        composite = 3.0

    composite = round(composite, 2)
    color = score_to_color(composite)

    # Compute combined score if WriteGuard available
    combined_score = None
    scoring = config.get("scoring", {})
    if writeguard_score is not None:
        det_weight = scoring.get("deterministic_weight", 0.50)
        judge_weight = scoring.get("llm_judge_weight", 0.50)
        # Normalize WriteGuard 0-100 to 1-5 scale
        wg_normalized = 1 + (writeguard_score / 100) * 4
        combined_score = round(det_weight * wg_normalized + judge_weight * composite, 2)

    elapsed_ms = int((time.time() - start) * 1000)

    return {
        "status": "evaluated",
        "content_type": content_type,
        "dimension_scores": {
            name: {"score": d["score"], "reasoning": d.get("reasoning", "")} for name, d in dimension_scores.items()
        },
        "composite_score": composite,
        "color_rating": color,
        "combined_score": combined_score,
        "combined_color": score_to_color(combined_score) if combined_score else None,
        "strengths": parsed.get("strengths", []),
        "weaknesses": parsed.get("weaknesses", []),
        "overall_assessment": parsed.get("overall_assessment", ""),
        "findings": parsed.get("weaknesses", []),
        "auto_rewrite": ct_config.get("auto_rewrite", False),
        "model": result.get("model", ""),
        "eval_ms": result.get("eval_ms", 0),
        "elapsed_ms": elapsed_ms,
    }


def evaluate_and_store(
    text: str,
    content_type: str = "blog",
    reference: str = "",
    writeguard_score: float | None = None,
    post_id: str = "",
    opportunity_id: str = "",
    section_id: str = "",
) -> dict:
    """Evaluate and store results in the audit table."""
    result = evaluate(text, content_type, reference, writeguard_score)

    if result.get("status") != "evaluated":
        return result

    # Store in DB
    try:
        from tools.db.storage import get_connection

        entry_id = f"judge-{hashlib.md5(f'{post_id}{time.time()}'.encode(), usedforsecurity=False).hexdigest()[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        with get_connection() as conn:
            conn.execute(
                """INSERT INTO llm_judge_evaluations (
                    id, post_id, opportunity_id, section_id, content_type,
                    composite_score, combined_score, color_rating,
                    dimension_scores, strengths, weaknesses, overall_assessment,
                    model_used, eval_ms, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    entry_id,
                    post_id,
                    opportunity_id,
                    section_id,
                    content_type,
                    result["composite_score"],
                    result.get("combined_score"),
                    result["color_rating"]["color"],
                    json.dumps(result["dimension_scores"]),
                    json.dumps(result["strengths"]),
                    json.dumps(result["weaknesses"]),
                    result.get("overall_assessment", ""),
                    result.get("model", ""),
                    result.get("eval_ms", 0),
                    now,
                ),
            )
            conn.commit()

        result["evaluation_id"] = entry_id
    except Exception as e:
        logger.warning("Failed to store judge evaluation: %s", e)
        result["store_error"] = str(e)

    return result


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


def check_health() -> dict:
    """Check if the judge model is available and responsive."""
    config = _load_config()
    model_name = config.get("model", {}).get("name", "tensortemplar/prometheus2:7b-fp16")

    result = {
        "model": model_name,
        "rubrics_available": [],
        "config_loaded": bool(config),
    }

    # Check rubrics
    for ct in ["blog", "proposal_technical", "proposal_management", "compliance_narrative"]:
        rubric = _load_rubric(ct)
        if rubric and rubric.get("dimensions"):
            result["rubrics_available"].append(ct)

    # Ping model
    try:
        from tools.http.client import request

        resp = request(
            "POST",
            "http://localhost:11434/api/chat",
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": "Respond with just: OK"}],
                "stream": False,
                "options": {"num_predict": 10},
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        result["model_available"] = True
        result["model_response"] = data.get("message", {}).get("content", "")[:50]
    except Exception as e:
        result["model_available"] = False
        result["model_error"] = str(e)

    return result


# ---------------------------------------------------------------------------
# DB table initialization
# ---------------------------------------------------------------------------


JUDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_judge_evaluations (
    id TEXT PRIMARY KEY,
    post_id TEXT,
    opportunity_id TEXT,
    section_id TEXT,
    content_type TEXT NOT NULL,
    composite_score REAL,
    combined_score REAL,
    color_rating TEXT,
    dimension_scores TEXT,
    strengths TEXT,
    weaknesses TEXT,
    overall_assessment TEXT,
    model_used TEXT,
    eval_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_judge_eval_post ON llm_judge_evaluations(post_id);
CREATE INDEX IF NOT EXISTS idx_judge_eval_opportunity ON llm_judge_evaluations(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_judge_eval_color ON llm_judge_evaluations(color_rating);
"""


def init_judge_db():
    """Initialize the judge evaluations table."""
    try:
        from tools.db.storage import get_connection

        with get_connection() as conn:
            conn.executescript(JUDGE_SCHEMA)
            conn.commit()
        logger.info("LLM judge table initialized")
    except Exception as e:
        logger.warning("Failed to initialize judge table: %s", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="LLM-as-a-Judge — rubric-based semantic evaluation")
    parser.add_argument("--evaluate", action="store_true", help="Evaluate text")
    parser.add_argument("--text", type=str, help="Text to evaluate")
    parser.add_argument("--file", type=str, help="File to evaluate")
    parser.add_argument(
        "--content-type",
        type=str,
        default="blog",
        choices=["blog", "proposal_technical", "proposal_management", "compliance_narrative"],
    )
    parser.add_argument("--reference", type=str, default="", help="Reference text (RFP shall-statement)")
    parser.add_argument("--writeguard-score", type=float, default=None, help="WriteGuard score 0-100")
    parser.add_argument("--health", action="store_true", help="Check judge health")
    parser.add_argument("--init-db", action="store_true", help="Initialize DB table")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = check_health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Model:    {result['model']}")
            print(f"Available: {result.get('model_available', False)}")
            print(f"Rubrics:  {', '.join(result['rubrics_available'])}")
        return

    if args.init_db:
        init_judge_db()
        print("Judge table initialized.")
        return

    if args.evaluate:
        text = args.text or ""
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        if not text:
            parser.error("--text or --file required with --evaluate")

        init_judge_db()
        result = evaluate(text, args.content_type, args.reference, args.writeguard_score)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result.get("status") == "evaluated":
                color = result["color_rating"]
                print(f"Color Rating: {color['color'].upper()} — {color['label']}")
                print(f"Composite:    {result['composite_score']}/5.0")
                if result.get("combined_score"):
                    print(f"Combined:     {result['combined_score']}/5.0")
                print("\nDimensions:")
                for dim, data in result["dimension_scores"].items():
                    print(f"  {dim:25s} {data['score']}/5  {data.get('reasoning', '')}")
                if result.get("strengths"):
                    print("\nStrengths:")
                    for s in result["strengths"]:
                        print(f"  + {s}")
                if result.get("weaknesses"):
                    print("\nWeaknesses:")
                    for w in result["weaknesses"]:
                        print(f"  - {w}")
                print(f"\nModel: {result.get('model', '')} ({result.get('elapsed_ms', 0)}ms)")
            else:
                print(f"Status: {result.get('status')}")
                print(f"Error:  {result.get('error', 'unknown')}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
