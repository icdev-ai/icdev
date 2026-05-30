#!/usr/bin/env python3
# CUI // SP-CTI
"""Mission Canvas Demo Seed -- populates mission_canvas.db with realistic mission demo data.

Tables seeded:
  mission_designs (4), mission_templates (3), mission_assessments (4), mission_audit (8),
  mission_versions (6), mission_portfolios (4), mission_twin_snapshots (4),
  mission_evidence (6), mission_narratives (4), mission_security_posture (4)

Usage:
    python tools/db/seeds/seed_mission_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_mission_demo.py --verify --json
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
        from tools.mission_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "mission_canvas.db"
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
        "mission_security_posture", "mission_narratives", "mission_evidence",
        "mission_twin_snapshots", "mission_portfolios", "mission_versions",
        "mission_audit", "mission_assessments", "mission_templates", "mission_designs",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_DESIGNS = [
    {
        "id": _uid(),
        "name": "Satellite Ground System (SGS)",
        "description": "Next-gen satellite ground station architecture with autonomous TT&C, multi-mission scheduling, and NISAR data processing pipeline.",
        "design_type": "operational",
        "graph_json": json.dumps({"nodes": [{"id": "antenna", "type": "ground_station", "label": "12m Antenna Array"}, {"id": "ttc", "type": "subsystem", "label": "TT&C Controller"}, {"id": "pds", "type": "subsystem", "label": "Product Delivery System"}], "edges": [{"id": "e1", "source": "antenna", "target": "ttc"}, {"id": "e2", "source": "ttc", "target": "pds"}]}),
        "template_id": "tpl-satellite-ground",
    },
    {
        "id": _uid(),
        "name": "C2 Platform (Command & Control)",
        "description": "Joint C2 platform with JADC2 interoperability, cross-domain guard, and real-time COP federation.",
        "design_type": "operational",
        "graph_json": json.dumps({"nodes": [{"id": "cop", "type": "display", "label": "Common Operating Picture"}, {"id": "gateway", "type": "guard", "label": "Cross-Domain Gateway"}, {"id": "cmd", "type": "controller", "label": "Command Node"}], "edges": [{"id": "e1", "source": "cmd", "target": "gateway"}, {"id": "e2", "source": "gateway", "target": "cop"}]}),
        "template_id": "tpl-c2-platform",
    },
    {
        "id": _uid(),
        "name": "ISR Pipeline (Intelligence)",
        "description": "Multi-INT ISR pipeline fusing SIGINT, GEOINT, and HUMINT with AI/ML analytics and dissemination.",
        "design_type": "intelligence",
        "graph_json": json.dumps({"nodes": [{"id": "sigint", "type": "sensor", "label": "SIGINT Collector"}, {"id": "fusion", "type": "processor", "label": "Multi-INT Fusion"}, {"id": "dissem", "type": "delivery", "label": "Dissemination"}], "edges": [{"id": "e1", "source": "sigint", "target": "fusion"}, {"id": "e2", "source": "fusion", "target": "dissem"}]}),
        "template_id": "tpl-isr-pipeline",
    },
    {
        "id": _uid(),
        "name": "Cyber Mission Defense Team",
        "description": "CMF-style cyber protection team with DCO/OCO capabilities, hunt-forward operations, and integrated threat intel.",
        "design_type": "security",
        "graph_json": json.dumps({"nodes": [{"id": "sensor", "type": "ids", "label": "Network Sensor"}, {"id": "hunt", "type": "team", "label": "Hunt Team"}, {"id": "response", "type": "playbook", "label": "Incident Response"}], "edges": [{"id": "e1", "source": "sensor", "target": "hunt"}, {"id": "e2", "source": "hunt", "target": "response"}]}),
        "template_id": "tpl-cyber-mission",
    },
]

_TEMPLATES = [
    {"id": "tpl-satellite-ground", "name": "Satellite Ground Station", "category": "space", "description": "Standard satellite ground station template", "graph_json": json.dumps({"nodes": [], "edges": []}), "tags": json.dumps(["space", "ttc"])},
    {"id": "tpl-c2-platform", "name": "C2 Platform", "category": "defense", "description": "Command and control platform template", "graph_json": json.dumps({"nodes": [], "edges": []}), "tags": json.dumps(["c2", "jadc2"])},
    {"id": "tpl-isr-pipeline", "name": "ISR Pipeline", "category": "intelligence", "description": "Intelligence collection and processing template", "graph_json": json.dumps({"nodes": [], "edges": []}), "tags": json.dumps(["isr", "fusion"])},
]


def seed_designs(conn) -> int:
    sql = """INSERT OR IGNORE INTO mission_designs (
        id, name, description, design_type, graph_json, template_id, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _DESIGNS:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["description"], row["design_type"],
            row["graph_json"], row["template_id"], "CUI", _ts(count * 3), _ts(count * 3),
        ))
        count += 1
    return count


