# CUI // SP-CTI
"""Genesis Continuous Evaluation Harness (Phase 1 — Observability).

Records reflex decisions and their actual outcomes in the append-only
``harness_eval`` table. Computes precision, recall, ECE, and false-heal rate
so the ``harness`` reflex can surface degradation cards to Kanban.

Gate thresholds are loaded from ``args/genesis_config.yaml::harness.gates``
instead of being hardcoded. When enough historical window data exists
(>= harness.anomaly_detection.min_samples), thresholds are tightened
adaptively via z-score anomaly detection (mean ± z*std), always at least
as strict as the static floors/ceilings in config.

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
import statistics
from pathlib import Path

from tools.logging.icdev_logger import get_logger
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

LOG = get_logger(__name__)

# Static fallback values — active when anomaly detector lacks sufficient history
# or when args/genesis_config.yaml (harness.gates) is absent.
_DEFAULT_GATES = {
    "precision_min": 0.80,
    "ece_max": 0.15,
    "false_heal_max": 0.20,
    "heal_success_min": 0.60,
    "min_decisions": 10,
}


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_harness_config() -> dict:
    """Load harness section from genesis_config.yaml; return defaults on failure.

    Walks up from __file__ to find the project root so the function works
    whether this file lives under tools/ or icdev/tools/.
    """
    search = Path(__file__).resolve()
    config_path: Path | None = None
    for parent in search.parents:
        candidate = parent / "args" / "genesis_config.yaml"
        if candidate.exists():
            config_path = candidate
            break

    if config_path is None:
        LOG.debug("[harness] genesis_config.yaml not found; using static thresholds")
        return {"gates": dict(_DEFAULT_GATES), "anomaly_detection": {}}

    try:
        import yaml  # noqa: PLC0415
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        harness = cfg.get("harness", {})
        gates = {**_DEFAULT_GATES, **harness.get("gates", {})}
        anomaly = harness.get("anomaly_detection", {})
        return {"gates": gates, "anomaly_detection": anomaly}
    except Exception as exc:
        LOG.debug("[harness] config load failed, using defaults: %s", exc)
        return {"gates": dict(_DEFAULT_GATES), "anomaly_detection": {}}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _safe_close(conn) -> None:
    """Close a connection, ignoring errors. Prevents idle-in-transaction leaks
    (an unclosed SELECT connection stays `idle in transaction` on PostgreSQL,
    holding ACCESS SHARE locks and contributing to a kanban_tasks lock storm)."""
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Anomaly detection — adaptive gate thresholds
# ---------------------------------------------------------------------------

class _AnomalyDetector:
    """Derives adaptive gate thresholds from historical harness_eval distributions.

    Partitions the evaluation history into sliding-window snapshots, computes
    per-metric statistics (mean ± std), and sets adaptive thresholds using
    Z-score bounds: lower-bound for "min" metrics (precision, heal_success_rate),
    upper-bound for "max" metrics (ece, false_heal_rate).

    Falls back to config/static defaults when fewer than ``min_samples`` snapshots
    exist or when queries fail.
    """

    _DEFAULT_CHUNK = 20
    _DEFAULT_WINDOW = 90

    def __init__(self) -> None:
        cfg = _load_harness_config()
        gates = cfg.get("gates", {})
        ad = cfg.get("anomaly_detection", {})

        self._base = {
            "precision_min": float(gates.get("precision_min", _DEFAULT_GATES["precision_min"])),
            "ece_max": float(gates.get("ece_max", _DEFAULT_GATES["ece_max"])),
            "false_heal_max": float(gates.get("false_heal_max", _DEFAULT_GATES["false_heal_max"])),
            "heal_success_min": float(gates.get("heal_success_min", _DEFAULT_GATES["heal_success_min"])),
        }
        self.min_samples = int(ad.get("min_samples", 5))
        self.z_score = float(ad.get("z_score", 2.0))
        self.window_days = int(ad.get("window_days", self._DEFAULT_WINDOW))
        self.chunk_size = int(ad.get("chunk_size", self._DEFAULT_CHUNK))
        self._cache: dict[str, dict] = {}

    def get_thresholds(self, reflex: str) -> dict[str, float]:
        """Return adaptive gate thresholds for *reflex*, falling back to config defaults.

        Keys: precision_min, ece_max, false_heal_max, heal_success_min.
        """
        if reflex in self._cache:
            return self._cache[reflex]

        thresholds = dict(self._base)
        snapshots = self._collect_snapshots(reflex)
        if len(snapshots) >= self.min_samples:
            thresholds.update(self._compute_adaptive(snapshots))
            LOG.debug("[harness] adaptive thresholds for %s from %d snapshots", reflex, len(snapshots))
        else:
            LOG.debug(
                "[harness] static thresholds for %s (%d snapshots < %d required)",
                reflex, len(snapshots), self.min_samples,
            )

        self._cache[reflex] = thresholds
        return thresholds

    def _collect_snapshots(self, reflex: str) -> list[dict]:
        conn = None
        total_days = self.window_days * self.min_samples
        try:
            conn = _conn()
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=total_days)
            ).isoformat(timespec="seconds")
            rows = conn.execute(
                """
                SELECT decision, confidence, actual_outcome
                  FROM harness_eval
                 WHERE reflex = ?
                   AND created_at >= ?
                   AND actual_outcome IS NOT NULL
                ORDER BY created_at
                """,
                (reflex, cutoff),
            ).fetchall()
        except Exception as exc:
            LOG.debug("[harness] anomaly_detector query failed for %s: %s", reflex, exc)
            return []
        finally:
            _safe_close(conn)

        chunk = self.chunk_size
        if len(rows) < chunk:
            return []

        snaps = []
        step = max(1, chunk // 2)
        for i in range(0, len(rows) - chunk + 1, step):
            snap = self._snapshot_metrics(rows[i : i + chunk], reflex)
            if snap:
                snaps.append(snap)
        return snaps

    def _snapshot_metrics(self, rows: list, reflex: str) -> dict | None:
        resolved = [r for r in rows if r["actual_outcome"] == "resolved"]
        false_pos = [r for r in rows if r["actual_outcome"] == "false_positive"]
        self_res = [r for r in rows if r["actual_outcome"] == "self_resolved"]
        failed = [r for r in rows if r["actual_outcome"] == "failed"]

        snap: dict = {}
        denom_p = len(resolved) + len(false_pos)
        if denom_p:
            snap["precision"] = len(resolved) / denom_p

        conf_rows = [r for r in rows if r["confidence"] is not None]
        ece_val = _compute_ece(conf_rows)
        if ece_val is not None:
            snap["ece"] = ece_val

        if reflex == "heal":
            heal_total = len(resolved) + len(self_res)
            if heal_total:
                snap["false_heal"] = len(self_res) / heal_total
            heal_denom = len(resolved) + len(failed)
            if heal_denom:
                snap["heal_success"] = len(resolved) / heal_denom

        return snap or None

    def _compute_adaptive(self, snapshots: list[dict]) -> dict:
        adaptive: dict = {}
        z = self.z_score

        def _mean_std(vals: list[float]) -> tuple[float, float]:
            m = statistics.mean(vals)
            s = statistics.stdev(vals) if len(vals) > 1 else 0.0
            return m, s

        precisions = [s["precision"] for s in snapshots if "precision" in s]
        if len(precisions) >= self.min_samples:
            m, s = _mean_std(precisions)
            adaptive["precision_min"] = max(0.50, m - z * s)

        eces = [s["ece"] for s in snapshots if "ece" in s]
        if len(eces) >= self.min_samples:
            m, s = _mean_std(eces)
            adaptive["ece_max"] = min(0.50, m + z * s)

        false_heals = [s["false_heal"] for s in snapshots if "false_heal" in s]
        if len(false_heals) >= self.min_samples:
            m, s = _mean_std(false_heals)
            adaptive["false_heal_max"] = min(0.60, m + z * s)

        heal_successes = [s["heal_success"] for s in snapshots if "heal_success" in s]
        if len(heal_successes) >= self.min_samples:
            m, s = _mean_std(heal_successes)
            adaptive["heal_success_min"] = max(0.30, m - z * s)

        return adaptive


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
    reflex:     Originating reflex name (oracle_triage | heal | harness ...).
    decision:   What the reflex decided (promote | dismiss | heal | skip | rate_limited).
    confidence: Score used by the reflex (0.0-1.0); None if not applicable.
    metadata:   Extra context stored as JSON.

    Returns the new row ID.
    """
    row_id = str(uuid.uuid4())
    conn = None
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
    finally:
        _safe_close(conn)
    return row_id


