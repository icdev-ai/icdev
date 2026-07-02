#!/usr/bin/env python3
# CUI // SP-CTI
"""QDC Demo Seed -- populates qdc_canvas.db with realistic quality design demo data.

Tables seeded:
  qdc_designs (5), qdc_assessments (5), qdc_audit (10), qdc_versions (8),
  qdc_gate_results (20), qdc_uqs_history (10), qdc_cross_canvas_links (8),
  qdc_collab_sessions (4)

Usage:
    python tools/db/seeds/seed_qdc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_qdc_demo.py --verify --json
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
        from tools.qdc_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "qdc_canvas.db"
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
        "qdc_collab_sessions", "qdc_cross_canvas_links", "qdc_uqs_history",
        "qdc_gate_results", "qdc_versions", "qdc_audit", "qdc_assessments",
        "qdc_designs",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_DESIGN_IDS = {f"qdc-design-{i:03d}": _uid() for i in range(5)}

_QDC_DESIGNS = [
    {
        "id": _DESIGN_IDS["qdc-design-000"],
        "name": "FedRAMP Moderate CI/CD Pipeline",
        "description": "Quality gate pipeline for FedRAMP Moderate ATO with SAST, SCA, unit tests, E2E tests, code review, and STIG compliance checks.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "src-repo", "label": "Code Repository", "x": 50, "y": 50},
                {"id": "n2", "type": "src-pipeline", "label": "CI/CD Pipeline", "x": 50, "y": 150},
                {"id": "n3", "type": "gate-sast", "label": "SAST Gate", "x": 250, "y": 30},
                {"id": "n4", "type": "gate-sca", "label": "SCA Gate", "x": 250, "y": 100},
                {"id": "n5", "type": "gate-unit", "label": "Unit Test Gate", "x": 250, "y": 170},
                {"id": "n6", "type": "gate-e2e", "label": "E2E Test Gate", "x": 250, "y": 240},
                {"id": "n7", "type": "gate-review", "label": "Code Review Gate", "x": 250, "y": 310},
                {"id": "n8", "type": "tgt-stig", "label": "STIG Compliance", "x": 450, "y": 80},
                {"id": "n9", "type": "con-compliance", "label": "Compliance Report", "x": 450, "y": 180},
                {"id": "n10", "type": "con-oscal", "label": "OSCAL Artifact", "x": 450, "y": 260},
                {"id": "n11", "type": "tgt-fedramp-mod", "label": "FedRAMP Moderate", "x": 650, "y": 150},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n3", "label": "scan"},
                {"id": "e2", "source": "n1", "target": "n4", "label": "audit"},
                {"id": "e3", "source": "n2", "target": "n5", "label": "run"},
                {"id": "e4", "source": "n2", "target": "n6", "label": "run"},
                {"id": "e5", "source": "n1", "target": "n7", "label": "review"},
                {"id": "e6", "source": "n3", "target": "n9"},
                {"id": "e7", "source": "n4", "target": "n9"},
                {"id": "e8", "source": "n5", "target": "n9"},
                {"id": "e9", "source": "n8", "target": "n9"},
                {"id": "e10", "source": "n9", "target": "n10"},
                {"id": "e11", "source": "n10", "target": "n11"},
            ],
        }),
        "template_id": "tpl-fedramp-mod-qa",
    },
    {
        "id": _DESIGN_IDS["qdc-design-001"],
        "name": "CMMC Level 2 Quality Gates",
        "description": "CMMC Level 2 quality control for CUI protection with SAST, SCA, unit tests, code review, and secret scanning gates.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "src-repo", "label": "Code Repository", "x": 50, "y": 80},
                {"id": "n2", "type": "gate-sast", "label": "SAST Gate", "x": 250, "y": 30},
                {"id": "n3", "type": "gate-sca", "label": "SCA Gate", "x": 250, "y": 100},
                {"id": "n4", "type": "gate-unit", "label": "Unit Test", "x": 250, "y": 170},
                {"id": "n5", "type": "gate-review", "label": "Code Review", "x": 250, "y": 240},
                {"id": "n6", "type": "gate-secret", "label": "Secret Scan", "x": 250, "y": 310},
                {"id": "n7", "type": "tgt-cmmc-l2", "label": "CMMC Level 2", "x": 450, "y": 150},
                {"id": "n8", "type": "con-compliance", "label": "Compliance Report", "x": 450, "y": 260},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n2"},
                {"id": "e2", "source": "n1", "target": "n3"},
                {"id": "e3", "source": "n1", "target": "n4"},
                {"id": "e4", "source": "n1", "target": "n5"},
                {"id": "e5", "source": "n1", "target": "n6"},
                {"id": "e6", "source": "n2", "target": "n7"},
                {"id": "e7", "source": "n7", "target": "n8"},
            ],
        }),
        "template_id": "tpl-cmmc-l2-qa",
    },
    {
        "id": _DESIGN_IDS["qdc-design-002"],
        "name": "Continuous ATO (cATO) Evidence Pipeline",
        "description": "Continuous ATO quality evidence pipeline with real-time monitoring, automated evidence collection, and UQS trending.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "src-pipeline", "label": "CI/CD Pipeline", "x": 50, "y": 100},
                {"id": "n2", "type": "src-health", "label": "Health Monitor", "x": 50, "y": 250},
                {"id": "n3", "type": "gate-sast", "label": "SAST Gate", "x": 250, "y": 30},
                {"id": "n4", "type": "gate-unit", "label": "Unit Test", "x": 250, "y": 100},
                {"id": "n5", "type": "gate-e2e", "label": "E2E Test", "x": 250, "y": 170},
                {"id": "n6", "type": "gate-review", "label": "Code Review", "x": 250, "y": 240},
                {"id": "n7", "type": "src-observability", "label": "Observability", "x": 250, "y": 330},
                {"id": "n8", "type": "con-cato", "label": "cATO Evidence", "x": 450, "y": 100},
                {"id": "n9", "type": "con-uqs", "label": "UQS Dashboard", "x": 450, "y": 200},
                {"id": "n10", "type": "con-trend", "label": "Trend Report", "x": 450, "y": 300},
                {"id": "n11", "type": "tgt-cato", "label": "cATO Continuous", "x": 650, "y": 180},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n3"},
                {"id": "e2", "source": "n1", "target": "n4"},
                {"id": "e3", "source": "n1", "target": "n5"},
                {"id": "e4", "source": "n1", "target": "n6"},
                {"id": "e5", "source": "n2", "target": "n7"},
                {"id": "e6", "source": "n3", "target": "n8"},
                {"id": "e7", "source": "n4", "target": "n8"},
                {"id": "e8", "source": "n8", "target": "n9"},
                {"id": "e9", "source": "n7", "target": "n10"},
                {"id": "e10", "source": "n9", "target": "n11"},
            ],
        }),
        "template_id": "tpl-cato-continuous",
    },
    {
        "id": _DESIGN_IDS["qdc-design-003"],
        "name": "AI-Generated Code Quality Assurance",
        "description": "Quality gates for AI-generated code with AI generation, SAST, human review, and test coverage validation.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "src-repo", "label": "AI Gen", "x": 50, "y": 50},
                {"id": "n2", "type": "gate-sast", "label": "SAST", "x": 200, "y": 50},
                {"id": "n3", "type": "gate-review", "label": "Review", "x": 350, "y": 50},
                {"id": "n4", "type": "gate-unit", "label": "Coverage", "x": 500, "y": 50},
                {"id": "n5", "type": "con-uqs", "label": "UQS", "x": 650, "y": 50},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n2"},
                {"id": "e2", "source": "n2", "target": "n3"},
                {"id": "e3", "source": "n3", "target": "n4"},
                {"id": "e4", "source": "n4", "target": "n5"},
            ],
        }),
        "template_id": "tpl-ai-code-assurance",
    },
    {
        "id": _DESIGN_IDS["qdc-design-004"],
        "name": "Cross-Canvas Quality Aggregation Hub",
        "description": "Central quality hub aggregating scores from all ICDEV canvases with UQS computation and compliance reporting.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "xc-idc", "label": "IDC", "x": 50, "y": 20},
                {"id": "n2", "type": "xc-sdc", "label": "SDC", "x": 50, "y": 80},
                {"id": "n3", "type": "xc-bdc", "label": "BDC", "x": 50, "y": 140},
                {"id": "n4", "type": "xc-pdc", "label": "PDC", "x": 50, "y": 200},
                {"id": "n5", "type": "xc-odc", "label": "ODC", "x": 50, "y": 260},
                {"id": "n6", "type": "xc-ddc", "label": "DDC", "x": 50, "y": 320},
                {"id": "n7", "type": "xc-ndc", "label": "NDC", "x": 50, "y": 380},
                {"id": "n8", "type": "con-uqs", "label": "UQS", "x": 300, "y": 200},
                {"id": "n9", "type": "con-compliance", "label": "Compliance", "x": 500, "y": 200},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n8"},
                {"id": "e2", "source": "n2", "target": "n8"},
                {"id": "e3", "source": "n3", "target": "n8"},
                {"id": "e4", "source": "n4", "target": "n8"},
                {"id": "e5", "source": "n5", "target": "n8"},
                {"id": "e6", "source": "n6", "target": "n8"},
                {"id": "e7", "source": "n7", "target": "n8"},
                {"id": "e8", "source": "n8", "target": "n9"},
            ],
        }),
        "template_id": "tpl-cross-canvas-hub",
    },
]

_ASSESSMENTS = []
for i, design in enumerate(_QDC_DESIGNS):
    score = random.uniform(75.0, 98.0)
    uqs = random.uniform(70.0, 95.0)
    _ASSESSMENTS.append({
        "id": _uid(),
        "design_id": design["id"],
        "assessment_type": random.choice(["compliance", "security", "coverage", "performance"]),
        "findings_json": json.dumps([
            {"severity": "high", "check": "test_coverage", "finding": "Coverage below 80% threshold"},
            {"severity": "medium", "check": "sast_gate", "finding": "CAT3 finding in auth module"},
            {"severity": "low", "check": "e2e_latency", "finding": "E2E test latency > 30s"},
        ]),
        "score": round(score, 1),
        "uqs_score": round(uqs, 1),
        "uqs_breakdown": json.dumps({
            "security": round(random.uniform(70, 95), 1),
            "coverage": round(random.uniform(65, 90), 1),
            "compliance": round(random.uniform(75, 98), 1),
            "performance": round(random.uniform(60, 88), 1),
            "reliability": round(random.uniform(70, 92), 1),
        }),
        "sa11_mapping": json.dumps({"AC-2": "partial", "AU-6": "full", "CM-7": "full", "RA-5": "partial"}),
    })

_GATE_RESULTS = []
GATE_TYPES = ["sast", "sca", "unit_test", "e2e_test", "code_review", "stig", "secret_scan", "container_scan", "dast", "fuzz_test"]
for i in range(20):
    design_id = _QDC_DESIGNS[i % len(_QDC_DESIGNS)]["id"]
    gate_type = GATE_TYPES[i % len(GATE_TYPES)]
    status = random.choice(["pass", "pass", "pass", "fail", "skip"])
    _GATE_RESULTS.append({
        "id": _uid(),
        "design_id": design_id,
        "gate_id": gate_type,
        "sa11_control": random.choice(["CM-7", "RA-5", "SI-3", "AC-2", "AU-6", "SC-28"]),
        "status": status,
        "evidence_json": json.dumps({"findings": random.randint(0, 5), "severity": random.choice(["CAT1", "CAT2", "CAT3"])}),
        "oscal_artifact": json.dumps({"type": "assessment-results", "href": f"/oscal/{gate_type}.json"}),
    })

_UQS_HISTORY = []
for i in range(10):
    design_id = _QDC_DESIGNS[i % len(_QDC_DESIGNS)]["id"]
    _UQS_HISTORY.append({
        "id": _uid(),
        "design_id": design_id,
        "uqs_score": round(random.uniform(70, 95), 1),
        "dimension_scores": json.dumps({
            "security": round(random.uniform(70, 95), 1),
            "coverage": round(random.uniform(65, 90), 1),
            "compliance": round(random.uniform(75, 98), 1),
            "performance": round(random.uniform(60, 88), 1),
            "reliability": round(random.uniform(70, 92), 1),
        }),
    })

_CROSS_CANVAS_LINKS = []
CANVASES = ["boundary", "infra", "observability", "data", "network", "security"]
for i in range(8):
    _CROSS_CANVAS_LINKS.append({
        "id": _uid(),
        "design_id": _QDC_DESIGNS[i % len(_QDC_DESIGNS)]["id"],
        "source_canvas": CANVASES[i % len(CANVASES)],
        "source_design_id": _uid(),
        "quality_score": round(random.uniform(0.5, 1.0), 2),
    })


def seed_designs(conn) -> int:
    sql = """INSERT OR IGNORE INTO qdc_designs (
        id, name, description, graph_json, template_id, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _QDC_DESIGNS:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["description"], row["graph_json"],
            row["template_id"], "CUI", _ts(count * 3), _ts(count * 3),
        ))
        count += 1
    return count


