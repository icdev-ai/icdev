# CUI // SP-CTI
"""Genesis Continuous Evaluation Harness (Phase 1 — Observability).

Records reflex decisions and their actual outcomes in the append-only
``harness_eval`` table. Computes precision, recall, ECE, and false-heal rate
so the ``harness`` reflex can surface degradation cards to Kanban.

Usage (from any reflex):
    from tools.genesis.harness.eval_harness import record_decision, record_outcome

    # After oracle_triage promotes/dismisses a task:
    record_decision(task_id="kt-123", reflex="oracle_triage",
                    decision="promote", confidence=0.82)

    # After the task resolves (called by kanban reflex on completion):
    record_outcome(task_id="kt-123", actual_outcome="resolved")

    # Harness reflex calls this every 6h:
    metrics = compute_metrics("oracle_triage", window_days=30)
    gates   = check_gates()
"""
from __future__ import annotations

import json

from tools.logging.icdev_logger import get_logger
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

LOG = get_logger(__name__)

# Thresholds that trigger a degradation alert
_GATE_PRECISION_MIN = 0.80
_GATE_ECE_MAX = 0.15
_GATE_FALSE_HEAL_MAX = 0.20
_GATE_HEAL_SUCCESS_MIN = 0.60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_decision(
    task_id: str,
    reflex: str,
    decision: str,
    confidence: float | None = None,
    metadata: dict | None = None,
) -> str:
    """Insert one harness_eval row for a reflex decision.

    Parameters
    ----------
    task_id:    Kanban task ID or failure ID being decided on.
    reflex:     Originating reflex name (oracle_triage | heal | harness …).
    decision:   What the reflex decided (promote | dismiss | heal | skip | rate_limited).
    confidence: Score used by the reflex (0.0–1.0); None if not applicable.
    metadata:   Extra context stored as JSON.

    Returns the new row ID.
    """
    row_id = str(uuid.uuid4())
    try:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO harness_eval
                (id, task_id, reflex, decision, confidence, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                task_id or "",
                reflex,
                decision,
                confidence,
                json.dumps(metadata or {}),
                _utcnow(),
            ),
        )
        conn.commit()
    except Exception as exc:
        LOG.warning("[harness] record_decision failed: %s", exc)
    return row_id


def record_outcome(
    task_id: str,
    actual_outcome: str,
) -> None:
    """Update harness_eval rows for task_id with the actual outcome.

    actual_outcome: resolved | false_positive | self_resolved | failed | pending
    """
    try:
        conn = _conn()
        conn.execute(
            """
            UPDATE harness_eval
               SET actual_outcome = ?,
                   resolved_at    = ?
             WHERE task_id = ?
               AND actual_outcome IS NULL
            """,
            (actual_outcome, _utcnow(), task_id),
        )
        conn.commit()
    except Exception as exc:
        LOG.warning("[harness] record_outcome failed for %s: %s", task_id, exc)


def compute_metrics(reflex: str, window_days: int = 30) -> dict[str, Any]:
    """Compute evaluation metrics for a reflex over the last window_days.

    Returns
    -------
    dict with keys:
        precision, recall, ece, false_heal_rate, heal_success_rate,
        total_decisions, resolved_count, window_days
    """
    try:
        conn = _conn()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat(timespec="seconds")

        rows = conn.execute(
            """
            SELECT decision, confidence, actual_outcome
              FROM harness_eval
             WHERE reflex = ?
               AND created_at >= ?
            """,
            (reflex, cutoff),
        ).fetchall()
    except Exception as exc:
        LOG.warning("[harness] compute_metrics query failed: %s", exc)
        return {"error": str(exc)}

    if not rows:
        return {"reflex": reflex, "window_days": window_days, "total_decisions": 0}

    total = len(rows)
    resolved = [r for r in rows if r["actual_outcome"] == "resolved"]
    false_pos = [r for r in rows if r["actual_outcome"] == "false_positive"]
    self_res = [r for r in rows if r["actual_outcome"] == "self_resolved"]

    # Precision: resolved / (resolved + false_positive)
    denom_p = len(resolved) + len(false_pos)
    precision = len(resolved) / denom_p if denom_p else None

    # Recall: resolved / total with known outcomes
    known = [r for r in rows if r["actual_outcome"] is not None]
    recall = len(resolved) / len(known) if known else None

    # Expected Calibration Error (ECE) over decile bins
    ece = _compute_ece([r for r in rows if r["confidence"] is not None and r["actual_outcome"] is not None])

    # False-heal rate: self_resolved / (resolved + self_resolved) for heal reflex
    heal_total = len(resolved) + len(self_res)
    false_heal_rate = len(self_res) / heal_total if heal_total and reflex == "heal" else None

    # Heal success rate: resolved / (resolved + failed) for heal actions
    failed = [r for r in rows if r["actual_outcome"] == "failed"]
    heal_denom = len(resolved) + len(failed)
    heal_success_rate = len(resolved) / heal_denom if heal_denom and reflex == "heal" else None

    return {
        "reflex": reflex,
        "window_days": window_days,
        "total_decisions": total,
        "resolved_count": len(resolved),
        "false_positive_count": len(false_pos),
        "self_resolved_count": len(self_res),
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "ece": round(ece, 4) if ece is not None else None,
        "false_heal_rate": round(false_heal_rate, 4) if false_heal_rate is not None else None,
        "heal_success_rate": round(heal_success_rate, 4) if heal_success_rate is not None else None,
    }


