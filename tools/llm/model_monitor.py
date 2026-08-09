
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
# [TEMPLATE: CUI // SP-CTI]
"""Model Drift & Performance Monitor.

Tracks model quality scores over time, detects statistical drift in
quality, latency, and token usage, and provides health dashboards.
All statistics are computed locally using pure Python (no numpy/scipy).

Tables:
    model_quality_scores  — per-invocation quality measurements
    model_drift_events    — append-only drift detection log
"""

import argparse
import json
import math
import statistics
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional

from tools.db.storage import get_connection

try:
    import yaml
except ImportError:
    yaml = None

logger = get_logger("icdev.llm.model_monitor")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "args" / "llm_config.yaml"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_QUALITY = """
CREATE TABLE IF NOT EXISTS model_quality_scores (
    id              TEXT PRIMARY KEY,
    model_id        TEXT NOT NULL,
    function_name   TEXT NOT NULL,
    quality_score   REAL NOT NULL CHECK(quality_score >= 0.0 AND quality_score <= 1.0),
    response_time_ms INTEGER,
    token_count     INTEGER,
    evaluation_method TEXT NOT NULL CHECK(evaluation_method IN ('automated', 'human', 'judge')),
    evaluator       TEXT NOT NULL DEFAULT 'automated-test',
    metadata_json   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_SCHEMA_DRIFT = """
