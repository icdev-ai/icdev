# CUI // SP-CTI
"""Author statements are reachable THROUGH the broker, and only there (dwr-ev-01).

The grant, the descriptor and the connector are pinned together, then a real
brokered fetch is driven against a real SQLite database: the rows come back,
one ``databridge_agent_access_log`` row is written, and an unauthorised role
is refused and audited. The connector reads a LOCAL table, so nothing here
opens a socket — and the descriptor's loopback-only allowlist is what the
shipped-grant tests derive "off-box" from.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from icdev.tools.databridge import broker

REPO_ROOT = Path(__file__).resolve().parents[2]
GRANT_PATH = REPO_ROOT / "args" / "databridge_agent_access.yaml"
CONNECTIONS_PATH = REPO_ROOT / "args" / "databridge_connections.yaml"

CONNECTOR = "icdev_author_evidence"
CONNECTION_ID = "dic-author-evidence-local"
TABLE = "author_assertions"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _grant() -> dict:
    raw = yaml.safe_load(GRANT_PATH.read_text(encoding="utf-8")) or {}
    (grant,) = [g for g in raw["connectors"] if g.get("name") == CONNECTOR]
    return grant


def _descriptor() -> dict:
    raw = yaml.safe_load(CONNECTIONS_PATH.read_text(encoding="utf-8")) or {}
    (entry,) = [c for c in raw["connections"] if c.get("id") == CONNECTION_ID]
    return entry


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

def test_the_grant_names_the_connector_and_the_descriptor_links_back():
    grant, descriptor = _grant(), _descriptor()
    assert grant["connection_id"] == CONNECTION_ID
    assert descriptor["connector_name"] == CONNECTOR
    assert grant["tables"] == [TABLE]
    assert grant["agents"], "an empty agents list grants every agent"
    assert grant["classification_ceiling"] == "UNCLASSIFIED"
    # No credential of any kind: it reads the local database.
    assert descriptor["auth_method"] == "none"
    assert not descriptor.get("auth_secret_ref")
    hosts = descriptor["config"]["egress_allowlist"]
    assert hosts and all(str(h).lower() in LOOPBACK_HOSTS for h in hosts)


def test_the_connector_registers_under_the_brokers_own_registry():
    from icdev.tools.databridge.registry import get_connector_instance

    instance = get_connector_instance(CONNECTOR)
    assert instance is not None
    assert instance.list_tables() == [TABLE]
    assert instance.capabilities.supports_write is False


# ---------------------------------------------------------------------------
# A real brokered read
# ---------------------------------------------------------------------------

@pytest.fixture
def live_db(tmp_path, monkeypatch):
    db = tmp_path / "author-evidence.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE databridge_agent_access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL DEFAULT 'unknown',
            connector_name TEXT NOT NULL DEFAULT '',
            table_name TEXT NOT NULL DEFAULT '',
            decision TEXT NOT NULL DEFAULT 'denied'
                CHECK(decision IN ('allowed','denied')),
            reason TEXT NOT NULL DEFAULT '',
            rows_returned INTEGER NOT NULL DEFAULT 0,
            redactions_applied INTEGER NOT NULL DEFAULT 0,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            classification TEXT NOT NULL DEFAULT 'CUI',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE db_connections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            connector_type TEXT NOT NULL,
            connector_name TEXT NOT NULL,
            config_yaml TEXT NOT NULL,
            auth_method TEXT NOT NULL DEFAULT 'none',
            auth_secret_ref TEXT,
            sync_direction TEXT DEFAULT 'read',
            status TEXT DEFAULT 'configured',
            health_status TEXT DEFAULT 'unknown',
            last_health_check TEXT,
            last_sync TEXT,
            sync_cadence_minutes INTEGER DEFAULT 60,
            classification TEXT DEFAULT 'UNCLASSIFIED',
            impact_level TEXT DEFAULT 'IL4',
            tenant_id TEXT NOT NULL DEFAULT 'default',
            project_id TEXT,
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.delenv("ICDEV_AIRGAP", raising=False)

    from icdev.tools.databridge.seed_connections import seed
    from tools.db.storage import get_connection
    from tools.document_intelligence.author_evidence import ensure_table, record_assertions

    assert CONNECTION_ID in seed()["created"]
    c = get_connection()
    ensure_table(c)
    record_assertions(
        c, doc_id="dic_doc_a", refresh_store=False,
        assertions=[{"entity": "model-zeta-1", "type": "chassis", "status": "fielded",
                     "as_of": "2026-08-01", "asserted_by": "lead"}],
    )
    c.commit()
    c.close()
    return db


def _audit_rows(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT agent_id, connector_name, table_name, decision, rows_returned "
            "FROM databridge_agent_access_log ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_an_authorised_role_reads_the_statements_and_one_audit_row_is_written(live_db):
    outcome = broker.fetch("docgen_analyst", CONNECTOR, TABLE, limit=5)

    assert outcome.ok is True, outcome.error
    assert outcome.audited is True
    assert outcome.row_count == 1
    row = outcome.rows[0]
    assert (row["entity_key"], row["status"], row["as_of"], row["as_of_basis"]) == (
        "model-zeta-1", "fielded", "2026-08-01", "author_stated",
    )
    assert outcome.connector_status == "ok"
    assert _audit_rows(live_db) == [("docgen_analyst", CONNECTOR, TABLE, "allowed", 1)]


def test_an_unauthorised_role_is_refused_and_audited(live_db):
    outcome = broker.fetch("security_analyst", CONNECTOR, TABLE, limit=5)

    assert outcome.ok is False
    assert "is not granted" in outcome.error
    assert outcome.rows == []
    assert _audit_rows(live_db) == [("security_analyst", CONNECTOR, TABLE, "denied", 0)]


def test_the_seam_never_reads_this_table_directly():
    """The drafter's evidence path reads the STORE through cortex.resolve; a
    private read of dic_author_assertions anywhere on it would be one side of a
    conflict skipping the authorization and the access-log row."""
    import ast

    for rel in ("tools/doc_modernization/evidence.py", "tools/cortex/search_service.py",
                "tools/cortex/entity_resolution.py"):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(source)
        strings = {n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert not any("dic_author_assertions" in s and "SELECT" in s.upper()
                       for s in strings), rel
        assert "author_evidence" not in {
            (n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
        }, rel
