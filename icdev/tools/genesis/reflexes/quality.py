# CUI // SP-CTI
"""Genesis Quality Reflex — Self-Learning QA/QC Improvement.

Autonomous reflex that:
1. Runs all QDC gates periodically
2. Tracks quality trends over time
3. Detects regression patterns
4. Proposes improvements via GKP (Genesis Knowledge Packets)
5. Auto-fixes safe issues (lint, deprecation)
6. Updates tool configurations based on findings

Scanner-tier only (qwen3.5 / zero Claude tokens).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from tools.db.storage import get_connection

logger = get_logger(__name__)

ICDEV_ROOT = Path(__file__).resolve().parents[3]
GENESIS_DB = ICDEV_ROOT / "data" / "genesis_quality.db"


def _load_reflex_config() -> Dict[str, Any]:
    """Load quality reflex config from genesis_config.yaml."""
    config_path = ICDEV_ROOT / "args" / "genesis_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        return config.get("reflexes", {}).get("quality", {})
    except Exception:
        return {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    """Get or create the genesis quality tracking DB."""
    GENESIS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path=str(GENESIS_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS quality_snapshots (
            id              TEXT PRIMARY KEY,
            snapshot_at     TEXT NOT NULL,
            gate_results    TEXT DEFAULT '[]',
            uqs_score       REAL DEFAULT 0.0,
            grade           TEXT DEFAULT 'F',
            tools_available INTEGER DEFAULT 0,
            total_findings  INTEGER DEFAULT 0,
            auto_fixed      INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS quality_trends (
            id              TEXT PRIMARY KEY,
            detected_at     TEXT NOT NULL,
            trend_type      TEXT NOT NULL,
            dimension       TEXT,
            direction       TEXT,
            severity        TEXT DEFAULT 'info',
            detail          TEXT DEFAULT '',
            resolved        INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS quality_gkps (
            id              TEXT PRIMARY KEY,
            created_at      TEXT NOT NULL,
            gkp_type        TEXT NOT NULL,
            title           TEXT NOT NULL,
            body            TEXT DEFAULT '',
            source_gate     TEXT,
            status          TEXT DEFAULT 'pending',
            promoted        INTEGER DEFAULT 0
        );
    """)
    return conn


def _compute_adaptive_thresholds(conn) -> dict:
    """Compute regression thresholds from historical quality_snapshots using Z-score anomaly detection.

    Returns adaptive values when anomaly_detection is enabled and enough snapshots
    exist (>= min_samples + 1 needed to produce deltas).  Falls back to the static
    config values otherwise.
    """
    cfg = _load_reflex_config()
    ad_cfg = cfg.get("anomaly_detection", {})
    bounds = ad_cfg.get("adaptive_bounds", {})

    static = {
        "uqs_regression_threshold": float(cfg.get("uqs_regression_threshold", -5.0)),
        "uqs_critical_threshold": float(cfg.get("uqs_critical_threshold", -15.0)),
        "findings_increase_threshold": int(cfg.get("findings_increase_threshold", 5)),
        "adaptive": False,
    }

    if not ad_cfg.get("enabled", False):
        return static

    min_samples = int(ad_cfg.get("min_samples", 5))
    sigma = float(ad_cfg.get("sigma_multiplier", 1.0))
    crit_sigma_mult = float(ad_cfg.get("critical_sigma_multiplier", 2.0))
    crit_floor_mult = float(ad_cfg.get("critical_floor_multiplier", 2.0))
    fetch_buffer = int(ad_cfg.get("fetch_buffer", 10))

    rows = conn.execute(
        "SELECT uqs_score, total_findings FROM quality_snapshots ORDER BY snapshot_at DESC LIMIT %s",
        (min_samples + fetch_buffer,),
    ).fetchall()

    if len(rows) < min_samples + 1:
        return static

    uqs_vals = [r["uqs_score"] for r in rows]
    findings_vals = [r["total_findings"] for r in rows]
    uqs_deltas = [uqs_vals[i] - uqs_vals[i + 1] for i in range(len(uqs_vals) - 1)]
    findings_deltas = [findings_vals[i] - findings_vals[i + 1] for i in range(len(findings_vals) - 1)]

    def _mean_std(vals: list) -> tuple:
        n = len(vals)
        mean = sum(vals) / n
        std = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
        return mean, std

    uqs_mean, uqs_std = _mean_std(uqs_deltas)
    f_mean, f_std = _mean_std(findings_deltas)

    uqs_reg = uqs_mean - sigma * uqs_std
    uqs_crit = uqs_mean - crit_sigma_mult * sigma * uqs_std
    f_inc = round(f_mean + sigma * f_std)

    # Apply adaptive bounds
    uqs_floor = float(bounds.get("uqs_regression_floor", -20.0))
    f_floor = int(bounds.get("findings_increase_floor", 1))
    uqs_reg = max(uqs_reg, uqs_floor)
    uqs_crit = max(uqs_crit, uqs_floor * crit_floor_mult)
    f_inc = max(f_floor, f_inc)

    return {
        "uqs_regression_threshold": uqs_reg,
        "uqs_critical_threshold": uqs_crit,
        "findings_increase_threshold": f_inc,
        "adaptive": True,
        "_n": len(uqs_deltas),
        "_uqs_delta_mean": round(uqs_mean, 3),
        "_uqs_delta_std": round(uqs_std, 3),
    }


