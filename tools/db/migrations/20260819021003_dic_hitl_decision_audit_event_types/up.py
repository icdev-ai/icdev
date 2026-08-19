#!/usr/bin/env python3
# CUI // SP-CTI
"""Admit the DIC human-in-the-loop audit event types (cef-ui-03).

Two names are added to `tools.audit.audit_logger.VALID_EVENT_TYPES`:

* `dic.hitl_decision` — the new record that a HUMAN disposed of a
  resolve-produced proposal, written by the three existing decision doors
  (`/api/modernization/findings/<id>/resolve`, `/api/suggestions/<id>/accept`
  and `/reject`, `/api/review/<id>/approve` and `/reject`).
* `dic.ssp_fragment.review` — NOT new code. `acoic._review_fragment` has passed
  this string to `log_event` since it was written, and the name was never in the
  vocabulary, so every human SSP-fragment approval raised `ValueError` on the
  first line of `log_event` and never reached the database. It is called with
  `raise_on_error=True` exactly so an unaudited approval cannot stand — but the
  route's `except Exception:` fallback caught the refusal and ran an unaudited
  UPDATE instead. Admitting the name is what makes that fail-closed audit real.

`audit_trail.event_type` carries a CHECK derived from that tuple. Adding names
to the tuple only changes what a *fresh* database declares — an existing
PostgreSQL table keeps the CHECK it was created with, so the INSERT would raise
at runtime. Hence this migration, following 20260808071512.

`rebuild_event_type_constraint` is a no-op on SQLite (a CHECK cannot be ALTERed
there, and rebuilding an append-only hash-chained table to change one is not
worth it); fresh SQLite databases pick the new names up from
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

    There is no "remove these two names" step: `down` regenerates the CHECK from
    whatever `VALID_EVENT_TYPES` says at the time it runs. Reverting the code
    reverts the constraint.
    """
    from tools.audit.audit_logger import rebuild_event_type_constraint

    rebuild_event_type_constraint(conn)
