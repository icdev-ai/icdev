# CUI // SP-CTI
"""Config-driven RAG for co-workers.

CoWorkerRAG generalizes the hand-coded StrategosRAG pattern into a
config-driven retriever that works with any set of tables declared in a
co-worker's YAML.  For ``mode='bespoke'`` it delegates to an existing
domain-specific RAG surface rather than replacing it.
"""
from __future__ import annotations

import importlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from icdev.tools.db.storage import get_connection, is_pg

logger = logging.getLogger(__name__)

# Registry of known bespoke delegates — import paths are relative to repo root.
_BESPOKE_DELEGATES: dict[str, str] = {
    "strategos": "tools.strategos.strategos_chat.StrategosRAG",
}

# Columns that are text-like regardless of declared type.
_TEXT_LIKE_NAMES: frozenset[str] = frozenset(
    {"name", "description", "title", "content", "summary", "notes", "text", "snippet", "body", "comment"}
)


class CoWorkerRAG:
    """Retrieve cited context rows from configured tables.

    Args:
        tables: list of table names to search.
        manifest_shards: not used in generic retrieval (future: shard indexing).
        goals: not used in generic retrieval (future: goal doc indexing).
        mode: ``"generic"`` for config-driven SQL search; ``"bespoke"`` to
              delegate to a domain-specific RAG class.
        coworker_id: used to look up the bespoke delegate when mode is
                     ``"bespoke"``.
    """

    def __init__(
        self,
        tables: list[str] | None = None,
        manifest_shards: list[str] | None = None,
        goals: list[str] | None = None,
        mode: str = "generic",
        coworker_id: str | None = None,
    ):
        self.tables = list(tables or [])
        self.manifest_shards = list(manifest_shards or [])
        self.goals = list(goals or [])
        self.mode = mode
        self.coworker_id = coworker_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """Return up to *top_k* cited chunks relevant to *query*.

        If ``mode='bespoke'`` and a delegate is registered, forwards to the
        delegate's ``retrieve()`` unchanged.
        """
        if self.mode == "bespoke":
            delegate = self._load_bespoke_delegate()
            if delegate is not None:
                return delegate.retrieve(query, top_k)
        return self._generic_retrieve(query, top_k)

    # ------------------------------------------------------------------
    # Bespoke delegation
    # ------------------------------------------------------------------

    def _load_bespoke_delegate(self) -> Any | None:
        if not self.coworker_id:
            return None
        path = _BESPOKE_DELEGATES.get(self.coworker_id)
        if not path:
            return None
        try:
            mod_path, cls_name = path.rsplit(".", 1)
            mod = importlib.import_module(mod_path)
            return getattr(mod, cls_name)()
        except Exception as exc:
            logger.warning("Bespoke delegate %s failed to load: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Generic retrieval
    # ------------------------------------------------------------------

    def _generic_retrieve(self, query: str, top_k: int) -> list[dict]:
        terms = [t.strip().lower() for t in query.split() if len(t.strip()) > 3][:6]
        if not terms or not self.tables:
            return []

        results: list[dict] = []
        max_workers = min(8, len(self.tables) or 1)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._query_table, table, terms, top_k): table
                for table in self.tables
            }
            for fut in as_completed(futures):
                try:
                    results.extend(fut.result())
                except Exception as exc:
                    logger.debug("CoWorkerRAG table error: %s", exc)

        results.sort(key=lambda r: r.get("score", 0), reverse=True)
        return results[:top_k]

    def _query_table(self, table: str, terms: list[str], top_k: int) -> list[dict]:
        text_cols = self._get_text_columns(table)
        if not text_cols:
            return []

        ph = "%s" if is_pg() else "?"
        like_parts: list[str] = []
        params: list[str] = []
        for col in text_cols:
            for term in terms:
                like_parts.append(f"LOWER({col}) LIKE {ph}")
                params.append(f"%{term}%")

        where_clause = " OR ".join(like_parts)
        limit_ph = "%s" if is_pg() else "?"
        sql = (
            f"SELECT * FROM {table} WHERE {where_clause} "  # nosec B608 — table + cols from PRAGMA
            f"LIMIT {limit_ph}"
        )

        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(sql, params + [top_k])
            rows = cur.fetchall()
            colnames = [d[0] for d in cur.description] if cur.description else []
            conn.close()
        except Exception as exc:
            logger.debug("CoWorkerRAG query error for %s: %s", table, exc)
            return []

        out: list[dict] = []
        for row in rows:
            pairs = []
            for cname, val in zip(colnames, row):
                if val is not None:
                    pairs.append(f"{cname}: {str(val)[:80]}")
            content = f"[{table}] " + " | ".join(pairs[:6])
            out.append({
                "content": content[:600],
                "source_type": table,
                "score": 0.6,
            })
        return out

    def _get_text_columns(self, table: str) -> list[str]:
        """Return column names in *table* that look searchable."""
        try:
            conn = get_connection()
            cur = conn.cursor()
            # PRAGMA table_info is translated to information_schema by storage.py.
            cur.execute(f"PRAGMA table_info({table})")
            rows = cur.fetchall()
            conn.close()
        except Exception as exc:
            logger.debug("PRAGMA table_info error for %s: %s", table, exc)
            return []

        text_cols: list[str] = []
        for row in rows:
            # PRAGMA returns: cid, name, type, notnull, dflt_value, pk
            col_name = row[1]
            col_type = (row[2] or "").lower()
            if "text" in col_type or "char" in col_type or "varchar" in col_type:
                text_cols.append(col_name)
            elif col_name.lower() in _TEXT_LIKE_NAMES:
                text_cols.append(col_name)
        return text_cols
