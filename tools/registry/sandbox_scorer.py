#!/usr/bin/env python3
# CUI // SP-CTI
"""Sandbox Security Scorer — 8th evaluation dimension (D-NC-4).

Measures the isolation posture of a capability's source environment,
adapted from NemoClaw's defense-in-depth scoring.  Plugs into
CapabilityEvaluator as an optional 8th dimension.

Scoring rubric (0.0-1.0):
    credential_isolation  0.25 — Source uses credential broker, not raw keys
    network_restriction   0.20 — Source has egress policy applied
    digest_verified       0.20 — Capability has verified blueprint digest
    provenance_chain      0.15 — Full PROV-AGENT lineage exists
    runtime_isolation     0.10 — Source runs in container (not host)
    audit_completeness    0.10 — All operations logged append-only

CLI:
    python tools/registry/sandbox_scorer.py --score --capability-id cap-123 --json
    python tools/registry/sandbox_scorer.py --score --source-metadata '{"has_broker":true}' --json
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# Gate threshold — matches capability evaluation score threshold (D224)
GATE_THRESHOLD = 0.65

# Scoring rubric weights (must sum to 1.0)
RUBRIC = {
    "credential_isolation": 0.25,
    "network_restriction": 0.20,
    "digest_verified": 0.20,
    "provenance_chain": 0.15,
    "runtime_isolation": 0.10,
    "audit_completeness": 0.10,
}


def _get_db(db_path: Path) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def score_sandbox(
    capability_data: Optional[Dict] = None,
    source_metadata: Optional[Dict] = None,
    db_path: Optional[Path] = None,
) -> Dict:
    """Score sandbox security posture for a capability source.

    Args:
        capability_data: Capability dict from evaluator (optional).
        source_metadata: Explicit metadata overrides (optional).
            Keys: has_broker, has_egress_policy, has_digest,
                  has_provenance, runs_in_container, has_audit_trail.
        db_path: Database path override.

    Returns:
        Dict with score, breakdown, recommendations.
    """
    db = db_path or DB_PATH
    meta = source_metadata or {}
    cap = capability_data or {}

    breakdown = {}
    recommendations = []

    # 1. Credential isolation — check if broker has granted tokens
    if meta.get("has_broker") is not None:
        broker_score = 1.0 if meta["has_broker"] else 0.0
    else:
        broker_score = _check_credential_broker(cap, db)
    breakdown["credential_isolation"] = broker_score
    if broker_score < 0.5:
        recommendations.append("Enable credential broker for agent credential isolation (D-NC-1)")

    # 2. Network restriction — check if egress policy has been resolved
    if meta.get("has_egress_policy") is not None:
        egress_score = 1.0 if meta["has_egress_policy"] else 0.0
    else:
        egress_score = _check_egress_policy(cap, db)
    breakdown["network_restriction"] = egress_score
    if egress_score < 0.5:
        recommendations.append("Apply per-agent egress policies (D-NC-2)")

    # 3. Digest verified — check blueprint_digests table
    if meta.get("has_digest") is not None:
        digest_score = 1.0 if meta["has_digest"] else 0.0
    else:
        digest_score = _check_digest(cap, db)
    breakdown["digest_verified"] = digest_score
    if digest_score < 0.5:
        recommendations.append("Compute and verify blueprint digest before absorption (D-NC-3)")

    # 4. Provenance chain — check prov_entities/prov_relations
    if meta.get("has_provenance") is not None:
        prov_score = 1.0 if meta["has_provenance"] else 0.0
    else:
        prov_score = _check_provenance(cap, db)
    breakdown["provenance_chain"] = prov_score
    if prov_score < 0.5:
        recommendations.append("Ensure PROV-AGENT lineage exists for capability source (D287)")

    # 5. Runtime isolation — check if source runs in container
    if meta.get("runs_in_container") is not None:
        container_score = 1.0 if meta["runs_in_container"] else 0.2
    else:
        container_score = _check_runtime_isolation(cap)
    breakdown["runtime_isolation"] = container_score
    if container_score < 0.5:
        recommendations.append("Run agent in STIG-hardened container for isolation")

    # 6. Audit completeness — check audit_trail entries exist
    if meta.get("has_audit_trail") is not None:
        audit_score = 1.0 if meta["has_audit_trail"] else 0.0
    else:
        audit_score = _check_audit_completeness(cap, db)
    breakdown["audit_completeness"] = audit_score
    if audit_score < 0.5:
        recommendations.append("Ensure all operations are logged to append-only audit trail (D6)")

    # Weighted average
    total = sum(breakdown[dim] * weight for dim, weight in RUBRIC.items())
    total = round(total, 4)

    return {
        "score": total,
        "breakdown": breakdown,
        "rubric_weights": dict(RUBRIC),
        "recommendations": recommendations,
        "source": cap.get("source", "unknown"),
    }


def _check_credential_broker(cap: Dict, db_path: Path) -> float:
    """Check if credential broker has active grants."""
    try:
        conn = _get_db(db_path)
        count = conn.execute("SELECT COUNT(*) FROM credential_broker_log WHERE action = 'grant'").fetchone()[0]
        conn.close()
        return 1.0 if count > 0 else 0.0
    except Exception:
        return 0.0


def _check_egress_policy(cap: Dict, db_path: Path) -> float:
    """Check if egress policies have been resolved."""
    try:
        conn = _get_db(db_path)
        count = conn.execute("SELECT COUNT(*) FROM egress_policy_audit WHERE action = 'resolve'").fetchone()[0]
        conn.close()
        return 1.0 if count > 0 else 0.0
    except Exception:
        return 0.0


def _check_digest(cap: Dict, db_path: Path) -> float:
    """Check if a verified digest exists for this capability."""
    cap_id = cap.get("id", "")
    try:
        conn = _get_db(db_path)
        if cap_id:
            row = conn.execute(
                """SELECT verification_result FROM blueprint_digests
                   WHERE entity_id = %s ORDER BY computed_at DESC LIMIT 1""",
                (cap_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT verification_result FROM blueprint_digests
                   ORDER BY computed_at DESC LIMIT 1""",
            ).fetchone()
        conn.close()
        if row and row["verification_result"] == "pass":
            return 1.0
        elif row:
            return 0.5  # Digest exists but not verified or failed
        return 0.0
    except Exception:
        return 0.0


