# CUI // SP-CTI
"""ndc-pna-page -- the Predictive Network Analytics page's data routes answer.

Found 2026-10-06 by an Academy walkthrough on the live board, every panel of
``/network/network/predictive-analytics`` was empty, for three stacked reasons:

1. THE TEMPLATE FETCHED URLS THAT DO NOT EXIST. It hand-typed
   ``/api/network/predict/*``; the routes live on the network blueprint, which
   is mounted at ``/network``. Every fetch 404'd. The URLs are now derived with
   ``url_for`` -- pinned here against the registered url_map, so a renamed
   endpoint fails a test instead of a demo.

2. FIVE OF SIX READ ROUTES PASSED KEYWORDS THEIR GETTER DOES NOT TAKE
   (``tier=``, ``risk=``, ``min_risk=``, ``plan_id=``). Each was a TypeError,
   caught, and returned as a 500 -- so they could not have worked even with the
   right URL.

3. THE TABLES WERE NEVER CREATED ON POSTGRESQL. ``init_db()``'s PG path skips
   every ``;``-chunk that starts with a ``--`` comment, and each PNA
   ``CREATE TABLE`` is preceded by one. Migration
   20261006080431_ndc_pna_predictive_tables creates them from the canvas's own
   rendered DDL; until it has run, a read answers 200 + an honest "no data yet"
   message, never a 500 with a raw "relation does not exist".

Runs against a throwaway SQLite file: the canvas connection's backend and path
are patched, so nothing here can reach the live board.
"""
from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest
from flask import Blueprint, Flask

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "tools" / "dashboard" / "templates" / "network" / "predictive_analytics.html"
MIGRATION_DIR = REPO_ROOT / "tools" / "db" / "migrations" / "20261006080431_ndc_pna_predictive_tables"

LIST_ENDPOINTS = ("eol", "bgp", "compliance", "capacity", "change", "supply-chain")


