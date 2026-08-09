# CUI // SP-CTI
"""Phase 2 — Drift Detector.

Reads ``awareness_component_health`` snapshots, compares each
component's latest status against a rolling baseline, and writes
``oracle_predictions`` rows under ``lens_name='internal_awareness'``
for any detected regression.

Drift rules shipped in Phase 2:
  * route_regression     (0.85) — http_head flipped pass → fail after >=3 days stable
  * module_import_broken (0.95) — module_import pass → fail
  * coherence_new_fail   (0.80) — coherence_status pass → fail

All rules are deterministic and run on the current snapshot set —
no LLM, no network, no background state.

Baseline: rolling 7-day window of snapshots for each (node_id,
probe_type) pair. A regression requires:
  1. Current status = 'fail' OR 'error'
  2. Baseline has >= 1 'pass' snapshot in the window
  3. The most recent `pass` is at least `stable_days` old
  4. There is NO matching oracle_predictions row already recorded
     for this (node_id, probe_type) within the last 24 hours
     (dedupe — don't spam the board)

CLI:
    python tools/awareness/drift_detector.py --detect --json
    python tools/awareness/drift_detector.py --stats --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402
LOG = get_logger("drift_detector")

try:
    from tools.db.storage import get_connection  # noqa: E402
except ImportError:
    get_connection = None  # type: ignore[assignment]

LENS_NAME = "internal_awareness"
DEFAULT_STABLE_DAYS = 3
DEFAULT_BASELINE_DAYS = 7
DEDUPE_HOURS = 24


# ---------------------------------------------------------------------------
# Drift rule definitions
# ---------------------------------------------------------------------------


_DRIFT_RULES: Dict[str, Dict[str, Any]] = {
    "http_head": {
        "rule_id": "route_regression",
        "confidence": 0.85,
        "severity": "high",
        "prediction_type": "regression",
    },
    "module_import": {
        "rule_id": "module_import_broken",
        "confidence": 0.95,
        "severity": "critical",
        "prediction_type": "regression",
    },
    "coherence_status": {
        "rule_id": "coherence_new_fail",
        "confidence": 0.80,
        "severity": "medium",
        "prediction_type": "regression",
    },
}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Handle both 'Z' suffix and '+00:00' forms
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Snapshot reader
# ---------------------------------------------------------------------------


def _latest_status_per_node_probe(
    conn: Any, window_days: int
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """Return snapshots grouped by (node_id, probe_type), each list
    ordered by probed_at ASC. Only includes snapshots within the
    rolling window.
    """
    cutoff = (_now() - timedelta(days=window_days)).isoformat()
    rows = conn.execute(
        "SELECT node_id, probe_type, status, detail, probed_at "
        "FROM awareness_component_health "
        "WHERE probed_at >= %s "
        "ORDER BY node_id, probe_type, probed_at ASC",
        (cutoff,),
    ).fetchall()
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        key = (d["node_id"], d["probe_type"])
        grouped.setdefault(key, []).append(d)
    return grouped


# ---------------------------------------------------------------------------
# Dedupe: check if we already raised a prediction for this component
# ---------------------------------------------------------------------------


def _recent_prediction_exists(
    conn: Any, node_id: str, probe_type: str, hours: int = DEDUPE_HOURS
) -> bool:
    cutoff = (_now() - timedelta(hours=hours)).isoformat()
    try:
        row = conn.execute(
            "SELECT 1 FROM oracle_predictions "
            "WHERE lens_name = %s AND subject_id = %s "
            "  AND prediction_type = %s "
            "  AND created_at >= %s "
            "LIMIT 1",
            (LENS_NAME, node_id, f"regression::{probe_type}", cutoff),
        ).fetchone()
        return row is not None
    except Exception as exc:
        LOG.debug("dedupe query failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Drift evaluation
# ---------------------------------------------------------------------------


def _evaluate_drift(
    snapshots: List[Dict[str, Any]],
    stable_days: int,
) -> Optional[Dict[str, Any]]:
    """Given a time-sorted list of snapshots for one (node, probe),
    decide whether it represents a regression. Returns a dict with
    evidence on regression, None otherwise.
    """
    if len(snapshots) < 2:
        return None
    latest = snapshots[-1]
    latest_status = latest.get("status", "")
    if latest_status not in ("fail", "error"):
        return None

    # Find the most recent pass before the latest snapshot
    most_recent_pass = None
    for snap in reversed(snapshots[:-1]):
        if snap.get("status") == "pass":
            most_recent_pass = snap
            break
    if most_recent_pass is None:
        return None  # never was passing — this isn't a regression, it's a new fail

    pass_time = _parse_iso(most_recent_pass["probed_at"])
    fail_time = _parse_iso(latest["probed_at"])
    if pass_time is None or fail_time is None:
        return None
    stable_delta = fail_time - pass_time
    if stable_delta < timedelta(days=stable_days):
        return None  # was only briefly passing — too flaky to count as regression

    return {
        "last_good_at": most_recent_pass["probed_at"],
        "first_bad_at": latest["probed_at"],
        "stable_days": round(stable_delta.total_seconds() / 86400.0, 2),
        "baseline_sample_count": len(snapshots),
        "latest_detail": (
            json.loads(latest.get("detail") or "{}")
            if isinstance(latest.get("detail"), str)
            else latest.get("detail") or {}
        ),
    }


# ---------------------------------------------------------------------------
# Oracle prediction writer
# ---------------------------------------------------------------------------


def _prediction_id(node_id: str, probe_type: str, ts: str) -> str:
    h = hashlib.sha256(f"aware::{node_id}::{probe_type}::{ts}".encode("utf-8")).hexdigest()
    return f"op-{h[:20]}"


def _write_prediction(
    conn: Any,
    node_id: str,
    probe_type: str,
    rule: Dict[str, Any],
    evidence: Dict[str, Any],
) -> Optional[str]:
    """Insert an oracle_predictions row. Returns the prediction_id
    on success, None on failure.
    """
    now = _now().isoformat()
    pred_id = _prediction_id(node_id, probe_type, now)

    text = (
        f"{rule['rule_id']}: {probe_type} regression detected on {node_id}. "
        f"Last passing snapshot: {evidence.get('last_good_at', '?')}. "
        f"Stable for {evidence.get('stable_days', 0)} days before "
        f"flipping at {evidence.get('first_bad_at', '?')}. "
        f"Baseline samples: {evidence.get('baseline_sample_count', 0)}."
    )
    try:
        conn.execute(
            "INSERT INTO oracle_predictions "
            "(id, lens_id, lens_name, prediction_text, confidence, "
            " created_at, subject_type, subject_id, prediction_type, "
            " severity, horizon_days, evidence_json, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                pred_id,
                "internal_awareness",
                LENS_NAME,
                text,
                float(rule["confidence"]),
                now,
                "component_health",
                node_id,
                f"regression::{probe_type}",
                rule["severity"],
                0,
                json.dumps(evidence, ensure_ascii=False),
                "CUI // SP-CTI",
            ),
        )
        conn.commit()
        return pred_id
    except Exception as exc:
        LOG.warning("prediction write failed for %s: %s", node_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Main detect loop
# ---------------------------------------------------------------------------


def detect(
    stable_days: int = DEFAULT_STABLE_DAYS,
    baseline_days: int = DEFAULT_BASELINE_DAYS,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run all drift rules and write oracle_predictions rows. Returns
    a summary dict with counts per rule and the list of prediction
    IDs created.
    """
    if get_connection is None:
        return {"error": "get_connection unavailable"}

    conn = get_connection()

    try:
        grouped = _latest_status_per_node_probe(conn, window_days=baseline_days)
    except Exception as exc:
        conn.close()
        return {"error": f"snapshot read failed: {exc}"}

    findings: List[Dict[str, Any]] = []
    deduped = 0

    for (node_id, probe_type), snapshots in grouped.items():
        rule = _DRIFT_RULES.get(probe_type)
        if not rule:
            continue
        evidence = _evaluate_drift(snapshots, stable_days=stable_days)
        if not evidence:
            continue
        # Dedupe against recent predictions
        if _recent_prediction_exists(conn, node_id, probe_type):
            deduped += 1
            continue
        finding = {
            "rule_id": rule["rule_id"],
            "node_id": node_id,
            "probe_type": probe_type,
            "confidence": rule["confidence"],
            "severity": rule["severity"],
            "evidence": evidence,
        }
        if not dry_run:
            pred_id = _write_prediction(conn, node_id, probe_type, rule, evidence)
            finding["prediction_id"] = pred_id
        findings.append(finding)

    try:
        conn.close()
    except Exception:
        pass

    by_rule: Dict[str, int] = {}
    for f in findings:
        by_rule[f["rule_id"]] = by_rule.get(f["rule_id"], 0) + 1

    return {
        "total_findings": len(findings),
        "deduped": deduped,
        "by_rule": by_rule,
        "findings": findings,
        "dry_run": dry_run,
    }


