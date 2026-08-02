#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 327: re-derive audit_trail.event_type CHECK from VALID_EVENT_TYPES.

Migration 318 reconciled the constraint with the constant. This run picks up
the govcon event types added since, in two groups:

* ``govcon.procurement_vehicle`` — tools/govcon/procurement_vehicles.py wrote
  its audit row against four columns audit_trail does not have and omitted the
  NOT NULL ``event_type`` entirely, so both its INSERT and its "schema differs"
  fallback raised and were swallowed.
* 28 verb-level types (``igce.*``, ``teaming.*``, ``pwin.compute`` …) passed at
  the call sites of the twelve govcon modules whose ``_audit`` takes
  ``event_type`` as a parameter. None were admitted either, so those writes
  were rejected and swallowed too — invisible to a search for ``govcon.*``.

The body is deliberately just the rebuild call: it regenerates the whole CHECK
from VALID_EVENT_TYPES, so it stays correct however many types were added. That
is the template for every future addition — add the string to the constant,
then add a migration whose whole body is the call below.
tests/test_audit_event_type_parity.py fails if the constant moves without one.
"""
from __future__ import annotations

from tools.audit.audit_logger import (
    VALID_EVENT_TYPES,
    rebuild_event_type_constraint,
)
from tools.db.storage import get_connection

NAME = "327_audit_event_type_procurement_vehicle"


def up(conn=None) -> None:
    """Rebuild the constraint from the constant.

    ``conn`` is optional and caller-owned: bootstrap_pg.py and the migration
    runner pass their own connection, and closing it here would break the rest
    of their run. Only a connection opened locally is closed locally.
    """
    owned = conn is None
    if owned:
        conn = get_connection()
    try:
        if rebuild_event_type_constraint(conn):
            print(
                f"[{NAME}] up: audit_trail_event_type_check rebuilt with "
                f"{len(VALID_EVENT_TYPES)} event types"
            )
        else:
            print(
                f"[{NAME}] up: SQLite — no-op "
                "(CHECK is generated at schema-init time)"
            )
    finally:
        if owned:
            conn.close()


if __name__ == "__main__":
    up()
