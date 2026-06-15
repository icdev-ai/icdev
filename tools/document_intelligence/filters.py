# CUI // SP-CTI
"""DIC Document Filters — anomaly-detection-based adaptive filtering.

Replaces hardcoded relevance/size/age thresholds with statistically-derived
bounds computed from the live document corpus.  Anomalies are flagged for
HITL review rather than silently dropped.

Algorithms
----------
* IQR fence  — median ± k * IQR  (robust, low-sample-count friendly)
* Z-score     — mean ± z * std    (fast, large-corpus default)

Public surface
--------------
    filter_by_relevance(docs, scores)
    filter_by_size(docs)
    filter_by_age(docs)
    anomaly_report(docs) -> dict
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── Fallback floor/ceiling constants (min corpus data) ───────────────────────
# These are LAST-RESORT fallbacks, not operating thresholds.
_MIN_RELEVANCE: float = 0.05
_MAX_DOC_SIZE_MB: float = 500.0
_MAX_AGE_DAYS: float = 3650.0  # 10 years


def _iqr_bounds(values: list[float], k: float = 1.5) -> tuple[float, float]:
    """Return (lower, upper) IQR fence.  Requires ≥4 values."""
    if len(values) < 4:
        return (0.0, float("inf"))
    sorted_v = sorted(values)
    q1 = statistics.median(sorted_v[: len(sorted_v) // 2])
    q3 = statistics.median(sorted_v[(len(sorted_v) + 1) // 2 :])
    iqr = q3 - q1
    return (q1 - k * iqr, q3 + k * iqr)


def _zscore_bounds(values: list[float], z: float = 2.5) -> tuple[float, float]:
    """Return (lower, upper) z-score bounds.  Requires ≥2 values."""
    if len(values) < 2:
        return (0.0, float("inf"))
    mu = statistics.mean(values)
    sigma = statistics.pstdev(values) or 1e-9
    return (mu - z * sigma, mu + z * sigma)


def _adaptive_bounds(
    values: list[float], *, z: float = 2.5, k: float = 1.5
) -> tuple[float, float]:
    """Choose IQR (robust, <30 samples) or z-score (fast, ≥30 samples)."""
    if len(values) >= 30:
        return _zscore_bounds(values, z=z)
    return _iqr_bounds(values, k=k)


# ── Public filter functions ───────────────────────────────────────────────────

def filter_by_relevance(
    docs: list[dict[str, Any]],
    scores: list[float],
    *,
    floor: float = _MIN_RELEVANCE,
) -> tuple[list[dict], list[dict]]:
    """Return (kept, anomalies) split by adaptive relevance threshold.

    Args:
        docs:   List of document dicts (must include at least {"doc_id": ...}).
        scores: Parallel relevance scores in [0, 1].
        floor:  Hard minimum — never keep a doc below this regardless of stats.

    Returns:
        kept      — docs that pass the adaptive threshold
        anomalies — outlier docs flagged for HITL review
    """
    if not docs or len(docs) != len(scores):
        logger.warning("filter_by_relevance: docs/scores length mismatch")
        return list(docs), []

    lb, _ = _adaptive_bounds(scores)
    threshold = max(lb, floor)

    kept, anomalies = [], []
    for doc, score in zip(docs, scores):
        if score >= threshold:
            kept.append({**doc, "_relevance_score": score, "_filter": "pass"})
        else:
            anomalies.append(
                {**doc, "_relevance_score": score, "_filter": "anomaly",
                 "_reason": f"score {score:.3f} below adaptive threshold {threshold:.3f}"}
            )
    logger.info(
        "filter_by_relevance: kept=%d anomalies=%d threshold=%.3f",
        len(kept), len(anomalies), threshold,
    )
    return kept, anomalies


def filter_by_size(
    docs: list[dict[str, Any]],
    *,
    size_key: str = "file_size_bytes",
    ceiling_mb: float = _MAX_DOC_SIZE_MB,
) -> tuple[list[dict], list[dict]]:
    """Return (kept, anomalies) split by adaptive file-size threshold.

    Anomalies are documents whose size is a statistical outlier on the
    HIGH end (unusually large) or that exceed the absolute ceiling.
    """
    sizes_mb = [
        (d.get(size_key) or 0) / 1_048_576
        for d in docs
    ]
    _, ub = _adaptive_bounds(sizes_mb)
    effective_ceiling = min(ub, ceiling_mb)

    kept, anomalies = [], []
    for doc, size_mb in zip(docs, sizes_mb):
        if size_mb <= effective_ceiling:
            kept.append({**doc, "_size_mb": size_mb, "_filter": "pass"})
        else:
            anomalies.append(
                {**doc, "_size_mb": size_mb, "_filter": "anomaly",
                 "_reason": f"size {size_mb:.1f}MB exceeds adaptive ceiling {effective_ceiling:.1f}MB"}
            )
    logger.info(
        "filter_by_size: kept=%d anomalies=%d ceiling_mb=%.1f",
        len(kept), len(anomalies), effective_ceiling,
    )
    return kept, anomalies


def filter_by_age(
    docs: list[dict[str, Any]],
    *,
    date_key: str = "created_at",
    ceiling_days: float = _MAX_AGE_DAYS,
) -> tuple[list[dict], list[dict]]:
    """Return (kept, anomalies) split by adaptive document age threshold.

    Very old documents (outliers on the high end) are surfaced for review
    rather than silently filtered out.
    """
    now = datetime.now(timezone.utc)

    def _age(doc: dict) -> float:
        raw = doc.get(date_key)
        if not raw:
            return 0.0
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (now - dt).total_seconds() / 86400.0
        except (ValueError, TypeError):
            return 0.0

    ages = [_age(d) for d in docs]
    _, ub = _adaptive_bounds(ages)
    effective_ceiling = min(ub, ceiling_days)

    kept, anomalies = [], []
    for doc, age in zip(docs, ages):
        if age <= effective_ceiling:
            kept.append({**doc, "_age_days": age, "_filter": "pass"})
        else:
            anomalies.append(
                {**doc, "_age_days": age, "_filter": "anomaly",
                 "_reason": f"age {age:.0f}d exceeds adaptive ceiling {effective_ceiling:.0f}d"}
            )
    logger.info(
        "filter_by_age: kept=%d anomalies=%d ceiling_days=%.0f",
        len(kept), len(anomalies), effective_ceiling,
    )
    return kept, anomalies


# ── Composite report ─────────────────────────────────────────────────────────

def anomaly_report(docs: list[dict[str, Any]], scores: list[float] | None = None) -> dict:
    """Return a summary anomaly report across all filter dimensions.

    Useful for the DIC dashboard heatmap and HITL review queue.
    """
    report: dict[str, Any] = {
        "total": len(docs),
        "dimensions": {},
        "anomaly_doc_ids": [],
    }

    if scores and len(scores) == len(docs):
        _, rel_anom = filter_by_relevance(docs, scores)
        report["dimensions"]["relevance"] = {
            "anomaly_count": len(rel_anom),
            "anomaly_doc_ids": [d.get("doc_id") for d in rel_anom],
        }
        for d in rel_anom:
            _id = d.get("doc_id")
            if _id and _id not in report["anomaly_doc_ids"]:
                report["anomaly_doc_ids"].append(_id)

    _, size_anom = filter_by_size(docs)
    report["dimensions"]["size"] = {
        "anomaly_count": len(size_anom),
        "anomaly_doc_ids": [d.get("doc_id") for d in size_anom],
    }
    for d in size_anom:
        _id = d.get("doc_id")
        if _id and _id not in report["anomaly_doc_ids"]:
            report["anomaly_doc_ids"].append(_id)

    _, age_anom = filter_by_age(docs)
    report["dimensions"]["age"] = {
        "anomaly_count": len(age_anom),
        "anomaly_doc_ids": [d.get("doc_id") for d in age_anom],
    }
    for d in age_anom:
        _id = d.get("doc_id")
        if _id and _id not in report["anomaly_doc_ids"]:
            report["anomaly_doc_ids"].append(_id)

    report["total_anomalies"] = len(report["anomaly_doc_ids"])
    return report