def _load_migration(name: str):
    spec = importlib.util.spec_from_file_location(f"pna_mig_{name}", MIGRATION_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def nc_db(tmp_path, monkeypatch):
    """Point the network canvas connection at an empty SQLite file."""
    from tools.network.db import init_db

    db = tmp_path / "network_canvas.db"
    monkeypatch.setattr(init_db, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(init_db, "DB_PATH", db)
    return db


@pytest.fixture()
def client(nc_db):
    from tools.network.routes.pna import register_pna_routes

    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("network_canvas", __name__)
    register_pna_routes(bp)
    app.register_blueprint(bp, url_prefix="/network")
    return app.test_client()


def _create_pna_tables(db: Path) -> None:
    stmts = _load_migration("up").pna_statements()
    conn = sqlite3.connect(str(db))
    try:
        for s in stmts:
            conn.execute(s)
        conn.commit()
    finally:
        conn.close()


def _seed(db: Path) -> None:
    _create_pna_tables(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO nc_eol_predictions (device_name, vendor, os_version, days_remaining, "
            "has_active_cves, active_cve_count, risk_score, risk_tier) "
            "VALUES ('rtr-1','Cisco','17.3',-10,1,2,0.9,'critical'),"
            "('rtr-2','Juniper','21.4',400,0,0,0.2,'low')"
        )
        conn.execute(
            "INSERT INTO nc_bgp_predictions (session_key, device_name, peer_ip, stability_score, "
            "flap_count_24h, flap_count_7d, flap_risk, confidence) "
            "VALUES ('10.0.0.1|rtr-1','rtr-1','10.0.0.1',0.2,5,12,'high',0.8),"
            "('10.0.0.2|rtr-2','rtr-2','10.0.0.2',0.95,0,0,'low',0.9)"
        )
        conn.execute(
            "INSERT INTO nc_compliance_drift (device_name, framework, current_score, drift_delta, "
            "drift_rate_per_day, days_to_failure, risk_score, risk_tier) "
            "VALUES ('rtr-1','DISA_STIG_IOS-XE',0.7,-0.1,-0.01,20,0.6,'high')"
        )
        conn.execute(
            "INSERT INTO nc_capacity_predictions (device_name, interface_name, current_util_pct, "
            "trend_slope, days_to_saturation, confidence, risk_score, risk_tier) "
            "VALUES ('rtr-1','Gi0/1',85.0,1.5,10,0.7,0.8,'high'),"
            "('rtr-2','Gi0/2',20.0,0.1,NULL,0.7,0.1,'low')"
        )
        conn.execute(
            "INSERT INTO nc_change_risk (change_request_id, device_name, failure_probability, "
            "blast_radius_size, maintenance_window_compliant, risk_tier) "
            "VALUES ('plan-a','rtr-1',0.75,4,1,'high'),('plan-b','rtr-2',0.1,1,1,'low')"
        )
        conn.execute(
            "INSERT INTO nc_supply_chain_risk (vendor, device_count, model_count, cve_count, "
            "kev_count, risk_score, vendor_risk_rating) "
            "VALUES ('Cisco',10,3,40,2,0.85,'critical')"
        )
        conn.commit()
    finally:
        conn.close()


# ── 1. The template's URLs are the registered routes ─────────────────────────

def test_template_hand_types_no_unprefixed_predict_url():
    src = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r"""['"`]/api/network/predict""", src) is None, (
        "the template must derive the predict URLs with url_for -- a hand-typed "
        "/api/network/predict/* drops the blueprint's /network prefix and 404s"
    )


def test_template_url_for_endpoints_resolve_under_the_canvas_prefix(client):
    src = TEMPLATE.read_text(encoding="utf-8")
    names = re.findall(r"url_for\('network_canvas\.(pna_\w+)'\)", src)
    assert len(names) >= 7, names
    app = client.application
    with app.test_request_context():
        from flask import url_for

        for name in names:
            assert url_for(f"network_canvas.{name}").startswith("/network/api/network/predict")


# ── 2. Every list route answers 200 with rows, and its filters work ──────────

@pytest.mark.parametrize("ep", LIST_ENDPOINTS)
def test_list_route_returns_rows(client, nc_db, ep):
    _seed(nc_db)
    resp = client.get(f"/network/api/network/predict/{ep}")
    assert resp.status_code == 200, resp.get_json()
    rows = resp.get_json()
    assert isinstance(rows, list) and rows, rows


def test_list_rows_carry_the_columns_the_template_renders(client, nc_db):
    _seed(nc_db)
    bgp = client.get("/network/api/network/predict/bgp").get_json()
    assert {"flap_count_24h", "flap_count_7d", "flap_risk", "stability_score"} <= set(bgp[0])
    comp = client.get("/network/api/network/predict/compliance").get_json()
    assert "drift_rate_per_day" in comp[0]
    cap = client.get("/network/api/network/predict/capacity").get_json()
    assert "trend_slope" in cap[0]
    chg = client.get("/network/api/network/predict/change").get_json()
    assert "maintenance_window_compliant" in chg[0]


def test_route_filters_reach_the_getters(client, nc_db):
    _seed(nc_db)
    get = client.get
    eol = get("/network/api/network/predict/eol?tier=critical").get_json()
    assert [r["device_name"] for r in eol] == ["rtr-1"]
    bgp = get("/network/api/network/predict/bgp?risk=high").get_json()
    assert [r["device_name"] for r in bgp] == ["rtr-1"]
    cap = get("/network/api/network/predict/capacity?min_risk=0.5").get_json()
    assert [r["interface_name"] for r in cap] == ["Gi0/1"]
    chg = get("/network/api/network/predict/change?plan_id=plan-b").get_json()
    assert [r["change_request_id"] for r in chg] == ["plan-b"]


# ── 3. Absent tables: 200 + an honest empty state, never a 500 ───────────────

@pytest.mark.parametrize("ep", LIST_ENDPOINTS)
def test_list_route_without_tables_is_empty_not_500(client, ep):
    resp = client.get(f"/network/api/network/predict/{ep}")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    if isinstance(body, list):
        # get_supply_chain_risks swallows SQLite's "no such table" itself.
        assert ep == "supply-chain" and body == []
        return
    assert body["predictions"] == []
    assert body["status"] == "no_data"
    assert "not been created" in body["message"]


GETTERS = {
    "eol": ("tools.network.eol_predictor", "get_eol_predictions"),
    "bgp": ("tools.network.bgp_predictor", "get_bgp_predictions"),
    "compliance": ("tools.network.compliance_drift_predictor", "get_compliance_drift"),
    "capacity": ("tools.network.capacity_predictor", "get_capacity_predictions"),
    "change": ("tools.network.change_failure_predictor", "get_change_risks"),
    "supply-chain": ("tools.network.supply_chain_risk_scorer", "get_supply_chain_risks"),
}


@pytest.mark.parametrize("ep", LIST_ENDPOINTS)
def test_list_route_on_postgres_missing_relation_is_empty_not_500(client, monkeypatch, ep):
    """The live failure, exactly: PostgreSQL's 'relation ... does not exist'."""
    import importlib

    mod_name, fn = GETTERS[ep]
    mod = importlib.import_module(mod_name)

    def _raise(*a, **k):
        raise Exception('relation "nc_x" does not exist\nLINE 1: SELECT * FROM nc_x')

    monkeypatch.setattr(mod, fn, _raise)
    resp = client.get(f"/network/api/network/predict/{ep}")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["predictions"] == [] and body["status"] == "no_data"


@pytest.mark.parametrize("ep", LIST_ENDPOINTS)
def test_summary_route_without_tables_is_not_500(client, ep):
    resp = client.get(f"/network/api/network/predict/{ep}/summary")
    assert resp.status_code == 200, resp.get_json()


def test_a_missing_column_is_not_dressed_up_as_no_data():
    from tools.network.routes.pna import _table_absent

    assert _table_absent(Exception('relation "nc_compliance_drift" does not exist'))
    assert _table_absent(Exception("no such table: nc_bgp_predictions"))
    assert not _table_absent(Exception('column "flap_risk" does not exist'))
    assert not _table_absent(TypeError("unexpected keyword argument 'risk'"))


# ── 4. The migration creates exactly the seven tables, from the canvas DDL ───

def test_migration_statements_create_the_seven_pna_tables(tmp_path):
    up = _load_migration("up")
    stmts = up.pna_statements()
    tables = [m.group(1) for s in stmts if (m := re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", s))]
    assert sorted(tables) == sorted(up.PNA_TABLES)
    # The CHECK lists are rendered from Python constants, never a @@CK marker.
    assert not any("@@CK" in s for s in stmts)

    class _PgShaped:
        """A connection that reports PostgreSQL but executes on SQLite."""

        _backend = "postgresql"

        def __init__(self, path):
            self._c = sqlite3.connect(str(path))

        def execute(self, sql, params=()):
            return self._c.execute(sql, params)

    conn = _PgShaped(tmp_path / "pg_shaped.db")
    result = up.up(conn)
    assert result["applied"] is True
    have = {r[0] for r in conn._c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(up.PNA_TABLES) <= have
    # Idempotent: a second run is a no-op, not an error.
    up.up(conn)


def test_migration_is_a_noop_on_sqlite_platform_db():
    class _Sqlite:
        _backend = "sqlite"

        def execute(self, *a, **k):  # pragma: no cover - must not be called
            raise AssertionError("must not execute on the SQLite platform database")

    assert _load_migration("up").up(_Sqlite())["applied"] is False
