# CUI // SP-CTI
"""AI Learning Paths — 3-track curriculum for non-AI teams."""
from __future__ import annotations


_TRACKS = [
    {
        "id": "consumer",
        "name": "AI Consumer",
        "level": "Beginner",
        "icon": "🌱",
        "description": (
            "Learn to use ICDEV™ AI tools confidently without writing code. "
            "Ideal for program managers, contracting officers, and leadership."
        ),
        "task_count": 8,
        "duration_hours": 6,
        "modules": [
            "Introduction to AI in Government",
            "Using the AI Launchpad Wizard",
            "Deploying AI Patterns",
            "Reading AI Recommendations",
            "Compliance & Classification Basics",
            "Reviewing AI-Generated Reports",
            "Working with the Knowledge Handoff",
            "Capstone: Deploy a Pattern to Production",
        ],
        "badge": "AI Consumer Badge",
        "badge_color": "success",
        "progress": 0,
    },
    {
        "id": "configurator",
        "name": "AI Configurator",
        "level": "Intermediate",
        "icon": "⚡",
        "description": (
            "Configure and customize ICDEV™ agents, args, and compliance rules. "
            "Ideal for system administrators, DevSecOps engineers, and analysts."
        ),
        "task_count": 12,
        "duration_hours": 14,
        "modules": [
            "FORGE Framework Deep Dive",
            "Configuring LLM Routing",
            "Compliance Control Customization",
            "Security Gate Tuning",
            "Kanban Workflow Configuration",
            "DB Migration Patterns",
            "Multi-Cloud Deployment Args",
            "SBOM & Dependency Management",
            "Fine-Tuning Evaluation",
            "Air-Gap Mode Setup",
            "Custom Pattern Creation",
            "Capstone: Configure a Full IL4 Stack",
        ],
        "badge": "AI Configurator Badge",
        "badge_color": "info",
        "progress": 0,
    },
    {
        "id": "builder",
        "name": "AI Builder",
        "level": "Advanced",
        "icon": "🚀",
        "description": (
            "Build new ICDEV™ tools, goals, agents, and canvases from scratch. "
            "Ideal for software engineers and architects extending the platform."
        ),
        "task_count": 16,
        "duration_hours": 24,
        "modules": [
            "ANVIL Workflow Mastery",
            "Writing FORGE Goals",
            "Building Deterministic Tools",
            "Agent Architecture Design",
            "Canvas Development (7-component gate)",
            "Visual Agent Builder API",
            "Custom Compliance Crosswalk",
            "MCP Gateway Extension",
            "Multi-Agent Orchestration",
            "Self-Healing Reflex Design",
            "AI Security & Sandboxing",
            "Supply Chain Integration",
            "Performance Optimization",
            "Publishing to Marketplace",
            "Air-Gap Deployment Strategy",
            "Capstone: Ship a Production Canvas",
        ],
        "badge": "AI Builder Badge",
        "badge_color": "warning",
        "progress": 0,
    },
]


def get_tracks() -> list[dict]:
    return _TRACKS
