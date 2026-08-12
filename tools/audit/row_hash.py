#!/usr/bin/env python3
# CUI // SP-CTI
"""The ONE audit-row hash recipe — writer and verifiers all call this.

Migration 149 added ``hash`` / ``previous_hash`` / ``signature`` to ``audit_trail``
and 14 sibling audit tables. A hash chain only detects tampering if every party
computes the digest the same way, so the recipe lives here and nowhere else:

* ``tools/blockchain/provenance_verifier.py::verify_audit_integrity`` — the
  online verifier, reading rows back out of the database.
* ``tools/agent_case/bundle_format.py::compute_audit_row_hash`` — the offline
  verifier, reading rows out of a portable forensic bundle on a machine that
  never had the database.
* the chain writer, which populates ``hash``/``previous_hash`` on insert.

If any two of those disagree by a single field, the chain reports tampering on
every row and the evidence is worthless. ``tests/test_audit_row_hash.py`` pins
the exact byte sequence so adding a column cannot silently change the digest.

This module is deliberately stdlib-only and touches no database: the offline
verifier must import it on a machine that has the bundle and nothing else.

Usage:
    from icdev.tools.audit.row_hash import compute_audit_row_hash, GENESIS_HASH
"""

import hashlib

# --- The recipe ------------------------------------------------------------
#
# Every element below is load-bearing and pinned by test_audit_row_hash.py.
# Changing any of them invalidates every hash already written.

#: Field order. NOT alphabetical, NOT the column order in the table — it is the
#: join order the original verifier used, and it is now the contract.
AUDIT_HASH_FIELDS = (
    "id",
    "project_id",
    "event_type",
    "actor",
    "action",
    "details",
    "classification",
    "ip_address",
    "session_id",
)

#: Field separator in the pre-digest string.
AUDIT_HASH_SEPARATOR = "|"

#: Encoding applied to the pre-digest string before hashing.
AUDIT_HASH_ENCODING = "utf-8"

#: Digest algorithm name, as recorded in bundles and reports.
AUDIT_HASH_ALGORITHM = "sha256"

#: ``previous_hash`` of the first row in a chain.
GENESIS_HASH = "0" * 64


def _field_value(row, field: str) -> str:
    """One field, rendered for the digest.

    Null handling is falsy-coerce, not None-coerce: ``None``, a missing key, an
    empty string and ``0`` all render as ``""``. That is what the original
    ``str(v or "")`` did, rows have already been hashed under it, and a bundle
    is a published artifact — so it is preserved verbatim rather than tightened.

    Accepts any row shape: a plain dict, a ``sqlite3.Row``, or a psycopg row.
    """
    try:
        value = row.get(field) if hasattr(row, "get") else row[field]
    except (KeyError, IndexError, TypeError):
        value = None
    return str(value or "")


def audit_row_content(row) -> str:
    """The exact string that gets hashed — separated out so a test can pin it.

    Callers verifying a chain should hash via :func:`compute_audit_row_hash`;
    this exists so a failure can show *what* differed, not just that two hex
    digests are unequal.
    """
    return AUDIT_HASH_SEPARATOR.join(_field_value(row, f) for f in AUDIT_HASH_FIELDS)


def compute_audit_row_hash(row) -> str:
    """SHA-256 hex digest of an audit row, per the migration-149 chain recipe."""
    return hashlib.sha256(audit_row_content(row).encode(AUDIT_HASH_ENCODING)).hexdigest()