CREATE TABLE IF NOT EXISTS model_drift_events (
    id              TEXT PRIMARY KEY,
    model_id        TEXT NOT NULL,
    function_name   TEXT NOT NULL,
    drift_type      TEXT NOT NULL CHECK(drift_type IN (
        'quality_degradation', 'latency_increase',
        'token_inflation', 'availability_drop'
    )),
    baseline_value  REAL NOT NULL,
    current_value   REAL NOT NULL,
    deviation_pct   REAL NOT NULL,
    severity        TEXT NOT NULL CHECK(severity IN ('info', 'warning', 'critical')),
    action_taken    TEXT NOT NULL CHECK(action_taken IN (
        'none', 'alert', 'retrain_triggered', 'model_swapped'
    )) DEFAULT 'none',
    window_start    TEXT,
    window_end      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_IDX_QUALITY = [
    "CREATE INDEX IF NOT EXISTS idx_mqs_model ON model_quality_scores(model_id);",
    "CREATE INDEX IF NOT EXISTS idx_mqs_func ON model_quality_scores(function_name);",
    "CREATE INDEX IF NOT EXISTS idx_mqs_created ON model_quality_scores(created_at);",
]

_IDX_DRIFT = [
    "CREATE INDEX IF NOT EXISTS idx_mde_model ON model_drift_events(model_id);",
    "CREATE INDEX IF NOT EXISTS idx_mde_severity ON model_drift_events(severity);",
    "CREATE INDEX IF NOT EXISTS idx_mde_created ON model_drift_events(created_at);",
]


def _ensure_tables(conn) -> None:
    """Create tables and indexes if they do not exist."""
    conn.execute(_SCHEMA_QUALITY)
    conn.execute(_SCHEMA_DRIFT)
    for idx in _IDX_QUALITY + _IDX_DRIFT:
        conn.execute(idx)
    conn.commit()


# ---------------------------------------------------------------------------
# Statistics helpers (pure Python, no scipy/numpy)
# ---------------------------------------------------------------------------


def _percentile(data: List[float], pct: float) -> float:
    """Return the pct-th percentile of *sorted* data (0-100 scale)."""
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * (pct / 100.0)
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return s[f]
    return s[f] * (c - k) + s[c] * (k - f)


def _welch_t_test(sample_a: List[float], sample_b: List[float]) -> float:
    """Return two-tailed p-value approximation using Welch's t-test.

    Uses the Abramowitz & Stegun approximation for the t-distribution CDF
    so we avoid requiring scipy.
    """
    n_a, n_b = len(sample_a), len(sample_b)
    if n_a < 2 or n_b < 2:
        return 1.0  # insufficient data

    mean_a = statistics.mean(sample_a)
    mean_b = statistics.mean(sample_b)
    var_a = statistics.variance(sample_a)
    var_b = statistics.variance(sample_b)

    se = math.sqrt(var_a / n_a + var_b / n_b)
    if se == 0:
        return 1.0

    t_stat = (mean_a - mean_b) / se

    # Welch-Satterthwaite degrees of freedom
    num = (var_a / n_a + var_b / n_b) ** 2
    denom = ((var_a / n_a) ** 2 / (n_a - 1)) + ((var_b / n_b) ** 2 / (n_b - 1))
    if denom == 0:
        return 1.0
    df = num / denom

    # Approximate two-tailed p-value via regularized incomplete beta function
    return _t_distribution_two_tailed_p(abs(t_stat), df)


def _t_distribution_two_tailed_p(t: float, df: float) -> float:
    """Approximate two-tailed p-value for Student's t-distribution.

    Uses the relationship: p = I_{df/(df+t^2)}(df/2, 1/2) where I is
    the regularized incomplete beta function, approximated via continued
    fraction expansion.
    """
    if df <= 0:
        return 1.0
    x = df / (df + t * t)
    a = df / 2.0
    b = 0.5
    p = _regularized_incomplete_beta(x, a, b)
    return max(0.0, min(1.0, p))


def _regularized_incomplete_beta(x: float, a: float, b: float, max_iter: int = 200, tol: float = 1e-12) -> float:
    """Regularized incomplete beta function I_x(a, b) via Lentz continued fraction."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    # Use symmetry relation for better convergence
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _regularized_incomplete_beta(1.0 - x, b, a, max_iter, tol)

    # Prefactor: x^a * (1-x)^b / (a * Beta(a,b))
    ln_prefactor = a * math.log(x) + b * math.log(1.0 - x) - math.log(a) - _log_beta(a, b)
    prefactor = math.exp(ln_prefactor)

    # Lentz's algorithm for continued fraction
    f = 1.0 + _cf_coeff(1, x, a, b)
    if abs(f) < 1e-30:
        f = 1e-30
    c = f
    d = 1.0
    result = 1.0 / f

    for m in range(2, max_iter + 1):
        coeff = _cf_coeff(m, x, a, b)
        d = 1.0 + coeff * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + coeff / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = c * d
        result *= delta
        if abs(delta - 1.0) < tol:
            break

    return prefactor * result


def _cf_coeff(m: int, x: float, a: float, b: float) -> float:
    """Continued fraction coefficients for incomplete beta."""
    if m % 2 == 0:
        k = m // 2
        return (k * (b - k) * x) / ((a + 2 * k - 1) * (a + 2 * k))
    else:
        k = (m - 1) // 2
        return -((a + k) * (a + b + k) * x) / ((a + 2 * k) * (a + 2 * k + 1))


def _log_beta(a: float, b: float) -> float:
    """Log of the beta function using lgamma."""
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def record_quality_score(
    model_id: str,
    function_name: str,
    score: float,
    response_time_ms: Optional[int] = None,
    token_count: Optional[int] = None,
    method: str = "automated",
    evaluator: str = "automated-test",
    metadata: Optional[dict] = None,
) -> dict:
    """Record a single quality evaluation for a model+function pair."""
    if method not in ("automated", "human", "judge"):
        raise ValueError(f"Invalid evaluation_method: {method}")
    if not (0.0 <= score <= 1.0):
        raise ValueError(f"quality_score must be 0.0-1.0, got {score}")

    entry_id = f"meval-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

    conn = get_connection()
    _ensure_tables(conn)
    conn.execute(
        """INSERT INTO model_quality_scores
           (id, model_id, function_name, quality_score, response_time_ms,
            token_count, evaluation_method, evaluator, metadata_json, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (entry_id, model_id, function_name, score, response_time_ms, token_count, method, evaluator, meta_json, now),
    )
    conn.commit()

    return {
        "status": "recorded",
        "id": entry_id,
        "model_id": model_id,
        "function_name": function_name,
        "quality_score": score,
        "created_at": now,
    }


def get_baseline(model_id: str, function_name: str, baseline_days: int = 30) -> dict:
    """Return baseline statistics for a model+function pair.

    Baseline = the first *baseline_days* days of recorded data, or all data
    if less than baseline_days of data exists and there are fewer than 10
    recent data points.
    """
    conn = get_connection()
    _ensure_tables(conn)

    # Find earliest record date
    row = conn.execute(
        """SELECT MIN(created_at) as earliest FROM model_quality_scores
           WHERE model_id = %s AND function_name = %s""",
        (model_id, function_name),
    ).fetchone()

    earliest = row["earliest"] if row and row["earliest"] else None
    if not earliest:
        return {"status": "no_data", "model_id": model_id, "function_name": function_name}

    cutoff = (datetime.fromisoformat(earliest.replace("Z", "+00:00")) + timedelta(days=baseline_days)).isoformat()

    rows = conn.execute(
        """SELECT quality_score, response_time_ms, token_count
           FROM model_quality_scores
           WHERE model_id = %s AND function_name = %s AND created_at <= %s
           ORDER BY created_at""",
        (model_id, function_name, cutoff),
    ).fetchall()

    if not rows:
        return {"status": "no_data", "model_id": model_id, "function_name": function_name}

    scores = [r["quality_score"] for r in rows]
    latencies = [r["response_time_ms"] for r in rows if r["response_time_ms"] is not None]
    tokens = [r["token_count"] for r in rows if r["token_count"] is not None]

    result = {
        "status": "ok",
        "model_id": model_id,
        "function_name": function_name,
        "sample_count": len(scores),
        "baseline_cutoff": cutoff,
        "quality": {
            "mean": round(statistics.mean(scores), 4),
            "stdev": round(statistics.stdev(scores), 4) if len(scores) >= 2 else 0.0,
            "p50": round(_percentile(scores, 50), 4),
        },
    }
    if latencies:
        result["latency_ms"] = {
            "mean": round(statistics.mean(latencies), 1),
            "p95": round(_percentile(latencies, 95), 1),
            "p99": round(_percentile(latencies, 99), 1),
        }
    if tokens:
        result["tokens"] = {
            "mean": round(statistics.mean(tokens), 1),
            "stdev": round(statistics.stdev(tokens), 1) if len(tokens) >= 2 else 0.0,
        }
    return result


def reset_baseline(model_id: str, function_name: str) -> dict:
    """Mark the current window as the new baseline by inserting a sentinel.

    In practice this deletes older quality scores so the remaining data
    becomes the new baseline.  Returns the count of archived records.
    """
    conn = get_connection()
    _ensure_tables(conn)

    # Keep only the most recent 30 days of data — older data is 'archived'
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    cur = conn.execute(
        """DELETE FROM model_quality_scores
           WHERE model_id = %s AND function_name = %s AND created_at < %s""",
        (model_id, function_name, cutoff),
    )
    conn.commit()
    archived = cur.rowcount if hasattr(cur, "rowcount") else 0

    return {
        "status": "baseline_reset",
        "model_id": model_id,
        "function_name": function_name,
        "archived_records": archived,
        "new_baseline_start": cutoff,
    }


def detect_drift(
    model_id: Optional[str] = None, function_name: Optional[str] = None, window_days: int = 7, baseline_days: int = 30
) -> List[dict]:
    """Detect statistical drift for model+function pairs.

    Compares a recent *window_days* window against the baseline period.
    Uses Welch's t-test (p < 0.05) for significance.

    Checks:
        - Quality score degradation  (mean drop > 10%)
        - Latency increase           (p95 increase > 25%)
        - Token inflation             (mean increase > 20%)
    """
    conn = get_connection()
    _ensure_tables(conn)

    # Discover model+function pairs to evaluate
    where_parts: list = []
    params: list = []
    if model_id:
        where_parts.append("model_id = ?")
        params.append(model_id)
    if function_name:
        where_parts.append("function_name = ?")
        params.append(function_name)
    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    pairs = conn.execute(
        f"SELECT DISTINCT model_id, function_name FROM model_quality_scores {where_clause}",  # nosec B608
        params,
    ).fetchall()

    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(days=window_days)).isoformat()
    window_end = now.isoformat()

    drift_events: List[dict] = []

    for pair in pairs:
        mid = pair["model_id"]
        fname = pair["function_name"]

        # Baseline data
        baseline = get_baseline(mid, fname, baseline_days)
        if baseline.get("status") != "ok" or baseline["sample_count"] < 5:
            continue

        # Recent window data
        recent_rows = conn.execute(
            """SELECT quality_score, response_time_ms, token_count
               FROM model_quality_scores
               WHERE model_id = %s AND function_name = %s AND created_at >= %s
               ORDER BY created_at""",
            (mid, fname, window_start),
        ).fetchall()

        if len(recent_rows) < 3:
            continue

        # Baseline raw data for t-test
        baseline_cutoff = baseline["baseline_cutoff"]
        baseline_rows = conn.execute(
            """SELECT quality_score, response_time_ms, token_count
               FROM model_quality_scores
               WHERE model_id = %s AND function_name = %s AND created_at <= %s""",
            (mid, fname, baseline_cutoff),
        ).fetchall()

        # --- Quality degradation ---
        base_scores = [r["quality_score"] for r in baseline_rows]
        recent_scores = [r["quality_score"] for r in recent_rows]
        if base_scores and recent_scores:
            base_mean = statistics.mean(base_scores)
            recent_mean = statistics.mean(recent_scores)
            if base_mean > 0:
                drop_pct = ((base_mean - recent_mean) / base_mean) * 100
                if drop_pct > 10:
                    p_val = _welch_t_test(base_scores, recent_scores)
                    if p_val < 0.05:
                        severity = "critical" if drop_pct > 25 else "warning"
                        evt = _record_drift_event(
                            conn,
                            mid,
                            fname,
                            "quality_degradation",
                            base_mean,
                            recent_mean,
                            drop_pct,
                            severity,
                            window_start,
                            window_end,
                        )
                        drift_events.append(evt)

        # --- Latency increase (p95) ---
        base_lat = [r["response_time_ms"] for r in baseline_rows if r["response_time_ms"] is not None]
        recent_lat = [r["response_time_ms"] for r in recent_rows if r["response_time_ms"] is not None]
        if len(base_lat) >= 3 and len(recent_lat) >= 3:
            base_p95 = _percentile(base_lat, 95)
            recent_p95 = _percentile(recent_lat, 95)
            if base_p95 > 0:
                increase_pct = ((recent_p95 - base_p95) / base_p95) * 100
                if increase_pct > 25:
                    p_val = _welch_t_test(base_lat, recent_lat)
                    if p_val < 0.05:
                        severity = "critical" if increase_pct > 50 else "warning"
                        evt = _record_drift_event(
                            conn,
                            mid,
                            fname,
                            "latency_increase",
                            base_p95,
                            recent_p95,
                            increase_pct,
                            severity,
                            window_start,
                            window_end,
                        )
                        drift_events.append(evt)

        # --- Token inflation ---
        base_tok = [r["token_count"] for r in baseline_rows if r["token_count"] is not None]
        recent_tok = [r["token_count"] for r in recent_rows if r["token_count"] is not None]
        if len(base_tok) >= 3 and len(recent_tok) >= 3:
            base_tok_mean = statistics.mean(base_tok)
            recent_tok_mean = statistics.mean(recent_tok)
            if base_tok_mean > 0:
                tok_pct = ((recent_tok_mean - base_tok_mean) / base_tok_mean) * 100
                if tok_pct > 20:
                    p_val = _welch_t_test(base_tok, recent_tok)
                    if p_val < 0.05:
                        severity = "critical" if tok_pct > 40 else "warning"
                        evt = _record_drift_event(
                            conn,
                            mid,
                            fname,
                            "token_inflation",
                            base_tok_mean,
                            recent_tok_mean,
                            tok_pct,
                            severity,
                            window_start,
                            window_end,
                        )
                        drift_events.append(evt)

    return drift_events


