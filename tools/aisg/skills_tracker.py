# CUI // SP-CTI
"""AI Skills Gap Tracker — maturity assessment and personalized recommendations."""
from __future__ import annotations

from tools.db.storage import get_connection


_SKILL_DOMAINS = [
    {
        "id": "prompt_engineering",
        "name": "Prompt Engineering",
        "description": "Writing effective prompts for LLM-backed agents",
        "current_level": 3,
        "target_level": 5,
        "category": "core",
    },
    {
        "id": "ai_security",
        "name": "AI Security",
        "description": "Securing AI pipelines, prompt injection defense, sandboxing",
        "current_level": 2,
        "target_level": 4,
        "category": "security",
    },
    {
        "id": "compliance_rmf",
        "name": "Compliance / RMF",
        "description": "NIST 800-53, FedRAMP, CMMC, ATO acceleration",
        "current_level": 4,
        "target_level": 5,
        "category": "compliance",
    },
    {
        "id": "mlops",
        "name": "MLOps & Fine-Tuning",
        "description": "Model training, evaluation, deployment lifecycle",
        "current_level": 1,
        "target_level": 3,
        "category": "advanced",
    },
    {
        "id": "multi_agent",
        "name": "Multi-Agent Orchestration",
        "description": "Designing and operating multi-agent systems",
        "current_level": 2,
        "target_level": 4,
        "category": "advanced",
    },
    {
        "id": "rag",
        "name": "RAG & Knowledge Systems",
        "description": "Retrieval-augmented generation, embeddings, vector stores",
        "current_level": 3,
        "target_level": 4,
        "category": "core",
    },
    {
        "id": "devsecops",
        "name": "DevSecOps Integration",
        "description": "CI/CD pipeline security, SBOM, SAST, supply chain",
        "current_level": 3,
        "target_level": 5,
        "category": "security",
    },
    {
        "id": "data_engineering",
        "name": "Data Engineering",
        "description": "Pipelines, schema design, ETL for AI workloads",
        "current_level": 3,
        "target_level": 4,
        "category": "core",
    },
]

_MATURITY_LEVELS = {
    1: {"label": "Exploring", "color": "text-dim", "badge_class": "badge-secondary"},
    2: {"label": "Piloting", "color": "text-warning", "badge_class": "badge-warning"},
    3: {"label": "Adopting", "color": "text-info", "badge_class": "badge-info"},
    4: {"label": "Scaling", "color": "text-success", "badge_class": "badge-success"},
    5: {"label": "Mastering", "color": "text-accent", "badge_class": "badge-primary"},
}

_RECOMMENDATIONS = [
    {
        "skill": "AI Security",
        "action": "Complete the AI Security module in the AI Builder learning track",
        "priority": "high",
        "link": "/ai-learning",
        "gap": 2,
    },
    {
        "skill": "MLOps & Fine-Tuning",
        "action": "Start with the Fine-Tuning Dashboard to build hands-on experience",
        "priority": "high",
        "link": "/fine-tuning",
        "gap": 2,
    },
    {
        "skill": "Multi-Agent Orchestration",
        "action": "Deploy the Threat Triage pattern to practice multi-agent coordination",
        "priority": "medium",
        "link": "/ai-patterns",
        "gap": 2,
    },
    {
        "skill": "Prompt Engineering",
        "action": "Practice with the AI Launchpad Wizard across 5 different use cases",
        "priority": "medium",
        "link": "/ai-wizard",
        "gap": 2,
    },
    {
        "skill": "DevSecOps Integration",
        "action": "Configure a full security gate pipeline using icdev-secure + icdev-deploy",
        "priority": "medium",
        "link": "/ai-skills",
        "gap": 2,
    },
]


def get_skills_data() -> dict:
    """Return skill domains, maturity badge, and recommendations."""
    total_current = sum(s["current_level"] for s in _SKILL_DOMAINS)
    avg_level = round(total_current / len(_SKILL_DOMAINS), 1) if _SKILL_DOMAINS else 0
    maturity_int = max(1, min(5, round(avg_level)))
    maturity = _MATURITY_LEVELS[maturity_int]

    gaps = [
        {**s, "gap": s["target_level"] - s["current_level"]}
        for s in _SKILL_DOMAINS
    ]
    gaps_sorted = sorted(gaps, key=lambda x: -x["gap"])

    team_members: list[dict] = []
    try:
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("SELECT DISTINCT triggered_by FROM aisg_roi_events LIMIT 5")
            team_members = [{"name": r[0], "maturity": "Adopting"} for r in c.fetchall()]
        finally:
            conn.close()
    except Exception:
        pass

    if not team_members:
        team_members = [
            {"name": "Alice M.", "maturity": "Scaling", "track": "AI Builder"},
            {"name": "Bob K.", "maturity": "Adopting", "track": "AI Consumer"},
            {"name": "Carol T.", "maturity": "Piloting", "track": "AI Configurator"},
            {"name": "Dave R.", "maturity": "Adopting", "track": "AI Consumer"},
        ]

    return {
        "skill_domains": gaps_sorted,
        "maturity_label": maturity["label"],
        "maturity_badge_class": maturity["badge_class"],
        "maturity_score": maturity_int,
        "avg_level": avg_level,
        "total_skills": len(_SKILL_DOMAINS),
        "recommendations": _RECOMMENDATIONS,
        "team_members": team_members,
        "maturity_levels": _MATURITY_LEVELS,
    }
