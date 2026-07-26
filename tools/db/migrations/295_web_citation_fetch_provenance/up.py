#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 295: make a fetched web page citeable (oss-cite-01).

Two changes, both idempotent and both safe on PostgreSQL and SQLite:

1. **`citation_type` gains `'web'`.** The CHECK constraint created by migration
   149 hardcoded ten values, so a fetched page could not be registered in
   ``source_citation_registry`` at all. Rather than hardcode eleven, the
   constraint is now re-derived from ``tools/provenance/registry.py::
   CITATION_TYPES`` — the same single-source-of-truth repair shape migration
   271 used for the ACE state constraints. Adding a future citation type means
   editing the Python tuple and re-running a repair, never hand-editing SQL.

   Note this must be an explicit repair, not a ``CREATE TABLE IF NOT EXISTS``:
   that statement never alters a constraint on a table that already exists, so
   on every live database the stale ten-value CHECK would survive untouched.

2. **`web_fetch_provenance` is created.** Provenance for a fetched URL was
   previously a url plus a content hash plus a metadata JSON blob on whatever
   row happened to hold it. The new table persists what a citation actually
   needs to be checkable later: requested URL, final URL after redirects, the
   redirect chain, HTTP status, ``fetched_at``, content hash, and the
   ETag/Last-Modified revalidators when the server sent them.

Down is a no-op — see down.py.
"""


def up(conn):
    from tools.provenance.registry import repair_citation_type_constraint
    from tools.provenance.web_citation import TABLE, init_tables

    constraint = repair_citation_type_constraint(conn)
    init_tables(conn)

    return {
        "status": "ok",
        "citation_type_constraint": constraint,
        "table": TABLE,
    }
