#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for the ICDEV™ GovChain blockchain subsystem.

Covers: ChainAnchor, provenance_verifier, source_citation_registry,
        NoOpFabricClient air-gap queue, and update_blockchain_anchor.

All tests run against an in-memory SQLite DB with the full blockchain schema
(hash-chained audit_trail + source_citation_registry + govchain_pending_operations).
No live Hyperledger Fabric peer is required.
"""

import hashlib
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Blockchain-aware test schema (superset of conftest audit_trail)
# Adds hash-chain columns from migration 149 and blockchain tables.
# ---------------------------------------------------------------------------
BLOCKCHAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_trail (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    project_id  TEXT,
    details     TEXT,
    classification TEXT DEFAULT 'CUI',
    ip_address  TEXT,
    session_id  TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    hash        TEXT,
    previous_hash TEXT,
    signature   TEXT
);

CREATE TABLE IF NOT EXISTS source_citation_registry (
    id TEXT PRIMARY KEY,
    citation_type TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_doc TEXT,
    source_hash TEXT,
    anchor_hash TEXT,
    merkle_root TEXT,
    blockchain_tx_id TEXT,
    classification TEXT DEFAULT 'CUI',
    project_id TEXT,
    trust_score REAL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Migration 149's columns, verbatim. This fixture previously invented an
-- `updated_at` column that exists in neither the PostgreSQL nor the SQLite
-- DDL, which is precisely what hid flush_pending()'s broken UPDATE: it passed
-- here and silently failed against every real database.
CREATE TABLE IF NOT EXISTS govchain_pending_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    submitted_at TIMESTAMP,
    error_message TEXT
);
"""


@pytest.fixture
def blockchain_db(tmp_path):
    """Blockchain-capable test DB with full schema."""
    db_path = tmp_path / "blockchain_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(BLOCKCHAIN_SCHEMA)
    conn.close()
    return db_path


@pytest.fixture
def blockchain_db_with_audit(blockchain_db):
    """Blockchain DB pre-populated with 3 audit entries."""
    conn = sqlite3.connect(str(blockchain_db))
    conn.row_factory = sqlite3.Row

    entries = [
        ("login", "alice", "user_login", "proj-1", "First entry", "CUI"),
        ("deploy", "bob", "deploy_app", "proj-1", "Second entry", "CUI"),
        ("audit", "alice", "run_scan", "proj-1", "Third entry", "CUI"),
    ]
    genesis = "0" * 64
    prev_hash = genesis

    for event_type, actor, action, project_id, details, classification in entries:
        content = "|".join([actor, action, event_type, details, classification, "", ""])
        row_hash = hashlib.sha256(content.encode()).hexdigest()
        conn.execute(
            """INSERT INTO audit_trail
               (event_type, actor, action, project_id, details, classification, hash, previous_hash)
               VALUES (?,?,?,?,?,?,?,?)""",
            (event_type, actor, action, project_id, details, classification, row_hash, prev_hash),
        )
        prev_hash = row_hash

    conn.commit()
    conn.close()
    return blockchain_db


# ---------------------------------------------------------------------------
# ChainAnchor tests
# ---------------------------------------------------------------------------


