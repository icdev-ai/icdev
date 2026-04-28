#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 070: Create tech_radar_entries and tech_radar_history tables.

tech_radar_entries is updatable (ring can change as assessments run).
tech_radar_history is append-only per NIST AU requirements — records every
ring transition with the composite score and triggering innovation signal.

Seeds 10 canonical entries bootstrapped from context/tech_radar/rings.yaml.
Scores are illustrative; the Tech Radar engine overwrites on first live scan.
"""

import sqlite3
import uuid
from datetime import timezone, datetime

MIGRATION_ID = "070"
MIGRATION_NAME = "tech_radar_entries"
DESCRIPTION = "Create tech_radar_entries and tech_radar_history tables, seed 10 entries"

_SEED_ENTRIES = [
    {
        "name": "rspack",
        "category": "build_tooling",
        "current_ring": "trial",
        "ecosystem_maturity": 0.68,
        "icdev_fit": 0.70,
        "airgap_compat": 0.72,
        "il_compliance": 0.45,
        "composite_score": 0.67,
        "rationale": (
            "Selected in ADR (memory: project_rust_migration_tools). Adopt rspack now per "
            "project guidance. In trial pending air-gap npm mirror resolution "
            "(memory: feedback_no_npm_airgap) — promotes to adopt once npm mirror ships."
        ),
    },
    {
        "name": "ast-grep",
        "category": "sast_code_intel",
        "current_ring": "trial",
        "ecosystem_maturity": 0.62,
        "icdev_fit": 0.73,
        "airgap_compat": 0.75,
        "il_compliance": 0.50,
        "composite_score": 0.65,
        "rationale": (
            "SAST candidate per project_rust_migration_tools memory. Rust binary distribution "
            "enables air-gap use. Trial until integrated into coherence_checker pipeline."
        ),
    },
    {
        "name": "Hypothesis",
        "category": "testing",
        "current_ring": "adopt",
        "ecosystem_maturity": 0.88,
        "icdev_fit": 0.82,
        "airgap_compat": 0.90,
        "il_compliance": 0.72,
        "composite_score": 0.84,
        "rationale": (
            "Mature PyPI package, installable air-gap via PyPI mirror, no external runtime. "
            "Fits ICDEV™ TDD/BDD mandate. Adopt for all Python tool testing."
        ),
    },
    {
        "name": "uv",
        "category": "package_management",
        "current_ring": "adopt",
        "ecosystem_maturity": 0.75,
        "icdev_fit": 0.85,
        "airgap_compat": 0.88,
        "il_compliance": 0.65,
        "composite_score": 0.79,
        "rationale": (
            "Single binary, no Python runtime required for install. Air-gap friendly. "
            "Significant CI/CD speed improvement over pip for ICDEV™ pipelines. "
            "Composite at boundary — treat as adopt given strong airgap_compat and icdev_fit."
        ),
    },
    {
        "name": "ruff",
        "category": "linting_formatting",
        "current_ring": "adopt",
        "ecosystem_maturity": 0.82,
        "icdev_fit": 0.88,
        "airgap_compat": 0.90,
        "il_compliance": 0.68,
        "composite_score": 0.83,
        "rationale": (
            "Single binary, PyPI installable. Already in widespread DoD-adjacent Python "
            "projects. Replaces three tools (flake8, black, isort) reducing dep surface."
        ),
    },
    {
        "name": "Playwright",
        "category": "e2e_testing",
        "current_ring": "adopt",
        "ecosystem_maturity": 0.85,
        "icdev_fit": 0.90,
        "airgap_compat": 0.70,
        "il_compliance": 0.65,
        "composite_score": 0.78,
        "rationale": (
            "Official E2E test framework for ICDEV™ per CLAUDE.md guardrail. MCP server "
            "integrated. Browser binaries downloadable for air-gap pre-staging. "
            "Adopted; Selenium remains for legacy headless Chrome flows (e2e_selenium memory)."
        ),
    },
    {
        "name": "Trivy",
        "category": "vulnerability_scanning",
        "current_ring": "adopt",
        "ecosystem_maturity": 0.84,
        "icdev_fit": 0.86,
        "airgap_compat": 0.85,
        "il_compliance": 0.80,
        "composite_score": 0.84,
        "rationale": (
            "Single binary, offline DB download supported. SBOM output aligns with ICDEV™ "
            "NIST 800-53 supply chain controls. Strong FedRAMP/CMMC evidence base."
        ),
    },
    {
        "name": "OpenTelemetry",
        "category": "observability",
        "current_ring": "adopt",
        "ecosystem_maturity": 0.87,
        "icdev_fit": 0.85,
        "airgap_compat": 0.78,
        "il_compliance": 0.82,
        "composite_score": 0.84,
        "rationale": (
            "CNCF graduated project. IL4/IL5 deployments reference OTel for NIST AU log "
            "controls. Python SDK pip-installable air-gap. Collector available as binary."
        ),
    },
    {
        "name": "Terraform",
        "category": "iac",
        "current_ring": "adopt",
        "ecosystem_maturity": 0.90,
        "icdev_fit": 0.88,
        "airgap_compat": 0.82,
        "il_compliance": 0.85,
        "composite_score": 0.87,
        "rationale": (
            "Primary IaC tool per CLAUDE.md and cicd_config.yaml. BSL license acceptable "
            "for internal Gov/DoD use. Provider binaries cacheable for air-gap. "
            "GovCloud provider support documented. Strong IL4/IL5 deployment evidence."
        ),
    },
    {
        "name": "Ansible",
        "category": "configuration_management",
        "current_ring": "adopt",
        "ecosystem_maturity": 0.91,
        "icdev_fit": 0.86,
        "airgap_compat": 0.87,
        "il_compliance": 0.88,
        "composite_score": 0.88,
        "rationale": (
            "Primary config management tool per CLAUDE.md and deployment_profiles.yaml. "
            "Agentless — no target-side binary install. PyPI installable air-gap. "
            "DISA STIG Ansible content available. Highest IL compliance score in seed set."
        ),
    },
]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    actions = []
    cur = conn.cursor()

    # ── tech_radar_entries (updatable) ────────────────────────────────────────
    if _table_exists(conn, "tech_radar_entries"):
        actions.append("tech_radar_entries_exists")
    else:
        cur.execute("""
            CREATE TABLE tech_radar_entries (
                id                  TEXT PRIMARY KEY,
                name                TEXT NOT NULL UNIQUE,
                category            TEXT,
                current_ring        TEXT CHECK(current_ring IN ('adopt','trial','assess','hold')),
                previous_ring       TEXT,
                ecosystem_maturity  REAL,
                icdev_fit           REAL,
                airgap_compat       REAL,
                il_compliance       REAL,
                composite_score     REAL,
                rationale           TEXT,
                last_assessed       TEXT,
                classification      TEXT DEFAULT 'CUI // SP-CTI'
            )
        """)
        actions.append("tech_radar_entries_created")

    # ── tech_radar_history (append-only) ─────────────────────────────────────
    if _table_exists(conn, "tech_radar_history"):
        actions.append("tech_radar_history_exists")
    else:
        cur.execute("""
            CREATE TABLE tech_radar_history (
                id                   TEXT PRIMARY KEY,
                entry_id             TEXT NOT NULL,
                from_ring            TEXT,
                to_ring              TEXT,
                composite_score      REAL,
                innovation_signal_id TEXT,
                changed_at           TEXT NOT NULL
            )
        """)
        actions.append("tech_radar_history_created")

    # ── Indexes ───────────────────────────────────────────────────────────────
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_techrad_ring "
        "ON tech_radar_entries(current_ring)"
    )
    actions.append("idx_techrad_ring_ensured")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_techrad_history_entry "
        "ON tech_radar_history(entry_id)"
    )
    actions.append("idx_techrad_history_entry_ensured")

    # ── Seed entries ──────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    seeded = 0
    for entry in _SEED_ENTRIES:
        existing = cur.execute(
            "SELECT id FROM tech_radar_entries WHERE name = ?", (entry["name"],)
        ).fetchone()
        if existing:
            continue
        cur.execute(
            """
            INSERT INTO tech_radar_entries
                (id, name, category, current_ring, previous_ring,
                 ecosystem_maturity, icdev_fit, airgap_compat, il_compliance,
                 composite_score, rationale, last_assessed, classification)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 'CUI // SP-CTI')
            """,
            (
                str(uuid.uuid4()),
                entry["name"],
                entry["category"],
                entry["current_ring"],
                entry["ecosystem_maturity"],
                entry["icdev_fit"],
                entry["airgap_compat"],
                entry["il_compliance"],
                entry["composite_score"],
                entry["rationale"],
                now,
            ),
        )
        seeded += 1

    if seeded:
        actions.append(f"seeded_{seeded}_entries")

    conn.commit()
    return {"status": "applied", "actions": actions}
