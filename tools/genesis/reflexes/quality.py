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
import logging
from datetime import datetime, timezone
from pathlib import Path

from tools.db.storage import get_connection

logger = get_logger(__name__)

ICDEV_ROOT = Path(__file__).resolve().parents[3]
GENESIS_DB = ICDEV_ROOT / "data" / "genesis_quality.db"


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
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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
    """Compare last two snapshots to detect quality regressions."""
    import uuid

    conn = _get_db()
    try:
        rows = conn.execute("SELECT * FROM quality_snapshots ORDER BY snapshot_at DESC LIMIT 2").fetchall()
        if len(rows) < 2:
            return []

        current = dict(rows[0])
        previous = dict(rows[1])
        regressions = []

        # UQS regression
        uqs_delta = current["uqs_score"] - previous["uqs_score"]
        if uqs_delta < -5.0:
            reg = {
                "id": f"qt-{uuid.uuid4().hex[:10]}",
                "detected_at": _utcnow(),
                "trend_type": "uqs_regression",
                "dimension": "overall",
                "direction": "declining",
                "severity": "warning" if uqs_delta > -15 else "critical",
                "detail": f"UQS dropped {abs(uqs_delta):.1f} points ({previous['uqs_score']:.1f} → {current['uqs_score']:.1f})",
                "resolved": 0,
            }
            regressions.append(reg)
            conn.execute(
                "INSERT INTO quality_trends (id, detected_at, trend_type, dimension, direction, severity, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    reg["id"],
                    reg["detected_at"],
                    reg["trend_type"],
                    reg["dimension"],
                    reg["direction"],
                    reg["severity"],
                    reg["detail"],
                ),
            )

        # Findings increase
        findings_delta = current["total_findings"] - previous["total_findings"]
        if findings_delta > 5:
            reg = {
                "id": f"qt-{uuid.uuid4().hex[:10]}",
                "detected_at": _utcnow(),
                "trend_type": "findings_increase",
                "dimension": "overall",
                "direction": "increasing",
                "severity": "warning",
                "detail": f"Total findings increased by {findings_delta} ({previous['total_findings']} → {current['total_findings']})",
                "resolved": 0,
            }
            regressions.append(reg)
            conn.execute(
                "INSERT INTO quality_trends (id, detected_at, trend_type, dimension, direction, severity, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    reg["id"],
                    reg["detected_at"],
                    reg["trend_type"],
                    reg["dimension"],
                    reg["direction"],
                    reg["severity"],
                    reg["detail"],
                ),
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
        worst_gates = sorted(results, key=lambda r: r.get("score", 100))[:3]
        if not worst_gates or worst_gates[0].get("score", 100) >= 80:
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
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
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