def record_outcome(
    task_id: str,
    actual_outcome: str,
) -> None:
    """Update harness_eval rows for task_id with the actual outcome.

    actual_outcome: resolved | false_positive | self_resolved | failed | pending
    """
    conn = None
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
    finally:
        _safe_close(conn)


def compute_metrics(reflex: str, window_days: int = 30) -> dict[str, Any]:
    """Compute evaluation metrics for a reflex over the last window_days.

    Returns
    -------
    dict with keys:
        precision, recall, ece, false_heal_rate, heal_success_rate,
        total_decisions, resolved_count, window_days
    """
    conn = None
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
    finally:
        _safe_close(conn)

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

    Thresholds are derived adaptively from historical metric distributions via
    Z-score anomaly detection (_AnomalyDetector). Static fallback values from
    args/genesis_config.yaml (harness.gates) are used when history is insufficient.

    Returns a list of degradation alerts, each a dict with:
        reflex, metric, value, threshold, adaptive, severity, recommendation
    Empty list = all gates green.
    """
    alerts: list[dict] = []
    detector = _AnomalyDetector()
    min_decisions = int(_DEFAULT_GATES["min_decisions"])

    for reflex in ("oracle_triage", "heal"):
        m = compute_metrics(reflex)
        if "error" in m or m.get("total_decisions", 0) < min_decisions:
            continue

        t = detector.get_thresholds(reflex)

        precision = m.get("precision")
        if precision is not None and precision < t["precision_min"]:
            alerts.append({
                "reflex": reflex,
                "metric": "precision",
                "value": precision,
                "threshold": t["precision_min"],
                "adaptive": t["precision_min"] != _DEFAULT_GATES["precision_min"],
                "severity": "high",
                "recommendation": (
                    f"{reflex} precision {precision:.0%} < {t['precision_min']:.0%}. "
                    "Extract top-10 error cases from harness_eval and run prompt refinement pass. "
                    "Check args/oracle_heuristics.yaml."
                ),
            })

        ece = m.get("ece")
        if ece is not None and ece > t["ece_max"]:
            alerts.append({
                "reflex": reflex,
                "metric": "ece",
                "value": ece,
                "threshold": t["ece_max"],
                "adaptive": t["ece_max"] != _DEFAULT_GATES["ece_max"],
                "severity": "medium",
                "recommendation": (
                    f"{reflex} ECE {ece:.3f} > {t['ece_max']:.3f}. "
                    "Confidence scores are miscalibrated. "
                    "Enable ICDEV_HARNESS_COLEARN=true to run DSPy-style prompt rewrite."
                ),
            })

        false_heal = m.get("false_heal_rate")
        if false_heal is not None and false_heal > t["false_heal_max"]:
            alerts.append({
                "reflex": "heal",
                "metric": "false_heal_rate",
                "value": false_heal,
                "threshold": t["false_heal_max"],
                "adaptive": t["false_heal_max"] != _DEFAULT_GATES["false_heal_max"],
                "severity": "medium",
                "recommendation": (
                    f"False-heal rate {false_heal:.0%} > {t['false_heal_max']:.0%}. "
                    "Heals are triggering on self-resolving anomalies. "
                    "Add a pre-heal wait window or tighten the confidence gate in args/genesis_config.yaml."
                ),
            })

        heal_success = m.get("heal_success_rate")
        if heal_success is not None and heal_success < t["heal_success_min"]:
            alerts.append({
                "reflex": "heal",
                "metric": "heal_success_rate",
                "value": heal_success,
                "threshold": t["heal_success_min"],
                "adaptive": t["heal_success_min"] != _DEFAULT_GATES["heal_success_min"],
                "severity": "high",
                "recommendation": (
                    f"Heal success rate {heal_success:.0%} < {t['heal_success_min']:.0%}. "
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
