#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""FedRAMP 20x Key Security Indicator (KSI) Generator.

Generates machine-readable KSI evidence artifacts for FedRAMP 20x
continuous authorization. Maps ICDEV evidence (DB records, configs,
scan results) to 61 KSI definitions organized by NIST 800-53 families.

Not a BaseAssessor — KSIs are evidence artifacts, not assessment checks.
Follows cssp_evidence_collector.py pattern (D338).

Usage:
    python tools/compliance/fedramp_ksi_generator.py --project-id proj-123 --all --json
    python tools/compliance/fedramp_ksi_generator.py --project-id proj-123 --ksi-id KSI-AC-01 --json
    python tools/compliance/fedramp_ksi_generator.py --project-id proj-123 --summary --json
    python tools/compliance/fedramp_ksi_generator.py --project-id proj-123 --all --human
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
KSI_SCHEMA_PATH = BASE_DIR / "context" / "compliance" / "fedramp_20x_ksi_schemas.json"


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _count_rows(conn: sqlite3.Connection, table: str, project_id: str) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def _count_rows_no_project(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def _file_exists(relative_path: str) -> bool:
    return (BASE_DIR / relative_path).exists()


def _config_contains(config_path: str, keywords: List[str]) -> bool:
    fp = BASE_DIR / config_path
    if not fp.exists():
        return False
    try:
        content = fp.read_text(encoding="utf-8", errors="ignore").lower()
        return any(kw.lower() in content for kw in keywords)
    except Exception:
        return False


# Evidence collection functions per source type
EVIDENCE_COLLECTORS = {
    "audit_trail": lambda conn, pid: _count_rows(conn, "audit_trail", pid),
    "hook_events": lambda conn, pid: _count_rows(conn, "hook_events", pid),
    "prompt_injection_log": lambda conn, pid: _count_rows(conn, "prompt_injection_log", pid),
    "ai_telemetry": lambda conn, pid: _count_rows(conn, "ai_telemetry", pid),
    "agent_trust_scores": lambda conn, pid: _count_rows(conn, "agent_trust_scores", pid),
    "tool_chain_events": lambda conn, pid: _count_rows(conn, "tool_chain_events", pid),
    "agent_output_violations": lambda conn, pid: _count_rows(conn, "agent_output_violations", pid),
    "ai_bom": lambda conn, pid: _count_rows(conn, "ai_bom", pid),
    "sbom_records": lambda conn, pid: _count_rows(conn, "sbom_records", pid) if _table_exists(conn, "sbom_records") else (1 if _file_exists("tools/compliance/sbom_generator.py") else 0),
    "production_audits": lambda conn, pid: _count_rows(conn, "production_audits", pid),
    "xai_assessments": lambda conn, pid: _count_rows(conn, "xai_assessments", pid),
    "shap_attributions": lambda conn, pid: _count_rows(conn, "shap_attributions", pid),
    "owasp_asi_assessments": lambda conn, pid: _count_rows(conn, "owasp_asi_assessments", pid),
    "atlas_assessments": lambda conn, pid: _count_rows(conn, "atlas_assessments", pid),
    "nist_ai_rmf_assessments": lambda conn, pid: _count_rows(conn, "nist_ai_rmf_assessments", pid),
    "model_cards": lambda conn, pid: _count_rows(conn, "model_cards", pid),
    "system_cards": lambda conn, pid: _count_rows(conn, "system_cards", pid),
    "ai_use_case_inventory": lambda conn, pid: _count_rows(conn, "ai_use_case_inventory", pid),
    "fairness_assessments": lambda conn, pid: _count_rows(conn, "fairness_assessments", pid),
    "gao_ai_assessments": lambda conn, pid: _count_rows(conn, "gao_ai_assessments", pid),
    "confabulation_checks": lambda conn, pid: _count_rows(conn, "confabulation_checks", pid),
    "ai_oversight_plans": lambda conn, pid: _count_rows(conn, "ai_oversight_plans", pid),
    "ai_caio_registry": lambda conn, pid: _count_rows_no_project(conn, "ai_caio_registry") if _table_exists(conn, "ai_caio_registry") else 0,
    "ai_ethics_reviews": lambda conn, pid: _count_rows(conn, "ai_ethics_reviews", pid),
    "ai_incident_log": lambda conn, pid: _count_rows(conn, "ai_incident_log", pid),
    "remediation_audit_log": lambda conn, pid: _count_rows(conn, "remediation_audit_log", pid),
    "heartbeat_checks": lambda conn, pid: _count_rows_no_project(conn, "heartbeat_checks") if _table_exists(conn, "heartbeat_checks") else 0,
    "auto_resolution_log": lambda conn, pid: _count_rows_no_project(conn, "auto_resolution_log") if _table_exists(conn, "auto_resolution_log") else 0,
    "dashboard_users": lambda conn, pid: _count_rows_no_project(conn, "dashboard_users") if _table_exists(conn, "dashboard_users") else 0,
    "dashboard_api_keys": lambda conn, pid: _count_rows_no_project(conn, "dashboard_api_keys") if _table_exists(conn, "dashboard_api_keys") else 0,
    "dashboard_auth_log": lambda conn, pid: _count_rows_no_project(conn, "dashboard_auth_log") if _table_exists(conn, "dashboard_auth_log") else 0,
    "remote_user_bindings": lambda conn, pid: _count_rows_no_project(conn, "remote_user_bindings") if _table_exists(conn, "remote_user_bindings") else 0,
    "remote_command_log": lambda conn, pid: _count_rows_no_project(conn, "remote_command_log") if _table_exists(conn, "remote_command_log") else 0,
    "devsecops_profiles": lambda conn, pid: _count_rows(conn, "devsecops_profiles", pid),
    "marketplace_scan_results": lambda conn, pid: _count_rows_no_project(conn, "marketplace_scan_results") if _table_exists(conn, "marketplace_scan_results") else 0,
    "scrm_assessments": lambda conn, pid: _count_rows(conn, "scrm_assessments", pid) if _table_exists(conn, "scrm_assessments") else 0,
    # Config-based evidence
    "rbac_config": lambda conn, pid: 1 if _file_exists("args/owasp_agentic_config.yaml") else 0,
    "mcp_tool_authorizer": lambda conn, pid: 1 if _file_exists("tools/security/mcp_tool_authorizer.py") else 0,
    "session_config": lambda conn, pid: 1 if _config_contains("args/security_gates.yaml", ["session"]) else 0,
    "network_policies": lambda conn, pid: 1 if _file_exists("k8s/network-policies.yaml") else 0,
    "append_only_tables": lambda conn, pid: 1 if _file_exists(".claude/hooks/pre_tool_use.py") else 0,
    "pre_tool_use_hook": lambda conn, pid: 1 if _file_exists(".claude/hooks/pre_tool_use.py") else 0,
    "hmac_config": lambda conn, pid: 1 if _config_contains("args/observability_config.yaml", ["hmac"]) else 0,
    "secrets_config": lambda conn, pid: 1 if _config_contains("args/cloud_config.yaml", ["secrets"]) else 0,
    "api_key_management": lambda conn, pid: 1 if _file_exists("tools/dashboard/auth.py") else 0,
    "agent_config": lambda conn, pid: 1 if _file_exists("args/agent_config.yaml") else 0,
    "a2a_agent_cards": lambda conn, pid: 1 if _file_exists("args/agent_config.yaml") else 0,
    "a2a_tls_config": lambda conn, pid: 1 if _config_contains("args/agent_config.yaml", ["tls", "mtls"]) else 0,
    "classification_config": lambda conn, pid: 1 if _file_exists("args/classification_config.yaml") else 0,
    "security_gates": lambda conn, pid: 1 if _file_exists("args/security_gates.yaml") else 0,
    "code_pattern_config": lambda conn, pid: 1 if _file_exists("args/code_pattern_config.yaml") else 0,
    "icdev_yaml": lambda conn, pid: 1 if _file_exists("icdev.yaml") or _file_exists("args/project_defaults.yaml") else 0,
    "cloud_config": lambda conn, pid: 1 if _file_exists("args/cloud_config.yaml") else 0,
    "resilience_config": lambda conn, pid: 1 if _file_exists("args/resilience_config.yaml") else 0,
    "hpa_config": lambda conn, pid: 1 if _file_exists("k8s/hpa.yaml") else 0,
    "rate_limiter": lambda conn, pid: 1 if _file_exists("tools/saas/rate_limiter.py") else 0,
    "encryption_config": lambda conn, pid: 1 if _config_contains("args/cloud_config.yaml", ["encrypt", "kms"]) else 0,
    "k8s_manifests": lambda conn, pid: 1 if _file_exists("k8s") else 0,
    "attestation_config": lambda conn, pid: 1 if _file_exists("tools/devsecops/attestation_manager.py") else 0,
    "ir_plan": lambda conn, pid: 1 if _file_exists("tools/compliance/incident_response_plan.py") else 0,
    "sbd_assessments": lambda conn, pid: _count_rows(conn, "sbd_assessments", pid) if _table_exists(conn, "sbd_assessments") else 0,
    "mosa_assessments": lambda conn, pid: _count_rows(conn, "mosa_assessments", pid) if _table_exists(conn, "mosa_assessments") else 0,
    "pipeline_config": lambda conn, pid: 1 if _file_exists("tools/ci/pipeline_config_generator.py") else 0,
    "test_results": lambda conn, pid: 1 if _file_exists("tests") else 0,
    "bdd_results": lambda conn, pid: 1 if _file_exists("features") else 0,
    "e2e_results": lambda conn, pid: 1 if _file_exists(".claude/commands/e2e") else 0,
    "sast_results": lambda conn, pid: 1 if _file_exists("tools/security/sast_runner.py") else 0,
    "dependency_audit": lambda conn, pid: 1 if _file_exists("tools/security/dependency_auditor.py") else 0,
    "code_pattern_scan": lambda conn, pid: 1 if _file_exists("tools/security/code_pattern_scanner.py") else 0,
    "behavioral_drift": lambda conn, pid: 1 if _config_contains("args/owasp_agentic_config.yaml", ["drift"]) else 0,
    "claude_dir_validator": lambda conn, pid: 1 if _file_exists("tools/testing/claude_dir_validator.py") else 0,
    "schema_migrations": lambda conn, pid: 1 if _table_exists(conn, "schema_migrations") else 0,
    "poam_records": lambda conn, pid: _count_rows(conn, "poam_items", pid) if _table_exists(conn, "poam_items") else 0,
    "vendor_records": lambda conn, pid: _count_rows(conn, "supply_chain_vendors", pid) if _table_exists(conn, "supply_chain_vendors") else 0,
    "vulnerability_records": lambda conn, pid: _count_rows(conn, "vulnerability_records", pid) if _table_exists(conn, "vulnerability_records") else 0,
    "cato_monitor": lambda conn, pid: 1 if _file_exists("tools/compliance/cato_monitor.py") else 0,
}


def _load_ksi_schemas() -> Dict:
    return json.loads(KSI_SCHEMA_PATH.read_text(encoding="utf-8"))


def _determine_maturity(ksi: Dict, evidence_counts: Dict[str, int]) -> str:
    sources = ksi.get("evidence_sources", [])
    if not sources:
        return "none"
    available = sum(1 for s in sources if evidence_counts.get(s, 0) > 0)
    ratio = available / len(sources)
    if ratio >= 0.8:
        return "advanced"
    elif ratio >= 0.5:
        return "intermediate"
    elif ratio > 0:
        return "basic"
    return "none"


def generate_ksi(project_id: str, ksi_id: str, db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Generate evidence for a single KSI."""
    schemas = _load_ksi_schemas()
    conn = _get_connection(db_path) if db_path.exists() else None

    try:
        for family in schemas["ksi_families"]:
            for ksi in family["ksis"]:
                if ksi["ksi_id"] == ksi_id:
                    evidence = {}
                    for source in ksi.get("evidence_sources", []):
                        collector = EVIDENCE_COLLECTORS.get(source)
                        if collector and conn:
                            evidence[source] = collector(conn, project_id)
                        elif collector:
                            evidence[source] = collector(None, project_id)
                        else:
                            evidence[source] = 0

                    maturity = _determine_maturity(ksi, evidence)
                    maturity_desc = ksi.get("maturity_levels", {}).get(maturity, "No evidence")

                    return {
                        "ksi_id": ksi_id,
                        "title": ksi["title"],
                        "family": family["family_id"],
                        "family_name": family["family_name"],
                        "nist_controls": ksi["nist_controls"],
                        "maturity_level": maturity,
                        "maturity_description": maturity_desc,
                        "evidence": evidence,
                        "evidence_available": sum(1 for v in evidence.values() if v > 0),
                        "evidence_total": len(evidence),
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "project_id": project_id,
                    }

        return {"error": f"KSI {ksi_id} not found", "ksi_id": ksi_id}
    finally:
        if conn:
            conn.close()


def generate_all_ksis(project_id: str, db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Generate evidence for all 61 KSIs."""
    schemas = _load_ksi_schemas()
    conn = _get_connection(db_path) if db_path.exists() else None

    try:
        # Collect all evidence once
        evidence_cache: Dict[str, int] = {}
        for source, collector in EVIDENCE_COLLECTORS.items():
            try:
                if conn:
                    evidence_cache[source] = collector(conn, project_id)
                else:
                    evidence_cache[source] = collector(None, project_id)
            except Exception:
                evidence_cache[source] = 0

        results = []
        maturity_counts = {"advanced": 0, "intermediate": 0, "basic": 0, "none": 0}

        for family in schemas["ksi_families"]:
            for ksi in family["ksis"]:
                evidence = {s: evidence_cache.get(s, 0) for s in ksi.get("evidence_sources", [])}
                maturity = _determine_maturity(ksi, evidence)
                maturity_counts[maturity] += 1
                maturity_desc = ksi.get("maturity_levels", {}).get(maturity, "No evidence")

                results.append({
                    "ksi_id": ksi["ksi_id"],
                    "title": ksi["title"],
                    "family": family["family_id"],
                    "nist_controls": ksi["nist_controls"],
                    "maturity_level": maturity,
                    "maturity_description": maturity_desc,
                    "evidence_available": sum(1 for v in evidence.values() if v > 0),
                    "evidence_total": len(evidence),
                })

        total_ksis = len(results)
        covered = total_ksis - maturity_counts["none"]
        coverage_pct = round((covered / total_ksis * 100) if total_ksis > 0 else 0, 1)

        return {
            "project_id": project_id,
            "total_ksis": total_ksis,
            "coverage_pct": coverage_pct,
            "maturity_summary": maturity_counts,
            "ksis": results,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        if conn:
            conn.close()


def generate_summary(project_id: str, db_path: Path = DB_PATH) -> Dict[str, Any]:
    """Generate a summary report of KSI coverage by family."""
    all_ksis = generate_all_ksis(project_id, db_path)
    family_summary = {}
    for ksi in all_ksis.get("ksis", []):
        fam = ksi["family"]
        if fam not in family_summary:
            family_summary[fam] = {"total": 0, "covered": 0, "advanced": 0, "intermediate": 0, "basic": 0, "none": 0}
        family_summary[fam]["total"] += 1
        family_summary[fam][ksi["maturity_level"]] += 1
        if ksi["maturity_level"] != "none":
            family_summary[fam]["covered"] += 1

    return {
        "project_id": project_id,
        "total_ksis": all_ksis["total_ksis"],
        "coverage_pct": all_ksis["coverage_pct"],
        "maturity_summary": all_ksis["maturity_summary"],
        "family_summary": family_summary,
        "generated_at": all_ksis["generated_at"],
    }


def main():
    parser = argparse.ArgumentParser(description="FedRAMP 20x KSI Generator")
    parser.add_argument("--project-id", required=True, help="Project UUID")
    parser.add_argument("--ksi-id", help="Specific KSI to generate (e.g., KSI-AC-01)")
    parser.add_argument("--all", action="store_true", help="Generate all KSIs")
    parser.add_argument("--summary", action="store_true", help="Summary by family")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=DB_PATH, help="Database path")
    args = parser.parse_args()

    if args.ksi_id:
        result = generate_ksi(args.project_id, args.ksi_id, args.db_path)
    elif args.summary:
        result = generate_summary(args.project_id, args.db_path)
    elif args.all:
        result = generate_all_ksis(args.project_id, args.db_path)
    else:
        result = generate_summary(args.project_id, args.db_path)

    if args.human:
        if "ksis" in result:
            print(f"\nFedRAMP 20x KSI Report — {result['project_id']}")
            print(f"Coverage: {result['coverage_pct']}% ({result['total_ksis']} KSIs)")
            ms = result.get("maturity_summary", {})
            print(f"Maturity: Advanced={ms.get('advanced',0)} | Intermediate={ms.get('intermediate',0)} | Basic={ms.get('basic',0)} | None={ms.get('none',0)}")
            print("-" * 70)
            for ksi in result["ksis"]:
                icon = {"advanced": "+", "intermediate": "~", "basic": ".", "none": "-"}.get(ksi["maturity_level"], "?")
                print(f"  [{icon}] {ksi['ksi_id']}: {ksi['title']} ({ksi['maturity_level']})")
        elif "family_summary" in result:
            print(f"\nFedRAMP 20x KSI Summary — {result['project_id']}")
            print(f"Coverage: {result['coverage_pct']}%")
            for fam, data in result["family_summary"].items():
                pct = round(data["covered"] / data["total"] * 100) if data["total"] > 0 else 0
                print(f"  {fam}: {data['covered']}/{data['total']} ({pct}%)")
        else:
            print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
