#!/usr/bin/env python3
# CUI // SP-CTI
"""Canonical citation-type vocabulary (oss-cite-01).

The `source_citation_registry.citation_type` CHECK constraint used to be a
hardcoded list inside migration 149, duplicated again in
`tools/db/schema/pg_consolidated.sql`. Adding a value meant editing SQL in two
places and hoping nothing else disagreed — exactly what the repo guardrail
"SQL CHECK constraints: derive from Python constants, never hardcode" exists to
prevent.

This module is that constant. Migration 295 renders its CHECK clause from
:func:`check_constraint_sql`, and `test_citation_types.py` asserts the shipped
SQL still matches, so a value added here without regenerating the constraint
fails CI rather than failing at INSERT time in production.

Adding a type:
  1. add it to :data:`CITATION_TYPES` with a one-line rationale
  2. write a migration using ``check_constraint_sql()``
  3. update ``tools/db/schema/pg_consolidated.sql``
  4. run the tests — they check all three agree
"""
from __future__ import annotations

from typing import Tuple

#: Every legal value of ``source_citation_registry.citation_type``.
#:
#: Order is the historical one from migration 149 so a regenerated constraint
#: diffs cleanly against the original; new values append.
CITATION_TYPES: Tuple[str, ...] = (
    "hitl",                  # human-in-the-loop citation (wf_citations)
    "rag",                   # retrieved chunk (rag_chunks)
    "prov_entity",           # PROV entity (prov_entities)
    "prov_activity",         # PROV activity
    "canvas_ai",             # canvas AI decision (canvas_ai_decisions)
    "slsa",                  # SLSA attestation
    "sbom",                  # SBOM record (sbom_records)
    "compliance_evidence",   # compliance evidence chain
    "agent_decision",        # autonomous agent decision
    "manual",                # hand-entered
    # oss-cite-01: a page fetched from the web. Without this a fetched URL could
    # not be registered as a first-class citation, so nothing the content
    # filter (oss-filter-01/02) extracts could satisfy the TRUST invariant that
    # inline [source: ...] citations validate against a persisted provenance
    # record. Fetch specifics live in web_fetch_provenance.
    "web",
    # cxo-trust-01: the Cortex governance pipeline's provenance gate. It has
    # passed citation_type="cortex" since it shipped, which is not in this
    # vocabulary, so register_citation() raised ValueError, governance.py caught
    # it and recorded provenance="warn". Measured 2026-08-02: 0 of 285 registry
    # rows were type 'cortex' and no Cortex operation had ever cleanly passed
    # governance. Because governance.fail_closed ships false, nothing blocked
    # and nothing alerted.
    "cortex",
    # cxo-trust-01: GovChain asset tokenization, the SAME bug found
    # independently. tools/blockchain/asset_ledger.py passes "asset_token"; the
    # raise is swallowed by a try/except with an `if reg_id:` guard, so reg_id
    # stayed None, anchor_status stayed "skipped", and the registry_id/tx_id
    # back-fill never ran — asset tokenization has never anchored to the chain.
    "asset_token",
    # trust-anchor-02: one TRUST gate verdict, as anchorable evidence. The
    # registry row's source_hash is the composed leaf
    # sha256(artifact_hash|findings_hash|delta_chain_hash|approver) rendered by
    # tools/provenance/trust_validation.py, and source_doc carries the four
    # components so the leaf can be RECOMPUTED at anchor time rather than
    # trusted. Anchoring runs on the existing 30-minute govchain_anchor reflex —
    # a trust_validation row is swept by ChainAnchor.periodic_anchor like any
    # other unanchored citation, so no new reflex exists to go inert.
    "trust_validation",
)

#: Types whose provenance detail lives in a companion table.
DETAIL_TABLES = {
    "web": "web_fetch_provenance",
}


def is_valid(citation_type: str) -> bool:
    """True when *citation_type* is a legal registry value."""
    return citation_type in CITATION_TYPES


def check_constraint_sql(column: str = "citation_type") -> str:
    """Render the PostgreSQL CHECK clause for *column* from the constant.

    Single source of truth for the SQL: migrations call this instead of
    retyping the list.

        >>> check_constraint_sql().startswith("CHECK (citation_type IN (")
        True
    """
    values = ", ".join(f"'{t}'" for t in CITATION_TYPES)
    return f"CHECK ({column} IN ({values}))"


def sqlite_check_clause(column: str = "citation_type") -> str:
    """CHECK clause in the inline form used by SQLite CREATE TABLE statements."""
    values = ",".join(f"'{t}'" for t in CITATION_TYPES)
    return f"CHECK({column} IN ({values}))"
