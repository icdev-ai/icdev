#!/usr/bin/env python3
# CUI // SP-CTI
"""Admit the SBOM distribution audit event types (sbx-gov-02).

`tools/compliance/sbom_distribution.py` records every retrieval of an SBOM
artifact — granted and denied alike — because Distribution and Delivery under
the 2026 SBOM Minimum Elements is a *controlled* sharing element: the record of
who received which version is the evidence that access control limited sharing
with unauthorized parties without blocking authorized ones.

`audit_trail.event_type` carries a CHECK derived from
`tools.audit.audit_logger.VALID_EVENT_TYPES`. Adding names to that tuple only
changes what a *fresh* database declares — an existing PostgreSQL table keeps
the CHECK it was created with, so the INSERT would raise, the caller's
best-effort `except` would swallow it, and distribution would report success
while recording nothing. That is exactly the failure mode CLAUDE.md documents
for `module_budget_usage` and `tools/govcon`. Hence this migration.

`rebuild_event_type_constraint` is a no-op on SQLite (a CHECK cannot be
ALTERed there, and rebuilding an append-only hash-chained table to change one
is not worth it); fresh SQLite databases pick the new names up from
`init_icdev_db.py`, which generates the constraint from the same tuple.
"""
from __future__ import annotations


def up(conn):
    """Regenerate audit_trail's event_type CHECK from VALID_EVENT_TYPES.

    The connection is the runner's — it owns the surrounding transaction, so
    this must not open its own or close the one it was handed.
    """
    from tools.audit.audit_logger import rebuild_event_type_constraint

    rebuild_event_type_constraint(conn)


def down(conn):
    """Rebuilding the constraint is its own inverse.

    There is no "remove these two names" step: `down` regenerates the CHECK
    from whatever `VALID_EVENT_TYPES` says at the time it runs. Reverting the
    code reverts the constraint.
    """
    from tools.audit.audit_logger import rebuild_event_type_constraint

    rebuild_event_type_constraint(conn)
