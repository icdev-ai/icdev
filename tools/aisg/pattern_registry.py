#!/usr/bin/env python3
# CUI // SP-CTI
"""AISG Pattern Registry — reusable AI workflow patterns for non-AI teams.

Provides list_patterns(), get_pattern(), deploy_pattern(), and
_seed_builtins() to populate the 4 built-in Gov/DoD starter patterns.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.db.storage import get_connection

BUILTIN_PATTERNS: list[dict] = [
    {
        "id": "pat-doc-qa-001",
        "name": "Document Q&A Assistant",
        "category": "document_qa",
        "description": (
            "Ingest PDF/Word policy documents and answer natural-language questions "
            "against them. No coding required — configure sources, run indexer, deploy."
        ),
        "goal_template": "goals/patterns/document_qa.md",
        "canvas_type": "DDC",
        "complexity": "beginner",
        "use_case_tags": ["policy", "HR", "legal", "knowledge-base"],
        "is_builtin": True,
    },
    {
        "id": "pat-proc-intel-001",
        "name": "Procurement Intelligence Analyzer",
        "category": "procurement",
        "description": (
            "Monitor SAM.gov solicitations, score them against agency requirements, "
            "and surface high-fit opportunities automatically."
        ),
        "goal_template": "goals/patterns/procurement_intel.md",
        "canvas_type": "BDC",
        "complexity": "intermediate",
        "use_case_tags": ["SAM.gov", "contracting", "acquisition", "BD"],
        "is_builtin": True,
    },
    {
        "id": "pat-threat-triage-001",
        "name": "Threat Report Triage",
        "category": "threat_triage",
        "description": (
            "Automatically classify incoming threat reports by severity and MITRE ATT&CK "
            "technique, route to the correct analyst queue, and generate a 1-page brief."
        ),
        "goal_template": "goals/patterns/threat_triage.md",
        "canvas_type": "SDC",
        "complexity": "intermediate",
        "use_case_tags": ["CTI", "SOC", "MITRE", "incident-response"],
        "is_builtin": True,
    },
    {
        "id": "pat-compliance-001",
        "name": "Compliance Evidence Collector",
        "category": "compliance",
        "description": (
            "Map system artifacts (screenshots, logs, configs) to NIST 800-53 controls "
            "and auto-generate SSP evidence packages for assessors."
        ),
        "goal_template": "goals/patterns/compliance_evidence.md",
        "canvas_type": "NDC",
        "complexity": "advanced",
        "use_case_tags": ["NIST", "FedRAMP", "CMMC", "RMF", "SSP"],
        "is_builtin": True,
    },
]


def _row_to_dict(row) -> dict:
    d = dict(row)
    tags = d.get("use_case_tags")
    if isinstance(tags, str):
        try:
            d["use_case_tags"] = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            d["use_case_tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    return d


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
        if complexity:
            sql += " AND complexity = %s"
            params.append(complexity)
        sql += " ORDER BY is_builtin DESC, deploy_count DESC, name ASC"
        c.execute(sql, params)
        rows = c.fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_pattern(pattern_id: str) -> dict | None:
    """Return a single pattern by ID, or None if not found."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM aisg_patterns WHERE id = %s", (pattern_id,))
        row = c.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def deploy_pattern(pattern_id: str, deployed_by: str = "user") -> dict:
    """Increment deploy_count and return the updated pattern.

    Returns {"error": ...} if pattern not found.
    """
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "UPDATE aisg_patterns SET deploy_count = deploy_count + 1, updated_at = %s WHERE id = %s",
            (datetime.now(timezone.utc).isoformat(), pattern_id),
        )
        conn.commit()
        if c.rowcount == 0:
            return {"error": f"Pattern {pattern_id} not found"}
        c.execute("SELECT * FROM aisg_patterns WHERE id = %s", (pattern_id,))
        row = c.fetchone()
        return _row_to_dict(row) if row else {"error": "Deploy succeeded but pattern vanished"}
    finally:
        conn.close()


def _seed_builtins() -> int:
    """Insert built-in patterns if they don't already exist. Returns count inserted."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    try:
        c = conn.cursor()
        for p in BUILTIN_PATTERNS:
            c.execute("SELECT id FROM aisg_patterns WHERE id = %s", (p["id"],))
            if c.fetchone():
                continue
            tags_val = json.dumps(p["use_case_tags"])
            c.execute(
                """INSERT INTO aisg_patterns
                   (id, name, category, description, goal_template, canvas_type,
                    complexity, use_case_tags, deploy_count, is_builtin, created_by,
                    created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    p["id"], p["name"], p["category"], p["description"],
                    p["goal_template"], p["canvas_type"], p["complexity"],
                    tags_val, 0, True, "system", now, now,
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


if __name__ == "__main__":
    n = _seed_builtins()
    print(f"Seeded {n} built-in pattern(s).")
    patterns = list_patterns()
    print(f"Total patterns: {len(patterns)}")
    for pat in patterns:
        print(f"  [{pat['complexity']:12s}] {pat['name']} ({pat['category']})")