def _check_provenance(cap: Dict, db_path: Path) -> float:
    """Check if PROV-AGENT provenance records exist."""
    try:
        conn = _get_db(db_path)
        count = conn.execute("SELECT COUNT(*) FROM prov_entities").fetchone()[0]
        conn.close()
        return 1.0 if count > 0 else 0.0
    except Exception:
        return 0.0


def _check_runtime_isolation(cap: Dict) -> float:
    """Check if source is containerized (enhanced with D-SEC-10 sandbox probe)."""
    source = cap.get("source", "")
    # Children and marketplace assets are containerized
    if source in ("child_report", "child", "marketplace"):
        return 1.0
    # Innovation engine runs in parent process
    if source in ("innovation", "introspective"):
        return 0.5
    # D-SEC-10: Attempt real container health check via SandboxExecutor
    try:
        from tools.security.sandbox_executor import SandboxExecutor

        executor = SandboxExecutor()
        if executor._available:
            health = executor.health_check()
            if health.get("docker_available"):
                return 0.8  # Docker confirmed available, partial credit
    except (ImportError, Exception):
        pass
    # External/unknown defaults to low
    return 0.2


def _check_audit_completeness(cap: Dict, db_path: Path) -> float:
    """Check if audit trail has entries."""
    try:
        conn = _get_db(db_path)
        count = conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]
        conn.close()
        return 1.0 if count > 0 else 0.0
    except Exception:
        return 0.0


def main():
    parser = argparse.ArgumentParser(description="Sandbox Security Scorer — 8th evaluation dimension (D-NC-4)")
    parser.add_argument("--score", action="store_true", help="Score sandbox security")
    parser.add_argument("--gate", action="store_true", help="Gate evaluation (exit 1 if score < threshold)")
    parser.add_argument("--capability-id", type=str, default="", help="Capability ID")
    parser.add_argument("--source-metadata", type=str, default="", help="JSON metadata override")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, help="Database path override")
    args = parser.parse_args()

    if args.score or args.gate:
        cap_data = {"id": args.capability_id} if args.capability_id else {}
        meta = json.loads(args.source_metadata) if args.source_metadata else None
        result = score_sandbox(
            capability_data=cap_data,
            source_metadata=meta,
            db_path=args.db_path,
        )
        if args.gate:
            passed = result["score"] >= GATE_THRESHOLD
            result = {
                "gate": "sandbox_security",
                "passed": passed,
                "score": result["score"],
                "threshold": GATE_THRESHOLD,
                "recommendations": result["recommendations"],
            }
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result)

    if args.gate and not result["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
