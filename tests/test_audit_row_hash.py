# CUI // SP-CTI
"""exa-audit-01 — one audit row-hash recipe, pinned to the byte.

Migration 149's hash chain is only evidence if the writer and both verifiers
agree exactly. These tests do two things:

1. Pin the recipe — field order, separator, encoding, null handling — against
   literal expected bytes and a literal expected digest. Adding a column to
   ``audit_trail`` cannot silently change the digest without failing here.
2. Prove the online verifier and the offline bundle verifier resolve to the
   SAME function object, so the two can never drift by construction.

The digest literals below were computed under the recipe as it stood BEFORE the
helper was extracted. If a change makes them fail, it has changed the meaning of
every ``audit_trail.hash`` already written and of every published case bundle.
"""

import hashlib

import pytest

from icdev.tools.audit import row_hash as rh

# ---------------------------------------------------------------------------
# The pinned row and its pinned digest
# ---------------------------------------------------------------------------

PINNED_ROW = {
    "id": 4242,
    "project_id": "proj-exa",
    "event_type": "agent_action",
    "actor": "agent:claude",
    "action": "egress_attempt",
    "details": "POST https://example.test",
    "classification": "CUI",
    "ip_address": "127.0.0.1",
    "session_id": "sess-exa-audit-01",
}

PINNED_CONTENT = (
    "4242|proj-exa|agent_action|agent:claude|egress_attempt|"
    "POST https://example.test|CUI|127.0.0.1|sess-exa-audit-01"
)

PINNED_DIGEST = "beafa532a871e76a9039e3b90fc7d3ef256f4aaafaa97329ddc98f543b1caba1"


# ---------------------------------------------------------------------------
# Recipe constants
# ---------------------------------------------------------------------------

