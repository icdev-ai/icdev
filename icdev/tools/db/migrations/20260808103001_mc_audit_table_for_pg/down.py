#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback for 20260808103001_mc_audit_table_for_pg — deliberately a no-op.

`mc_audit` is an append-only audit table (NIST AU); dropping it would destroy the
trail it exists to preserve, and an unused table is harmless. Narrowing the
`audit_trail.event_type` CHECK back would orphan any `migration_canvas` rows
already written under it. Roll the application code back instead.
"""


def down(conn):  # noqa: ARG001 - signature fixed by the runner
    return {"skipped": "append-only audit schema is not rolled back"}