def _compute_adaptive_gate_score_threshold(conn) -> float:
    """Compute an adaptive gate score threshold from historical gate scores.

    Parses the gate_results JSON blobs in quality_snapshots to build a
    population of historical scores.  Returns mean - sigma*std clamped to
    gate_score_floor when enough data exists; falls back to the static
    gate_score_threshold config value otherwise.
    """
    cfg = _load_reflex_config()
    ad_cfg = cfg.get("anomaly_detection", {})
    bounds = ad_cfg.get("adaptive_bounds", {})

    static_threshold = float(cfg.get("gate_score_threshold", 80.0))

    if not ad_cfg.get("enabled", False):
        return static_threshold

    min_samples = int(ad_cfg.get("min_samples", 5))
    sigma = float(ad_cfg.get("sigma_multiplier", 1.0))
    fetch_buffer = int(ad_cfg.get("fetch_buffer", 10))
    gate_floor = float(bounds.get("gate_score_floor", 50.0))

    rows = conn.execute(
        "SELECT gate_results FROM quality_snapshots ORDER BY snapshot_at DESC LIMIT %s",
        (min_samples + fetch_buffer,),
    ).fetchall()

    if len(rows) < min_samples:
        return static_threshold

    all_scores: list = []
    for row in rows:
        try:
            for g in json.loads(row["gate_results"] or "[]"):
                score = g.get("score")
                if score is not None:
                    all_scores.append(float(score))
        except Exception:
            continue

    if len(all_scores) < min_samples:
        return static_threshold

    n = len(all_scores)
    mean = sum(all_scores) / n
    std = math.sqrt(sum((s - mean) ** 2 for s in all_scores) / n)
    return max(gate_floor, mean - sigma * std)


def run_quality_scan() -> dict:
    """Execute all QDC gates and store snapshot.

    This is the main entry point called by the Genesis daemon.
    """
    import uuid

    try:
        from tools.qdc_canvas.gate_executor import check_tool_availability, execute_all_gates
    except ImportError:
        return {"status": "error", "message": "gate_executor not available"}

    # Check tool availability
    tools = check_tool_availability()
    available = tools["available_count"]

    # Execute all gates
    results = execute_all_gates()

    # Compute UQS
    try:
        from tools.qdc_canvas.qdc_engine import compute_uqs

        uqs_result = compute_uqs(results)
        uqs_score = uqs_result.get("uqs_score", 0.0)
        grade = uqs_result.get("grade", "F")
    except Exception:
        uqs_score = 0.0
        grade = "F"

    total_findings = sum(r.get("findings_count", 0) for r in results)

    # Store snapshot
    conn = _get_db()
    try:
        snapshot_id = f"qs-{uuid.uuid4().hex[:10]}"
        conn.execute(
            "INSERT INTO quality_snapshots "
            "(id, snapshot_at, gate_results, uqs_score, grade, tools_available, total_findings, auto_fixed) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (snapshot_id, _utcnow(), json.dumps(results), uqs_score, grade, available, total_findings, 0),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "completed",
        "snapshot_id": snapshot_id,
        "uqs_score": uqs_score,
        "grade": grade,
        "tools_available": available,
        "total_findings": total_findings,
        "gate_count": len(results),
        "scanned_at": _utcnow(),
    }