def _record_drift_event(
    conn,
    model_id: str,
    function_name: str,
    drift_type: str,
    baseline_value: float,
    current_value: float,
    deviation_pct: float,
    severity: str,
    window_start: str,
    window_end: str,
    action: str = "alert",
) -> dict:
    """Insert an append-only drift event and return it as a dict."""
    event_id = f"mdrift-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        """INSERT INTO model_drift_events
           (id, model_id, function_name, drift_type,
            baseline_value, current_value, deviation_pct,
            severity, action_taken, window_start, window_end, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            event_id,
            model_id,
            function_name,
            drift_type,
            round(baseline_value, 4),
            round(current_value, 4),
            round(deviation_pct, 2),
            severity,
            action,
            window_start,
            window_end,
            now,
        ),
    )
    conn.commit()

    return {
        "id": event_id,
        "model_id": model_id,
        "function_name": function_name,
        "drift_type": drift_type,
        "baseline_value": round(baseline_value, 4),
        "current_value": round(current_value, 4),
        "deviation_pct": round(deviation_pct, 2),
        "severity": severity,
        "action_taken": action,
        "window_start": window_start,
        "window_end": window_end,
        "created_at": now,
    }


def get_model_health(model_id: Optional[str] = None) -> dict:
    """Return current health status per model (and per function).

    Aggregates the most recent 50 quality scores and any drift events
    from the last 24 hours.
    """
    conn = get_connection()
    _ensure_tables(conn)

    where_parts: list = []
    params: list = []
    if model_id:
        where_parts.append("model_id = ?")
        params.append(model_id)
    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    pairs = conn.execute(
        f"SELECT DISTINCT model_id, function_name FROM model_quality_scores {where_clause}",  # nosec B608
        params,
    ).fetchall()

    health: dict = {"status": "ok", "models": {}}
    now = datetime.now(timezone.utc)
    recent_24h = (now - timedelta(hours=24)).isoformat()

    for pair in pairs:
        mid = pair["model_id"]
        fname = pair["function_name"]

        rows = conn.execute(
            """SELECT quality_score, response_time_ms, token_count, created_at
               FROM model_quality_scores
               WHERE model_id = %s AND function_name = %s
               ORDER BY created_at DESC LIMIT 50""",
            (mid, fname),
        ).fetchall()

        scores = [r["quality_score"] for r in rows]
        latencies = [r["response_time_ms"] for r in rows if r["response_time_ms"] is not None]
        tokens = [r["token_count"] for r in rows if r["token_count"] is not None]

        func_health: dict = {
            "sample_count": len(scores),
            "quality_mean": round(statistics.mean(scores), 4) if scores else None,
            "quality_p50": round(_percentile(scores, 50), 4) if scores else None,
        }
        if latencies:
            func_health["latency_p50_ms"] = round(_percentile(latencies, 50), 1)
            func_health["latency_p95_ms"] = round(_percentile(latencies, 95), 1)
        if tokens:
            func_health["tokens_mean"] = round(statistics.mean(tokens), 1)

        # Recent drift events
        drift_rows = conn.execute(
            """SELECT drift_type, severity, deviation_pct, created_at
               FROM model_drift_events
               WHERE model_id = %s AND function_name = %s AND created_at >= %s
               ORDER BY created_at DESC""",
            (mid, fname, recent_24h),
        ).fetchall()

        if drift_rows:
            func_health["active_drift"] = [
                {
                    "drift_type": d["drift_type"],
                    "severity": d["severity"],
                    "deviation_pct": d["deviation_pct"],
                }
                for d in drift_rows
            ]
            worst = max(d["severity"] for d in drift_rows)
            func_health["health_status"] = "degraded" if worst == "critical" else "warning"
        else:
            func_health["health_status"] = "healthy"

        health["models"].setdefault(mid, {})[fname] = func_health

    # Overall status
    all_statuses = []
    for mid_data in health["models"].values():
        for func_data in mid_data.values():
            all_statuses.append(func_data.get("health_status", "healthy"))
    if "degraded" in all_statuses:
        health["status"] = "degraded"
    elif "warning" in all_statuses:
        health["status"] = "warning"

    return health


