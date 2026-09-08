# CUI // SP-CTI
"""A review comment PROMOTED to an attributed SME assertion (dwr-ev-02).

WHAT A COMMENT IS, BY DEFAULT
-----------------------------

An INSTRUCTION. "Shorten this." "We moved to TLS 1.3 last quarter." A reviewer's
chat message is not a source, and nothing in this module, in
``args/entity_currency.yaml`` or anywhere downstream reads
``dic_section_annotations`` as evidence. A comment nobody promoted is invisible
to the drafter, to ``cortex.resolve``, to every pack and to every citation --
not ranked low, not weakly weighted: ABSENT. Letting unverifiable prose ground a
compliance claim is the hallucination the TRUST chain exists to stop, and the
cheapest way to keep it stopped is for the prose to have no path at all.

WHAT PROMOTION IS
-----------------

One deliberate act, on ONE comment, by a named human, recorded as a DECISION.
``promote_comment`` writes a row to ``dic_sme_assertions`` and refreshes the
``entity_currency`` store for this source alone. There is no bulk promote, no
promotion on any heuristic, and no automatic promotion anywhere -- the writer
takes exactly one ``ann_id``, and it refuses a comment it has already promoted
rather than quietly re-writing what a human already decided.

THE PROSE IS NEVER PARSED, AND THAT IS THE WHOLE TRUST ARGUMENT
---------------------------------------------------------------

A promotion carries a TYPED claim the promoting human supplies -- entity, type,
status, optionally a version and dates -- validated against the SAME closed
vocabulary an author's upload is validated against
(``author_evidence.AUTHOR_STATUSES``, reached through the same one validator,
never respelled). The comment text is stored VERBATIM as the quotation and the
attribution, and no code here reads a word of it. That is deliberate, and it is
the rule dwr-ev-01 already stated for uploads: an assertion extracted from prose
is a ``text_pattern`` claim, and a ``text_pattern`` claim can never reach a pack
(TRUST rule 2). So what the comment contributes is not the claim -- it is WHO
said it, WHEN they said it and against WHICH span of WHICH document, which is
exactly what makes the resulting evidence *attributed*.

TWO CLOCKS, KEPT APART
----------------------

``as_of`` is the SME's -- when they say the fact was true. An SME who states no
date gets the COMMENT's ``created_at`` with ``as_of_basis: comment_time``
recorded beside it, so a defaulted clock can never be read as a stated one.
``promoted_at`` is OURS: when a human decided the statement was evidence. They
are facts about different people at different times and they are never merged.

RENDERED DISTINCTLY, STRUCTURALLY
---------------------------------

The store already carries ``source_kind`` on every resolved view (dwr-ev-01).
This source declares ``kind: sme_attributed``, and ``cortex/search_service.py``
maps that kind onto the citation ``source_type`` ``sme_assertion`` -- a
DIFFERENT badge from the ``currency_assertion`` a machine feed produces and from
the document types a corpus chunk produces. A reader never has to infer from a
table name that they are looking at a person's statement; the citation says so,
and DocDrift renders it apart from a document citation rather than in the same
list of blue chips.

RANKED BESIDE THE AUTHOR, NOT ABOVE THEM
----------------------------------------

``precedence: 0``, the same as ``dic_author_assertions``. Two humans stating
facts about THIS estate do not structurally outrank one another, so the pair
ties on precedence and on the declared prior and the LATER human clock wins,
with the earlier preserved under ``others`` with ``conflict: true``. Ranking an
SME comment above an author's upload declaration would let a 2020 remark beat a
2026 signed statement; ranking it below would let a stale upload beat this
morning's correction. Neither is a rule about evidence. Recency between equals
is, and the store already applies it.

DISAGREEMENT, AND WHAT IS STILL NOT MODELLED
--------------------------------------------

Two SMEs contradicting each other are TWO ROWS here -- the unique key is the
COMMENT, so nothing overwrites anything -- and ``promote_comment`` REPORTS the
contradiction it is creating under ``contradicts`` rather than landing it
silently. The ``entity_currency`` store still keeps one row per (source, entity)
and so carries the newest human clock; the loser stays readable here and through
``list_assertions``. Making two statements from ONE source two STORE rows is a
change to the store's identity key, named rather than approximated -- the same
discipline dwr-ev-01 applied when it named this card.

A PROMOTION IS NOT REVERSIBLE THROUGH THIS SEAM, AND WHY
--------------------------------------------------------

``entity_currency.backfill`` is upsert-only and the store has no delete path, so
deleting a row here would leave the derived currency row standing: a revocation
that looks like it worked and did not, which is worse than no revocation at all.
Rather than invent a second writer of currency rows inside a document-
intelligence module, correction works the way human evidence works -- a LATER
attributed statement with a later ``as_of`` supersedes the earlier one in the
store, and the earlier stays visible here. A real revoke needs a store deletion
path and is its own card.

A library, no CLI. Import it::

    from tools.document_intelligence.sme_evidence import (
        promote_comment, list_assertions, promotions_for)
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

TABLE = "dic_sme_assertions"

#: The comment table this reads to VERIFY a promotion target exists and to copy
#: the attribution off. It is read HERE and nowhere downstream: no evidence
#: seam, no search backend and no source declaration names it.
COMMENT_TABLE = "dic_section_annotations"

#: ``args/entity_currency.yaml`` ``sources[].id``. Written verbatim to
#: ``entity_currency.source``.
SOURCE_ID = "dic_sme_assertions"

#: ``sources[].kind``. Read by ``cortex/search_service.py`` to give the citation
#: its own ``source_type``, so a human's statement is never badged as a feed's.
SOURCE_KIND = "sme_attributed"

#: Where ``as_of`` came from. ``comment_time`` is a DEFAULT, not a statement --
#: the SME wrote the comment then, which is not the same as asserting the fact
#: was true then, and the two must stay tellable apart.
AS_OF_BASES = ("sme_stated", "comment_time")

#: ``author_evidence``'s bases, mapped onto this module's. The claim vocabulary
#: is validated by ONE function (``author_evidence.normalize_assertion``) so a
#: status word cannot mean one thing on an upload and another on a promotion;
#: only the BASIS is respelled, because the clock a defaulted date came from is
#: genuinely a different clock.
_BASIS_FROM_AUTHOR = {"author_stated": "sme_stated", "upload_time": "comment_time"}

#: The whole shape, in plain TEXT so ONE string serves PG and SQLite. Executed
#: by the migration and by ``ensure_table`` on a database the migration has not
#: reached -- a second copy would drift.
DDL = (
    # The table name is spelled LITERALLY here (not through the constant) so
    # tools/db/schema_ownership.py's CREATE TABLE scan can see it.
    """