def detect_regressions() -> list[dict]:
    """Detect quality regressions using anomaly-detection-derived thresholds.

    Thresholds are adaptive (z-score over historical UQS/findings deltas) when
    anomaly_detection is enabled and enough snapshots exist; otherwise falls back
    to static config values.  Always compares the two most recent snapshots so
    the delta signal stays fresh while the threshold adapts to the population.
    """
    import uuid

    conn = _get_db()
    try:
        thresholds = _compute_adaptive_thresholds(conn)
        uqs_regression_threshold = thresholds["uqs_regression_threshold"]
        uqs_critical_threshold = thresholds["uqs_critical_threshold"]
        findings_increase_threshold = thresholds["findings_increase_threshold"]
        adaptive = thresholds.get("adaptive", False)
        if adaptive:
            logger.debug(
                "quality: adaptive thresholds active (n=%d, uqs_mean=%.3f, uqs_std=%.3f)",
                thresholds.get("_n", 0),
                thresholds.get("_uqs_delta_mean", 0.0),
                thresholds.get("_uqs_delta_std", 0.0),
            )

        rows = conn.execute("SELECT * FROM quality_snapshots ORDER BY snapshot_at DESC LIMIT 2").fetchall()
        if len(rows) < 2:
            return []

        current = dict(rows[0])
        previous = dict(rows[1])
        regressions = []
        mode_tag = f"adaptive n={thresholds.get('_n', '?')}" if adaptive else "static"

        uqs_delta = current["uqs_score"] - previous["uqs_score"]
        if uqs_delta < uqs_regression_threshold:
            reg = {
                "id": f"qt-{uuid.uuid4().hex[:10]}",
                "detected_at": _utcnow(),
                "trend_type": "uqs_regression",
                "dimension": "overall",
                "direction": "declining",
                "severity": "warning" if uqs_delta > uqs_critical_threshold else "critical",
                "detail": (
                    f"UQS dropped {abs(uqs_delta):.1f} points "
                    f"({previous['uqs_score']:.1f} → {current['uqs_score']:.1f}) "
                    f"[threshold={uqs_regression_threshold:.1f}, {mode_tag}]"
                ),
                "resolved": 0,
            }
            regressions.append(reg)
            conn.execute(
                "INSERT INTO quality_trends (id, detected_at, trend_type, dimension, direction, severity, detail) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (reg["id"], reg["detected_at"], reg["trend_type"], reg["dimension"],
                 reg["direction"], reg["severity"], reg["detail"]),
            )

        findings_delta = current["total_findings"] - previous["total_findings"]
        if findings_delta > findings_increase_threshold:
            reg = {
                "id": f"qt-{uuid.uuid4().hex[:10]}",
                "detected_at": _utcnow(),
                "trend_type": "findings_increase",
                "dimension": "overall",
                "direction": "increasing",
                "severity": "warning",
                "detail": (
                    f"Total findings increased by {findings_delta} "
                    f"({previous['total_findings']} → {current['total_findings']}) "
                    f"[threshold={findings_increase_threshold}, {mode_tag}]"
                ),
                "resolved": 0,
            }
            regressions.append(reg)
            conn.execute(
                "INSERT INTO quality_trends (id, detected_at, trend_type, dimension, direction, severity, detail) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (reg["id"], reg["detected_at"], reg["trend_type"], reg["dimension"],
                 reg["direction"], reg["severity"], reg["detail"]),
            )

        conn.commit()
        return regressions
    finally:
        conn.close()