def trigger_retrain(model_id: str, function_name: str, reason: str) -> dict:
    """Log a retrain request as a drift event and return it.

    Can be extended to invoke tools/finetune/retrain_trigger.py.
    """
    conn = get_connection()
    _ensure_tables(conn)
    now = datetime.now(timezone.utc).isoformat()

    evt = _record_drift_event(
        conn,
        model_id,
        function_name,
        drift_type="quality_degradation",
        baseline_value=0.0,
        current_value=0.0,
        deviation_pct=0.0,
        severity="critical",
        window_start=now,
        window_end=now,
        action="retrain_triggered",
    )
    evt["reason"] = reason
    logger.info("Retrain triggered for %s/%s: %s", model_id, function_name, reason)
    return evt


def get_drift_history(limit: int = 50, model_id: Optional[str] = None) -> List[dict]:
    """Return recent drift events."""
    conn = get_connection()
    _ensure_tables(conn)

    where_parts: list = []
    params: list = []
    if model_id:
        where_parts.append("model_id = ?")
        params.append(model_id)
    where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    rows = conn.execute(
        f"""SELECT id, model_id, function_name, drift_type,
                   baseline_value, current_value, deviation_pct,
                   severity, action_taken, window_start, window_end, created_at
            FROM model_drift_events {where_clause}
            ORDER BY created_at DESC LIMIT %s""",  # nosec B608
        params + [limit],
    ).fetchall()

    return [dict(r) for r in rows]


