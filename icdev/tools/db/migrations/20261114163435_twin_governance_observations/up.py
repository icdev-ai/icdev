# CUI // SP-CTI
"""Migration 20261114163435 — governance twin observations (icx-cnf-03).

``twin_governance_observations`` holds one append-only row per governance-twin
observation: the evidence a twin read, its digest, and the verdict's digest, so
``tools.twin_core.governance.replay`` can re-judge it later and say whether it
still comes out the same.

The DDL and the immutability triggers are the ones
``tools/twin_core/governance.py`` runs on first write, imported rather than
restated so the two cannot disagree. Idempotent: CREATE ... IF NOT EXISTS, and
the PostgreSQL triggers are dropped before each create.
"""
from __future__ import annotations

_TAG = "[20261114163435_twin_governance_observations]"


def up(conn) -> dict:
    from tools.twin_core.governance import TABLE, ensure_schema

    ensure_schema(conn)
    print(f"{_TAG} {TABLE} and its no-update/no-delete triggers installed")
    return {"status": "applied", "table": TABLE}