def check_gates() -> list[dict[str, Any]]:
    """Run all threshold checks across oracle_triage and heal reflexes.

    Returns a list of degradation alerts, each a dict with:
        reflex, metric, value, threshold, severity, recommendation
    Empty list = all gates green.
    """
    alerts: list[dict] = []

    for reflex in ("oracle_triage", "heal"):
        m = compute_metrics(reflex)
        if "error" in m or m.get("total_decisions", 0) < 10:
            continue  # not enough data yet

        precision = m.get("precision")
        if precision is not None and precision < _GATE_PRECISION_MIN:
            alerts.append({
                "reflex": reflex,
                "metric": "precision",
                "value": precision,
                "threshold": _GATE_PRECISION_MIN,
                "severity": "high",
                "recommendation": (
                    f"{reflex} precision {precision:.0%} < {_GATE_PRECISION_MIN:.0%}. "
                    "Extract top-10 error cases from harness_eval and run prompt refinement pass. "
                    "Check args/oracle_heuristics.yaml."
                ),
            })

        ece = m.get("ece")
        if ece is not None and ece > _GATE_ECE_MAX:
            alerts.append({
                "reflex": reflex,
                "metric": "ece",
                "value": ece,
                "threshold": _GATE_ECE_MAX,
                "severity": "medium",
                "recommendation": (
                    f"{reflex} ECE {ece:.3f} > {_GATE_ECE_MAX}. "
                    "Confidence scores are miscalibrated. "
                    "Enable ICDEV_HARNESS_COLEARN=true to run DSPy-style prompt rewrite."
                ),
            })

        false_heal = m.get("false_heal_rate")
        if false_heal is not None and false_heal > _GATE_FALSE_HEAL_MAX:
            alerts.append({
                "reflex": "heal",
                "metric": "false_heal_rate",
                "value": false_heal,
                "threshold": _GATE_FALSE_HEAL_MAX,
                "severity": "medium",
                "recommendation": (
                    f"False-heal rate {false_heal:.0%} > {_GATE_FALSE_HEAL_MAX:.0%}. "
                    "Heals are triggering on self-resolving anomalies. "
                    "Add a pre-heal wait window or tighten the confidence gate in args/genesis_config.yaml."
                ),
            })

        heal_success = m.get("heal_success_rate")
        if heal_success is not None and heal_success < _GATE_HEAL_SUCCESS_MIN:
            alerts.append({
                "reflex": "heal",
                "metric": "heal_success_rate",
                "value": heal_success,
                "threshold": _GATE_HEAL_SUCCESS_MIN,
                "severity": "high",
                "recommendation": (
                    f"Heal success rate {heal_success:.0%} < {_GATE_HEAL_SUCCESS_MIN:.0%}. "
                    "Review action type breakdown in harness_eval. "
                    "Demote low-success action types in healing_patterns table."
                ),
            })

    return alerts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_ece(rows: list) -> float | None:
    """Expected Calibration Error over 10 confidence decile bins."""
    if len(rows) < 5:
        return None

    bins: dict[int, list] = {i: [] for i in range(10)}
    for r in rows:
        conf = float(r["confidence"]) if r["confidence"] is not None else 0.5
        outcome = r["actual_outcome"] == "resolved"
        bin_idx = min(int(conf * 10), 9)
        bins[bin_idx].append((conf, outcome))

    ece = 0.0
    n = len(rows)
    for items in bins.values():
        if not items:
            continue
        avg_conf = sum(c for c, _ in items) / len(items)
        avg_acc = sum(1 for _, o in items if o) / len(items)
        ece += (len(items) / n) * abs(avg_conf - avg_acc)

    return ece
