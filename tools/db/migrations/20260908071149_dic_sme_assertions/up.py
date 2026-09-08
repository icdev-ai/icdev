# CUI // SP-CTI
"""Migration 20260908071149 — dic_sme_assertions (dwr-ev-02).

A review comment PROMOTED to an attributed SME assertion. One row per PROMOTED
comment — the unique key is the comment, so two SMEs contradicting each other
are two rows and nothing overwrites anything — carrying the typed claim the
promoting human supplied, the comment VERBATIM as the quotation, the comment
author as ``asserted_by`` and the promoter as ``promoted_by``.
``args/entity_currency.yaml`` declares this table as the source
``dic_sme_assertions`` (kind ``sme_attributed``, precedence 0, beside the
author source) and the store's ONE resolver ranks its rows against every other
source, keeping the losers under ``others``.

An UNPROMOTED comment has no row here, which is the whole of the "a comment
cannot be cited" property: ``dic_section_annotations`` is declared as a source
nowhere and read by no evidence seam, so there is no flag to go stale and no
second place the answer is written down.

The DDL is IMPORTED from ``tools.document_intelligence.sme_evidence.DDL``
rather than respelled here: ``sme_evidence.ensure_table`` executes the same
tuple for a database this migration has not reached, and two copies of a table
shape drift the first time a column is added (the dwr-ev-01 precedent,
20260908003920).

Not append-only: the audit record of a promotion is the fail-closed
``dic.hitl_decision`` row the route writes BEFORE the promotion, in
``audit_trail``, which is where the immutable record of a human decision
belongs. This table is the derived evidence the store reads.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.document_intelligence.sme_evidence import DDL, TABLE  # noqa: E402


def up(conn=None) -> None:
    owned = conn is None
    conn = conn or get_connection()
    try:
        for stmt in DDL:
            conn.execute(stmt)
        conn.commit()
        print(f"[20260908071149_dic_sme_assertions] up: {TABLE} created (or already exists)")
    finally:
        if owned:
            conn.close()


if __name__ == "__main__":  # pragma: no cover - CLI
    up()
