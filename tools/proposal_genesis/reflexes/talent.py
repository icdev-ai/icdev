#!/usr/bin/env python3
# CUI // SP-CTI
"""R20: Talent Reflex — Talent Intelligence (section 3.16).

Monitors competitor job postings for competitive intelligence signals.
Analyzes hiring patterns to detect capability build-up and strategic shifts.

Schedule: daily scan, weekly aggregation.
GREEN tier (read-only DB queries + signal velocity metrics).
Scanner-tier only (zero Claude tokens — fully deterministic).
"""

import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.talent")

# ---------------------------------------------------------------------------
# Module-level fallback constants — Talent Reflex (R20) surge detection.
# AI-ify opp 5448, hardcoded_threshold -> anomaly_detection. The legacy static
# "count >= 5 postings = surge" rule is replaced by adaptive z-score outlier
# detection: a "surge" is a competitor whose hiring volume is a statistical
# anomaly vs the competitor population (> Nσ above the mean), not a fixed count.
# The static count survives only as a small-sample / zero-variance fallback.
# Overridable from proposal_genesis_config.yaml under reflexes.talent
# (velocity_threshold_zscore). Change config, not code.
# ---------------------------------------------------------------------------
_SURGE_COUNT_THRESHOLD = 5      # Static fallback: >= N postings = surge (legacy / small sample)
_DEFAULT_ZSCORE        = 2.0    # Hiring spikes > Nσ above the mean = anomaly
_MIN_SURGE_SAMPLES     = 3      # Need >= N competitors before a distribution is meaningful
_LOOKBACK_DAYS         = 30     # Default talent-signal lookback window (days)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgtal") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Talent signal analysis
# ---------------------------------------------------------------------------


