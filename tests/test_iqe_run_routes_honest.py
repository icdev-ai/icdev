# CUI // SP-CTI
"""/iqe/run and /api/v1/iqe/query never report an unreadable collection as an empty success.

Both used to answer a seed whose collection could not be read -- no adapter, no
table -- with ``{"ok": true, "count": 0}`` (/iqe/run swallowed the executor's
error into an empty row list). An empty answer must mean "no rows", so an
unreadable collection is now ``ok: false`` with the reason, and the listing
withholds the seeds that have no executor data source at all.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask import Flask

from tools.dashboard.api.iqe import iqe_api
from tools.iqe import catalog
from tools.iqe.executor import CollectionUnavailableError

NO_SOURCE_SEED = "network/iface_admin_oper_mismatch.iqe"  # network.interfaces


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(iqe_api)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def v1_client():
    from tools.api.v1.blueprint import v1_api

    app = Flask(__name__)
    app.register_blueprint(v1_api, url_prefix="/api/v1")
    app.config["TESTING"] = True
    with patch("tools.auth.api_key.verify_api_key", return_value=("tenant-iqe-test", ["read"])):
        yield app.test_client()


def _post(client, url, path):
    headers = {"Authorization": "Bearer iqe-honesty-test-key"}
    resp = client.post(url, json={"query_path": path}, headers=headers)
    return resp.status_code, json.loads(resp.data)


# ── /iqe/run ──────────────────────────────────────────────────────────────────

def test_run_seed_with_no_executor_source_is_not_an_empty_success(client):
    status, body = _post(client, "/iqe/run", NO_SOURCE_SEED)
    assert status == 422
    assert body["ok"] is False
    assert "network.interfaces" in body["error"]
    assert "rows" not in body and "count" not in body


def test_run_reports_an_unavailable_collection_raised_while_executing(client):
    err = CollectionUnavailableError("aadc.designs", "table 'aadc_designs' could not be read")
    with patch("tools.iqe.catalog.execute_query_with_meta", side_effect=err):
        status, body = _post(client, "/iqe/run", "aadc/01_all_designs.iqe")
    assert status == 422
    assert body["ok"] is False
    assert "aadc.designs" in body["error"]


def test_run_reports_an_adapter_failure_without_its_raw_text(client):
    with patch("tools.iqe.catalog.execute_query_with_meta",
               side_effect=RuntimeError("password=hunter2 at db.internal:5432")):
        status, body = _post(client, "/iqe/run", "aadc/01_all_designs.iqe")
    assert status == 500
    assert body["ok"] is False
    assert "hunter2" not in body["error"] and "5432" not in body["error"]


def test_run_executes_with_each_adapter_owning_its_connection(client):
    """conn=None, as every canvas route does: each adapter opens its own (canvas) DB."""
    from tools.iqe.executor import ExecutionResult

    done = ExecutionResult(rows=[{"name": "d"}])
    with patch("tools.iqe.catalog.execute_query_with_meta", return_value=done) as ex:
        status, body = _post(client, "/iqe/run", "aadc/01_all_designs.iqe")
    assert status == 200 and body["ok"] is True and body["count"] == 1
    assert body["complete"] is True and body["incomplete"] == []
    assert ex.call_args.args[1] is None


def test_run_says_when_rows_are_only_a_lower_bound(client):
    from tools.iqe.executor import ExecutionResult

    partial = ExecutionResult(rows=[{"name": "d"}],
                              incomplete=[{"collection": "aadc.designs", "reason": "row_cap", "limit": 1}])
    with patch("tools.iqe.catalog.execute_query_with_meta", return_value=partial):
        status, body = _post(client, "/iqe/run", "aadc/01_all_designs.iqe")
    assert status == 200 and body["complete"] is False
    assert body["incomplete"][0]["reason"] == "row_cap"


def test_listing_withholds_seeds_with_no_executor_source(client):
    resp = client.get("/iqe")
    body = json.loads(resp.data)
    listed = {q["path"] for q in body["queries"]}
    withheld = {q["path"]: q["reason"] for q in body["not_executable"]}
    assert NO_SOURCE_SEED not in listed
    assert withheld.get(NO_SOURCE_SEED)
    assert "aadc/01_all_designs.iqe" in listed
    assert body["count"] == len(body["queries"])


# ── /api/v1/iqe/query ─────────────────────────────────────────────────────────

def test_v1_seed_with_no_executor_source_is_an_error(v1_client):
    status, body = _post(v1_client, "/api/v1/iqe/query", NO_SOURCE_SEED)
    assert status == 422
    assert body["ok"] is False
    assert "network.interfaces" in body["error"]


def test_v1_adapter_failure_text_does_not_leak(v1_client):
    with patch("tools.iqe.catalog.execute_query_with_meta", side_effect=RuntimeError("secret-dsn")):
        status, body = _post(v1_client, "/api/v1/iqe/query", "aadc/01_all_designs.iqe")
    assert status == 500
    assert body["ok"] is False and "secret-dsn" not in body["error"]


def test_catalog_reason_names_every_unavailable_collection():
    from tools.iqe.parser import parse

    catalog.load_all_adapters()
    ast = parse('foreach r in join("network.interfaces", "network.routes", "device") select r.name')
    reason = catalog.unavailable_reason(ast)
    assert reason and "network.interfaces" in reason and "network.routes" in reason
