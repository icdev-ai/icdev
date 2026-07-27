#!/usr/bin/env python3
# CUI // SP-CTI
"""Dataset lineage tracker — records the chain from source document to training pair.

Answers: "Which training pairs came from document X?" and "If I change doc X, which
pairs are stale?"

Inspired by DeepSpec's 3-stage pipeline (download → regen → cache) where each stage's
output feeds the next; here each source document → extracted chunks → generated pairs
is the analogous chain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("icdev.finetune.dataset_lineage")

# DDL uses SQLite dialect; StorageConnection.executescript handles PG translation.
_SQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS ft_source_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL UNIQUE,
    title TEXT DEFAULT '',
    source_type TEXT DEFAULT 'document',
    content_hash TEXT NOT NULL,
    tenant_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ft_pair_lineage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    example_id INTEGER NOT NULL,
    doc_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    generation_method TEXT DEFAULT 'llm_generated',
    generator_model_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ft_pair_lineage_doc ON ft_pair_lineage(doc_id);
CREATE INDEX IF NOT EXISTS idx_ft_pair_lineage_example ON ft_pair_lineage(example_id);
CREATE INDEX IF NOT EXISTS idx_ft_pair_lineage_dataset ON ft_pair_lineage(dataset_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class LineageRecord:
    """Single pair-to-source linkage record."""

    example_id: int
    doc_id: str
    dataset_id: str
    generation_method: str = "llm_generated"
    generator_model_id: str = ""


class DatasetLineage:
    """Tracks which training pairs came from which source documents."""

    def __init__(self, conn=None) -> None:
        self._conn = conn

    def _get_conn(self):
        """Return stored connection or open a new one via get_connection()."""
        if self._conn is not None:
            return self._conn, False  # caller must not close
        from icdev.tools.db.storage import get_connection

        return get_connection(), True  # caller should close

    def ensure_schema(self) -> None:
        """Create lineage tables if they do not exist."""
        conn, should_close = self._get_conn()
        try:
            conn.executescript(_SQL_SCHEMA)
            conn.commit()
        finally:
            if should_close:
                conn.close()

    def register_source(
        self,
        doc_id: str,
        content: str,
        *,
        title: str = "",
        source_type: str = "document",
        tenant_id: str = "",
    ) -> str:
        """Insert or upsert a source document; returns doc_id."""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        conn, should_close = self._get_conn()
        try:
            existing = conn.execute(
                "SELECT id FROM ft_source_documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE ft_source_documents SET content_hash = ?, title = ?, source_type = ? WHERE doc_id = ?",
                    (content_hash, title, source_type, doc_id),
                )
            else:
                conn.execute(
                    "INSERT INTO ft_source_documents (doc_id, title, source_type, content_hash, tenant_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (doc_id, title, source_type, content_hash, tenant_id, _now()),
                )
            conn.commit()
        except Exception as exc:
            logger.warning("register_source failed for doc_id=%s: %s", doc_id, exc)
        finally:
            if should_close:
                conn.close()
        return doc_id

    def record_pair_lineage(
        self,
        example_id: int,
        doc_id: str,
        dataset_id: str,
        *,
        generation_method: str = "llm_generated",
        generator_model_id: str = "",
    ) -> None:
        """Insert a pair→source linkage record."""
        conn, should_close = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO ft_pair_lineage
                   (example_id, doc_id, dataset_id, generation_method, generator_model_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (example_id, doc_id, dataset_id, generation_method, generator_model_id, _now()),
            )
            conn.commit()
        finally:
            if should_close:
                conn.close()

    def get_pairs_for_doc(self, doc_id: str) -> list[dict[str, Any]]:
        """Return all lineage records (joined with example data) for doc_id."""
        conn, should_close = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT pl.id, pl.example_id, pl.doc_id, pl.dataset_id,
                          pl.generation_method, pl.generator_model_id, pl.created_at,
                          ex.user_input, ex.expected_output, ex.content_hash AS example_hash
                   FROM ft_pair_lineage pl
                   LEFT JOIN ft_dataset_examples ex ON ex.id = pl.example_id
                   WHERE pl.doc_id = ?
                   ORDER BY pl.created_at DESC""",
                (doc_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("get_pairs_for_doc failed: %s", exc)
            return []
        finally:
            if should_close:
                conn.close()

    def get_stale_pairs(self, doc_id: str, new_content_hash: str) -> list[int]:
        """Return example_ids whose source document content has changed."""
        conn, should_close = self._get_conn()
        try:
            row = conn.execute(
                "SELECT content_hash FROM ft_source_documents WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
            if row is None:
                return []
            stored_hash = row["content_hash"] if hasattr(row, "keys") else row[0]
            if stored_hash == new_content_hash:
                return []
            # Hash changed — all pairs for this doc are stale
            rows = conn.execute(
                "SELECT example_id FROM ft_pair_lineage WHERE doc_id = ?",
                (doc_id,),
            ).fetchall()
            return [r["example_id"] if hasattr(r, "keys") else r[0] for r in rows]
        except Exception as exc:
            logger.warning("get_stale_pairs failed: %s", exc)
            return []
        finally:
            if should_close:
                conn.close()

    def invalidate_pairs(self, example_ids: list[int], dataset_id: str) -> int:
        """Mark training examples as stale by setting approved=0.

        Returns count of examples affected.
        """
        if not example_ids:
            return 0
        conn, should_close = self._get_conn()
        try:
            placeholders = ", ".join(["?"] * len(example_ids))
            try:
                conn.execute(
                    f"UPDATE ft_dataset_examples SET approved = 0 WHERE id IN ({placeholders}) AND dataset_id = ?",  # noqa: S608
                    (*example_ids, dataset_id),
                )
                conn.commit()
                return len(example_ids)
            except Exception:
                logger.warning(
                    "invalidate_pairs: could not update ft_dataset_examples; "
                    "returning count without DB update. example_ids=%s",
                    example_ids,
                )
                return len(example_ids)
        finally:
            if should_close:
                conn.close()

    def lineage_report(self, dataset_id: str) -> dict[str, Any]:
        """Return summary of source documents and pair counts for a dataset."""
        conn, should_close = self._get_conn()
        try:
            total_row = conn.execute(
                "SELECT COUNT(*) FROM ft_pair_lineage WHERE dataset_id = ?",
                (dataset_id,),
            ).fetchone()
            total = total_row[0] if total_row else 0

            source_rows = conn.execute(
                """SELECT pl.doc_id, sd.title, COUNT(*) AS pair_count, sd.content_hash
                   FROM ft_pair_lineage pl
                   LEFT JOIN ft_source_documents sd ON sd.doc_id = pl.doc_id
                   WHERE pl.dataset_id = ?
                   GROUP BY pl.doc_id, sd.title, sd.content_hash
                   ORDER BY pair_count DESC""",
                (dataset_id,),
            ).fetchall()

            sources = []
            for r in source_rows:
                if hasattr(r, "keys"):
                    sources.append(
                        {
                            "doc_id": r["doc_id"],
                            "title": r["title"],
                            "pair_count": r["pair_count"],
                            "content_hash": r["content_hash"],
                        }
                    )
                else:
                    sources.append(
                        {
                            "doc_id": r[0],
                            "title": r[1],
                            "pair_count": r[2],
                            "content_hash": r[3],
                        }
                    )

            return {
                "dataset_id": dataset_id,
                "total_pairs": total,
                "sources": sources,
            }
        except Exception as exc:
            logger.warning("lineage_report failed: %s", exc)
            return {"dataset_id": dataset_id, "total_pairs": 0, "sources": [], "error": str(exc)}
        finally:
            if should_close:
                conn.close()


def get_lineage(conn=None) -> DatasetLineage:
    """Factory for DatasetLineage."""
    return DatasetLineage(conn=conn)


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Dataset lineage tracker")
    parser.add_argument("--report", metavar="DATASET_ID", help="Print lineage report for a dataset")
    parser.add_argument(
        "--stale",
        nargs=2,
        metavar=("DOC_ID", "NEW_HASH"),
        help="Check if pairs for doc are stale",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    lineage = get_lineage()
    lineage.ensure_schema()

    result: dict[str, Any] = {}
    if args.report:
        result = lineage.lineage_report(args.report)
    elif args.stale:
        doc_id, new_hash = args.stale
        stale_ids = lineage.get_stale_pairs(doc_id, new_hash)
        result = {"doc_id": doc_id, "stale": bool(stale_ids), "stale_example_ids": stale_ids}
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
