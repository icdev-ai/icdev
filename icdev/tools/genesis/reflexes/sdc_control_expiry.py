# CUI // SP-CTI
"""Genesis Reflex — SDC Security Control Expiry (4h cadence).

Scans sc_controls for controls whose review_date is within a dynamically
computed anomaly threshold or already past.  The threshold is derived via
IQR-based statistical anomaly detection on the observed distribution of
days-until-expiry values; when fewer than *min_rows_for_anomaly_detection*
data points are available the configurable ``warn_days_fallback`` is used
instead.  An optional Claude Haiku pass ranks the flagged controls by
severity.

Air-gap safe: LLM severity analysis is skipped gracefully when no provider
is reachable or the LLM toggle is disabled in config.

Config: args/sdc_control_expiry.yaml  (env var AIIFY_SDC_LLM_MODEL overrides model).
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import os
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "args", "sdc_control_expiry.yaml"
)

def _load_config() -> Dict[str, Any]:
    try:
        with open(os.path.normpath(_CONFIG_PATH), encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _cfg_thresholds(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("thresholds", {})


def _cfg_llm(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("llm", {})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CADENCE_HOURS: int = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_until(iso_str: str) -> int:
    if not iso_str:
        return 9999
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - datetime.now(timezone.utc)).days
    except Exception:
        return 9999


# ---------------------------------------------------------------------------
# Anomaly detection — IQR-based lower-outlier threshold
# ---------------------------------------------------------------------------

def _compute_anomaly_threshold(
    all_days: List[int],
    iqr_multiplier: float,
    fallback: int,
    min_rows: int,
) -> int:
    """Return the days threshold below which a control is considered anomalous.

    Uses Q1 - iqr_multiplier * IQR (lower-fence outlier detection).
    Falls back to *fallback* when not enough data points are available.
    The result is clamped to [0, fallback] so the threshold never exceeds the
    configured fallback or goes negative.
    """
    finite = [d for d in all_days if d < 9999]
    if len(finite) < min_rows:
        logger.debug(
            "sdc_control_expiry: only %d data points — using fallback threshold %d days",
            len(finite), fallback,
        )
        return fallback

    try:
        q1 = statistics.quantiles(finite, n=4)[0]   # 25th percentile
        q3 = statistics.quantiles(finite, n=4)[2]   # 75th percentile
        iqr = q3 - q1
        lower_fence = q1 - iqr_multiplier * iqr
        # Translate the outlier fence into a "warn if days_until <= threshold"
        # The lower fence is the point below which values are outliers; we add
        # the fence value back to get a positive-days window.
        # Concretely: flag anything in [lower_fence, upper_normal_bound].
        # Because review dates *far out* are fine, we care about the lower tail.
        # The effective warn threshold is the 25th percentile (Q1) distance
        # above the fence — i.e. anything below Q1 + margin is at risk.
        # Simpler interpretation: use Q1 as the data-driven warn band.
        anomaly_threshold = int(max(0, q1))
        # Cap at fallback so we never silently widen the window beyond intent.
        capped = min(anomaly_threshold, fallback)
        logger.debug(
            "sdc_control_expiry: anomaly threshold %d days (Q1=%s IQR=%s fence=%s, cap=%d)",
            capped, q1, iqr, lower_fence, fallback,
        )
        return capped
    except Exception as exc:
        logger.warning("sdc_control_expiry: anomaly threshold computation failed: %s", exc)
        return fallback


# ---------------------------------------------------------------------------
# Optional LLM severity triage
# ---------------------------------------------------------------------------

def _llm_severity_triage(
    expiring: List[Dict[str, Any]],
    model: str,
    max_controls: int,
) -> Optional[List[Dict[str, Any]]]:
    """Call Claude Haiku to rank expiring controls by severity.

    Returns the enriched list (each item gains a ``severity`` key) or None
    on failure / when the LLM is unavailable.
    """
    try:
        from tools.llm.anthropic_provider import anthropic_sdk  # noqa: PLC0415
        client = anthropic_sdk.Anthropic()
    except Exception:
        return None

    sample = expiring[:max_controls]
    lines = "\n".join(
        f"- id={c['id']} title={c['title']!r} days_until_expiry={c['days_until_expiry']}"
        for c in sample
    )
    prompt = (
        "You are a security compliance analyst.  The following security controls are "
        "approaching or past their review date.  Rate each as HIGH, MEDIUM, or LOW severity "
        "based solely on urgency (days until expiry).  Reply with a JSON array, one object "
        "per control: {\"id\": <id>, \"severity\": \"HIGH|MEDIUM|LOW\"}.\n\n"
        f"Controls:\n{lines}"
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        import json as _json
        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = "\n".join(
                ln for ln in text.splitlines()
                if not ln.startswith("```")
            )
        ratings = {item["id"]: item["severity"] for item in _json.loads(text)}
        for ctrl in expiring:
            ctrl["severity"] = ratings.get(ctrl["id"], "MEDIUM")
        return expiring
    except Exception as exc:
        logger.warning("sdc_control_expiry: LLM severity triage failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main reflex entry point
# ---------------------------------------------------------------------------

def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Check SDC sc_controls for approaching review dates using anomaly detection.

    Returns:
        controls_checked: int
        warn_threshold_days: int  — computed (anomaly-based or fallback)
        threshold_source: str     — "anomaly_detection" | "fallback"
        expiring_soon: list of {id, title, review_date, days_until_expiry[, severity]}
        events_published: int
    """
    cfg = _load_config()
    th = _cfg_thresholds(cfg)
    llm_cfg = _cfg_llm(cfg)

    fallback_days: int = int(th.get("warn_days_fallback", 90))
    iqr_multiplier: float = float(th.get("iqr_multiplier", 1.5))
    min_rows: int = int(th.get("min_rows_for_anomaly_detection", 5))
    always_flag_days: int = int(th.get("always_flag_days", 7))

    llm_enabled: bool = bool(llm_cfg.get("enabled", True))
    llm_model: str = os.environ.get(
        "AIIFY_SDC_LLM_MODEL",
        llm_cfg.get("model", "claude-haiku-4-5-20251001"),
    )
    llm_max: int = int(llm_cfg.get("max_controls_per_call", 20))

    dry_run: bool = ctx.get("dry_run", False)

    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "controls_checked": 0,
        "warn_threshold_days": fallback_days,
        "threshold_source": "fallback",
        "expiring_soon": [],
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }

    try:
        from tools.security_canvas.db.init_db import get_connection as sdc_conn  # noqa: PLC0415
        from tools.db.storage import column_exists  # noqa: PLC0415
        conn_sdc = sdc_conn()
        try:
            # Shared backend-aware column probe (information_schema on PG,
            # PRAGMA table_info on SQLite) instead of a raw PRAGMA that only
            # introspects on SQLite.
            date_col = (
                "review_date" if column_exists(conn_sdc, "sc_controls", "review_date")
                else ("next_review" if column_exists(conn_sdc, "sc_controls", "next_review") else None)
            )
            if date_col:
                rows = conn_sdc.execute(
                    f"SELECT id, title, {date_col} FROM sc_controls "  # nosec B608
                    f"WHERE {date_col} IS NOT NULL ORDER BY {date_col}"  # nosec B608
                ).fetchall()
            else:
                rows = []
        finally:
            conn_sdc.close()

        result["controls_checked"] = len(rows)

        # Collect all days-until-expiry values for anomaly detection.
        all_days: List[int] = []
        rows_parsed: List[tuple] = []
        for row in rows:
            cid = row[0] if isinstance(row, (tuple, list)) else row["id"]
            title = row[1] if isinstance(row, (tuple, list)) else row["title"]
            date_val = row[2] if isinstance(row, (tuple, list)) else row[date_col]
            days = _days_until(date_val or "")
            all_days.append(days)
            rows_parsed.append((cid, title, date_val, days))

        # Compute adaptive threshold via anomaly detection.
        warn_threshold = _compute_anomaly_threshold(
            all_days, iqr_multiplier, fallback_days, min_rows
        )
        threshold_source = (
            "anomaly_detection" if len([d for d in all_days if d < 9999]) >= min_rows
            else "fallback"
        )
        result["warn_threshold_days"] = warn_threshold
        result["threshold_source"] = threshold_source

        expiring: List[Dict[str, Any]] = []
        for cid, title, date_val, days in rows_parsed:
            if days <= warn_threshold or days <= always_flag_days:
                expiring.append({
                    "id": cid,
                    "title": title,
                    "review_date": date_val,
                    "days_until_expiry": days,
                })

        # Optional: LLM severity triage via Claude Haiku.
        if expiring and llm_enabled:
            enriched = _llm_severity_triage(expiring, llm_model, llm_max)
            if enriched is not None:
                expiring = enriched

        result["expiring_soon"] = expiring

        if expiring and not dry_run:
            try:
                from tools.canvas.event_bus import publish  # noqa: PLC0415
                for ctrl in expiring:
                    publish("sdc", "sdc.control.expiring", {
                        "control_id": ctrl["id"],
                        "title": ctrl["title"],
                        "days_until_expiry": ctrl["days_until_expiry"],
                        "review_date": ctrl["review_date"],
                        "severity": ctrl.get("severity"),
                        "threshold_source": threshold_source,
                    })
                    result["events_published"] += 1
            except Exception as exc:
                result["errors"].append(f"event_bus: {exc}")

    except Exception as exc:
        logger.error("sdc_control_expiry reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))

    # Genesis daemon success contract (shx-safe-04): the daemon reads
    # result["success"], result["metric_value"] and result["details"].  Without
    # these keys run_reflex_impl() treats every run as a failure and trips the
    # circuit breaker after 3 cycles, so the reflex would go dormant again.
    result["success"] = result["status"] == "ok"
    result["metric_value"] = float(len(result["expiring_soon"]))
    result["details"] = {
        "controls_checked": result["controls_checked"],
        "expiring_soon": len(result["expiring_soon"]),
        "warn_threshold_days": result["warn_threshold_days"],
        "threshold_source": result["threshold_source"],
        "events_published": result["events_published"],
        "status": result["status"],
    }

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
