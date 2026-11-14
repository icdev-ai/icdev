# CUI // SP-CTI
"""Migration 20261114175314 — cortex_audit hash chain and triggers (icx-cnf-06).

Adds ``chain_seq``, ``prev_hash`` and ``row_hash`` to ``cortex_audit``, the
``cortex_audit_chain_genesis`` table with its one genesis row, and BEFORE
UPDATE / BEFORE DELETE triggers on both tables (SQLite and PostgreSQL). Also
makes sure ``source_citation_registry`` exists, which the Cortex provenance gate
writes to and which only the blockchain migration created before.

Existing ``cortex_audit`` rows are never rewritten: they keep NULL chain
columns, and the genesis row records their count and a digest over them, so
``audit_chain.verify`` can still tell if one changes.

The work is ``tools.cortex.db.audit_chain.ensure_chain`` and
``tools.provenance.registry.ensure_schema``, imported rather than restated, the
same code the first audit write runs on a database that never ran this.
Idempotent.
"""
from __future__ import annotations

_TAG = "[20261114175314_cortex_audit_hash_chain]"


def up(conn) -> dict:
    from tools.cortex.db import audit_chain
    from tools.cortex.db.init_db import init_db
    from tools.db.storage import table_exists
    from tools.provenance.registry import ensure_schema as ensure_registry

    ensure_registry(conn)
    conn.commit()
    if not table_exists(conn, audit_chain.TABLE):
        init_db(conn)  # creates the cortex tables, then the chain on them
    result = audit_chain.ensure_chain(conn)
    print(f"{_TAG} chain columns added {result['columns_added'] or 'none (present)'}, "
          f"genesis {result['genesis'] or 'NOT recorded'}, "
          f"triggers installed on {result['triggers_installed'] or 'none (present)'}")
    return {"status": "applied", **result}
