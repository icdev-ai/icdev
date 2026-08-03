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
import math
import statistics
from pathlib import Path

from tools.logging.icdev_logger import get_logger
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any

LOG = get_logger(__name__)

# ---------------------------------------------------------------------------
# Canonical outcome vocabulary for harness_eval.actual_outcome
# ---------------------------------------------------------------------------
# This column had three writers speaking three different languages: the kanban
# path wrote resolved/failed, the confidence sampler wrote correct/incorrect,
# and calibration_report scored its sampled rows against {"passed"} (the
# vocabulary of kanban_verifications.result, which is the right answer for the
# *organic* join but not for rows read straight out of harness_eval). The net
# effect was that a human who correctly labelled a drawn sample produced a row
# that scored as a miscalibration. Everything that writes or interprets this
# column must go through the names below.
OUTCOME_RESOLVED = "resolved"
OUTCOME_FALSE_POSITIVE = "false_positive"
OUTCOME_SELF_RESOLVED = "self_resolved"
OUTCOME_FAILED = "failed"
OUTCOME_PENDING = "pending"

VALID_OUTCOMES = frozenset({
    OUTCOME_RESOLVED,
    OUTCOME_FALSE_POSITIVE,
    OUTCOME_SELF_RESOLVED,
    OUTCOME_FAILED,
    OUTCOME_PENDING,
})

#: Outcomes that mean "the reflex's decision was borne out".
SUCCESS_OUTCOMES = frozenset({OUTCOME_RESOLVED})

# Static fallback values — active when anomaly detector lacks sufficient history
# or when args/genesis_config.yaml (harness.gates) is absent.
_DEFAULT_GATES = {
    "precision_min": 0.80,
    "ece_max": 0.15,
    "false_heal_max": 0.20,
    "heal_success_min": 0.60,
    "min_decisions": 10,
    "auroc_min": 0.65,
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
        bounds = ad.get("adaptive_bounds", {})
        self._bounds = {
            "precision_min_floor": float(bounds.get("precision_min_floor", 0.50)),
            "ece_max_ceiling": float(bounds.get("ece_max_ceiling", 0.50)),
            "false_heal_max_ceiling": float(bounds.get("false_heal_max_ceiling", 0.60)),
            "heal_success_min_floor": float(bounds.get("heal_success_min_floor", 0.30)),
        }
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
        # window_days is the per-snapshot window size; total lookback spans min_samples windows
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
                 WHERE reflex = %s
                   AND created_at >= %s
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
        b = self._bounds

        def _mean_std(vals: list[float]) -> tuple[float, float]:
            m = statistics.mean(vals)
            s = statistics.stdev(vals) if len(vals) > 1 else 0.0
            return m, s

        precisions = [s["precision"] for s in snapshots if "precision" in s]
        if len(precisions) >= self.min_samples:
            m, s = _mean_std(precisions)
            adaptive["precision_min"] = max(b["precision_min_floor"], m - z * s)

        eces = [s["ece"] for s in snapshots if "ece" in s]
        if len(eces) >= self.min_samples:
            m, s = _mean_std(eces)
            adaptive["ece_max"] = min(b["ece_max_ceiling"], m + z * s)

        false_heals = [s["false_heal"] for s in snapshots if "false_heal" in s]
        if len(false_heals) >= self.min_samples:
            m, s = _mean_std(false_heals)
            adaptive["false_heal_max"] = min(b["false_heal_max_ceiling"], m + z * s)

        heal_successes = [s["heal_success"] for s in snapshots if "heal_success" in s]
        if len(heal_successes) >= self.min_samples:
            m, s = _mean_std(heal_successes)
            adaptive["heal_success_min"] = max(b["heal_success_min_floor"], m - z * s)

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
    reflex:     Originating reflex name (oracle_triage | heal | harness …).
    decision:   What the reflex decided (promote | dismiss | heal | skip | rate_limited).
    confidence: Score used by the reflex (0.0–1.0); None if not applicable.
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
            VALUES (%s, %s, %s, %s, %s, %s, %s)
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
) -> dict[str, Any]:
    """Update harness_eval rows for task_id with the actual outcome.

    actual_outcome: resolved | false_positive | self_resolved | failed | pending

    The UPDATE only matches rows a reflex previously wrote via
    :func:`record_decision`. When no such row exists — because the decision was
    never recorded, or the task_id used at decision time differs from the one
    used here — the statement matches zero rows. That used to return silently,
    so every caller believed the outcome had been stored while ``harness_eval``
    kept accumulating rows with ``actual_outcome IS NULL`` and
    :func:`compute_metrics` quietly derived precision, recall and ECE from a
    fraction of the table.

    This function therefore reports what actually happened instead of assuming
    success. It deliberately does **not** insert a synthetic decision row on a
    miss: an outcome with no matching decision is not evidence a reflex decided
    anything, and fabricating one would corrupt precision rather than fix it.
    Callers that hit ``no_decision_row`` should fix the pairing upstream.

    Returns
    -------
    dict with keys:
        status:         recorded | no_decision_row | unknown | error
        rows:           number of rows updated (-1 when the driver cannot say)
        task_id:        echoed back for log correlation
        actual_outcome: echoed back
        error:          present only when status == "error"

    Never raises — callers treat this as fire-and-forget telemetry.
    """
    result: dict[str, Any] = {
        "status": "error",
        "rows": 0,
        "task_id": task_id,
        "actual_outcome": actual_outcome,
    }
    if actual_outcome not in VALID_OUTCOMES:
        # Not fatal — the row is still worth storing — but an off-vocabulary
        # value is invisible to compute_metrics and calibration scoring, so it
        # must not pass unremarked.
        LOG.warning(
            "[harness] record_outcome called with off-vocabulary outcome %r for "
            "task_id=%r; metrics recognise only %s",
            actual_outcome,
            task_id,
            sorted(VALID_OUTCOMES),
        )

    conn = None
    try:
        conn = _conn()
        cur = conn.execute(
            """
            UPDATE harness_eval
               SET actual_outcome = %s,
                   resolved_at    = %s
             WHERE task_id = %s
               AND actual_outcome IS NULL
            """,
            (actual_outcome, _utcnow(), task_id),
        )
        conn.commit()

        rows = getattr(cur, "rowcount", -1)
        rows = -1 if rows is None else int(rows)
        result["rows"] = rows

        if rows > 0:
            result["status"] = "recorded"
        elif rows == 0:
            result["status"] = "no_decision_row"
            LOG.warning(
                "[harness] record_outcome matched no decision row for task_id=%r "
                "(outcome=%r) — the outcome was NOT recorded. Either the reflex "
                "never called record_decision for this task, the task_id differs "
                "between the decision and outcome call sites, or an outcome was "
                "already set.",
                task_id,
                actual_outcome,
            )
        else:
            # Driver could not report an affected-row count; do not cry wolf.
            result["status"] = "unknown"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        LOG.warning("[harness] record_outcome failed for %s: %s", task_id, exc)
    finally:
        _safe_close(conn)
    return result


