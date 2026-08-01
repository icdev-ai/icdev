# CUI // SP-CTI
"""Collection registry — a document must never be invisible.

`dic_documents.collection_id` is free-text with no foreign key, and every
ingestion path takes it from the caller: `/api/ingest` defaults it to
``"default"``, the CLI passes ``--collection`` verbatim, and the IDR flow mints
``idr-<session_id>``. None of them create the matching `dic_collections` row.

That matters because the Collections UI enumerates *collections*, not documents
(`blueprint.py::api_collections_list` selects from `dic_collections`). A document
whose `collection_id` has no row has no container to appear in, so it is ingested,
chunked, embedded, linked and scanned — and then is unreachable. It is not lost;
it is invisible, which is worse, because nothing reports a failure.

This module is the one place that closes the gap: call `ensure_collection` before
writing a document and the container always exists.

Why not just rewrite the bad ids to proper hash ids? Because `collection_id` is
load-bearing identity, not a label: `ingest_orchestrator._doc_id` derives
`doc_id` from ``f"{collection_id}:{filepath}"``, chunks carry it as `project_id`,
and content hashes are scoped by it. Renaming a collection would orphan a
document from its own chunks. The id stays; the row gets created to match.
"""

from __future__ import annotations

from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.dic.collection_registry")

# Most-restrictive-wins. Ordering is explicit because it is NOT alphabetical:
# sorting would rank 'UNCLASSIFIED' above 'SECRET' and silently under-mark a
# collection holding classified documents.
_CLASSIFICATION_RANK: dict[str, int] = {
    "UNCLASSIFIED": 1,
    "CUI": 2,
    "SECRET": 3,
    "TOP SECRET": 4,
}
_RANK_TO_CLASSIFICATION: dict[int, str] = {v: k for k, v in _CLASSIFICATION_RANK.items()}

DEFAULT_CLASSIFICATION = "CUI"
DEFAULT_TENANT = "default"


def most_restrictive(*classifications: str | None) -> str:
    """Return the highest classification among the arguments.

    Unknown or empty values fall back to CUI rather than UNCLASSIFIED: an
    unrecognised marking is not evidence that content is releasable.
    """
    best = 0
    for c in classifications:
        rank = _CLASSIFICATION_RANK.get((c or "").strip().upper(), 0)
        if rank > best:
            best = rank
    return _RANK_TO_CLASSIFICATION.get(best, DEFAULT_CLASSIFICATION)


def ensure_collection(
    conn: Any,
    collection_id: str | None,
    *,
    name: str | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
) -> bool:
    """Create the `dic_collections` row for ``collection_id`` if it is missing.

    Returns True if the collection exists (created or already present), False if
    ``collection_id`` is empty — there is nothing to register, and inventing an
    id here would only hide the caller's bug.

    Does NOT commit: the caller owns the transaction, so the collection and the
    document it is for land together or not at all.

    Exceptions are deliberately NOT swallowed. On PostgreSQL a failed statement
    poisons the surrounding transaction, so a caught-and-ignored error here would
    surface later as an unrelated "current transaction is aborted" on the
    document INSERT — far from its cause.
    """
    if not collection_id or not str(collection_id).strip():
        return False

    collection_id = str(collection_id).strip()
    cur = conn.cursor()

    # ON CONFLICT DO NOTHING rather than a SELECT-then-INSERT: two concurrent
    # ingests into a new collection would both see "missing" and race.
    #
    # Only the columns this helper actually decides. `description`, `created_at`
    # and the rest carry DB defaults; naming them here would buy nothing and
    # couple the write to every future column change.
    cur.execute(
        """
        INSERT INTO dic_collections (collection_id, name, tenant_id, classification)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (collection_id) DO NOTHING
        """,
        (
            collection_id,
            # The id verbatim, not a prettified guess. The caller chose this
            # string; showing it back is how they recognise their own documents.
            (name or collection_id).strip()[:200],
            (tenant_id or DEFAULT_TENANT),
            most_restrictive(classification),
        ),
    )
    if getattr(cur, "rowcount", 0):
        logger.info("registered dic collection %r (was missing)", collection_id)
    return True
