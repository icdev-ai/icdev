# CUI // SP-CTI
"""Genesis Reflex — PDC Pipeline Staleness Alert (6h cadence).

Flags pipelines in the Pipeline Design Canvas that have not been updated
based on anomaly detection of update-interval distribution across all pipelines.
Falls back to STALE_DAYS_FLOOR when insufficient data is available.

Air-gap safe: LLM enrichment is optional; pure statistical fallback always runs.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"
import json
import re
import statistics
from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = get_logger(__name__)

CADENCE_HOURS = 6
STALE_DAYS_FLOOR = 14       # Hard minimum — never flag pipelines updated within this window
_ANOMALY_MODEL = "claude-haiku-4-5-20251001"
_MIN_PIPELINES_FOR_STATS = 3  # Need at least this many to compute a meaningful IQR threshold


def _days_since(iso_str: str) -> int:
    if not iso_str:
        return 999
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 999


def _compute_anomaly_threshold(day_values: List[int]) -> int:
    """Compute IQR-based staleness threshold from observed update intervals.

    Uses Tukey's fence (Q3 + 1.5 * IQR) with STALE_DAYS_FLOOR as minimum.
    Returns STALE_DAYS_FLOOR when fewer than _MIN_PIPELINES_FOR_STATS values.
    """
    valid = sorted(d for d in day_values if d < 999)
    if len(valid) < _MIN_PIPELINES_FOR_STATS:
        return STALE_DAYS_FLOOR
    n = len(valid)
    q1 = statistics.median(valid[: n // 2])
    q3 = statistics.median(valid[(n + 1) // 2 :])
    iqr = q3 - q1
    return max(int(q3 + 1.5 * iqr), STALE_DAYS_FLOOR)


def _llm_assess_staleness(
    pipelines: List[Dict],
    threshold: int,
    stats: Dict,
) -> Optional[List[Dict]]:
    """Ask the LLM to validate which flagged pipelines are genuinely anomalous.

    Returns refined list on success, None on any failure (air-gap safe).
    """
    if not pipelines:
        return None
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        prompt = (
            "You are a pipeline health anomaly detector.\n\n"
            f"Update-interval statistics across all pipelines:\n{json.dumps(stats, indent=2)}\n\n"
            f"Dynamic staleness threshold: {threshold} days\n\n"
            f"Candidate stale pipelines:\n{json.dumps(pipelines, indent=2)}\n\n"
            "For each pipeline decide if it is genuinely anomalous (stale) or a false positive "
            "(e.g. a known slow-update pipeline). Output a JSON array with objects containing: "
            "id, name, days_since_update, is_anomaly (bool), reason (string). "
            "Reply ONLY with the JSON array — no markdown, no extra text."
        )
        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a pipeline staleness anomaly detection engine. Reply only with JSON.",
            model=_ANOMALY_MODEL,
            max_tokens=512,
            temperature=0.0,
            agent_id="pdc-pipeline-stale-anomaly",
            classification="CUI",
            effort="low",
            skip_injection_scan=True,
        )
        response = router.invoke("anomaly_detection", request)
        raw = response.content or ""
        text = re.sub(r"```(?:json)?|```", "", raw).strip()
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [p for p in parsed if p.get("is_anomaly", True)]
    except Exception as exc:
        logger.debug("LLM staleness assessment skipped (%s), using statistical result", exc)
    return None


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Identify pipelines with anomalously long update gaps.

    Uses IQR-based anomaly detection on the distribution of update intervals
    across all pipelines. Falls back to STALE_DAYS_FLOOR when data is sparse.
    LLM validation (claude-haiku) is optional and air-gap safe.

    Returns:
        pipelines_checked: int
        stale_pipelines: list of {id, name, days_since_update}
        threshold_used: int  -- dynamic threshold or STALE_DAYS_FLOOR fallback
        events_published: int
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "pipelines_checked": 0,
        "stale_pipelines": [],
        "threshold_used": STALE_DAYS_FLOOR,
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }
    try:
        from tools.pipeline.db.init_db import get_connection as pdc_conn
        conn_pdc = pdc_conn()
        try:
            rows = conn_pdc.execute(
                "SELECT id, name, updated_at FROM pipelines ORDER BY updated_at"
            ).fetchall()
        finally:
            conn_pdc.close()

        result["pipelines_checked"] = len(rows)

        pipeline_ages: List[Dict] = []
        for row in rows:
            pid = row["id"] if isinstance(row, dict) else row[0]
            name = row["name"] if isinstance(row, dict) else row[1]
            updated_at = row["updated_at"] if isinstance(row, dict) else row[2]
            days = _days_since(updated_at or "")
            pipeline_ages.append({"id": pid, "name": name, "days_since_update": days})

        # Derive adaptive threshold from the observed update-interval distribution
        all_days = [p["days_since_update"] for p in pipeline_ages]
        threshold = _compute_anomaly_threshold(all_days)
        result["threshold_used"] = threshold

        valid_days = [d for d in all_days if d < 999]
        stats: Dict = {}
        if valid_days:
            stats = {
                "count": len(valid_days),
                "mean_days": round(sum(valid_days) / len(valid_days), 1),
                "min_days": min(valid_days),
                "max_days": max(valid_days),
                "threshold": threshold,
            }

        stale = [p for p in pipeline_ages if p["days_since_update"] >= threshold]

        # Optional LLM pass — filters false positives from candidate list
        llm_refined = _llm_assess_staleness(stale, threshold, stats)
        if llm_refined is not None:
            stale = llm_refined

        result["stale_pipelines"] = stale

        if stale and not dry_run:
            try:
                from tools.canvas.event_bus import publish
                for p in stale:
                    publish("pdc", "pdc.pipeline.stale", {
                        "pipeline_id": p["id"],
                        "pipeline_name": p["name"],
                        "days_since_update": p["days_since_update"],
                    })
                    result["events_published"] += 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

    except Exception as exc:
        logger.error("pdc_pipeline_stale reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    return result


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    import json as _json
    print(_json.dumps(run({}), indent=2))
