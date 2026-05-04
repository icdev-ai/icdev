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


def register_cato_evidence(health_score: float, findings_summary: Dict) -> List[Dict]:
    """Register review board health score as cATO continuous monitoring evidence."""
    conn = _get_connection()
    results = []
    try:
        now = _now()
        expires = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

        for control_id, mapping in CONTROL_MAPPINGS.items():
            evidence_hash = hashlib.sha256(
                json.dumps({"score": health_score, "control": control_id, "at": now}).encode()
            ).hexdigest()

            try:
                # Use INSERT OR REPLACE to update existing evidence for this control
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cato_evidence
                        (project_id, control_id, evidence_type, evidence_source,
                         evidence_path, evidence_hash, collected_at, expires_at,
                         is_fresh, status, automation_frequency)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        "icdev",
                        control_id,
                        "scan_result",
                        "review_board_daemon",
                        f"review_board/health/{control_id}",
                        evidence_hash,
                        now,
                        expires,
                        1,
                        "current",
                        "daily",
                    ),
                )
                results.append({"control_id": control_id, "status": "registered"})
            except Exception as e:
                results.append({"control_id": control_id, "status": "failed", "error": str(e)})

        conn.commit()
    except Exception as e:
        results.append({"status": "error", "error": str(e)})
    finally:
        conn.close()

    return results


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
                        "WHERE reflex_name = ?",
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

    # 2. Get current health score if not provided
    if health_score is None:
        try:
            from tools.review_board.health_scorer import get_latest

            latest = get_latest()
            health_score = latest.get("score", 0)
        except Exception:
            health_score = 0

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
    results["evidence"] = register_cato_evidence(health_score, findings_summary)

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
            print(f"Evidence registered: {len(result['evidence'])} controls")
            controls = result.get("controls", {})
            print(f"Control coverage: {controls.get('fully_implemented', 0)}/{controls.get('total_controls', 0)}")
    elif args.evidence:
        evidence = register_cato_evidence(0, {})
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