def _get_recent_signals(days: int = 30) -> List[Dict]:
    """Get talent signals from the last N days."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, competitor_name, role_title, skill_tags, "
            "location, clearance_required, scan_date "
            "FROM pg_talent_signals "
            "WHERE scan_date >= datetime('now', %s) "
            "ORDER BY scan_date DESC "
            "LIMIT 200",
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _count_by_competitor(signals: List[Dict]) -> Dict[str, int]:
    """Tally signals per competitor_name."""
    counts: Dict[str, int] = {}
    for sig in signals:
        name = sig.get("competitor_name", "unknown")
        counts[name] = counts.get(name, 0) + 1
    return counts


def _compute_velocity(signals: List[Dict]) -> Dict[str, Any]:
    """Compute hiring velocity metrics per competitor."""
    if not signals:
        return {"competitors": {}, "total_signals": 0}

    competitor_counts = _count_by_competitor(signals)
    clearance_counts: Dict[str, int] = {}
    skill_counts: Dict[str, int] = {}

    for sig in signals:
        name = sig.get("competitor_name", "unknown")

        if sig.get("clearance_required"):
            clearance_counts[name] = clearance_counts.get(name, 0) + 1

        # Parse skill tags (stored as comma-separated or JSON)
        tags = sig.get("skill_tags") or ""
        if tags.startswith("["):
            try:
                tag_list = json.loads(tags)
            except (json.JSONDecodeError, ValueError):
                tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        else:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        for tag in tag_list:
            skill_counts[tag.lower()] = skill_counts.get(tag.lower(), 0) + 1

        if sig.get("clearance_required"):
            clearance_counts[name] = clearance_counts.get(name, 0) + 1

        # Parse skill tags (stored as comma-separated or JSON)
        tags = sig.get("skill_tags") or ""
        if tags.startswith("["):
            try:
                tag_list = json.loads(tags)
            except (json.JSONDecodeError, ValueError):
                tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        else:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        for tag in tag_list:
            skill_counts[tag.lower()] = skill_counts.get(tag.lower(), 0) + 1

    # Top competitors by hiring volume
    top_competitors = sorted(competitor_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Top skills in demand
    top_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)[:15]

    return {
        "competitors": dict(top_competitors),
        "clearance_heavy": {k: v for k, v in clearance_counts.items() if v >= 2},
        "top_skills": dict(top_skills),
        "total_signals": len(signals),
    }


def _compute_surge_threshold(
    competitor_counts: Dict[str, int],
    cfg: Dict[str, Any],
) -> float:
    """Compute an adaptive hiring-surge threshold from competitor counts.

    Uses mean + zscore * stddev when enough competitors are present, otherwise
    falls back to the static ``fallback_surge_count``. The result is floored by
    ``min_absolute_surge``.
    """
    enabled = cfg.get("enabled", True)
    if not enabled:
        return float(cfg.get("fallback_surge_count", _SURGE_COUNT_THRESHOLD))

    min_samples = int(cfg.get("min_samples", _MIN_SURGE_SAMPLES))
    zscore = float(cfg.get("velocity_threshold_zscore", _DEFAULT_ZSCORE))
    min_abs = int(cfg.get("min_absolute_surge", _SURGE_COUNT_THRESHOLD))
    fallback = float(cfg.get("fallback_surge_count", _SURGE_COUNT_THRESHOLD))

    counts = list(competitor_counts.values())
    n = len(counts)
    if n < min_samples:
        return fallback

    mean = sum(counts) / n
    stddev = math.sqrt(sum((c - mean) ** 2 for c in counts) / n) if n > 0 else 0.0
    threshold = mean + zscore * stddev
    return max(float(min_abs), threshold)


def _detect_surges(
    signals: List[Dict],
    threshold: int = _SURGE_COUNT_THRESHOLD,
    zscore_threshold: float = _DEFAULT_ZSCORE,
) -> List[Dict]:
    """Detect hiring surges via z-score anomaly detection.

    A competitor's posting volume is flagged as a "surge" when it is a
    statistical outlier relative to the competitor population — i.e. its
    z-score ``(count - mean) / stddev`` meets or exceeds ``zscore_threshold``
    (default 2σ). This adapts to the actual data instead of relying on a
    single static count, so a surge means "anomalously high vs peers" rather
    than "above an arbitrary number".

    Falls back to the static ``threshold`` count when the sample is too small
    (< ``_MIN_SURGE_SAMPLES`` competitors) or has zero spread (every
    competitor posted the same amount → no anomaly possible), so small or
    uniform datasets still behave sensibly.

    Deterministic / zero-token (GREEN tier) — pure statistics, no LLM.
    """
    competitor_counts: Dict[str, int] = {}
    for sig in signals:
        name = sig.get("competitor_name", "unknown")
        competitor_counts[name] = competitor_counts.get(name, 0) + 1

    if not competitor_counts:
        return []

    counts = list(competitor_counts.values())
    n = len(counts)
    mean = sum(counts) / n
    # Population standard deviation across the competitor cohort.
    stddev = math.sqrt(sum((c - mean) ** 2 for c in counts) / n)

    use_zscore = n >= _MIN_SURGE_SAMPLES and stddev > 0.0

    surges = []
    for name, count in competitor_counts.items():
        if use_zscore:
            zscore = (count - mean) / stddev
            is_surge = zscore >= zscore_threshold
        else:
            zscore = 0.0
            is_surge = count >= threshold

        if is_surge:
            surges.append(
                {
                    "competitor": name,
                    "postings": count,
                    "zscore": round(zscore, 2),
                    "method": "zscore" if use_zscore else "static",
                    "signal": "hiring_surge",
                }
            )

    return sorted(surges, key=lambda x: x["postings"], reverse=True)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Talent Reflex (R20).

    Steps:
      1. Query recent talent signals from pg_talent_signals
      2. Compute hiring velocity per competitor
      3. Detect hiring surges above threshold
      4. Identify clearance-heavy hiring (may indicate classified work)

    Returns standard reflex result dict.
    """
    lookback_days = config.get("talent_lookback_days", _LOOKBACK_DAYS)
    zscore_threshold = config.get("velocity_threshold_zscore", _DEFAULT_ZSCORE)

    # Anomaly-detection config (legacy top-level keys merge into sub-config)
    anomaly_cfg: Dict[str, Any] = dict(config.get("anomaly_detection", {}) or {})
    if "talent_surge_threshold" in config:
        anomaly_cfg.setdefault("fallback_surge_count", config["talent_surge_threshold"])
    if "velocity_threshold_zscore" in config:
        anomaly_cfg.setdefault("velocity_threshold_zscore", zscore_threshold)

    signals = _get_recent_signals(days=lookback_days)
    velocity = _compute_velocity(signals)
    competitor_counts = velocity.get("competitors", {})
    surge_threshold = _compute_surge_threshold(competitor_counts, anomaly_cfg)
    surges = _detect_surges(
        signals, threshold=int(surge_threshold), zscore_threshold=zscore_threshold
    )

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                "talent_scan",
                "talent",
                "green",
                json.dumps(
                    {
                        "signals_analyzed": len(signals),
                        "surges_detected": len(surges),
                        "lookback_days": lookback_days,
                        "zscore_threshold": zscore_threshold,
                    }
                ),
                1,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("run: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s", exc)
    finally:
        conn.close()

    return {
        "success": True,
        "metric_value": float(len(signals)),
        "details": {
            "signals_analyzed": len(signals),
            "lookback_days": lookback_days,
            "zscore_threshold": zscore_threshold,
            "surge_threshold": surge_threshold,
            "top_competitors": velocity.get("competitors", {}),
            "top_skills": velocity.get("top_skills", {}),
            "clearance_heavy_hiring": velocity.get("clearance_heavy", {}),
            "hiring_surges": surges,
        },
    }


# CUI // SP-CTI
