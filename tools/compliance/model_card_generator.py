#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Model Card Generator — OMB M-26-04 compliant model documentation.

Generates model cards per OMB M-26-04 / Google Model Cards template
(Mitchell et al., 2019). Pulls data from ai_bom, ai_telemetry,
shap_attributions, and xai_assessments tables.

ADR D308: Model cards follow Google Model Cards format.

Usage:
    python tools/compliance/model_card_generator.py --project-id proj-123 --model-name claude-sonnet --json
    python tools/compliance/model_card_generator.py --project-id proj-123 --list --json
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
        CREATE TABLE IF NOT EXISTS model_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            model_name TEXT NOT NULL,
            card_data TEXT NOT NULL,
            card_hash TEXT,
            version INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, model_name)
        );
        CREATE INDEX IF NOT EXISTS idx_model_cards_project
            ON model_cards(project_id);
    """)
    conn.commit()


def _get_ai_bom_data(conn: sqlite3.Connection, project_id: str, model_name: str) -> Dict:
    """Pull model info from AI BOM if available."""
    try:
        row = conn.execute(
            """SELECT * FROM ai_bom WHERE project_id = ?
               AND (component_name LIKE ? OR component_name LIKE ?)
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, f"%{model_name}%", f"%{model_name.replace('-', '_')}%"),
        ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _get_telemetry_summary(conn: sqlite3.Connection, project_id: str) -> Dict:
    """Pull AI telemetry summary."""
    try:
        row = conn.execute(
            """SELECT COUNT(*) as total_calls,
                      COUNT(DISTINCT model) as models_used
               FROM ai_telemetry WHERE project_id = ?""",
            (project_id,),
        ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _get_xai_data(conn: sqlite3.Connection, project_id: str) -> Dict:
    """Pull XAI assessment data."""
    try:
        row = conn.execute(
            """SELECT * FROM xai_assessments WHERE project_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def generate_model_card(
    project_id: str,
    model_name: str,
    model_type: str = "Large Language Model",
    intended_use: str = "",
    out_of_scope: str = "",
    training_data: str = "Third-party pre-trained model. Training data managed by model provider.",
    ethical_considerations: str = "",
    caveats: str = "",
    db_path: Path = DB_PATH,
) -> Dict:
    """Generate a model card and store in DB."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        now = datetime.now(timezone.utc).isoformat()

        # Gather data from existing tables
        bom_data = _get_ai_bom_data(conn, project_id, model_name)
        telemetry = _get_telemetry_summary(conn, project_id)
        xai_data = _get_xai_data(conn, project_id)

        card = {
            "model_card_version": "1.0",
            "generated_at": now,
            "classification": "CUI // SP-CTI",
            "model_details": {
                "name": model_name,
                "type": model_type,
                "version": bom_data.get("component_version", "latest"),
                "provider": bom_data.get("vendor", "Unknown"),
                "description": bom_data.get("description", f"{model_name} AI model"),
                "license": bom_data.get("license", "See provider terms"),
            },
            "intended_use": {
                "primary_use_cases": intended_use or "AI-assisted software development, compliance assessment, code generation",
                "out_of_scope_uses": out_of_scope or "Autonomous decision-making without human oversight, safety-critical systems without human review",
                "users": "Federal agency developers, compliance officers, system administrators",
            },
            "factors": {
                "relevant_factors": [
                    "Input language and domain specificity",
                    "Prompt complexity and length",
                    "Classification level of processed data",
                    "Regulatory context (DoD, FedRAMP, CMMC)",
                ],
                "evaluation_factors": [
                    "Code correctness and security",
                    "Compliance artifact accuracy",
                    "Confabulation rate",
                    "Response consistency",
                ],
            },
            "metrics": {
                "performance_metrics": {
                    "total_api_calls": telemetry.get("total_calls", 0),
                    "distinct_models_used": telemetry.get("models_used", 0),
                },
                "fairness_metrics": "See fairness_assessments table for detailed metrics",
                "explainability": {
                    "xai_assessment_available": bool(xai_data),
                    "shap_analysis_available": "See shap_attributions table",
                },
            },
            "training_data": {
                "description": training_data,
                "known_limitations": "Model training data is managed by the AI provider. ICDEV does not fine-tune models.",
                "data_privacy": "No customer data used for training. All interactions are inference-only.",
            },
            "ethical_considerations": {
                "description": ethical_considerations or "Model is used within ICDEV's compliance framework with human oversight, audit trails, and appeal processes per OMB M-25-21 and M-26-04.",
                "risks": [
                    "Confabulation — mitigated by output validation and human review",
                    "Bias — mitigated by fairness assessments and disparity analysis",
                    "Privacy — mitigated by PII screening and data classification controls",
                ],
                "mitigations": [
                    "Prompt injection detection (5 categories)",
                    "Behavioral drift monitoring",
                    "Agent trust scoring",
                    "MCP tool-level RBAC",
                    "Append-only audit trail",
                ],
            },
            "caveats_and_limitations": {
                "description": caveats or "Model outputs require human review for high-impact decisions. Not suitable for autonomous safety-critical operations.",
                "known_limitations": [
                    "May generate plausible but incorrect information (confabulation)",
                    "Performance varies by domain and prompt quality",
                    "Third-party model — ICDEV cannot guarantee model behavior changes across versions",
                ],
            },
        }

        # Store in DB
        card_json = json.dumps(card, indent=2)
        card_hash = hashlib.sha256(card_json.encode()).hexdigest()[:16]

        # Get current version
        existing = conn.execute(
            "SELECT version FROM model_cards WHERE project_id = ? AND model_name = ?",
            (project_id, model_name),
        ).fetchone()
        version = (existing["version"] + 1) if existing else 1

        conn.execute(
            """INSERT OR REPLACE INTO model_cards
               (project_id, model_name, card_data, card_hash, version, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, model_name, card_json, card_hash, version, now),
        )
        conn.commit()

        # Audit trail
        try:
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details, classification)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    project_id, "model_card_generated",
                    "icdev-compliance-engine",
                    f"Generated model card for {model_name}",
                    json.dumps({"model_name": model_name, "version": version, "hash": card_hash}),
                    "CUI",
                ),
            )
            conn.commit()
        except Exception:
            pass

        return {
            "status": "success",
            "project_id": project_id,
            "model_name": model_name,
            "version": version,
            "card_hash": card_hash,
            "card": card,
        }
    finally:
        conn.close()


def list_model_cards(project_id: str, db_path: Path = DB_PATH) -> Dict:
    """List all model cards for a project."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT model_name, version, card_hash, created_at FROM model_cards WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        return {
            "project_id": project_id,
            "model_cards": [dict(r) for r in rows],
            "count": len(rows),
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Model Card Generator (OMB M-26-04)")
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--model-name", help="Model name (e.g., claude-sonnet)")
    parser.add_argument("--model-type", default="Large Language Model", help="Model type")
    parser.add_argument("--list", action="store_true", help="List all model cards")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")
    args = parser.parse_args()

    db = args.db_path or DB_PATH

    try:
        if args.list:
            result = list_model_cards(args.project_id, db)
        elif args.model_name:
            result = generate_model_card(args.project_id, args.model_name, args.model_type, db_path=db)
        else:
            print("ERROR: --model-name or --list required", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if args.list:
                print(f"Model Cards for {args.project_id}: {result['count']}")
                for mc in result["model_cards"]:
                    print(f"  - {mc['model_name']} v{mc['version']} ({mc['created_at']})")
            else:
                print(f"Model card generated: {result['model_name']} v{result['version']}")
                print(f"  Hash: {result['card_hash']}")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
