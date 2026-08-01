# CUI // SP-CTI
"""Seed FA DB with 8 AIMC/AADC-integrated Academy missions.

5 AIMC missions (foundation model lifecycle) + 3 AADC fundamentals.
Run once: python apps/forge_academy/seed_aimc_missions.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.db.storage import get_connection


_MISSIONS = [
    # ── AIMC Missions ─────────────────────────────────────────────────────────
    {
        "slug": "m-swe-aiml-01-foundations",
        "title": "Foundation Model Selection",
        "tagline": "Navigate the multi-cloud model catalog. Pick the right model for your IL level.",
        "tier": 2,
        "topic": "aiml",
        "role_filter": "swe_arch,dataops",
        "mission_type": "guided",
        "xp_reward": 350,
        "order_idx": 1,
        "difficulty": "beginner",
        "estimated_minutes": 25,
        "prereq_slugs_json": json.dumps(["m10-tier1-capstone"]),
        "steps": [
            {
                "step_num": 1,
                "title": "Explore the AIMC Model Catalog",
                "step_type": "guided",
                "content_path": "tier2/m-swe-aiml-01-foundations/steps/step1_foundations.md",
                "starter_code_path": "",
                "test_code_path": "",
                "config_schema_json": json.dumps({}),
                "xp_partial": 350,
                "skill_tag": "aimc-model-selection",
                "hint_allowed": 1,
                "estimated_seconds": 900,
            },
        ],
    },
    {
        "slug": "m-swe-aiml-02-adaptation",
        "title": "Adaptation Strategy Design",
        "tagline": "Prompt, RAG, or Fine-tune? Use the decision engine to find out.",
        "tier": 2,
        "topic": "aiml",
        "role_filter": "swe_arch,dataops",
        "mission_type": "coding",
        "xp_reward": 400,
        "order_idx": 2,
        "difficulty": "intermediate",
        "estimated_minutes": 30,
        "prereq_slugs_json": json.dumps(["m-swe-aiml-01-foundations"]),
        "steps": [
            {
                "step_num": 1,
                "title": "Recommend Adaptation Strategies via API",
                "step_type": "coding",
                "content_path": "tier2/m-swe-aiml-02-adaptation/steps/step1_adaptation.md",
                "starter_code_path": "",
                "test_code_path": "",
                "config_schema_json": json.dumps({}),
                "xp_partial": 400,
                "skill_tag": "aimc-adaptation",
                "hint_allowed": 1,
                "estimated_seconds": 1200,
            },
        ],
    },
    {
        "slug": "m-swe-aiml-03-safety-eval",
        "title": "Safety Layers & Evaluation",
        "tagline": "Build the guardrail chain. Achieve ≥ 70 assessment score.",
        "tier": 2,
        "topic": "aiml",
        "role_filter": "swe_arch,secops_eng",
        "mission_type": "coding",
        "xp_reward": 400,
        "order_idx": 3,
        "difficulty": "intermediate",
        "estimated_minutes": 35,
        "prereq_slugs_json": json.dumps(["m-swe-aiml-02-adaptation"]),
        "steps": [
            {
                "step_num": 1,
                "title": "Build Safety + Eval Nodes and Pass Assessment",
                "step_type": "coding",
                "content_path": "tier2/m-swe-aiml-03-safety-eval/steps/step1_safety_eval.md",
                "starter_code_path": "",
                "test_code_path": "",
                "config_schema_json": json.dumps({}),
                "xp_partial": 400,
                "skill_tag": "aimc-safety",
                "hint_allowed": 1,
                "estimated_seconds": 1500,
            },
        ],
    },
    {
        "slug": "m-ciso-aiml-04-governance",
        "title": "Model Governance (DoD RAI + IL)",
        "tagline": "Achieve ≥ 75% governance score across DoD RAI + OMB M-25-21.",
        "tier": 2,
        "topic": "governance",
        "role_filter": "ciso,issm",
        "mission_type": "guided",
        "xp_reward": 450,
        "order_idx": 4,
        "difficulty": "intermediate",
        "estimated_minutes": 35,
        "prereq_slugs_json": json.dumps(["m-swe-aiml-01-foundations"]),
        "steps": [
            {
                "step_num": 1,
                "title": "Build Governance-Complete AIMC Design",
                "step_type": "guided",
                "content_path": "tier2/m-ciso-aiml-04-governance/steps/step1_governance.md",
                "starter_code_path": "",
                "test_code_path": "",
                "config_schema_json": json.dumps({}),
                "xp_partial": 450,
                "skill_tag": "aimc-governance",
                "hint_allowed": 0,
                "estimated_seconds": 1500,
            },
        ],
    },
    {
        "slug": "m-devops-aiml-05-deploy",
        "title": "Deployment Planning (CSP Selection)",
        "tagline": "Select the right inference server for every IL level and provider.",
        "tier": 2,
        "topic": "devops",
        "role_filter": "devops,sre",
        "mission_type": "guided",
        "xp_reward": 350,
        "order_idx": 5,
        "difficulty": "beginner",
        "estimated_minutes": 25,
        "prereq_slugs_json": json.dumps(["m-swe-aiml-01-foundations"]),
        "steps": [
            {
                "step_num": 1,
                "title": "Compare Deployment Plans Across IL Levels",
                "step_type": "coding",
                "content_path": "tier2/m-devops-aiml-05-deploy/steps/step1_deploy.md",
                "starter_code_path": "",
                "test_code_path": "",
                "config_schema_json": json.dumps({}),
                "xp_partial": 350,
                "skill_tag": "aimc-deployment",
                "hint_allowed": 1,
                "estimated_seconds": 900,
            },
        ],
    },
    # ── AADC Fundamental Missions ─────────────────────────────────────────────
    {
        "slug": "m-swe-aadc-06-fundamentals",
        "title": "Agent Topology Fundamentals",
        "tagline": "Single vs multi-agent. Orchestrator + sub-agent. Build and assess your first AADC design.",
        "tier": 2,
        "topic": "swe_arch",
        "role_filter": "swe_arch",
        "mission_type": "guided",
        "xp_reward": 400,
        "order_idx": 6,
        "difficulty": "beginner",
        "estimated_minutes": 30,
        "prereq_slugs_json": json.dumps(["m10-tier1-capstone"]),
        "steps": [
            {
                "step_num": 1,
                "title": "Build a 3-Agent Orchestrator Design",
                "step_type": "guided",
                "content_path": "tier2/m-swe-aadc-06-fundamentals/steps/step1_fundamentals.md",
                "starter_code_path": "",
                "test_code_path": "",
                "config_schema_json": json.dumps({}),
                "xp_partial": 400,
                "skill_tag": "aadc-topology",
                "hint_allowed": 1,
                "estimated_seconds": 1200,
            },
        ],
    },
    {
        "slug": "m-swe-aadc-07-autonomy",
        "title": "Autonomy Level Design (L0–L5)",
        "tagline": "Understand the autonomy spectrum. Avoid L5 catastrophes with circuit breakers and HITL.",
        "tier": 2,
        "topic": "swe_arch",
        "role_filter": "swe_arch",
        "mission_type": "coding",
        "xp_reward": 450,
        "order_idx": 7,
        "difficulty": "intermediate",
        "estimated_minutes": 40,
        "prereq_slugs_json": json.dumps(["m-swe-aadc-06-fundamentals"]),
        "steps": [
            {
                "step_num": 1,
                "title": "Compare L5 vs L2 Autonomy Assessment Findings",
                "step_type": "coding",
                "content_path": "tier2/m-swe-aadc-07-autonomy/steps/step1_autonomy.md",
                "starter_code_path": "",
                "test_code_path": "",
                "config_schema_json": json.dumps({}),
                "xp_partial": 450,
                "skill_tag": "aadc-autonomy",
                "hint_allowed": 1,
                "estimated_seconds": 1500,
            },
        ],
    },
    {
        "slug": "m-swe-aadc-08-safety-redundancy",
        "title": "Safety Redundancy Design",
        "tagline": "Build defense-in-depth for agentic systems. 3 safety nodes minimum.",
        "tier": 2,
        "topic": "secops",
        "role_filter": "swe_arch,secops_eng",
        "mission_type": "coding",
        "xp_reward": 500,
        "order_idx": 8,
        "difficulty": "advanced",
        "estimated_minutes": 45,
        "prereq_slugs_json": json.dumps(["m-swe-aadc-07-autonomy"]),
        "steps": [
            {
                "step_num": 1,
                "title": "Achieve Safety Coverage 80%+ with Redundant Safety Nodes",
                "step_type": "coding",
                "content_path": "tier2/m-swe-aadc-08-safety-redundancy/steps/step1_safety_redundancy.md",
                "starter_code_path": "",
                "test_code_path": "",
                "config_schema_json": json.dumps({}),
                "xp_partial": 500,
                "skill_tag": "aadc-safety-redundancy",
                "hint_allowed": 0,
                "estimated_seconds": 2000,
            },
        ],
    },
]


def seed():
    conn = get_connection()
    seeded = 0
    try:
        for m in _MISSIONS:
            steps = m.pop("steps", [])
            existing = conn.execute(
                "SELECT id FROM fa_missions WHERE slug=%s", (m["slug"],)
            ).fetchone()

            if existing:
                mid = existing["id"]
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
                mid = conn.execute("SELECT id FROM fa_missions WHERE slug=%s", (m["slug"],)).fetchone()["id"]
                print(f"  [+] seeded mission: {m['slug']} (id={mid})")
                seeded += 1

            for s in steps:
                ex_step = conn.execute(
                    "SELECT id FROM fa_mission_steps WHERE mission_id=%s AND step_num=%s",
                    (mid, s["step_num"]),
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
                        mid, s["step_num"], s["title"], s["step_type"],
                        s["content_path"], s.get("starter_code_path", ""),
                        s.get("test_code_path", ""), s.get("config_schema_json", "{}"),
                        s["xp_partial"], s["skill_tag"], s["hint_allowed"],
                        s["estimated_seconds"],
                    ),
                )
                print(f"      [+] seeded step {s['step_num']}: {s['title']}")

        conn.commit()
        print(f"\nDone — {seeded} new mission(s) seeded.")
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
