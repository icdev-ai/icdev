#!/usr/bin/env python3
# CUI // SP-CTI
"""AISG Demo Seed -- populates aisg_canvas.db with realistic AI strategy demo data.

Tables seeded:
  aisg_roadmaps (3), aisg_sprints (6), aisg_patterns (6), aisg_skills (6),
  aisg_compliance_checks (6), aisg_executive_summaries (3),
  aisg_knowledge_handoffs (4), aisg_roi_tracking (6), aisg_audit (8)

Usage:
    python tools/db/seeds/seed_aisg_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_aisg_demo.py --verify --json
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
        from tools.aisg.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "aisg_canvas.db"
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
        "aisg_roi_tracking", "aisg_knowledge_handoffs", "aisg_executive_summaries",
        "aisg_compliance_checks", "aisg_skills", "aisg_patterns", "aisg_sprints",
        "aisg_audit", "aisg_roadmaps",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_ROADMAPS = [
    {
        "id": _uid(),
        "name": "AI Transformation Roadmap — FY26",
        "description": "Enterprise-wide AI transformation covering governance, infrastructure, talent, and delivery pipelines.",
        "roadmap_type": "transformation",
        "phases_json": json.dumps([
            {"phase": 1, "name": "Foundation", "focus": "Governance & Infrastructure"},
            {"phase": 2, "name": "Pilot", "focus": "Use-case pilots & MLOps"},
            {"phase": 3, "name": "Scale", "focus": "Enterprise deployment & monitoring"},
        ]),
    },
    {
        "id": _uid(),
        "name": "FedRAMP AI Moderate Authorization",
        "description": "Roadmap to achieve FedRAMP Moderate authorization for AI/ML workloads handling CUI data.",
        "roadmap_type": "compliance",
        "phases_json": json.dumps([
            {"phase": 1, "name": "Gap Analysis", "focus": "Control mapping & assessment"},
            {"phase": 2, "name": "Remediation", "focus": "Control implementation & evidence"},
            {"phase": 3, "name": "Assessment", "focus": "3PAO assessment & ATO"},
        ]),
    },
    {
        "id": _uid(),
        "name": "ICDEV Platform Modernization",
        "description": "Modernize ICDEV platform with agentic AI, RAG+KG, and autonomous self-healing capabilities.",
        "roadmap_type": "product",
        "phases_json": json.dumps([
            {"phase": 1, "name": "Research", "focus": "RAG+KG and agentic primitives"},
            {"phase": 2, "name": "Integration", "focus": "MCP servers and multi-agent orchestration"},
            {"phase": 3, "name": "Production", "focus": "Scale to IL6 with full audit trail"},
        ]),
    },
]

_SPRINTS = []
for r_idx, r in enumerate(_ROADMAPS):
    for s in range(2):
        _SPRINTS.append({
            "id": _uid(),
            "roadmap_id": r["id"],
            "sprint_number": s + 1,
            "name": f"Sprint {s+1} — {r['name'][:20]}",
            "description": f"Sprint {s+1} deliverables for {r['name']}",
            "goals_json": json.dumps([f"Goal {g}" for g in range(1, 4)]),
            "status": random.choice(["planned", "in_progress", "completed"]),
            "start_date": _ts((r_idx * 2 + s) * 24),
            "end_date": _ts((r_idx * 2 + s) * 24 + 168),
            "velocity": round(random.uniform(20, 60), 1),
        })

_PATTERNS = [
    {"id": _uid(), "name": "Document Q&A Assistant", "category": "document_qa", "description": "Ingest PDF/Word policy documents and answer natural-language questions against them.", "use_case": "Enable non-AI teams to build a Q&A knowledge base over policy docs in hours.", "deploy_config": json.dumps({"goal_template": "goals/patterns/document_qa.md", "canvas_type": "DDC", "complexity": "beginner"}), "tags": json.dumps(["policy", "HR", "legal", "knowledge-base", "document_qa"]), "is_builtin": 1, "status": "published"},
    {"id": _uid(), "name": "Procurement Intelligence Analyzer", "category": "procurement", "description": "Monitor SAM.gov solicitations, score them against agency requirements, and surface high-fit opportunities automatically.", "use_case": "BD teams get auto-scored opportunity alerts without manual SAM.gov monitoring.", "deploy_config": json.dumps({"goal_template": "goals/patterns/procurement_intel.md", "canvas_type": "BDC", "complexity": "intermediate"}), "tags": json.dumps(["SAM.gov", "contracting", "acquisition", "BD", "procurement"]), "is_builtin": 1, "status": "published"},
    {"id": _uid(), "name": "Threat Report Triage", "category": "threat_triage", "description": "Automatically classify incoming threat reports by severity and MITRE ATT&CK technique, route to the correct analyst queue, and generate a 1-page brief.", "use_case": "SOC teams triage 10x more reports without additional headcount.", "deploy_config": json.dumps({"goal_template": "goals/patterns/threat_triage.md", "canvas_type": "SDC", "complexity": "intermediate"}), "tags": json.dumps(["CTI", "SOC", "MITRE", "incident-response", "threat_triage"]), "is_builtin": 1, "status": "published"},
    {"id": _uid(), "name": "Compliance Evidence Collector", "category": "compliance_evidence", "description": "Map system artifacts (screenshots, logs, configs) to NIST 800-53 controls and auto-generate SSP evidence packages for assessors.", "use_case": "Cut evidence collection from weeks to hours for FedRAMP/CMMC assessments.", "deploy_config": json.dumps({"goal_template": "goals/patterns/compliance_evidence.md", "canvas_type": "NDC", "complexity": "advanced"}), "tags": json.dumps(["NIST", "FedRAMP", "CMMC", "RMF", "SSP", "compliance"]), "is_builtin": 1, "status": "published"},
    {"id": _uid(), "name": "Custom AI Workflow", "category": "custom", "description": "User-defined custom pattern for specialized AI workflow automation.", "use_case": "Tailored pattern for unique organizational AI use cases.", "deploy_config": json.dumps({"goal_template": "goals/patterns/custom.md", "canvas_type": "AADC", "complexity": "intermediate"}), "tags": json.dumps(["custom", "workflow"]), "is_builtin": 0, "status": "draft"},
    {"id": _uid(), "name": "Custom Procurement Scoring", "category": "custom", "description": "Custom scoring algorithm for internal procurement evaluation criteria.", "use_case": "Score internal RFP responses against custom evaluation rubrics.", "deploy_config": json.dumps({"goal_template": "goals/patterns/custom_procurement.md", "canvas_type": "BDC", "complexity": "advanced"}), "tags": json.dumps(["custom", "procurement", "scoring"]), "is_builtin": 0, "status": "draft"},
]

_SKILLS = [
    {"id": _uid(), "name": "Agentic AI Architecture", "skill_type": "technical", "description": "Design and implement agentic AI systems with tool use and multi-agent orchestration.", "proficiency_levels_json": json.dumps(["novice", "practitioner", "expert"]), "gaps_json": json.dumps(["MCP server development", "A2A protocol security"])},
    {"id": _uid(), "name": "FedRAMP Compliance Engineering", "skill_type": "technical", "description": "Implement and maintain FedRAMP-compliant cloud infrastructure and documentation.", "proficiency_levels_json": json.dumps(["associate", "professional", "expert"]), "gaps_json": json.dumps(["ATO package writing", "3PAO liaison"])},
    {"id": _uid(), "name": "Knowledge Graph Engineering", "skill_type": "technical", "description": "Model, ingest, and query RDF/Property Graph knowledge graphs at scale.", "proficiency_levels_json": json.dumps(["beginner", "intermediate", "advanced"]), "gaps_json": json.dumps(["OWL reasoning", "SHACL validation"])},
    {"id": _uid(), "name": "Zero Trust Architecture", "skill_type": "technical", "description": "Design and deploy NIST 800-207 Zero Trust architectures.", "proficiency_levels_json": json.dumps(["foundation", "practitioner", "architect"]), "gaps_json": json.dumps(["IL6 deployment experience", "TIC 3.0 integration"])},
    {"id": _uid(), "name": "MLOps at Scale", "skill_type": "technical", "description": "Build production ML pipelines with monitoring, drift detection, and rollback.", "proficiency_levels_json": json.dumps(["associate", "professional", "expert"]), "gaps_json": json.dumps(["GPU cluster management", "model serving optimization"])},
    {"id": _uid(), "name": "AI Ethics & Governance", "skill_type": "strategic", "description": "Establish AI ethics frameworks, bias detection, and model governance policies.", "proficiency_levels_json": json.dumps(["awareness", "practitioner", "leader"]), "gaps_json": json.dumps(["NIST AI RMF implementation", "EU AI Act mapping"])},
]


def seed_roadmaps(conn) -> int:
    sql = """INSERT OR IGNORE INTO aisg_roadmaps (
        id, name, description, roadmap_type, phases_json, status, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _ROADMAPS:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["description"], row["roadmap_type"],
            row["phases_json"], "active", "CUI", _ts(count * 4), _ts(count * 4),
        ))
        count += 1
    return count


