#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 20260905070028: create ``floci_twin_snapshots`` (flx-twin-01).

Twin snapshot storage is PER-TWIN in this tree — every adapter that persists
declares its own ``<key>_twin_snapshots`` table (``odc_twin_snapshots``,
``idc_twin_snapshots``, ``qdc_twin_snapshots``, ...). The floci adapter joins
that convention.

A PYTHON MIGRATION, and the reason is the ``provenance`` CHECK. The permitted
vocabulary is DERIVED from ``twin_core.schema.SNAPSHOT_PROVENANCES`` rather
than respelled as a SQL literal: a hand-written CHECK is a second copy of the
rule, and its failure mode is silent — the constant gains a value, the CHECK
does not, and the INSERT raises inside whatever ``except`` happens to surround
it. The guardrail is stated in CLAUDE.md ("SQL CHECK constraints: derive from
Python constants, never hardcode").

The CHECK is the STRUCTURAL half of the card's provenance rule. The writer
hard-wires ``emulated`` and a test reads its AST to prove no caller-supplied
value reaches it; this makes the database refuse one as well, so a future edit
that slips past both the writer and its test still cannot record an emulated
estate as an observed one.

NOT append-only: a snapshot of an emulator is a re-derivable observation of a
disposable local container, not an audit record. It is deliberately absent from
``APPEND_ONLY_TABLES``.
"""
from __future__ import annotations

from tools.db.storage import get_connection
from tools.twin_core.schema import DEFAULT_SNAPSHOT_PROVENANCE, SNAPSHOT_PROVENANCES

#: The table name is spelled as a LITERAL in the CREATE TABLE below, not
#: interpolated from here. ``tools/db/schema_ownership.py`` reads migrations as
#: text to find which tables a migration declares, so an f-string target is a
#: table it cannot see — and an undiscovered table has no owner, silently.
_TABLE = "floci_twin_snapshots"


def _provenance_check() -> str:
    """``CHECK (provenance IN (...))`` derived from the canonical constant."""
    values = ", ".join(f"'{p}'" for p in SNAPSHOT_PROVENANCES)
    return f"CHECK (provenance IN ({values}))"


def _ddl(backend: str) -> str:
    ts = "TIMESTAMPTZ NOT NULL DEFAULT NOW()" if backend == "postgresql" else \
        "TEXT NOT NULL DEFAULT (datetime('now'))"
    return f"""
CREATE TABLE IF NOT EXISTS floci_twin_snapshots (
    id              TEXT    NOT NULL,
    target_id       TEXT    NOT NULL,
    label           TEXT    NOT NULL DEFAULT '',
    -- 'emulated', never 'discovery'. An emulated estate must never be readable
    -- as an observed one (the ni_devices.source vocabulary, rmf-disc-02).
    provenance      TEXT    NOT NULL DEFAULT '{DEFAULT_SNAPSHOT_PROVENANCE}' {_provenance_check()},
    target_csp      TEXT,
    region          TEXT,
    -- pass | warn | fail | unknown. `unknown` is the DEFAULT: a row written by
    -- a future writer that forgot to score must not read as a clean bill of
    -- health.
    verdict         TEXT    NOT NULL DEFAULT 'unknown',
    verdict_basis   TEXT    NOT NULL DEFAULT 'unmeasured',
    -- NULLABLE on purpose. A snapshot over an unreachable emulator counted
    -- nothing; 0 would say the emulated estate is empty, which is a different
    -- finding with a different repair.
    resource_count  INTEGER,
    tables_ok       INTEGER,
    tables_declared INTEGER,
    payload_json    TEXT    NOT NULL DEFAULT '{{}}',
    created_at      {ts},
    PRIMARY KEY (id)
)
"""


_IDX_TARGET = (
    f"CREATE INDEX IF NOT EXISTS idx_floci_twin_snap_target ON {_TABLE}(target_id)"
)
_IDX_CREATED = (
    f"CREATE INDEX IF NOT EXISTS idx_floci_twin_snap_created ON {_TABLE}(created_at)"
)


def up(conn=None) -> None:
    owned = conn is None
    conn = conn or get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        conn.execute(_ddl(backend))
        conn.execute(_IDX_TARGET)
        conn.execute(_IDX_CREATED)
        conn.commit()
        print(f"[20260905070028_floci_twin_snapshots] up: {_TABLE} created (or already exists)")
    finally:
        if owned:
            conn.close()


if __name__ == "__main__":
    up()