def seed_assessments(conn) -> int:
    sql = """INSERT OR IGNORE INTO qdc_assessments (
        id, design_id, assessment_type, findings_json, score, uqs_score, uqs_breakdown, sa11_mapping, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _ASSESSMENTS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["assessment_type"],
            row["findings_json"], row["score"], row["uqs_score"],
            row["uqs_breakdown"], row["sa11_mapping"], _ts(count * 4),
        ))
        count += 1
    return count


def seed_audit(conn) -> int:
    sql = """INSERT OR IGNORE INTO qdc_audit (
        id, design_id, \"user\", action, detail, classification, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    actions = [
        ("design_created", "Quality design created"),
        ("assessment_run", "Quality assessment executed"),
        ("gate_passed", "Quality gate passed"),
        ("gate_failed", "Quality gate failed — CAT2 finding"),
        ("uqs_updated", "UQS score recalculated"),
    ]
    for i in range(10):
        design_id = _QDC_DESIGNS[i % len(_QDC_DESIGNS)]["id"]
        action, detail = actions[i % len(actions)]
        _safe_execute(conn, sql, (
            _uid(), design_id, random.choice(["qa-smith", "dev-jones", "system"]),
            action, detail, "CUI // SP-CTI", _ts(i * 2),
        ))
        count += 1
    return count


