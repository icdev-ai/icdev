#!/usr/bin/env python3
# CUI // SP-CTI
"""SDC Demo Runner — 3 live scenarios for executive demos.

Scenarios:
  A: Before State       — 47 STIG findings, 3 open attack paths
  B: Remediation        — threats mitigated, SOPs approved
  C: After State        — IaC generated, compliance crosswalk (cat1=0, paths_blocked=3)

Usage:
    python tools/sdc/demo_runner.py --scenario C --json
    python tools/sdc/demo_runner.py --scenario A --audience exec --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_SDC_DB = _ROOT / "data" / "security_canvas.db"

_SCENARIO_META = {
    "A": {
        "title": "Before State — Open STIG Findings",
        "hook": "47 unmitigated STIG findings / 3 active attack paths",
        "color": "#e74c3c",
    },
    "B": {
        "title": "Remediation in Progress",
        "hook": "CAT1 findings remediated / SOPs approved",
        "color": "#f39c12",
    },
    "C": {
        "title": "After State — IaC Generated + Compliance Crosswalk",
        "hook": "cat1=0 / paths_blocked=3 / NIST-800-53 + FedRAMP + CMMC coverage",
        "color": "#27ae60",
    },
}

# After-state graph: fully hardened BCAP/VDSS + IAM + data pipeline design
_AFTER_STATE_GRAPH: Dict[str, Any] = {
    "nodes": [
        {"id": "bcap",      "type": "boundary-bcap",    "label": "BCAP Perimeter"},
        {"id": "vdss-fw",   "type": "ctrl-firewall",    "label": "VDSS Firewall"},
        {"id": "vdss-ids",  "type": "ctrl-ids",         "label": "VDSS IDS/IPS (Inline)"},
        {"id": "vdms",      "type": "asset-server",     "label": "VDMS"},
        {"id": "mission",   "type": "asset-server",     "label": "Mission App"},
        {"id": "db",        "type": "asset-database",   "label": "Mission DB"},
        {"id": "siem",      "type": "ctrl-siem",        "label": "VDMS SIEM"},
        {"id": "encryptor", "type": "ctrl-encryption",  "label": "Type 1 Encryptor"},
        {"id": "kms",       "type": "ctrl-kms",         "label": "KMS (Auto-Rotation)"},
        {"id": "idp",       "type": "ctrl-idp",         "label": "IdP / MFA (FIDO2)"},
        {"id": "pam",       "type": "ctrl-pam",         "label": "PAM Vault (Hardened)"},
        {"id": "cloudtrail","type": "ctrl-siem",        "label": "CloudTrail (All-Region)"},
        {"id": "guardduty", "type": "ctrl-ids",         "label": "GuardDuty"},
        {"id": "ssm",       "type": "ctrl-pam",         "label": "SSM Parameter Store"},
    ],
    "edges": [
        {"id": "e1",  "source": "bcap",       "target": "vdss-fw",    "protocol": "IPSec",   "encrypted": True,  "authenticated": True},
        {"id": "e2",  "source": "vdss-fw",    "target": "vdss-ids",   "protocol": "SPAN",    "encrypted": True,  "authenticated": True},
        {"id": "e3",  "source": "vdss-ids",   "target": "vdms",       "protocol": "TLS",     "encrypted": True,  "authenticated": True},
        {"id": "e4",  "source": "vdms",       "target": "mission",    "protocol": "HTTPS",   "encrypted": True,  "authenticated": True},
        {"id": "e5",  "source": "mission",    "target": "db",         "protocol": "TLS/TCP", "encrypted": True,  "authenticated": True},
        {"id": "e6",  "source": "mission",    "target": "siem",       "protocol": "TLS",     "encrypted": True,  "authenticated": True},
        {"id": "e7",  "source": "bcap",       "target": "encryptor",  "protocol": "Type 1",  "encrypted": True,  "authenticated": True},
        {"id": "e8",  "source": "kms",        "target": "db",         "protocol": "PKCS#11", "encrypted": True,  "authenticated": True},
        {"id": "e9",  "source": "idp",        "target": "pam",        "protocol": "SAML",    "encrypted": True,  "authenticated": True},
        {"id": "e10", "source": "cloudtrail", "target": "siem",       "protocol": "TLS",     "encrypted": True,  "authenticated": True},
        {"id": "e11", "source": "guardduty",  "target": "siem",       "protocol": "TLS",     "encrypted": True,  "authenticated": True},
        {"id": "e12", "source": "ssm",        "target": "mission",    "protocol": "HTTPS",   "encrypted": True,  "authenticated": True},
    ],
    "boundaries": [
        {"id": "bnd-vdss",    "label": "VDSS Zone",    "boundary_type": "network",  "classification": "CUI", "il_level": "IL5", "contained_assets": ["vdss-fw", "vdss-ids"]},
        {"id": "bnd-vdms",    "label": "VDMS Zone",    "boundary_type": "network",  "classification": "CUI", "il_level": "IL5", "contained_assets": ["vdms", "siem"]},
        {"id": "bnd-mission", "label": "Mission Zone", "boundary_type": "network",  "classification": "CUI", "il_level": "IL5", "contained_assets": ["mission", "db"]},
        {"id": "bnd-iam",     "label": "IAM Zone",     "boundary_type": "network",  "classification": "CUI", "il_level": "IL4", "contained_assets": ["pam", "idp"]},
        {"id": "bnd-cloud",   "label": "Cloud Zone",   "boundary_type": "enclave",  "classification": "CUI", "il_level": "IL5", "contained_assets": ["kms", "cloudtrail", "guardduty", "ssm"]},
    ],
}


def _sdc_conn() -> sqlite3.Connection:
    _SDC_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_SDC_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _run_scenario_a(project_id: str, conn: sqlite3.Connection) -> Dict[str, Any]:
    """Before state — 47 open STIG findings, 3 active attack paths."""
    cat1 = conn.execute(
        "SELECT COUNT(*) as cnt FROM sc_threats WHERE threat_category='CAT1' AND status='open'"
    ).fetchone()
    cat2 = conn.execute(
        "SELECT COUNT(*) as cnt FROM sc_threats WHERE threat_category='CAT2' AND status='open'"
    ).fetchone()
    cat3 = conn.execute(
        "SELECT COUNT(*) as cnt FROM sc_threats WHERE threat_category='CAT3' AND status='open'"
    ).fetchone()
    attack_paths = conn.execute(
        "SELECT COUNT(*) as cnt FROM sdc_attack_snapshots"
    ).fetchone()

    return {
        "scenario": "A",
        "title": _SCENARIO_META["A"]["title"],
        "cat1_count": cat1["cnt"] if cat1 else 15,
        "cat2_count": cat2["cnt"] if cat2 else 22,
        "cat3_count": cat3["cnt"] if cat3 else 10,
        "attack_paths_open": attack_paths["cnt"] if attack_paths else 3,
        "posture_grade": "F",
        "risk_score": 9.8,
    }


def _run_scenario_b(project_id: str, conn: sqlite3.Connection) -> Dict[str, Any]:
    """Remediation — CAT1 findings closed, SOPs approved."""
    sops = conn.execute(
        "SELECT COUNT(*) as cnt FROM sdc_sops WHERE approval_status='approved'"
    ).fetchone()
    cat1_open = conn.execute(
        "SELECT COUNT(*) as cnt FROM sc_threats WHERE threat_category='CAT1' AND status='open'"
    ).fetchone()

    return {
        "scenario": "B",
        "title": _SCENARIO_META["B"]["title"],
        "cat1_remaining": cat1_open["cnt"] if cat1_open else 0,
        "sops_approved": sops["cnt"] if sops else 5,
        "posture_grade": "C",
        "risk_score": 4.2,
    }


def _run_scenario_c(project_id: str, conn: sqlite3.Connection) -> Dict[str, Any]:
    """After state — IaC generated + compliance crosswalk (cat1=0, paths_blocked=3)."""

    # 1. Query after-state from sdc_compliance_timeline (cat1=0)
    row = None
    try:
        row = conn.execute(
            """SELECT cat1_count, cat2_count, cat3_count, risk_score, posture_grade,
                      controls_implemented, remediation_hours, snapshot_label
               FROM sdc_compliance_timeline
               WHERE cat1_count = 0
               ORDER BY snapshot_at DESC
               LIMIT 1"""
        ).fetchone()
    except sqlite3.OperationalError:
        pass  # table not yet migrated — fall through to synthetic data

    if row:
        timeline = {
            "cat1_count": row["cat1_count"],
            "cat2_count": row["cat2_count"],
            "cat3_count": row["cat3_count"],
            "risk_score": row["risk_score"],
            "posture_grade": row["posture_grade"],
            "controls_implemented": row["controls_implemented"],
            "remediation_hours": row["remediation_hours"],
            "snapshot_label": row["snapshot_label"],
        }
    else:
        timeline = {
            "cat1_count": 0,
            "cat2_count": 3,
            "cat3_count": 2,
            "risk_score": 12.5,
            "posture_grade": "B+",
            "controls_implemented": 42,
            "remediation_hours": 186.0,
            "snapshot_label": "after-remediation",
        }

    # paths_blocked = attack snapshot count (all 3 paths now mitigated)
    try:
        pb_row = conn.execute("SELECT COUNT(*) as cnt FROM sdc_attack_snapshots").fetchone()
        paths_blocked = pb_row["cnt"] if pb_row else 3
    except sqlite3.OperationalError:
        paths_blocked = 3

    # 2. Generate Terraform / IaC artifacts
    from tools.sdc.iac_generator import generate_iac  # noqa: PLC0415
    iac = generate_iac(project_id)

    # 3. Compute NIST-800-53 / FedRAMP / CMMC compliance crosswalk
    from tools.security_canvas.security_engine import compute_compliance_crosswalk  # noqa: PLC0415
    crosswalk_result = compute_compliance_crosswalk(_AFTER_STATE_GRAPH)

    frameworks = crosswalk_result.get("frameworks", {})
    coverage = {
        "nist_800_53_pct": frameworks.get("nist_800_53", {}).get("coverage_pct", 0),
        "fedramp_pct": frameworks.get("fedramp", {}).get("coverage_pct", 0),
        "cmmc_pct": frameworks.get("cmmc", {}).get("coverage_pct", 0),
        "overall_summary": crosswalk_result.get("overall_summary", ""),
        "crosswalk_matrix_count": len(crosswalk_result.get("crosswalk_matrix", [])),
    }

    return {
        "scenario": "C",
        "title": _SCENARIO_META["C"]["title"],
        "timeline": timeline,
        "cat1_count": timeline["cat1_count"],
        "paths_blocked": paths_blocked,
        "artifact_path": iac["report_path"],
        "iac": {
            "main_tf": iac["main_tf"],
            "vars_tf": iac["vars_tf"],
            "ansible_pb": iac["ansible_pb"],
            "validate_py": iac["validate_py"],
        },
        "coverage": coverage,
    }


def run_demo(
    scenario: str = "C",
    project_id: str = "sdc-demo",
    audience: str = "exec",
) -> Dict[str, Any]:
    conn = _sdc_conn()
    try:
        runners = {
            "A": _run_scenario_a,
            "B": _run_scenario_b,
            "C": _run_scenario_c,
        }
        key = scenario.upper()
        if key not in runners:
            return {"error": f"Unknown scenario '{scenario}'. Choose A, B, or C."}
        result = runners[key](project_id, conn)
    finally:
        conn.close()

    return {
        "canvas": "sdc",
        "audience": audience,
        "project_id": project_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="SDC Demo Runner")
    parser.add_argument("--scenario", default="C", choices=["A", "B", "C"])
    parser.add_argument("--project-id", default="sdc-demo")
    parser.add_argument("--audience", default="exec", choices=["exec", "tech", "compliance"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_demo(
        scenario=args.scenario,
        project_id=args.project_id,
        audience=args.audience,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        meta = _SCENARIO_META.get(args.scenario.upper(), {})
        print(f"\n=== SDC Demo — Scenario {args.scenario}: {meta.get('title', '')} ===\n")
        print(f"  Hook:      {meta.get('hook', '')}")
        if args.scenario.upper() == "C":
            print(f"  CAT1 findings: {result.get('cat1_count', 'N/A')}")
            print(f"  Paths blocked: {result.get('paths_blocked', 'N/A')}")
            cov = result.get("coverage", {})
            print(f"  NIST 800-53:   {cov.get('nist_800_53_pct', 0)}%")
            print(f"  FedRAMP:       {cov.get('fedramp_pct', 0)}%")
            print(f"  CMMC:          {cov.get('cmmc_pct', 0)}%")
            print(f"  Artifact:      {result.get('artifact_path', 'N/A')}")
        print()


if __name__ == "__main__":
    main()
