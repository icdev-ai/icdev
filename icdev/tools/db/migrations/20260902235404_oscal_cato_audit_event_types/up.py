#!/usr/bin/env python3
# CUI // SP-CTI
"""Admit the two compliance audit event types that were never admitted (rmf-cyc-01).

`oscal_generated` and `cato_evidence_collected` are added to
`tools.audit.audit_logger.VALID_EVENT_TYPES`. Both have been WRITTEN since their
producers were authored — `oscal_generator._log_audit` and
`cato_monitor._log_audit_event` — and neither was ever in the vocabulary, so the
CHECK on `audit_trail.event_type` refused every row. Each writer wraps its INSERT
in `except Exception: print("Warning: ...", file=sys.stderr)`, which sends the
refusal to a stream nothing reads, and the generator then returns success.

MEASURED 2026-09-02, both backends: on the live PostgreSQL board the CHECK names
neither value, and on a database freshly built by `init_icdev_db.py` a full
OSCAL SSP generation plus a cATO evidence collection produced exactly two
warnings on stderr and zero audit rows. Every OSCAL artifact this platform has
ever generated, and every piece of continuous-monitoring evidence it has ever
collected, is unaudited — under NIST AU, on the two artifact chains an ATO
package is assembled from.

Adding the names to the tuple only changes what a FRESH database declares; an
existing table keeps the CHECK it was created with, which is what this migration
rewrites. Following 20260819021003 and 20260821045946.

`rebuild_event_type_constraint` is a no-op on SQLite (a CHECK cannot be ALTERed
there); fresh SQLite databases pick the names up from `init_icdev_db.py`, which
generates the constraint from the same tuple.
"""
from __future__ import annotations


def up(conn):
    """Regenerate audit_trail's event_type CHECK from VALID_EVENT_TYPES."""
    from tools.audit.audit_logger import rebuild_event_type_constraint

    rebuild_event_type_constraint(conn)


def down(conn):
    """Rebuilding the constraint is its own inverse: reverting the code reverts
    the constraint.

    Note that narrowing it again will FAIL on PostgreSQL once rows carrying
    either name exist — PostgreSQL validates a new CHECK against existing rows.
    That is correct: deleting the audit record of a generated compliance
    artifact to make a rollback succeed is not a rollback.
    """
    from tools.audit.audit_logger import rebuild_event_type_constraint

    rebuild_event_type_constraint(conn)
