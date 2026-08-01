# CUI // SP-CTI
"""Migration Intelligence Engine — Database initialization.

Dual-backend: PostgreSQL (default, shared icdev db) or SQLite (dedicated file
data/migration_intel.db).  Env: MI_STORAGE_BACKEND, MI_DB_PATH.

Tables cover: goals, opportunities, strategies, roadmaps, wishlist, budget cycles, scan history.
All financial and goal tables are append-only/immutable after approval.

All runtime SQL uses ``%s`` placeholders; connections are StorageConnection
wrappers whose translate_sql pass rewrites placeholders per backend, so callers
never mix ``?``/``%s`` styles (cnr-mi-01).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
_DB_PATH = _ICDEV_ROOT / "data" / "migration_intel.db"

# Env var carrying the dedicated SQLite path when the resolved backend is sqlite.
# On PostgreSQL (primary) it is ignored; mi_* tables live in the shared icdev
# database, namespaced by their `mi_` prefix.
_MI_DB_PATH_ENV = "MI_DB_PATH"
os.environ.setdefault(_MI_DB_PATH_ENV, str(_DB_PATH))

SCHEMA = """
-- ── Enterprise Technical Goals ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mi_goals (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    description   TEXT,
    category      TEXT DEFAULT 'infrastructure'
        CHECK(category IN ('infrastructure','platform','security','compliance',
                           'cost','cloud','network','application','data','workforce')),
    horizon_years INTEGER DEFAULT 5,
    target_year   INTEGER,
    priority      TEXT DEFAULT 'medium'
        CHECK(priority IN ('critical','high','medium','low')),
    source        TEXT DEFAULT 'manual'
        CHECK(source IN ('manual','chat','yaml','oracle')),
    owner         TEXT,
    status        TEXT DEFAULT 'active'
        CHECK(status IN ('draft','active','achieved','deferred','cancelled')),
    success_criteria TEXT,
    kpis          TEXT,   -- JSON array of KPI strings
    tags          TEXT,   -- JSON array
    created_by    TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Goal chat / NL parsing log (append-only) ────────────────────────────────
CREATE TABLE IF NOT EXISTS mi_goal_chat_log (
    id            TEXT PRIMARY KEY,
    user_input    TEXT NOT NULL,
    parsed_goals  TEXT,   -- JSON: parsed goal objects
    goals_created TEXT,   -- JSON: list of goal IDs created
    llm_model     TEXT,
    confidence    REAL,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Migration Opportunities ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mi_opportunities (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    opportunity_type TEXT DEFAULT 'technology_refresh'
        CHECK(opportunity_type IN (
            'eol_replacement','technology_refresh','vendor_consolidation',
            'cloud_migration','platform_modernization','security_uplift',
            'cost_optimization','compliance_remediation','network_redesign',
            'application_migration','data_migration','capacity_expansion'
        )),
    source_canvas   TEXT,     -- ndc, mdc, sdc, bdc, pdc, odc, ddc
    source_entity_id TEXT,    -- ID of the canvas entity (topology, design, etc.)
    source_entity_name TEXT,
    affected_systems TEXT,    -- JSON array
    current_platform TEXT,
    target_platform TEXT,
    -- App profile (set by sla_enforcer): standard_three_tier | other
    app_profile     TEXT DEFAULT '',
    -- Scoring dimensions (0.0–1.0 each)
    eol_urgency_score   REAL DEFAULT 0.0,
    goal_alignment_score REAL DEFAULT 0.0,
    business_value_score REAL DEFAULT 0.0,
    risk_score          REAL DEFAULT 0.0,
    effort_score        REAL DEFAULT 0.0,  -- lower effort = higher score
    compliance_score    REAL DEFAULT 0.0,
    composite_score     REAL DEFAULT 0.0,
    -- Metadata
    eol_date        TEXT,
    estimated_effort_days INTEGER,
    estimated_cost_usd REAL,
    priority        TEXT DEFAULT 'medium'
        CHECK(priority IN ('critical','high','medium','low')),
    status          TEXT DEFAULT 'identified'
        CHECK(status IN ('identified','scored','strategy_generated','planned',
                         'in_progress','completed','deferred','rejected')),
    linked_goal_ids TEXT,   -- JSON array of mi_goals.id
    linked_wishlist_ids TEXT, -- JSON array of mi_wishlist.id
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Goal ↔ Opportunity Alignment ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mi_goal_alignments (
    id              TEXT PRIMARY KEY,
    goal_id         TEXT REFERENCES mi_goals(id),
    opportunity_id  TEXT REFERENCES mi_opportunities(id),
    alignment_score REAL DEFAULT 0.0,
    alignment_reason TEXT,
    computed_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Migration Strategies (7R + network) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS mi_strategies (
    id              TEXT PRIMARY KEY,
    opportunity_id  TEXT REFERENCES mi_opportunities(id),
    strategy_type   TEXT NOT NULL
        CHECK(strategy_type IN (
            'rehost','replatform','refactor','rebuild','replace',
            'retire','retain',
            'rip_and_replace','graceful_migration','parallel_run',
            'phased_cutover','forklift','consolidate'
        )),
    title           TEXT,
    rationale       TEXT,
    target_platform TEXT,
    estimated_effort_days INTEGER,
    estimated_cost_usd REAL,
    risk_level      TEXT CHECK(risk_level IN ('low','medium','high','critical')),
    pros            TEXT,   -- JSON array
    cons            TEXT,   -- JSON array
    prerequisites   TEXT,   -- JSON array
    -- SLA annotations (set by sla_enforcer)
    cutover_hours_estimate REAL,
    sla_compliant   INTEGER DEFAULT 1,
    recommended     INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'generated'
        CHECK(status IN ('generated','reviewed','approved','executing','completed')),
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Migration Roadmap (wave-based) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mi_roadmaps (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    horizon_years   INTEGER DEFAULT 5,
    wave_count      INTEGER DEFAULT 0,
    opportunities_count INTEGER DEFAULT 0,
    estimated_duration_months INTEGER,
    estimated_total_cost_usd REAL,
    payload         TEXT,   -- JSON: full wave structure
    status          TEXT DEFAULT 'draft'
        CHECK(status IN ('draft','reviewed','approved','executing','completed')),
    generated_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    approved_at     TEXT,
    approved_by     TEXT
);

-- ── Wishlist (Budget / Lifecycle Planning) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS mi_wishlist (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    item_type       TEXT DEFAULT 'hardware'
        CHECK(item_type IN (
            'hardware','software_license','cloud_service','professional_services',
            'training','security_tool','network_equipment','storage','compute',
            'managed_service','subscription','other'
        )),
    vendor          TEXT,
    product_model   TEXT,
    quantity        INTEGER DEFAULT 1,
    -- Budget
    unit_cost_usd   REAL,
    total_cost_usd  REAL,
    fiscal_year     INTEGER,
    quarter         TEXT CHECK(quarter IN ('Q1','Q2','Q3','Q4',NULL)),
    budget_category TEXT DEFAULT 'capex'
        CHECK(budget_category IN ('capex','opex','r_and_d','security','compliance')),
    -- Lifecycle
    current_eol_date TEXT,
    replacement_urgency TEXT DEFAULT 'planned'
        CHECK(replacement_urgency IN ('immediate','urgent','planned','future')),
    -- Linkage
    linked_opportunity_id TEXT REFERENCES mi_opportunities(id),
    linked_goal_ids TEXT,   -- JSON array
    -- Approval
    priority        TEXT DEFAULT 'medium'
        CHECK(priority IN ('critical','high','medium','low')),
    status          TEXT DEFAULT 'wishlist'
        CHECK(status IN ('wishlist','budgeted','approved','ordered','delivered','cancelled')),
    requestor       TEXT,
    justification   TEXT,
    tags            TEXT,   -- JSON array
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Budget Cycles (annual / multi-year) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS mi_budget_cycles (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    fiscal_year     INTEGER NOT NULL,
    budget_type     TEXT DEFAULT 'annual'
        CHECK(budget_type IN ('annual','multi_year','emergency','supplemental')),
    total_budget_usd REAL,
    allocated_usd   REAL DEFAULT 0,
    committed_usd   REAL DEFAULT 0,
    spent_usd       REAL DEFAULT 0,
    status          TEXT DEFAULT 'planning'
        CHECK(status IN ('planning','approved','executing','closed')),
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Scan Run History ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mi_scans (
    id              TEXT PRIMARY KEY,
    scan_type       TEXT DEFAULT 'full'
        CHECK(scan_type IN ('full','canvas','eol','goal_alignment','budget')),
    airgap_mode     INTEGER DEFAULT 0,
    canvases_scanned TEXT,  -- JSON array
    opportunities_found INTEGER DEFAULT 0,
    opportunities_new   INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    status          TEXT DEFAULT 'running'
        CHECK(status IN ('running','completed','failed','partial')),
    error_msg       TEXT,
    details         TEXT,   -- JSON
    started_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at    TEXT
);

-- ── Indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_mi_opps_score ON mi_opportunities(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_mi_opps_status ON mi_opportunities(status);
CREATE INDEX IF NOT EXISTS idx_mi_opps_type ON mi_opportunities(opportunity_type);
CREATE INDEX IF NOT EXISTS idx_mi_goals_status ON mi_goals(status);
CREATE INDEX IF NOT EXISTS idx_mi_wishlist_fy ON mi_wishlist(fiscal_year, status);
CREATE INDEX IF NOT EXISTS idx_mi_wishlist_urgency ON mi_wishlist(replacement_urgency, status);
CREATE INDEX IF NOT EXISTS idx_mi_scans_started ON mi_scans(started_at DESC);
"""


def _backend() -> str:
    """Resolve the migration-intel storage backend (PG-primary)."""
    from tools.db.storage import resolve_canvas_backend
    return resolve_canvas_backend("MI_STORAGE_BACKEND")


def get_connection(db_path: str | None = None):
    """Return a StorageConnection (RLS disabled) for migration-intel tables.

    cnr-mi-01: previously returned a raw ``sqlite3.connect`` in qmark style while
    every call site used ``%s`` placeholders, which raised OperationalError on
    SQLite and left the whole pipeline non-functional.  Routing through
    StorageConnection makes ``%s`` work on both backends (translate_sql rewrites
    it to ``?`` for SQLite) and, on PostgreSQL, targets the shared icdev db.

    mi_* tables carry no tenant_id/classification columns, so RLS is disabled
    (security_context=None) to avoid UndefinedColumn on PostgreSQL.
    """
    from tools.db.storage import get_connection as _storage_get_connection

    if _backend() == "sqlite":
        path = db_path or os.environ.get(_MI_DB_PATH_ENV, str(_DB_PATH))
        conn = _storage_get_connection(db_path=path)
    else:
        # PG-primary: shared icdev database (mi_* tables namespaced by prefix).
        conn = _storage_get_connection()
    conn.set_security_context(None)  # rls-bypass: canvas tables have no tenant_id/classification columns, so RLS injection would raise UndefinedColumn on every query
    return conn


def init_db(db_path: str | None = None) -> None:
    if _backend() == "sqlite":
        path = db_path or os.environ.get(_MI_DB_PATH_ENV, str(_DB_PATH))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    else:
        path = None
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    print(f"[mi_init_db] Migration Intelligence DB initialized ({_backend()})", file=sys.stderr)
