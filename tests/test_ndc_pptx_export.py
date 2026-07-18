# CUI // SP-CTI
"""Tests for NDC PPTX topology export (ndc-brg-04).

``tools/network/pptx_export.py`` renders a topology as a PowerPoint deck via
the tools/viz presentation layer. These tests cover:

  (a) export of a seeded topology → real .pptx bytes (PK zip magic,
      ``zipfile.is_zipfile`` true, ``[Content_Types].xml`` present, >=1 slide).
  (b) device/node names appear in the rendered slide XML.
  (c) a missing topology → ``None`` from the API and a 404 at the route level
      (driven through the Flask test client built from create_network_blueprint).
  (d) the dependency-missing path (python-pptx unavailable) → a clean 501/JSON
      response, never a stack trace.

The temp-canvas SQLite setup mirrors ``tests/test_ndc_graph_cache.py`` (direct
export) and ``tests/test_ndc_update_routes.py`` (full-schema route client).
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

pptx = pytest.importorskip("pptx", reason="python-pptx not installed")

from tools.network import blueprint_helpers as bh


@pytest.fixture(autouse=True)
def _clear_cache():
    """The parsed-graph cache is process-global; isolate each test."""
    bh.parsed_graph_cache_clear()
    yield
    bh.parsed_graph_cache_clear()


def _canvas_db(tmp_path, monkeypatch):
    """Point the network canvas at a throwaway SQLite DB with a topologies table."""
    from tools.network.db import init_db

    db_file = tmp_path / "nc_pptx_export.db"
    monkeypatch.setattr(init_db, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(init_db, "DB_PATH", db_file)
    conn = init_db.get_connection()
    conn.execute(
        "CREATE TABLE topologies ("
        "id TEXT PRIMARY KEY, name TEXT, classification TEXT, "
        "graph_json TEXT, updated_at TEXT)"
    )
    conn.commit()
    return init_db, conn


def _insert(conn, tid, name, graph, classification="CUI", updated_at="2026-01-01T00:00:00"):
    conn.execute(
        "INSERT INTO topologies (id, name, classification, graph_json, updated_at) "
        "VALUES (%s,%s,%s,%s,%s)",
        (tid, name, classification, json.dumps(graph), updated_at),
    )
    conn.commit()


_SAMPLE_GRAPH = {
    "nodes": [
        {"id": "n1", "label": "CoreRouter1", "type": "router",
         "config": {"vendor": "Cisco", "model": "ASR9000", "ip": "10.0.0.1"}},
        {"id": "n2", "label": "EdgeSwitch2", "type": "switch",
         "config": {"vendor": "Arista", "model": "7050X", "ip": "10.0.0.2"}},
        {"id": "z1", "label": "DMZ Zone", "type": "zone"},  # non-device, filtered
    ],
    "edges": [{"source": "n1", "target": "n2"}],
}


# ── (a) real .pptx bytes ────────────────────────────────────────────────────────

def test_export_returns_valid_pptx_bytes(tmp_path, monkeypatch):
    _, conn = _canvas_db(tmp_path, monkeypatch)
    _insert(conn, "t1", "Prod Core Fabric", _SAMPLE_GRAPH)

    from tools.network.pptx_export import export_topology_pptx

    data = export_topology_pptx("t1")
    assert isinstance(data, bytes)
    assert data[:2] == b"PK", "not a ZIP/OOXML container"

    buf = io.BytesIO(data)
    assert zipfile.is_zipfile(buf)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        assert "[Content_Types].xml" in names
        slides = [n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
        assert len(slides) >= 1, f"no slide parts found: {names}"
        # Three-slide deck: title, diagram, inventory.
        assert len(slides) >= 3, f"expected >=3 slides, got {len(slides)}"


def test_export_to_path(tmp_path, monkeypatch):
    _, conn = _canvas_db(tmp_path, monkeypatch)
    _insert(conn, "t1", "Path Topology", _SAMPLE_GRAPH)

    from tools.network.pptx_export import export_topology_pptx

    out = tmp_path / "deck.pptx"
    result = export_topology_pptx("t1", out_path=str(out))
    assert result == str(out)
    assert out.exists()
    assert zipfile.is_zipfile(str(out))


# ── (b) node names appear in slide XML ─────────────────────────────────────────

def test_node_names_appear_in_slide_xml(tmp_path, monkeypatch):
    _, conn = _canvas_db(tmp_path, monkeypatch)
    _insert(conn, "t1", "Named Nodes", _SAMPLE_GRAPH)

    from tools.network.pptx_export import export_topology_pptx

    data = export_topology_pptx("t1")
    xml_blob = ""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for n in zf.namelist():
            if n.startswith("ppt/slides/slide") and n.endswith(".xml"):
                xml_blob += zf.read(n).decode("utf-8", errors="replace")

    assert "CoreRouter1" in xml_blob
    assert "EdgeSwitch2" in xml_blob
    # Classification marking must be stamped on the artifact.
    assert "CUI" in xml_blob


def test_classification_marking_reflects_topology(tmp_path, monkeypatch):
    _, conn = _canvas_db(tmp_path, monkeypatch)
    _insert(conn, "pub", "Public Net", _SAMPLE_GRAPH, classification="public")

    from tools.network.pptx_export import export_topology_pptx

    data = export_topology_pptx("pub")
    xml_blob = ""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for n in zf.namelist():
            if n.startswith("ppt/slides/slide") and n.endswith(".xml"):
                xml_blob += zf.read(n).decode("utf-8", errors="replace")
    assert "UNCLASSIFIED" in xml_blob


# ── (c) missing topology → None (API) + 404 (route) ────────────────────────────

def test_missing_topology_returns_none(tmp_path, monkeypatch):
    _canvas_db(tmp_path, monkeypatch)  # empty table
    from tools.network.pptx_export import export_topology_pptx

    assert export_topology_pptx("does-not-exist") is None


def _route_app(tmp_path, monkeypatch):
    """Full-schema NDC blueprint on a temp SQLite DB, returned as a Flask app."""
    monkeypatch.setenv("ICDEV_NETWORK_ENABLED", "true")
    from tools.network.db import init_db as ndc_init

    db_file = tmp_path / "network_canvas_route.db"
    monkeypatch.setattr(ndc_init, "_NC_BACKEND", "sqlite")
    monkeypatch.setattr(ndc_init, "DB_PATH", db_file)
    ndc_init.init_db()

    from flask import Flask

    from tools.network.blueprint import create_network_blueprint

    bp = create_network_blueprint()
    assert bp is not None
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp)
    return app, ndc_init


def test_route_missing_topology_404(tmp_path, monkeypatch):
    app, _ = _route_app(tmp_path, monkeypatch)
    client = app.test_client()
    resp = client.get("/api/export/pptx/nonexistent-id")
    assert resp.status_code == 404
    assert resp.is_json
    assert "error" in resp.get_json()


def test_route_exports_pptx(tmp_path, monkeypatch):
    app, ndc_init = _route_app(tmp_path, monkeypatch)
    conn = ndc_init.get_connection()
    conn.execute(
        "INSERT INTO topologies (id, name, description, graph_json, classification) "
        "VALUES (?, ?, ?, ?, ?)",
        ("rt1", "Route Fabric", "", json.dumps(_SAMPLE_GRAPH), "CUI"),
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    resp = client.get("/api/export/pptx/rt1")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.mimetype == (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert resp.headers["Content-Disposition"].endswith(".pptx")
    body = resp.get_data()
    assert body[:2] == b"PK"
    assert zipfile.is_zipfile(io.BytesIO(body))


# ── (d) dependency-missing → clean 501/JSON ────────────────────────────────────

def test_route_dependency_missing_501(tmp_path, monkeypatch):
    app, ndc_init = _route_app(tmp_path, monkeypatch)
    conn = ndc_init.get_connection()
    conn.execute(
        "INSERT INTO topologies (id, name, graph_json, classification) "
        "VALUES (?, ?, ?, ?)",
        ("dep1", "Dep Topo", json.dumps(_SAMPLE_GRAPH), "CUI"),
    )
    conn.commit()
    conn.close()

    # Simulate python-pptx being unavailable at the export layer's single
    # detection seam, which raises PptxDependencyError → the route answers 501
    # (never a trace).
    from tools.network import pptx_export

    def _boom() -> None:
        raise pptx_export.PptxDependencyError("simulated missing python-pptx")

    monkeypatch.setattr(pptx_export, "_require_pptx", _boom)

    client = app.test_client()
    resp = client.get("/api/export/pptx/dep1")
    assert resp.status_code == 501, resp.get_data(as_text=True)
    assert resp.is_json
    assert "error" in resp.get_json()


def test_export_api_raises_dependency_error(tmp_path, monkeypatch):
    _, conn = _canvas_db(tmp_path, monkeypatch)
    _insert(conn, "t1", "Dep Direct", _SAMPLE_GRAPH)

    from tools.network import pptx_export
    from tools.network.pptx_export import PptxDependencyError, export_topology_pptx

    def _boom() -> None:
        raise PptxDependencyError("simulated missing python-pptx")

    monkeypatch.setattr(pptx_export, "_require_pptx", _boom)
    with pytest.raises(PptxDependencyError):
        export_topology_pptx("t1")