def seed_sprints(conn) -> int:
    sql = """INSERT OR IGNORE INTO aisg_sprints (
        id, roadmap_id, sprint_number, name, description, goals_json, status,
        start_date, end_date, velocity, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _SPRINTS:
        _safe_execute(conn, sql, (
            row["id"], row["roadmap_id"], row["sprint_number"], row["name"],
            row["description"], row["goals_json"], row["status"],
            row["start_date"], row["end_date"], row["velocity"], "CUI",
            _ts(count * 2), _ts(count * 2),
        ))
        count += 1
    return count


def seed_patterns(conn) -> int:
    sql = """INSERT OR IGNORE INTO aisg_patterns (
        id, name, category, description, use_case, deploy_config, created_at, tags, is_builtin, updated_at, classification
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _PATTERNS:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["category"], row["description"], row["use_case"],
            row["deploy_config"], _ts(count * 2), row["tags"], row["is_builtin"],
            _ts(count * 2), "CUI",
        ))
        count += 1
    return count


def seed_skills(conn) -> int:
    sql = """INSERT OR IGNORE INTO aisg_skills (
        id, name, skill_type, description, proficiency_levels_json, gaps_json,
        status, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _SKILLS:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["skill_type"], row["description"],
            row["proficiency_levels_json"], row["gaps_json"], "active", "CUI",
            _ts(count * 2), _ts(count * 2),
        ))
        count += 1
    return count


def seed_compliance_checks(conn) -> int:
    sql = """INSERT OR IGNORE INTO aisg_compliance_checks (
        id, roadmap_id, check_type, regime, findings_json, score, status, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for i in range(6):
        r = _ROADMAPS[i % len(_ROADMAPS)]
        _safe_execute(conn, sql, (
            _uid(), r["id"], random.choice(["nist_ai_rmf", "eu_ai_act", "fedramp"]),
            random.choice(["nist_ai_rmf", "eu_ai_act", "fedramp"]),
            json.dumps([{"control": f"AC-{j}", "status": random.choice(["pass", "fail", "partial"])} for j in range(1, 4)]),
            round(random.uniform(0.5, 1.0), 2),
            random.choice(["pending", "passed", "failed"]), "CUI",
            _ts(i * 3), _ts(i * 3),
        ))
        count += 1
    return count


def seed_executive_summaries(conn) -> int:
    sql = """INSERT OR IGNORE INTO aisg_executive_summaries (
        id, roadmap_id, title, summary, kpis_json, risk_flags_json, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for i in range(3):
        r = _ROADMAPS[i % len(_ROADMAPS)]
        _safe_execute(conn, sql, (
            _uid(), r["id"], f"Executive Summary: {r['name']}",
            f"High-level summary for {r['name']}. Key milestones on track with {random.randint(1,3)} risk flags.",
            json.dumps({"budget_utilization": round(random.uniform(0.6, 0.95), 2), "schedule_variance": round(random.uniform(-10, 5), 1)}),
            json.dumps(["talent_gap", "vendor_delay"]), "CUI", _ts(i * 8),
        ))
        count += 1
    return count


def seed_knowledge_handoffs(conn) -> int:
    sql = """INSERT OR IGNORE INTO aisg_knowledge_handoffs (
        id, roadmap_id, handoff_type, from_entity, to_entity, content_json, status, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    handoffs = [
        ("transition", "Architecture Team", "Implementation Team"),
        ("sprint_close", "Sprint Team", "QA Team"),
        ("deployment", "Dev Team", "Ops Team"),
        ("audit", "Compliance Team", "Auditor"),
    ]
    for i, (htype, fent, tent) in enumerate(handoffs):
        r = _ROADMAPS[i % len(_ROADMAPS)]
        _safe_execute(conn, sql, (
            _uid(), r["id"], htype, fent, tent,
            json.dumps({"artifacts": ["design_doc", "test_plan"], "checklist": ["reviewed", "approved"]}),
            random.choice(["draft", "completed"]), "CUI", _ts(i * 6), _ts(i * 6),
        ))
        count += 1
    return count


def seed_roi_tracking(conn) -> int:
    sql = """INSERT OR IGNORE INTO aisg_roi_tracking (
        id, roadmap_id, action_type, count, minutes_saved, cost_saved_usd,
        period_start, period_end, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    actions = ["self_heal", "compliance_check", "security_scan", "test_run", "evidence_collect", "pattern_deploy"]
    for i in range(6):
        r = _ROADMAPS[i % len(_ROADMAPS)]
        mins = round(random.uniform(30, 5000), 1)
        _safe_execute(conn, sql, (
            _uid(), r["id"], actions[i % len(actions)],
            random.randint(10, 500), mins, round(mins * 2.5, 2),
            _ts(0), _ts(720), "CUI", _ts(i * 4),
        ))
        count += 1
    return count


def seed_audit(conn) -> int:
    sql = """INSERT OR IGNORE INTO aisg_audit (
        roadmap_id, actor, action, detail, classification, created_at
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    actions = [
        ("roadmap_created", "Roadmap created"),
        ("sprint_started", "Sprint started"),
        ("pattern_published", "Pattern published"),
        ("compliance_check_run", "Compliance check executed"),
        ("roi_updated", "ROI metrics updated"),
        ("knowledge_handoff", "Knowledge handoff completed"),
    ]
    for i in range(8):
        r = _ROADMAPS[i % len(_ROADMAPS)]
        action, detail = actions[i % len(actions)]
        _safe_execute(conn, sql, (
            r["id"], random.choice(["admin", "pm", "architect"]),
            action, detail, "CUI // SP-CTI", _ts(i * 2),
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in (
        "aisg_roadmaps", "aisg_sprints", "aisg_patterns", "aisg_skills",
        "aisg_compliance_checks", "aisg_executive_summaries", "aisg_knowledge_handoffs",
        "aisg_roi_tracking", "aisg_audit",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="AISG Demo Seed")
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
            "aisg_roadmaps": seed_roadmaps(conn),
            "aisg_sprints": seed_sprints(conn),
            "aisg_patterns": seed_patterns(conn),
            "aisg_skills": seed_skills(conn),
            "aisg_compliance_checks": seed_compliance_checks(conn),
            "aisg_executive_summaries": seed_executive_summaries(conn),
            "aisg_knowledge_handoffs": seed_knowledge_handoffs(conn),
            "aisg_roi_tracking": seed_roi_tracking(conn),
            "aisg_audit": seed_audit(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_aisg] Seeded {counts}")
            print(f"[seed_aisg] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