def run_gate() -> dict:
    """Gate check — exit 1 if any critical drift detected in last 24h."""
    conn = get_connection()
    _ensure_tables(conn)

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = conn.execute(
        """SELECT COUNT(*) as cnt FROM model_drift_events
           WHERE severity = 'critical' AND created_at >= %s""",
        (cutoff,),
    ).fetchone()

    count = rows["cnt"] if rows else 0
    passed = count == 0
    return {
        "gate": "model_drift",
        "passed": passed,
        "critical_drift_events_24h": count,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Model Drift & Performance Monitor for ICDEV™",
    )
    parser.add_argument("--record", action="store_true", help="Record a quality score")
    parser.add_argument("--detect-drift", action="store_true", help="Run drift detection")
    parser.add_argument("--health", action="store_true", help="Show model health status")
    parser.add_argument("--baseline", action="store_true", help="Show baseline statistics")
    parser.add_argument("--reset-baseline", action="store_true", help="Reset baseline for a model+function")
    parser.add_argument("--drift-history", action="store_true", help="Show recent drift events")
    parser.add_argument("--retrain", action="store_true", help="Trigger retrain request")
    parser.add_argument("--gate", action="store_true", help="Gate check — exit 1 if critical drift")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Record args
    parser.add_argument("--model", type=str, help="Model ID")
    parser.add_argument("--function", type=str, help="Function name")
    parser.add_argument("--score", type=float, help="Quality score (0.0-1.0)")
    parser.add_argument("--response-time", type=int, help="Response time in ms")
    parser.add_argument("--tokens", type=int, help="Token count")
    parser.add_argument(
        "--method", type=str, default="automated", choices=["automated", "human", "judge"], help="Evaluation method"
    )
    parser.add_argument("--evaluator", type=str, default="automated-test", help="Evaluator identifier")

    # Drift detection args
    parser.add_argument("--window-days", type=int, default=7, help="Recent window size in days (default: 7)")

    # History args
    parser.add_argument("--limit", type=int, default=50, help="Limit for drift history (default: 50)")

    # Retrain args
    parser.add_argument("--reason", type=str, help="Reason for retrain trigger")

    args = parser.parse_args()

    result = None

    if args.record:
        if not args.model or not args.function or args.score is None:
            parser.error("--record requires --model, --function, and --score")
        result = record_quality_score(
            model_id=args.model,
            function_name=args.function,
            score=args.score,
            response_time_ms=args.response_time,
            token_count=args.tokens,
            method=args.method,
            evaluator=args.evaluator,
        )

    elif args.detect_drift:
        events = detect_drift(
            model_id=args.model,
            function_name=args.function,
            window_days=args.window_days,
        )
        result = {"drift_events": events, "count": len(events)}

    elif args.health:
        result = get_model_health(model_id=args.model)

    elif args.baseline:
        if not args.model or not args.function:
            parser.error("--baseline requires --model and --function")
        result = get_baseline(args.model, args.function)

    elif args.reset_baseline:
        if not args.model or not args.function:
            parser.error("--reset-baseline requires --model and --function")
        result = reset_baseline(args.model, args.function)

    elif args.drift_history:
        events = get_drift_history(limit=args.limit, model_id=args.model)
        result = {"drift_events": events, "count": len(events)}

    elif args.retrain:
        if not args.model or not args.function or not args.reason:
            parser.error("--retrain requires --model, --function, and --reason")
        result = trigger_retrain(args.model, args.function, args.reason)

    elif args.gate:
        result = run_gate()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = "PASSED" if result["passed"] else "FAILED"
            print(f"Gate model_drift: {status} (critical events 24h: {result['critical_drift_events_24h']})")
        sys.exit(0 if result["passed"] else 1)

    else:
        parser.print_help()
        sys.exit(0)

    if result is not None:
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            for key, val in (result if isinstance(result, dict) else {"result": result}).items():
                print(f"{key}: {val}")


if __name__ == "__main__":
    main()
