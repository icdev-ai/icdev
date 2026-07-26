#!/usr/bin/env python3
# CUI // SP-CTI
"""Unified source citation registry for ICDEV.

Indexes ALL citations across subsystems into source_citation_registry,
enabling cross-subsystem provenance queries and trust scoring.

Usage:
    from tools.provenance.registry import register_citation, get_citations_for_project

    register_citation(
        citation_type="hitl",
        source_table="wf_citations",
        source_record_id="wfc-abc123",
        source_doc="NIST SP 800-53 Rev 5",
        source_hash="sha256...",
        project_id="proj-test",
    )
"""

import argparse
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

DB_PATH = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# Citation types — the single source of truth
# ---------------------------------------------------------------------------
# The ``source_citation_registry.citation_type`` CHECK constraint is DERIVED
# from this tuple (see :func:`citation_type_check_sql`), never hand-written in
# SQL. Adding a value here and running the newest constraint migration is the
# whole procedure; a hand-edited CHECK would silently drift from the Python
# constant, which is exactly the failure migration 271 had to repair for the
# ACE state constraints.

CITATION_TYPES: tuple[str, ...] = (
    "hitl",
    "rag",
    "prov_entity",
    "prov_activity",
    "canvas_ai",
    "slsa",
    "sbom",
    "compliance_evidence",
    "agent_decision",
    "manual",
    # oss-cite-01 — a page fetched over HTTP, with fetch provenance persisted in
    # web_fetch_provenance (tools/provenance/web_citation.py).
    "web",
    # --- Types shipped code already passes, which migration 149 never allowed --
    # These are not new capabilities. Both call sites below have been calling
    # register_citation() with these values since they shipped; the INSERT failed
    # the ten-value CHECK every time and the `except Exception: return ""` below
    # swallowed it, so the caller got an empty id and no row was ever written.
    # That is a silent TRUST violation: Cortex's Gate 7a reports a provenance
    # record it never persisted. Enumerating them here is what makes the
    # derived constraint true to the code, and is required for correctness of
    # the ValueError raised below — without it, that raise converts a silent
    # failure into a hard one (Cortex fails closed when CortexContext.fail_closed).
    "cortex",       # tools/cortex/governance.py::_gate_register_provenance
    "asset_token",  # tools/blockchain/asset_ledger.py (2 call sites)
)

CITATION_TYPE_CONSTRAINT = "source_citation_registry_citation_type_check"

# Column order of source_citation_registry as created by migration 149. Used by
# the SQLite constraint rebuild, which has to copy rows column-by-column.
_REGISTRY_COLUMNS: tuple[str, ...] = (
    "id",
    "citation_type",
    "source_table",
    "source_record_id",
    "source_doc",
    "source_hash",
    "anchor_hash",
    "merkle_root",
    "blockchain_tx_id",
    "classification",
    "project_id",
    "trust_score",
    "created_at",
)

_QUOTED_RE = re.compile(r"'([^']+)'")
# The citation_type CHECK body inside a stored SQLite CREATE TABLE statement.
_CHECK_BODY_RE = re.compile(
    r"CHECK\s*\(\s*citation_type\s+IN\s*\(([^)]*)\)", re.IGNORECASE
)


def citation_type_check_sql(column: str = "citation_type") -> str:
    """Return the CHECK body for ``citation_type``, derived from CITATION_TYPES."""
    values = ", ".join(f"'{t}'" for t in CITATION_TYPES)
    return f"{column} IN ({values})"


def citation_registry_ddl(table: str = "source_citation_registry") -> str:
    """CREATE TABLE for the registry with the derived CHECK constraint."""
    return f"""
        CREATE TABLE {table} (
            id TEXT PRIMARY KEY,
            citation_type TEXT NOT NULL CHECK({citation_type_check_sql()}),
            source_table TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            source_doc TEXT,
            source_hash TEXT NOT NULL,
            anchor_hash TEXT,
            merkle_root TEXT,
            blockchain_tx_id TEXT,
            classification TEXT DEFAULT 'CUI',
            project_id TEXT,
            trust_score REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """.strip()


