#!/usr/bin/env python3
"""Seed Kanban tasks for 32 Enterprise Showcase Applications.

Each application is standalone, uses OSINT + synthetic data, and targets
enterprise readiness (not POC). All live under showcase/ which is gitignored.

Run: python tools/kanban/seed_showcase.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402

_NOW = datetime.now(timezone.utc).isoformat()


def _conn():
    return get_connection()


# ═══════════════════════════════════════════════════════════════════════════════
# 32 Showcase Applications
# ═══════════════════════════════════════════════════════════════════════════════

APPS = [
    # Cyber Automation (9)
    ("cyber-twins", "Cyber Automation", "Cyber network twins with CNO virtualization"),
    ("cyber-surveillance", "Cyber Automation", "Advanced network cyber surveillance / diagnostics"),
    ("sw-vulndisc", "Cyber Automation", "SW vulnerability discovery and exploits"),
    ("hw-vulndisc", "Cyber Automation", "HW vulnerability discovery and exploits"),
    ("tactical-protection", "Cyber Automation", "Tactical platform protection systems (Incl Weapons)"),
    ("critical-infra", "Cyber Automation", "Critical Infrastructure Cyber Protection Systems"),
    ("cyber-actor-defeat", "Cyber Automation", "Advanced cyber actor defeat technologies"),
    ("cyber-analyst-ops", "Cyber Automation", "Cyber analyst operational support platform"),
    ("genai-pentest", "Cyber Automation", "GenAI enabled PenTesting and CTI extraction"),
    # Advanced Comms / Networking (8)
    ("lpilpd-waveform", "Advanced Comms", "LPI/LPD (COVCOM) waveform / system development"),
    ("hybrid-cloud-suite", "Advanced Comms", "Hybrid Cloud Automation Tool Suite Development"),
    ("cloud-edge-ai", "Advanced Comms", "Cloud to edge secure data management using AI"),
    ("agentic-arch", "Advanced Comms", "Agentic AI architecture and platform development"),
    ("data-obscuration", "Advanced Comms", "Data and signature obscuration"),
    ("data-provenance", "Advanced Comms", "Data provenance and blockchain integration"),
    ("enterprise-analytics", "Advanced Comms", "Automated enterprise data analytics / discovery"),
    ("tactical-waveform", "Advanced Comms", "Tactical waveform and signal processing development"),
    # Spectrum / EW / Tactical Systems (5)
    ("resilient-sdr", "Spectrum EW", "Resilient multimodal SW defined data networking"),
    ("polymorphic-comms", "Spectrum EW", "Polymorphic communications solutions"),
    ("dynamic-spectrum", "Spectrum EW", "Dynamic Spectrum management"),
    ("obfuscation-ops", "Spectrum EW", "Obfuscation / Concealment operations / tradecraft technologies"),
    ("cuas-lethal", "Spectrum EW", "CUAS and Lethal Autonomy"),
    # Agentic AI / Enterprise Automation (5)
    ("info-ops-platform", "Agentic AI", "Information Operations Platform"),
    ("agentic-dev", "Agentic AI", "Agentic AI Applications / Architecture / Model Development"),
    ("isr-hmt", "Agentic AI", "ISR Automation / Human Machine Teaming"),
    ("industrial-twins", "Agentic AI", "Industrial enterprise twins for continuous optimization"),
    ("adversarial-defense", "Agentic AI", "Adversarial AI detection and defense"),
    # C5ISR / All Domain Ops / Space (5)
    ("ai-mission-plan", "C5ISR Space", "AI augmented mission planning"),
    ("cislunar-sim", "C5ISR Space", "Cislunar / Orbital dynamics mod/sim"),
    ("space-awareness", "C5ISR Space", "Space Domain Awareness"),
    ("tactical-maneuver", "C5ISR Space", "Tactical maneuver mission support aids"),
    ("all-domain-sim", "C5ISR Space", "AI powered All Domain Mod/Sim"),
]


def _app_tasks(slug: str, category: str, description: str) -> list[dict]:
    name = slug.replace("-", " ").title()
    return [
        {
            "id": f"sc-{slug}-01",
            "title": f"SHOWCASE [{category}]: Scaffold {name} application",
            "description": (
                f"Create showcase/{slug}/ directory and scaffold the enterprise application.\n\n"
                f"Original requirement: {description}\n\n"
                "Steps:\n"
                "  1. Run child_app_generator.py to create base scaffold.\n"
                "  2. Customize args/llm_config.yaml with app-specific routing.\n"
                "  3. Create app-specific constants.py with CUI/SECRET markings.\n"
                "  4. Add DB migration for app-specific tables (use init_icdev_db.py pattern).\n"
                "  5. Create Dockerfile and docker-compose.yml for standalone deployment.\n"
                "  6. Add README.md with architecture diagram and deployment instructions.\n\n"
                "Enterprise requirements:\n"
                "  - Full auth (session + API key).\n"
                "  - Audit trail on every mutation (tools/audit/audit_logger.py).\n"
                "  - CUI // SP-CTI classification banners on all outputs.\n"
                "  - Input validation and injection scanning on all endpoints."
            ),
            "task_type": "build",
            "priority": "high",
            "status": "backlog",
            "scheduled_at": None,
            "depends_on_task_id": "sc-infra-04",
        },
        {
            "id": f"sc-{slug}-02",
            "title": f"SHOWCASE [{category}]: Build OSINT + synthetic data modules for {name}",
            "description": (
                f"Build OSINT ingestion and synthetic data generation for {name}.\n\n"
                "OSINT module (tools/showcase/osint_engine.py integration):\n"
                "  - Ingest public data sources (OSINT feeds, CVE, CPE, ADS-B, FCC, etc.).\n"
                "  - Cache raw data in app-specific SQLite/PostgreSQL tables.\n"
                "  - Normalize and deduplicate using SHA-256 fingerprints.\n"
                "  - Respect rate limits and robots.txt.\n"
                "  - Air-gap mode: use cached snapshots when offline.\n\n"
                "Synthetic data module (tools/showcase/synthetic_data_engine.py integration):\n"
                "  - Generate realistic training/test data matching the domain.\n"
                "  - Use ICDEV LLM router for narrative generation of synthetic scenarios.\n"
                "  - Store synthetic datasets in data/ directory (gitignored).\n"
                "  - Provide CLI to regenerate data on demand.\n\n"
                "Both modules must be importable and testable independently."
            ),
            "task_type": "build",
            "priority": "high",
            "status": "backlog",
            "scheduled_at": None,
            "depends_on_task_id": f"sc-{slug}-01",
        },
        {
            "id": f"sc-{slug}-03",
            "title": f"SHOWCASE [{category}]: Build core logic + API for {name}",
            "description": (
                f"Build the core business logic and REST API for {name}.\n\n"
                "Backend modules:\n"
                "  - <slug>/engine.py — domain-specific analysis engine.\n"
                "  - <slug>/analyzer.py — main analysis pipeline using OSINT + synthetic data.\n"
                "  - <slug>/reporter.py — report generation (PDF, HTML, JSON, OSCAL).\n"
                "  - <slug>/blueprint.py — Flask blueprint with all routes.\n\n"
                "API routes (enterprise-grade):\n"
                "  - POST /api/v1/analyze — run analysis on provided input.\n"
                "  - GET  /api/v1/status/{job_id} — poll async job status.\n"
                "  - GET  /api/v1/results/{job_id} — fetch results.\n"
                "  - POST /api/v1/reports — generate and download reports.\n"
                "  - GET  /api/v1/osint/refresh — trigger OSINT ingestion.\n"
                "  - GET  /api/v1/synthetic/generate — regenerate synthetic dataset.\n\n"
                "All routes must:\n"
                "  - Use get_connection() for DB access (never sqlite3.connect).\n"
                "  - Validate input via JSON schemas.\n"
                "  - Log to llm_telemetry when LLM is invoked.\n"
                "  - Return proper HTTP status codes and error messages.\n"
                "  - Support both sync and async execution modes."
            ),
            "task_type": "build",
            "priority": "high",
            "status": "backlog",
            "scheduled_at": None,
            "depends_on_task_id": f"sc-{slug}-02",
        },
        {
            "id": f"sc-{slug}-04",
            "title": f"SHOWCASE [{category}]: Build UI/dashboard for {name}",
            "description": (
                f"Build the user interface and dashboard for {name}.\n\n"
                "Templates (Jinja2):\n"
                "  - templates/<slug>/index.html — landing page with app branding.\n"
                "  - templates/<slug>/dashboard.html — main dashboard with visualizations.\n"
                "  - templates/<slug>/analysis.html — analysis input + results view.\n"
                "  - templates/<slug>/reports.html — report library and download.\n"
                "  - templates/<slug>/osint.html — OSINT data browser.\n"
                "  - templates/<slug>/settings.html — app configuration panel.\n\n"
                "Frontend requirements:\n"
                "  - Responsive design (Bootstrap 5 or Tailwind).\n"
                "  - Real-time updates via Server-Sent Events or polling.\n"
                "  - Interactive charts (Chart.js or D3.js).\n"
                "  - Dark mode toggle.\n"
                "  - CUI banner and classification footer on every page.\n"
                "  - Export buttons for CSV, PDF, PNG.\n\n"
                "Blueprint routes:\n"
                "  - GET /{slug}/ — render index.\n"
                "  - GET /{slug}/dashboard — render dashboard.\n"
                "  - GET /{slug}/analysis — render analysis page.\n"
                "  - GET /{slug}/reports — render reports library.\n"
                "  - GET /{slug}/osint — render OSINT browser.\n"
                "  - GET /{slug}/settings — render settings.\n\n"
                "Navigation:\n"
                "  - Link from main ICDEV dashboard via canvas grid or sidebar.\n"
                "  - Breadcrumb trail on every page."
            ),
            "task_type": "build",
            "priority": "high",
            "status": "backlog",
            "scheduled_at": None,
            "depends_on_task_id": f"sc-{slug}-03",
        },
        {
            "id": f"sc-{slug}-05",
            "title": f"SHOWCASE [{category}]: Tests + V&V gate for {name}",
            "description": (
                f"Write comprehensive tests and run validation gates for {name}.\n\n"
                "Unit tests (pytest):\n"
                "  - test_engine.py — test analyzer logic with mocked OSINT data.\n"
                "  - test_api.py — test all REST endpoints (success + error paths).\n"
                "  - test_osint.py — test OSINT ingestion and caching.\n"
                "  - test_synthetic.py — test synthetic data generation determinism.\n"
                "  - test_reporter.py — test report generation for all formats.\n\n"
                "E2E tests (Playwright or Selenium):\n"
                "  - Landing page loads with CUI banner.\n"
                "  - Dashboard renders charts without JS errors.\n"
                "  - Analysis flow: submit input -> see progress -> view results.\n"
                "  - Report generation and download works.\n"
                "  - Settings page saves and persists configuration.\n\n"
                "Validation gates:\n"
                "  1. python tools/testing/health_check.py --json (no import errors).\n"
                "  2. python -m pytest showcase/{slug}/tests/ --cov (>= 80% coverage).\n"
                "  3. python tools/workflow/coherence_checker.py --changed-files showcase/{slug}/ (pass).\n"
                "  4. python tools/dx/companion.py --sync --write --json (sync complete).\n"
                "  5. Bandit security scan: python -m bandit -r showcase/{slug}/ -ll.\n\n"
                "Mark this task DONE only when all gates pass."
            ),
            "task_type": "test",
            "priority": "critical",
            "status": "backlog",
            "scheduled_at": None,
            "depends_on_task_id": f"sc-{slug}-04",
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Infrastructure Tasks (shared across all 32 apps)
# ═══════════════════════════════════════════════════════════════════════════════

INFRA_TASKS = [
    {
        "id": "sc-infra-01",
        "title": "SHOWCASE INFRA: Create showcase/ directory + update .gitignore/.dockerignore",
        "description": (
            "1. Create showcase/ directory at repo root.\n"
            "2. Add showcase/ to .gitignore (already done if running this plan).\n"
            "3. Add showcase/ to .dockerignore (already done if running this plan).\n"
            "4. Create showcase/README.md explaining the showcase program.\n"
            "5. Add showcase/ to child_app_generator.py exclusion list if needed."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "sc-infra-02",
        "title": "SHOWCASE INFRA: Create reusable OSINT integration module",
        "description": (
            "Create tools/showcase/osint_engine.py — reusable OSINT ingestion framework.\n\n"
            "Features:\n"
            "  - Abstract base class BaseOSINTSource with fetch(), parse(), normalize().\n"
            "  - Concrete sources: CVE/NVD, CPE, Shodan (mock/air-gap safe), ADS-B Exchange, FCC, AIS.\n"
            "  - Rate limiting with token bucket.\n"
            "  - Caching layer using tools/llm/response_cache.py pattern (SQLite/PostgreSQL).\n"
            "  - Deduplication via SHA-256 fingerprint.\n"
            "  - Air-gap mode: reads from data/osint_cache/ if online fetch fails.\n"
            "  - CLI: python tools/showcase/osint_engine.py --source cve --fetch --json.\n\n"
            "All sources must be mockable for unit testing."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sc-infra-01",
    },
    {
        "id": "sc-infra-03",
        "title": "SHOWCASE INFRA: Create reusable synthetic data generator",
        "description": (
            "Create tools/showcase/synthetic_data_engine.py — reusable synthetic data generator.\n\n"
            "Features:\n"
            "  - Domain-agnostic schema definition (YAML or Python dataclasses).\n"
            "  - LLM-powered narrative generation for realistic scenarios.\n"
            "  - Deterministic seeding for reproducible datasets.\n"
            "  - Output formats: JSON, CSV, Parquet, SQL insert statements.\n"
            "  - Privacy-safe: no real PII; all names/addresses are synthetic.\n"
            "  - Volume control: generate 100 to 1M records.\n"
            "  - CLI: python tools/showcase/synthetic_data_engine.py --domain cyber --records 1000 --output data/synthetic/.\n\n"
            "Integrates with tools/llm/router.py for high-fidelity generation."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sc-infra-01",
    },
    {
        "id": "sc-infra-04",
        "title": "SHOWCASE INFRA: Create showcase app template/boilerplate",
        "description": (
            "Create tools/showcase/app_template/ — a cookiecutter-like boilerplate for all 32 apps.\n\n"
            "Template structure:\n"
            "  showcase/{{app_slug}}/\n"
            "    __init__.py\n"
            "    blueprint.py        — Flask blueprint with auth, routes, error handlers.\n"
            "    engine.py           — stub analysis engine.\n"
            "    analyzer.py         — stub pipeline.\n"
            "    reporter.py         — stub report generator.\n"
            "    constants.py        — app name, version, classification markings.\n"
            "    models.py           — SQLAlchemy or raw SQL models.\n"
            "    templates/{{slug}}/  — Jinja2 templates (index, dashboard, analysis, reports, settings).\n"
            "    static/{{slug}}/     — CSS, JS, images.\n"
            "    tests/              — pytest stubs.\n"
            "    Dockerfile          — multi-stage build.\n"
            "    docker-compose.yml  — PG + app stack.\n"
            "    README.md           — architecture + deploy guide.\n"
            "    requirements.txt    — pinned deps.\n\n"
            "Generator script:\n"
            "  python tools/showcase/generate_app.py --slug <name> --category <cat> --description '...'\n\n"
            "The template must be child-app compatible (inherits tools/llm/, tools/db/, etc.)."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sc-infra-02",
    },
    {
        "id": "sc-infra-05",
        "title": "SHOWCASE INFRA: Create showcase validation framework",
        "description": (
            "Create tools/showcase/validator.py — validation framework for all showcase apps.\n\n"
            "Checks:\n"
            "  1. Directory structure matches template.\n"
            "  2. Blueprint registers without ImportError.\n"
            "  3. All templates render without Jinja errors.\n"
            "  4. DB migrations apply cleanly.\n"
            "  5. OSINT module is importable and has fetch() method.\n"
            "  6. Synthetic data module is importable and has generate() method.\n"
            "  7. API routes return 200 for health checks.\n"
            "  8. CUI banner present on all templates.\n"
            "  9. Tests exist and pytest discovers them.\n"
            "  10. Bandit scan finds no high-severity issues.\n\n"
            "CLI: python tools/showcase/validator.py --app <slug> --json.\n"
            "Returns pass/fail with detailed findings."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sc-infra-04",
    },
    {
        "id": "sc-infra-06",
        "title": "SHOWCASE INFRA: Register in manifest + update CLAUDE.md",
        "description": (
            "1. Add tools/showcase/ entries to tools/manifest/showcase.md (new shard).\n"
            "2. Update CLAUDE.md docs/reference/commands.md with showcase CLI commands.\n"
            "3. Update CLAUDE.md Quick Reference with:\n"
            "   python tools/showcase/generate_app.py --slug <name> --category <cat>\n"
            "   python tools/showcase/osint_engine.py --source cve --fetch --json\n"
            "   python tools/showcase/synthetic_data_engine.py --domain cyber --records 1000\n"
            "   python tools/showcase/validator.py --app <slug> --json\n"
            "4. Add args/showcase_config.yaml with global defaults.\n"
            "5. Run coherence_checker.py to validate."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sc-infra-05",
    },
    {
        "id": "sc-infra-07",
        "title": "SHOWCASE INFRA: Run infrastructure V&V gate",
        "description": (
            "Post-implementation validation of shared infrastructure:\n\n"
            "1. python tools/testing/health_check.py --json\n"
            "2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "3. python tools/dx/companion.py --sync --write --json\n"
            "4. python -m pytest tools/showcase/tests/ --cov\n"
            "5. python -m bandit -r tools/showcase/ -ll\n\n"
            "All gates must pass before any per-app tasks begin."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "sc-infra-06",
    },
]


def seed():
    conn = _conn()
    cur = conn.cursor()

    all_tasks = []
    all_tasks.extend(INFRA_TASKS)
    for slug, category, description in APPS:
        all_tasks.extend(_app_tasks(slug, category, description))

    inserted = 0
    skipped = 0

    for task in all_tasks:
        row = cur.execute(
            "SELECT id FROM kanban_tasks WHERE id = ?", (task["id"],)
        ).fetchone()
        if row:
            skipped += 1
            continue

        cur.execute(
            """
            INSERT INTO kanban_tasks
                (id, title, description, task_type, priority, status,
                 scheduled_at, created_at, updated_at, depends_on_task_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["id"],
                task["title"],
                task["description"],
                task.get("task_type", "build"),
                task.get("priority", "high"),
                task.get("status", "backlog"),
                task.get("scheduled_at"),
                _NOW,
                _NOW,
                task.get("depends_on_task_id"),
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()
    print(f"Inserted {inserted} tasks, skipped {skipped} existing.")
    return {"inserted": inserted, "skipped": skipped}


if __name__ == "__main__":
    seed()