def test_field_order_is_pinned():
    """Not alphabetical, not the table's column order — the join order."""
    assert rh.AUDIT_HASH_FIELDS == (
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


def test_separator_encoding_and_algorithm_are_pinned():
    assert rh.AUDIT_HASH_SEPARATOR == "|"
    assert rh.AUDIT_HASH_ENCODING == "utf-8"
    assert rh.AUDIT_HASH_ALGORITHM == "sha256"
    assert rh.GENESIS_HASH == "0" * 64
    assert len(rh.GENESIS_HASH) == 64


# ---------------------------------------------------------------------------
# The bytes
# ---------------------------------------------------------------------------

def test_pre_digest_string_is_byte_identical():
    assert rh.audit_row_content(PINNED_ROW) == PINNED_CONTENT


def test_pre_digest_bytes_are_utf8_of_that_string():
    content = rh.audit_row_content(PINNED_ROW)
    assert content.encode(rh.AUDIT_HASH_ENCODING) == PINNED_CONTENT.encode("utf-8")


def test_digest_matches_the_literal_pinned_hex():
    assert rh.compute_audit_row_hash(PINNED_ROW) == PINNED_DIGEST


def test_digest_is_sha256_over_exactly_those_bytes():
    """No salt, no length prefix, no trailing newline — the whole recipe."""
    assert PINNED_DIGEST == hashlib.sha256(PINNED_CONTENT.encode("utf-8")).hexdigest()


def test_a_new_field_appended_to_the_row_does_not_change_the_digest():
    """A column added to audit_trail must not silently re-key the chain.

    Only the nine pinned fields feed the digest. If a future change wants a new
    field in the hash it must edit AUDIT_HASH_FIELDS, which fails the pinning
    test above — deliberately, loudly.
    """
    extended = dict(PINNED_ROW, some_new_column_from_a_future_migration="value")
    assert rh.compute_audit_row_hash(extended) == PINNED_DIGEST


def test_field_order_is_load_bearing():
    """Two fields swapped is a different digest — the order really matters."""
    swapped = dict(PINNED_ROW)
    swapped["actor"], swapped["action"] = swapped["action"], swapped["actor"]
    assert rh.compute_audit_row_hash(swapped) != PINNED_DIGEST


# ---------------------------------------------------------------------------
# Null handling — falsy-coerce, preserved verbatim from the original recipe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("absent", [None, "", 0])
def test_falsy_values_render_as_empty_string(absent):
    row = dict(PINNED_ROW, details=absent)
    assert rh.audit_row_content(row) == PINNED_CONTENT.replace(
        "POST https://example.test", ""
    )


def test_a_missing_key_renders_the_same_as_null():
    partial = {k: v for k, v in PINNED_ROW.items() if k != "ip_address"}
    nulled = dict(PINNED_ROW, ip_address=None)
    assert rh.audit_row_content(partial) == rh.audit_row_content(nulled)


def test_empty_row_is_eight_separators():
    assert rh.audit_row_content({}) == "|" * (len(rh.AUDIT_HASH_FIELDS) - 1)


def test_non_ascii_is_hashed_as_utf8_not_the_platform_codepage():
    row = dict(PINNED_ROW, details="dépôt — café")
    content = rh.audit_row_content(row)
    assert rh.compute_audit_row_hash(row) == hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Row shapes — the online verifier hands in a DB row, not a dict
# ---------------------------------------------------------------------------

def test_sqlite3_row_hashes_identically_to_the_equivalent_dict():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(f"{f} TEXT" for f in rh.AUDIT_HASH_FIELDS)
    conn.execute(f"CREATE TABLE audit_trail ({cols})")
    conn.execute(
        "INSERT INTO audit_trail VALUES (%s)" % ", ".join("?" * len(rh.AUDIT_HASH_FIELDS)),
        tuple(PINNED_ROW[f] for f in rh.AUDIT_HASH_FIELDS),
    )
    row = conn.execute("SELECT * FROM audit_trail").fetchone()
    try:
        assert not hasattr(row, "get")          # the branch we care about
        assert rh.compute_audit_row_hash(row) == PINNED_DIGEST
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# One definition — the whole point of the task
# ---------------------------------------------------------------------------

def test_bundle_format_reexports_the_canonical_helper():
    from icdev.tools.agent_case import bundle_format as bf

    assert bf.compute_audit_row_hash is rh.compute_audit_row_hash
    assert bf.AUDIT_HASH_FIELDS is rh.AUDIT_HASH_FIELDS
    assert bf.GENESIS_HASH == rh.GENESIS_HASH


def test_provenance_verifier_uses_the_canonical_helper():
    from icdev.tools.blockchain import provenance_verifier as pv

    assert pv.compute_audit_row_hash is rh.compute_audit_row_hash
    assert pv.GENESIS_HASH == rh.GENESIS_HASH


def test_both_copies_of_the_helper_agree_byte_for_byte():
    """``tools/`` mirrors ``icdev/tools/`` as two real files, not one redirect.

    ``importlib.import_module("tools.audit.row_hash")`` resolves through the
    filesystem, so the mirror is a genuinely separate module object and CAN
    drift. Pin both the source bytes and the digest — an edit to one copy that
    is not mirrored to the other fails here rather than in production.
    """
    import importlib
    from pathlib import Path

    mirror = importlib.import_module("tools.audit.row_hash")
    assert mirror.compute_audit_row_hash(PINNED_ROW) == PINNED_DIGEST
    assert mirror.AUDIT_HASH_FIELDS == rh.AUDIT_HASH_FIELDS

    repo_root = Path(__file__).resolve().parents[1]
    assert (repo_root / "tools/audit/row_hash.py").read_bytes() == \
           (repo_root / "icdev/tools/audit/row_hash.py").read_bytes()


def test_no_module_recomputes_the_recipe_inline():
    """A pipe-joined field list outside row_hash.py is a drifting fourth copy."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    needle = '"|".join'
    offenders = []
    for rel in (
        "icdev/tools/agent_case/bundle_format.py",
        "icdev/tools/blockchain/provenance_verifier.py",
        "tools/agent_case/bundle_format.py",
        "tools/blockchain/provenance_verifier.py",
    ):
        source = (repo_root / rel).read_text(encoding="utf-8")
        if needle in source or "AUDIT_HASH_FIELDS = (" in source:
            offenders.append(rel)
    assert offenders == [], f"inline audit-hash recipe still present in {offenders}"
