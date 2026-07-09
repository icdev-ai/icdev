# CUI // SP-CTI
"""Initialize CPMP DB tables (idempotent — safe to call at startup).

Creates all Contract Performance Management Portal tables (Phase 60):
cpmp_contracts, cpmp_clins, cpmp_wbs, cpmp_deliverables, cpmp_status_history,
cpmp_evm_periods, cpmp_subcontractors, cpmp_cpars_assessments,
cpmp_negative_events, cpmp_small_business_plan, cpmp_cdrl_generations,
cpmp_sam_contract_awards, cpmp_cor_access_log, cpmp_contract_mods,
cpmp_milestones, cpmp_milestone_deps (IMS — prop-pm-01).

All DDL uses CREATE TABLE IF NOT EXISTS so this is safe to call on both
fresh SQLite databases (CI/E2E) and production DBs that already have tables.
"""
from __future__ import annotations

_CPMP_CONTRACTS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_contracts (
    id TEXT PRIMARY KEY,
    contract_number TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    agency TEXT NOT NULL DEFAULT '',
    cor_name TEXT,
    cor_email TEXT,
    cor_phone TEXT,
    contract_type TEXT NOT NULL DEFAULT 'FFP',
    idiq_contract_id TEXT,
    task_order_number TEXT,
    naics_code TEXT,
    total_value REAL DEFAULT 0.0,
    funded_value REAL DEFAULT 0.0,
    obligated_value REAL DEFAULT 0.0,
    ceiling_value REAL,
    billed_value REAL DEFAULT 0.0,
    pop_start TEXT,
    pop_end TEXT,
    period_type TEXT DEFAULT 'base',
    option_number INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft',
    health TEXT DEFAULT 'green',
    health_score REAL,
    cpars_rating_current TEXT,
    opportunity_id TEXT,
    customer_delivery_id TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    created_by TEXT,
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_CONTRACT_PERIODS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_contract_periods (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    period_type TEXT NOT NULL DEFAULT 'base',
    option_number INTEGER DEFAULT 0,
    pop_start TEXT,
    pop_end TEXT,
    obligated_value REAL DEFAULT 0.0,
    funded_value REAL DEFAULT 0.0,
    ceiling_value REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'unexercised',
    exercised_at TEXT,
    exercised_by TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
)
"""

_CPMP_CLINS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_clins (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    clin_number TEXT NOT NULL DEFAULT '',
    description TEXT,
    clin_type TEXT NOT NULL DEFAULT 'labor',
    total_value REAL DEFAULT 0.0,
    funded_value REAL DEFAULT 0.0,
    billed_value REAL DEFAULT 0.0,
    pop_start TEXT,
    pop_end TEXT,
    status TEXT DEFAULT 'active',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_WBS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_wbs (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    parent_id TEXT,
    wbs_number TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    description TEXT,
    level INTEGER DEFAULT 1,
    budget_at_completion REAL DEFAULT 0.0,
    pv_cumulative REAL DEFAULT 0.0,
    ev_cumulative REAL DEFAULT 0.0,
    ac_cumulative REAL DEFAULT 0.0,
    percent_complete REAL DEFAULT 0.0,
    planned_start TEXT,
    planned_finish TEXT,
    actual_start TEXT,
    actual_finish TEXT,
    responsible_person TEXT,
    status TEXT DEFAULT 'not_started',
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_DELIVERABLES_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_deliverables (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    clin_id TEXT,
    wbs_id TEXT,
    cdrl_number TEXT,
    did_number TEXT,
    title TEXT NOT NULL DEFAULT '',
    description TEXT,
    deliverable_type TEXT NOT NULL DEFAULT 'cdrl',
    frequency TEXT,
    due_date TEXT,
    submitted_date TEXT,
    accepted_date TEXT,
    rejected_date TEXT,
    rejection_reason TEXT,
    status TEXT DEFAULT 'not_started',
    days_overdue INTEGER DEFAULT 0,
    generated_by_tool TEXT,
    output_path TEXT,
    reviewer TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_STATUS_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT,
    reason TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
)
"""

_CPMP_EVM_PERIODS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_evm_periods (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    wbs_id TEXT,
    period_date TEXT NOT NULL,
    budget_at_completion REAL DEFAULT 0.0,
    bac REAL DEFAULT 0.0,
    planned_value REAL DEFAULT 0.0,
    pv REAL DEFAULT 0.0,
    earned_value REAL DEFAULT 0.0,
    ev REAL DEFAULT 0.0,
    actual_cost REAL DEFAULT 0.0,
    ac REAL DEFAULT 0.0,
    bcws REAL DEFAULT 0.0,
    bcwp REAL DEFAULT 0.0,
    acwp REAL DEFAULT 0.0,
    cpi REAL,
    spi REAL,
    cost_variance REAL,
    cv REAL,
    schedule_variance REAL,
    sv REAL,
    eac REAL,
    etc REAL,
    vac REAL,
    tcpi REAL,
    source TEXT DEFAULT 'manual',
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_SUBCONTRACTORS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_subcontractors (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    cage_code TEXT,
    uei TEXT,
    business_size TEXT,
    business_type TEXT,
    subcontract_type TEXT,
    subcontract_value REAL DEFAULT 0.0,
    billed_value REAL DEFAULT 0.0,
    performance_rating TEXT,
    flow_down_complete INTEGER DEFAULT 0,
    flowdown_verified INTEGER DEFAULT 0,
    cybersecurity_compliant INTEGER DEFAULT 0,
    cmmc_level INTEGER,
    isr_ssr_current INTEGER DEFAULT 0,
    contact_name TEXT,
    contact_email TEXT,
    status TEXT DEFAULT 'active',
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_CPARS_ASSESSMENTS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_cpars_assessments (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    quality_rating REAL,
    schedule_rating REAL,
    cost_rating REAL,
    management_rating REAL,
    small_business_rating REAL,
    overall_rating TEXT,
    overall_score REAL,
    predicted_overall TEXT,
    predicted_score REAL,
    narrative TEXT,
    government_narrative TEXT,
    negative_event_count INTEGER DEFAULT 0,
    corrective_actions_completed INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft',
    submitted_date TEXT,
    finalized_date TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_NEGATIVE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_negative_events (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence TEXT,
    deliverable_id TEXT,
    subcontractor_id TEXT,
    corrective_action TEXT,
    corrective_action_status TEXT DEFAULT 'open',
    corrective_action_due TEXT,
    cpars_impact REAL DEFAULT 0.0,
    detected_by TEXT,
    reported_by TEXT,
    reported_date TEXT DEFAULT (datetime('now')),
    resolved_date TEXT,
    source_entity_type TEXT,
    source_entity_id TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_SMALL_BUSINESS_PLAN_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_small_business_plan (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    reporting_period TEXT NOT NULL,
    report_type TEXT NOT NULL DEFAULT 'isr',
    total_subcontract_dollars REAL DEFAULT 0.0,
    sb_goal_pct REAL DEFAULT 0.0,
    sb_actual_pct REAL DEFAULT 0.0,
    sb_actual_dollars REAL DEFAULT 0.0,
    sdb_goal_pct REAL DEFAULT 0.0,
    sdb_actual_pct REAL DEFAULT 0.0,
    sdb_actual_dollars REAL DEFAULT 0.0,
    wosb_goal_pct REAL DEFAULT 0.0,
    wosb_actual_pct REAL DEFAULT 0.0,
    wosb_actual_dollars REAL DEFAULT 0.0,
    hubzone_goal_pct REAL DEFAULT 0.0,
    hubzone_actual_pct REAL DEFAULT 0.0,
    hubzone_actual_dollars REAL DEFAULT 0.0,
    sdvosb_goal_pct REAL DEFAULT 0.0,
    sdvosb_actual_pct REAL DEFAULT 0.0,
    sdvosb_actual_dollars REAL DEFAULT 0.0,
    compliant INTEGER DEFAULT 0,
    submitted_date TEXT,
    status TEXT DEFAULT 'draft',
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_CDRL_GENERATIONS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_cdrl_generations (
    id TEXT PRIMARY KEY,
    deliverable_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    cdrl_type TEXT NOT NULL,
    generation_tool TEXT NOT NULL,
    tool_args TEXT DEFAULT '{}',
    output_path TEXT,
    output_hash TEXT,
    file_size_bytes INTEGER,
    status TEXT DEFAULT 'generated',
    error_message TEXT,
    generated_by TEXT,
    reviewed_by TEXT,
    approved_by TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_SAM_CONTRACT_AWARDS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_sam_contract_awards (
    id TEXT PRIMARY KEY,
    sam_award_id TEXT NOT NULL UNIQUE,
    piid TEXT,
    referenced_idv_piid TEXT,
    award_type TEXT,
    awardee_name TEXT,
    awardee_uei TEXT,
    awardee_cage TEXT,
    awarding_agency TEXT,
    awarding_sub_agency TEXT,
    funding_agency TEXT,
    naics_code TEXT,
    psc_code TEXT,
    obligation_amount REAL,
    base_exercised_options_value REAL,
    total_dollars_obligated REAL,
    award_date TEXT,
    pop_start TEXT,
    pop_end TEXT,
    place_of_performance TEXT,
    linked_contract_id TEXT,
    content_hash TEXT NOT NULL,
    raw_json TEXT,
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_COR_ACCESS_LOG_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_cor_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    user_email TEXT,
    contract_id TEXT NOT NULL,
    action TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
)
"""

_CPMP_MILESTONES_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_milestones (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    wbs_id TEXT,
    title TEXT NOT NULL DEFAULT '',
    description TEXT,
    baseline_date TEXT,
    forecast_date TEXT,
    actual_date TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN (
        'pending', 'in_progress', 'complete', 'missed', 'on_hold')),
    evm_period_id TEXT,
    responsible_person TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_CPMP_MILESTONE_DEPS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_milestone_deps (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    predecessor_id TEXT NOT NULL,
    successor_id TEXT NOT NULL,
    lag_days INTEGER DEFAULT 0,
    dep_type TEXT DEFAULT 'finish_to_start' CHECK(dep_type IN (
        'finish_to_start', 'start_to_start', 'finish_to_finish', 'start_to_finish')),
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    UNIQUE(predecessor_id, successor_id)
)
"""

_CPMP_CONTRACT_MODS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_contract_mods (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    contract_id TEXT NOT NULL,
    mod_number INTEGER NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('admin','funding','scope','pop')),
    description TEXT NOT NULL DEFAULT '',
    value_delta REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested','in_review','approved','rejected','executed')),
    requested_by TEXT,
    requested_at TEXT NOT NULL DEFAULT (datetime('now','utc')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    approved_by TEXT,
    approved_at TEXT,
    rejection_reason TEXT,
    effective_date TEXT,
    executed_at TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','utc')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','utc')),
    FOREIGN KEY (contract_id) REFERENCES cpmp_contracts(id)
)
"""

_CPMP_RISKS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_risks (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'other' CHECK(category IN (
        'cost', 'schedule', 'technical', 'cyber',
        'supply_chain', 'compliance', 'staffing', 'other')),
    probability INTEGER NOT NULL DEFAULT 3 CHECK(probability BETWEEN 1 AND 5),
    impact INTEGER NOT NULL DEFAULT 3 CHECK(impact BETWEEN 1 AND 5),
    exposure INTEGER NOT NULL DEFAULT 9,
    mitigation TEXT,
    owner TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN (
        'open', 'mitigating', 'accepted', 'closed', 'transferred')),
    milestone_id TEXT,
    negative_event_id TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (contract_id) REFERENCES cpmp_contracts(id),
    FOREIGN KEY (milestone_id) REFERENCES cpmp_milestones(id),
    FOREIGN KEY (negative_event_id) REFERENCES cpmp_negative_events(id)
)
"""

_CPMP_INT_COVERAGE_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_int_coverage (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    discipline TEXT NOT NULL DEFAULT '',
    coverage_area TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'gap' CHECK (status IN ('gap', 'partial', 'covered')),
    confidence REAL DEFAULT 0.0,
    source_type TEXT DEFAULT '',
    notes TEXT,
    last_assessed TEXT,
    persistent_since TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
)
"""

_CPMP_COLLECTION_REQUIREMENTS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_collection_requirements (
    id TEXT PRIMARY KEY,
    coverage_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    requirement_text TEXT NOT NULL DEFAULT '',
    discipline TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('critical', 'high', 'medium', 'low')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'tasked', 'satisfied')),
    ai_generated INTEGER DEFAULT 0,
    tasked_to TEXT,
    tasked_at TEXT,
    satisfied_at TEXT,
    notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
)
"""

_CPMP_OPTION_PERIODS_DDL = """
CREATE TABLE IF NOT EXISTS cpmp_option_periods (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    option_number INTEGER NOT NULL,
    description TEXT,
    period_start TEXT,
    period_end TEXT,
    ceiling_value REAL NOT NULL DEFAULT 0.0,
    exercise_deadline TEXT NOT NULL,
    exercise_notice_days INTEGER NOT NULL DEFAULT 60,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','exercised','lapsed','waived')),
    exercised_date TEXT,
    exercised_by TEXT,
    ai_recommendation TEXT,
    ai_recommendation_ts TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI',
    tenant_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (contract_id) REFERENCES cpmp_contracts(id)
)
"""

_CPMP_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_cpmp_contract_status ON cpmp_contracts(status)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_contract_health ON cpmp_contracts(health)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_contract_opp    ON cpmp_contracts(opportunity_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_clin_contract   ON cpmp_clins(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_wbs_contract    ON cpmp_wbs(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_deliv_contract  ON cpmp_deliverables(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_deliv_status    ON cpmp_deliverables(status)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_deliv_due       ON cpmp_deliverables(due_date)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_hist_entity     ON cpmp_status_history(entity_type, entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_evm_contract    ON cpmp_evm_periods(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_sub_contract    ON cpmp_subcontractors(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_cpars_contract  ON cpmp_cpars_assessments(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_nevent_contract ON cpmp_negative_events(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_cdrl_gen_deliv  ON cpmp_cdrl_generations(deliverable_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_cor_log_contract ON cpmp_cor_access_log(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_contract_mods_contract ON cpmp_contract_mods(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_contract_mods_status ON cpmp_contract_mods(status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cpmp_contract_mods_number ON cpmp_contract_mods(contract_id, mod_number)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_ms_contract   ON cpmp_milestones(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_ms_wbs        ON cpmp_milestones(wbs_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_ms_status     ON cpmp_milestones(status)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_ms_baseline   ON cpmp_milestones(baseline_date)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_msdep_contract ON cpmp_milestone_deps(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_msdep_pred    ON cpmp_milestone_deps(predecessor_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_msdep_succ    ON cpmp_milestone_deps(successor_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_risk_contract  ON cpmp_risks(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_risk_status    ON cpmp_risks(status)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_risk_exposure  ON cpmp_risks(exposure)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_risk_milestone ON cpmp_risks(milestone_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_opt_contract   ON cpmp_option_periods(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_opt_status     ON cpmp_option_periods(status)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_opt_deadline   ON cpmp_option_periods(exercise_deadline)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_cov_contract   ON cpmp_int_coverage(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_cov_status     ON cpmp_int_coverage(status)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_cov_discipline ON cpmp_int_coverage(discipline)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_cov_persistent ON cpmp_int_coverage(persistent_since)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_creq_coverage  ON cpmp_collection_requirements(coverage_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_creq_contract  ON cpmp_collection_requirements(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_creq_status    ON cpmp_collection_requirements(status)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_creq_discipline ON cpmp_collection_requirements(discipline)",
]

_CPMP_INDEXES = _CPMP_INDEXES + [
    "CREATE INDEX IF NOT EXISTS idx_cpmp_periods_contract ON cpmp_contract_periods(contract_id)",
    "CREATE INDEX IF NOT EXISTS idx_cpmp_periods_status   ON cpmp_contract_periods(contract_id, status)",
]

_ALL_DDLS = [
    _CPMP_CONTRACTS_DDL,
    _CPMP_CONTRACT_PERIODS_DDL,
    _CPMP_CLINS_DDL,
    _CPMP_WBS_DDL,
    _CPMP_DELIVERABLES_DDL,
    _CPMP_STATUS_HISTORY_DDL,
    _CPMP_EVM_PERIODS_DDL,
    _CPMP_SUBCONTRACTORS_DDL,
    _CPMP_CPARS_ASSESSMENTS_DDL,
    _CPMP_NEGATIVE_EVENTS_DDL,
    _CPMP_SMALL_BUSINESS_PLAN_DDL,
    _CPMP_CDRL_GENERATIONS_DDL,
    _CPMP_SAM_CONTRACT_AWARDS_DDL,
    _CPMP_COR_ACCESS_LOG_DDL,
    _CPMP_CONTRACT_MODS_DDL,
    _CPMP_MILESTONES_DDL,
    _CPMP_MILESTONE_DEPS_DDL,
    _CPMP_RISKS_DDL,
    _CPMP_OPTION_PERIODS_DDL,
    _CPMP_INT_COVERAGE_DDL,
    _CPMP_COLLECTION_REQUIREMENTS_DDL,
]


def init_cpmp_tables(conn=None) -> dict:
    """Create all CPMP tables. Safe to call on any DB state."""
    from tools.db.storage import get_connection

    _close = conn is None
    if conn is None:
        conn = get_connection()
    try:
        for ddl in _ALL_DDLS:
            conn.execute(ddl)
        for idx in _CPMP_INDEXES:
            conn.execute(idx)
        conn.commit()
        return {"status": "ok", "tables_created": len(_ALL_DDLS)}
    finally:
        if _close:
            conn.close()


# =========================================================================
# GovCon Intelligence tables (Phase 59, D361-D373)
# proposal_opportunities, rfp_shall_statements, rfp_requirement_patterns,
# icdev_capability_map, proposal_section_drafts, proposal_knowledge_base,
# govcon_awards, proposal_compliance_matrix, proposal_status_history,
# proposal_questions, sam_gov_opportunities
# =========================================================================

_GOVCON_SAM_OPPS_DDL = """
CREATE TABLE IF NOT EXISTS sam_gov_opportunities (
    id TEXT PRIMARY KEY,
    solicitation_number TEXT,
    title TEXT NOT NULL DEFAULT '',
    agency TEXT NOT NULL DEFAULT '',
    agency_hierarchy TEXT,
    naics_code TEXT,
    classification_code TEXT,
    notice_type TEXT NOT NULL DEFAULT 'presolicitation',
    posted_date TEXT,
    response_deadline TEXT,
    description TEXT,
    point_of_contact TEXT,
    set_aside_type TEXT,
    place_of_performance TEXT,
    attachment_urls TEXT DEFAULT '[]',
    active TEXT DEFAULT 'true',
    proposal_opportunity_id TEXT,
    content_hash TEXT NOT NULL DEFAULT '',
    metadata TEXT DEFAULT '{}',
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_synced TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_GOVCON_PROP_OPPS_DDL = """
CREATE TABLE IF NOT EXISTS proposal_opportunities (
    id TEXT PRIMARY KEY,
    solicitation_number TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    agency TEXT NOT NULL DEFAULT '',
    sub_agency TEXT,
    due_date TEXT NOT NULL DEFAULT '',
    due_time TEXT DEFAULT '17:00',
    set_aside_type TEXT,
    naics_code TEXT,
    estimated_value_low REAL,
    estimated_value_high REAL,
    proposal_type TEXT NOT NULL DEFAULT 'other',
    status TEXT NOT NULL DEFAULT 'intake',
    bid_decision TEXT,
    bid_decision_date TEXT,
    bid_decision_rationale TEXT,
    rfp_document_path TEXT,
    rfp_url TEXT,
    capture_manager TEXT,
    proposal_manager TEXT,
    domain TEXT DEFAULT 'general',
    classification TEXT DEFAULT 'CUI',
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    licensing_model TEXT,
    sam_gov_opportunity_id TEXT,
    questions_due_date TEXT,
    amendment_count INTEGER DEFAULT 0,
    question_count INTEGER DEFAULT 0,
    contract_id TEXT
)
"""

_GOVCON_SHALL_STMTS_DDL = """
CREATE TABLE IF NOT EXISTS rfp_shall_statements (
    id TEXT PRIMARY KEY,
    sam_opportunity_id TEXT,
    proposal_opportunity_id TEXT,
    statement_text TEXT NOT NULL DEFAULT '',
    statement_type TEXT NOT NULL DEFAULT 'shall',
    domain_category TEXT,
    keywords TEXT DEFAULT '[]',
    keyword_fingerprint TEXT,
    source_section TEXT,
    content_hash TEXT NOT NULL DEFAULT '',
    extracted_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_GOVCON_REQ_PATTERNS_DDL = """
CREATE TABLE IF NOT EXISTS rfp_requirement_patterns (
    id TEXT PRIMARY KEY,
    pattern_name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    domain_category TEXT NOT NULL DEFAULT 'other',
    frequency INTEGER NOT NULL DEFAULT 1,
    shall_statement_ids TEXT NOT NULL DEFAULT '[]',
    sam_opportunity_ids TEXT NOT NULL DEFAULT '[]',
    keyword_fingerprint TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    representative_text TEXT NOT NULL DEFAULT '',
    capability_coverage REAL DEFAULT 0.0,
    icdev_capability_ids TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'new',
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI'
)
"""

_GOVCON_CAP_MAP_DDL = """
CREATE TABLE IF NOT EXISTS icdev_capability_map (
    id TEXT PRIMARY KEY,
    pattern_id TEXT NOT NULL DEFAULT '',
    capability_id TEXT NOT NULL DEFAULT '',
    capability_name TEXT DEFAULT '',
    coverage_score REAL NOT NULL DEFAULT 0.0,
    grade TEXT DEFAULT 'N',
    matched_keywords TEXT DEFAULT '[]',
    created_at TEXT DEFAULT (datetime('now')),
    mapped_at TEXT DEFAULT (datetime('now')),
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI'
)
"""

_GOVCON_SECTION_DRAFTS_DDL = """
CREATE TABLE IF NOT EXISTS proposal_section_drafts (
    id TEXT PRIMARY KEY,
    section_id TEXT,
    opportunity_id TEXT NOT NULL DEFAULT '',
    shall_statement_id TEXT,
    capability_ids TEXT DEFAULT '[]',
    knowledge_block_ids TEXT DEFAULT '[]',
    draft_content TEXT NOT NULL DEFAULT '',
    draft_method TEXT,
    confidence_score REAL DEFAULT 0.0,
    confidence REAL DEFAULT 0.0,
    domain_category TEXT,
    generation_model TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    reviewer_notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_GOVCON_KNOWLEDGE_BASE_DDL = """
CREATE TABLE IF NOT EXISTS proposal_knowledge_base (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'other',
    domain TEXT NOT NULL DEFAULT 'general',
    naics_codes TEXT DEFAULT '[]',
    volume_type TEXT,
    keywords TEXT NOT NULL DEFAULT '[]',
    usage_count INTEGER DEFAULT 0,
    win_rate REAL,
    last_used_at TEXT,
    created_by TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_GOVCON_AWARDS_DDL = """
CREATE TABLE IF NOT EXISTS govcon_awards (
    id TEXT PRIMARY KEY,
    sam_opportunity_id TEXT,
    solicitation_number TEXT,
    title TEXT NOT NULL DEFAULT '',
    agency TEXT NOT NULL DEFAULT '',
    naics_code TEXT,
    awardee_name TEXT NOT NULL DEFAULT '',
    awardee_duns TEXT,
    awardee_uei TEXT,
    contract_number TEXT,
    award_amount REAL,
    award_date TEXT,
    period_of_performance_start TEXT,
    period_of_performance_end TEXT,
    set_aside_type TEXT,
    competitor_id TEXT,
    content_hash TEXT NOT NULL DEFAULT '',
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
)
"""

_GOVCON_COMPLIANCE_MATRIX_DDL = """
CREATE TABLE IF NOT EXISTS proposal_compliance_matrix (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL DEFAULT '',
    section_ref TEXT NOT NULL DEFAULT '',
    volume_ref TEXT,
    requirement_text TEXT NOT NULL DEFAULT '',
    requirement_type TEXT DEFAULT 'L',
    compliance_status TEXT DEFAULT 'not_addressed',
    proposal_section_id TEXT,
    response_summary TEXT,
    notes TEXT,
    sort_order INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
)
"""

_GOVCON_STATUS_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS proposal_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL DEFAULT 'opportunity',
    entity_id TEXT NOT NULL DEFAULT '',
    old_status TEXT,
    new_status TEXT NOT NULL DEFAULT '',
    changed_by TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)
"""

_GOVCON_QUESTIONS_DDL = """
CREATE TABLE IF NOT EXISTS proposal_questions (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL DEFAULT '',
    question_number INTEGER,
    question_text TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'scope',
    priority TEXT NOT NULL DEFAULT 'medium',
    source TEXT NOT NULL DEFAULT 'manual',
    rfp_section_ref TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    ambiguity_trigger TEXT,
    content_hash TEXT,
    created_by TEXT,
    approved_by TEXT,
    approved_at TEXT,
    submitted_at TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
)
"""

# RFI Capability-Gap Demand Loop (rfidem-store-01/02).
# Aggregation store: one row per distinct unmet capability, deduped by content_hash
# across RFIs so recurring gaps accrue frequency/priority. NOT append-only — frequency
# and priority are updated in place (mirrors pulse_demand_signals).
_GOVCON_RFI_GAPS_DDL = """
CREATE TABLE IF NOT EXISTS rfi_capability_gaps (
    content_hash TEXT PRIMARY KEY,
    capability_need TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    domain TEXT,
    frequency INTEGER NOT NULL DEFAULT 1,
    velocity REAL NOT NULL DEFAULT 0.0,
    best_coverage REAL NOT NULL DEFAULT 0.0,
    priority REAL NOT NULL DEFAULT 0.0,
    is_high_demand INTEGER NOT NULL DEFAULT 0,
    rfi_refs TEXT NOT NULL DEFAULT '[]',
    prediction_id TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    classification TEXT DEFAULT 'CUI'
)
"""

# Append-only provenance: which kanban task(s) a gap produced, via which route
# (direct SUGGESTED decomposition vs Foundry harvest). Immutable — see
# APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py. UNIQUE(gap_hash,task_id) makes
# emission idempotent so re-runs never duplicate cards.
_GOVCON_RFI_GAP_LINKS_DDL = """
CREATE TABLE IF NOT EXISTS rfi_gap_task_links (
    id TEXT PRIMARY KEY,
    gap_hash TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    route TEXT NOT NULL DEFAULT 'direct',
    emitted_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI',
    UNIQUE (gap_hash, task_id)
)
"""

_GOVCON_INTELLIGENCE_DDLS = [
    _GOVCON_SAM_OPPS_DDL,
    _GOVCON_PROP_OPPS_DDL,
    _GOVCON_SHALL_STMTS_DDL,
    _GOVCON_REQ_PATTERNS_DDL,
    _GOVCON_CAP_MAP_DDL,
    _GOVCON_SECTION_DRAFTS_DDL,
    _GOVCON_KNOWLEDGE_BASE_DDL,
    _GOVCON_AWARDS_DDL,
    _GOVCON_COMPLIANCE_MATRIX_DDL,
    _GOVCON_STATUS_HISTORY_DDL,
    _GOVCON_QUESTIONS_DDL,
    _GOVCON_RFI_GAPS_DDL,
    _GOVCON_RFI_GAP_LINKS_DDL,
]

_GOVCON_INTELLIGENCE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sam_naics      ON sam_gov_opportunities(naics_code)",
    "CREATE INDEX IF NOT EXISTS idx_sam_agency     ON sam_gov_opportunities(agency)",
    "CREATE INDEX IF NOT EXISTS idx_prop_opp_status ON proposal_opportunities(status)",
    "CREATE INDEX IF NOT EXISTS idx_prop_opp_due    ON proposal_opportunities(due_date)",
    "CREATE INDEX IF NOT EXISTS idx_shall_sam      ON rfp_shall_statements(sam_opportunity_id)",
    "CREATE INDEX IF NOT EXISTS idx_shall_prop     ON rfp_shall_statements(proposal_opportunity_id)",
    "CREATE INDEX IF NOT EXISTS idx_shall_domain   ON rfp_shall_statements(domain_category)",
    "CREATE INDEX IF NOT EXISTS idx_rfp_pattern_domain ON rfp_requirement_patterns(domain_category)",
    "CREATE INDEX IF NOT EXISTS idx_rfp_pattern_freq   ON rfp_requirement_patterns(frequency)",
    "CREATE INDEX IF NOT EXISTS idx_capmap_pattern  ON icdev_capability_map(pattern_id)",
    "CREATE INDEX IF NOT EXISTS idx_draft_opp      ON proposal_section_drafts(opportunity_id)",
    "CREATE INDEX IF NOT EXISTS idx_draft_status   ON proposal_section_drafts(status)",
    "CREATE INDEX IF NOT EXISTS idx_kb_domain      ON proposal_knowledge_base(domain)",
    "CREATE INDEX IF NOT EXISTS idx_kb_status      ON proposal_knowledge_base(status)",
    "CREATE INDEX IF NOT EXISTS idx_award_awardee  ON govcon_awards(awardee_name)",
    "CREATE INDEX IF NOT EXISTS idx_prop_cm_opp    ON proposal_compliance_matrix(opportunity_id)",
    "CREATE INDEX IF NOT EXISTS idx_prop_q_opp     ON proposal_questions(opportunity_id)",
    "CREATE INDEX IF NOT EXISTS idx_prop_q_status  ON proposal_questions(status)",
    "CREATE INDEX IF NOT EXISTS idx_rfi_gaps_priority ON rfi_capability_gaps(priority)",
    "CREATE INDEX IF NOT EXISTS idx_rfi_gaps_status   ON rfi_capability_gaps(status)",
    "CREATE INDEX IF NOT EXISTS idx_rfi_gaps_high     ON rfi_capability_gaps(is_high_demand)",
    "CREATE INDEX IF NOT EXISTS idx_rfi_gap_links_gap ON rfi_gap_task_links(gap_hash)",
]


def init_govcon_intelligence_tables(conn=None) -> dict:
    """Create all GovCon Intelligence tables (Phase 59). Safe to call at startup.

    Creates: sam_gov_opportunities, proposal_opportunities, rfp_shall_statements,
    rfp_requirement_patterns, icdev_capability_map, proposal_section_drafts,
    proposal_knowledge_base, govcon_awards, proposal_compliance_matrix,
    proposal_status_history, proposal_questions, rfi_capability_gaps,
    rfi_gap_task_links.

    All DDL uses CREATE TABLE IF NOT EXISTS — idempotent and safe to call
    on both fresh SQLite databases (CI/E2E) and production DBs.
    """
    from tools.db.storage import get_connection

    _close = conn is None
    if conn is None:
        conn = get_connection()
    try:
        for ddl in _GOVCON_INTELLIGENCE_DDLS:
            conn.execute(ddl)
        for idx in _GOVCON_INTELLIGENCE_INDEXES:
            conn.execute(idx)
        conn.commit()
        return {"status": "ok", "tables_created": len(_GOVCON_INTELLIGENCE_DDLS)}
    finally:
        if _close:
            conn.close()


if __name__ == "__main__":
    result = init_cpmp_tables()
    print(result)