class TestChainAnchorQueueFallback:
    """ChainAnchor always falls back to govchain_pending_operations when Fabric is unavailable."""

    def test_anchor_audit_batch_queues_when_disabled(self, blockchain_db_with_audit):
        from tools.blockchain.chain_anchor import ChainAnchor
        from tools.blockchain.blockchain_config import reset_config

        reset_config()
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "false"}):
            anchor = ChainAnchor(db_path=blockchain_db_with_audit)
            result = anchor.anchor_audit_batch([1, 2, 3])

        assert result["status"] in ("queued", "disabled", "empty")

    def test_queue_operation_persists_to_db(self, blockchain_db):
        from tools.blockchain.chain_anchor import ChainAnchor
        from tools.blockchain.blockchain_config import reset_config

        reset_config()
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "false"}):
            anchor = ChainAnchor(db_path=blockchain_db)
            result = anchor._queue_operation(
                "anchor_merkle_root",
                "abc123def456" * 4,
                {"source": "test"},
            )

        assert result["status"] == "queued"

        conn = sqlite3.connect(str(blockchain_db))
        row = conn.execute("SELECT * FROM govchain_pending_operations WHERE status='pending'").fetchone()
        conn.close()

        assert row is not None
        assert "anchor_merkle_root" in row[1]  # operation_type
        assert row[3] == "pending"  # status

    def test_anchor_audit_batch_empty_ids_returns_empty(self, blockchain_db):
        from tools.blockchain.chain_anchor import ChainAnchor

        anchor = ChainAnchor(db_path=blockchain_db)
        # IDs that don't exist
        result = anchor.anchor_audit_batch([9999, 9998])
        assert result["status"] in ("empty", "disabled")

    def test_periodic_anchor_returns_summary_dict(self, blockchain_db_with_audit):
        from tools.blockchain.chain_anchor import ChainAnchor
        from tools.blockchain.blockchain_config import reset_config

        reset_config()
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "false"}):
            anchor = ChainAnchor(db_path=blockchain_db_with_audit)
            result = anchor.periodic_anchor()

        assert isinstance(result, dict)
        # Either disabled or returned a summary with expected keys
        if result.get("status") != "disabled":
            assert "audit_batches" in result
            assert "errors" in result

    def test_anchor_provenance_queues_registry_ids(self, blockchain_db):
        from tools.blockchain.chain_anchor import ChainAnchor
        from tools.blockchain.blockchain_config import reset_config

        # Pre-insert a registry entry
        conn = sqlite3.connect(str(blockchain_db))
        conn.execute(
            """INSERT INTO source_citation_registry
               (id, citation_type, source_table, source_record_id, source_hash, classification)
               VALUES (?,?,?,?,?,?)""",
            ("scr-test001", "sbom", "sbom_records", "sbom-1", "a" * 64, "CUI"),
        )
        conn.commit()
        conn.close()

        reset_config()
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "false"}):
            anchor = ChainAnchor(db_path=blockchain_db)
            result = anchor.anchor_provenance(["scr-test001"])

        assert result["status"] in ("queued", "disabled")


# ---------------------------------------------------------------------------
# NoOpFabricClient tests
# ---------------------------------------------------------------------------


class TestNoOpFabricClient:
    def test_chaincode_invoke_returns_queued(self):
        from tools.blockchain.blockchain_config import NoOpFabricClient

        client = NoOpFabricClient(queue_to_db=False)
        result = client.chaincode_invoke("chan", "AuditContract", "StoreMerkleRoot", ["abc"])
        assert result["status"] == "queued"
        assert result["tx_id"] is None

    def test_chaincode_query_returns_queued(self):
        from tools.blockchain.blockchain_config import NoOpFabricClient

        client = NoOpFabricClient(queue_to_db=False)
        result = client.chaincode_query("chan", "AuditContract", "GetHash", ["abc"])
        assert result["status"] == "queued"


# ---------------------------------------------------------------------------
# BlockchainConfig tests
# ---------------------------------------------------------------------------


class TestBlockchainConfig:
    def test_disabled_by_env_var(self):
        from tools.blockchain.blockchain_config import BlockchainConfig, reset_config

        reset_config()
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "false"}):
            cfg = BlockchainConfig({"fabric": {"channel_default": "govchain-channel"}})
            assert not cfg.is_enabled()

    def test_enabled_env_var_requires_a_healthy_transport(self):
        """is_enabled() asks the transport registry, not HAS_FABRIC.

        It used to be ``self._enabled and HAS_FABRIC``; since hfc is in neither
        requirements.txt nor pyproject.toml that constant was permanently False
        and every anchor in the platform reached the no-op. See
        tests/test_blockchain_transports.py for the transport-level coverage.
        """
        from tools.blockchain.blockchain_config import BlockchainConfig, reset_config

        reset_config()
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true"}):
            cfg = BlockchainConfig({"fabric": {"channel_default": "govchain-channel"}})
            assert cfg.is_enabled() == (cfg.active_transport() is not None)

    def test_air_gap_flag(self):
        from tools.blockchain.blockchain_config import BlockchainConfig, reset_config

        reset_config()
        cfg = BlockchainConfig({"air_gapped": {"enabled": True}})
        assert cfg.is_air_gapped() is True
        assert not cfg.is_enabled()

    def test_defaults(self):
        from tools.blockchain.blockchain_config import BlockchainConfig, reset_config

        reset_config()
        cfg = BlockchainConfig({})
        assert cfg.channel() == "govchain-channel"
        assert cfg.anchor_batch_size() == 100
        assert cfg.required_confirmations() == 3


# ---------------------------------------------------------------------------
# provenance_verifier tests
# ---------------------------------------------------------------------------