def compute_metrics(reflex: str, window_days: int = 30) -> dict[str, Any]:
    """Compute evaluation metrics for a reflex over the last window_days.

    Returns
    -------
    dict with keys:
        precision, recall, ece, auroc, false_heal_rate, heal_success_rate,
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
             WHERE reflex = %s
               AND created_at >= %s
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
    conf_outcome_rows = [r for r in rows if r["confidence"] is not None and r["actual_outcome"] is not None]
    ece = _compute_ece(conf_outcome_rows)

    # AUROC over the same rows with confidence scores and known outcomes
    auroc = _compute_auroc(conf_outcome_rows)

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
        "auroc": round(auroc, 4) if auroc is not None else None,
        "false_heal_rate": round(false_heal_rate, 4) if false_heal_rate is not None else None,
        "heal_success_rate": round(heal_success_rate, 4) if heal_success_rate is not None else None,
    }


# The two reflexes the gate has always covered. Kept as the default so behaviour
# is unchanged when nothing is configured.
_DEFAULT_GATED_REFLEXES = ("oracle_triage", "heal")


def _gated_reflexes(cfg: dict | None = None) -> tuple[str, ...]:
    """Which reflexes check_gates() evaluates.

    This was a literal `for reflex in ("oracle_triage", "heal")`. Everything else
    that gates on confidence — citation grounding, docmod findings, the AADC
    proceed/escalate gate, the awareness promotion priors — was unmeasurable not
    by decision but because a tuple in one loop never mentioned it. The
    calibration engine existed; its reach was two names long.

    Config: harness.gated_reflexes in args/genesis_config.yaml. A surface only
    yields metrics once it writes to harness_eval, so listing one costs nothing
    until it has data.
    """
    cfg = cfg if cfg is not None else _load_harness_config()
    configured = (cfg or {}).get("gated_reflexes")
    if not configured:
        return _DEFAULT_GATED_REFLEXES
    if isinstance(configured, str):
        configured = [configured]
    names = tuple(str(r).strip() for r in configured if str(r).strip())
    return names or _DEFAULT_GATED_REFLEXES


def _band_of(confidence: float, width: float) -> float:
    """Which band a confidence falls in — via integer math, deliberately.

    The obvious `math.floor(conf / width) * width` is wrong at exactly the values
    that matter: 0.7 / 0.1 is 6.999999999999999 in binary floating point, so
    floor() returns 6 and a 0.7 lands in the 0.6 band. 41 live oracle predictions
    sit at exactly 0.7 — the promotion gate — and every one would be filed a band
    low, leaving the gate's own band looking empty while the band beneath it
    inherited its rows. A calibration report that misfiles the threshold it exists
    to evaluate is worse than none. Scaling to integers makes the boundary exact.
    """
    scale = 1000
    c = int(round(float(confidence) * scale))
    w = int(round(float(width) * scale))
    if w <= 0:
        return 0.0
    return round((c // w) * w / scale, 4)


def _wilson_interval(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion.

    Wilson rather than the normal approximation because n here is small and the
    proportions sit near 0 and 1, where the normal interval famously runs past
    [0,1] and reports nonsense like "accuracy 1.0 ± 0.3". Wilson stays inside the
    unit interval and does not collapse to zero width at p=0 or p=1 — exactly the
    cases this report hits most.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def calibration_by_key(
    rows: list,
    key_of: "Callable[[dict], Any]",
    success_outcomes: "frozenset[str] | set[str] | None" = None,
) -> list[dict[str, Any]]:
    """Observed accuracy vs claimed confidence, grouped by an arbitrary key.

    Shared by calibration_by_band and calibration_by_rule. The grouping key is
    the only thing that differs between them, and the rules they must agree on —
    that a NULL or ``bypassed`` outcome is tracked but never scored, that a group
    under MIN_CALIBRATION_SAMPLES renders as unmeasured — are exactly the ones
    that must not drift apart. Two copies of this loop disagreeing is not a
    hypothetical: _compute_ece already shipped that bug, filtering outcomes in
    one caller and not the other, so the same rows scored differently depending
    on who asked.

    Each group carries its own ``claimed`` values rather than assuming one, so a
    caller can see when a group's claim is not a single number.
    """
    success = frozenset(success_outcomes) if success_outcomes else SUCCESS_OUTCOMES
    groups: dict[Any, dict[str, Any]] = {}

    for r in rows:
        if r["confidence"] is None:
            continue
        key = key_of(r)
        g = groups.setdefault(key, {"labelled": 0, "hits": 0, "non_evidence": 0,
                                    "claimed_values": set()})
        g["claimed_values"].add(round(float(r["confidence"]), 4))

        outcome = r["actual_outcome"]
        if outcome is None or str(outcome).strip().lower() in NON_EVIDENCE_OUTCOMES:
            # Tracked, never scored. "Nobody checked" is not a verdict — counting
            # it as a miss manufactures miscalibration out of missing data.
            g["non_evidence"] += 1
            continue
        g["labelled"] += 1
        if str(outcome).strip().lower() in success:
            g["hits"] += 1

    out: list[dict[str, Any]] = []
    for key in sorted(groups, key=str, reverse=True):
        g = groups[key]
        n = g["labelled"]
        claimed_values = sorted(g["claimed_values"])
        # Mean over the rows' own claims. For a rule this is normally a single
        # constant; more than one means the rule's confidence was edited and old
        # rows kept the old value.
        claimed = round(sum(claimed_values) / len(claimed_values), 4) if claimed_values else None
        observed = (g["hits"] / n) if n else None
        lo, hi = _wilson_interval(g["hits"], n) if n else (None, None)
        out.append({
            "key": key,
            "claimed": claimed,
            "claimed_values": claimed_values,
            "labelled": n,
            "hits": g["hits"],
            "misses": n - g["hits"],
            "non_evidence": g["non_evidence"],
            "measured": n >= MIN_CALIBRATION_SAMPLES,
            "observed_accuracy": round(observed, 4) if observed is not None else None,
            "ci_low": round(lo, 4) if lo is not None else None,
            "ci_high": round(hi, 4) if hi is not None else None,
            # Signed gap: positive means the system claimed more than it delivered.
            "gap": round(claimed - observed, 4) if observed is not None and claimed is not None else None,
        })
    return out


def calibration_by_rule(
    rows: list,
    success_outcomes: "frozenset[str] | set[str] | None" = None,
    rule_field: str = "decision",
) -> list[dict[str, Any]]:
    """Observed accuracy per RULE — the cut that can actually be acted on.

    Confidence in this system is not computed per prediction; it is a per-rule
    constant copied out of awareness_config.yaml, so a band is mostly a single
    rule wearing a number. Grouping by band therefore averages over rules that
    have nothing to do with each other, and on the live corpus it actively
    misleads: the 0.9 band reads "20 labels, observed 0.90" but is 19 rows of
    tool_not_in_manifest plus 1 row of route_not_listed. The band's number is
    tool_not_in_manifest's number with a rounding error attached.

    A rule that is wrong is a fixable thing. A band is an arbitrary set union.
    """
    out = calibration_by_key(rows, lambda r: r.get(rule_field), success_outcomes)
    for g in out:
        g["rule"] = g["key"]
    return out


def calibration_by_band(
    rows: list,
    success_outcomes: "frozenset[str] | set[str] | None" = None,
    bin_width: float = 0.1,
) -> list[dict[str, Any]]:
    """Observed accuracy vs claimed confidence, per band, with intervals.

    This is the report the ECE number cannot give you: a single ECE says "the
    numbers are off by 0.15" without saying *which* band lies.

    Prefer calibration_by_rule where a rule is available. A band is only a real
    unit of analysis when confidence is a per-prediction estimate; where it is a
    per-rule constant (the awareness lens), a band is a set union of unrelated
    rules and its accuracy is an average nobody can act on.
    """
    out = calibration_by_key(
        rows, lambda r: _band_of(float(r["confidence"]), bin_width), success_outcomes
    )
    for g in out:
        g["band"] = g["key"]
        # A band's claim is the band, not the mean of the claims inside it.
        g["claimed"] = g["key"]
        obs = g["observed_accuracy"]
        g["gap"] = round(g["key"] - obs, 4) if obs is not None else None
    out.sort(key=lambda g: g["band"], reverse=True)
    return out


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
    cfg = _load_harness_config()
    min_decisions = int(cfg.get("gates", {}).get("min_decisions", _DEFAULT_GATES["min_decisions"]))

    for reflex in _gated_reflexes(cfg):
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

        auroc_min = float(t.get("auroc_min", _DEFAULT_GATES["auroc_min"]))
        auroc = m.get("auroc")
        if auroc is not None and auroc < auroc_min:
            alerts.append({
                "reflex": reflex,
                "metric": "auroc",
                "value": auroc,
                "threshold": auroc_min,
                "adaptive": auroc_min != _DEFAULT_GATES["auroc_min"],
                "severity": "medium",
                "recommendation": (
                    f"{reflex} AUROC {auroc:.3f} < {auroc_min:.3f}. "
                    "Confidence scores have poor discriminative power. "
                    "Review confidence derivation in oracle heuristics and consider re-calibration."
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

# Outcome vocabularies differ per surface. The harness reflexes call a decision
# correct when it "resolved"; a docmod finding is correct when a human "accepted"
# it; a sampled oracle prediction is correct when its task "passed". Hardcoding
# one vocabulary made every other surface score 0% accurate — not because it was
# wrong, but because its label was spelled differently.
SUCCESS_OUTCOMES: frozenset[str] = frozenset({"resolved"})

# Outcomes that are NOT evidence either way. "bypassed" means verification was
# skipped; "unknown"/"pending" mean nobody has judged yet. Counting these as
# failures is how you manufacture miscalibration out of missing data.
NON_EVIDENCE_OUTCOMES: frozenset[str] = frozenset({"bypassed", "unknown", "pending", ""})

# Below this many labelled rows a calibration number is noise wearing a decimal
# point. _compute_ece has always honoured it; the reports must too.
MIN_CALIBRATION_SAMPLES = 5


def _compute_ece(rows: list, success_outcomes: "frozenset[str] | set[str] | None" = None) -> float | None:
    """Expected Calibration Error over 10 confidence decile bins.

    Args:
        rows: mappings with ``confidence`` and ``actual_outcome``.
        success_outcomes: outcome values meaning "the prediction was right".
            Defaults to SUCCESS_OUTCOMES ("resolved") — the harness vocabulary.

    Returns None when fewer than MIN_CALIBRATION_SAMPLES usable rows remain.

    Two things this refuses to do, both of which it used to do:

    * **Invent a confidence.** It read ``float(r["confidence"]) if not None else
      0.5`` — a row with no confidence became a fabricated 0.5 *inside a
      calibration measurement*. A function whose only job is to check whether the
      numbers are honest must not make its own numbers up. Such rows are dropped.

    * **Score "unknown" as "wrong."** ``outcome = r["actual_outcome"] ==
      "resolved"`` turned a NULL outcome into False — a prediction nobody has
      judged yet counted as a failed one, inflating ECE. compute_metrics()
      happened to pre-filter for this; _snapshot_metrics() did not, so the same
      function reported different calibration depending on which caller reached
      it. Unlabelled and non-evidence rows are dropped here instead, so neither
      caller can get it wrong.
    """
    success = frozenset(success_outcomes) if success_outcomes else SUCCESS_OUTCOMES

    usable = [
        r for r in rows
        if r["confidence"] is not None
        and r["actual_outcome"] is not None
        and str(r["actual_outcome"]).strip().lower() not in NON_EVIDENCE_OUTCOMES
    ]
    if len(usable) < MIN_CALIBRATION_SAMPLES:
        return None

    bins: dict[int, list] = {i: [] for i in range(10)}
    for r in usable:
        conf = float(r["confidence"])
        outcome = str(r["actual_outcome"]).strip().lower() in success
        bin_idx = min(int(conf * 10), 9)
        bins[bin_idx].append((conf, outcome))

    ece = 0.0
    n = len(usable)
    for items in bins.values():
        if not items:
            continue
        avg_conf = sum(c for c, _ in items) / len(items)
        avg_acc = sum(1 for _, o in items if o) / len(items)
        ece += (len(items) / n) * abs(avg_conf - avg_acc)

    return ece


def _compute_auroc(rows: list, success_outcomes: "frozenset[str] | set[str] | None" = None) -> float | None:
    """Area under ROC curve via trapezoidal rule over confidence-sorted thresholds.

    Where ECE asks "are the confidence numbers honest?", AUROC asks the separate
    question "do they *discriminate* at all?" — a reflex can be perfectly
    calibrated and still rank a wrong decision above a right one.

    Args:
        rows: mappings with ``confidence`` and ``actual_outcome``.
        success_outcomes: outcome values meaning "the prediction was right".
            Defaults to SUCCESS_OUTCOMES ("resolved") — the harness vocabulary.

    Returns None when fewer than MIN_CALIBRATION_SAMPLES usable rows remain, or
    when the usable rows are all one class (a ROC curve needs both).

    Row filtering matches _compute_ece deliberately: rows with no confidence, no
    outcome, or a non-evidence outcome are dropped rather than scored as
    negatives. Counting an unjudged prediction as wrong would understate AUROC
    for exactly the reflexes whose outcomes are slowest to land.
    """
    success = frozenset(success_outcomes) if success_outcomes else SUCCESS_OUTCOMES

    usable = [
        r for r in rows
        if r["confidence"] is not None
        and r["actual_outcome"] is not None
        and str(r["actual_outcome"]).strip().lower() not in NON_EVIDENCE_OUTCOMES
    ]
    if len(usable) < MIN_CALIBRATION_SAMPLES:
        return None

    def _is_positive(row) -> bool:
        return str(row["actual_outcome"]).strip().lower() in success

    total_pos = sum(1 for r in usable if _is_positive(r))
    total_neg = len(usable) - total_pos
    if not total_pos or not total_neg:
        return None

    # Stable sort: tied confidences keep their input order, so a run of equal
    # scores traces the diagonal rather than an artificially perfect corner.
    sorted_rows = sorted(usable, key=lambda r: float(r["confidence"]), reverse=True)

    points: list[tuple[float, float]] = [(0.0, 0.0)]
    tp = 0
    fp = 0
    for r in sorted_rows:
        if _is_positive(r):
            tp += 1
        else:
            fp += 1
        points.append((fp / total_neg, tp / total_pos))
    points.append((1.0, 1.0))

    auroc = 0.0
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        auroc += (x1 - x0) * (y0 + y1) / 2.0

    return auroc
