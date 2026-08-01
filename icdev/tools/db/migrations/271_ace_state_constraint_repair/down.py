#!/usr/bin/env python3
# CUI // SP-CTI
"""Down for migration 271 — intentionally a no-op.

See up.py: re-narrowing the CHECK constraints would reintroduce the
CheckViolation bug and reversing the zombie sweep would resurrect zombie
instances. Nothing to undo.
"""


def down(conn):
    return {"status": "noop"}
