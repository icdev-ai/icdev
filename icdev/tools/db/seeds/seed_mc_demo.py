#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration Canvas Demo Seed -- populates migration_canvas.db with realistic migration demo data.

Tables seeded (core subset):
  migration_designs (5), mc_assessments (5), mc_audit (10), mc_versions (6),
  mc_wave_plans (8), mc_oracle_predictions (8), mc_runbooks (6), mc_sops (5)

Usage:
    python tools/db/seeds/seed_mc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_mc_demo.py --verify --json
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

random.seed(42)

_NOW = datetime.now(timezone.utc)
_T0 = _NOW - timedelta(hours=72)


def _ts(offset_hours: float = 0.0) -> str:
    return (_T0 + timedelta(hours=offset_hours)).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _get_conn():
    try:
        from tools.migration_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "migration_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn


def _safe_execute(conn, sql, params):
    expected = sql.count("?")
    actual = len(params) if isinstance(params, (list, tuple)) else 1
    if expected != actual:
        raise ValueError(f"Placeholder mismatch: {expected} placeholders but {actual} params")
    conn.execute(sql, params)


def _reset_demo_data(conn) -> None:
    for tbl in (
        "mc_sops", "mc_runbooks", "mc_oracle_predictions", "mc_wave_plans",
        "mc_versions", "mc_audit", "mc_assessments", "migration_designs",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_DESIGNS = [
    {
        "id": _uid(),
        "name": "Analytics App — AWS GovCloud Rehost",
        "description": "Lift-and-shift analytics application from on-prem VMware to AWS GovCloud EC2 with VPC peering and Direct Connect.",
        "migration_type": "application",
        "graph_json": json.dumps({"nodes": [{"id": "app", "type": "app", "label": "Analytics App"}, {"id": "ec2", "type": "vm", "label": "EC2 Instance"}, {"id": "rds", "type": "db", "label": "RDS PostgreSQL"}], "edges": [{"id": "e1", "source": "app", "target": "ec2"}, {"id": "e2", "source": "app", "target": "rds"}]}),
        "template_id": "tpl-app-rehost",
    },
    {
        "id": _uid(),
        "name": "Legacy Oracle to PostgreSQL Refactor",
        "description": "Database refactoring from Oracle 11g to PostgreSQL 15 on Azure Database for PostgreSQL Flexible Server.",
        "migration_type": "database",
        "graph_json": json.dumps({"nodes": [{"id": "oracle", "type": "db", "label": "Oracle 11g"}, {"id": "pg", "type": "db", "label": "PostgreSQL 15"}, {"id": "etl", "type": "pipeline", "label": "ETL Pipeline"}], "edges": [{"id": "e1", "source": "oracle", "target": "etl"}, {"id": "e2", "source": "etl", "target": "pg"}]}),
        "template_id": "tpl-db-refactor",
    },
    {
        "id": _uid(),
        "name": "Network Backbone — MPLS to SD-WAN",
        "description": "Migrate federal agency MPLS WAN to SD-WAN with zero-touch provisioning and TIC 3.0 compliance.",
        "migration_type": "network",
        "graph_json": json.dumps({"nodes": [{"id": "mpls", "type": "circuit", "label": "MPLS Backbone"}, {"id": "sdwan", "type": "gateway", "label": "SD-WAN Edge"}, {"id": "tic", "type": "gateway", "label": "TIC 3.0"}], "edges": [{"id": "e1", "source": "mpls", "target": "sdwan"}, {"id": "e2", "source": "sdwan", "target": "tic"}]}),
        "template_id": "tpl-net-sdwan",
    },
    {
        "id": _uid(),
        "name": "Windows Server 2012 R2 to Windows Server 2022",
        "description": "Server migration with application compatibility assessment, rightsizing, and hypervisor transition.",
        "migration_type": "server",
        "graph_json": json.dumps({"nodes": [{"id": "w12", "type": "server", "label": "Win 2012 R2"}, {"id": "w22", "type": "server", "label": "Win 2022"}, {"id": "hv", "type": "hypervisor", "label": "Hyper-V 2022"}], "edges": [{"id": "e1", "source": "w12", "target": "hv"}, {"id": "e2", "source": "hv", "target": "w22"}]}),
        "template_id": "tpl-server-upgrade",
    },
    {
        "id": _uid(),
        "name": "SAP ECC to SAP S/4HANA on AWS",
        "description": "ERP transformation with Brownfield conversion, data migration, and cutover planning.",
        "migration_type": "application",
        "graph_json": json.dumps({"nodes": [{"id": "ecc", "type": "app", "label": "SAP ECC"}, {"id": "s4", "type": "app", "label": "S/4HANA"}, {"id": "aws", "type": "cloud", "label": "AWS"}], "edges": [{"id": "e1", "source": "ecc", "target": "s4"}, {"id": "e2", "source": "s4", "target": "aws"}]}),
        "template_id": "tpl-sap-brownfield",
    },
]

_STRATEGIES = ["rehost", "refactor", "replatform", "repurchase", "retire", "retain"]
_SEVERITIES = ["info", "low", "medium", "high", "critical"]


def seed_designs(conn) -> int:
    sql = """INSERT OR IGNORE INTO migration_designs (
        id, name, description, migration_type, graph_json, template_id, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _DESIGNS:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["description"], row["migration_type"],
            row["graph_json"], row["template_id"], "CUI", _ts(count * 3), _ts(count * 3),
        ))
        count += 1
    return count


def seed_assessments(conn) -> int:
    sql = """INSERT OR IGNORE INTO mc_assessments (
        id, design_id, assessment_type, findings_json, score, grade, cat1_findings, cat2_findings, cat3_findings, readiness_score, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for i, d in enumerate(_DESIGNS):
        _safe_execute(conn, sql, (
            _uid(), d["id"], "full",
            json.dumps([{"rule": f"R-{j:03d}", "status": random.choice(["pass", "fail", "partial"]), "severity": random.choice(_SEVERITIES)} for j in range(1, 6)]),
            round(random.uniform(60, 95), 1),
            random.choice(["A", "B", "C", "D"]),
            random.randint(0, 2), random.randint(0, 5), random.randint(0, 10),
            round(random.uniform(0.4, 1.0), 2),
            _ts(i * 4),
        ))
        count += 1
    return count


def seed_audit(conn) -> int:
    sql = """INSERT OR IGNORE INTO mc_audit (
        design_id, user, action, detail, classification, created_at
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    actions = [
        ("design_created", "Migration design created"),
        ("assessment_run", "Readiness assessment completed"),
        ("wave_planned", "Migration wave planned"),
        ("oracle_prediction", "Oracle prediction generated"),
        ("runbook_triggered", "Runbook triggered"),
        ("sop_approved", "SOP approved for execution"),
    ]
    for i in range(10):
        d = _DESIGNS[i % len(_DESIGNS)]
        action, detail = actions[i % len(actions)]
        _safe_execute(conn, sql, (
            d["id"], random.choice(["admin", "architect", "pm"]),
            action, detail, "CUI // SP-CTI", _ts(i * 2),
        ))
        count += 1
    return count


def seed_versions(conn) -> int:
    sql = """INSERT OR IGNORE INTO mc_versions (
        id, design_id, version_number, graph_json, change_summary, user_id, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for i in range(6):
        d = _DESIGNS[i % len(_DESIGNS)]
        _safe_execute(conn, sql, (
            _uid(), d["id"], i + 1, d["graph_json"],
            f"Version {i+1} update", "system", _ts(i * 5),
        ))
        count += 1
    return count


def seed_wave_plans(conn) -> int:
    sql = """INSERT OR IGNORE INTO mc_wave_plans (
        id, design_id, wave_number, name, description, node_ids_json, strategy, status,
        estimated_hours, risk_score, start_date, end_date, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for i in range(8):
        d = _DESIGNS[i % len(_DESIGNS)]
        _safe_execute(conn, sql, (
            _uid(), d["id"], i + 1, f"Wave {i+1} — {d['name'][:20]}",
            f"Migration wave {i+1} for {d['name']}",
            json.dumps([f"node-{j}" for j in range(1, 4)]),
            random.choice(_STRATEGIES),
            random.choice(["planned", "in_progress", "completed", "on_hold"]),
            round(random.uniform(40, 400), 1),
            round(random.uniform(0.1, 0.8), 2),
            _ts(i * 24), _ts(i * 24 + 168),
            _ts(i * 4), _ts(i * 4),
        ))
        count += 1
    return count


def seed_oracle_predictions(conn) -> int:
    sql = """INSERT OR IGNORE INTO mc_oracle_predictions (
        id, design_id, lens_id, title, description, confidence, severity, category,
        recommendations, data_json, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for i in range(8):
        d = _DESIGNS[i % len(_DESIGNS)]
        _safe_execute(conn, sql, (
            _uid(), d["id"], "migration",
            f"Prediction {i+1}: {d['name'][:30]}",
            f"Oracle prediction for {d['name']}. Risk of delay: {random.randint(10, 60)}%.",
            round(random.uniform(0.5, 0.95), 2),
            random.choice(_SEVERITIES),
            random.choice(["schedule", "budget", "technical", "compliance"]),
            json.dumps([f"Mitigation {j}" for j in range(1, 4)]),
            json.dumps({"model": "oracle-v2", "features": 12}),
            _ts(i * 3),
        ))
        count += 1
    return count


def seed_runbooks(conn) -> int:
    sql = """INSERT OR IGNORE INTO mc_runbooks (
        id, design_id, title, trigger_event, severity, description, steps_json, owner, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    runbooks = [
        ("Migration Failure — Rollback", "migration_issue", "critical", "Rollback procedures for failed migration wave"),
        ("Database Cutover Runbook", "cutover", "high", "Steps for database cutover window"),
        ("Network Validation Checklist", "validation", "medium", "Post-migration network connectivity validation"),
        ("Server Decommissioning", "decommission", "medium", "Safe decommission of source servers"),
        ("Compliance Evidence Collection", "audit", "high", "Collect evidence for ATO package"),
        ("Performance Baseline Capture", "baseline", "low", "Capture performance baseline post-migration"),
    ]
    for i, (title, trigger, sev, desc) in enumerate(runbooks):
        d = _DESIGNS[i % len(_DESIGNS)]
        _safe_execute(conn, sql, (
            _uid(), d["id"], title, trigger, sev, desc,
            json.dumps([f"Step {j}" for j in range(1, 5)]),
            random.choice(["admin", "sre", "ops"]), "CUI // SP-CTI",
            _ts(i * 4), _ts(i * 4),
        ))
        count += 1
    return count


def seed_sops(conn) -> int:
    sql = """INSERT OR IGNORE INTO mc_sops (
        id, title, sop_type, description, purpose, scope, steps, nist_controls, owner, reviewer,
        approval_status, version, next_review_date, classification, approved_by, approved_at, rejected_reason, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    sops = [
        ("Pre-Migration Readiness Review", "standard", "Checklist for pre-migration readiness gate"),
        ("Data Backup and Validation SOP", "standard", "Backup source data and validate integrity before migration"),
        ("Cutover Communication Plan", "standard", "Stakeholder communication during cutover window"),
        ("Post-Migration Validation SOP", "standard", "Validation steps after migration completion"),
        ("Incident Response During Migration", "emergency", "Procedures for handling incidents during active migration"),
    ]
    for i, (title, stype, desc) in enumerate(sops):
        _safe_execute(conn, sql, (
            _uid(), title, stype, desc,
            "Ensure consistent and safe migration execution",
            "All migration waves",
            json.dumps([f"Step {j}" for j in range(1, 6)]),
            json.dumps(["CM-3", "CM-4", "AU-6"]),
            "Migration Team", "Compliance Officer",
            random.choice(["draft", "approved", "under_review"]), "1.0",
            _ts(720), "CUI // SP-CTI", "", "", "",
            _ts(i * 4), _ts(i * 4),
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in (
        "migration_designs", "mc_assessments", "mc_audit", "mc_versions",
        "mc_wave_plans", "mc_oracle_predictions", "mc_runbooks", "mc_sops",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="Migration Canvas Demo Seed")
    parser.add_argument("--reset", action="store_true", help="Clear existing demo data")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verify", action="store_true", help="Only verify counts")
    args = parser.parse_args()

    conn = _get_conn()
    try:
        if args.verify:
            result = verify(conn)
            print(json.dumps(result, indent=2) if args.json else result)
            return

        if args.reset:
            _reset_demo_data(conn)

        counts = {
            "migration_designs": seed_designs(conn),
            "mc_assessments": seed_assessments(conn),
            "mc_audit": seed_audit(conn),
            "mc_versions": seed_versions(conn),
            "mc_wave_plans": seed_wave_plans(conn),
            "mc_oracle_predictions": seed_oracle_predictions(conn),
            "mc_runbooks": seed_runbooks(conn),
            "mc_sops": seed_sops(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_mc] Seeded {counts}")
            print(f"[seed_mc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
