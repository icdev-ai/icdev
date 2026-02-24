#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""GAO Evidence Builder — compile audit evidence per GAO-21-519SP.

Pulls evidence from existing ICDEV data (audit_trail, ai_telemetry,
XAI, SHAP, provenance) to build evidence packages aligned with GAO's
4 accountability principles.

ADR D313: Reuses existing ICDEV data — no new data collection needed.

Usage:
    python tools/compliance/gao_evidence_builder.py --project-id proj-123 --json
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _count_table(conn: sqlite3.Connection, table: str, project_id: str) -> int:
    try:
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def _count_table_global(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def build_evidence(project_id: str, db_path: Path = DB_PATH) -> Dict:
    """Build GAO evidence package from existing ICDEV data."""
    conn = _get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()

        evidence = {
            "report_type": "GAO-21-519SP Evidence Package",
            "classification": "CUI // SP-CTI",
            "project_id": project_id,
            "generated_at": now,
            "principles": {},
            "summary": {},
        }

        # ── Governance Evidence ──
        governance_items = []

        audit_count = _count_table(conn, "audit_trail", project_id)
        governance_items.append({
            "requirement": "GAO-GOV-1",
            "evidence_type": "Audit Trail",
            "source_table": "audit_trail",
            "record_count": audit_count,
            "status": "available" if audit_count > 0 else "missing",
        })

        # Agent authority
        agents = _count_table_global(conn, "agents")
        governance_items.append({
            "requirement": "GAO-GOV-1",
            "evidence_type": "Agent Registry & Authority Matrix",
            "source_table": "agents",
            "record_count": agents,
            "status": "available" if agents > 0 else "missing",
        })

        # Risk assessments
        for table, name in [
            ("nist_ai_rmf_assessments", "NIST AI RMF Assessment"),
            ("atlas_assessments", "ATLAS Assessment"),
            ("xai_assessments", "XAI Assessment"),
        ]:
            count = _count_table(conn, table, project_id)
            governance_items.append({
                "requirement": "GAO-GOV-4",
                "evidence_type": name,
                "source_table": table,
                "record_count": count,
                "status": "available" if count > 0 else "missing",
            })

        evidence["principles"]["governance"] = {
            "title": "Governance",
            "items": governance_items,
            "coverage": sum(1 for i in governance_items if i["status"] == "available") / max(len(governance_items), 1) * 100,
        }

        # ── Data Evidence ──
        data_items = []

        ai_bom = _count_table(conn, "ai_bom", project_id)
        data_items.append({
            "requirement": "GAO-DATA-2",
            "evidence_type": "AI Bill of Materials",
            "source_table": "ai_bom",
            "record_count": ai_bom,
            "status": "available" if ai_bom > 0 else "missing",
        })

        prov_entities = _count_table(conn, "prov_entities", project_id)
        data_items.append({
            "requirement": "GAO-DATA-2",
            "evidence_type": "Provenance Records",
            "source_table": "prov_entities",
            "record_count": prov_entities,
            "status": "available" if prov_entities > 0 else "missing",
        })

        # Data classification
        try:
            dc_count = _count_table(conn, "data_classifications", project_id)
            data_items.append({
                "requirement": "GAO-DATA-3",
                "evidence_type": "Data Classifications",
                "source_table": "data_classifications",
                "record_count": dc_count,
                "status": "available" if dc_count > 0 else "missing",
            })
        except Exception:
            data_items.append({
                "requirement": "GAO-DATA-3",
                "evidence_type": "Data Classifications",
                "source_table": "data_classifications",
                "record_count": 0,
                "status": "missing",
            })

        evidence["principles"]["data"] = {
            "title": "Data",
            "items": data_items,
            "coverage": sum(1 for i in data_items if i["status"] == "available") / max(len(data_items), 1) * 100,
        }

        # ── Performance Evidence ──
        perf_items = []

        telemetry = _count_table(conn, "ai_telemetry", project_id)
        perf_items.append({
            "requirement": "GAO-PERF-1",
            "evidence_type": "AI Telemetry",
            "source_table": "ai_telemetry",
            "record_count": telemetry,
            "status": "available" if telemetry > 0 else "missing",
        })

        xai = _count_table(conn, "xai_assessments", project_id)
        perf_items.append({
            "requirement": "GAO-PERF-3",
            "evidence_type": "XAI Assessment",
            "source_table": "xai_assessments",
            "record_count": xai,
            "status": "available" if xai > 0 else "missing",
        })

        shap = _count_table(conn, "shap_attributions", project_id)
        perf_items.append({
            "requirement": "GAO-PERF-3",
            "evidence_type": "SHAP Attributions",
            "source_table": "shap_attributions",
            "record_count": shap,
            "status": "available" if shap > 0 else "missing",
        })

        perf_items.append({
            "requirement": "GAO-PERF-4",
            "evidence_type": "Audit Trail",
            "source_table": "audit_trail",
            "record_count": audit_count,
            "status": "available" if audit_count > 0 else "missing",
        })

        evidence["principles"]["performance"] = {
            "title": "Performance",
            "items": perf_items,
            "coverage": sum(1 for i in perf_items if i["status"] == "available") / max(len(perf_items), 1) * 100,
        }

        # ── Monitoring Evidence ──
        mon_items = []

        mon_items.append({
            "requirement": "GAO-MON-1",
            "evidence_type": "AI Telemetry (Continuous Monitoring)",
            "source_table": "ai_telemetry",
            "record_count": telemetry,
            "status": "available" if telemetry > 0 else "missing",
        })

        # Behavioral drift
        try:
            drift = conn.execute(
                """SELECT COUNT(*) as cnt FROM ai_telemetry
                   WHERE project_id = ? AND event_type = 'drift_detected'""",
                (project_id,),
            ).fetchone()
            drift_count = drift["cnt"] if drift else 0
        except Exception:
            drift_count = 0

        mon_items.append({
            "requirement": "GAO-MON-1",
            "evidence_type": "Behavioral Drift Detection",
            "source_table": "ai_telemetry",
            "record_count": drift_count,
            "status": "available",
        })

        # Trust scores
        trust = _count_table_global(conn, "agent_trust_scores")
        mon_items.append({
            "requirement": "GAO-MON-1",
            "evidence_type": "Agent Trust Scores",
            "source_table": "agent_trust_scores",
            "record_count": trust,
            "status": "available" if trust > 0 else "missing",
        })

        # Prompt injection detection
        pi_count = _count_table(conn, "prompt_injection_log", project_id)
        mon_items.append({
            "requirement": "GAO-MON-3",
            "evidence_type": "Prompt Injection Detection",
            "source_table": "prompt_injection_log",
            "record_count": pi_count,
            "status": "available" if pi_count >= 0 else "missing",
        })

        evidence["principles"]["monitoring"] = {
            "title": "Monitoring",
            "items": mon_items,
            "coverage": sum(1 for i in mon_items if i["status"] == "available") / max(len(mon_items), 1) * 100,
        }

        # Overall summary
        all_items = (
            governance_items + data_items + perf_items + mon_items
        )
        available = sum(1 for i in all_items if i["status"] == "available")
        total = len(all_items)
        overall_coverage = round(available / total * 100, 1) if total > 0 else 0

        evidence["summary"] = {
            "total_evidence_items": total,
            "available": available,
            "missing": total - available,
            "overall_coverage_pct": overall_coverage,
            "principle_coverage": {
                k: round(v["coverage"], 1)
                for k, v in evidence["principles"].items()
            },
        }

        return evidence
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="GAO Evidence Builder (GAO-21-519SP)")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    try:
        result = build_evidence(args.project_id, db)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"GAO Evidence Package for {args.project_id}")
            print(f"  Overall coverage: {result['summary']['overall_coverage_pct']}%")
            for principle, data in result["principles"].items():
                print(f"  {data['title']}: {round(data['coverage'], 1)}%")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
