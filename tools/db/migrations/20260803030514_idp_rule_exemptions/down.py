# CUI // SP-CTI
"""Migration 20260803030514 rollback — drop idp_rule_exemptions.

Dropping this table destroys the record of who waived what and why, which is
the only durable evidence an exemption ever leaves. Nothing else in the
platform holds it: ``component_audit_log`` receives a best-effort mirror of
each event, but that mirror is a convenience for the component-audit surface
and is not guaranteed to have landed.

So this rollback is for a failed migration on a fresh database, not for
routine use. Every reader — ``tools/idp/exemptions.py`` and the scorecard
evaluator — degrades to "no exemptions" when the table is absent, so the
platform stays up; it simply stops honouring every waiver at once, which will
fail components that were legitimately exempt.
"""
from __future__ import annotations

from tools.db.storage import get_connection


def down(conn=None) -> dict:
    own = conn is None
    conn = conn or get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS idp_rule_exemptions")
        conn.commit()
    finally:
        if own:
            conn.close()
    return {"status": "rolled_back", "dropped": ["idp_rule_exemptions"]}


if __name__ == "__main__":
    print(down())
