#!/usr/bin/env python3
# CUI // SP-CTI
"""Down for migration 295 — intentionally a no-op.

Re-narrowing the ``citation_type`` CHECK would invalidate every ``web``
citation already registered, and dropping ``web_fetch_provenance`` would
destroy the provenance those citations validate against. The table is
append-only evidence (NIST AU); a rollback that deletes evidence is not a
rollback. Leaving both in place is harmless — an unused table and a constraint
that permits one more value.
"""


def down(conn):
    return {"status": "noop"}
