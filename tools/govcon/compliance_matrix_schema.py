# CUI // SP-CTI
"""The ONE compliance matrix vocabulary (rmf-rfp-01).

`proposal_compliance_matrix` is the authoritative L/M matrix. It is the table
the /api/proposals compliance routes, the govcon auto-populate route, the
proposal detail pages and the IQE adapter read and write — measured on the live
board 2026-09-03 at 499 rows. `pg_compliance_matrix` was a second table with the
same purpose: compliance_matrix_builder.py was its only writer and that writer
had ZERO callers, so it held 0 rows while five readers (opportunity_lifecycle,
color_review_simulator, program_bridge, and the proposal_genesis bridge/trace
reflexes) computed coverage over it. Two matrices is how a coverage number
silently describes a subset; migration 20260903185253 folds the second into the
first and drops it.

This module holds nothing but constants so that the DDL in init_icdev_db.py, the
migration, the builder and the API can all derive their CHECK constraints and
status maps from the same tuples (CLAUDE.md: "SQL CHECK constraints: derive from
Python constants, never hardcode"). It must stay import-light — init_icdev_db.py
imports it at module load.
"""
from __future__ import annotations

MATRIX_TABLE = "proposal_compliance_matrix"

# Where a requirement came from. L/M/N and `other` are the original proposal
# vocabulary (N was, and still is, used by the auto-populate route as a GRADE —
# that conflation predates this card and is left as found). C, attachment and
# amendment are the sources the builder extracts that the proposal table could
# not previously spell.
REQUIREMENT_TYPES = ("L", "M", "N", "other", "C", "attachment", "amendment")

# How well the proposal addresses a requirement.
COMPLIANCE_STATUSES = (
    "compliant", "partial", "non_compliant", "not_applicable", "not_addressed",
)

# The two statuses every coverage ratio in the tree counts as "addressed".
ADDRESSED_STATUSES = ("compliant", "partial")

# The vocabulary the dropped pg_compliance_matrix used, mapped onto the one
# above. Read by the migration when it carries rows across, and by nothing else.
LEGACY_STATUS_MAP = {
    "addressed": "compliant",
    "partial": "partial",
    "gap": "not_addressed",
    "na": "not_applicable",
}

# The columns the migration ADDS to proposal_compliance_matrix so the builder's
# Section M evaluation metadata and amendment tracking are not lost in the fold.
ADDED_COLUMNS = (
    ("evaluation_factor", "TEXT"),
    ("evaluation_weight", "REAL"),
    ("amendment_version", "INTEGER DEFAULT 0"),
)


def sql_in_list(values) -> str:
    """Render a tuple as the body of a SQL IN (...) list."""
    return ", ".join(f"'{v}'" for v in values)
