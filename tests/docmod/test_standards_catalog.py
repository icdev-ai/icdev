# CUI // SP-CTI
"""docmod-catalog: central /standards-catalog page — CRUD + audit, import,
propose-from-defacto, seeds, registry wiring."""
from __future__ import annotations

import io

import flask
import pytest

_DOCMOD_DDL_KEYS = ("docmod_catalog_entries", "docmod_catalog_audit",
                    "docmod_defacto_standards")


@pytest.fixture()
def db():
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    conn = get_connection()
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if any(k in stmt for k in _DOCMOD_DDL_KEYS) and "CREATE TABLE" in stmt:
            conn.execute(stmt)
    for t in ("docmod_catalog_entries", "docmod_catalog_audit"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    yield


@pytest.fixture()
def client():
    from tools.doc_modernization.blueprint import docmod_bp

    app = flask.Flask(
        __name__,
        template_folder=str(
            __import__("pathlib").Path(__file__).resolve().parents[2]
            / "tools" / "dashboard" / "templates"
        ),
    )
    app.register_blueprint(docmod_bp)  # self-prefixed (core-extension convention)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _fetch(sql, params=()):
    from tools.db.storage import get_connection
    conn = get_connection()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def test_create_entry_writes_audit(client, db):
    resp = client.post("/standards-catalog/api/entries", json={
        "domain": "software", "product": "Ubuntu", "version": "24.04",
        "category": "os", "vendor": "Canonical",
    })
    assert resp.status_code == 201, resp.get_json()
    entry_id = resp.get_json()["entry_id"]

    audits = _fetch("SELECT * FROM docmod_catalog_audit WHERE entry_id = %s", (entry_id,))
    assert audits and audits[0]["event_type"] == "create"

    listing = client.get("/standards-catalog/api/entries?domain=software").get_json()
    assert any(e["entry_id"] == entry_id and e["editable"] for e in listing)


def test_status_lifecycle_and_audit(client, db):
    entry_id = client.post("/standards-catalog/api/entries", json={
        "domain": "software", "product": "CentOS", "version": "7", "category": "os",
    }).get_json()["entry_id"]

    resp = client.post(f"/standards-catalog/api/entries/{entry_id}/status",
                       json={"status": "deprecated"})
    assert resp.status_code == 200
    resp = client.post(f"/standards-catalog/api/entries/{entry_id}/status",
                       json={"status": "retired"})
    assert resp.status_code == 200
    assert client.post(f"/standards-catalog/api/entries/{entry_id}/status",
                       json={"status": "bogus"}).status_code == 400

    events = [a["event_type"] for a in _fetch(
        "SELECT event_type FROM docmod_catalog_audit WHERE entry_id = %s ORDER BY recorded_at",
        (entry_id,),
    )]
    assert events == ["create", "status_change", "status_change"]

    listing = client.get(
        "/standards-catalog/api/entries?domain=software&status=retired"
    ).get_json()
    assert any(e["entry_id"] == entry_id for e in listing)


def test_csv_import_with_validation_report(client, db):
    csv_text = (
        "domain,product,category,vendor,version,status,eol_date\n"
        "software,Windows Server,os,Microsoft,2016,approved,2027-01-12\n"
        ",MissingDomain,os,,,,\n"
    )
    resp = client.post(
        "/standards-catalog/api/import",
        data={"file": (io.BytesIO(csv_text.encode()), "bulk.csv")},
        content_type="multipart/form-data",
    )
    report = resp.get_json()
    assert report["imported"] == 1
    assert len(report["skipped"]) == 1 and report["skipped"][0]["row"] == 1

    audits = _fetch("SELECT * FROM docmod_catalog_audit WHERE event_type = 'import'")
    assert audits


def test_propose_from_defacto_endpoint(client, db):
    resp = client.post("/standards-catalog/api/propose-from-defacto", json={
        "domain": "network_hardware", "category": "switch",
        "vendor": "Cisco", "product": "Catalyst 9300",
    })
    assert resp.status_code == 201
    entry_id = resp.get_json()["entry_id"]
    rows = _fetch("SELECT * FROM docmod_catalog_entries WHERE entry_id = %s", (entry_id,))
    assert rows and rows[0]["source"] == "promoted_from_defacto"


def test_seed_idempotent(client, db):
    r1 = client.post("/standards-catalog/api/seed").get_json()
    assert r1["seeded"] > 0
    r2 = client.post("/standards-catalog/api/seed").get_json()
    assert r2["seeded"] == 0  # ON CONFLICT DO NOTHING


def test_registry_wiring():
    from tools.config.component_registry import ComponentRegistry

    comps = [c for c in ComponentRegistry().list_all() if c.key == "standards_catalog"]
    assert comps, "standards_catalog missing from component_registry.yaml"
    c = comps[0]
    assert c.kind == "core_extension"
    # core extensions are registered WITHOUT the registry url_prefix — the
    # Blueprint self-prefixes; registry carries '' per the cam/forecast convention
    assert c.url_prefix == ""
    assert c.blueprint_attr == "docmod_bp"
    assert "standards_catalog.entries" in (c.iqe or {}).get("collections", [])


def test_pages_line_updated():
    from pathlib import Path

    start = (Path(__file__).resolve().parents[2] / ".claude" / "commands" / "start.md").read_text(
        encoding="utf-8"
    )
    assert "`/standards-catalog`" in start


def test_iqe_seed_queries_exist():
    from pathlib import Path

    qdir = Path(__file__).resolve().parents[2] / "context" / "iqe" / "queries" / "standards_catalog"
    assert len(list(qdir.glob("*.iqe"))) >= 3
