# CUI // SP-CTI
"""PVM — Predictive Vulnerability Management: Risk Predictor (pvm-pred-02).

Computes time-series composite risk scores for CVE advisories in the network
canvas DB. Reads from nc_advisories + nc_advisory_assessments, applies a
4-weight formula, and writes predictions to nc_vuln_predictions (APPEND-ONLY).

Algorithm weights:
    cvss_base/10         × 0.35
    exploit_weight       × 0.30  (1.0 if KEV/exploited, 0.5 if CVSS≥7, 0.1 else)
    patch_lag_norm       × 0.20  (days since published / 365, capped at 1.0)
    impacted_trend       × 0.15  (normalised Δimpacted_count over last 3 assessments)

Public API:
    predict_advisory_risk(advisory_id)  → dict
    predict_all_open_advisories()       → list[dict]
    get_risk_trajectory(advisory_id)    → list[dict]
    get_top_risks(limit)                → list[dict]

CLI:
    python tools/network/vuln_predictor.py --predict <id> --json
    python tools/network/vuln_predictor.py --predict-all --json
    python tools/network/vuln_predictor.py --trajectory <id> --json
    python tools/network/vuln_predictor.py --top-risks [--limit N] --json
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone

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
from tools.network.db.init_db import get_connection

logger = get_logger(__name__)

MODEL_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------
_W_CVSS = 0.35
_W_EXPLOIT = 0.30
_W_LAG = 0.20
_W_TREND = 0.15

_CONFIDENCE_MAP = {0: 0.30, 1: 0.40, 2: 0.60}  # 3+ → 0.85


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _days_since(published_date: str | None) -> float:
    """Return fractional days between published_date (YYYY-MM-DD) and now."""
    if not published_date:
        return 0.0
    try:
        pub = datetime.strptime(published_date[:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        return max(0.0, (datetime.now(timezone.utc) - pub).total_seconds() / 86400.0)
    except ValueError:
        return 0.0


def _fetch_assessments(conn, advisory_id: int) -> list[dict]:
    """Return last 3 assessment rows for advisory, ordered by created_at ASC."""
    rows = conn.execute(
        """SELECT id, advisory_id, impacted_count, created_at
           FROM nc_advisory_assessments
           WHERE advisory_id = %s
           ORDER BY created_at ASC
           LIMIT 3""",
        (advisory_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _compute_impacted_trend(assessments: list[dict]) -> float:
    """Normalised Δimpacted_count over last 3 assessments → [0, 1]."""
    if len(assessments) < 2:
        return 0.5  # neutral when no history
    earliest = assessments[0].get("impacted_count") or 0
    latest = assessments[-1].get("impacted_count") or 0
    delta_raw = (latest - earliest) / max(float(earliest), 1.0)
    delta_clamped = max(-1.0, min(1.0, delta_raw))
    return (delta_clamped + 1.0) / 2.0  # maps [-1,1] → [0,1]


def _trend_label(delta: float) -> str:
    if delta > 0.1:
        return "rising"
    if delta < -0.1:
        return "declining"
    return "stable"


def _compute_scores(advisory: dict, assessments: list[dict]) -> dict:
    """Return all computed scoring components."""
    cvss_base = float(advisory.get("cvss_score") or 0.0)
    exploited = str(advisory.get("exploited_in_wild") or "0") == "1"

    # exploit_weight
    if exploited:
        exploit_weight = 1.0
    elif cvss_base >= 7.0:
        exploit_weight = 0.5
    else:
        exploit_weight = 0.1

    # patch_lag_norm
    days = _days_since(advisory.get("published_date"))
    patch_lag_norm = min(days / 365.0, 1.0)

    # impacted_trend
    impacted_trend = _compute_impacted_trend(assessments)

    # composite risk
    composite = (
        (cvss_base / 10.0) * _W_CVSS
        + exploit_weight * _W_EXPLOIT
        + patch_lag_norm * _W_LAG
        + impacted_trend * _W_TREND
    )
    composite = max(0.0, min(1.0, composite))

    # trend label based on raw impacted delta
    raw_delta = impacted_trend * 2.0 - 1.0  # back to [-1, 1]
    trend = _trend_label(raw_delta)

    # acceleration factor for forward forecasts
    accel = max(0.0, raw_delta)  # only positive acceleration contributes
    risk_30d = min(1.0, composite * (1.0 + accel * 0.10))
    risk_90d = min(1.0, composite * (1.0 + accel * 0.25))

    # confidence from assessment history depth
    confidence = _CONFIDENCE_MAP.get(len(assessments), 0.85)

    latest_assessment_id = assessments[-1]["id"] if assessments else None

    return {
        "advisory_id": advisory["id"],
        "assessment_id": latest_assessment_id,
        "risk_score_composite": round(composite, 4),
        "risk_score_30d": round(risk_30d, 4),
        "risk_score_90d": round(risk_90d, 4),
        "trend": trend,
        "confidence": round(confidence, 4),
        "cvss_base": round(cvss_base, 4),
        "exploit_weight": round(exploit_weight, 4),
        "patch_lag_norm": round(patch_lag_norm, 4),
        "impacted_trend": round(impacted_trend, 4),
        "model_version": MODEL_VERSION,
    }


def _write_prediction(conn, scores: dict) -> str:
    """Insert into nc_vuln_predictions and return new row id."""
    now = _now()
    row_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO nc_vuln_predictions
           (id, advisory_id, assessment_id, risk_score_composite, risk_score_30d,
            risk_score_90d, trend, confidence, cvss_base, exploit_weight,
            patch_lag_norm, impacted_trend, model_version, predicted_at, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            row_id,
            str(scores["advisory_id"]),
            scores["assessment_id"],
            scores["risk_score_composite"],
            scores["risk_score_30d"],
            scores["risk_score_90d"],
            scores["trend"],
            scores["confidence"],
            scores["cvss_base"],
            scores["exploit_weight"],
            scores["patch_lag_norm"],
            scores["impacted_trend"],
            scores["model_version"],
            now,
            now,
        ),
    )
    conn.commit()
    return row_id


def _validate_baseline(scores: dict) -> None:
    """Optional: push composite score to threat-analysis baseline comparison."""
    try:
        from tools.threat_analysis.service import validate_indicator_score

        validate_indicator_score(
            "vuln_risk_composite",
            scores["risk_score_composite"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Baseline validation skipped: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def predict_advisory_risk(advisory_id: int) -> dict:
    """Compute and persist a risk prediction for one advisory.

    Returns the inserted nc_vuln_predictions row as a dict.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM nc_advisories WHERE id = %s", (advisory_id,)
        ).fetchone()
        if row is None:
            return {"error": f"Advisory {advisory_id} not found", "advisory_id": advisory_id}

        advisory = dict(row)
        assessments = _fetch_assessments(conn, advisory_id)
        scores = _compute_scores(advisory, assessments)

        try:
            new_id = _write_prediction(conn, scores)
        except Exception as exc:
            if "no such table" in str(exc).lower():
                return {
                    "error": "nc_vuln_predictions table not found — run migration 221",
                    "advisory_id": advisory_id,
                }
            raise

        _validate_baseline(scores)

        pred_row = conn.execute(
            "SELECT * FROM nc_vuln_predictions WHERE id = %s", (new_id,)
        ).fetchone()
        result = dict(pred_row) if pred_row else scores
        result["cve_id"] = advisory.get("cve_id", "")
        return result
    finally:
        conn.close()