def _is_pg(conn: Any) -> bool:
    try:
        from tools.db.storage import is_pg

        return bool(is_pg(conn))
    except Exception:
        mod = type(conn).__module__
        return "psycopg" in mod or "psycopg2" in mod


def repair_citation_type_constraint(conn: Any) -> dict:
    """Re-derive the ``citation_type`` CHECK constraint from :data:`CITATION_TYPES`.

    Idempotent, and safe on a table that already matches — a second call
    reports ``"ok"``. Returns ``{"status": ..., "allowed": [...]}`` where status
    is one of ``ok`` / ``repaired`` / ``added`` / ``absent`` / ``skipped:<why>``.

    PostgreSQL: read ``pg_get_constraintdef``, compare the encoded value set to
    the constant, and DROP + ADD in one transaction when they differ — the same
    shape as ``tools/ace/db/init_db.py::repair_state_constraints``.

    SQLite: a CHECK constraint cannot be altered in place, so the table is
    rebuilt (create-copy-drop-rename) when its stored DDL does not already
    allow every value in the constant. This path matters because SQLite is a
    real runtime backend here, not only a test harness: without the rebuild an
    ``INSERT ... citation_type='web'`` fails the stale CHECK and
    :func:`register_citation` swallows it and returns ``""``.
    """
    expected = set(CITATION_TYPES)

    if _is_pg(conn):
        try:
            row = conn.execute(
                "SELECT pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = 'source_citation_registry' AND c.conname = %s",
                (CITATION_TYPE_CONSTRAINT,),
            ).fetchone()
        except Exception as exc:
            return {"status": f"skipped:{type(exc).__name__}", "allowed": sorted(expected)}

        current = set(_QUOTED_RE.findall(row[0])) if row else None
        if current == expected:
            return {"status": "ok", "allowed": sorted(expected)}
        try:
            if row is not None:
                conn.execute(
                    f"ALTER TABLE source_citation_registry DROP CONSTRAINT {CITATION_TYPE_CONSTRAINT}"
                )
            conn.execute(
                f"ALTER TABLE source_citation_registry ADD CONSTRAINT {CITATION_TYPE_CONSTRAINT} "
                f"CHECK ({citation_type_check_sql()})"
            )
            conn.commit()
            return {
                "status": "repaired" if row is not None else "added",
                "allowed": sorted(expected),
            }
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            return {"status": f"skipped:{type(exc).__name__}", "allowed": sorted(expected)}

    # ---- SQLite: rebuild the table ----------------------------------------
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'source_citation_registry'"
        ).fetchone()
    except Exception as exc:
        return {"status": f"skipped:{type(exc).__name__}", "allowed": sorted(expected)}

    if not row:
        return {"status": "absent", "allowed": sorted(expected)}

    ddl = row[0] or ""
    # Scope the value scan to the citation_type CHECK body only. A naive scan of
    # the whole DDL also picks up DEFAULT 'CUI', which never matches the constant
    # and so would make every call report "repaired" — an infinite rebuild.
    # A table with no CHECK at all yields an empty set and is rebuilt once, so
    # the constraint starts being enforced.
    body = _CHECK_BODY_RE.search(ddl)
    current = set(_QUOTED_RE.findall(body.group(1))) if body else set()
    if current == expected:
        return {"status": "ok", "allowed": sorted(expected)}

    cols = ", ".join(_REGISTRY_COLUMNS)
    try:
        conn.execute(citation_registry_ddl("source_citation_registry__new"))
        conn.execute(
            f"INSERT INTO source_citation_registry__new ({cols}) "
            f"SELECT {cols} FROM source_citation_registry"
        )
        conn.execute("DROP TABLE source_citation_registry")
        conn.execute(
            "ALTER TABLE source_citation_registry__new RENAME TO source_citation_registry"
        )
        for idx, col in (
            ("idx_scr_project", "project_id"),
            ("idx_scr_type", "citation_type"),
            ("idx_scr_hash", "source_hash"),
            ("idx_scr_anchor", "anchor_hash"),
        ):
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {idx} ON source_citation_registry({col})"
            )
        conn.commit()
        return {"status": "repaired", "allowed": sorted(expected)}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            conn.execute("DROP TABLE IF EXISTS source_citation_registry__new")
            conn.commit()
        except Exception:
            pass
        return {"status": f"skipped:{type(exc).__name__}", "allowed": sorted(expected)}


