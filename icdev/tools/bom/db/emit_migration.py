# CUI // SP-CTI
"""Regenerate migration 267 from SCHEMA_PG.

The migration and the Python schema are the same thing written twice, and two
copies of a truth is one copy too many: the day they diverge, fresh databases
and migrated databases quietly grow different columns and nothing fails until
something important does. So the .sql is generated, never authored.

    python -m tools.bom.db.emit_migration           # rewrite the file
    python -m tools.bom.db.emit_migration --check   # fail if it is stale (CI)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.bom.db.init_db import SCHEMA_PG

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "tools" / "db" / "migrations" / "322_bom_evidence_engine.sql"
)

HEADER = """-- Migration 267: BOM Evidence Engine.
-- CUI // SP-CTI
--
-- Reconciles a pile of disparate documents (workbooks, decks, PDFs, diagrams)
-- into one bill of materials a person can defend in front of the people who
-- control the money.
--
-- GENERATED from tools/bom/db/init_db.py::SCHEMA_PG — do not hand-edit. The CHECK
-- constraints are derived from the tuples in tools/bom/constants.py, so Python
-- and the database cannot drift apart. Regenerate with:
--     python -m tools.bom.db.emit_migration
--
-- PostgreSQL is the runtime backend and this DDL is authored PG-first. It stays
-- inside the subset SQLite tolerates verbatim (TEXT ids, JSONB, TIMESTAMP,
-- BOOLEAN DEFAULT FALSE, NUMERIC), so the init-fallback applies without leaning
-- on translate_sql, which rewrites %s->? for DML and is never load-bearing here.
--
-- Every table carries tenant_id + classification, so all of these are
-- RLS-governed and use the normal get_connection().
--
-- APPEND-ONLY (registered in APPEND_ONLY_TABLES, .claude/hooks/pre_tool_use.py):
--   bom_match_decisions — a human's merge verdicts. Clusters are a recomputed
--                         projection OVER these; the decisions are the only
--                         durable record of intent. Updating one rewrites history
--                         the customer relied on.
--   bom_audit           — NIST AU trail.
--
-- Idempotent: CREATE TABLE / INDEX IF NOT EXISTS throughout.
"""


def render() -> str:
    return HEADER + "\n" + ";\n\n".join(s.strip() for s in SCHEMA_PG) + ";\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the migration is out of date")
    args = ap.parse_args()

    rendered = render()
    current = MIGRATION.read_text(encoding="utf-8") if MIGRATION.exists() else ""

    if args.check:
        if current != rendered:
            print(f"STALE: {MIGRATION.name} does not match SCHEMA_PG.\n"
                  "Run: python -m tools.bom.db.emit_migration", file=sys.stderr)
            return 1
        print(f"OK: {MIGRATION.name} matches SCHEMA_PG")
        return 0

    MIGRATION.write_text(rendered, encoding="utf-8", newline="")
    print(f"wrote {MIGRATION} ({len(SCHEMA_PG)} statements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
