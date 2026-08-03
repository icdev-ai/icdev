# CUI // SP-CTI
"""GovChain tokenization must LAND an asset_token row, not merely try (cxo-trust-04).

The GovChain half of cxo-trust-01. ``tools/blockchain/asset_ledger.py`` passes
``citation_type="asset_token"`` inside a ``try:`` whose ``except Exception: pass``
swallows the ``ValueError`` ``register_citation`` raises for a type absent from
``CITATION_TYPES``. ``reg_id`` therefore stayed ``None``, the ``if reg_id:`` guard
never opened, ``anchor_status`` stayed ``"skipped"``, and the
``blockchain_registry_id``/``blockchain_tx_id`` back-fill never ran — asset
tokenization had never anchored to the chain. Measured 2026-08-02 against live
PostgreSQL: zero ``asset_token`` rows in ``source_citation_registry``.

``tests/provenance/test_cortex_asset_token_registration.py`` (cxo-trust-01) pins
the vocabulary, but its ``test_register_citation_does_not_raise_for_it``
deliberately swallows every non-``ValueError`` exception — including a failed
INSERT — so it asserts the call did not reject the type, NOT that a row exists.
These tests do the read.

``anchor_status`` is asserted to be anything other than ``"skipped"`` rather than
a specific value: without Hyperledger Fabric deps present ``ChainAnchor``
short-circuits to ``"disabled"``, which is a truthful environment report. The
regression being pinned is ``"skipped"`` — the literal the pre-fix code could
never leave, because it means the registry id was falsy.
"""
from __future__ import annotations

import importlib

import pytest

from tools.blockchain import asset_ledger

citation_types = importlib.import_module("tools.provenance.citation_types")

ASSET_TYPE = "government_property"


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    """Temp SQLite DB holding both the govchain tables and the registry.

    ``register_asset`` threads ``db_path`` through to ``register_citation`` and
    ``ChainAnchor``, so one path covers all three writers. ``ICDEV_DB_PATH`` is
    pointed at the same file so the assertion helper reads what they wrote.

    The registry CHECK clause is rendered from ``sqlite_check_clause()`` rather
    than retyped, so this inserts against the constraint the migration ships.
    """
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS source_citation_registry ("
            " id TEXT PRIMARY KEY,"
            " citation_type TEXT NOT NULL "
            + citation_types.sqlite_check_clause()
            + ", source_table TEXT NOT NULL,"
            " source_record_id TEXT NOT NULL,"
            " source_doc TEXT,"
            " source_hash TEXT NOT NULL,"
            " anchor_hash TEXT,"
            " merkle_root TEXT,"
            " blockchain_tx_id TEXT,"
            " classification TEXT DEFAULT 'CUI',"
            " project_id TEXT,"
            " trust_score REAL DEFAULT 0.0,"
            " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _tokenize(db_path):
    result = asset_ledger.register_asset(
        asset_type=ASSET_TYPE,
        serial_number="SN-CXO-0001",
        nsn="1234-00-000-0001",
        custodian="unit-42",
        db_path=db_path,
    )
    assert result["ok"] is True, result
    return result


def _registry_rows(db_path, citation_type="asset_token"):
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, citation_type, source_table, source_record_id, source_hash, "
            "classification FROM source_citation_registry WHERE citation_type = %s",
            (citation_type,),
        )
        cols = ["id", "citation_type", "source_table", "source_record_id",
                "source_hash", "classification"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _asset_row(db_path, asset_id):
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, token_hash, blockchain_registry_id FROM govchain_assets "
            "WHERE id = %s",
            (asset_id,),
        )
        row = cur.fetchone()
        return dict(zip(["id", "token_hash", "blockchain_registry_id"], row)) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# the asset_token row landed
# ---------------------------------------------------------------------------
def test_tokenization_writes_an_asset_token_registry_row(ledger_db):
    """The read cxo-trust-01 would have failed: zero asset_token rows existed."""
    result = _tokenize(ledger_db)

    rows = _registry_rows(ledger_db)
    assert len(rows) == 1, (
        "asset tokenization wrote no source_citation_registry row of type "
        "'asset_token' — this is cxo-trust-01, where register_citation raised "
        "and `except Exception: pass` erased the failure"
    )
    assert rows[0]["source_table"] == "govchain_assets"
    assert rows[0]["source_record_id"] == result["asset_id"]
    assert rows[0]["source_hash"] == result["token_hash"]


def test_anchor_status_is_not_skipped(ledger_db):
    """'skipped' is the literal a falsy reg_id could never leave behind."""
    result = _tokenize(ledger_db)

    assert result["blockchain_anchor_status"] != "skipped", (
        "anchor_status is still 'skipped', which means the `if reg_id:` guard "
        "never opened and tokenization did not reach the chain at all"
    )
    assert result["blockchain_registry_id"], "no registry id returned to the caller"


def test_registry_id_is_backfilled_onto_the_asset(ledger_db):
    """The back-fill UPDATE runs only past the guard — it proves the guard opened."""
    result = _tokenize(ledger_db)

    asset = _asset_row(ledger_db, result["asset_id"])
    assert asset is not None
    assert asset["blockchain_registry_id"] == result["blockchain_registry_id"]
    assert asset["blockchain_registry_id"] == _registry_rows(ledger_db)[0]["id"]


# ---------------------------------------------------------------------------
# discrimination — the same reads must fail on the pre-fix tree
# ---------------------------------------------------------------------------
def test_pre_fix_vocabulary_lands_nothing(ledger_db, monkeypatch):
    """Restore the pre-cxo-trust-01 vocabulary; every assertion above must flip.

    ``is_valid`` reads the module global at call time, so dropping 'asset_token'
    reproduces the shipped bug exactly. Note ``ok`` stays True and the asset row
    is still written: the failure was invisible from the caller's side, which is
    the whole reason it survived. Only the provenance reads expose it.
    """
    monkeypatch.setattr(
        citation_types,
        "CITATION_TYPES",
        tuple(t for t in citation_types.CITATION_TYPES if t != "asset_token"),
    )

    result = _tokenize(ledger_db)

    assert _registry_rows(ledger_db) == []
    assert result["blockchain_anchor_status"] == "skipped"
    assert not result["blockchain_registry_id"]
    assert not _asset_row(ledger_db, result["asset_id"])["blockchain_registry_id"]