def register_citation(
    citation_type: str,
    source_table: str,
    source_record_id: str,
    source_hash: str,
    source_doc: Optional[str] = None,
    anchor_hash: Optional[str] = None,
    merkle_root: Optional[str] = None,
    blockchain_tx_id: Optional[str] = None,
    classification: str = "CUI",
    project_id: Optional[str] = None,
    trust_score: float = 0.0,
    db_path: Path = None,
) -> str:
    """Register a citation in the unified source_citation_registry.

    Returns the registry entry ID, or ``""`` if the insert failed.

    Raises ValueError for a ``citation_type`` outside :data:`CITATION_TYPES`.
    An unknown type would fail the CHECK constraint anyway, and the INSERT
    below swallows that into an empty return — so the caller would see a
    "registered" citation that does not exist. Failing loudly on a typo is the
    only way that distinction survives.
    """
    if citation_type not in CITATION_TYPES:
        raise ValueError(
            f"unknown citation_type {citation_type!r}; "
            f"allowed: {', '.join(CITATION_TYPES)}"
        )
    reg_id = f"scr-{uuid.uuid4().hex[:16]}"
    conn = get_connection(db_path=str(db_path or DB_PATH))
    try:
        conn.execute(
            """INSERT INTO source_citation_registry
               (id, citation_type, source_table, source_record_id, source_doc,
                source_hash, anchor_hash, merkle_root, blockchain_tx_id,
                classification, project_id, trust_score, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                reg_id,
                citation_type,
                source_table,
                source_record_id,
                source_doc,
                source_hash,
                anchor_hash,
                merkle_root,
                blockchain_tx_id,
                classification,
                project_id,
                trust_score,
                __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return reg_id
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return ""
    finally:
        conn.close()


def get_citations_for_project(project_id: str, db_path: Path = None) -> List[dict]:
    """Return all registry entries for a project."""
    conn = get_connection(db_path=str(db_path or DB_PATH))
    try:
        rows = conn.execute(
            "SELECT * FROM source_citation_registry WHERE project_id = %s ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_citation_by_hash(source_hash: str, db_path: Path = None) -> Optional[dict]:
    """Return a registry entry by its source hash."""
    conn = get_connection(db_path=str(db_path or DB_PATH))
    try:
        row = conn.execute(
            "SELECT * FROM source_citation_registry WHERE source_hash = %s LIMIT 1",
            (source_hash,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_trust_score(registry_id: str, trust_score: float, db_path: Path = None) -> bool:
    """Update the trust score of a registry entry."""
    conn = get_connection(db_path=str(db_path or DB_PATH))
    try:
        conn.execute(
            "UPDATE source_citation_registry SET trust_score = %s WHERE id = %s",
            (trust_score, registry_id),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def update_blockchain_anchor(
    registry_id: str,
    merkle_root: str,
    blockchain_tx_id: str,
    db_path: Path = None,
) -> bool:
    """Update the blockchain anchor fields after ledger submission."""
    conn = get_connection(db_path=str(db_path or DB_PATH))
    try:
        conn.execute(
            "UPDATE source_citation_registry SET merkle_root = %s, blockchain_tx_id = %s WHERE id = %s",
            (merkle_root, blockchain_tx_id, registry_id),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def has_cross_reference(registry_id: str, db_path: Path = None) -> bool:
    """Check if a registry entry is cross-referenced by another subsystem."""
    conn = get_connection(db_path=str(db_path or DB_PATH))
    try:
        # A cross-reference exists if the same source_hash appears in multiple source_tables
        row = conn.execute(
            """SELECT COUNT(DISTINCT source_table) as cnt FROM source_citation_registry
               WHERE source_hash = (SELECT source_hash FROM source_citation_registry WHERE id = %s)""",
            (registry_id,),
        ).fetchone()
        return row is not None and row["cnt"] > 1
    except Exception:
        return False
    finally:
        conn.close()


def index_existing_citations(db_path: Path = None) -> dict:
    """One-time backfill from existing provenance sources.

    Iterates prov_entities, canvas_ai_decisions, compliance_evidence_chain,
    sbom_records, and wf_citations (if source_hash exists).
    """
    conn = get_connection(db_path=str(db_path or DB_PATH))
    stats = {"prov_entities": 0, "canvas_ai": 0, "evidence": 0, "sbom": 0, "hitl": 0, "errors": 0}

    def _hash_row(row: dict, keys: List[str]) -> str:
        canonical = "|".join(str(row.get(k, "")) for k in keys)
        return hashlib.sha256(canonical.encode()).hexdigest()

    try:
        # 1. prov_entities with content_hash
        try:
            rows = conn.execute(
                "SELECT id, entity_type, label, content_hash, project_id FROM prov_entities WHERE content_hash IS NOT NULL"
            ).fetchall()
            for r in rows:
                h = r["content_hash"] or _hash_row(dict(r), ["id", "entity_type", "label"])
                register_citation(
                    citation_type="prov_entity",
                    source_table="prov_entities",
                    source_record_id=r["id"],
                    source_hash=h,
                    source_doc=r.get("label"),
                    project_id=r.get("project_id"),
                )
                stats["prov_entities"] += 1
        except Exception:
            stats["errors"] += 1

        # 2. canvas_ai_decisions
        try:
            rows = conn.execute(
                "SELECT id, canvas_type, record_id, decision_type, decision, model_used, confidence, project_id FROM canvas_ai_decisions"
            ).fetchall()
            for r in rows:
                h = _hash_row(dict(r), ["id", "canvas_type", "record_id", "decision_type", "decision", "model_used", "confidence"])
                register_citation(
                    citation_type="canvas_ai",
                    source_table="canvas_ai_decisions",
                    source_record_id=str(r["id"]),
                    source_hash=h,
                    source_doc=f"{r['canvas_type']}:{r['decision_type']}",
                    project_id=r.get("project_id"),
                )
                stats["canvas_ai"] += 1
        except Exception:
            stats["errors"] += 1

        # 3. compliance_evidence_chain
        try:
            rows = conn.execute("SELECT id, project_id, control_family FROM compliance_evidence_chain").fetchall()
            for r in rows:
                h = _hash_row(dict(r), ["id", "project_id", "control_family"])
                register_citation(
                    citation_type="compliance_evidence",
                    source_table="compliance_evidence_chain",
                    source_record_id=str(r["id"]),
                    source_hash=h,
                    source_doc=r.get("control_family"),
                    project_id=r.get("project_id"),
                )
                stats["evidence"] += 1
        except Exception:
            stats["errors"] += 1

        # 4. sbom_records
        try:
            rows = conn.execute("SELECT id, project_id, file_path, version FROM sbom_records").fetchall()
            for r in rows:
                h = _hash_row(dict(r), ["id", "project_id", "file_path", "version"])
                register_citation(
                    citation_type="sbom",
                    source_table="sbom_records",
                    source_record_id=str(r["id"]),
                    source_hash=h,
                    source_doc=r.get("file_path"),
                    project_id=r.get("project_id"),
                )
                stats["sbom"] += 1
        except Exception:
            stats["errors"] += 1

        # 5. wf_citations (if source_hash column exists)
        try:
            rows = conn.execute(
                "SELECT id, instance_id, source_doc, source_hash, cited_by FROM wf_citations WHERE source_hash IS NOT NULL"
            ).fetchall()
            for r in rows:
                register_citation(
                    citation_type="hitl",
                    source_table="wf_citations",
                    source_record_id=r["id"],
                    source_hash=r["source_hash"],
                    source_doc=r.get("source_doc"),
                    project_id=r.get("instance_id"),
                )
                stats["hitl"] += 1
        except Exception:
            stats["errors"] += 1

    finally:
        conn.close()

    return stats


def main():
    parser = argparse.ArgumentParser(description="Source Citation Registry")
    parser.add_argument("--index-existing", action="store_true", help="Backfill from existing tables")
    parser.add_argument("--list-project", help="List citations for a project")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.index_existing:
        stats = index_existing_citations()
        print(json.dumps(stats, indent=2) if args.json else stats)
        return

    if args.list_project:
        rows = get_citations_for_project(args.list_project)
        print(json.dumps(rows, indent=2, default=str) if args.json else rows)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
