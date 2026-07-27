#!/usr/bin/env python3
"""Creating the database and vector store when they don't exist. CUI // SP-CTI.

`icdev setup` wrote a DSN and `icdev-init-db` created tables, but both assumed
something was already listening and that a database of that name existed.
Neither holds on a fresh machine.

Four things must exist before RAG works, and each fails differently:

    server -> database+role -> pgvector extension -> schema

The third is the one that bites: the extension is only *installable* if the
running image ships pgvector, so on stock `postgres:16` the CREATE EXTENSION in
migration 044 fails and every embedding write raises afterwards.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from tools.cli import provision_db as pd


# --------------------------------------------------------------------------- #
# DSN handling
# --------------------------------------------------------------------------- #


def test_parse_dsn_extracts_every_part():
    p = pd.parse_dsn("postgresql://u:s3cret@db.example:6543/mydb")
    assert p == {"user": "u", "password": "s3cret", "host": "db.example",
                 "port": 6543, "database": "mydb"}


def test_parse_dsn_defaults_are_sane():
    p = pd.parse_dsn("postgresql:///")
    assert p["port"] == 5432 and p["database"] == "icdev"


def test_with_port_changes_only_the_port():
    assert pd.with_port("postgresql://u:pw@h:5432/d", 5433) == "postgresql://u:pw@h:5433/d"


def test_maintenance_dsn_targets_the_bootstrap_database():
    """You cannot connect to a database in order to ask whether it exists."""
    dsn = pd._maintenance_dsn(pd.parse_dsn("postgresql://u:pw@h:5432/mydb"))
    assert dsn.endswith("/" + pd.MAINTENANCE_DB)
    assert "mydb" not in dsn


# --------------------------------------------------------------------------- #
# Status: which layer is missing
# --------------------------------------------------------------------------- #


def test_missing_layers_are_reported_in_dependency_order():
    st = pd.DbStatus(backend="postgresql", server_reachable=True)
    assert st.missing() == ["database", "vector-extension", "schema"]


def test_an_unreachable_server_hides_the_rest():
    """Reporting 'database missing' when nothing is listening is noise."""
    st = pd.DbStatus(backend="postgresql", server_reachable=False)
    assert st.missing() == ["server"]


def test_ready_requires_the_vector_extension():
    """A database with tables but no pgvector is NOT ready — embeddings fail."""
    st = pd.DbStatus(backend="postgresql", server_reachable=True,
                     database_exists=True, schema_present=True, vector_enabled=False)
    assert not st.ready


def test_sqlite_is_ready_once_the_schema_exists():
    st = pd.DbStatus(backend="sqlite", schema_present=True)
    assert st.ready and st.missing() == []


def test_sqlite_check_on_an_absent_file(tmp_path):
    st = pd.check_sqlite(tmp_path / "nope.db")
    assert not st.database_exists and st.missing() == ["schema"]


def test_sqlite_check_detects_the_schema(tmp_path):
    import sqlite3

    f = tmp_path / "icdev.db"
    con = sqlite3.connect(str(f))
    con.execute("CREATE TABLE rag_chunks (id TEXT)")
    con.commit()
    con.close()
    assert pd.check_sqlite(f).schema_present


# --------------------------------------------------------------------------- #
# Port conflicts — the first-run failure on a machine that already has Postgres
# --------------------------------------------------------------------------- #


def _listener():
    """A socket that accepts but speaks no known protocol."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)

    def _accept():
        try:
            while True:
                conn, _ = srv.accept()
                conn.close()
        except OSError:
            pass

    threading.Thread(target=_accept, daemon=True).start()
    time.sleep(0.2)
    return srv, srv.getsockname()[1]


def test_a_free_port_is_reported_free():
    assert pd.inspect_port("127.0.0.1", 59999)["occupied"] is False


def test_a_non_postgres_listener_is_identified_as_such():
    """Docker would fail to bind and report a generic 'port already allocated',
    which reads as a Docker problem rather than 'something else owns this'."""
    srv, port = _listener()
    try:
        got = pd.inspect_port("127.0.0.1", port, timeout=1.0)
        assert got["occupied"] is True
        assert got["is_postgres"] is False
    finally:
        srv.close()


def test_find_free_port_falls_back_past_a_taken_one():
    srv, taken = _listener()
    try:
        assert pd.find_free_port("127.0.0.1", taken) != taken
    finally:
        srv.close()


def test_fallback_ports_are_adjacent_and_recognisable():
    """A random high port would be unrecognisable in `docker ps` later."""
    assert pd._PORT_FALLBACKS[0] == 5433


