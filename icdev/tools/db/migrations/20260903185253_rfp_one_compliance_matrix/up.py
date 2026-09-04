#!/usr/bin/env python3
# CUI // SP-CTI
"""ONE compliance matrix: fold pg_compliance_matrix into proposal_compliance_matrix (rmf-rfp-01).

Two tables held "the" L/M compliance matrix. `proposal_compliance_matrix` is
what the /api/proposals compliance routes, the govcon auto-populate route, the
proposal detail pages and the IQE adapter read and write — 499 rows on the live
board, measured 2026-09-03. `pg_compliance_matrix` was written by exactly one
module, compliance_matrix_builder.py, which had ZERO callers, so it held 0 rows
— while opportunity_lifecycle (the MAP->DRAFT coverage gate), the color-team
review simulator, program_bridge's CDRL gatherer and the proposal_genesis
bridge/trace reflexes all computed coverage over it. Every one of those readers
therefore reported "no compliance matrix entries" for opportunities whose
matrix had hundreds of rows in the other table. Two matrices is how a coverage
number silently describes a subset.

This migration makes proposal_compliance_matrix the ONE table:

  1. ADD the three columns the builder's rows carried that the proposal table
     could not (evaluation_factor, evaluation_weight, amendment_version), so
     nothing is lost in the fold. Idempotent on both backends.
  2. WIDEN the requirement_type CHECK from (L, M, N, other) to the union with
     the builder's source sections (C, attachment, amendment). The constraint
     is regenerated from REQUIREMENT_TYPES in
     tools/govcon/compliance_matrix_schema.py — the same tuple
     init_icdev_db.py derives the fresh-database DDL from. PostgreSQL only:
     SQLite cannot ALTER a CHECK, and a fresh SQLite database picks the new
     vocabulary up from init_icdev_db.py (the same shape as 20260902235404).
  3. COPY any pg_compliance_matrix rows across, translating the vocabulary
     (addressed->compliant, gap->not_addressed, na->not_applicable,
     source_section->requirement_type, assigned_volume->volume_ref), skipping
     a row whose (opportunity_id, requirement_text) already exists — the same
     dedupe key every writer of the surviving table uses. On the live board
     this copies nothing, and that is the measurement, not an assumption.
  4. DROP pg_compliance_matrix. Its DDL leaves init_icdev_db.py in the same
     change, so it cannot come back on a fresh database either.

All four steps are table-existence-graceful: an older database that never
created pg_compliance_matrix, or a fresh one that created neither, runs
whichever steps apply and reports what it did.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.db.storage import is_pg, table_exists
from tools.govcon.compliance_matrix_schema import (
    ADDED_COLUMNS,
    LEGACY_STATUS_MAP,
    MATRIX_TABLE,
    REQUIREMENT_TYPES,
    sql_in_list,
)

LEGACY_TABLE = "pg_compliance_matrix"
_PG_TYPE_CHECK = "proposal_compliance_matrix_requirement_type_check"


def _columns(conn, table: str, pg: bool) -> set:
    if pg:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
        return {(r["column_name"] if hasattr(r, "keys") else r[0]) for r in rows}
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {(r["name"] if hasattr(r, "keys") else r[1]) for r in rows}


def _add_columns(conn, pg: bool) -> list:
    present = _columns(conn, MATRIX_TABLE, pg)
    added = []
    for name, ddl in ADDED_COLUMNS:
        if name in present:
            continue
        conn.execute(f"ALTER TABLE {MATRIX_TABLE} ADD COLUMN {name} {ddl}")
        added.append(name)
    return added


def _widen_check(conn, pg: bool) -> str:
    if not pg:
        return "skipped_sqlite"
    conn.execute(f"ALTER TABLE {MATRIX_TABLE} DROP CONSTRAINT IF EXISTS {_PG_TYPE_CHECK}")
    conn.execute(
        f"ALTER TABLE {MATRIX_TABLE} ADD CONSTRAINT {_PG_TYPE_CHECK} "
        f"CHECK (requirement_type IN ({sql_in_list(REQUIREMENT_TYPES)}))"
    )
    return "rebuilt"


def _copy_rows(conn) -> dict:
    rows = conn.execute(f"SELECT * FROM {LEGACY_TABLE}").fetchall()
    copied = 0
    skipped = 0
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        row = dict(r)
        opp_id = row.get("opportunity_id")
        text = (row.get("requirement_text") or "")[:2000]
        existing = conn.execute(
            f"SELECT id FROM {MATRIX_TABLE} WHERE opportunity_id = %s AND requirement_text = %s",
            (opp_id, text),
        ).fetchone()
        if existing:
            skipped += 1
            continue
        src = row.get("source_section") or "other"
        if src not in REQUIREMENT_TYPES:
            src = "other"
        status = LEGACY_STATUS_MAP.get(row.get("compliance_status") or "", "not_addressed")
        notes = row.get("notes")
        if row.get("assigned_section"):
            notes = f"{notes}; assigned_section={row['assigned_section']}" if notes else f"assigned_section={row['assigned_section']}"
        conn.execute(
            f"INSERT INTO {MATRIX_TABLE} "
            "(id, opportunity_id, section_ref, volume_ref, requirement_text, requirement_type, "
            "compliance_status, notes, sort_order, evaluation_factor, evaluation_weight, "
            "amendment_version, classification, tenant_id, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                row.get("id") or str(uuid.uuid4()),
                opp_id,
                src,
                row.get("assigned_volume"),
                text,
                src,
                status,
                notes,
                0,
                row.get("evaluation_factor"),
                row.get("evaluation_weight"),
                row.get("amendment_version") or 0,
                row.get("classification") or "CUI",
                row.get("tenant_id"),
                row.get("created_at") or now,
                row.get("updated_at") or now,
            ),
        )
        copied += 1
    return {"legacy_rows": len(rows), "copied": copied, "skipped_duplicate": skipped}


def up(conn) -> dict:
    pg = is_pg(conn)
    result = {"status": "applied", "table": MATRIX_TABLE}

    if not table_exists(conn, MATRIX_TABLE):
        result["status"] = "skipped_no_matrix_table"
        conn.commit()
        return result

    result["columns_added"] = _add_columns(conn, pg)
    result["check"] = _widen_check(conn, pg)

    if table_exists(conn, LEGACY_TABLE):
        result["copy"] = _copy_rows(conn)
        conn.execute(f"DROP TABLE {LEGACY_TABLE}")
        result["dropped"] = LEGACY_TABLE
    else:
        result["copy"] = {"legacy_rows": 0, "copied": 0, "skipped_duplicate": 0}
        result["dropped"] = None

    conn.commit()
    return result