def seed_versions(conn) -> int:
    sql = """INSERT OR IGNORE INTO qdc_versions (
        id, design_id, version_number, graph_json, change_summary, user_id, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for i in range(8):
        design_id = _QDC_DESIGNS[i % len(_QDC_DESIGNS)]["id"]
        _safe_execute(conn, sql, (
            _uid(), design_id, i + 1, _QDC_DESIGNS[i % len(_QDC_DESIGNS)]["graph_json"],
            f"Version {i+1}: updated quality gates", "system", _ts(i * 5),
        ))
        count += 1
    return count


def seed_gate_results(conn) -> int:
    sql = """INSERT OR IGNORE INTO qdc_gate_results (
        id, design_id, gate_id, sa11_control, status, evidence_json, oscal_artifact, executed_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _GATE_RESULTS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["gate_id"], row["sa11_control"],
            row["status"], row["evidence_json"], row["oscal_artifact"], _ts(count * 2),
        ))
        count += 1
    return count


def seed_uqs_history(conn) -> int:
    sql = """INSERT OR IGNORE INTO qdc_uqs_history (
        id, design_id, uqs_score, dimension_scores, computed_at
    ) VALUES (?,?,?,?,?)"""
    count = 0
    for row in _UQS_HISTORY:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["uqs_score"], row["dimension_scores"], _ts(count * 6),
        ))
        count += 1
    return count


