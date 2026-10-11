#!/usr/bin/env python3
# CUI // SP-CTI
"""Review Board Compliance Bridge — ties findings into ICDEV™ audit, evidence, and NIST controls.

Integration points:
    1. Audit trail (tools/audit/audit_logger.py) — log_event() for all review board actions
    2. cATO evidence (cato_evidence table) — health scores as continuous monitoring evidence
    3. NIST control mapping — AU-2, AU-6, SI-4, CA-7

NIST 800-53 Control Mapping:
    AU-2  (Audit Events)          — Review board event logging
    AU-6  (Audit Review, Analysis) — Continuous automated review of codebase
    SI-4  (System Monitoring)      — SRE/Perf reflexes monitor operational health
    CA-7  (Continuous Monitoring)  — 8-persona continuous assessment
    SA-11 (Developer Testing)      — QA reflex coverage analysis
    RA-5  (Vulnerability Scanning) — Security reflex CVE/SAST checks

Usage:
    python tools/review_board/compliance_bridge.py --sync --json
    python tools/review_board/compliance_bridge.py --evidence --json
    python tools/review_board/compliance_bridge.py --controls --json
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection as _raw_get_connection  # noqa: E402

# NIST controls that Review Board satisfies
CONTROL_MAPPINGS = {
    "AU-2": {
        "title": "Audit Events",
        "description": "Review Board logs all scan, finding, remediation, and escalation events to append-only audit trail",
        "reflexes": ["sre", "qa", "security", "perf", "ux", "docs", "product", "report"],
    },
    "AU-6": {
        "title": "Audit Review, Analysis, and Reporting",
        "description": "Automated continuous review of codebase via 8 engineering personas with weekly digest reports",
        "reflexes": ["report"],
    },
    "SI-4": {
        "title": "System Monitoring",
        "description": "SRE and Performance reflexes continuously monitor operational health, error rates, resource usage",
        "reflexes": ["sre", "perf"],
    },
    "CA-7": {
        "title": "Continuous Monitoring",
        "description": "8-persona continuous assessment covers security, quality, performance, accessibility, documentation",
        "reflexes": ["sre", "qa", "security", "perf", "ux", "docs", "product", "report"],
    },
    "SA-11": {
        "title": "Developer Testing and Evaluation",
        "description": "QA reflex monitors test coverage, detects untested modules, syntax errors, test-to-code ratio",
        "reflexes": ["qa"],
    },
    "RA-5": {
        "title": "Vulnerability Monitoring and Scanning",
        "description": "Security reflex scans for CVE SLA compliance, secret exposure, injection patterns, dangerous code",
        "reflexes": ["security"],
    },
}


def _get_connection():
    conn = _raw_get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_to_audit_trail(event_type: str, action: str, details: Dict = None) -> int:
    """Write review board event to main ICDEV™ audit trail (NIST AU-2)."""
    try:
        from tools.audit.audit_logger import log_event

        return log_event(
            event_type=event_type,
            actor="review-board-daemon",
            action=action,
            details=details,
            classification="CUI",
        )
    except Exception:
        return -1


# Evidence is valid for this long, and must be backed by a review run inside it.
EVIDENCE_WINDOW_HOURS = 24

# PG-native upsert on cato_evidence's real unique key -- also valid on SQLite
# >= 3.24. Authored directly so translate_sql is not load-bearing: its
# `INSERT OR REPLACE` rewrite picked a conflict target with no matching
# constraint and aborted the transaction on PostgreSQL (rem-hyg-22).
_UPSERT_EVIDENCE_SQL = """
    INSERT INTO cato_evidence
        (project_id, control_id, evidence_type, evidence_source,
         evidence_path, evidence_hash, collected_at, expires_at,
         is_fresh, status, automation_frequency)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (project_id, control_id, evidence_type, evidence_source) DO UPDATE SET
        evidence_path = excluded.evidence_path,
        evidence_hash = excluded.evidence_hash,
        collected_at = excluded.collected_at,
        expires_at = excluded.expires_at,
        is_fresh = excluded.is_fresh,
        status = excluded.status,
        automation_frequency = excluded.automation_frequency
