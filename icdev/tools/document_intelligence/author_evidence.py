# CUI // SP-CTI
"""Author-supplied content as a DECLARED currency source (dwr-ev-01).

WHAT THIS IS

An author who uploads a document into the DIC corpus can state, alongside it,
what that document ASSERTS about the currency of the entities it names:
"the Catalyst 6500 is still fielded here as of 2026-08-01, signed, the network
lead". That statement is written to ``dic_author_assertions`` — one row per
(document, entity, version) — and from there into the platform's ONE
entity-currency store (``entity_currency``, cef-fnd-04) under the source id
``dic_author_assertions``, which ``args/entity_currency.yaml`` declares exactly
the way it declares the endoflife.date feed and the curated catalog: an
``evidence_kind``, a column mapping, a verdict map, and a ``precedence`` that
ranks author-supplied content TOP.

THE RULE THAT DOES NOT LIVE HERE

This module contains NO precedence logic. ``entity_currency.resolve()`` already
ranks every source's assertion under the policy the YAML declares and hands the
losers back under ``others`` with ``conflict: true``. Author content joins that
policy as a declared source; it does not get a second resolver. A second copy
of a precedence rule is how two surfaces come to disagree about which source
won, so anything that wants to know whether the author outranks the catalog
asks ``resolve()`` and nothing else.

DISAGREEMENT IS PRESERVED, BY CONSTRUCTION

The store keys rows on (source, entity_type, namespace, entity_key,
entity_version). An author row and a catalog row about the same entity are two
rows under two sources. Writing the author's assertion therefore cannot touch
the catalog's — there is no code path here that reads, updates or deletes
another source's row — and ``resolve()`` returns BOTH, the author's first and
the curated one under ``others``. A NIST EOL feed that says "retired" against an
author who says "still fielded" is a VISIBLE conflict a reviewer adjudicates,
never a silent overwrite in either direction.

TWO CLOCKS, KEPT APART

``as_of`` is the AUTHOR's clock — when the author says the fact was true.
``created_at`` here, and ``observed_at`` in the store, is OURS. An author who
states no date gets the upload time as ``as_of`` and ``as_of_basis:
upload_time`` recorded beside it, so a defaulted clock can never be mistaken
for a stated one.

TRUST IS UNCHANGED

Nothing here calls a model. The author's status word is mapped onto the store's
closed verdict vocabulary by the YAML ``value_map`` — a lookup, not a judgement
— and it reaches a docmod pack only as an ``extraction: structured`` claim off
the ``currency`` backend, the one lane ``tools/doc_modernization/evidence.py``
lets through. A pack still derives its verdict deterministically from typed
fields; this module only adds a source for those fields to come from.

WHAT IS RECORDED, WHAT IS NOT

Every assertion an author makes is kept in ``dic_author_assertions`` with the
document it came with. The STORE keeps one row per (entity, version) for this
source, and the backfill orders rows by ``as_of`` so the author's LATEST
statement is the one it carries — a later upload correcting an earlier one is
the latest fact, and the earlier statement stays readable here. Two authors
disagreeing with EACH OTHER is not modelled as two store rows today; that is
the attributed-SME-assertion shape dwr-ev-02 builds, and it is named rather
than approximated.

A library, no CLI. Import it:

    from tools.document_intelligence.author_evidence import (
        record_assertions, parse_assertions, list_assertions)
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

TABLE = "dic_author_assertions"

#: ``args/entity_currency.yaml`` ``sources[].id`` for this table. The store
#: writes it verbatim to ``entity_currency.source``.
SOURCE_ID = "dic_author_assertions"

#: ``sources[].kind`` — the evidence class, beside external_feed / curated /
#: inventory / derived. A reader of ``entity_currency.source_kind`` can tell an
#: author's statement from a vendor feed without opening the YAML.
SOURCE_KIND = "author_supplied"

#: The AUTHOR's vocabulary — what the upload accepts. These are the KEYS of the
#: ``verdict.value_map`` declared for this source in args/entity_currency.yaml;
#: the mapping onto the store's verdicts lives THERE and only there, and
#: tests/currency/test_author_precedence.py asserts every word here is mapped.
#: Validated here so a typo is refused at the upload rather than stored and
#: silently read as ``unknown``.
AUTHOR_STATUSES = (
    "current",          # the author asserts it is the thing in use / to be on
    "fielded",          # deployed in the author's estate
    "in_use",           # synonym for fielded
    "deprecated",       # the author says it is being retired
    "end_of_support",   # the author says support has ended
    "retired",          # the author says it is gone
    "decommissioned",   # synonym for retired
    "unknown",          # the author names it and asserts nothing
)

#: Where ``as_of`` came from. ``upload_time`` is a DEFAULT, not a statement.
AS_OF_BASES = ("author_stated", "upload_time")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DATE10_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The whole shape, in plain TEXT so ONE string serves PG and SQLite. Executed
#: by the migration (20260908003920) and by ``ingest_orchestrator._ensure_schema``
#: for a database the migration has not reached — a second copy would drift.
#: No CHECK on ``status``: the same call entity_currency made for ``verdict``
#: (a CHECK is a second copy of a vocabulary), and AUTHOR_STATUSES is enforced
#: at the writer.
DDL = (
    # The table name is spelled LITERALLY here (not through the constant) so
    # tools/db/schema_ownership.py's CREATE TABLE scan can see it.
    """