def auto_remediate() -> dict:
    """Attempt auto-fix on safe gates (lint, deprecation)."""
    try:
        from tools.qdc_canvas.gate_executor import auto_fix, execute_gate
    except ImportError:
        return {"status": "error", "message": "gate_executor not available"}

    fixes = []

    # Try lint auto-fix
    lint_result = execute_gate("lint")
    if lint_result.get("status") == "fail":
        fix_result = auto_fix(lint_result)
        fixes.append(fix_result)

    # Try deprecation auto-fix
    dep_result = execute_gate("deprecation")
    if dep_result.get("status") == "fail":
        fix_result = auto_fix(dep_result)
        fixes.append(fix_result)

    return {
        "status": "completed",
        "fixes_attempted": len(fixes),
        "fixes": fixes,
        "remediated_at": _utcnow(),
    }


def generate_improvement_gkp() -> dict | None:
    """Generate a Genesis Knowledge Packet proposing QA/QC improvements.

    Analyzes recent trends and proposes actionable improvements.
    """
    import uuid

    conn = _get_db()
    try:
        # Get latest snapshot
        latest = conn.execute("SELECT * FROM quality_snapshots ORDER BY snapshot_at DESC LIMIT 1").fetchone()
        if not latest:
            return None

        latest = dict(latest)
        results = json.loads(latest.get("gate_results", "[]"))

        # Find worst-performing gates
        cfg = _load_reflex_config()
        worst_gates_count = int(cfg.get("worst_gates_count", 3))
        gate_score_threshold = _compute_adaptive_gate_score_threshold(conn)
        worst_gates = sorted(results, key=lambda r: r.get("score", 100))[:worst_gates_count]
        if not worst_gates or worst_gates[0].get("score", 100) >= gate_score_threshold:
            return None  # No improvement needed

        gate = worst_gates[0]
        gkp_id = f"gkp-{uuid.uuid4().hex[:10]}"
        title = f"QA/QC Improvement: {gate.get('gate_id', 'unknown')} gate at {gate.get('score', 0):.0f}%"
        body = (
            f"## Quality Improvement Proposal\n\n"
            f"**Gate**: {gate.get('gate_id')}\n"
            f"**Current Score**: {gate.get('score', 0):.1f}%\n"
            f"**Tool**: {gate.get('tool', 'unknown')}\n"
            f"**Findings**: {gate.get('findings_count', 0)}\n\n"
            f"### Recommended Actions\n"
            f"1. Review findings from {gate.get('tool')} output\n"
            f"2. Address highest-severity items first\n"
            f"3. Re-run gate to verify improvement\n"
            f"4. Update quality thresholds if needed\n"
        )

        conn.execute(
            "INSERT INTO quality_gkps (id, created_at, gkp_type, title, body, source_gate, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (gkp_id, _utcnow(), "improvement", title, body, gate.get("gate_id"), "pending"),
        )
        conn.commit()

        return {"gkp_id": gkp_id, "title": title, "source_gate": gate.get("gate_id"), "status": "pending"}
    finally:
        conn.close()


def run_reflex() -> dict:
    """Main reflex entry point — called by Genesis daemon.

    Pipeline: scan → detect regressions → auto-remediate → propose improvements
    """
    scan = run_quality_scan()
    regressions = detect_regressions()
    remediation = auto_remediate()
    gkp = generate_improvement_gkp()

    return {
        "reflex": "quality",
        "scan": scan,
        "regressions": regressions,
        "remediation": remediation,
        "gkp": gkp,
        "completed_at": _utcnow(),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Genesis Quality Reflex")
    parser.add_argument("--scan", action="store_true", help="Run quality scan")
    parser.add_argument("--regressions", action="store_true", help="Detect regressions")
    parser.add_argument("--remediate", action="store_true", help="Auto-remediate safe issues")
    parser.add_argument("--gkp", action="store_true", help="Generate improvement GKP")
    parser.add_argument("--full", action="store_true", help="Run full reflex pipeline")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.full:
        result = run_reflex()
    elif args.scan:
        result = run_quality_scan()
    elif args.regressions:
        result = detect_regressions()
    elif args.remediate:
        result = auto_remediate()
    elif args.gkp:
        result = generate_improvement_gkp()
    else:
        parser.print_help()
        result = None

    if result is not None:
        print(json.dumps(result, indent=2, default=str))