def seed_cross_canvas_links(conn) -> int:
    sql = """INSERT OR IGNORE INTO qdc_cross_canvas_links (
        id, design_id, source_canvas, source_design_id, quality_score, last_synced
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    for row in _CROSS_CANVAS_LINKS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["source_canvas"],
            row["source_design_id"], row["quality_score"], _ts(count * 3),
        ))
        count += 1
    return count


def seed_collab_sessions(conn) -> int:
    sql = """INSERT OR IGNORE INTO qdc_collab_sessions (
        id, design_id, user_id, user_name, color, joined_at, last_seen, is_active
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    users = [
        ("user-001", "Alice Smith", "#3498db"),
        ("user-002", "Bob Jones", "#e74c3c"),
        ("user-003", "Carol Lee", "#2ecc71"),
        ("user-004", "David Brown", "#f39c12"),
    ]
    for i in range(4):
        design_id = _QDC_DESIGNS[i % len(_QDC_DESIGNS)]["id"]
        user_id, user_name, color = users[i]
        _safe_execute(conn, sql, (
            _uid(), design_id, user_id, user_name, color,
            _ts(i * 6), _ts(i * 6 + 2), 1 if random.random() > 0.3 else 0,
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in (
        "qdc_designs", "qdc_assessments", "qdc_audit", "qdc_versions",
        "qdc_gate_results", "qdc_uqs_history", "qdc_cross_canvas_links",
        "qdc_collab_sessions",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="QDC Demo Seed")
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
            "qdc_designs": seed_designs(conn),
            "qdc_assessments": seed_assessments(conn),
            "qdc_audit": seed_audit(conn),
            "qdc_versions": seed_versions(conn),
            "qdc_gate_results": seed_gate_results(conn),
            "qdc_uqs_history": seed_uqs_history(conn),
            "qdc_cross_canvas_links": seed_cross_canvas_links(conn),
            "qdc_collab_sessions": seed_collab_sessions(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_qdc] Seeded {counts}")
            print(f"[seed_qdc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
