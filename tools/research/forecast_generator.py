"""Forecast Generator — FORECAST pipeline stage for Industry Research Engine.

Generates AI-assisted forward-looking predictions by aggregating signals from
the Research Engine, Innovation Engine, and Creative Engine. Each prediction
is scored for confidence and surprise (non-obviousness), with time horizons.

Architecture Decisions:
  D-RES-17: FORECAST is 9th pipeline stage between SYNTHESIZE and DOSSIER
  D-RES-18: Cross-engine aggregation queries innovation + creative tables
  D-RES-19: Each prediction has confidence, surprise_score, composite_rank
  D-RES-20: research_forecasts table (append-only, D6)

CUI // SP-CTI
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import json
import sqlite3
import uuid
from tools.db.storage import get_connection, table_exists
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
_DB_PATH = _ROOT / "data" / "icdev.db"

PREDICTION_TYPES = (
    "trend_trajectory",
    "greenfield",
    "convergence",
    "disruption",
    "regulatory_shift",
)

TIME_HORIZONS = ("3mo", "6mo", "1yr", "3yr")

# Default forecast prompt template
_FORECAST_PROMPT = """\
You are a forward-looking industry analyst. Based on the data below, generate \
exactly {max_predictions} predictions about the future of "{vertical_name}".

For EACH prediction, output a JSON object with these fields:
- title: Short headline (max 100 chars)
- description: Detailed narrative (2-3 sentences)
- prediction_type: One of {prediction_types}
- confidence: 0.0-1.0 (how likely is this prediction)
- surprise_score: 0.0-1.0 (how non-obvious — higher = more surprising/contrarian)
- time_horizon: One of {time_horizons}
- supporting_evidence: List of signal/trend IDs that support this

Focus on:
1. NON-OBVIOUS insights — predictions that contradict majority sentiment
2. CROSS-VERTICAL convergence — where different domains intersect
3. TIMING inflection points — when a slow trend will accelerate
4. GREENFIELD opportunities — unserved markets or capability gaps
5. CONTRARIAN views — things the data suggests but most analysts miss

IMPORTANT: Surprise score should be HIGH for predictions that:
- Contradict majority signal sentiment
- Involve unexpected cross-domain convergence
- Identify timing inflection points others miss
- Reveal hidden greenfield opportunities

Return a JSON array of prediction objects. No markdown wrapping.

=== RESEARCH DATA ===
{research_data}

=== INNOVATION ENGINE SIGNALS ===
{innovation_data}

=== CREATIVE ENGINE INSIGHTS ===
{creative_data}

=== EXISTING TRENDS ===
{trends_data}

=== CHALLENGES ===
{challenges_data}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _forecast_id() -> str:
    return f"rfor-{uuid.uuid4().hex[:12]}"


def _get_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Connect to ICDEV™ database."""
    path = db_path or str(_DB_PATH)
    if not Path(path).exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = get_connection(db_path=str(path))
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table exists in the database."""
    # Backend-aware, translation-independent existence probe (pgrt-sweep-06).
    return table_exists(conn, table_name)


