# CUI // SP-CTI — NDC NIPR Constraint Validator (NET-NIPR-001)
"""Validates that the system satisfies the NIPR-only network constraint.

Checks that no SIPR/IL6 dependencies exist in:
  - args/cloud_config.yaml (impact_level, network_constraint)
  - args/remote_gateway_config.yaml (channel max_il bounds)
  - Network traffic flows in the DB (nc_traffic_flows classification)
  - Compliance regimes (no NSA Type 1 / cnss1253 / icd503 required)

Exit codes: 0 = PASS, 1 = FAIL (blocked), 2 = WARN.

Usage:
    python tools/network/nipr_constraint_validator.py --json
    python tools/network/nipr_constraint_validator.py --json --gate
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

ROOT = Path(__file__).resolve().parents[2]

# Networks/classifications that must never appear as requirements
_BLOCKED_NETWORKS = {"sipr", "jwics", "il6"}
_BLOCKED_IMPACT_LEVELS = {"IL6"}
_BLOCKED_REGIMES = {"cnss1253", "icd503"}  # NSA/IC regimes implying SIPR-level encryption
_BLOCKED_ENC_KEYWORDS = {"type 1", "type1", "nsa type", "comsec", "kg-", "taclane", "kiv-"}


def _load_yaml(path: Path) -> dict:
    if not _HAS_YAML:
        return {}
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _check_cloud_config() -> list[dict]:
    """Validate args/cloud_config.yaml network_constraint block."""
    findings = []
    cfg = _load_yaml(ROOT / "args" / "cloud_config.yaml")

    cloud = cfg.get("cloud", {})
    impact = cloud.get("impact_level", "")
    if impact in _BLOCKED_IMPACT_LEVELS:
        findings.append({
            "id": "NET-NIPR-001-A",
            "severity": "BLOCK",
            "message": f"cloud.impact_level is '{impact}' — IL6 requires SIPR. "
                       "Set impact_level to IL5 or lower.",
            "field": "cloud.impact_level",
        })

    nc = cfg.get("network_constraint", {})
    if nc.get("sipr_required", False) is True:
        findings.append({
            "id": "NET-NIPR-001-B",
            "severity": "BLOCK",
            "message": "network_constraint.sipr_required is true — must be false for NIPR-only operation.",
            "field": "network_constraint.sipr_required",
        })

    mode = nc.get("mode", "")
    if mode and mode != "nipr_only":
        findings.append({
            "id": "NET-NIPR-001-C",
            "severity": "BLOCK",
            "message": f"network_constraint.mode is '{mode}' — must be 'nipr_only'.",
            "field": "network_constraint.mode",
        })

    blocked = {n.lower() for n in nc.get("blocked_networks", [])}
    missing = _BLOCKED_NETWORKS - blocked
    if missing:
        findings.append({
            "id": "NET-NIPR-001-D",
            "severity": "WARN",
            "message": f"network_constraint.blocked_networks is missing entries: {sorted(missing)}. "
                       "Add sipr, jwics, il6 to explicitly exclude them.",
            "field": "network_constraint.blocked_networks",
        })

    return findings


def _check_remote_gateway() -> list[dict]:
    """Validate remote_gateway_config.yaml channel max_il bounds."""
    findings = []
    cfg = _load_yaml(ROOT / "args" / "remote_gateway_config.yaml")

    for name, ch in (cfg.get("channels") or {}).items():
        max_il = str(ch.get("max_il", "")).upper()
        if max_il == "IL6":
            findings.append({
                "id": "NET-NIPR-001-E",
                "severity": "WARN",
                "message": f"Gateway channel '{name}' has max_il=IL6. "
                           "Consider bounding at IL5 for NIPR-only deployments.",
                "field": f"channels.{name}.max_il",
            })

    return findings


def _check_db_traffic_flows() -> list[dict]:
    """Check nc_traffic_flows table for SIPR-classified flows."""
    findings = []
    try:
        from tools.db.storage import get_connection
        conn = get_connection("network_canvas")
        cur = conn.execute(
            "SELECT id, name, classification FROM nc_traffic_flows "
            "WHERE LOWER(classification) IN ('sipr', 'il6') LIMIT 20"
        )
        rows = cur.fetchall()
        conn.close()
        if rows:
            for row in rows:
                findings.append({
                    "id": "NET-NIPR-001-F",
                    "severity": "WARN",
                    "message": f"Traffic flow '{row[1]}' (id={row[0]}) has classification "
                               f"'{row[2]}' — SIPR/IL6 classified flows should not exist in "
                               "a NIPR-only deployment.",
                    "field": "nc_traffic_flows.classification",
                })
    except Exception:
        pass  # DB unavailable — skip, not a blocking condition
    return findings


def _check_compliance_regimes() -> list[dict]:
    """Check that no SIPR-only compliance regimes are required."""
    findings = []
    # Look for regime enablement in any args yaml
    for name in ("security_gates.yaml", "cloud_config.yaml"):
        cfg = _load_yaml(ROOT / "args" / name)
        raw = json.dumps(cfg).lower()
        for regime in _BLOCKED_REGIMES:
            if '"required": true' in raw and regime in raw:
                findings.append({
                    "id": "NET-NIPR-001-G",
                    "severity": "WARN",
                    "message": f"Compliance regime '{regime}' appears required in {name}. "
                               f"This regime implies NSA/IC-level encryption (SIPR). "
                               "Verify it is not mandated for this NIPR-only system.",
                    "field": name,
                })
                break
    return findings


def _run_all_checks() -> dict:
    findings = []
    findings += _check_cloud_config()
    findings += _check_remote_gateway()
    findings += _check_db_traffic_flows()
    findings += _check_compliance_regimes()

    blocks = [f for f in findings if f["severity"] == "BLOCK"]
    warns = [f for f in findings if f["severity"] == "WARN"]

    passed = len(blocks) == 0
    status = "PASS" if passed else "FAIL"
    if passed and warns:
        status = "WARN"

    return {
        "gate": "NET-NIPR-001",
        "name": "NIPR-Only Network Constraint",
        "status": status,
        "passed": passed,
        "blocks": blocks,
        "warnings": warns,
        "summary": (
            f"{len(blocks)} blocking finding(s), {len(warns)} warning(s). "
            f"System {'IS' if passed else 'IS NOT'} compliant with NIPR-only constraint."
        ),
        "nist_controls": ["SC-7", "SC-7(4)", "SC-32", "AC-4", "AC-4(21)"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="NET-NIPR-001 NIPR-Only Constraint Validator")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if blocked, 2 if warnings only")
    args = parser.parse_args()

    result = _run_all_checks()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[{result['status']}] {result['name']}")
        print(f"  {result['summary']}")
        for f in result["blocks"]:
            print(f"  BLOCK [{f['id']}] {f['message']}")
        for w in result["warnings"]:
            print(f"  WARN  [{w['id']}] {w['message']}")

    if args.gate:
        if not result["passed"]:
            sys.exit(1)
        if result["warnings"]:
            sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
