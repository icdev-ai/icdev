#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""System Card Generator — AI system-level documentation.

Generates system cards per OMB M-26-04. System cards are broader than
model cards — they cover the entire AI system including all models,
tools, agents, data flows, human oversight, and compliance status.

ADR D309: System cards are ICDEV-specific (covers full agentic system).

Usage:
    python tools/compliance/system_card_generator.py --project-id proj-123 --json
    python tools/compliance/system_card_generator.py --project-id proj-123 --list --json
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS system_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            card_data TEXT NOT NULL,
            card_hash TEXT,
            version INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id)
        );
        CREATE INDEX IF NOT EXISTS idx_system_cards_project
            ON system_cards(project_id);
    """)
    conn.commit()


def _get_project_info(conn: sqlite3.Connection, project_id: str) -> Dict:
    try:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else {"id": project_id, "name": project_id}
    except Exception:
        return {"id": project_id, "name": project_id}


def _get_model_cards(conn: sqlite3.Connection, project_id: str) -> List[Dict]:
    try:
        rows = conn.execute(
            "SELECT model_name, version, card_hash, created_at FROM model_cards WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_agent_info(conn: sqlite3.Connection) -> List[Dict]:
    try:
        rows = conn.execute(
            "SELECT agent_id, agent_type, status FROM agents LIMIT 20"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_compliance_status(conn: sqlite3.Connection, project_id: str) -> List[Dict]:
    try:
        rows = conn.execute(
            """SELECT framework_id, coverage_pct, gate_status, last_assessed
               FROM project_framework_status WHERE project_id = ?""",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def generate_system_card(
    project_id: str,
    system_purpose: str = "",
    db_path: Path = DB_PATH,
) -> Dict:
    """Generate a system card and store in DB."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        now = datetime.now(timezone.utc).isoformat()

        project = _get_project_info(conn, project_id)
        model_cards = _get_model_cards(conn, project_id)
        agents = _get_agent_info(conn)
        compliance = _get_compliance_status(conn, project_id)

        card = {
            "system_card_version": "1.0",
            "generated_at": now,
            "classification": "CUI // SP-CTI",
            "system_overview": {
                "name": project.get("name", project_id),
                "project_id": project_id,
                "purpose": system_purpose or "ICDEV-generated application with AI-assisted development, compliance automation, and continuous monitoring.",
                "system_type": "Agentic AI Development Platform",
                "deployment_status": project.get("status", "active"),
            },
            "ai_components": {
                "models": [
                    {
                        "name": mc["model_name"],
                        "model_card_version": mc["version"],
                        "model_card_hash": mc.get("card_hash", ""),
                    }
                    for mc in model_cards
                ],
                "model_count": len(model_cards),
                "agents": [
                    {
                        "agent_id": a.get("agent_id", ""),
                        "type": a.get("agent_type", ""),
                        "status": a.get("status", ""),
                    }
                    for a in agents[:10]
                ],
                "agent_count": len(agents),
                "tools": "See tools/manifest.md for complete tool inventory",
            },
            "data_flows": {
                "input_sources": [
                    "User prompts via CLI or dashboard",
                    "Requirements documents (SOW, CDD, CONOPS)",
                    "Source code repositories",
                    "Compliance catalogs and frameworks",
                ],
                "processing": [
                    "LLM inference via cloud providers (Bedrock, Azure, GCP, OCI, IBM)",
                    "Deterministic tool execution (GOTCHA framework)",
                    "Compliance assessment via crosswalk engine",
                ],
                "outputs": [
                    "Generated code with CUI markings",
                    "Compliance artifacts (SSP, POAM, STIG, SBOM)",
                    "Model cards and system cards",
                    "Audit trail entries",
                ],
                "data_classification": "CUI // SP-CTI (configurable per impact level)",
            },
            "risk_profile": {
                "impact_classification": "See ai_use_case_inventory for per-component classification",
                "key_risks": [
                    "Confabulation in generated code or compliance artifacts",
                    "Prompt injection via untrusted inputs",
                    "Model drift affecting output quality",
                    "Supply chain risk from third-party AI providers",
                ],
                "mitigations": [
                    "Multi-layer security: prompt injection detection, behavioral drift monitoring, agent trust scoring",
                    "Compliance framework: 28+ frameworks, dual-hub crosswalk, continuous assessment",
                    "Human oversight: agent authority matrix, veto system, appeal process",
                    "Audit: append-only trail, W3C PROV provenance, AgentSHAP attribution",
                ],
            },
            "human_oversight": {
                "oversight_roles": [
                    "Chief AI Officer (CAIO) — overall AI governance",
                    "ISSO — security and compliance oversight",
                    "Program Manager — operational oversight",
                    "Developer — day-to-day AI interaction",
                ],
                "override_capability": "Agent authority matrix with hard/soft vetoes (args/agent_authority.yaml)",
                "appeal_process": "Human review of AI-assisted decisions, independent appeal via governance chain",
            },
            "monitoring": {
                "continuous_monitoring": [
                    "AI telemetry logging (SHA-256 hashed)",
                    "Behavioral drift detection (z-score baseline)",
                    "Performance metrics tracking",
                    "Confabulation detection checks",
                ],
                "alerting": "Configurable thresholds with automated escalation",
                "incident_response": "Integrated with cyber IR plan, AI-specific procedures",
            },
            "compliance_status": {
                "frameworks_assessed": len(compliance),
                "framework_results": compliance,
                "transparency_controls": [
                    "Model cards per OMB M-26-04",
                    "AI use case inventory per OMB M-25-21",
                    "XAI assessments per NIST AI RMF",
                    "GAO evidence packages per GAO-21-519SP",
                ],
            },
        }

        # Store
        card_json = json.dumps(card, indent=2)
        card_hash = hashlib.sha256(card_json.encode()).hexdigest()[:16]

        existing = conn.execute(
            "SELECT version FROM system_cards WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        version = (existing["version"] + 1) if existing else 1

        conn.execute(
            """INSERT OR REPLACE INTO system_cards
               (project_id, card_data, card_hash, version, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (project_id, card_json, card_hash, version, now),
        )
        conn.commit()

        # Audit
        try:
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details, classification)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    project_id, "system_card_generated",
                    "icdev-compliance-engine",
                    "Generated system card",
                    json.dumps({"version": version, "hash": card_hash}),
                    "CUI",
                ),
            )
            conn.commit()
        except Exception:
            pass

        return {
            "status": "success",
            "project_id": project_id,
            "version": version,
            "card_hash": card_hash,
            "card": card,
        }
    finally:
        conn.close()


def list_system_cards(project_id: str, db_path: Path = DB_PATH) -> Dict:
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT version, card_hash, created_at FROM system_cards WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        return {
            "project_id": project_id,
            "system_cards": [dict(r) for r in rows],
            "count": len(rows),
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="System Card Generator (OMB M-26-04)")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--system-purpose", default="", help="System purpose description")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    try:
        if args.list:
            result = list_system_cards(args.project_id, db)
        else:
            result = generate_system_card(args.project_id, args.system_purpose, db)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if args.list:
                print(f"System Cards for {args.project_id}: {result['count']}")
                for sc in result["system_cards"]:
                    print(f"  - v{sc['version']} ({sc['created_at']})")
            else:
                print(f"System card generated: v{result['version']}")
                print(f"  Hash: {result['card_hash']}")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