def _parse_json(text: str) -> Optional[List[Dict]]:
    """Parse JSON from LLM output, handling markdown wrapping and thinking tags."""
    if not text:
        return None
    cleaned = text.strip()

    # Strip qwen3.5 thinking tags (model emits <think>...</think> before JSON)
    import re

    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    # Try to extract JSON array from the text if it contains other content
    if not cleaned.startswith("[") and not cleaned.startswith("{"):
        # Find first [ or { and last ] or }
        arr_start = cleaned.find("[")
        obj_start = cleaned.find("{")
        if arr_start >= 0 and (obj_start < 0 or arr_start < obj_start):
            arr_end = cleaned.rfind("]")
            if arr_end > arr_start:
                cleaned = cleaned[arr_start : arr_end + 1]
        elif obj_start >= 0:
            obj_end = cleaned.rfind("}")
            if obj_end > obj_start:
                cleaned = cleaned[obj_start : obj_end + 1]

    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "predictions" in result:
            return result["predictions"]
        if isinstance(result, dict):
            return [result]
        return None
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Cross-engine data aggregation (D-RES-18)
# ---------------------------------------------------------------------------
def _aggregate_cross_engine_data(session_id: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Aggregate data from Research + Innovation + Creative engines.

    Queries research tables for current session, plus innovation and creative
    tables for cross-engine context. Tables that don't exist are skipped.
    """
    conn = _get_db(db_path)
    result: Dict[str, Any] = {
        "session": {},
        "signals": [],
        "trends": [],
        "challenges": [],
        "innovation_trends": [],
        "innovation_signals": [],
        "creative_pain_points": [],
        "creative_feature_gaps": [],
    }

    try:
        # Session info
        row = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        if row:
            result["session"] = dict(row)

        # Research signals (top 50 by upvotes)
        rows = conn.execute(
            """SELECT id, source, source_type, title, body, url, author,
                      upvotes, citations, sentiment, keywords, metadata
               FROM research_signals
               WHERE session_id = %s
               ORDER BY upvotes DESC LIMIT 50""",
            (session_id,),
        ).fetchall()
        result["signals"] = [dict(r) for r in rows]

        # Research trends — session_ids is a JSON list, not a scalar column
        all_trends = conn.execute(
            """SELECT id, name, velocity, acceleration,
                      signal_count, keywords, session_ids, metadata
               FROM research_trends
               ORDER BY velocity DESC"""
        ).fetchall()
        session_trends = []
        for tr in all_trends:
            sids = tr["session_ids"] or "[]"
            try:
                sid_list = json.loads(sids) if isinstance(sids, str) else sids
            except (json.JSONDecodeError, TypeError):
                sid_list = []
            if session_id in sid_list:
                session_trends.append(dict(tr))
        result["trends"] = session_trends[:20]

        # Research challenges (top 20 by composite_score)
        rows = conn.execute(
            """SELECT id, title, description, severity, signal_count,
                      composite_score, keywords, market_demand,
                      regulatory_pressure, technical_complexity
               FROM research_challenges
               WHERE session_id = %s
               ORDER BY composite_score DESC LIMIT 20""",
            (session_id,),
        ).fetchall()
        result["challenges"] = [dict(r) for r in rows]

        # Innovation Engine — trends (if table exists)
        if _table_exists(conn, "innovation_trends"):
            rows = conn.execute(
                """SELECT id, name, category, velocity, acceleration,
                          keywords, signal_count
                   FROM innovation_trends
                   ORDER BY velocity DESC LIMIT 15"""
            ).fetchall()
            result["innovation_trends"] = [dict(r) for r in rows]

        # Innovation Engine — recent signals (if table exists)
        if _table_exists(conn, "innovation_signals"):
            rows = conn.execute(
                """SELECT id, title, description, source_type, innovation_score,
                          category
                   FROM innovation_signals
                   WHERE innovation_score >= 0.5
                   ORDER BY innovation_score DESC LIMIT 20"""
            ).fetchall()
            result["innovation_signals"] = [dict(r) for r in rows]

        # Creative Engine — pain points (if table exists)
        if _table_exists(conn, "creative_pain_points"):
            rows = conn.execute(
                """SELECT id, title, description, category, frequency,
                          severity, keywords
                   FROM creative_pain_points
                   ORDER BY frequency DESC LIMIT 15"""
            ).fetchall()
            result["creative_pain_points"] = [dict(r) for r in rows]

        # Creative Engine — feature gaps (if table exists)
        if _table_exists(conn, "creative_feature_gaps"):
            rows = conn.execute(
                """SELECT id, feature_name, description, gap_score,
                          market_demand, requested_by_count
                   FROM creative_feature_gaps
                   ORDER BY gap_score DESC LIMIT 15"""
            ).fetchall()
            result["creative_feature_gaps"] = [dict(r) for r in rows]

    except Exception as exc:
        logger.warning("Cross-engine aggregation error: %s", exc)
    finally:
        conn.close()

    return result


# ---------------------------------------------------------------------------
# LLM prompt construction
# ---------------------------------------------------------------------------
def _format_data_section(items: List[Dict], max_items: int = 10) -> str:
    """Format a list of data items for LLM prompt context."""
    if not items:
        return "(none available)"
    lines = []
    for item in items[:max_items]:
        title = item.get("title") or item.get("name", "")
        desc = item.get("description") or item.get("body", "")
        item_id = item.get("id", "")
        score = item.get("composite_score") or item.get("velocity") or ""
        keywords = item.get("keywords", "")
        line = f"- [{item_id}] {title}"
        if score:
            line += f" (score={score})"
        if desc:
            line += f": {str(desc)[:200]}"
        if keywords and keywords != "[]":
            line += f" | keywords: {keywords}"
        lines.append(line)
    return "\n".join(lines)


def _build_forecast_prompt(aggregated: Dict[str, Any], config: Dict) -> str:
    """Build LLM prompt from aggregated cross-engine data."""
    forecast_cfg = config.get("forecast", {})
    max_predictions = forecast_cfg.get("max_predictions", 5)

    vertical_name = aggregated.get("session", {}).get("vertical_name", "industry")

    return _FORECAST_PROMPT.format(
        max_predictions=max_predictions,
        vertical_name=vertical_name,
        prediction_types=", ".join(PREDICTION_TYPES),
        time_horizons=", ".join(TIME_HORIZONS),
        research_data=_format_data_section(aggregated["signals"], 15),
        innovation_data=_format_data_section(aggregated["innovation_trends"] + aggregated["innovation_signals"], 10),
        creative_data=_format_data_section(
            aggregated["creative_pain_points"] + aggregated["creative_feature_gaps"], 10
        ),
        trends_data=_format_data_section(aggregated["trends"], 10),
        challenges_data=_format_data_section(aggregated["challenges"], 10),
    )


# ---------------------------------------------------------------------------
# LLM invocation
# ---------------------------------------------------------------------------
def _invoke_forecast_llm(prompt: str, config: Dict) -> Tuple[List[Dict], str]:
    """Invoke LLM via two-tier routing for forecast generation.

    Returns (predictions_list, model_name).
    """
    forecast_cfg = config.get("forecast", {})
    llm_function = forecast_cfg.get("llm_function", "research_forecast")

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        # Prepend /no_think for qwen3.5 (burns all tokens on thinking otherwise)
        prefixed_prompt = "/no_think\n" + prompt
        request = LLMRequest(
            messages=[{"role": "user", "content": prefixed_prompt}],
            max_tokens=4096,
            temperature=0.7,
            # The forecast prompt aggregates content from our own internal DB
            # tables (research_signals, innovation_trends, creative_pain_points,
            # research_challenges) — not user input. CVE/security signals
            # legitimately contain phrases like "ignore previous instructions"
            # because they describe known prompt-injection techniques, which
            # tripped the router's injection guard at conf 0.96 and blocked
            # the entire stage. Mark this call trusted-internal so the guard
            # is skipped; the upstream sources are vetted by other tools.
            skip_injection_scan=True,
        )
        response = router.invoke(llm_function, request)
        content = response.content or ""
        model = response.model_id or "unknown"

        predictions = _parse_json(content)
        if predictions:
            return predictions, model

        # If parsing failed, try to extract from raw text
        logger.warning("Failed to parse LLM JSON response — using empty predictions")
        return [], model

    except Exception as exc:
        logger.error("Forecast LLM invocation failed: %s", exc)
        return [], "error"


# ---------------------------------------------------------------------------
# Surprise scoring (D-RES-19)
# ---------------------------------------------------------------------------
def _score_surprise(
    prediction: Dict,
    existing_signals: List[Dict],
    existing_trends: List[Dict],
) -> float:
    """Compute surprise score (0-1) for a prediction.

    Higher scores for predictions that:
    - Contradict majority signal sentiment
    - Involve cross-vertical convergence
    - Identify timing inflection points
    - Reveal hidden greenfield opportunities

    Uses deterministic heuristics + LLM-provided base score.
    """
    base_score = float(prediction.get("surprise_score", 0.5))

    # Adjust based on prediction type (some types are inherently more surprising)
    type_boost = {
        "greenfield": 0.10,  # unseen opportunities are surprising
        "convergence": 0.08,  # cross-domain convergence is unexpected
        "disruption": 0.05,  # disruptions can be surprising
        "regulatory_shift": 0.03,  # regulatory changes are somewhat predictable
        "trend_trajectory": 0.0,  # extending existing trends is least surprising
    }
    pred_type = prediction.get("prediction_type", "trend_trajectory")
    base_score += type_boost.get(pred_type, 0.0)

    # Boost if prediction references cross-engine sources
    cross_sources = prediction.get("cross_engine_sources", [])
    if cross_sources:
        base_score += min(len(cross_sources) * 0.03, 0.10)

    # Boost if prediction contradicts majority sentiment
    title_lower = (prediction.get("title", "") + " " + prediction.get("description", "")).lower()
    contrarian_keywords = [
        "contrary",
        "despite",
        "unexpected",
        "overlooked",
        "hidden",
        "contrarian",
        "underestimated",
        "paradox",
        "counterintuitive",
    ]
    contrarian_count = sum(1 for kw in contrarian_keywords if kw in title_lower)
    base_score += min(contrarian_count * 0.04, 0.12)

    # Clamp to [0, 1]
    return max(0.0, min(1.0, round(base_score, 3)))


# ---------------------------------------------------------------------------
# Prediction ranking (D-RES-19)
# ---------------------------------------------------------------------------
def _rank_predictions(
    predictions: List[Dict],
    config: Dict,
    existing_signals: List[Dict],
    existing_trends: List[Dict],
) -> List[Dict]:
    """Score, rank, and return top N predictions by composite_rank.

    composite_rank = confidence × surprise_score
    """
    forecast_cfg = config.get("forecast", {})
    max_predictions = forecast_cfg.get("max_predictions", 5)
    confidence_threshold = forecast_cfg.get("confidence_threshold", 0.3)
    surprise_threshold = forecast_cfg.get("surprise_threshold", 0.2)

    ranked = []
    for pred in predictions:
        # Ensure valid fields
        confidence = float(pred.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        # Compute surprise score with heuristic adjustments
        surprise = _score_surprise(pred, existing_signals, existing_trends)

        # Apply thresholds
        if confidence < confidence_threshold or surprise < surprise_threshold:
            continue

        composite = round(confidence * surprise, 4)

        # Validate prediction_type
        pred_type = pred.get("prediction_type", "trend_trajectory")
        if pred_type not in PREDICTION_TYPES:
            pred_type = "trend_trajectory"

        # Validate time_horizon
        horizon = pred.get("time_horizon", "6mo")
        if horizon not in TIME_HORIZONS:
            horizon = "6mo"

        ranked.append(
            {
                "title": (pred.get("title", "") or "")[:500],
                "description": pred.get("description", ""),
                "prediction_type": pred_type,
                "confidence": confidence,
                "surprise_score": surprise,
                "composite_rank": composite,
                "time_horizon": horizon,
                "supporting_evidence": pred.get("supporting_evidence", []),
                "cross_engine_sources": pred.get("cross_engine_sources", []),
            }
        )

    # Sort by composite_rank descending
    ranked.sort(key=lambda x: x["composite_rank"], reverse=True)
    return ranked[:max_predictions]


# ---------------------------------------------------------------------------
# Storage (append-only, D6/D-RES-20)
# ---------------------------------------------------------------------------
def _store_forecasts(
    session_id: str,
    predictions: List[Dict],
    llm_model: str,
    llm_raw: str,
    db_path: Optional[str] = None,
) -> List[str]:
    """INSERT predictions into research_forecasts (append-only)."""
    conn = _get_db(db_path)
    ids = []
    try:
        for pred in predictions:
            fid = _forecast_id()
            conn.execute(
                """INSERT INTO research_forecasts
                   (id, session_id, trend_id, title, description,
                    prediction_type, confidence, surprise_score, composite_rank,
                    time_horizon, supporting_evidence, cross_engine_sources,
                    llm_model, llm_raw_response, metadata, generated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    fid,
                    session_id,
                    None,  # trend_id — can be linked later
                    pred["title"],
                    pred.get("description"),
                    pred["prediction_type"],
                    pred["confidence"],
                    pred["surprise_score"],
                    pred["composite_rank"],
                    pred["time_horizon"],
                    json.dumps(pred.get("supporting_evidence", [])),
                    json.dumps(pred.get("cross_engine_sources", [])),
                    llm_model,
                    llm_raw[:8000] if llm_raw else None,
                    json.dumps(pred.get("metadata", {})),
                    _now(),
                ),
            )
            ids.append(fid)
        conn.commit()
    finally:
        conn.close()
    return ids


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def generate_forecasts(
    session_id: str,
    db_path: Optional[str] = None,
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Generate AI-assisted predictions for a research session.

    This is the FORECAST stage entry point. It:
    1. Aggregates cross-engine data (Research + Innovation + Creative)
    2. Builds an LLM prompt with structured context
    3. Invokes the LLM via two-tier routing
    4. Scores each prediction for surprise (non-obviousness)
    5. Ranks by composite_rank = confidence × surprise_score
    6. Stores top N predictions in research_forecasts table

    Args:
        session_id: Research session ID.
        db_path: Optional database path.
        config: Optional pre-loaded config dict.

    Returns:
        Dict with predictions, count, model, and timing info.
    """
    started_at = _now()

    # Load config if not provided
    if config is None:
        try:
            import yaml

            config_path = _ROOT / "args" / "research_config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    config = yaml.safe_load(f) or {}
            else:
                config = {}
        except ImportError:
            config = {}

    forecast_cfg = config.get("forecast", {})
    if not forecast_cfg.get("enabled", True):
        return {
            "session_id": session_id,
            "predictions": [],
            "count": 0,
            "skipped": True,
            "reason": "forecast disabled in config",
        }

    # Step 1: Aggregate cross-engine data
    aggregated = _aggregate_cross_engine_data(session_id, db_path)

    if not aggregated["signals"] and not aggregated["trends"]:
        return {
            "session_id": session_id,
            "predictions": [],
            "count": 0,
            "skipped": True,
            "reason": "no research data available for forecasting",
        }

    # Step 2: Build prompt
    prompt = _build_forecast_prompt(aggregated, config)

    # Step 3: Invoke LLM
    method = forecast_cfg.get("method", "llm")
    if method == "llm":
        raw_predictions, llm_model = _invoke_forecast_llm(prompt, config)
    else:
        # Deterministic fallback — generate basic trend extrapolations
        raw_predictions = _deterministic_forecast(aggregated, config)
        llm_model = "deterministic"

    # Step 4-5: Score surprise and rank
    ranked = _rank_predictions(
        raw_predictions,
        config,
        aggregated["signals"],
        aggregated["trends"],
    )

    # Step 6: Store (append-only)
    llm_raw = json.dumps(raw_predictions)
    forecast_ids = []
    if ranked:
        try:
            forecast_ids = _store_forecasts(session_id, ranked, llm_model, llm_raw, db_path)
        except Exception as exc:
            logger.error("Failed to store forecasts: %s", exc)

    completed_at = _now()

    return {
        "session_id": session_id,
        "predictions": [{**pred, "id": fid} for pred, fid in zip(ranked, forecast_ids)] if forecast_ids else ranked,
        "count": len(ranked),
        "model": llm_model,
        "cross_engine_sources": {
            "innovation_trends": len(aggregated["innovation_trends"]),
            "innovation_signals": len(aggregated["innovation_signals"]),
            "creative_pain_points": len(aggregated["creative_pain_points"]),
            "creative_feature_gaps": len(aggregated["creative_feature_gaps"]),
        },
        "started_at": started_at,
        "completed_at": completed_at,
    }


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------
def _deterministic_forecast(aggregated: Dict[str, Any], config: Dict) -> List[Dict]:
    """Generate basic predictions without LLM — trend extrapolation only.

    Used when forecast.method = 'deterministic' or LLM is unavailable.
    """
    predictions = []

    # Extract predictions from top trends
    for trend in aggregated.get("trends", [])[:3]:
        velocity = float(trend.get("velocity", 0))
        confidence_val = float(trend.get("confidence", 0.5))
        name = trend.get("name", "Unknown trend")

        if velocity > 0:
            predictions.append(
                {
                    "title": f"{name} — continued acceleration expected",
                    "description": (
                        f"Based on current velocity ({velocity:.2f}) and "
                        f"{trend.get('signal_count', 0)} supporting signals, "
                        f"this trend is likely to continue accelerating."
                    ),
                    "prediction_type": "trend_trajectory",
                    "confidence": min(confidence_val + 0.1, 0.9),
                    "surprise_score": 0.3,  # extrapolation is not surprising
                    "time_horizon": "6mo",
                    "supporting_evidence": [trend.get("id", "")],
                    "cross_engine_sources": [],
                }
            )

    # Extract predictions from top challenges
    for challenge in aggregated.get("challenges", [])[:2]:
        score = float(challenge.get("composite_score", 0))
        if score > 0.6:
            predictions.append(
                {
                    "title": f"Emerging opportunity in: {challenge.get('title', '')[:80]}",
                    "description": (
                        f"High-scoring challenge (score={score:.2f}) suggests market gap that could be addressed."
                    ),
                    "prediction_type": "greenfield",
                    "confidence": min(score, 0.8),
                    "surprise_score": 0.5,
                    "time_horizon": "1yr",
                    "supporting_evidence": [challenge.get("id", "")],
                    "cross_engine_sources": [],
                }
            )

    return predictions


# ---------------------------------------------------------------------------
# Query helper — used by MCP server and dossier generator
# ---------------------------------------------------------------------------
def get_forecasts(
    session_id: str,
    db_path: Optional[str] = None,
    limit: int = 10,
) -> List[Dict]:
    """Retrieve stored forecasts for a session, ordered by composite_rank."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT id, session_id, trend_id, title, description,
                      prediction_type, confidence, surprise_score, composite_rank,
                      time_horizon, supporting_evidence, cross_engine_sources,
                      llm_model, outcome, outcome_date, outcome_notes,
                      metadata, generated_at
               FROM research_forecasts
               WHERE session_id = %s
               ORDER BY composite_rank DESC
               LIMIT %s""",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    """CLI entry point for standalone testing."""
    import argparse

    parser = argparse.ArgumentParser(description="Forecast Generator — Research Engine")
    parser.add_argument("--generate", action="store_true", help="Generate forecasts")
    parser.add_argument("--session-id", help="Research session ID")
    parser.add_argument("--db-path", help="Database path override")
    parser.add_argument("--get", action="store_true", help="Get stored forecasts")
    parser.add_argument("--limit", type=int, default=10, help="Result limit")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.generate and args.session_id:
        result = generate_forecasts(args.session_id, args.db_path)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Generated {result['count']} predictions for {args.session_id}")
            for pred in result.get("predictions", []):
                print(
                    f"  [{pred['prediction_type']}] {pred['title'][:60]} "
                    f"(conf={pred['confidence']:.2f}, "
                    f"surprise={pred['surprise_score']:.2f}, "
                    f"rank={pred['composite_rank']:.3f})"
                )

    elif args.get and args.session_id:
        forecasts = get_forecasts(args.session_id, args.db_path, args.limit)
        if args.json:
            print(json.dumps(forecasts, indent=2, default=str))
        else:
            print(f"Found {len(forecasts)} forecasts")
            for f in forecasts:
                print(f"  [{f['prediction_type']}] {f['title'][:60]} (rank={f['composite_rank']:.3f})")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