CREATE TABLE IF NOT EXISTS dic_author_assertions (
    assertion_id    TEXT PRIMARY KEY,
    -- The upload that carried the statement. Provenance, not a foreign key:
    -- dic_documents has no FK from anything today and this table follows suit.
    doc_id          TEXT NOT NULL,
    version_id      TEXT,
    -- Open vocabulary, the store's: software_release / hardware_model / ...
    entity_type     TEXT NOT NULL,
    namespace       TEXT NOT NULL DEFAULT '',
    -- Normalized join key (entity_currency.normalize_key) and the author's
    -- verbatim spelling beside it.
    entity_key      TEXT NOT NULL,
    entity_label    TEXT NOT NULL,
    entity_version  TEXT NOT NULL DEFAULT '',
    -- The author's own word — AUTHOR_STATUSES. Mapped to a verdict by the YAML.
    status          TEXT NOT NULL,
    superseded_by   TEXT,
    eol_date        TEXT,
    eos_date        TEXT,
    -- THE AUTHOR'S CLOCK. ISO8601. Never ours.
    as_of           TEXT NOT NULL,
    -- author_stated | upload_time. A defaulted clock says so.
    as_of_basis     TEXT NOT NULL,
    asserted_by     TEXT,
    note            TEXT,
    -- OUR clock.
    created_at      TEXT NOT NULL,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI'
)
""",
    # One statement per (document, entity, version): re-ingesting the same
    # document updates it rather than growing a duplicate.
    f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_identity "
    f"ON {TABLE} (doc_id, entity_type, namespace, entity_key, entity_version)",
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_entity ON {TABLE} (entity_type, entity_key)",
    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_tenant ON {TABLE} (tenant_id, as_of)",
)


class AuthorAssertionError(ValueError):
    """An author assertion is malformed. Named so a route can 400 on it."""


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_key(value: Any) -> str:
    """The store's own join key — reused, never re-implemented."""
    from tools.currency.entity_currency import normalize_key

    return normalize_key(value)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _iso_or_none(value: Any, field_name: str) -> Optional[str]:
    """An ISO date or datetime, or None. Anything else is REFUSED rather than
    stored — a date that does not parse would be dropped by the store's
    comparison and the author would never learn their date was ignored."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    text = str(value).strip()
    if _DATE10_RE.match(text):
        return text
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthorAssertionError(f"{field_name} {text!r} is not an ISO date") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _date10_or_none(value: Any, field_name: str) -> Optional[str]:
    iso = _iso_or_none(value, field_name)
    return iso[:10] if iso else None


# ── the assertion ─────────────────────────────────────────────────────────────

def normalize_assertion(raw: Any, *, default_as_of: Optional[str] = None) -> dict:
    """One author-supplied mapping -> the row shape, or raise.

    Accepts the short spellings a form is likely to send (``entity`` for
    ``entity_label``, ``type`` for ``entity_type``, ``version`` for
    ``entity_version``) and refuses anything it cannot make sense of. Nothing
    is guessed: a missing entity, a missing type or a status outside
    AUTHOR_STATUSES is an error, not an ``unknown``.
    """
    if not isinstance(raw, dict):
        raise AuthorAssertionError("an author assertion must be a mapping")

    label = _clean(raw.get("entity_label") or raw.get("entity") or raw.get("label"))
    if not label:
        raise AuthorAssertionError("author assertion names no entity (entity_label)")

    entity_type = _normalize_key(raw.get("entity_type") or raw.get("type"))
    if not entity_type:
        raise AuthorAssertionError(f"author assertion for {label!r} names no entity_type")

    status = _clean(raw.get("status") or raw.get("verdict")).casefold().replace("-", "_")
    if status not in AUTHOR_STATUSES:
        raise AuthorAssertionError(
            f"author assertion for {label!r}: status {status!r} is not one of "
            f"{list(AUTHOR_STATUSES)}"
        )

    stated = raw.get("as_of")
    if stated not in (None, ""):
        as_of = _iso_or_none(stated, "as_of")
        basis = "author_stated"
    else:
        as_of = default_as_of or _now()
        basis = "upload_time"

    return {
        "entity_type": entity_type,
        "namespace": _normalize_key(raw.get("namespace") or raw.get("vendor")),
        "entity_key": _normalize_key(label),
        "entity_label": label,
        "entity_version": _clean(raw.get("entity_version") or raw.get("version")),
        "status": status,
        "superseded_by": _clean(raw.get("superseded_by")) or None,
        "eol_date": _date10_or_none(raw.get("eol_date"), "eol_date"),
        "eos_date": _date10_or_none(raw.get("eos_date"), "eos_date"),
        "as_of": as_of,
        "as_of_basis": basis,
        "asserted_by": _clean(raw.get("asserted_by")) or None,
        "note": _clean(raw.get("note")) or None,
    }


def parse_assertions(raw: Any, *, default_as_of: Optional[str] = None) -> list[dict]:
    """A form field / request value -> validated assertions, or raise.

    ``raw`` may be a JSON string (the multipart form case), a list of mappings,
    or empty. A malformed field is refused whole: recording half of what an
    author declared, silently, is worse than recording none of it and saying so.
    """
    if raw in (None, "", b""):
        return []
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise AuthorAssertionError(f"author_assertions is not valid JSON: {exc}") from exc
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise AuthorAssertionError("author_assertions must be a list of mappings")
    out = []
    for i, item in enumerate(raw):
        try:
            out.append(normalize_assertion(item, default_as_of=default_as_of))
        except AuthorAssertionError as exc:
            raise AuthorAssertionError(f"author_assertions[{i}]: {exc}") from exc
    return out


def assertion_id(doc_id: str, a: dict) -> str:
    """Deterministic over the identity tuple, so a re-ingest addresses the same
    row (the same reasoning as ``CurrencyAssertion.record_id``)."""
    ident = "|".join((
        str(doc_id), a["entity_type"], a["namespace"], a["entity_key"], a["entity_version"],
    ))
    return "aa-" + hashlib.sha256(ident.encode("utf-8")).hexdigest()[:24]


# ── db ────────────────────────────────────────────────────────────────────────

def _connect():
    from tools.db.storage import get_connection

    return get_connection()


def ensure_table(conn) -> None:
    """Create the table on a database the migration has not reached.

    ``CREATE TABLE IF NOT EXISTS`` — it never alters an existing table, which is
    why the DDL above is the one copy and the migration executes the same
    string.
    """
    for stmt in DDL:
        conn.execute(stmt)


_INSERT = (
    f"INSERT INTO {TABLE} "  # nosec B608 - module constant; every value is bound
    "(assertion_id, doc_id, version_id, entity_type, namespace, entity_key, "
    " entity_label, entity_version, status, superseded_by, eol_date, eos_date, "
    " as_of, as_of_basis, asserted_by, note, created_at, tenant_id, classification) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (doc_id, entity_type, namespace, entity_key, entity_version) "
    "DO UPDATE SET "
    "    version_id = excluded.version_id, "
    "    entity_label = excluded.entity_label, "
    "    status = excluded.status, "
    "    superseded_by = excluded.superseded_by, "
    "    eol_date = excluded.eol_date, "
    "    eos_date = excluded.eos_date, "
    "    as_of = excluded.as_of, "
    "    as_of_basis = excluded.as_of_basis, "
    "    asserted_by = excluded.asserted_by, "
    "    note = excluded.note, "
    "    created_at = excluded.created_at"
)


def record_assertions(
    conn,
    *,
    doc_id: str,
    assertions: Iterable[dict],
    version_id: Optional[str] = None,
    tenant_id: str = "default",
    classification: str = "CUI",
    asserted_by: Optional[str] = None,
    uploaded_at: Optional[str] = None,
    refresh_store: bool = True,
) -> dict:
    """Write an upload's assertions, then refresh the store for THIS source.

    Two writes, one transaction of the caller's: the rows here, and the
    ``entity_currency`` rows derived from them through
    ``entity_currency.backfill(sources=[SOURCE_ID])`` — the SAME mapping code
    the nightly sweep runs over every declared source, so there is one writer
    of currency assertions and this is not a second one. Does not commit; the
    caller owns the transaction (``ingest_file`` commits the document, its
    version and its assertions together).

    Returns ``{"recorded", "assertion_ids", "store": <backfill report | None>}``.
    ``store`` is None when the refresh was not asked for; a refresh that FAILED
    carries the error in the report rather than raising, because the document
    is already written and the store can be re-derived by the sweep — but it is
    reported, never swallowed.
    """
    rows = [a if _is_normalized(a) else normalize_assertion(a, default_as_of=uploaded_at)
            for a in (assertions or [])]
    if not rows:
        return {"recorded": 0, "assertion_ids": [], "store": None}

    now = _now()
    ids = []
    for a in rows:
        aid = assertion_id(doc_id, a)
        conn.execute(_INSERT, (
            aid, doc_id, version_id, a["entity_type"], a["namespace"], a["entity_key"],
            a["entity_label"], a["entity_version"], a["status"], a["superseded_by"],
            a["eol_date"], a["eos_date"], a["as_of"], a["as_of_basis"],
            a.get("asserted_by") or asserted_by, a.get("note"), now,
            tenant_id or "default", classification or "CUI",
        ))
        ids.append(aid)

    store = None
    if refresh_store:
        from tools.currency.entity_currency import backfill

        store = backfill(conn=conn, sources=[SOURCE_ID])
        if store.get("errors"):
            logger.warning(
                "author_evidence: store refresh for %s reported %s", doc_id, store["errors"]
            )
    return {"recorded": len(ids), "assertion_ids": ids, "store": store}


def _is_normalized(a: Any) -> bool:
    return isinstance(a, dict) and "as_of_basis" in a and "entity_key" in a


def list_assertions(
    doc_id: Optional[str] = None,
    entity_key: Optional[str] = None,
    limit: int = 200,
    conn=None,
) -> list[dict]:
    """Author statements as recorded, newest author clock first.

    A READ of this table, offered for the brokered connector and the UI. It is
    NOT the way the drafter learns what an author asserted — the drafter asks
    ``cortex.resolve`` / ``entity_currency.resolve``, where the author's row is
    ranked against every other source under the one policy.
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
            clauses.append("entity_key = %s")
            params.append(_normalize_key(entity_key))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY as_of DESC, created_at DESC LIMIT %s"
        params.append(max(1, min(int(limit or 1), 1000)))
        try:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        except Exception as exc:  # noqa: BLE001 — an absent table is an empty list, and logged
            logger.warning("author_evidence: list failed (%s)", exc)
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            return []
    finally:
        if own:
            conn.close()