CREATE TABLE IF NOT EXISTS dic_sme_assertions (
    assertion_id    TEXT PRIMARY KEY,
    -- THE COMMENT THAT WAS PROMOTED. One promotion per comment, enforced by a
    -- unique index: a second promotion of the same comment is a refusal, never
    -- a silent rewrite of what a human already decided.
    ann_id          TEXT NOT NULL,
    doc_id          TEXT NOT NULL DEFAULT '',
    section_id      TEXT NOT NULL DEFAULT '',
    -- The comment VERBATIM, and the span it was anchored to. Quotation and
    -- attribution. NOTHING parses either of them.
    comment         TEXT NOT NULL,
    selected_text   TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL DEFAULT '',
    -- THE CLAIM, supplied by the promoting human and typed. Same fields, same
    -- vocabulary and the same validator as an author's upload declaration.
    entity_type     TEXT NOT NULL,
    namespace       TEXT NOT NULL DEFAULT '',
    entity_key      TEXT NOT NULL,
    entity_label    TEXT NOT NULL,
    entity_version  TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,
    superseded_by   TEXT,
    eol_date        TEXT,
    eos_date        TEXT,
    -- THE SME'S CLOCK. ISO8601. Never ours.
    as_of           TEXT NOT NULL,
    -- sme_stated | comment_time. A defaulted clock says so.
    as_of_basis     TEXT NOT NULL,
    -- WHO SAID IT: the comment's author, COPIED off the comment and never typed
    -- in by the promoter. An attribution the promoter can edit is not one.
    asserted_by     TEXT NOT NULL,
    -- WHO DECIDED IT WAS EVIDENCE, and when. OUR clock.
    promoted_by     TEXT NOT NULL,
    promoted_at     TEXT NOT NULL,
    note            TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI'
)
""",
    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_comment ON {TABLE} (ann_id)",
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_entity ON {TABLE} (entity_type, entity_key)",
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_doc ON {TABLE} (doc_id, promoted_at)",
)


class SMEPromotionError(ValueError):
    """A promotion is refused. Named so a route can turn it into a status."""


class CommentNotFound(SMEPromotionError):
    """No such comment. The route answers 404."""


class AlreadyPromoted(SMEPromotionError):
    """This comment already carries an assertion. The route answers 409."""


# -- helpers ------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def ensure_table(conn) -> None:
    """Create the table on a database the migration has not reached.

    ``CREATE TABLE IF NOT EXISTS`` -- it never alters an existing table, which
    is why the DDL above is the one copy and the migration executes that same
    tuple rather than a hand-written twin.
    """
    for stmt in DDL:
        conn.execute(stmt)


def _connect():
    from tools.db.storage import get_connection

    return get_connection()


def assertion_id(ann_id: str) -> str:
    """Deterministic over the COMMENT, because the comment IS the identity: one
    comment is one promotion, so a repeat addresses the same row -- where it is
    refused rather than written."""
    return "sme-" + hashlib.sha256(str(ann_id).encode("utf-8")).hexdigest()[:24]


# -- the claim ----------------------------------------------------------------

def normalize_claim(raw: Any, *, default_as_of: Optional[str] = None) -> dict:
    """The promoter's typed claim -> the row shape, or raise.

    Delegates every field and the whole status vocabulary to
    ``author_evidence.normalize_assertion``: an author's declaration and an
    SME's promoted comment describe the same kind of fact, and two validators
    for one vocabulary is how two surfaces come to accept different words. Only
    ``as_of_basis`` is respelled -- a defaulted clock here came from the
    COMMENT, not from an upload.
    """
    from tools.document_intelligence.author_evidence import (
        AuthorAssertionError,
        normalize_assertion,
    )

    try:
        claim = normalize_assertion(raw, default_as_of=default_as_of)
    except AuthorAssertionError as exc:
        raise SMEPromotionError(str(exc)) from exc
    claim["as_of_basis"] = _BASIS_FROM_AUTHOR[claim["as_of_basis"]]
    return claim


# -- read ---------------------------------------------------------------------

def _rows(conn, sql: str, params: tuple) -> list[dict]:
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 - an absent table is an empty list, and logged
        logger.warning("sme_evidence: query failed (%s)", exc)
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []


def promotions_for(conn, ann_ids: Iterable[str]) -> dict:
    """``ann_id -> assertion row`` for the comments given.

    What the comments API attaches so the review page can render a promoted
    comment apart from an instruction. A comment ABSENT from this mapping has
    not been promoted, and that absence is the whole of the "cannot be cited"
    property: there is no flag on the comment that could go stale, and no
    second place the answer is written down.
    """
    ids = [str(a) for a in (ann_ids or []) if str(a or "").strip()]
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    rows = _rows(
        conn,
        f"SELECT * FROM {TABLE} WHERE ann_id IN ({placeholders})",  # nosec B608 - constant identifier, bound values
        tuple(ids),
    )
    return {str(r.get("ann_id")): r for r in rows}


def list_assertions(
    doc_id: Optional[str] = None,
    entity_key: Optional[str] = None,
    ann_id: Optional[str] = None,
    limit: int = 200,
    conn=None,
) -> list[dict]:
    """Promoted statements as recorded, newest SME clock first.

    A READ of this table. It is NOT how the drafter learns what an SME
    asserted -- the drafter asks ``cortex.resolve`` / ``entity_currency
    .resolve()``, where this row is ranked against every other source under the
    one declared policy. A reader taking its verdict from here is reading one
    side of a conflict.
    """
    own = conn is None
    if own:
        conn = _connect()
    try:
        sql = f"SELECT * FROM {TABLE}"  # nosec B608 - constant identifier
        clauses, params = [], []
        if doc_id:
            clauses.append("doc_id = %s")
            params.append(str(doc_id))
        if entity_key:
            from tools.currency.entity_currency import normalize_key

            clauses.append("entity_key = %s")
            params.append(normalize_key(entity_key))
        if ann_id:
            clauses.append("ann_id = %s")
            params.append(str(ann_id))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY as_of DESC, promoted_at DESC LIMIT %s"
        params.append(max(1, min(int(limit or 1), 1000)))
        return _rows(conn, sql, tuple(params))
    finally:
        if own:
            conn.close()


def contradicting(conn, claim: dict, *, exclude_ann_id: str = "") -> list[dict]:
    """Promoted assertions about the SAME entity stating a DIFFERENT status.

    Reported by ``promote_comment`` on the promotion it is about to make. Two
    SMEs disagreeing is a fact a reviewer adjudicates; a promotion that landed
    it silently would be indistinguishable from two SMEs agreeing.
    """
    rows = _rows(
        conn,
        f"SELECT * FROM {TABLE} WHERE entity_type = %s AND namespace = %s "  # nosec B608 - constant identifier
        f"AND entity_key = %s AND entity_version = %s",
        (claim["entity_type"], claim["namespace"], claim["entity_key"],
         claim["entity_version"]),
    )
    return [
        r for r in rows
        if str(r.get("ann_id")) != str(exclude_ann_id)
        and str(r.get("status") or "") != claim["status"]
    ]


# -- write --------------------------------------------------------------------

_INSERT = (
    f"INSERT INTO {TABLE} "  # nosec B608 - module constant; every value is bound
    "(assertion_id, ann_id, doc_id, section_id, comment, selected_text, category, "
    " entity_type, namespace, entity_key, entity_label, entity_version, status, "
    " superseded_by, eol_date, eos_date, as_of, as_of_basis, asserted_by, "
    " promoted_by, promoted_at, note, tenant_id, classification) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
)


def read_comment(conn, ann_id: str) -> Optional[dict]:
    """The comment being promoted, or None. The ONE read of the comment table."""
    rows = _rows(
        conn,
        f"SELECT * FROM {COMMENT_TABLE} WHERE ann_id = %s",  # nosec B608 - constant identifier
        (str(ann_id),),
    )
    return rows[0] if rows else None


def promote_comment(
    conn,
    *,
    ann_id: str,
    claim: Any,
    promoted_by: str,
    tenant_id: str = "default",
    classification: str = "CUI",
    refresh_store: bool = True,
) -> dict:
    """Promote ONE comment to an attributed SME assertion.

    Refuses, rather than guessing, on every unknown: no such comment
    (:class:`CommentNotFound`), already promoted (:class:`AlreadyPromoted`), a
    claim outside the closed vocabulary or naming no entity
    (:class:`SMEPromotionError`), or no named promoter -- an unattributed
    promotion of an unattributed remark is precisely the prose this card exists
    to keep out of the evidence chain.

    Does NOT commit; the caller owns the transaction, so a route can write its
    audit row first and abandon the whole promotion if that write fails.

    Returns ``{"assertion", "contradicts", "store"}``. ``store`` is the
    ``entity_currency.backfill`` report for THIS SOURCE ONLY -- the same mapping
    the nightly sweep runs over every declared source, never a second writer of
    currency rows.
    """
    promoter = _clean(promoted_by)
    if not promoter:
        raise SMEPromotionError("a promotion must name the human making it (promoted_by)")

    ensure_table(conn)
    comment = read_comment(conn, ann_id)
    if comment is None:
        raise CommentNotFound(f"no comment {ann_id!r}")

    existing = promotions_for(conn, [ann_id]).get(str(ann_id))
    if existing:
        raise AlreadyPromoted(
            f"comment {ann_id!r} was already promoted by "
            f"{existing.get('promoted_by')} at {existing.get('promoted_at')}"
        )

    # The SME's clock defaults to when THEY wrote the comment, never to now: a
    # promotion made months later must not restamp their statement as today's.
    normalized = normalize_claim(
        claim, default_as_of=str(comment.get("created_at") or "") or None
    )

    # Attribution is COPIED off the comment. The promoter supplies the claim and
    # never the name attached to it.
    asserted_by = _clean(comment.get("author")) or "reviewer"
    conflicts = contradicting(conn, normalized, exclude_ann_id=ann_id)

    now = _now()
    aid = assertion_id(ann_id)
    conn.execute(_INSERT, (
        aid, str(ann_id), str(comment.get("doc_id") or ""),
        str(comment.get("section_id") or ""), str(comment.get("comment") or ""),
        str(comment.get("selected_text") or ""), str(comment.get("category") or ""),
        normalized["entity_type"], normalized["namespace"], normalized["entity_key"],
        normalized["entity_label"], normalized["entity_version"], normalized["status"],
        normalized["superseded_by"], normalized["eol_date"], normalized["eos_date"],
        normalized["as_of"], normalized["as_of_basis"], asserted_by,
        promoter, now, normalized.get("note"),
        tenant_id or "default",
        classification or str(comment.get("classification") or "") or "CUI",
    ))

    store = None
    if refresh_store:
        from tools.currency.entity_currency import backfill

        store = backfill(conn=conn, sources=[SOURCE_ID])
        if store.get("errors"):
            logger.warning(
                "sme_evidence: store refresh for %s reported %s", ann_id, store["errors"]
            )

    assertion = promotions_for(conn, [ann_id]).get(str(ann_id)) or {"assertion_id": aid}
    return {"assertion": assertion, "contradicts": conflicts, "store": store}