def get_stats() -> Dict[str, Any]:
    """Counts of oracle predictions by lens + severity."""
    if get_connection is None:
        return {"error": "get_connection unavailable"}
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM oracle_predictions WHERE lens_name = %s",
            (LENS_NAME,),
        ).fetchone()
        total = dict(row).get("cnt", 0) if row else 0
        rows = conn.execute(
            "SELECT severity, COUNT(*) AS cnt FROM oracle_predictions "
            "WHERE lens_name = %s GROUP BY severity",
            (LENS_NAME,),
        ).fetchall()
        by_severity = {dict(r)["severity"]: dict(r)["cnt"] for r in rows}
        return {
            "lens": LENS_NAME,
            "total_predictions": total,
            "by_severity": by_severity,
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="drift_detector",
        description="Phase 2 — detect regressions across awareness_component_health",
    )
    parser.add_argument("--detect", action="store_true", help="Run drift detection")
    parser.add_argument("--stats", action="store_true", help="Show prediction stats")
    parser.add_argument(
        "--stable-days",
        type=int,
        default=DEFAULT_STABLE_DAYS,
        help="Required pass-stable duration before a flip counts as regression",
    )
    parser.add_argument(
        "--baseline-days",
        type=int,
        default=DEFAULT_BASELINE_DAYS,
        help="Rolling baseline window",
    )
    parser.add_argument("--dry-run", action="store_true", help="Collect findings without writing")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.stats:
        result: Dict[str, Any] = get_stats()
    elif args.detect:
        result = detect(
            stable_days=args.stable_days,
            baseline_days=args.baseline_days,
            dry_run=args.dry_run,
        )
    else:
        parser.print_help()
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for k, v in result.items():
            if k == "findings":
                print(f"  findings: {len(v)} items")
            else:
                print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