"""


def register_cato_evidence(health_score: float, findings_summary: Dict, score_ref: str = None) -> List[Dict]:
    """Register review board health score as cATO continuous monitoring evidence.

    Refuses when ``health_score`` is None: an unmeasured score (no review
    ran) is not evidence, and registering it would write `status='current'`
    rows for six controls backed by nothing (rem-hyg-22).
    """
    if health_score is None:
        return [
            {"control_id": control_id, "status": "refused", "reason": "health score unmeasured: no review ran"}
            for control_id in CONTROL_MAPPINGS
        ]

    conn = _get_connection()
    results = []
    try:
        now = _now()
        expires = (datetime.now(timezone.utc) + timedelta(hours=EVIDENCE_WINDOW_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        evidence_path_base = f"review_board_health_history/{score_ref}" if score_ref else "review_board/health"

        for control_id in CONTROL_MAPPINGS:
            evidence_hash = hashlib.sha256(
                json.dumps({"score": health_score, "control": control_id, "at": now}).encode()
            ).hexdigest()
            conn.execute(
                _UPSERT_EVIDENCE_SQL,
                (
                    "icdev",
                    control_id,
                    "scan_result",
                    "review_board_daemon",
                    f"{evidence_path_base}?control={control_id}&score={health_score}",
                    evidence_hash,
                    now,
                    expires,
                    1,
                    "current",
                    "daily",
                ),
            )
            results.append({"control_id": control_id, "status": "registered", "score": health_score})

        conn.commit()
    except Exception as e:
        # One failed statement aborts a PG transaction, so a partial batch is
        # not committed: report every control as failed rather than a mix.
        try:
            conn.rollback()
        except Exception:
            pass
        results = [
            {"control_id": control_id, "status": "failed", "error": str(e)} for control_id in CONTROL_MAPPINGS
        ]
    finally:
        conn.close()

    return results


def _parse_ts(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def measured_score(window_hours: int = EVIDENCE_WINDOW_HOURS) -> Dict[str, Any]:
    """Return the score evidence may cite, or why there is none.

    Evidence needs BOTH a reflex run inside the window and a measured health
    score computed inside it. The latest history row alone is not enough: the
    table holds scores of 100 computed while no reflex ran at all.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    conn = _get_connection()
    try:
        reviews_in_window = 0
        try:
            rows = conn.execute(
                "SELECT reflex_name, last_run_at FROM review_board_reflex_state WHERE last_run_at IS NOT NULL"
            ).fetchall()
            for r in rows:
                try:
                    if _parse_ts(r[1]) >= cutoff:
                        reviews_in_window += 1
                except (ValueError, TypeError):
                    continue
        except Exception:
            pass
        if reviews_in_window == 0:
            return {
                "score": None,
                "reviews_in_window": 0,
                "reason": f"no Review Board reflex ran in the last {window_hours}h",
            }

        try:
            latest = conn.execute(
                "SELECT id, score, created_at FROM review_board_health_history ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        except Exception:
            latest = None
        if not latest or latest[1] is None or _parse_ts(latest[2]) < cutoff:
            return {
                "score": None,
                "reviews_in_window": reviews_in_window,
                "reason": f"no measured health score computed in the last {window_hours}h",
            }
        return {"score": latest[1], "score_ref": latest[0], "reviews_in_window": reviews_in_window}
    finally:
        conn.close()


def get_control_coverage() -> Dict[str, Any]:
    """Get NIST control coverage provided by Review Board."""
    conn = _get_connection()
    coverage = {}
    try:
        for control_id, mapping in CONTROL_MAPPINGS.items():
            # Check if reflexes are active
            active_reflexes = 0
            for reflex in mapping["reflexes"]:
                try:
                    row = conn.execute(
                        "SELECT enabled, total_runs, total_successes FROM review_board_reflex_state "
                        "WHERE reflex_name = %s",
                        (reflex,),
                    ).fetchone()
                    if row and row[0]:  # enabled
                        active_reflexes += 1
                except Exception:
                    pass

            coverage[control_id] = {
                "title": mapping["title"],
                "description": mapping["description"],
                "total_reflexes": len(mapping["reflexes"]),
                "active_reflexes": active_reflexes,
                "coverage_pct": round((active_reflexes / len(mapping["reflexes"])) * 100, 1)
                if mapping["reflexes"]
                else 0,
                "status": "implemented"
                if active_reflexes == len(mapping["reflexes"])
                else "partially_implemented"
                if active_reflexes > 0
                else "not_implemented",
            }
    except Exception:
        pass
    finally:
        conn.close()

    return {
        "controls": coverage,
        "total_controls": len(CONTROL_MAPPINGS),
        "fully_implemented": sum(1 for c in coverage.values() if c["status"] == "implemented"),
    }


def sync_all(health_score: float = None) -> Dict[str, Any]:
    """Full sync: audit trail + cATO evidence + control mapping."""
    results = {"audit": None, "evidence": [], "controls": None}

    # 1. Log sync event to audit trail
    results["audit"] = log_to_audit_trail(
        "review_board.scan_completed",
        "Review Board compliance sync — registering evidence for AU-2, AU-6, SI-4, CA-7, SA-11, RA-5",
        {"health_score": health_score},
    )

    # 2. Get the score evidence may cite. Never default to 0 or 100: with no
    # review in the window there is no score, and no evidence is registered.
    score_ref = None
    if health_score is None:
        measured = measured_score()
        health_score = measured.get("score")
        score_ref = measured.get("score_ref")
        results["measurement"] = measured

    # 3. Get findings summary
    conn = _get_connection()
    findings_summary = {}
    try:
        rows = conn.execute(
            "SELECT severity, COUNT(*) FROM review_board_findings WHERE fix_applied = 0 GROUP BY severity"
        ).fetchall()
        findings_summary = {r[0]: r[1] for r in rows}
    except Exception:
        pass
    finally:
        conn.close()

    # 4. Register cATO evidence
    results["evidence"] = register_cato_evidence(health_score, findings_summary, score_ref=score_ref)

    # 5. Get control coverage
    results["controls"] = get_control_coverage()

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Review Board Compliance Bridge")
    parser.add_argument("--sync", action="store_true", help="Full sync: audit + evidence + controls")
    parser.add_argument("--evidence", action="store_true", help="Register cATO evidence only")
    parser.add_argument("--controls", action="store_true", help="Show NIST control coverage")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.sync:
        result = sync_all()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Audit event: {result['audit']}")
            registered = sum(1 for e in result["evidence"] if e.get("status") == "registered")
            print(f"Evidence registered: {registered}/{len(result['evidence'])} controls")
            if result.get("measurement", {}).get("reason"):
                print(f"  Not registered: {result['measurement']['reason']}")
            controls = result.get("controls", {})
            print(f"Control coverage: {controls.get('fully_implemented', 0)}/{controls.get('total_controls', 0)}")
    elif args.evidence:
        measured = measured_score()
        evidence = register_cato_evidence(measured.get("score"), {}, score_ref=measured.get("score_ref"))
        if args.json:
            print(json.dumps(evidence, indent=2))
        else:
            for e in evidence:
                print(f"  {e.get('control_id', 'N/A')}: {e.get('status', 'unknown')}")
    elif args.controls:
        coverage = get_control_coverage()
        if args.json:
            print(json.dumps(coverage, indent=2))
        else:
            for cid, info in coverage.get("controls", {}).items():
                print(f"  {cid} ({info['title']}): {info['status']} ({info['coverage_pct']}%)")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
