# CUI // SP-CTI
"""Route tests for the Network Design Canvas UPDATE paths fixed in ndc-sql-01.

These exercise two representative code paths that were broken by non-f-string
``{_ph}`` SQL literals:

* ``PUT /api/topologies/<id>`` — the ``fields.append(f"updated_at={_ph}")``
  family (blueprint.py:1740). Before the fix the literal ``{_ph}`` in the SET
  clause desynchronized the placeholder/value counts and the UPDATE raised.
* ``PUT /api/peering-agreements/<id>`` — the entity-update family
  (blueprint.py:4252), same root cause.

Each test seeds a row in a temp SQLite DB, drives the Flask route via the test
client, then reads the row back and asserts the UPDATE actually persisted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def ndc_ctx(tmp_path, monkeypatch):
    """Build the Network blueprint against a temp SQLite DB.

    Forces the network canvas onto the SQLite backend and points its
    module-level ``DB_PATH`` at a throwaway file, then initializes the schema
    via the canvas's own ``init_db()``.
    """
    monkeypatch.setenv("ICDEV_NETWORK_ENABLED", "true")

    from tools.network.db import init_db as ndc_init

    db_file = tmp_path / "network_canvas_test.db"
    monkeypatch.setattr(ndc_init, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(ndc_init, "DB_PATH", db_file)
    ndc_init.init_db()  # create schema in the temp DB

    from flask import Flask

    from tools.network.blueprint import create_network_blueprint

    bp = create_network_blueprint()
    assert bp is not None, "create_network_blueprint returned None (disabled?)"

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp)

    return app, ndc_init


def _exec(ndc_init, sql, params=()):
    conn = ndc_init.get_connection()
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _fetchone(ndc_init, sql, params=()):
    conn = ndc_init.get_connection()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row


def test_update_topology_persists(ndc_ctx):
    """The :1740 family — topology metadata UPDATE must persist to the DB."""
    app, ndc_init = ndc_ctx
    tid = "topo-ndc-sql-01"
    _exec(
        ndc_init,
        "INSERT INTO topologies (id, name, description, graph_json, classification) "
        "VALUES (?, ?, ?, ?, ?)",
        (tid, "Old Name", "old desc", '{"nodes":[],"edges":[]}', "public"),
    )

    client = app.test_client()
    resp = client.put(
        f"/api/topologies/{tid}",
        json={
            "name": "New Name",
            "description": "new desc",
            "graph_json": {"nodes": [{"id": "n1"}], "edges": []},
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    row = _fetchone(
        ndc_init,
        "SELECT name, description, graph_json, updated_at FROM topologies WHERE id=?",
        (tid,),
    )
    assert row is not None
    assert row["name"] == "New Name"
    assert row["description"] == "new desc"
    graph = json.loads(row["graph_json"])
    assert graph["nodes"] == [{"id": "n1"}]
    # updated_at must be a real timestamp, never the literal placeholder text.
    assert row["updated_at"]
    assert "{_ph}" not in str(row["updated_at"])


def test_update_peering_agreement_persists(ndc_ctx):
    """The :4252 family — entity UPDATE (peering agreement) must persist."""
    app, ndc_init = ndc_ctx
    aid = "peer-ndc-sql-01"
    _exec(
        ndc_init,
        "INSERT INTO nc_peering_agreements (id, peer_name, notes, monthly_cost) "
        "VALUES (?, ?, ?, ?)",
        (aid, "Acme Transit", "old notes", 0),
    )

    client = app.test_client()
    resp = client.put(
        f"/api/peering-agreements/{aid}",
        json={"notes": "updated notes", "monthly_cost": 4200},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    row = _fetchone(
        ndc_init,
        "SELECT peer_name, notes, monthly_cost, updated_at FROM nc_peering_agreements WHERE id=?",
        (aid,),
    )
    assert row is not None
    assert row["peer_name"] == "Acme Transit"  # untouched column preserved
    assert row["notes"] == "updated notes"
    assert float(row["monthly_cost"]) == 4200.0
    assert row["updated_at"]
    assert "{_ph}" not in str(row["updated_at"])
