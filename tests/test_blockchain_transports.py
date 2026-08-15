#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the pluggable GovChain anchor transports (trust-anchor-01, D-GC-1).

The headline case is the one that had never been exercised: with EVERY
transport unhealthy an anchor must land in ``govchain_pending_operations`` with
``status='pending'`` and return ``{'status': 'queued', 'tx_id': None}`` — never
a silent drop — and once a transport comes up ``flush_pending()`` must actually
drain it.

The schema fixture here is migration 149's, column for column (``submitted_at``
/ ``error_message``, and NO ``updated_at``). Getting that wrong is what hid the
flush bug: ``tests/test_blockchain.py`` invented an ``updated_at`` column, so
the UPDATE that fails against the real database passed against the fixture.

No Hyperledger Fabric peer, and no ``hfc``, is required by any test in this file.
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Migration 149 schema, verbatim (SQLite branch).
MIGRATION_149_SCHEMA = """
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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

CONFIG_ENABLED = {
    "fabric": {
        "channel_default": "govchain-channel",
        "orderer_default": "orderer.govchain.example.com:7050",
        "cli_path": "peer",
        "cli_timeout_seconds": 60,
        "transport_health_ttl_seconds": 0,  # no caching in tests
        "transports": {
            "fabric_sdk": {"enabled": True, "priority": 10},
            "peer_cli": {"enabled": True, "priority": 20},
            "noop": {"enabled": True, "priority": 90, "healthy": False},
        },
    }
}


@pytest.fixture
def govchain_db(tmp_path):
    """Empty GovChain DB carrying the real migration-149 tables."""
    db_path = tmp_path / "govchain_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(MIGRATION_149_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def registry_db(govchain_db):
    """GovChain DB with one provenance row ready to anchor."""
    conn = sqlite3.connect(str(govchain_db))
    conn.execute(
        """INSERT INTO source_citation_registry
           (id, citation_type, source_table, source_record_id, source_hash, classification)
           VALUES (?,?,?,?,?,?)""",
        ("scr-anchor-01", "sbom", "sbom_records", "sbom-1", "a" * 64, "CUI"),
    )
    conn.commit()
    conn.close()
    return govchain_db


@pytest.fixture(autouse=True)
def _clean_transport_state():
    """Every test starts from a cold config + transport registry."""
    from tools.blockchain.blockchain_config import reset_config

    reset_config()
    yield
    reset_config()


def _no_peer_binary():
    """Force ``peer`` off PATH so peer_cli is unavailable without a subprocess."""
    return patch("tools.blockchain.transports.peer_cli.shutil.which", return_value=None)


def _pending_rows(db_path, status=None):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM govchain_pending_operations"
    params = ()
    if status:
        sql += " WHERE status=?"
        params = (status,)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY id", params).fetchall()]
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# base contract
# ---------------------------------------------------------------------------


class TestTransportBase:
    def test_health_status_vocabulary(self):
        from tools.blockchain.transports.base import (
            HEALTHY_STATUSES, STATUS_DEGRADED, STATUS_OK,
            STATUS_UNAVAILABLE, STATUS_UNREACHABLE,
        )

        assert STATUS_OK in HEALTHY_STATUSES
        assert STATUS_DEGRADED in HEALTHY_STATUSES
        # Unreachable and unavailable must NEVER count as healthy — that is
        # the whole basis for the queue fall-through.
        assert STATUS_UNREACHABLE not in HEALTHY_STATUSES
        assert STATUS_UNAVAILABLE not in HEALTHY_STATUSES

    def test_transport_health_dataclass_roundtrip(self):
        from tools.blockchain.transports.base import STATUS_OK, TransportHealth

        health = TransportHealth(transport_name="t", backend="b", status=STATUS_OK)
        assert health.healthy is True
        assert health.to_dict()["healthy"] is True

    def test_transports_package_never_imports_hfc(self):
        """hfc is an undeclared dependency; importing the package must not need it."""
        import importlib

        for module in (
            "tools.blockchain.transports.base",
            "tools.blockchain.transports.noop",
            "tools.blockchain.transports.peer_cli",
            "tools.blockchain.transports.fabric_sdk",
            "tools.blockchain.transport_registry",
        ):
            assert importlib.import_module(module) is not None

    def test_fabric_sdk_module_has_no_top_level_hfc_import(self):
        source = (BASE_DIR / "tools" / "blockchain" / "transports" / "fabric_sdk.py").read_text(
            encoding="utf-8"
        )
        for line in source.splitlines():
            # A top-level import has no leading whitespace.
            assert not line.startswith(("import hfc", "from hfc")), line


# ---------------------------------------------------------------------------
# no-op transport
# ---------------------------------------------------------------------------


class TestNoOpTransport:
    def test_unhealthy_by_default(self):
        from tools.blockchain.transports.noop import NoOpTransport

        health = NoOpTransport(queue_to_db=False).health()
        assert health.healthy is False
        assert health.status == "unavailable"

    def test_invoke_when_unhealthy_returns_queued_with_null_tx(self):
        from tools.blockchain.transports.noop import NoOpTransport

        result = NoOpTransport(queue_to_db=False).chaincode_invoke(
            "chan", "AuditContract", "StoreMerkleRoot", ["abc"]
        )
        assert result["status"] == "queued"
        assert result["tx_id"] is None

    def test_healthy_flip_accepts_with_synthetic_tx(self):
        from tools.blockchain.transports.noop import SYNTHETIC_TX_PREFIX, NoOpTransport

        transport = NoOpTransport(queue_to_db=False, healthy=True)
        assert transport.health().healthy is True
        result = transport.chaincode_invoke("chan", "AuditContract", "StoreMerkleRoot", ["abc"])
        assert result["status"] == "anchored"
        assert result["tx_id"].startswith(SYNTHETIC_TX_PREFIX)
        assert result["synthetic"] is True

    def test_healthy_from_env(self):
        from tools.blockchain.transports.noop import NoOpTransport

        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "1"}):
            assert NoOpTransport(queue_to_db=False).health().healthy is True

    def test_env_overrides_config_value(self):
        """The shipped config says healthy: false — the env var must still win,
        or it would be a no-op for every real deployment."""
        from tools.blockchain.transports.noop import NoOpTransport

        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "1"}):
            assert NoOpTransport(queue_to_db=False, healthy=False).health().healthy is True
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "0"}):
            assert NoOpTransport(queue_to_db=False, healthy=True).health().healthy is False

    def test_query_never_fabricates_a_result(self):
        from tools.blockchain.transports.noop import NoOpTransport

        # Even when healthy: a no-op holds no ledger, so returning data would
        # let a caller treat invented state as on-chain.
        result = NoOpTransport(queue_to_db=False, healthy=True).chaincode_query(
            "chan", "AuditContract", "GetMerkleRoot", ["abc"]
        )
        assert result["result"] is None

    def test_backward_compatible_alias(self):
        from tools.blockchain.blockchain_config import NoOpFabricClient
        from tools.blockchain.transports.base import AnchorTransport

        client = NoOpFabricClient(queue_to_db=False)
        assert isinstance(client, AnchorTransport)
        assert client.chaincode_invoke("c", "cc", "f", ["a"])["tx_id"] is None


# ---------------------------------------------------------------------------
# peer CLI transport (D-GC-1 — the transport nobody built)
# ---------------------------------------------------------------------------


class TestPeerCliTransport:
    def test_unavailable_when_binary_absent(self):
        from tools.blockchain.transports.peer_cli import PeerCliTransport

        with _no_peer_binary():
            health = PeerCliTransport().health()
        assert health.status == "unavailable"
        assert health.healthy is False
        assert "not on PATH" in health.message

    def test_health_probe_spawns_no_subprocess_when_binary_absent(self):
        """The common case (no Fabric installed) must stay cheap — is_enabled()
        is on the dashboard render path."""
        from tools.blockchain.transports.peer_cli import PeerCliTransport

        with _no_peer_binary():
            with patch("tools.blockchain.transports.peer_cli.subprocess.run") as run:
                PeerCliTransport().health()
        run.assert_not_called()

    def test_healthy_when_peer_version_succeeds(self):
        from tools.blockchain.transports.peer_cli import PeerCliTransport

        completed = _completed(0, "peer:\n Version: 2.5.4\n")
        with patch("tools.blockchain.transports.peer_cli.shutil.which", return_value="/usr/bin/peer"):
            with patch("tools.blockchain.transports.peer_cli.subprocess.run", return_value=completed):
                health = PeerCliTransport(orderer="orderer:7050").health()
        assert health.status == "ok"
        assert health.healthy is True

    def test_degraded_without_orderer(self):
        """Invokes need an orderer. Say so rather than report a clean bill."""
        from tools.blockchain.transports.peer_cli import PeerCliTransport

        completed = _completed(0, "peer:\n Version: 2.5.4\n")
        with patch("tools.blockchain.transports.peer_cli.shutil.which", return_value="/usr/bin/peer"):
            with patch("tools.blockchain.transports.peer_cli.subprocess.run", return_value=completed):
                health = PeerCliTransport(orderer=None).health()
        assert health.status == "degraded"
        assert health.healthy is True  # still worth trying for queries

    def test_unreachable_when_peer_version_fails(self):
        from tools.blockchain.transports.peer_cli import PeerCliTransport

        completed = _completed(1, "", "connection refused")
        with patch("tools.blockchain.transports.peer_cli.shutil.which", return_value="/usr/bin/peer"):
            with patch("tools.blockchain.transports.peer_cli.subprocess.run", return_value=completed):
                health = PeerCliTransport(orderer="orderer:7050").health()
        assert health.status == "unreachable"
        assert health.healthy is False

    def test_invoke_builds_argv_form_and_parses_txid(self):
        from tools.blockchain.transports.peer_cli import PeerCliTransport

        tx = "f" * 64
        completed = _completed(0, "", f"INFO 001 Chaincode invoke successful. txid [{tx}]")
        with patch("tools.blockchain.transports.peer_cli.subprocess.run", return_value=completed) as run:
            result = PeerCliTransport(orderer="orderer:7050").chaincode_invoke(
                "govchain-channel", "AuditContract", "StoreMerkleRoot", ["abc", "{}"]
            )

        assert result["status"] == "anchored"
        assert result["tx_id"] == tx
        assert result["tx_id_confirmed"] is True

        argv = run.call_args[0][0]
        assert run.call_args.kwargs["shell"] is False
        assert argv[:3] == ["peer", "chaincode", "invoke"]
        # Chaincode args are JSON-encoded into ONE -c operand and can never
        # become extra argv entries.
        payload = argv[argv.index("-c") + 1]
        assert '"Args"' in payload and "StoreMerkleRoot" in payload and "abc" in payload

    def test_invoke_nonzero_exit_returns_failed_not_anchored(self):
        from tools.blockchain.transports.peer_cli import PeerCliTransport

        completed = _completed(1, "", "Error: endorsement failure")
        with patch("tools.blockchain.transports.peer_cli.subprocess.run", return_value=completed):
            result = PeerCliTransport(orderer="o:7050").chaincode_invoke("c", "cc", "f", ["a"])
        assert result["status"] == "failed"
        assert result["tx_id"] is None

    def test_invoke_timeout_returns_failed(self):
        import subprocess

        from tools.blockchain.transports.peer_cli import PeerCliTransport

        with patch(
            "tools.blockchain.transports.peer_cli.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="peer", timeout=60),
        ):
            result = PeerCliTransport(orderer="o:7050").chaincode_invoke("c", "cc", "f", ["a"])
        assert result["status"] == "failed"
        assert "timed out" in result["reason"]

    def test_invoke_success_without_txid_is_not_confirmed(self):
        """An anchor with no tx id is weak evidence — say so, do not invent one."""
        from tools.blockchain.transports.peer_cli import PeerCliTransport

        completed = _completed(0, "", "INFO 001 Chaincode invoke successful. result: status:200")
        with patch("tools.blockchain.transports.peer_cli.subprocess.run", return_value=completed):
            result = PeerCliTransport(orderer="o:7050").chaincode_invoke("c", "cc", "f", ["a"])
        assert result["status"] == "anchored"
        assert result["tx_id"] is None
        assert result["tx_id_confirmed"] is False

    def test_query_parses_json_payload(self):
        from tools.blockchain.transports.peer_cli import PeerCliTransport

        completed = _completed(0, '{"root": "abc"}')
        with patch("tools.blockchain.transports.peer_cli.subprocess.run", return_value=completed):
            result = PeerCliTransport().chaincode_query("c", "cc", "GetRoot", ["abc"])
        assert result["status"] == "ok"
        assert result["result"] == {"root": "abc"}


# ---------------------------------------------------------------------------
# fabric-sdk-py transport
# ---------------------------------------------------------------------------


class TestFabricSdkTransport:
    def test_unavailable_reports_sdk_absence_without_raising(self):
        from tools.blockchain.transports.fabric_sdk import FabricSdkTransport

        health = FabricSdkTransport().health()
        # In a stock ICDEV install hfc is absent; if a dev machine has it, the
        # empty net_profile is the other unavailable reason. Either way the
        # transport must be unhealthy and must not raise.
        assert health.healthy is False
        assert health.status == "unavailable"

    def test_invoke_without_sdk_returns_failed_not_exception(self):
        from tools.blockchain.transports.fabric_sdk import FabricSdkTransport

        result = FabricSdkTransport().chaincode_invoke("c", "cc", "f", ["a"])
        assert result["status"] == "failed"
        assert result["tx_id"] is None


# ---------------------------------------------------------------------------
# registry routing / failover
# ---------------------------------------------------------------------------


class TestTransportRegistry:
    def test_build_from_config_registers_all_three_backends(self):
        from tools.blockchain.transport_registry import build_registry_from_config

        registry = build_registry_from_config(CONFIG_ENABLED)
        backends = [t.backend for t in registry.transports()]
        assert backends == ["fabric_sdk", "peer_cli", "noop"]  # priority order

    def test_best_healthy_is_none_when_all_down(self):
        from tools.blockchain.transport_registry import build_registry_from_config

        with _no_peer_binary():
            registry = build_registry_from_config(CONFIG_ENABLED)
            assert registry.best_healthy() is None

    def test_best_healthy_prefers_lowest_priority(self):
        from tools.blockchain.transport_registry import AnchorTransportRegistry
        from tools.blockchain.transports.noop import NoOpTransport

        registry = AnchorTransportRegistry(ttl_seconds=0)
        registry.register(NoOpTransport(queue_to_db=False, healthy=True, priority=90, name="low"))
        registry.register(NoOpTransport(queue_to_db=False, healthy=True, priority=5, name="high"))
        assert registry.best_healthy().name == "high"

    def test_failover_skips_unhealthy_higher_priority(self):
        from tools.blockchain.transport_registry import AnchorTransportRegistry
        from tools.blockchain.transports.noop import NoOpTransport

        registry = AnchorTransportRegistry(ttl_seconds=0)
        registry.register(NoOpTransport(queue_to_db=False, healthy=False, priority=5, name="down"))
        registry.register(NoOpTransport(queue_to_db=False, healthy=True, priority=90, name="up"))
        assert registry.best_healthy().name == "up"

    def test_raising_health_probe_is_unhealthy_not_fatal(self):
        from tools.blockchain.transport_registry import AnchorTransportRegistry
        from tools.blockchain.transports.noop import NoOpTransport

        broken = NoOpTransport(queue_to_db=False, healthy=True, priority=5, name="broken")
        broken.health = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        registry = AnchorTransportRegistry(ttl_seconds=0)
        registry.register(broken)
        registry.register(NoOpTransport(queue_to_db=False, healthy=True, priority=90, name="up"))
        assert registry.best_healthy().name == "up"

    def test_multiple_peer_endpoints_become_ordered_transports(self):
        """Registering one transport per peer IS the failover primitive."""
        from tools.blockchain.transport_registry import build_registry_from_config

        raw = {
            "fabric": {
                "transport_health_ttl_seconds": 0,
                "transports": {
                    "fabric_sdk": {"enabled": False},
                    "noop": {"enabled": False},
                    "peer_cli": {
                        "enabled": True,
                        "priority": 20,
                        "peers": ["peer0.gov1:7051", {"address": "peer1.gov1:7051", "priority": 25}],
                    },
                },
            }
        }
        registry = build_registry_from_config(raw)
        names = [t.name for t in registry.transports()]
        assert names == ["peer_cli:peer0.gov1:7051", "peer_cli:peer1.gov1:7051"]
        assert [t.priority for t in registry.transports()] == [20, 25]

    def test_health_cache_respects_ttl(self):
        from tools.blockchain.transport_registry import AnchorTransportRegistry
        from tools.blockchain.transports.noop import NoOpTransport

        transport = NoOpTransport(queue_to_db=False, healthy=True, name="cached")
        calls = []
        original = transport.health
        transport.health = lambda: (calls.append(1), original())[1]

        registry = AnchorTransportRegistry(ttl_seconds=3600)
        registry.register(transport)
        registry.best_healthy()
        registry.best_healthy()
        assert len(calls) == 1
        registry.best_healthy(force=True)
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# blockchain_config wiring
# ---------------------------------------------------------------------------


class TestBlockchainConfigTransportWiring:
    def test_is_enabled_no_longer_requires_the_sdk(self):
        """The bug this task exists for: HAS_FABRIC was a permanent False."""
        from tools.blockchain.blockchain_config import HAS_FABRIC, BlockchainConfig

        assert HAS_FABRIC is False or HAS_FABRIC is True  # either way it is not the switch
        cfg = BlockchainConfig(_with_healthy_noop())
        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true"}):
            cfg = BlockchainConfig(_with_healthy_noop())
            assert cfg.is_enabled() is True

    def test_is_enabled_false_when_no_transport_healthy(self):
        from tools.blockchain.blockchain_config import BlockchainConfig

        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true"}):
                assert BlockchainConfig(CONFIG_ENABLED).is_enabled() is False

    def test_get_fabric_client_returns_best_healthy_transport(self):
        from tools.blockchain.blockchain_config import BlockchainConfig

        with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true"}):
            client = BlockchainConfig(_with_healthy_noop()).get_fabric_client()
        assert client.health().healthy is True

    def test_get_fabric_client_never_returns_none(self):
        from tools.blockchain.blockchain_config import BlockchainConfig
        from tools.blockchain.transports.base import AnchorTransport

        with _no_peer_binary():
            client = BlockchainConfig(CONFIG_ENABLED).get_fabric_client()
        assert isinstance(client, AnchorTransport)

    def test_transport_health_report_shape(self):
        from tools.blockchain.blockchain_config import BlockchainConfig

        with _no_peer_binary():
            report = BlockchainConfig(CONFIG_ENABLED).transport_health()
        assert report["any_healthy"] is False
        assert report["active_transport"] is None
        assert {e["backend"] for e in report["transports"]} == {"fabric_sdk", "peer_cli", "noop"}


# ---------------------------------------------------------------------------
# ACCEPTANCE — queue-on-all-unhealthy, then drain
# ---------------------------------------------------------------------------


class TestAnchorQueueAndDrain:
    """The path the task exists for, end to end."""

    def test_anchor_provenance_queues_when_every_transport_is_unhealthy(self, registry_db):
        from tools.blockchain.blockchain_config import get_config, reset_config
        from tools.blockchain.chain_anchor import ChainAnchor

        reset_config()
        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true",
                                           "ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "0"}):
                cfg = get_config()
                assert cfg.active_transport() is None, "precondition: no transport is healthy"

                anchor = ChainAnchor(db_path=registry_db)
                result = anchor.anchor_provenance(["scr-anchor-01"])

        # Never a silent drop.
        assert result["status"] == "queued"
        assert result["tx_id"] is None

        rows = _pending_rows(registry_db, status="pending")
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"
        assert rows[0]["operation_type"].startswith("anchor_merkle_root")
        assert rows[0]["payload_hash"]

    def test_flush_pending_drains_the_queue_once_a_transport_is_healthy(self, registry_db):
        from tools.blockchain.blockchain_config import get_config, reset_config
        from tools.blockchain.chain_anchor import ChainAnchor

        # 1. everything down -> queued
        reset_config()
        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true",
                                           "ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "0"}):
                ChainAnchor(db_path=registry_db).anchor_provenance(["scr-anchor-01"])
        assert len(_pending_rows(registry_db, status="pending")) == 1

        # 2. flip the no-op healthy -> flush drains it
        reset_config()
        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true",
                                           "ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "1"}):
                assert get_config().active_transport() is not None
                summary = ChainAnchor(db_path=registry_db).flush_pending()

        assert summary["status"] == "ok"
        assert summary["flushed"] == 1
        assert summary["failed"] == 0

        rows = _pending_rows(registry_db)
        assert len(rows) == 1, "flush must not leave a duplicate behind"
        assert rows[0]["status"] == "flushed"
        # submitted_at is migration 149's column; the old code wrote updated_at,
        # which does not exist, so the UPDATE silently did nothing.
        assert rows[0]["submitted_at"] is not None

    def test_flush_is_idempotent(self, registry_db):
        from tools.blockchain.blockchain_config import reset_config
        from tools.blockchain.chain_anchor import ChainAnchor

        reset_config()
        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true"}):
                ChainAnchor(db_path=registry_db).anchor_provenance(["scr-anchor-01"])

        reset_config()
        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true",
                                           "ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "1"}):
                anchor = ChainAnchor(db_path=registry_db)
                first = anchor.flush_pending()
                second = anchor.flush_pending()

        assert first["flushed"] == 1
        assert second["flushed"] == 0
        assert len(_pending_rows(registry_db)) == 1

    def test_flush_leaves_row_pending_when_transport_drops_mid_flush(self, registry_db):
        """A re-queued anchor must NOT be marked flushed.

        'queued' means the write did not land and _queue_operation inserted a
        NEW row; marking the original flushed would both lie and grow the queue
        by one on every cycle.
        """
        from tools.blockchain.blockchain_config import reset_config
        from tools.blockchain.chain_anchor import ChainAnchor

        reset_config()
        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true"}):
                ChainAnchor(db_path=registry_db).anchor_provenance(["scr-anchor-01"])

        reset_config()
        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true",
                                           "ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "1"}):
                anchor = ChainAnchor(db_path=registry_db)
                anchor._ensure_config()
                with patch.object(anchor, "anchor_merkle_root",
                                  return_value={"status": "queued", "tx_id": None}):
                    summary = anchor.flush_pending()

        assert summary["flushed"] == 0
        assert summary["skipped"] == 1
        assert _pending_rows(registry_db)[0]["status"] == "pending"

    def test_transport_returning_failed_is_queued_not_reported_anchored(self, registry_db):
        """A transport reports failure by RETURNING it, not by raising."""
        from tools.blockchain.blockchain_config import reset_config
        from tools.blockchain.chain_anchor import ChainAnchor

        reset_config()
        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true",
                                           "ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "1"}):
                anchor = ChainAnchor(db_path=registry_db)
                anchor._ensure_config()
                anchor._client = _FailingTransport()
                result = anchor.anchor_merkle_root("b" * 64, {"source": "test"})

        assert result["status"] == "queued"
        assert result["tx_id"] is None
        assert len(_pending_rows(registry_db, status="pending")) == 1


# ---------------------------------------------------------------------------
# dashboard surface (tools.dashboard.pages.provenance)
# ---------------------------------------------------------------------------


class TestBlockchainStatusEndpoint:
    """/api/govchain-provenance/blockchain-status must say WHY anchoring is off.

    It reported ``fabric_enabled: false`` and nothing else, which is
    indistinguishable from "not configured", "peer down" and "SDK missing".
    """

    def _client(self, db_path):
        flask = pytest.importorskip("flask")
        from tools.dashboard.pages import provenance

        app = flask.Flask(__name__)
        app.register_blueprint(provenance.govchain_provenance_api)
        # _storage_conn, never a raw sqlite3 handle: the route's SQL is written
        # for PostgreSQL and only StorageConnection rewrites %s -> ?.
        with patch.object(provenance, "_get_db", lambda: _storage_conn(db_path)):
            yield app.test_client()

    def test_reports_per_transport_health_when_nothing_is_healthy(self, govchain_db):
        from tools.blockchain.blockchain_config import reset_config

        reset_config()
        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true",
                                           "ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "0"}):
                for client in self._client(govchain_db):
                    payload = client.get("/api/govchain-provenance/blockchain-status").get_json()

        assert payload["fabric_enabled"] is False
        assert payload["active_transport"] is None
        # Every backend reports itself, with a reason.
        assert {t["backend"] for t in payload["transports"]} == {"fabric_sdk", "peer_cli", "noop"}
        assert all(t["message"] for t in payload["transports"])

    def test_reports_the_active_transport_when_one_is_healthy(self, govchain_db):
        from tools.blockchain.blockchain_config import reset_config

        reset_config()
        with _no_peer_binary():
            with patch.dict("os.environ", {"ICDEV_BLOCKCHAIN_ENABLED": "true",
                                           "ICDEV_BLOCKCHAIN_NOOP_HEALTHY": "1"}):
                for client in self._client(govchain_db):
                    payload = client.get("/api/govchain-provenance/blockchain-status").get_json()

        assert payload["fabric_enabled"] is True
        assert payload["active_transport"] == "noop"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _storage_conn(db_path):
    """The runtime connection wrapper, pointed at the fixture database."""
    from tools.db.storage import get_connection

    return get_connection(db_path=str(db_path))


def _with_healthy_noop() -> dict:
    return {
        "fabric": {
            "channel_default": "govchain-channel",
            "transport_health_ttl_seconds": 0,
            "transports": {
                "fabric_sdk": {"enabled": False},
                "peer_cli": {"enabled": False},
                "noop": {"enabled": True, "priority": 90, "healthy": True},
            },
        }
    }


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    import subprocess

    return subprocess.CompletedProcess(args=["peer"], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class _FailingTransport:
    name = "failing"

    def chaincode_invoke(self, **kwargs):
        return {"status": "failed", "tx_id": None, "transport": self.name,
                "reason": "endorsement failure"}