def test_compose_port_rewrite_moves_only_the_host_side(tmp_path):
    """Postgres inside the container always listens on 5432 regardless of what
    the host publishes — rewriting both sides would break the container."""
    f = tmp_path / "docker-compose.yml"
    f.write_text('services:\n  postgres:\n    ports:\n      - "5432:5432"\n',
                 encoding="utf-8")
    assert pd._rewrite_compose_port(f, 5433)
    assert '"5433:5432"' in f.read_text(encoding="utf-8")


def test_compose_rewrite_is_a_noop_when_the_file_is_absent(tmp_path):
    assert pd._rewrite_compose_port(tmp_path / "missing.yml", 5433) is False


def test_compose_rewrite_does_nothing_on_dry_run(tmp_path):
    f = tmp_path / "docker-compose.yml"
    f.write_text('      - "5432:5432"\n', encoding="utf-8")
    pd._rewrite_compose_port(f, 5433, dry_run=True)
    assert '"5432:5432"' in f.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Provisioning is non-destructive
# --------------------------------------------------------------------------- #


def test_dry_run_plans_every_layer_without_touching_anything(monkeypatch):
    monkeypatch.setattr(pd, "check_postgres",
                        lambda *a, **k: pd.DbStatus(backend="postgresql",
                                                    server_reachable=True))
    out = pd.provision("postgresql://u:pw@h:5432/d", dry_run=True)
    assert [s["step"] for s in out["steps"]] == ["database", "vector-store", "schema"]
    assert all(not a["done"] for s in out["steps"] for a in s["actions"])


def test_nothing_is_planned_when_everything_exists(monkeypatch):
    monkeypatch.setattr(pd, "check_postgres", lambda *a, **k: pd.DbStatus(
        backend="postgresql", server_reachable=True, database_exists=True,
        vector_enabled=True, schema_present=True))
    out = pd.provision("postgresql://u:pw@h:5432/d", dry_run=True)
    assert out["ok"] and out["steps"] == []


def test_provision_reports_the_dsn_it_ended_up_using(monkeypatch):
    """If the port was relocated, the caller must persist the NEW dsn."""
    monkeypatch.setattr(pd, "check_postgres", lambda *a, **k: pd.DbStatus(
        backend="postgresql", server_reachable=True, database_exists=True,
        vector_enabled=True, schema_present=True))
    assert pd.provision("postgresql://u:pw@h:5432/d", dry_run=True)["dsn"].endswith("/d")


def test_an_existing_postgres_is_reused_rather_than_duplicated(monkeypatch):
    """Two servers would be a silent second source of truth."""
    monkeypatch.setattr(pd, "check_postgres",
                        lambda *a, **k: pd.DbStatus(backend="postgresql"))
    monkeypatch.setattr(pd, "inspect_port", lambda *a, **k: {
        "occupied": True, "is_postgres": True, "detail": "PostgreSQL"})
    called = []

    def _boom(*_a, **_k):
        called.append(1)
        return pd.ProvisionResult()

    monkeypatch.setattr(pd, "start_postgres_container", _boom)
    pd.provision("postgresql://u:pw@h:5432/d", use_docker=True, dry_run=True)
    assert not called, "must not start a container beside an existing PostgreSQL"


def test_no_free_port_fails_with_an_actionable_hint(monkeypatch):
    monkeypatch.setattr(pd, "check_postgres",
                        lambda *a, **k: pd.DbStatus(backend="postgresql"))
    monkeypatch.setattr(pd, "inspect_port", lambda *a, **k: {
        "occupied": True, "is_postgres": False, "detail": "in use by nginx"})
    monkeypatch.setattr(pd, "find_free_port", lambda *a, **k: None)
    out = pd.provision("postgresql://u:pw@h:5432/d", use_docker=True)
    assert not out["ok"]
    assert "--sqlite" in out["hint"]


# --------------------------------------------------------------------------- #
# Guidance
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("system,expect", [
    ("Windows", "winget"), ("Darwin", "brew"), ("Linux", "apt-get")])
def test_native_install_guidance_per_os(system, expect):
    assert any(expect in line for line in pd.install_guidance(system))


def test_guidance_mentions_pgvector_everywhere():
    """Installing postgres WITHOUT pgvector leaves RAG broken in a way whose
    error message never mentions the installer."""
    for system in ("Windows", "Darwin", "Linux"):
        assert any("pgvector" in line for line in pd.install_guidance(system))


def test_docker_missing_is_reported_clearly(monkeypatch, tmp_path):
    monkeypatch.setattr(pd.shutil, "which", lambda _n: None)
    r = pd.start_postgres_container(tmp_path / "docker-compose.yml")
    assert not r.ok and "docker" in r.error


def test_missing_compose_file_points_at_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(pd.shutil, "which", lambda _n: "/usr/bin/docker")
    r = pd.start_postgres_container(tmp_path / "nope.yml")
    assert not r.ok and "icdev setup" in r.error
