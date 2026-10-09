# CUI // SP-CTI
"""Migration 20260908003920 — dic_author_assertions (dwr-ev-01).

Author-supplied content as a DECLARED currency source. One row per
(document, entity, version) statement an author made at upload time about the
currency of an entity the document names; ``args/entity_currency.yaml``
declares this table as the source ``dic_author_assertions`` (kind
``author_supplied``, precedence 0) and the store's ONE resolver ranks its rows
against every other source, keeping the losers under ``others``.

The DDL is IMPORTED from ``tools.document_intelligence.author_evidence.DDL``
rather than respelled here: ``ingest_orchestrator._ensure_schema`` executes the
same tuple for a database this migration has not reached, and two copies of a
table shape drift the first time a column is added (the ``dic_suggestions``
precedent, 20260907213944, carried its CREATE_FULL in the migration for the
same reason in the other direction — that table's owner did not import).

``as_of`` is the AUTHOR's clock and ``created_at`` is ours; ``as_of_basis``
records whether the author stated a date or the upload time was defaulted in.
Not append-only: a re-ingest of the same document UPDATES its statement, the
same call ``entity_currency`` makes for a refreshable assertion cache.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.document_intelligence.author_evidence import DDL, TABLE  # noqa: E402


def up(conn=None) -> None:
    owned = conn is None
    conn = conn or get_connection()
    try:
        for stmt in DDL:
            conn.execute(stmt)
        conn.commit()
        print(f"[20260908003920_dic_author_assertions] up: {TABLE} created (or already exists)")
    finally:
        if owned:
            conn.close()


if __name__ == "__main__":  # pragma: no cover - CLI
    up()
