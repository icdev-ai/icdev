#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback for 20261011222631_audit_event_types_re_analysis.

Idempotent-forward, for the reason 20260808064841's down.py gives: the CHECK
follows `VALID_EVENT_TYPES` in both directions, so to un-admit the two event
types remove them from the constant and run this.
"""


def down(conn):
    from tools.audit.audit_logger import rebuild_event_type_constraint

    return {"constraint_rebuilt": rebuild_event_type_constraint(conn)}
