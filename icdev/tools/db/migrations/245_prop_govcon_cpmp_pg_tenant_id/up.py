# CUI // SP-CTI
"""Migration 245 — Backfill tenant_id (+ confirm classification) onto every
proposal/govcon/CPMP/Proposal-Genesis surface table.

These tables (proposal_*, sam_gov_opportunities, rfp_*, cpmp_*, pg_*) were
created before per-tenant RLS was enforced, so most have `classification`
but none have `tenant_id`, and a handful (cpmp_cor_access_log,
cpmp_status_history, and all pg_proposal_genesis*/pg_crm_*/pg_capture_*/
pg_teaming_*/pg_compliance_matrix/pg_review_findings/pg_cost_volumes/
pg_lcat_allocations/pg_cmmc_supply_chain/pg_ai_clause_compliance/
pg_talent_*/pg_win_*/pg_theme_tracking rows) are missing `classification`
too. Adding both makes every query through these tables compatible with
the get_connection() RLS predicate injector (see [[rls-consideration-in-code-generation]]).

Table-existence-graceful: a table that hasn't been created yet on this
database (e.g. a fresh install that hasn't run init_icdev_db.py, or an
older DB missing a newer pg_* table) is skipped rather than raising, so
this migration is safe to run standalone in any order relative to the
tables' owning CREATE TABLE statements.
"""
from tools.db.storage import get_connection, is_pg

# Tables that already have `classification` — only add tenant_id.
TABLES_TENANT_ONLY = [
    "cpmp_cdrl_generations", "cpmp_clins", "cpmp_contracts",
    "cpmp_cpars_assessments", "cpmp_deliverables", "cpmp_evm_periods",
    "cpmp_milestone_deps", "cpmp_milestones", "cpmp_negative_events",
    "cpmp_sam_contract_awards", "cpmp_small_business_plan",
    "cpmp_subcontractors", "cpmp_wbs",
    "proposal_opportunities", "proposal_volumes", "proposal_sections",
    "proposal_section_dependencies", "proposal_compliance_matrix",
    "proposal_reviews", "proposal_review_findings", "proposal_section_drafts",
    "proposal_knowledge_base", "proposal_amendments", "proposal_competitors",
    "proposal_questions", "proposal_question_responses", "proposal_shred_items",
    "proposal_teaming_partners", "proposal_versions", "proposal_status_history",
    "proposal_reviewer_assignments", "proposal_taxonomy",
    "sam_gov_opportunities", "rfp_shall_statements", "rfp_requirement_patterns",
]

# Tables missing both `tenant_id` and `classification`.
TABLES_BOTH = [
    "cpmp_cor_access_log", "cpmp_status_history",
    "pg_proposal_genesis_audit", "pg_proposal_genesis_state",
    "pg_proposal_genesis_config", "pg_amendment_diffs",
    "pg_pulse_proposal_links", "pg_proposal_quality_scores",
    "pg_bid_decisions", "pg_bid_decision_outcomes", "pg_win_loss_records",
    "pg_win_loss_lessons", "pg_crm_accounts", "pg_crm_contacts",
    "pg_crm_interactions", "pg_crm_engagement_scores", "pg_capture_plans",
    "pg_capture_activities", "pg_capture_gate_decisions", "pg_teaming_partners",
    "pg_teaming_assessments", "pg_compliance_matrix", "pg_review_findings",
    "pg_cost_volumes", "pg_lcat_allocations", "pg_cmmc_supply_chain",
    "pg_ai_clause_compliance", "pg_talent_signals", "pg_talent_velocity",
    "pg_win_themes", "pg_theme_tracking", "pg_teaming_workshare",
]

_MISSING_TABLE_MARKERS = ("no such table", "does not exist", "undefined table", "relation")


def _table_exists(conn, table: str, pg: bool) -> bool:
    if pg:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s AND table_schema = 'public'",
            (table,),
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = %s", (table,)
    ).fetchone()
    return row is not None


def _add_column(conn, table: str, column: str, ddl_suffix: str, pg: bool) -> None:
    if pg:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl_suffix}")
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_suffix}")
    except Exception as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def up(conn=None) -> dict:
    conn = get_connection()
    pg = is_pg()

    altered = []
    skipped_missing = []

    for table in TABLES_TENANT_ONLY:
        if not _table_exists(conn, table, pg):
            skipped_missing.append(table)
            continue
        _add_column(conn, table, "tenant_id", "TEXT", pg)
        altered.append(table)

    for table in TABLES_BOTH:
        if not _table_exists(conn, table, pg):
            skipped_missing.append(table)
            continue
        _add_column(conn, table, "tenant_id", "TEXT", pg)
        _add_column(conn, table, "classification", "TEXT DEFAULT 'CUI'", pg)
        altered.append(table)

    conn.commit()
    conn.close()

    return {
        "status": "applied",
        "altered": altered,
        "skipped_missing_table": skipped_missing,
    }


if __name__ == "__main__":
    result = up()
    print(f"Migration 245 applied: {len(result['altered'])} tables altered, "
          f"{len(result['skipped_missing_table'])} skipped (table not present).")
