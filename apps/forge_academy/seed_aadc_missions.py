# CUI // SP-CTI
"""Seed FA DB with the 3 AADC-integrated missions and their steps."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.db.storage import get_connection


_MISSIONS = [
    {
        "slug": "m-secops-05-aadc-threat-model",
        "title": "AADC Threat Modeling",
        "tagline": "Map every MITRE ATLAS technique to your agentic pipeline. Fix before an adversary finds it.",
        "tier": 2,
        "topic": "secops",
        "role_filter": "secops_eng,isso",
        "mission_type": "coding",
        "xp_reward": 400,
        "order_idx": 5,
        "difficulty": "intermediate",
        "estimated_minutes": 40,
        "prereq_slugs_json": json.dumps(["m10-tier1-capstone"]),
        "steps": [
            {
                "step_num": 1,
                "title": "STRIDE Analysis & ATLAS Mapping",
                "step_type": "coding",
                "content_path": "tier2/m-secops-05-aadc-threat-model/steps/step1_aadc_threat_model.md",
                "starter_code_path": "tier2/m-secops-05-aadc-threat-model/steps/step1_starter.py",
                "test_code_path": "tier2/m-secops-05-aadc-threat-model/steps/step1_test.py",
                "config_schema_json": json.dumps({}),
                "xp_partial": 200,
                "skill_tag": "ai-threat-model",
                "hint_allowed": 1,
                "estimated_seconds": 600,
            },
        ],
    },
    {
        "slug": "m-ciso-02-aadc-governance",
        "title": "AI Governance Architecture",
        "tagline": "Design the governance layer that keeps your AI systems accountable. NIST AI RMF compliant.",
        "tier": 2,
        "topic": "governance",
        "role_filter": "ciso",
        "mission_type": "guided",
        "xp_reward": 400,
        "order_idx": 2,
        "difficulty": "intermediate",
        "estimated_minutes": 30,
        "prereq_slugs_json": json.dumps(["m-ciso-01-ai-inventory"]),
        "steps": [
            {
                "step_num": 1,
                "title": "Design a Governance-Compliant Agentic System",
                "step_type": "design",
                "content_path": "tier2/m-ciso-02-aadc-governance/steps/step1_aadc_governance.md",
                "starter_code_path": None,
                "test_code_path": None,
                "config_schema_json": json.dumps({
                    "verifier": "aadc_design_compliant",
                    "required_checks": ["gov-1", "gov-2", "p4-trusted-monitor"],
                    "min_score": 70,
                }),
                "xp_partial": 250,
                "skill_tag": "compliance-design",
                "hint_allowed": 1,
                "estimated_seconds": 900,
            },
        ],
    },
    {
        "slug": "m-issm-02-aadc-ato-design",
        "title": "Compliant AI System Design",
        "tagline": "Design an agentic system that passes OWASP LLM Top 10 — before the STIG auditor arrives.",
        "tier": 2,
        "topic": "ato",
        "role_filter": "issm",
        "mission_type": "guided",
        "xp_reward": 400,
        "order_idx": 2,
        "difficulty": "intermediate",
        "estimated_minutes": 30,
        "prereq_slugs_json": json.dumps(["m-issm-01-ato-acceleration"]),
        "steps": [
            {
                "step_num": 1,
                "title": "Harden a RAG Agent to OWASP LLM Top 10",
                "step_type": "design",
                "content_path": "tier2/m-issm-02-aadc-ato-design/steps/step1_aadc_ato_design.md",
                "starter_code_path": None,
                "test_code_path": None,
                "config_schema_json": json.dumps({
                    "verifier": "aadc_design_compliant",
                    "required_checks": ["llm01", "llm02", "llm04", "llm06", "llm08", "llm10"],
                    "min_score": 75,
                }),
                "xp_partial": 250,
                "skill_tag": "compliance-design",
                "hint_allowed": 1,
                "estimated_seconds": 900,
            },
        ],
    },
]


def seed():
    conn = get_connection()
    seeded = 0
    for m in _MISSIONS:
        steps = m.pop("steps")
        existing = conn.execute(
            "SELECT id FROM fa_missions WHERE slug=%s", (m["slug"],)
        ).fetchone()

        if existing:
            mission_id = existing["id"]
            print(f"  [skip] mission already exists: {m['slug']}")
        else:
            conn.execute(
                """INSERT INTO fa_missions
                   (slug, title, tagline, tier, topic, role_filter, mission_type,
                    xp_reward, prereq_slugs_json, order_idx, difficulty, estimated_minutes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    m["slug"], m["title"], m["tagline"], m["tier"], m["topic"],
                    m["role_filter"], m["mission_type"], m["xp_reward"],
                    m["prereq_slugs_json"], m["order_idx"], m["difficulty"],
                    m["estimated_minutes"],
                ),
            )
            mission_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            print(f"  [+] seeded mission: {m['slug']} (id={mission_id})")
            seeded += 1

        for s in steps:
            ex_step = conn.execute(
                "SELECT id FROM fa_mission_steps WHERE mission_id=%s AND step_num=%s",
                (mission_id, s["step_num"]),
            ).fetchone()
            if ex_step:
                print(f"      [skip] step {s['step_num']} already exists")
                continue
            conn.execute(
                """INSERT INTO fa_mission_steps
                   (mission_id, step_num, title, step_type, content_path,
                    starter_code_path, test_code_path, config_schema_json,
                    xp_partial, skill_tag, hint_allowed, estimated_seconds)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    mission_id, s["step_num"], s["title"], s["step_type"],
                    s["content_path"], s["starter_code_path"], s["test_code_path"],
                    s["config_schema_json"], s["xp_partial"], s["skill_tag"],
                    s["hint_allowed"], s["estimated_seconds"],
                ),
            )
            print(f"      [+] seeded step {s['step_num']}: {s['title']}")

    conn.commit()
    print(f"\nDone — {seeded} new mission(s) seeded.")


if __name__ == "__main__":
    seed()
