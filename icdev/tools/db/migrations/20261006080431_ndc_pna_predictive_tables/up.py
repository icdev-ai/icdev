#!/usr/bin/env python3
# CUI // SP-CTI
"""Create the seven PNA (Predictive Network Analytics) tables on PostgreSQL (ndc-pna-page).

WHY THEY NEVER EXISTED. The DDL has always been in
``tools/network/db/init_db.py``'s schema, but on PostgreSQL ``init_db()`` splits
that schema on ``;`` and SKIPS every chunk that starts with ``--``. Each PNA
``CREATE TABLE`` is preceded by a comment line, so each was skipped, silently,
on every start. (The canvas-local ``tools/network/db/migrations/222_pna_predictive.sql``
is applied by nothing.) Measured 2026-10-06 on the live board: all seven tables
absent, and every ``/network/api/network/predict/*`` read 500'd.

THE DDL IS THE CANVAS'S OWN, NOT A SECOND COPY. Statements are taken from the
rendered ``init_db.SCHEMA`` -- the same text SQLite installs get -- so the
CHECK constraints stay DERIVED from the Python constants (cvx-sql-04) and the
column types match what the predictors write (``has_active_cves`` is written
as 0/1, which the BOOLEAN in 222_pna_predictive.sql would refuse).

POSTGRESQL ONLY. On SQLite the network canvas lives in its own
``data/network_canvas.db``, where ``init_db()``'s ``executescript`` already
creates these tables; this migration's connection is the platform database,
so there it is a stated no-op. Every statement is ``IF NOT EXISTS``, so a
re-run, or a board where the tables were made by hand, is a no-op too.
"""
from __future__ import annotations

import re

PNA_TABLES = (
    "nc_eol_predictions",
    "nc_bgp_events",
    "nc_bgp_predictions",
    "nc_compliance_drift",
    "nc_capacity_predictions",
    "nc_change_risk",
    "nc_supply_chain_risk",
)

_TABLE_RE = re.compile(r"^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.I)
_INDEX_RE = re.compile(r"^CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+\w+\s+ON\s+(\w+)\s*\(", re.I)


def pna_statements(schema: str | None = None) -> list[str]:
    """The CREATE TABLE / CREATE INDEX statements for the PNA tables, in order."""
    if schema is None:
        from tools.network.db.init_db import SCHEMA as schema  # noqa: N811
    out: list[str] = []
    for chunk in schema.split(";"):
        stmt = "\n".join(
            ln for ln in chunk.splitlines() if not ln.strip().startswith("--")
        ).strip()
        m = _TABLE_RE.match(stmt) or _INDEX_RE.match(stmt)
        if m and m.group(1) in PNA_TABLES:
            out.append(stmt)
    created = {_TABLE_RE.match(s).group(1) for s in out if _TABLE_RE.match(s)}
    missing = set(PNA_TABLES) - created
    if missing:
        # Loud, not silent: a schema edit that drops one of these must not
        # turn this migration into a partial success.
        raise RuntimeError(f"PNA DDL not found in init_db.SCHEMA for: {sorted(missing)}")
    return out


def up(conn) -> dict:
    if getattr(conn, "_backend", "sqlite") != "postgresql":
        return {"applied": False, "reason": "sqlite_uses_network_canvas_db"}
    stmts = pna_statements()
    for stmt in stmts:
        conn.execute(stmt)
    return {"applied": True, "tables": list(PNA_TABLES), "statements": len(stmts)}
