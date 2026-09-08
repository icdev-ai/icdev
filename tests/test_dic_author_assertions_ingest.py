# CUI // SP-CTI
"""An upload that DECLARES what it asserts lands in the store (dwr-ev-01).

The DONE criterion end to end, on the real ingest: upload a document with an
``author_assertions`` field saying a product is current, resolve that entity,
and get BOTH assertions back with the author's first and ``conflict: True``.
Embedding and the KG bridge are off, as in tests/test_dic_ingest_orchestrator.py;
the subject is the DIC row writes and the store refresh.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tools.db.storage import get_connection
from tools.document_intelligence import ingest_orchestrator as orch
from tools.document_intelligence.ingest_orchestrator import ingest_file

_PRODUCT = "model-epsilon-7"
_AUTHORITY = "example-corp"
_INGEST_OFF = dict(
    embed=False, bridge_kg=False, summarize=False, extract_metadata=False,
    extract_identifiers=False, extract_correspondence=False,
)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    conn = get_connection()
    try:
        orch._ensure_schema(conn)
        for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
            if "CREATE" in stmt and any(
                k in stmt for k in ("entity_currency", "docmod_catalog_entries")
            ):
                conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def sample_doc(tmp_path: Path) -> Path:
    p = tmp_path / "estate-note.md"
    p.write_text(
        f"# Estate note\n\nThe {_PRODUCT} chassis remains fielded in the core. " * 40,
        encoding="utf-8",
    )
    return p


def _seed_catalog_retired():
    conn = get_connection()
    conn.execute(
        "INSERT INTO docmod_catalog_entries (entry_id, domain, category, vendor, product, "
        "version, status, source, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        ("cat-eps", "network_hardware", "chassis", _AUTHORITY, _PRODUCT, "", "retired",
         "manual", "2026-08-30T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    from tools.currency.entity_currency import backfill

    assert backfill(sources=["docmod_catalog_entries"])["errors"] == {}


def test_upload_with_assertions_resolves_author_first_and_keeps_the_catalog(sample_doc):
    from tools.currency.entity_currency import resolve
    from tools.document_intelligence.author_evidence import (
        SOURCE_ID, list_assertions, parse_assertions,
    )

    _seed_catalog_retired()
    outcome = ingest_file(
        str(sample_doc), "estate", tenant_id="acme", classification="CUI",
        created_by="network-lead",
        author_assertions=parse_assertions([{
            "entity": _PRODUCT, "type": "chassis", "vendor": _AUTHORITY,
            "status": "fielded", "as_of": "2026-08-01",
        }]),
        **_INGEST_OFF,
    )
    assert outcome.author_assertions == 1
    assert outcome.to_dict()["author_assertions"] == 1
    assert not [e for e in outcome.errors if "entity_currency" in e], outcome.errors

    # The statement, as made, tied to the document that carried it.
    (a,) = list_assertions(doc_id=outcome.doc_id)
    assert a["version_id"] == outcome.version_id
    assert a["asserted_by"] == "network-lead"
    assert a["tenant_id"] == "acme"
    assert a["as_of_basis"] == "author_stated"

    # The store: author first, catalog preserved, conflict visible.
    view = resolve(_PRODUCT, entity_type="chassis", namespace=_AUTHORITY)
    assert view["source"] == SOURCE_ID
    assert view["verdict"] == "current"
    assert view["conflict"] is True
    assert [(o["source"], o["verdict"]) for o in view["others"]] == [
        ("docmod_catalog_entries", "end_of_life"),
    ]
    assert view["provenance"] == {
        "table": "dic_author_assertions", "id": a["assertion_id"],
        "record_id": view["provenance"]["record_id"],
    }


def test_an_upload_declaring_nothing_writes_nothing(sample_doc):
    from tools.document_intelligence.author_evidence import list_assertions

    outcome = ingest_file(str(sample_doc), "estate", **_INGEST_OFF)
    assert outcome.author_assertions == 0
    assert list_assertions(doc_id=outcome.doc_id) == []


def test_a_reingest_updates_the_statement_rather_than_duplicating_it(sample_doc):
    from tools.document_intelligence.author_evidence import list_assertions, parse_assertions

    first = ingest_file(
        str(sample_doc), "estate",
        author_assertions=parse_assertions([{
            "entity": _PRODUCT, "type": "chassis", "status": "fielded", "as_of": "2026-03-01",
        }]),
        **_INGEST_OFF,
    )
    conn = get_connection()
    conn.execute("DELETE FROM dic_documents WHERE doc_id = %s", (first.doc_id,))
    conn.commit()
    conn.close()
    second = ingest_file(
        str(sample_doc), "estate",
        author_assertions=parse_assertions([{
            "entity": _PRODUCT, "type": "chassis", "status": "retired", "as_of": "2026-08-01",
        }]),
        **_INGEST_OFF,
    )
    assert second.doc_id == first.doc_id  # same collection, same path -> same id
    rows = list_assertions(doc_id=second.doc_id)
    assert [(r["status"], r["as_of"]) for r in rows] == [("retired", "2026-08-01")]


def test_the_upload_route_refuses_a_malformed_field_before_touching_the_file(monkeypatch):
    """A 400 the author sees, never an assertion dropped inside the thread.

    The blueprint alone, the tests/docmod/test_hitl_decision_wiring.py shape:
    the subject is the route's validation, not the dashboard's login."""
    import flask

    import tools.document_intelligence.blueprint as bp_mod

    monkeypatch.setattr(orch, "ingest_file", lambda *a, **k: pytest.fail("ingest ran"))
    app = flask.Flask(__name__)
    app.register_blueprint(bp_mod.dic_bp, url_prefix="/document-intelligence")
    app.config["TESTING"] = True
    with app.test_client() as client:
        resp = client.post(
            "/document-intelligence/api/ingest",
            data={
                "file": (io.BytesIO(b"# note"), "note.md"),
                "collection_id": "estate",
                "author_assertions": json.dumps([{"entity": "x", "status": "fielded"}]),
            },
            content_type="multipart/form-data",
        )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "entity_type" in resp.get_json()["error"]
