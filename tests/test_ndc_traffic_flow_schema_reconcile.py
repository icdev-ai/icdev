# CUI // SP-CTI
"""ndc-fix-04 — nc_traffic_flows schema-drift regression guard.

TrafficFlowEngine writes ``src_zone`` / ``dst_zone`` / ``app_type`` but the
init_db.py DDL historically created the table with
``source_zone`` / ``destination_zone`` / ``application_type``. On any database where
init_db created the table first, ``POST .../traffic-flows`` returned HTTP 500
(UndefinedColumn / no such column). Confirmed live on PG by ndc-e2e-01 (PR #378).

These tests create ``nc_traffic_flows`` via the REAL init_db DDL (rendered SCHEMA),
then exercise the engine against it — proving the DDL and the engine agree. The
pre-existing traffic-flow tests hand-roll their own schema, so they never caught
the drift; these deliberately do not.
"""
from __future__ import annotations

import re

from tools.network.db import init_db as m
from tools.network.traffic_flow import TrafficFlowEngine


def _init_sqlite_db(tmp_path, monkeypatch):
    """Build a fresh network_canvas DB from the real DDL and return a connection."""
    db = tmp_path / "nc_tfw_reconcile.db"
    monkeypatch.setattr(m, "DB_PATH", db)
    monkeypatch.setattr(m, "_NC_BACKEND", "sqlite")
    m.init_db()
    return m.get_connection()


class TestDDLEngineAgreement:
    def test_schema_uses_engine_column_names(self):
        """The rendered DDL must expose the engine's column names, not the drifted set."""
        # Isolate the nc_traffic_flows CREATE TABLE block.
        block = re.search(
            r"CREATE TABLE IF NOT EXISTS nc_traffic_flows\b.*?\);",
            m.SCHEMA,
            re.DOTALL,
        )
        assert block, "nc_traffic_flows CREATE TABLE not found in SCHEMA"
        ddl = block.group(0)
        for col in ("src_zone", "dst_zone", "app_type"):
            assert re.search(rf"\b{col}\b", ddl), f"{col} missing from DDL"
        for legacy in ("source_zone", "destination_zone", "application_type"):
            assert not re.search(rf"\b{legacy}\b", ddl), f"legacy column {legacy} still in DDL"

    def test_check_constraint_renders_on_app_type(self):
        """@@CK9@@ must render CHECK(app_type IN (...)) so the parity reconciler tracks it."""
        assert "CHECK(app_type IN (" in m.SCHEMA
        assert "CHECK(application_type IN (" not in m.SCHEMA

    def test_engine_write_read_round_trip_against_real_ddl(self, tmp_path, monkeypatch):
        """Engine create_flow + list_flows must round-trip on an init_db-created table.

        This is the core regression: before the fix the INSERT hit
        source_zone/destination_zone/application_type columns and failed.
        """
        conn = _init_sqlite_db(tmp_path, monkeypatch)
        try:
            engine = TrafficFlowEngine()
            flow_id = engine.create_flow(
                topology_id="topo-1",
                name="E2E SSO Flow",
                src_zone="user",
                dst_zone="server",
                app_type="sso_saml",
                classification="NIPR",
                conn=conn,
            )
            assert flow_id

            flows = engine.list_flows("topo-1", conn)
            assert len(flows) == 1
            row = flows[0]
            assert row["src_zone"] == "user"
            assert row["dst_zone"] == "server"
            assert row["app_type"] == "sso_saml"
            assert row["classification"] == "NIPR"
        finally:
            conn.close()

    def test_generate_walkthrough_reads_flow_columns(self, tmp_path, monkeypatch):
        """generate_walkthrough reads flow['src_zone'/'dst_zone'/'app_type'] — must not KeyError."""
        conn = _init_sqlite_db(tmp_path, monkeypatch)
        try:
            engine = TrafficFlowEngine()
            flow_id = engine.create_flow(
                topology_id="topo-1",
                name="Walkthrough Flow",
                src_zone="user",
                dst_zone="server",
                app_type="sso_saml",
                classification="NIPR",
                conn=conn,
            )
            # No topology graph rows -> falls back to [src, dst] two-step walkthrough,
            # which still exercises every flow[...] column read on the engine's path.
            steps = engine.generate_walkthrough(flow_id, conn)
            assert len(steps) == 2
            assert steps[0]["node_id"] == "user"
            assert steps[1]["node_id"] == "server"
        finally:
            conn.close()
