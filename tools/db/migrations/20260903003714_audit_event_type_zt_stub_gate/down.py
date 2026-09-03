#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback for 20260903003714_audit_event_type_zt_stub_gate.

Rebuilds the constraint from whatever ``VALID_EVENT_TYPES`` currently holds,
which is the only correct rollback available. Narrowing the CHECK to a
hardcoded "before" list would (a) respell the vocabulary and become the stale
copy the parity test refuses, and (b) leave the constraint rejecting
``zt.stub_gate`` rows the code still writes — turning a rollback into a silent
audit-write failure of a security control.

To genuinely un-admit the event type, remove it from ``VALID_EVENT_TYPES`` and
run this. The constraint follows the constant, in both directions.
"""


def down(conn):
    from tools.audit.audit_logger import rebuild_event_type_constraint

    return {"constraint_rebuilt": rebuild_event_type_constraint(conn)}