def seed_templates(conn) -> int:
    sql = """INSERT OR IGNORE INTO mission_templates (
        id, name, category, description, graph_json, tags
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    for row in _TEMPLATES:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["category"], row["description"],
            row["graph_json"], row["tags"],
        ))
        count += 1
    return count


def seed_assessments(conn) -> int:
    sql = """INSERT OR IGNORE INTO mission_assessments (
        id, design_id, assessment_type, findings_json, score, grade, readiness_score, created_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for i, d in enumerate(_DESIGNS):
        _safe_execute(conn, sql, (
            _uid(), d["id"], "full",
            json.dumps([{"rule": "R-01", "status": "pass"}, {"rule": "R-02", "status": "fail"}]),
            round(random.uniform(60, 95), 1),
            random.choice(["A", "B", "C"]),
            round(random.uniform(0.5, 1.0), 2),
            _ts(i * 4),
        ))
        count += 1
    return count


def seed_audit(conn) -> int:
    sql = """INSERT OR IGNORE INTO mission_audit (
        design_id, actor, action, detail, classification, created_at
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    actions = [
        ("design_created", "Design created"),
        ("assessment_run", "Assessment completed"),
        ("twin_snapshot", "Digital twin snapshot taken"),
        ("narrative_generated", "Narrative generated"),
        ("evidence_added", "Evidence package added"),
        ("security_posture_updated", "Security posture updated"),
    ]
    for i in range(8):
        d = _DESIGNS[i % len(_DESIGNS)]
        action, detail = actions[i % len(actions)]
        _safe_execute(conn, sql, (
            d["id"], random.choice(["admin", "sme", "operator"]),
            action, detail, "CUI // SP-CTI", _ts(i * 2),
        ))
        count += 1
    return count


def seed_versions(conn) -> int:
    sql = """INSERT OR IGNORE INTO mission_versions (
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


def seed_portfolios(conn) -> int:
    sql = """INSERT OR IGNORE INTO mission_portfolios (
        id, design_id, name, description, portfolio_type, metrics_json, status, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    portfolios = [
        ("Space Mission Portfolio", "All space-related mission designs", "program"),
        ("JADC2 Portfolio", "C2 and joint interoperability programs", "program"),
        ("ISR Collection Portfolio", "Intelligence collection missions", "program"),
        ("Cyber Defense Portfolio", "Defensive cyber operations", "program"),
    ]
    for i, (name, desc, ptype) in enumerate(portfolios):
        d = _DESIGNS[i % len(_DESIGNS)]
        _safe_execute(conn, sql, (
            _uid(), d["id"], name, desc, ptype,
            json.dumps({"budget_m": round(random.uniform(10, 500), 1), "personnel": random.randint(50, 5000)}),
            "active", _ts(i * 4), _ts(i * 4),
        ))
        count += 1
    return count


def seed_twin_snapshots(conn) -> int:
    sql = """INSERT OR IGNORE INTO mission_twin_snapshots (
        id, design_id, snapshot_name, snapshot_json, status, classification, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for i in range(4):
        d = _DESIGNS[i % len(_DESIGNS)]
        _safe_execute(conn, sql, (
            _uid(), d["id"], f"Snapshot {i+1}",
            json.dumps({"health": random.choice(["green", "amber", "red"]), "timestamp": _ts(i * 6)}),
            "current" if i == 0 else "archived", "CUI", _ts(i * 6),
        ))
        count += 1
    return count


def seed_evidence(conn) -> int:
    sql = """INSERT OR IGNORE INTO mission_evidence (
        id, design_id, evidence_type, title, content, source, provenance_json, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for i in range(6):
        d = _DESIGNS[i % len(_DESIGNS)]
        _safe_execute(conn, sql, (
            _uid(), d["id"], random.choice(["document", "image", "log", "report"]),
            f"Evidence Package {i+1}", f"Content for evidence {i+1}",
            random.choice(["satellite_telemetry", "c2_log", "sigint_feed", "audit_trail"]),
            json.dumps({"chain": ["source-a", "processor-b"], "integrity": "sha256:abc123"}),
            "CUI", _ts(i * 3),
        ))
        count += 1
    return count


def seed_narratives(conn) -> int:
    sql = """INSERT OR IGNORE INTO mission_narratives (
        id, design_id, narrative_type, title, content, source_evidence_ids, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for i in range(4):
        d = _DESIGNS[i % len(_DESIGNS)]
        _safe_execute(conn, sql, (
            _uid(), d["id"], random.choice(["sitrep", "briefing", "after_action"]),
            f"Narrative {i+1}: {d['name']}",
            f"Plain-English summary for {d['name']}. Operational status is nominal with {random.randint(0,5)} open items.",
            json.dumps(["ev-001", "ev-002"]), "CUI", _ts(i * 6),
        ))
        count += 1
    return count


def seed_security_posture(conn) -> int:
    sql = """INSERT OR IGNORE INTO mission_security_posture (
        id, design_id, zta_score, fedramp_status, il_level, findings_json, assessed_at, created_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for i in range(4):
        d = _DESIGNS[i % len(_DESIGNS)]
        _safe_execute(conn, sql, (
            _uid(), d["id"], round(random.uniform(0.6, 0.95), 2),
            random.choice(["not_started", "in_process", "authorized"]),
            random.choice(["IL4", "IL5", "IL6"]),
            json.dumps([{"id": f"F-{j}", "severity": random.choice(["low", "medium", "high"])} for j in range(3)]),
            _ts(i * 8), _ts(i * 8),
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in (
        "mission_designs", "mission_templates", "mission_assessments", "mission_audit",
        "mission_versions", "mission_portfolios", "mission_twin_snapshots",
        "mission_evidence", "mission_narratives", "mission_security_posture",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="Mission Canvas Demo Seed")
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
            "mission_designs": seed_designs(conn),
            "mission_templates": seed_templates(conn),
            "mission_assessments": seed_assessments(conn),
            "mission_audit": seed_audit(conn),
            "mission_versions": seed_versions(conn),
            "mission_portfolios": seed_portfolios(conn),
            "mission_twin_snapshots": seed_twin_snapshots(conn),
            "mission_evidence": seed_evidence(conn),
            "mission_narratives": seed_narratives(conn),
            "mission_security_posture": seed_security_posture(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_mission] Seeded {counts}")
            print(f"[seed_mission] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
