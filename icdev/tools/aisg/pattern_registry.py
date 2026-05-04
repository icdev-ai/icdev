#!/usr/bin/env python3
# CUI // SP-CTI
"""AISG Pattern Registry — reusable AI workflow patterns for non-AI teams.

Uses the existing aisg_patterns table schema:
  id, name, category, description, use_case, deploy_config (JSONB/text),
  created_at, tags (JSON text/array), is_builtin, updated_at

Provides list_patterns(), get_pattern(), deploy_pattern(), _seed_builtins().
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.db.storage import get_connection

# Bandit B608: All SQL queries use parameterized placeholders (%s) - verified 2026-05-03

BUILTIN_PATTERNS: list[dict] = [
    {
        "id": "aisg-doc-qa-001",
        "name": "Document Q&A Assistant",
        "category": "document_qa",
        "description": (
            "Ingest PDF/Word policy documents and answer natural-language questions "
            "against them. No coding required — configure sources, run indexer, deploy."
        ),
        "use_case": "Enable non-AI teams to build a Q&A knowledge base over policy docs in hours.",
        "deploy_config": {
            "goal_template": "goals/patterns/document_qa.md",
            "canvas_type": "DDC",
            "complexity": "beginner",
            "task_type": "build",
        },
        "tags": ["policy", "HR", "legal", "knowledge-base", "document_qa"],
        "is_builtin": True,
    },
    {
        "id": "aisg-proc-intel-001",
        "name": "Procurement Intelligence Analyzer",
        "category": "procurement",
        "description": (
            "Monitor SAM.gov solicitations, score them against agency requirements, "
            "and surface high-fit opportunities automatically."
        ),
        "use_case": "BD teams get auto-scored opportunity alerts without manual SAM.gov monitoring.",
        "deploy_config": {
            "goal_template": "goals/patterns/procurement_intel.md",
            "canvas_type": "BDC",
            "complexity": "intermediate",
            "task_type": "build",
        },
        "tags": ["SAM.gov", "contracting", "acquisition", "BD", "procurement"],
        "is_builtin": True,
    },
    {
        "id": "aisg-threat-triage-001",
        "name": "Threat Report Triage",
        "category": "threat_triage",
        "description": (
            "Automatically classify incoming threat reports by severity and MITRE ATT&CK "
            "technique, route to the correct analyst queue, and generate a 1-page brief."
        ),
        "use_case": "SOC teams triage 10x more reports without additional headcount.",
        "deploy_config": {
            "goal_template": "goals/patterns/threat_triage.md",
            "canvas_type": "SDC",
            "complexity": "intermediate",
            "task_type": "build",
        },
        "tags": ["CTI", "SOC", "MITRE", "incident-response", "threat_triage"],
        "is_builtin": True,
    },
    {
        "id": "aisg-compliance-001",
        "name": "Compliance Evidence Collector",
        "category": "compliance_evidence",
        "description": (
            "Map system artifacts (screenshots, logs, configs) to NIST 800-53 controls "
            "and auto-generate SSP evidence packages for assessors."
        ),
        "use_case": "Cut evidence collection from weeks to hours for FedRAMP/CMMC assessments.",
        "deploy_config": {
            "goal_template": "goals/patterns/compliance_evidence.md",
            "canvas_type": "NDC",
            "complexity": "advanced",
            "task_type": "build",
        },
        "tags": ["NIST", "FedRAMP", "CMMC", "RMF", "SSP", "compliance"],
        "is_builtin": True,
    },
]


def _normalize(row: dict) -> dict:
    """Normalize a DB row: parse tags JSON, extract complexity/canvas_type from deploy_config."""
    tags = row.get("tags")
    if isinstance(tags, str):
        try:
            row["tags"] = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            row["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    elif tags is None:
        row["tags"] = []

    deploy = row.get("deploy_config")
    if isinstance(deploy, str):
        try:
            deploy = json.loads(deploy)
        except (json.JSONDecodeError, TypeError):
            deploy = {}
    if isinstance(deploy, dict):
        row["complexity"] = deploy.get("complexity", "intermediate")
        row["canvas_type"] = deploy.get("canvas_type", "")
        row["goal_template"] = deploy.get("goal_template", "")
    else:
        row["complexity"] = "intermediate"
        row["canvas_type"] = ""
        row["goal_template"] = ""
    return row


def list_patterns(category: str | None = None, complexity: str | None = None) -> list[dict]:
    """Return all patterns, optionally filtered by category and/or complexity."""
    conn = get_connection()
    try:
        c = conn.cursor()
        sql = "SELECT * FROM aisg_patterns WHERE 1=1"
        params: list = []
        if category:
            sql += " AND category = %s"
            params.append(category)
        sql += " ORDER BY is_builtin DESC, name ASC"
        c.execute(sql, params)
        rows = [_normalize(dict(r)) for r in c.fetchall()]
        if complexity:
            rows = [r for r in rows if r.get("complexity") == complexity]
        return rows
    finally:
        conn.close()


def get_pattern(pattern_id: str) -> dict | None:
    """Return a single pattern by ID, or None if not found."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM aisg_patterns WHERE id = %s", (pattern_id,))
        row = c.fetchone()
        return _normalize(dict(row)) if row else None
    finally:
        conn.close()


def deploy_pattern(pattern_id: str, deployed_by: str = "user") -> dict:
    """Increment deploy count (stored in deploy_config) and return the updated pattern."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM aisg_patterns WHERE id = %s", (pattern_id,))
        row = c.fetchone()
        if not row:
            return {"error": f"Pattern {pattern_id} not found"}
        p = _normalize(dict(row))
        deploy_cfg = p.get("deploy_config") or {}
        if isinstance(deploy_cfg, str):
            try:
                deploy_cfg = json.loads(deploy_cfg)
            except (json.JSONDecodeError, TypeError):
                deploy_cfg = {}
        deploy_cfg["deploy_count"] = deploy_cfg.get("deploy_count", 0) + 1
        now = datetime.now(timezone.utc).isoformat()
        c.execute(
            "UPDATE aisg_patterns SET deploy_config = %s, updated_at = %s WHERE id = %s",
            (json.dumps(deploy_cfg), now, pattern_id),
        )
        conn.commit()
        p["deploy_config"] = deploy_cfg
        p["deploy_count"] = deploy_cfg["deploy_count"]
        return p
    finally:
        conn.close()


def _seed_builtins() -> int:
    """Insert built-in AISG patterns if they don't already exist. Returns count inserted."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    try:
        c = conn.cursor()
        for p in BUILTIN_PATTERNS:
            c.execute("SELECT id FROM aisg_patterns WHERE id = %s", (p["id"],))
            if c.fetchone():
                continue
            c.execute(
                """INSERT INTO aisg_patterns
                   (id, name, category, description, use_case, deploy_config, tags,
                    is_builtin, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    p["id"], p["name"], p["category"], p["description"],
                    p.get("use_case", ""),
                    json.dumps(p.get("deploy_config", {})),
                    json.dumps(p.get("tags", [])),
                    1, now, now,
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = _seed_builtins()
    print(f"Seeded {n} built-in AISG pattern(s).")
    patterns = list_patterns()
    print(f"Total patterns: {len(patterns)}")
    for pat in patterns:
        print(f"  [{pat.get('complexity','?'):12s}] {pat['name']} ({pat.get('category', '?')})")