class TestProvenanceVerifier:
    def test_verify_audit_integrity_not_found(self, blockchain_db):
        from tools.blockchain.provenance_verifier import verify_audit_integrity

        result = verify_audit_integrity(99999, db_path=blockchain_db)
        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    def test_verify_audit_integrity_basic(self, blockchain_db_with_audit):
        from tools.blockchain.provenance_verifier import verify_audit_integrity

        result = verify_audit_integrity(1, db_path=blockchain_db_with_audit)
        assert "ok" in result
        assert "hash_valid" in result
        assert "chain_valid" in result
        assert "blockchain_verified" in result

    def test_verify_citation_not_found(self, blockchain_db):
        from tools.blockchain.provenance_verifier import verify_citation

        result = verify_citation("scr-doesnotexist", db_path=blockchain_db)
        assert result["ok"] is False

    def test_verify_citation_found(self, blockchain_db):
        from tools.blockchain.provenance_verifier import verify_citation

        conn = sqlite3.connect(str(blockchain_db))
        conn.execute(
            """INSERT INTO source_citation_registry
               (id, citation_type, source_table, source_record_id, source_hash,
                merkle_root, blockchain_tx_id, classification, trust_score)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            ("scr-abc123", "sbom", "sbom_records", "sbom-1",
             "b" * 64, "c" * 64, "tx-001", "CUI", 0.9),
        )
        conn.commit()
        conn.close()

        result = verify_citation("scr-abc123", db_path=blockchain_db)
        assert result["ok"] is True
        assert result["source_hash_present"] is True
        assert result["merkle_root_present"] is True
        assert result["blockchain_tx_present"] is True
        assert result["blockchain_verified"] is True

    def test_verify_citation_unanchored(self, blockchain_db):
        from tools.blockchain.provenance_verifier import verify_citation

        conn = sqlite3.connect(str(blockchain_db))
        conn.execute(
            """INSERT INTO source_citation_registry
               (id, citation_type, source_table, source_record_id, source_hash, classification, trust_score)
               VALUES (?,?,?,?,?,?,?)""",
            ("scr-unanchored", "sbom", "sbom_records", "sbom-2", "d" * 64, "CUI", 0.3),
        )
        conn.commit()
        conn.close()

        result = verify_citation("scr-unanchored", db_path=blockchain_db)
        assert result["ok"] is True
        assert result["blockchain_verified"] is False

    def test_generate_verification_report_empty_project(self, blockchain_db):
        from tools.blockchain.provenance_verifier import generate_verification_report

        result = generate_verification_report("proj-nonexistent", db_path=blockchain_db)
        assert "audit" in result
        assert "citations" in result
        assert result["audit"]["total"] == 0
        assert result["overall_ok"] is True


# ---------------------------------------------------------------------------
# source_citation_registry (provenance.registry) tests
# ---------------------------------------------------------------------------


class TestSourceCitationRegistry:
    def test_register_citation_returns_id(self, blockchain_db):
        from tools.provenance.registry import register_citation

        reg_id = register_citation(
            citation_type="sbom",
            source_table="sbom_records",
            source_record_id="sbom-test-1",
            source_hash="e" * 64,
            project_id="proj-reg-test",
            db_path=blockchain_db,
        )
        assert reg_id.startswith("scr-")

    def test_register_and_retrieve(self, blockchain_db):
        from tools.provenance.registry import register_citation, get_citations_for_project

        reg_id = register_citation(
            citation_type="slsa",
            source_table="slsa_attestations",
            source_record_id="slsa-1",
            source_hash="f" * 64,
            project_id="proj-retrieve",
            db_path=blockchain_db,
        )
        assert reg_id

        rows = get_citations_for_project("proj-retrieve", db_path=blockchain_db)
        assert len(rows) == 1
        assert rows[0]["id"] == reg_id
        assert rows[0]["citation_type"] == "slsa"

    def test_update_blockchain_anchor(self, blockchain_db):
        from tools.provenance.registry import register_citation, update_blockchain_anchor, get_citation_by_hash

        source_hash = "a1b2c3" * 10 + "a1b2"
        reg_id = register_citation(
            citation_type="sbom",
            source_table="sbom_records",
            source_record_id="sbom-anchor-test",
            source_hash=source_hash,
            project_id="proj-anchor",
            db_path=blockchain_db,
        )
        assert reg_id

        ok = update_blockchain_anchor(
            reg_id,
            merkle_root="m" * 64,
            blockchain_tx_id="tx-fabric-abc123",
            db_path=blockchain_db,
        )
        assert ok is True

        entry = get_citation_by_hash(source_hash, db_path=blockchain_db)
        assert entry is not None
        assert entry["merkle_root"] == "m" * 64
        assert entry["blockchain_tx_id"] == "tx-fabric-abc123"

    def test_update_anchor_nonexistent_id_returns_true(self, blockchain_db):
        from tools.provenance.registry import update_blockchain_anchor

        # UPDATE on a non-existent row is not an error in SQLite — returns True
        ok = update_blockchain_anchor("scr-ghost", "m" * 64, "tx-ghost", db_path=blockchain_db)
        assert ok is True

    def test_has_cross_reference_single_table(self, blockchain_db):
        from tools.provenance.registry import register_citation, has_cross_reference

        shared_hash = "shared" * 10 + "ha"
        reg_id = register_citation(
            citation_type="sbom",
            source_table="sbom_records",
            source_record_id="sbom-x",
            source_hash=shared_hash,
            db_path=blockchain_db,
        )
        # Only one table — no cross-reference
        assert has_cross_reference(reg_id, db_path=blockchain_db) is False

    def test_has_cross_reference_multiple_tables(self, blockchain_db):
        from tools.provenance.registry import register_citation, has_cross_reference

        shared_hash = "cross" * 12 + "ref0"
        reg_id1 = register_citation(
            citation_type="sbom",
            source_table="sbom_records",
            source_record_id="sbom-cr",
            source_hash=shared_hash,
            db_path=blockchain_db,
        )
        register_citation(
            citation_type="slsa",
            source_table="slsa_attestations",
            source_record_id="slsa-cr",
            source_hash=shared_hash,
            db_path=blockchain_db,
        )
        assert has_cross_reference(reg_id1, db_path=blockchain_db) is True

    def test_get_citation_by_hash_missing(self, blockchain_db):
        from tools.provenance.registry import get_citation_by_hash

        result = get_citation_by_hash("0" * 64, db_path=blockchain_db)
        assert result is None


# ---------------------------------------------------------------------------
# govchain_pending_operations integrity
# ---------------------------------------------------------------------------


class TestGovchainPendingOperations:
    def test_multiple_queued_ops_accumulate(self, blockchain_db):
        from tools.blockchain.chain_anchor import ChainAnchor
        from tools.blockchain.blockchain_config import reset_config

        reset_config()
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "false"}):
            anchor = ChainAnchor(db_path=blockchain_db)
            for i in range(5):
                anchor._queue_operation("test_op", f"hash{i:04d}" * 16, {"i": i})

        conn = sqlite3.connect(str(blockchain_db))
        count = conn.execute(
            "SELECT COUNT(*) FROM govchain_pending_operations WHERE status='pending'"
        ).fetchone()[0]
        conn.close()

        assert count == 5

    def test_pending_ops_status_is_pending(self, blockchain_db):
        from tools.blockchain.chain_anchor import ChainAnchor
        from tools.blockchain.blockchain_config import reset_config

        reset_config()
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "false"}):
            anchor = ChainAnchor(db_path=blockchain_db)
            anchor._queue_operation("anchor_merkle_root", "f" * 64, {"batch": 1})

        conn = sqlite3.connect(str(blockchain_db))
        row = conn.execute("SELECT status FROM govchain_pending_operations ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()

        assert row[0] == "pending"


# ---------------------------------------------------------------------------
# flush_pending tests
# ---------------------------------------------------------------------------


class TestFlushPending:
    def test_flush_returns_disabled_when_no_deps(self, blockchain_db):
        from tools.blockchain.chain_anchor import ChainAnchor
        from tools.blockchain.blockchain_config import reset_config

        reset_config()
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "false"}):
            anchor = ChainAnchor(db_path=blockchain_db)
            # Force _cfg to be None so flush_pending hits "disabled" path
            anchor._cfg = None
            result = anchor.flush_pending()

        assert result["status"] in ("disabled", "fabric_unavailable")
        assert result["flushed"] == 0

    def test_flush_fabric_unavailable_when_disabled(self, blockchain_db):
        from tools.blockchain.chain_anchor import ChainAnchor
        from tools.blockchain.blockchain_config import BlockchainConfig, reset_config

        reset_config()
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "false"}):
            anchor = ChainAnchor(db_path=blockchain_db)
            # Simulate _cfg initialized but Fabric disabled
            anchor._cfg = BlockchainConfig({})
            result = anchor.flush_pending()

        assert result["status"] == "fabric_unavailable"
        assert result["flushed"] == 0
        assert result["failed"] == 0

    def test_flush_returns_ok_with_empty_queue(self, blockchain_db):
        from tools.blockchain.chain_anchor import ChainAnchor
        from tools.blockchain.blockchain_config import reset_config

        reset_config()
        anchor = ChainAnchor(db_path=blockchain_db)
        # Simulate Fabric enabled but flushed = 0 since queue is empty
        mock_cfg = MagicMock()
        mock_cfg.is_enabled.return_value = True
        anchor._cfg = mock_cfg

        result = anchor.flush_pending()
        assert result["status"] == "ok"
        assert result["flushed"] == 0
