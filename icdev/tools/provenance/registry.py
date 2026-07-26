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
import sys
import uuid
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

DB_PATH = BASE_DIR / "data" / "icdev.db"


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

    Returns the registry entry ID, or ``""`` on failure.

    Raises:
        ValueError: if *citation_type* is not in
            :data:`tools.provenance.citation_types.CITATION_TYPES`.

    The type is validated in Python **before** the INSERT because the except
    branch below swallows database errors and returns ``""``. Without this
    check, a typo'd citation_type violates the CHECK constraint, gets swallowed,
    and the caller sees an empty id it very likely does not check — the citation
    simply never exists. For a TRUST surface, failing loudly on a bad vocabulary
    value is worth more than the convenience of never raising.
    """
    from tools.provenance.citation_types import CITATION_TYPES, is_valid

    if not is_valid(citation_type):
        raise ValueError(
            f"unknown citation_type {citation_type!r}; "
            f"valid: {', '.join(CITATION_TYPES)}. "
            "Add new types to tools/provenance/citation_types.py and ship a "
            "migration rendered from check_constraint_sql()."
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


# ---------------------------------------------------------------------------
# Web citations (oss-cite-01)
# ---------------------------------------------------------------------------


def register_web_citation(
    requested_url: str,
    content: str,
    final_url: Optional[str] = None,
    http_status: Optional[int] = None,
    content_type: Optional[str] = None,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    fetcher: str = "",
    classification: str = "CUI",
    project_id: Optional[str] = None,
    trust_score: float = 0.0,
    db_path: Path = None,
) -> dict:
    """Register a fetched web page as a first-class citation.

    Writes both halves atomically-ish: the registry row that makes the page
    citeable, and the ``web_fetch_provenance`` row that records what was actually
    served. Without the second half a ``[source: https://...]`` marker is a
    claim about the internet with nothing behind it.

    ``final_url`` is stored separately from ``requested_url`` on purpose: when
    they differ, the cited content is not at the URL the citation names, and a
    reviewer needs to see that rather than infer it.

    ``etag`` / ``last_modified`` are kept so a later re-fetch can prove the page
    un/changed without re-downloading it — the cheap half of evidence-drift
    detection.

    Args:
        requested_url: URL as asked for.
        content: Fetched body; hashed, never stored here.
        final_url: URL after redirects, when different.
        http_status: Response status code.
        content_type: Response Content-Type.
        etag: Response ETag.
        last_modified: Response Last-Modified.
        fetcher: Module that performed the fetch, for audit.
        classification: CUI marking. Default "CUI".
        project_id: Owning project.
        trust_score: Initial trust score for the registry row.
        db_path: Override database path.

    Returns:
        ``{"citation_id": str, "provenance_id": str, "content_hash": str}``;
        ids are empty strings when the write failed.
    """
    import datetime as _dt

    from tools.provenance.citation_types import CITATION_TYPES

    content_hash = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()

    citation_id = register_citation(
        citation_type="web",
        source_table="web_fetch_provenance",
        source_record_id=content_hash,
        source_doc=requested_url,
        source_hash=content_hash,
        classification=classification,
        project_id=project_id,
        trust_score=trust_score,
        db_path=db_path,
    )
    if not citation_id:
        # register_citation swallows its own errors and returns "". Surfacing
        # that here rather than writing an orphan provenance row.
        return {"citation_id": "", "provenance_id": "", "content_hash": content_hash}

    assert "web" in CITATION_TYPES  # tripwire if the vocabulary is edited badly

    prov_id = f"wfp-{uuid.uuid4().hex[:16]}"
    conn = get_connection(db_path=str(db_path or DB_PATH))
    try:
        conn.execute(
            """INSERT INTO web_fetch_provenance
               (id, citation_id, requested_url, final_url, http_status,
                content_hash, content_type, content_length, etag, last_modified,
                fetched_at, fetcher, classification, project_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                prov_id, citation_id, requested_url, final_url, http_status,
                content_hash, content_type, len(content or ""), etag, last_modified,
                now, fetcher, classification, project_id,
            ),
        )
        conn.commit()
        return {
            "citation_id": citation_id,
            "provenance_id": prov_id,
            "content_hash": content_hash,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {
            "citation_id": citation_id,
            "provenance_id": "",
            "content_hash": content_hash,
        }
    finally:
        conn.close()


def get_web_provenance(citation_id: str, db_path: Path = None) -> List[dict]:
    """Return every recorded fetch behind a web citation, newest first.

    A list, not a single row: the table is append-only, so a re-fetched URL
    accumulates rows and the history is the evidence-drift signal.
    """
    conn = get_connection(db_path=str(db_path or DB_PATH))
    try:
        rows = conn.execute(
            "SELECT * FROM web_fetch_provenance WHERE citation_id = %s "
            "ORDER BY fetched_at DESC",
            (citation_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()