def predict_all_open_advisories() -> list[dict]:
    """Run predict_advisory_risk for all open/in_progress advisories."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id FROM nc_advisories WHERE status IN ('open','in_progress')"
        ).fetchall()
        advisory_ids = [r[0] for r in rows]
    finally:
        conn.close()

    results = []
    for adv_id in advisory_ids:
        try:
            result = predict_advisory_risk(adv_id)
            results.append(result)
        except Exception as exc:
            logger.warning("Prediction failed for advisory %s: %s", adv_id, exc)
            results.append({"advisory_id": adv_id, "error": str(exc)})

    return results


def get_risk_trajectory(advisory_id: int, limit: int = 10) -> list[dict]:
    """Return last N prediction rows for an advisory, ordered chronologically."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT p.*, a.cve_id
               FROM nc_vuln_predictions p
               LEFT JOIN nc_advisories a ON a.id = p.advisory_id
               WHERE p.advisory_id = %s
               ORDER BY p.predicted_at ASC
               LIMIT %s""",
            (advisory_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    finally:
        conn.close()


def get_top_risks(limit: int = 20) -> list[dict]:
    """Return latest prediction per advisory, ordered by composite risk DESC."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT p.*, a.cve_id, a.vendor, a.severity
               FROM nc_vuln_predictions p
               JOIN nc_advisories a ON a.id = CAST(p.advisory_id AS INTEGER)
               WHERE p.id IN (
                   SELECT MAX(id) FROM nc_vuln_predictions GROUP BY advisory_id
               )
               ORDER BY p.risk_score_composite DESC
               LIMIT %s""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="PVM Vulnerability Risk Predictor")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--predict", type=int, metavar="ADVISORY_ID",
                       help="Predict risk for one advisory")
    group.add_argument("--predict-all", action="store_true",
                       help="Predict risk for all open advisories")
    group.add_argument("--trajectory", type=int, metavar="ADVISORY_ID",
                       help="Show prediction history for one advisory")
    group.add_argument("--top-risks", action="store_true",
                       help="Show top-N advisories by composite risk")
    parser.add_argument("--limit", type=int, default=20, help="Max rows to return")
    parser.add_argument("--json", action="store_true", dest="json_out",
                        help="Output JSON")
    args = parser.parse_args()

    if args.predict:
        result = predict_advisory_risk(args.predict)
    elif args.predict_all:
        result = predict_all_open_advisories()
    elif args.trajectory:
        result = get_risk_trajectory(args.trajectory, limit=args.limit)
    else:
        result = get_top_risks(limit=args.limit)

    if args.json_out:
        print(json.dumps(result, indent=2, default=str))
    else:
        if isinstance(result, list):
            for r in result:
                print(r)
        else:
            print(result)


if __name__ == "__main__":
    _main()
