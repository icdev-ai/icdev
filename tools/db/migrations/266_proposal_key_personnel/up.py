#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 266: proposal_key_personnel — the bid-side people->LCAT table.

Before this, the bid side had no people table at all:
  - pg_lcat_allocations is task -> LCAT -> FTE and never names a human.
  - pma_personnel (tools/govcon/personnel_manager.py) is POST-AWARD, keyed on
    contract_id, so it cannot hold anyone until after we win.
  - program_bridge._gather_key_personnel regex-scraped names out of the free
    text of proposal_section_drafts.

Creates proposal_key_personnel: opportunity-scoped proposed staff, their
proposed LCAT, the qualification verdict against the solicitation's bar, and
the evidence behind it. Append-only (verdict changes and withdrawals append a
new revision), with classification/tenant_id from the start so it is compatible
with the get_connection() RLS predicate injector rather than needing a retrofit
like migration 245 had to do for every other pg_*/proposal_* table.

The DDL is imported from tools.govcon.key_personnel so the CHECK constraints
stay derived from the Python constants (QUALIFICATION_VERDICTS, PERSON_SOURCES,
PERSON_STATUSES) instead of being hardcoded here and drifting.

Idempotent: CREATE TABLE / CREATE INDEX IF NOT EXISTS.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

MIGRATION_ID = "266"
MIGRATION_NAME = "proposal_key_personnel"
DESCRIPTION = "Add proposal_key_personnel (bid-side people->LCAT registry, append-only)"


def up(conn) -> dict:
    from tools.govcon.key_personnel import _table_sql

    conn.execute(_table_sql())
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pkp_opp ON proposal_key_personnel(opportunity_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pkp_ref ON proposal_key_personnel(opportunity_id, person_ref)"
    )
    conn.commit()
    return {"created": ["proposal_key_personnel"], "indexes": ["idx_pkp_opp", "idx_pkp_ref"]}
